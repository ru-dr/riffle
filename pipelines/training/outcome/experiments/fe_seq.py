"""
fe_seq.py - pass 3 sequence model: reads the author's and the repo's previous PRs as sequences instead of summary rates.

For PR i (opened at t_i):
  author sequence  the author's last K_A PRs in this repo opened strictly before t_i (newest first). Each token: a few
                   at-open facts of that PR, the time since it opened, and its outcome **only if it closed before
                   t_i** (else a "still open" flag).
  repo sequence    the repo's last K_R PRs closed strictly before t_i: outcome, outside / first-timer flags, time to
                   close, time since close, size.
  static           the same dense row the MLP reads (Dense in fe_models.py).
Model: a GRU per sequence + an MLP on the static row -> 3 logits. Calibrated, scored and saved like fe_models.py.

    python fe_seq.py [--splits pr repo] [--seeds 0]
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

import fe_features as F
import fe_models as M
import fe_run as R

K_A, K_R = 16, 32
OFF = F.OFF


def _num(d, c):
    return pd.to_numeric(d[c], errors="coerce").fillna(0).to_numpy(float) if c in d else np.zeros(len(d))


def token_table(d):
    """At-open facts of each PR, usable as a token in someone else's sequence (scaled roughly to unit range)."""
    churn = np.log1p(_num(d, "LA") + _num(d, "LD")) / 8
    nf = np.log1p(_num(d, "NF")) / 4
    fork = _num(d, "head_from_fork")
    outside = d["outside"].to_numpy(float)
    first = np.nan_to_num(d["ah_first_pr"].to_numpy(float))
    risk = np.nan_to_num(pd.to_numeric(d["semantic_risk_1to5"], errors="coerce").to_numpy(float)) / 5
    test = _num(d, "frac_test_lines")
    docs = _num(d, "docs_only")
    return np.c_[churn, nf, fork, outside, first, risk, test, docs].astype(np.float32)


def author_sequences(d):
    """idx (n, K_A) of the author's previous PRs in the repo (opened strictly before), -1 = none."""
    ar = pd.factorize(d["repo"] + "\x00" + d["author_key"].fillna("\x01"))[0]
    ar[d["author_key"].isna().to_numpy()] = -1
    _, k = F._own_keys(ar)
    t = d["t_open"].to_numpy()
    order = np.lexsort((t, k))
    ks, ts = k[order], t[order]
    pos = np.empty(len(d), np.int64)
    pos[order] = np.arange(len(d))
    idx = np.full((len(d), K_A), -1, np.int64)
    for j in range(1, K_A + 1):
        p = pos - j
        ok = p >= 0
        pc = np.clip(p, 0, None)
        ok &= (ks[pc] == k) & (ts[pc] < t)
        idx[:, j - 1] = np.where(ok, order[pc], -1)
    return idx


def close_secs(d):
    """Close time for history. A stale-open row (closed_at null: still open when mined, labelled abandoned) was open
    at every earlier moment and its outcome was not known before mining, so it closes in the far future: it stays
    "still open" in author sequences and never enters a repo sequence. (Before this fix it closed at its open: a leak.)"""
    ok = pd.to_datetime(d["closed_at"], utc=True, errors="coerce").notna().to_numpy()
    return np.where(ok, d["t_close"].to_numpy(), np.int64(1) << 35)


def repo_sequences(d):
    """idx (n, K_R) of the repo's last PRs closed strictly before this PR opened, -1 = none."""
    repo = pd.factorize(d["repo"])[0].astype(np.int64)
    tc, t = close_secs(d), d["t_open"].to_numpy()
    ev = repo * OFF + tc
    order = np.argsort(ev, kind="stable")
    evs = ev[order]
    hi = np.searchsorted(evs, repo * OFF + t, side="left")        # first close >= t_i
    start = np.searchsorted(evs, repo * OFF, side="left")
    idx = np.full((len(d), K_R), -1, np.int64)
    for j in range(1, K_R + 1):
        p = hi - j
        idx[:, j - 1] = np.where(p >= start, order[np.clip(p, 0, None)], -1)
    return idx


def build_tokens(d, tok, ia, ir):
    """Token tensors with the point-in-time masking applied."""
    t, tc, y = d["t_open"].to_numpy(), close_secs(d), d["y"].to_numpy()
    Y = np.eye(3, dtype=np.float32)[y]
    ttc = (np.log1p((d["t_close"].to_numpy() - t) / 3600) / 8).astype(np.float32)   # used only where known

    def gather(idx, extra):
        valid = idx >= 0
        j = np.clip(idx, 0, None)
        parts = [tok[j]] + extra(j, valid)
        X = np.concatenate([p if p.ndim == 3 else p[..., None] for p in parts], axis=2).astype(np.float32)
        X[~valid] = 0
        return X, valid

    def a_extra(j, valid):
        known = (tc[j] < t[:, None]) & valid                          # outcome visible only if closed before t_i
        since = np.log1p(np.maximum(t[:, None] - t[j], 0) / F.DAY) / 8
        return [Y[j] * known[..., None], (~known & valid).astype(np.float32), ttc[j] * known, since, valid]

    def r_extra(j, valid):
        since = np.log1p(np.maximum(t[:, None] - tc[j], 0) / F.DAY) / 8
        return [Y[j] * valid[..., None], ttc[j], since, valid]

    A, va = gather(ia, a_extra)
    B, vb = gather(ir, r_extra)
    return A, va, B, vb


class Net(nn.Module):
    def __init__(self, n_static, da, db, h=64):
        super().__init__()
        self.ga = nn.GRU(da, h, batch_first=True)
        self.gb = nn.GRU(db, h, batch_first=True)
        self.static = nn.Sequential(nn.Linear(n_static, 256), nn.LayerNorm(256), nn.SiLU(), nn.Dropout(0.2),
                                    nn.Linear(256, 128), nn.SiLU())
        self.head = nn.Sequential(nn.Linear(128 + 2 * h, 128), nn.SiLU(), nn.Dropout(0.2), nn.Linear(128, 3))

    def forward(self, s, a, b):
        # sequences are newest first; the GRU reads oldest -> newest, so flip; empty slots are zero tokens
        _, ha = self.ga(torch.flip(a, [1]))
        _, hb = self.gb(torch.flip(b, [1]))
        return self.head(torch.cat([self.static(s), ha[-1], hb[-1]], 1))


def seq_learner(A, B):
    """Learner in fe_models' interface; the row index of X tells it which sequences to use."""
    def fit(Xtr, ytr, wtr, Xva, yva, wva, seed):
        torch.manual_seed(seed)
        dev = "cuda"
        enc = M.Dense().fit(Xtr)
        it, iv = Xtr.index.to_numpy(), Xva.index.to_numpy()
        S = lambda X: torch.tensor(enc.transform(X))
        st, sv = S(Xtr).to(dev), S(Xva).to(dev)
        at, bt = torch.tensor(A[it]).to(dev), torch.tensor(B[it]).to(dev)
        av, bv = torch.tensor(A[iv]).to(dev), torch.tensor(B[iv]).to(dev)
        ya, yb = torch.tensor(np.asarray(ytr)).to(dev), torch.tensor(np.asarray(yva)).to(dev)
        wa = torch.tensor(np.asarray(wtr, np.float32)).to(dev)
        wb = torch.tensor(np.asarray(wva, np.float32)).to(dev)
        net = Net(st.shape[1], A.shape[2], B.shape[2]).to(dev)
        opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
        loss = nn.CrossEntropyLoss(reduction="none")
        best, state, bad, bs = np.inf, None, 0, 1024
        for epoch in range(60):
            net.train()
            perm = torch.randperm(len(st), device=dev)
            for i in range(0, len(st), bs):
                j = perm[i:i + bs]
                opt.zero_grad()
                (loss(net(st[j], at[j], bt[j]), ya[j]) * wa[j]).sum().div(wa[j].sum()).backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                Z = torch.cat([net(sv[i:i + 8192], av[i:i + 8192], bv[i:i + 8192]) for i in range(0, len(sv), 8192)])
                v = float((loss(Z, yb) * wb).sum() / wb.sum())
            if v < best - 1e-4:
                best, bad = v, 0
                state = {k: x.detach().clone() for k, x in net.state_dict().items()}
            else:
                bad += 1
                if bad >= 6:
                    break
        net.load_state_dict(state)
        net.eval().cpu()

        def predict(X):
            i = X.index.to_numpy()
            with torch.no_grad():
                return net(S(X), torch.tensor(A[i]), torch.tensor(B[i])).numpy()
        return predict
    return fit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=["pr", "repo"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--features", default="base")
    a = ap.parse_args()
    d, allowed = R.load()
    g = F.feature_groups(d, allowed)
    X = R.encode(d, M.feature_set(g, a.features))
    t0 = time.time()
    A, va, B, vb = build_tokens(d, token_table(d), author_sequences(d), repo_sequences(d))
    print(f"sequences built in {time.time() - t0:.0f}s: author {A.shape}, filled {va.mean():.2f}; "
          f"repo {B.shape}, filled {vb.mean():.2f}", flush=True)
    rows = d["mature"].to_numpy() & (d["open_snapshot"] != "final").to_numpy()
    train_rows = rows & d["outside"].to_numpy()
    ex = (d["open_snapshot"] == "exact").to_numpy()
    cnt = pd.DataFrame({"r": d["repo"], "y": d["y"], "x": ex})[train_rows].groupby(["r", "y"])["x"].agg(["size", "sum"])
    wx = np.nan_to_num((cnt["size"] / cnt["sum"].clip(lower=1)).reindex(
        pd.MultiIndex.from_arrays([d["repo"], d["y"]])).to_numpy(), nan=1.0)
    train_rows &= ex
    for split in a.splits:
        t0 = time.time()
        m = M.run(d, X, seq_learner(A, B), split, rows, train_rows, wx, a.seeds, f"p3_seq_{a.features}")
        row = dict(model="seq_gru", split=split, features=a.features, seeds=len(a.seeds), n_features=X.shape[1],
                   seconds=round(time.time() - t0), **m)
        print(json.dumps(row), flush=True)
        with open(M.RESULTS, "a") as fh:
            fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()

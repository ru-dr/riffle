"""
fe_profile.py - pass 4: what each blend member costs to train and to serve.

    python fe_profile.py all              # every member, each step in a fresh process; writes fe_profile.json
    python fe_profile.py train NAME       # one member: train on the by-PR training rows, save to fe_profile/NAME/
    python fe_profile.py serve NAME       # one member, CPU only: load from disk, latency, memory

Training (seed 0, by-PR split, the pass-3 recipe): wall time, peak RAM above the loaded data, peak GPU memory,
CPU-only training time (MLP and sequence: one CPU epoch times the epochs used on the GPU), determinism (trained twice
with the same seed: largest difference in a test probability).
Serving (CUDA hidden, so a CPU-only install): library import time, model load time, model size on disk, RAM added by
the model, latency for one PR at a time (the webhook case) and per PR in batches of 256. Features are prepared before
timing; they are shared by every member except the sequence model's token gather, timed separately.
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import threading
import time

import numpy as np
import pandas as pd
import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fe_profile")
PY = sys.executable
MEMBERS = ("lgb_default", "lgb_strong_reg", "xgb", "logreg", "mlp", "seq_gru")


class Sampler:
    """Peak RSS of this process and peak GPU memory in use (nvidia-smi), sampled every 0.1 s."""

    def __init__(self, gpu=False):
        self.p, self.gpu = psutil.Process(), gpu
        self.rss0, self.rss, self.g0, self.g = self.p.memory_info().rss, 0, self._gpu(), 0
        self.stop = False

    def _gpu(self):
        if not self.gpu:
            return 0
        try:
            return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                      capture_output=True, text=True).stdout.split()[0])
        except Exception:
            return 0

    def __enter__(self):
        def loop():
            while not self.stop:
                self.rss = max(self.rss, self.p.memory_info().rss)
                if self.gpu:
                    self.g = max(self.g, self._gpu())
                time.sleep(0.1)
        self.th = threading.Thread(target=loop, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *a):
        self.stop = True
        self.th.join()
        self.rss_mb = max(self.rss - self.rss0, 0) / 2**20
        self.gpu_mb = max(self.g - self.g0, 0)


def data():
    import fe_features as F
    import fe_models as M
    import fe_run as R
    d, allowed = R.load()
    X = R.encode(d, M.feature_set(F.feature_groups(d, allowed), "base"))
    rows = d["mature"].to_numpy() & (d["open_snapshot"] != "final").to_numpy()
    train_rows = rows & d["outside"].to_numpy()
    ex = (d["open_snapshot"] == "exact").to_numpy()
    cnt = pd.DataFrame({"r": d["repo"], "y": d["y"], "x": ex})[train_rows].groupby(["r", "y"])["x"].agg(["size", "sum"])
    wx = np.nan_to_num((cnt["size"] / cnt["sum"].clip(lower=1)).reindex(
        pd.MultiIndex.from_arrays([d["repo"], d["y"]])).to_numpy(), nan=1.0)
    train_rows &= ex
    te = rows & d["outside"].to_numpy() & (d["u_pr"].to_numpy() < 0.2)
    rest = train_rows & (d["u_pr"].to_numpy() >= 0.2)
    va, tr = rest & (d["u_val"].to_numpy() < 0.15), rest & (d["u_val"].to_numpy() >= 0.15)
    return d, X, tr, va, te, wx


# ---------------------------------------------------------------- per-member train / save / load / predict
def train(name, d, X, tr, va, wx, seed=0, device="cuda"):
    """Returns (artifact dict to pickle or save, predict_raw function, info)."""
    import fe_models as M
    import fe_run as R
    y, repo = d["y"].to_numpy(), d["repo"].to_numpy()
    w = R.repo_weights(repo[tr]) * wx[tr]
    info = {}
    if name.startswith("lgb"):
        import lightgbm as lgb
        extra = dict(learning_rate=0.05) if name == "lgb_default" else dict(
            learning_rate=0.05, num_leaves=31, min_data_in_leaf=200, lambda_l2=30, feature_fraction=0.5)
        p = dict(R.PARAMS, **extra, seed=seed)
        dtr = lgb.Dataset(X[tr], y[tr], weight=w)
        m = lgb.train(p, dtr, 6000, valid_sets=[lgb.Dataset(X[va], y[va], weight=wx[va], reference=dtr)],
                      callbacks=[lgb.early_stopping(150, verbose=False)])
        info["rounds"] = m.best_iteration
        return {"kind": "lgb", "model": m}, (lambda Z: m.predict(Z, raw_score=True)), info
    if name == "xgb":
        import xgboost as xgb
        p = dict(objective="multi:softprob", num_class=3, tree_method="hist", device=device, eta=0.05, max_depth=6,
                 min_child_weight=20, subsample=0.8, colsample_bytree=0.7, reg_lambda=5.0, max_cat_to_onehot=8,
                 eval_metric="mlogloss", seed=seed)
        m = xgb.train(p, xgb.DMatrix(X[tr], y[tr], weight=w, enable_categorical=True), 6000,
                      evals=[(xgb.DMatrix(X[va], y[va], weight=wx[va], enable_categorical=True), "va")],
                      early_stopping_rounds=150, verbose_eval=False)
        info["rounds"] = m.best_iteration + 1
        it = (0, m.best_iteration + 1)
        return ({"kind": "xgb", "model": m, "it": it},
                lambda Z: np.log(np.clip(m.predict(xgb.DMatrix(Z, enable_categorical=True), iteration_range=it),
                                         1e-9, 1)), info)
    if name == "logreg":
        from sklearn.linear_model import LogisticRegression
        enc = M.Dense().fit(X[tr])
        m = LogisticRegression(C=0.1, max_iter=2000).fit(enc.transform(X[tr]), y[tr], sample_weight=w)
        return {"kind": "logreg", "enc": enc, "model": m}, (lambda Z: m.decision_function(enc.transform(Z))), info
    raise ValueError(name)


def train_torch(name, d, X, tr, va, wx, seed=0, device="cuda", max_epochs=None):
    """MLP and sequence model, with their own loops so epochs and devices can be controlled."""
    import torch
    import torch.nn as nn
    import fe_models as M
    import fe_run as R
    import fe_seq as S
    torch.manual_seed(seed)
    y, repo = d["y"].to_numpy(), d["repo"].to_numpy()
    w = R.repo_weights(repo[tr]) * wx[tr]
    enc = M.Dense().fit(X[tr])
    st, sv = (torch.tensor(enc.transform(X[m])).to(device) for m in (tr, va))
    ya, yb = torch.tensor(y[tr]).to(device), torch.tensor(y[va]).to(device)
    wa, wb = torch.tensor(w.astype(np.float32)).to(device), torch.tensor(wx[va].astype(np.float32)).to(device)
    if name == "seq_gru":
        A, _, B, _ = S.build_tokens(d, S.token_table(d), S.author_sequences(d), S.repo_sequences(d))
        at, av = torch.tensor(A[tr]).to(device), torch.tensor(A[va]).to(device)
        bt, bv = torch.tensor(B[tr]).to(device), torch.tensor(B[va]).to(device)
        net = S.Net(st.shape[1], A.shape[2], B.shape[2]).to(device)
        fwd = lambda s, a, b: net(s, a, b)
        patience, epochs = 6, 60
    else:
        layers, k = [], st.shape[1]
        for n in (512, 256, 128):
            layers += [nn.Linear(k, n), nn.LayerNorm(n), nn.SiLU(), nn.Dropout(0.2)]
            k = n
        net = nn.Sequential(*layers, nn.Linear(k, 3)).to(device)
        at = av = bt = bv = None
        fwd = lambda s, a, b: net(s)
        patience, epochs = 8, 80
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
    loss = nn.CrossEntropyLoss(reduction="none")
    best, state, bad, used, t_epoch = np.inf, None, 0, 0, []
    for epoch in range(max_epochs or epochs):
        t0 = time.perf_counter()
        net.train()
        perm = torch.randperm(len(st), device=device)
        for i in range(0, len(st), 1024):
            j = perm[i:i + 1024]
            opt.zero_grad()
            out = fwd(st[j], at[j] if at is not None else None, bt[j] if bt is not None else None)
            (loss(out, ya[j]) * wa[j]).sum().div(wa[j].sum()).backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            Z = torch.cat([fwd(sv[i:i + 8192], av[i:i + 8192] if av is not None else None,
                               bv[i:i + 8192] if bv is not None else None) for i in range(0, len(sv), 8192)])
            v = float((loss(Z, yb) * wb).sum() / wb.sum())
        t_epoch.append(time.perf_counter() - t0)
        used = epoch + 1
        if v < best - 1e-4:
            best, bad = v, 0
            state = {k_: x.detach().clone().cpu() for k_, x in net.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    net.load_state_dict(state)
    net.eval().cpu()
    info = {"epochs": used, "seconds_per_epoch": float(np.mean(t_epoch)),
            "parameters": int(sum(p.numel() for p in net.parameters()))}
    art = {"kind": name, "enc": enc, "state": state, "n_static": st.shape[1]}
    if name == "seq_gru":
        art["dims"] = (A.shape[2], B.shape[2])

        def predict(Z):
            i = Z.index.to_numpy()
            with torch.no_grad():
                return net(torch.tensor(enc.transform(Z)), torch.tensor(A[i]), torch.tensor(B[i])).numpy()
    else:
        def predict(Z):
            with torch.no_grad():
                return net(torch.tensor(enc.transform(Z))).numpy()
    return art, predict, info


def save(name, art, cal):
    path = os.path.join(OUT, name)
    os.makedirs(path, exist_ok=True)
    for f in os.listdir(path):
        os.remove(os.path.join(path, f))
    if art["kind"] == "lgb":
        art["model"].save_model(os.path.join(path, "model.txt"))
        meta = {}
    elif art["kind"] == "xgb":
        art["model"].save_model(os.path.join(path, "model.ubj"))
        meta = {"it": art["it"]}
    elif art["kind"] in ("mlp", "seq_gru"):
        import torch
        torch.save(art["state"], os.path.join(path, "state.pt"))
        meta = {"enc": art["enc"], "n_static": art["n_static"], "dims": art.get("dims")}
    else:
        meta = {"enc": art["enc"], "model": art["model"]}
    with open(os.path.join(path, "meta.pkl"), "wb") as fh:
        pickle.dump({**meta, "kind": art["kind"], "cal": cal.t}, fh)
    return sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path)) / 2**20


def load(name):
    path = os.path.join(OUT, name)
    meta = pickle.load(open(os.path.join(path, "meta.pkl"), "rb"))
    k = meta["kind"]
    if k == "lgb":
        import lightgbm as lgb
        m = lgb.Booster(model_file=os.path.join(path, "model.txt"))
        return meta, lambda Z, extra=None: m.predict(Z, raw_score=True)
    if k == "xgb":
        import xgboost as xgb
        m = xgb.Booster()
        m.load_model(os.path.join(path, "model.ubj"))
        m.set_param({"device": "cpu"})
        it = tuple(meta["it"])
        return meta, lambda Z, extra=None: np.log(np.clip(
            m.predict(xgb.DMatrix(Z, enable_categorical=True), iteration_range=it), 1e-9, 1))
    if k in ("mlp", "seq_gru"):
        import torch
        import torch.nn as nn
        torch.set_num_threads(4)
        state = torch.load(os.path.join(path, "state.pt"))
        enc = meta["enc"]
        if k == "seq_gru":
            import fe_seq as S
            net = S.Net(meta["n_static"], *meta["dims"])
        else:
            layers, n_in = [], meta["n_static"]
            for n in (512, 256, 128):
                layers += [nn.Linear(n_in, n), nn.LayerNorm(n), nn.SiLU(), nn.Dropout(0.2)]
                n_in = n
            net = nn.Sequential(*layers, nn.Linear(n_in, 3))
        net.load_state_dict(state)
        net.eval()

        def predict(Z, extra=None):
            with torch.no_grad():
                s = torch.tensor(enc.transform(Z))
                return (net(s, torch.tensor(extra[0]), torch.tensor(extra[1])) if k == "seq_gru" else net(s)).numpy()
        return meta, predict
    m, enc = meta["model"], meta["enc"]
    return meta, lambda Z, extra=None: m.decision_function(enc.transform(Z))


# ---------------------------------------------------------------- steps
def step_train(name):
    import fe_run as R
    d, X, tr, va, te, wx = data()
    torchy = name in ("mlp", "seq_gru")
    fit = (lambda seed=0, device="cuda", max_epochs=None: train_torch(name, d, X, tr, va, wx, seed, device, max_epochs)) \
        if torchy else (lambda seed=0, device="cuda", max_epochs=None: train(name, d, X, tr, va, wx, seed, device))
    with Sampler(gpu=name in ("xgb", "mlp", "seq_gru")) as s:
        t0 = time.perf_counter()
        art, predict, info = fit()
        secs = time.perf_counter() - t0
    y, repo, outside = d["y"].to_numpy(), d["repo"].to_numpy(), d["outside"].to_numpy()
    cv = va & outside
    cal = R.Calibrator().fit(predict(X[cv]), y[cv], R.repo_weights(repo[cv]) * wx[cv])
    P1 = cal(predict(X[te]))
    size = save(name, art, cal)
    # determinism: the same seed again
    art2, predict2, _ = fit()
    P2 = R.Calibrator().fit(predict2(X[cv]), y[cv], R.repo_weights(repo[cv]) * wx[cv])(predict2(X[te]))
    res = dict(member=name, train_seconds=round(secs, 1), train_peak_ram_mb=round(s.rss_mb),
               train_peak_gpu_mb=s.gpu_mb, model_mb=round(size, 2),
               rerun_max_prob_diff=float(np.abs(P1 - P2).max()), **info)
    # CPU-only training time
    if name == "xgb":
        t0 = time.perf_counter()
        _, _, i2 = train(name, d, X, tr, va, wx, 0, "cpu")
        res["cpu_train_seconds"] = round(time.perf_counter() - t0, 1)
    elif torchy:
        import torch
        torch.set_num_threads(16)
        _, _, i2 = fit(device="cpu", max_epochs=1)
        res["cpu_train_seconds_estimate"] = round(i2["seconds_per_epoch"] * info["epochs"], 1)
    else:
        res["cpu_train_seconds"] = res["train_seconds"]
    print(json.dumps(res))


def step_serve(name):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    t0 = time.perf_counter()
    if name.startswith("lgb"):
        import lightgbm  # noqa: F401
    elif name == "xgb":
        import xgboost  # noqa: F401
    elif name in ("mlp", "seq_gru"):
        import torch  # noqa: F401
    else:
        import sklearn.linear_model  # noqa: F401
    import_s = time.perf_counter() - t0
    d, X, tr, va, te, wx = data()
    import fe_seq as S
    extra_all = None
    if name == "seq_gru":
        t0 = time.perf_counter()
        A, _, B, _ = S.build_tokens(d, S.token_table(d), S.author_sequences(d), S.repo_sequences(d))
        tok_ms = (time.perf_counter() - t0) * 1000 / len(d)
        extra_all = (A, B)
    Xte = X[te]
    ite = np.flatnonzero(te)
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    t0 = time.perf_counter()
    meta, predict = load(name)
    load_s = time.perf_counter() - t0
    from scipy.special import softmax
    T = meta["cal"]
    cal = lambda Z: softmax(Z / np.exp(T[0]) + np.r_[0.0, T[1:]], axis=1)
    run = lambda lo, hi: cal(predict(Xte.iloc[lo:hi], None if extra_all is None else
                                     (extra_all[0][ite[lo:hi]], extra_all[1][ite[lo:hi]])))
    run(0, 256)                                                      # warm-up
    one = []
    for i in range(300):
        t1 = time.perf_counter()
        run(i, i + 1)
        one.append((time.perf_counter() - t1) * 1000)
    t1 = time.perf_counter()
    n = min(len(Xte), 256 * 40)
    for i in range(0, n, 256):
        run(i, i + 256)
    batch_ms = (time.perf_counter() - t1) * 1000 / n
    res = dict(member=name, import_seconds=round(import_s, 2), load_seconds=round(load_s, 3),
               serve_model_ram_mb=round((proc.memory_info().rss - rss0) / 2**20),
               latency_one_pr_ms_median=round(float(np.median(one)), 3),
               latency_one_pr_ms_p95=round(float(np.percentile(one, 95)), 3),
               latency_batch256_ms_per_pr=round(batch_ms, 4))
    if name == "seq_gru":
        res["token_build_ms_per_pr"] = round(tok_ms, 4)
    print(json.dumps(res))


def lib_footprint():
    import site
    sp = site.getsitepackages()[0]

    def du(prefixes):
        tot = 0
        for e in os.listdir(sp):
            if any(e.lower().startswith(p) for p in prefixes):
                for root, _, files in os.walk(os.path.join(sp, e)):
                    tot += sum(os.path.getsize(os.path.join(root, f)) for f in files
                               if not os.path.islink(os.path.join(root, f)))
        return round(tot / 2**20)
    return {"lightgbm": du(["lightgbm"]), "xgboost": du(["xgboost"]),
            "torch (with its CUDA libraries)": du(["torch", "nvidia", "triton"]),
            "scikit-learn + scipy": du(["sklearn", "scikit_learn", "scipy"]),
            "numpy + pandas (shared)": du(["numpy", "pandas"])}


def main():
    if sys.argv[1] in ("train", "serve"):
        (step_train if sys.argv[1] == "train" else step_serve)(sys.argv[2])
        return
    os.makedirs(OUT, exist_ok=True)
    results = {"libraries_mb": lib_footprint(), "members": {}}
    for name in (sys.argv[2:] or MEMBERS):
        row = {}
        for step in ("train", "serve"):
            r = subprocess.run([PY, "-W", "ignore", __file__, step, name], capture_output=True, text=True, cwd=HERE)
            line = [l for l in r.stdout.splitlines() if l.startswith("{")]
            if r.returncode or not line:
                print(r.stdout[-2000:], r.stderr[-3000:])
                raise SystemExit(f"{step} {name} failed")
            row.update(json.loads(line[-1]))
            print(name, step, line[-1], flush=True)
        results["members"][name] = row
    json.dump(results, open(os.path.join(HERE, "fe_profile.json"), "w"), indent=1)


if __name__ == "__main__":
    main()

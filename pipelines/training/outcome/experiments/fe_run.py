"""
fe_run.py - feature-engineering experiments. One LightGBM recipe; each config changes only features or rows.

    python fe_run.py --pass 1 [--only NAME ...]

Splits (both for every config):
  pr    20% of PRs held out by a hash of the head commit (PRs sharing a head stay together); repos are seen.
  repo  5 folds by repo hash; each repo is scored by a model that never saw it.
Scored rows: outside contributors (not a bot, not a confirmed maintainer), mature (90+ days), at-open diff not
`final`. Metric: eval_script.py's skill (1 - Brier / base-rate Brier, averaged over repos), base rate = the training
rows' outcome mix; plus log loss, accuracy, macro AUROC and calibration error.
Results append to fe_results.jsonl (one line per config and split); a config already there is skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import log_softmax, softmax
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # pipelines/training, for frames.py
import fe_features as F  # noqa: E402
import frames  # noqa: E402

DATA_DIR = os.environ.get("RIFFLE_DATA_DIR", os.path.expanduser("~/riffle-data/v0.1"))   # the unzipped miner data
DATA = os.path.join(DATA_DIR, "dataset_merged.parquet")
RESULTS = os.path.join(HERE, "fe_results.jsonl")
CACHE = os.path.join(DATA_DIR, "fe_prepared.parquet")
PARAMS = dict(objective="multiclass", num_class=3, learning_rate=0.1, num_leaves=31, min_data_in_leaf=50,
              feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, num_threads=16,
              verbose=-1)
ALPHA = 0.5


def h(s: str, salt: str) -> float:
    return int(hashlib.sha1(f"{salt}:{s}".encode()).hexdigest()[:12], 16) / 16**12


# ---------------------------------------------------------------- data
def load():
    raw = pd.read_parquet(DATA)
    allowed = [c for c in raw.columns if c not in frames.non_input_columns()]
    if os.path.exists(CACHE) and os.path.getmtime(CACHE) > os.path.getmtime(F.__file__):
        d = pd.read_parquet(CACHE)
    else:
        t0 = time.time()
        d = F.prepare(raw)
        d.to_parquet(CACHE)
        print(f"features built in {time.time() - t0:.0f}s", flush=True)
    d["u_pr"] = [h(s or f"{r}#{n}", "pr") for s, r, n in zip(d["head_sha"], d["repo"], d["pr_number"])]
    d["u_val"] = [h(s or f"{r}#{n}", "val") for s, r, n in zip(d["head_sha"], d["repo"], d["pr_number"])]
    d["fold"] = [int(h(r.lower(), "fold") * 5) % 5 for r in d["repo"]]
    return d, allowed


def encode(d, cols):
    X = {}
    for c in cols:
        s = d[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            X[c] = s.astype(float)
        elif set(s.dropna().unique().tolist()) <= {True, False}:
            X[c] = s.map({True: 1.0, False: 0.0}).astype(float)
        else:
            X[c] = pd.Categorical(s.where(s.isna(), s.astype(str)))
    return pd.DataFrame(X, index=d.index)


# ---------------------------------------------------------------- model
class Calibrator:
    def fit(self, Z, y, w):
        Y = np.eye(3)[y]

        def nll(t):
            return -(w * (Y * log_softmax(Z / np.exp(t[0]) + np.r_[0.0, t[1:]], axis=1)).sum(1)).sum() / w.sum()
        self.t = minimize(nll, np.zeros(3), method="L-BFGS-B").x
        return self

    def __call__(self, Z):
        return softmax(Z / np.exp(self.t[0]) + np.r_[0.0, self.t[1:]], axis=1)


def repo_weights(repo):
    n = pd.Series(repo).map(pd.Series(repo).value_counts()).to_numpy(float)
    w = n ** -ALPHA
    return w * len(w) / w.sum()


def fit_predict(X, d, tr, va, te, seed, cal_mask, wx=None):
    """Train on tr, stop early on va, calibrate on va & cal_mask, predict te. wx: extra row weights."""
    p = dict(PARAMS, seed=seed)
    wx = np.ones(len(d)) if wx is None else wx
    w = repo_weights(d["repo"].to_numpy()[tr]) * wx[tr]
    dtr = lgb.Dataset(X[tr], d["y"].to_numpy()[tr], weight=w)
    dva = lgb.Dataset(X[va], d["y"].to_numpy()[va], weight=wx[va], reference=dtr)
    m = lgb.train(p, dtr, 4000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
    cv = va & cal_mask
    cal = Calibrator().fit(m.predict(X[cv], raw_score=True), d["y"].to_numpy()[cv],
                           repo_weights(d["repo"].to_numpy()[cv]) * wx[cv])
    return cal(m.predict(X[te], raw_score=True)), m, cal


# ---------------------------------------------------------------- metrics
def metrics(y, P, repo, base, w=None):
    """w: optional row weights (the exact-diff view reweights rows so each repo keeps its full class mix)."""
    Y = np.eye(3)[y]
    w = np.ones(len(y)) if w is None else w
    b, rb = ((P - Y) ** 2).sum(1), ((base - Y) ** 2).sum(1)
    per = pd.DataFrame({"r": repo, "b": b * w, "rb": rb * w, "w": w}).groupby("r").sum()
    per["b"], per["rb"] = per["b"] / per["w"], per["rb"] / per["w"]
    ece = []
    for k in range(3):
        bins = np.minimum((P[:, k] * 10).astype(int), 9)
        g = pd.DataFrame({"b": bins, "p": P[:, k] * w, "y": Y[:, k] * w}).groupby("b").sum()
        ece.append(float((g["p"] - g["y"]).abs().sum() / w.sum()))      # weighted, 10 bins
    return dict(skill=float(1 - per["b"].mean() / per["rb"].mean()),
                log_loss=float(np.average(-np.log(np.clip(P[np.arange(len(y)), y], 1e-9, 1)), weights=w)),
                accuracy=float(np.average(P.argmax(1) == y, weights=w)),
                auroc=float(np.mean([roc_auc_score(Y[:, k], P[:, k], sample_weight=w) for k in range(3)])),
                calib=float(np.mean(ece)), prs=int(len(y)), repos=int(per.shape[0]))


# ---------------------------------------------------------------- runs
def run_split(d, X, split, rows, train_rows, seeds, per_repo=False, wx=None):
    """Returns (metrics, last model) for one split. rows = eligible rows (mature, not final); scored = rows & outside."""
    scored = rows & d["outside"].to_numpy()
    y, repo = d["y"].to_numpy(), d["repo"].to_numpy()
    out, model = [], None
    for seed in seeds:
        P = np.full((len(d), 3), np.nan)
        base = np.full((len(d), 3), np.nan)
        if split == "pr":
            te = scored & (d["u_pr"].to_numpy() < 0.2)
            rest = train_rows & (d["u_pr"].to_numpy() >= 0.2)
            va, tr = rest & (d["u_val"].to_numpy() < 0.15), rest & (d["u_val"].to_numpy() >= 0.15)
            if per_repo:
                for r in np.unique(repo[te]):
                    m_r = repo == r
                    P[te & m_r] = fit_predict(X, d, tr & m_r, va & m_r, te & m_r, seed, d["outside"].to_numpy(), wx)[0]
            else:
                P[te], model, _ = fit_predict(X, d, tr, va, te, seed, d["outside"].to_numpy(), wx)
            base[te] = np.bincount(y[tr & scored], minlength=3) / (tr & scored).sum()
            mask = te
        else:
            fold = d["fold"].to_numpy()
            for k in range(5):
                te = scored & (fold == k)
                rest = train_rows & (fold != k)
                reps = sorted(set(repo[rest]), key=lambda r: h(r, f"val{k}"))
                vrep = set(reps[:max(2, round(0.15 * len(reps)))])
                va = rest & np.isin(repo, list(vrep))
                tr = rest & ~va
                P[te], model, _ = fit_predict(X, d, tr, va, te, seed, d["outside"].to_numpy(), wx)
                base[te] = np.bincount(y[tr & scored], minlength=3) / (tr & scored).sum()
            mask = scored
        m_all = metrics(y[mask], P[mask], repo[mask], base[mask])
        # exact-diff view: production diffs are always exact, so score exact rows only, each weighted by
        # (scored test rows of its repo and class) / (exact ones among them)
        ex = mask & (d["open_snapshot"] == "exact").to_numpy()
        cnt = pd.DataFrame({"r": repo[mask], "y": y[mask], "x": ex[mask]}).groupby(["r", "y"])["x"].agg(["size", "sum"])
        wt = (cnt["size"] / cnt["sum"]).reindex(pd.MultiIndex.from_arrays([repo[ex], y[ex]])).to_numpy()
        m_ex = metrics(y[ex], P[ex], repo[ex], base[ex], wt)
        out.append({**m_all, **{"x_" + k: v for k, v in m_ex.items()}})
        np.savez_compressed(os.path.join(HERE, "fe_preds", f"{run_split.name}_{split}_s{seed}.npz"),
                            idx=np.flatnonzero(mask), P=P[mask], base=base[mask])
    m = {k: float(np.mean([o[k] for o in out])) for k in out[0]}
    if len(seeds) > 1:
        m["skill_seed_sd"] = float(np.std([o["skill"] for o in out]))
    return m, model


def configs(g, d, pass_=1):
    if pass_ == 2:
        return configs2(g, d)
    allowed = [c for c in g["allowed"] if c not in ("open_snapshot",)]
    clean = [c for c in allowed if c not in g["leaks"]]
    cons = [c for c in clean if c not in g["llm_detail"] and c not in g["redundant"]] + g["consolidated"]
    full = cons + g["author_history"] + g["repo_history"] + g["context"]
    C = {
        # name: (columns, options)
        "p1_00_all_allowed_with_leaks": (allowed, {}),
        "p1_01_leaks_dropped": (clean, {}),
        "p1_02_consolidated": (cons, {}),
        "p1_03_plus_author_history": (cons + g["author_history"], {}),
        "p1_04_plus_repo_history": (cons + g["author_history"] + g["repo_history"], {}),
        "p1_05_plus_context": (full, {}),
        "p1_06_plus_cross_repo_author": (full + g["cross_repo"], {}),
        # tests on the full set
        "p1_t1_drop_recovered_rows": (full, {"drop_recovered": True}),
        "p1_t2_train_outside_only": (full, {"train_outside": True}),
        "p1_t3_repo_as_feature": (full + ["repo"], {"splits": ["pr"]}),
        "p1_t3_per_repo_models": (full, {"splits": ["pr"], "per_repo": True}),
        "p1_t4_seed_noise": (full, {"seeds": [0, 1, 2]}),
    }
    return C


def configs2(g, d):
    allowed = [c for c in g["allowed"] if c not in ("open_snapshot",)]
    clean = [c for c in allowed if c not in g["leaks"]]
    cons = [c for c in clean if c not in g["llm_detail"] and c not in g["redundant"]] + g["consolidated"]
    base = cons + g["author_history"] + g["repo_history"]
    new = g["author_recent"] + g["repo_ft90"] + g["rel_size"]
    out = {"train_outside": True}
    has = lambda *p: [c for c in base if c.startswith(p)]
    prune = {
        "file_history": has("max_", "mean_", "min_", "sum_", "cochange_", "n_topk_", "touches_topk_",
                            "expected_test_cochange"),
        "scanners": has("semgrep_", "osv_", "ghsa_", "vuln_", "hardcoded_secret", "migration_rev", "api_contract",
                        "dependency_major", "n_deps_", "n_major_"),
        "repo_local": has("path_", "revealed_path", "similar_pr", "release_cycle"),
        "llm": [c for c in base if c in F.LLM_KEEP or c.startswith("c_llm")],
    }
    leaks = {
        "author_association": ["author_association"],
        "body": ["body_len", "body_headings", "body_checklist_items", "body_has_test_plan", "has_linked_issue",
                 "agent_boilerplate"],
        "similarity": ["max_sim_open_prs", "max_sim_recent_merged"],
        "agent": ["ai_agent_authored", "agent_name"],
        "author_prs_merged_repo": ["author_prs_merged_repo"],
    }
    C = {"p2_00_base": (base, dict(out, seeds=[0, 1, 2])),
         "p2_01_drop_recovered_reweighted": (base, dict(out, drop_recovered_rw=True))}
    for k, cols in leaks.items():
        C[f"p2_l_{k}_added_back"] = (base + cols, dict(out))
    for k, cols in prune.items():
        C[f"p2_p_without_{k}"] = ([c for c in base if c not in cols], dict(out))
    C["p2_n_author_recent"] = (base + g["author_recent"], dict(out))
    C["p2_n_repo_ft90"] = (base + g["repo_ft90"], dict(out))
    C["p2_n_rel_size"] = (base + g["rel_size"], dict(out))
    C["p2_n_all_new"] = (base + new, dict(out))
    C["p2_n_all_new_repo_as_feature"] = (base + new + ["repo"], dict(out, splits=["pr"]))
    # pass 2b: the deciding configs again, on the new default (recovered rows dropped, exact rows reweighted)
    rw = dict(out, drop_recovered_rw=True)
    C["p2b_rw_base_seeds"] = (base, dict(rw, seeds=[0, 1, 2]))
    for k in ("scanners", "file_history", "repo_local"):
        C[f"p2b_rw_without_{k}"] = ([c for c in base if c not in prune[k]], dict(rw))
    C["p2b_rw_all_new"] = (base + new, dict(rw))
    C["p2b_rw_all_new_repo_as_feature"] = (base + new + ["repo"], dict(rw, splits=["pr"]))
    C["p2b_rw_repo_as_feature"] = (base + ["repo"], dict(rw, splits=["pr"]))
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_", type=int, default=1)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    d, allowed = load()
    g = F.feature_groups(d, allowed)
    done = set()
    if os.path.exists(RESULTS):
        done = {(j["config"], j["split"]) for j in map(json.loads, open(RESULTS))}
    not_final = (d["open_snapshot"] != "final").to_numpy()
    base_rows = d["mature"].to_numpy() & not_final
    for name, (cols, opt) in configs(g, d, a.pass_).items():
        if a.only and name not in a.only:
            continue
        X = encode(d, cols)
        train_rows = base_rows.copy()
        if opt.get("drop_recovered"):
            train_rows &= (d["open_snapshot"] == "exact").to_numpy()
        if opt.get("train_outside"):
            train_rows &= d["outside"].to_numpy()
        wx = None
        if opt.get("drop_recovered_rw"):
            # exact rows only, each weighted by (eligible rows of its repo and class) / (exact ones among them)
            ex = (d["open_snapshot"] == "exact").to_numpy()
            key = pd.MultiIndex.from_arrays([d["repo"], d["y"]])
            cnt = pd.DataFrame({"r": d["repo"], "y": d["y"], "x": ex})[train_rows].groupby(["r", "y"])["x"].agg(
                ["size", "sum"])
            wx = np.nan_to_num((cnt["size"] / cnt["sum"].clip(lower=1)).reindex(key).to_numpy(), nan=1.0)
            train_rows &= ex
        for split in opt.get("splits", ["pr", "repo"]):
            if (name, split) in done:
                continue
            t0 = time.time()
            run_split.name = name
            m, model = run_split(d, X, split, base_rows, train_rows, opt.get("seeds", [0]), opt.get("per_repo", False),
                                 wx)
            row = dict(config=name, split=split, n_features=len(cols), seconds=round(time.time() - t0), **m)
            print(json.dumps(row), flush=True)
            with open(RESULTS, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            if split == "pr" and model is not None:
                imp = pd.Series(model.feature_importance("gain"), index=model.feature_name()).sort_values(ascending=False)
                imp.to_csv(os.path.join(HERE, "fe_importance", f"{name}.csv"))


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "fe_importance"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "fe_preds"), exist_ok=True)
    main()

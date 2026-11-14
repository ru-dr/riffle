# %% [markdown]
# # Riffle merge-outcome model: end-to-end pipeline
#
# **What this does.** Predicts, the moment a pull request opens, how it will end:
# `lands_clean`, `lands_after_rework` or `goes_nowhere`. It also predicts why a PR goes nowhere (`p_reason_*`,
# `p_close_reason_*`) and the post-merge labels (`p_label_*`). Everything runs from the miner's dataset table: our
# own point-in-time features, our own train/test split, and output in (at least) `sample_output.csv`'s 56-column
# schema.
#
# **The model ("setup G").** A geometric blend of LightGBM, XGBoost (trained on the CPU, for reproducibility) and
# logistic regression, then recalibrated; 3 seeds averaged. Two final versions:
#
# | Version | Tuned and calibrated on | Use when |
# |---|---|---|
# | `models/seen_repos` | held-out PRs of the training repos | scoring repos that were in the training data |
# | `models/new_repos` | held-out whole repos | scoring a repo the model has never seen (the worst case) |
#
# Both are trained on every eligible PR; they differ in how they are early-stopped and calibrated.
#
# **How to run.** From this folder: `jupyter nbconvert --to notebook --execute --inplace pipeline.ipynb` (about 30
# minutes on 16 cores), or open it and run all cells. Paths are relative to this folder. Settings come from
# environment variables:
#
# | Variable | Default | Meaning |
# |---|---|---|
# | `RIFFLE_DATA` | `riffle_mined_v0.1.zip` | the miner zip (put it in this folder), or a `dataset*.parquet` / `.jsonl` |
# | `RIFFLE_OUT` | this folder | where `models/`, `eval/` and `predictions/` go |
# | `RIFFLE_EVAL_SCRIPT` | `tools/eval_script.py`, else the repo's `../../evaluation/eval_script.py` | the team's evaluation script (skipped if absent) |
# | `RIFFLE_SAMPLE_OUTPUT` | `tools/sample_output.csv` | the team's sample output, to check the column order (skipped if absent) |
# | `RIFFLE_FAST` | unset | `1` = a quick check on a few repos, 1 seed, 3 folds |
#
# **Requirements.** Python 3.10+, numpy, pandas, pyarrow, scipy, scikit-learn, lightgbm, xgboost, and matplotlib for
# the evaluation script. No GPU needed.
# This notebook imports no project code.
#
# Why each choice was made (leaks, rows, features, model) is in `WRITEUP.md`, with the experiment log behind it.

# %%
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import warnings
import zipfile

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.optimize import minimize
from scipy.special import expit, log_softmax, softmax
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
pd.set_option("display.width", 180)
pd.set_option("display.max_columns", 40)

DATA = os.environ.get("RIFFLE_DATA", "riffle_mined_v0.1.zip")
HERE = os.path.abspath(os.environ.get("RIFFLE_OUT", "."))
EVAL_SCRIPT = os.environ.get("RIFFLE_EVAL_SCRIPT") or next(
    (p for p in (os.path.join("tools", "eval_script.py"), os.path.join("..", "..", "evaluation", "eval_script.py"))
     if os.path.exists(p)), os.path.join("tools", "eval_script.py"))
FAST = os.environ.get("RIFFLE_FAST") == "1"
SEEDS = [0] if FAST else [0, 1, 2]
FOLDS = 3 if FAST else 5
THREADS = os.cpu_count() or 4
MODELS, EVAL, PRED = (os.path.join(HERE, x) for x in ("models", "eval", "predictions"))
for p in (MODELS, EVAL, PRED):
    os.makedirs(p, exist_ok=True)


def h(s: str, salt: str) -> float:
    """A stable hash in [0, 1): the same PR or repo always lands on the same side of a split."""
    return int(hashlib.sha1(f"{salt}:{s}".encode()).hexdigest()[:12], 16) / 16 ** 12


print(f"data {DATA}\noutput {HERE}\nseeds {SEEDS}, folds {FOLDS}, threads {THREADS}, fast {FAST}")
print("lightgbm", lgb.__version__, "| xgboost", xgb.__version__, "| pandas", pd.__version__)

# %% [markdown]
# ## 1. Data
#
# Only the dataset table is used (one row per closed PR, features as at open). The snapshot and commit tables
# describe the PR *after* it opened, so a model that scores at open cannot use them.

# %%
def read_dataset(path: str) -> pd.DataFrame:
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith("dataset_merged.parquet")
                        or (n.endswith(".parquet") and "snapshot" not in n and "commit" not in n))
            return pd.read_parquet(io.BytesIO(z.read(name)))
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_json(path, lines=True)


raw = read_dataset(DATA)
if FAST:
    raw = raw[[h(r.lower(), "fast") < 0.3 for r in raw["repo"]]].reset_index(drop=True)
print(f"{len(raw):,} PRs, {raw.shape[1]} columns, {raw['repo'].nunique()} repos")
print(raw["outcome_3class"].value_counts(dropna=False).to_string())

# %% [markdown]
# ## 2. What the model may read
#
# **Leaks we drop** (each found in the exploratory analysis or the miner's documentation; evidence in `WRITEUP.md`):
#
# | Column | Why it leaks |
# |---|---|
# | `author_association` | GitHub's *current* status of the author, read at mining time. First-timers whose PR went nowhere stay `NONE` (92% goes nowhere); those whose PR later landed become `CONTRIBUTOR` (20%). Alone worth +0.12 skill. |
# | `body_len`, `body_headings`, `body_checklist_items`, `body_has_test_plan`, `has_linked_issue`, `agent_boilerplate` | The PR body is read at mining time, so it includes edits made after open. |
# | `max_sim_open_prs`, `max_sim_recent_merged` | Text similarity using that same mining-time body. |
# | `ai_agent_authored`, `agent_name` | Partly detected from body watermarks. (`head_branch_agent_prefix` stays: no body.) |
# | `author_prs_merged_repo` | Blank only on merged PRs, so a blank says "merged"; our own author history replaces it. |
#
# **Rows.** Training and scoring use PRs with a known outcome, opened at least 90 days before the data was collected
# (so the outcome has settled), whose at-open diff is not the `final` one (the miner could not recover the diff as at
# open). Models train on, and are scored on, **outside contributors**: not a bot, not a confirmed maintainer before
# this PR opened.
#
# **Recovered diffs.** About 42% of rework PRs have a *recovered* (not exact) at-open diff, against almost no clean
# PRs, and a model can tell recovered from exact at AUROC 0.64 from which columns are blank alone. In production the
# diff is always exact, so models train on exact diffs only, **reweighted** so every repo keeps its full class mix.
#
# **Features (161).** 131 miner columns plus 30 of our own, all as at open:
# - *Consolidated* (`c_*`): first commit ever, specialisation (SEXP / EXP), churn, share deleted, files per directory,
#   whether the LLM ran and how many of its 18 detail answers flagged something.
# - *Author history in this repo* (`ah_*`): counts and smoothed rates of the author's earlier PRs by outcome, PRs open
#   now, first PR, days since the previous PR, tenure.
# - *Repo recent behaviour* (`rh*`): last-90-day outcome mix, PRs open now, PRs opened in 30 days, typical time to
#   close, and how the repo treated outside first-timers and outside authors over 180 days.
#
# History counts a past PR **only if it closed strictly before this PR opened**. Section 3 tests that.

# %%
CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")
REASONS = ("abandoned", "duplicate", "unwanted", "ci_technical", "unknown")
CLOSE_NAME = {"abandoned": "abandoned", "duplicate": "duplicate", "unwanted": "unwanted",
              "ci_technical": "ci_technical", "unknown": "other"}
DAY = 86400
MATURITY_DAYS = 90
LEAKS = ("author_association", "body_len", "body_headings", "body_checklist_items", "body_has_test_plan",
         "has_linked_issue", "agent_boilerplate", "max_sim_open_prs", "max_sim_recent_merged", "ai_agent_authored",
         "agent_name", "author_prs_merged_repo")
LLM_DETAIL = ("llm_touches_authn_authz", "authz_check_change", "changes_public_api_signature", "error_handling_change",
              "exception_swallowed", "db_schema_or_query_change", "transaction_boundary_change",
              "input_validation_change", "config_or_feature_flag_change", "feature_flag_guarding",
              "security_sensitive_ops", "resource_lifecycle_change", "null_handling_change", "numeric_money_precision",
              "time_handling_change", "retry_timeout_idempotency_change", "test_code_alignment", "satd_added")
QUIET = {"none", "n_a", "no", "pure_refactor", "aligned", "False", "false"}
MINER_FEATURES = __MINER__
ENGINEERED = __ENGINEERED__
FEATURES = MINER_FEATURES + ENGINEERED
CATEGORICAL = ("migration_reversibility", "ghsa_max_severity_introduced", "api_contract_change",
               "refactor_vs_behavior", "change_intent", "tests_added_for_behavior_change")
assert not set(FEATURES) & set(LEAKS)
missing = [c for c in MINER_FEATURES if c not in raw.columns]
print(f"{len(FEATURES)} features ({len(MINER_FEATURES)} miner, {len(ENGINEERED)} engineered); "
      f"missing from this table (treated as unknown): {missing or 'none'}")

# %% [markdown]
# ## 3. Feature engineering
#
# `prior_sum` is the one counting primitive: for each PR, sum something over PRs with the same key (author, repo)
# whose event time is strictly before this PR's open time, optionally inside a window. It sorts once and uses binary
# search, so all history for 190,000 PRs takes a few seconds.

# %%
OFF = np.int64(1) << 36          # key * OFF + seconds: one sortable number per (key, time)


def secs(s: pd.Series) -> np.ndarray:
    """Seconds since 1970, whatever unit the timestamps are stored in (parquet often gives microseconds)."""
    t = pd.to_datetime(s, utc=True, errors="coerce")
    return ((t - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy(np.int64)


def _own_keys(key):
    """Unknown keys (< 0) each get a key of their own, so their events never count for anyone else."""
    key = np.asarray(key, np.int64)
    k = key.copy()
    bad = key < 0
    k[bad] = key.max(initial=0) + 1 + np.arange(int(bad.sum()))
    return key, k


def prior_sum(key, t_query, t_event, W, window=None):
    """For each row i: the sum of W[j] over rows j with key[j] == key[i] and t_event[j] < t_query[i]
    (and t_event[j] >= t_query[i] - window when given). Unknown key: NaN."""
    key, k = _own_keys(key)
    W = np.asarray(W, float).reshape(len(key), -1)
    ev = k * OFF + t_event
    order = np.argsort(ev, kind="stable")
    evs = ev[order]
    cum = np.vstack([np.zeros((1, W.shape[1])), np.cumsum(W[order], axis=0)])
    q = k * OFF + t_query
    lo = k * OFF if window is None else np.maximum(q - window, k * OFF)
    out = cum[np.searchsorted(evs, q, side="left")] - cum[np.searchsorted(evs, lo, side="left")]
    out[key < 0] = np.nan
    return out


def prev_event_gap(key, t_query, t_event):
    """Seconds since the latest event of the same key strictly before t_query (NaN when none)."""
    key, k = _own_keys(key)
    evs = np.sort(k * OFF + t_event)
    pos = np.searchsorted(evs, k * OFF + t_query, side="left") - 1
    prev = evs[np.clip(pos, 0, None)]
    ok = (pos >= 0) & (prev // OFF == k) & (key >= 0)
    return np.where(ok, t_query - (prev - k * OFF), np.nan).astype(float)


def first_event_gap(key, t_query, t_event):
    """Seconds since the key's first event, if that event is strictly before t_query (NaN otherwise)."""
    key, k = _own_keys(key)
    first = pd.Series(t_event).groupby(k).transform("min").to_numpy()
    return np.where((first < t_query) & (key >= 0), t_query - first, np.nan).astype(float)


def _rates(counts, n, prior, strength):
    """Class rates shrunk towards `prior`: (count + strength * prior) / (n + strength)."""
    return (counts + strength * prior) / (n[:, None] + strength)


def _flag(s: pd.Series) -> np.ndarray:
    """True / False / unknown (NaN) from bools, 0/1 or strings."""
    m = s.map(lambda v: 1.0 if v is True or v == 1 or str(v).lower() == "true" else
              0.0 if v is False or v == 0 or str(v).lower() == "false" else np.nan)
    return m.to_numpy(float)


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """The miner's table -> cleaned rows with every engineered feature and every training target.
    History uses every PR in the table (bots, maintainers and `final` rows included: their outcomes are real)."""
    d = raw.reset_index(drop=True).copy()
    d["y"] = d["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).fillna(-1).astype(int)
    known = (d["y"] >= 0).to_numpy()
    d["t_open"] = secs(d["created_at"])
    tc = secs(d["closed_at"]) if "closed_at" in d else np.full(len(d), np.iinfo(np.int64).min)
    tc = np.where(tc < 0, np.int64(2) ** 34, tc)                  # not closed (or unknown): far in the future
    d["t_close"] = np.maximum(tc, d["t_open"].to_numpy())         # a close stamped before the open is not earlier
    obs = pd.to_datetime(d.get("observed_through"), utc=True, errors="coerce") if "observed_through" in d else None
    cut = (obs.groupby(d["repo"]).transform("max") if obs is not None else pd.Series(pd.NaT, index=d.index))
    fallback = pd.to_datetime(d["closed_at"], utc=True, errors="coerce").groupby(d["repo"]).transform("max")
    d["observed_through"] = (obs if obs is not None else cut).fillna(cut).fillna(fallback)
    d["t_obs"] = secs(d["observed_through"])
    d["mature"] = (d["t_obs"] - d["t_open"]) >= MATURITY_DAYS * DAY
    flag0 = lambda c: (np.nan_to_num(_flag(d[c])) == 1) if c in d else np.zeros(len(d), bool)
    d["outside"] = ~flag0("author_is_bot") & ~flag0("author_confirmed_maintainer")
    d["author_key"] = d["author_email"].map(lambda e: e.strip().lower() if isinstance(e, str) and e.strip() else None)
    for c in MINER_FEATURES:
        if c not in d:
            d[c] = np.nan
    # repair: a blank head_from_fork is a fork deleted later; when the webhook fires the fork exists
    d["head_from_fork"] = pd.Series(_flag(d["head_from_fork"])).fillna(1.0).to_numpy()

    # consolidated columns
    num = lambda c: pd.to_numeric(d[c], errors="coerce")
    exp = num("EXP")
    d["c_exp_zero"] = (exp == 0).astype(float).where(exp.notna())
    d["c_exp_specialisation"] = num("SEXP") / (exp + 1)
    la, ld = num("LA"), num("LD")
    d["c_churn"] = la + ld
    d["c_delete_share"] = ld / (la + ld).where((la + ld) > 0)
    d["c_files_per_dir"] = num("NF") / num("ND").clip(lower=1)
    ran = d["semantic_risk_1to5"].notna().to_numpy()
    flags = np.zeros(len(d))
    for c in LLM_DETAIL:
        if c in d:
            flags += d[c].notna().to_numpy() & ~d[c].astype(str).isin(QUIET).to_numpy()
    d["c_llm_ran"] = ran.astype(float)
    d["c_llm_flags"] = np.where(ran, flags, np.nan)

    # history
    t, tc = d["t_open"].to_numpy(), d["t_close"].to_numpy()
    # a PR whose close time is unknown (1,339 goes-nowhere PRs in v0.1) never adds an outcome or an open/closed
    # count to anyone's history: when its outcome became known is unknown. It still counts as having opened.
    hist_ok = known & pd.to_datetime(d["closed_at"], utc=True, errors="coerce").notna().to_numpy() \
        if "closed_at" in d else np.zeros(len(d), bool)
    Y = np.eye(3)[np.clip(d["y"].to_numpy(), 0, 2)] * hist_ok[:, None]
    ones = np.ones(len(d))
    ones_h = hist_ok.astype(float)
    repo = pd.factorize(d["repo"])[0]
    ar = pd.factorize(d["repo"] + "\x00" + d["author_key"].fillna("\x01"))[0]
    ar[d["author_key"].isna().to_numpy()] = -1
    r365 = prior_sum(repo, t, tc, Y, 365 * DAY)
    prior = (r365 + 1.0) / (r365.sum(1, keepdims=True) + 3.0)
    a = prior_sum(ar, t, tc, Y)
    an = a.sum(1)
    opened = prior_sum(ar, t, t, ones)[:, 0]
    d["ah_n"], d["ah_merged"], d["ah_nowhere"] = an, a[:, 0] + a[:, 1], a[:, 2]
    rate = _rates(a, an, prior, 3.0)
    d["ah_clean_rate"], d["ah_rework_rate"], d["ah_nowhere_rate"] = rate.T
    d["ah_open_now"] = prior_sum(ar, t, t, ones_h)[:, 0] - prior_sum(ar, t, tc, ones_h)[:, 0]
    d["ah_first_pr"] = ((an == 0) & (opened == 0)).astype(float)
    d["ah_days_since_prev_pr"] = prev_event_gap(ar, t, t) / DAY
    d["ah_tenure_days"] = first_event_gap(ar, t, t) / DAY
    d.loc[d["author_key"].isna(), [c for c in ENGINEERED if c.startswith("ah_")]] = np.nan
    r90 = prior_sum(repo, t, tc, Y, 90 * DAY)
    rn = r90.sum(1)
    d["rh90_n"] = rn
    rr = np.where(rn[:, None] >= 5, r90 / np.maximum(rn, 1)[:, None], np.nan)
    d["rh90_clean_rate"], d["rh90_rework_rate"], d["rh90_nowhere_rate"] = rr.T
    d["rh_open_now"] = prior_sum(repo, t, t, ones_h)[:, 0] - prior_sum(repo, t, tc, ones_h)[:, 0]
    d["rh30_opened"] = prior_sum(repo, t, t, ones, 30 * DAY)[:, 0]
    s = prior_sum(repo, t, tc, np.c_[np.log1p((tc - t) / 3600.0) * hist_ok, hist_ok], 90 * DAY)
    d["rh90_mean_log_hours_to_close"] = np.where(s[:, 1] >= 5, s[:, 0] / np.maximum(s[:, 1], 1), np.nan)
    base = np.where(np.isnan(rr), prior, rr)
    outside = d["outside"].to_numpy()
    for name, mask in (("ft", outside & (an == 0)), ("out", outside)):
        c = prior_sum(repo, t, tc, Y * mask[:, None], 180 * DAY)
        n = c.sum(1)
        r = _rates(c, n, base, 5.0)
        d[f"rh180_{name}_n"] = n
        d[f"rh180_{name}_clean_rate"], d[f"rh180_{name}_nowhere_rate"] = r[:, 0], r[:, 2]

    # training targets for the reason and label models (NaN = not observed: never coerced to 0)
    merged = (d["y"] < 2).to_numpy() & known
    nw = (d["y"] == 2).to_numpy()
    rcode = d["nowhere_reason"].map({r: i for i, r in enumerate(REASONS)}) if "nowhere_reason" in d else np.nan
    d["t_reason"] = np.where(nw, rcode, np.nan)
    col = lambda c: _flag(d[c]) if c in d else np.full(len(d), np.nan)
    for task, lab, win in (("revert", "label_revert", "revert_window_closed"),
                           ("hotfix", "label_hotfix", "hotfix_window_closed"),
                           ("szz_bug", "label_szz_bug", "szz_window_closed")):
        f, o = col(lab), col(win)
        d[f"t_{task}"] = np.where(merged & ((o == 1) | (f == 1)), f == 1, np.nan)
    # label_bad, the team's definition (Nishit's input files, ADR 0021): revert OR hotfix, on PRs merged 30+ days
    # before the data was observed, observed when both windows closed or either fired
    fr, fh = col("label_revert") == 1, col("label_hotfix") == 1
    seen_r = (col("revert_window_closed") == 1) | fr
    seen_h = (col("hotfix_window_closed") == 1) | fh
    age_ok = (d["t_obs"] - d["t_close"]).to_numpy() >= 30 * DAY
    bad_obs = merged & age_ok & ((seen_r & seen_h) | fr | fh)
    d["t_bad"] = np.where(bad_obs, fr | fh, np.nan)
    d["label_bad_team"] = pd.array(np.where(merged & age_ok, fr | fh, np.nan), dtype="boolean")
    d["label_bad_team_observed"] = pd.array(np.where(merged, bad_obs, np.nan), dtype="boolean")
    # label_bad, the miner's definition (any post-merge breakage signal, CI included), where the miner observed it
    lb, lbo = col("label_bad"), col("label_bad_observed")
    d["t_bad_miner"] = np.where(merged & (lbo == 1), lb == 1, np.nan)
    return d


t0 = time.time()
d = prepare(raw)
print(f"features built in {time.time() - t0:.1f}s")
d[ENGINEERED].describe().T[["count", "mean", "50%", "max"]].round(3)

# %% [markdown]
# **Point-in-time test.** On made-up data: compute every engineered feature, then again with everything after a cut
# date unknown (PRs opened later removed; PRs closed later still open, their outcome scrambled). Every feature of a PR
# opened before the cut must be identical. Then the stale-open rows (no `closed_at`: still open when mined, labelled
# abandoned at mining): scrambling their outcomes must change no feature. Plus checks of the counting primitive and
# of timestamp units.

# %%
def _toy(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    created = pd.Timestamp("2024-01-01", tz="UTC") + pd.to_timedelta(rng.uniform(0, 400, n), unit="D")
    closed = created + pd.to_timedelta(rng.exponential(10, n), unit="D")
    return pd.DataFrame({
        "repo": rng.choice(["a/x", "b/y", "c/z"], n), "pr_number": np.arange(n),
        "author_email": rng.choice([f"u{i}@e.com" for i in range(60)] + [None], n),
        "created_at": created, "closed_at": closed, "observed_through": closed.max(),
        "outcome_3class": rng.choice(CLASSES, n), "author_is_bot": False,
        "author_confirmed_maintainer": rng.random(n) < 0.1, "head_from_fork": rng.choice([True, False, None], n),
        "EXP": rng.integers(0, 50, n), "SEXP": rng.integers(0, 10, n), "LA": rng.integers(0, 500, n),
        "LD": rng.integers(0, 100, n), "NF": rng.integers(1, 20, n), "ND": rng.integers(1, 5, n),
        "semantic_risk_1to5": rng.choice([1, 3, None], n), "nowhere_reason": None})


toy = _toy()
cut = pd.Timestamp("2024-08-01", tz="UTC")
early = toy[toy["created_at"] < cut].copy()
late = early["closed_at"] >= cut
early.loc[late, "closed_at"] = pd.Timestamp("2099-01-01", tz="UTC")
early.loc[late, "outcome_3class"] = np.random.default_rng(1).choice(CLASSES, int(late.sum()))
full, part = prepare(toy).set_index("pr_number"), prepare(early).set_index("pr_number")
hist = [c for c in ENGINEERED if c.startswith(("ah_", "rh"))]
bad = [c for c in hist if not np.allclose(full.loc[part.index, c].to_numpy(float), part[c].to_numpy(float),
                                          equal_nan=True)]
assert not bad, f"features that see the future: {bad}"
# stale-open rows (closed_at null: still open when mined, labelled at mining) lend no outcome to anyone's history
stale = np.random.default_rng(2).random(len(toy)) < 0.08
a, b = toy.copy(), toy.copy()
a.loc[stale, "closed_at"] = b.loc[stale, "closed_at"] = pd.NaT
a.loc[stale, "outcome_3class"] = "goes_nowhere"
b.loc[stale, "outcome_3class"] = np.random.default_rng(3).choice(CLASSES, int(stale.sum()))
fa, fb = prepare(a).set_index("pr_number"), prepare(b).set_index("pr_number")
bad = [c for c in hist if not np.allclose(fa[c].to_numpy(float), fb.loc[fa.index, c].to_numpy(float), equal_nan=True)]
assert not bad, f"features that read a stale-open row's outcome: {bad}"
s = prior_sum(np.array([0, 0, 0, 1, -1]), np.array([10, 20, 30, 20, 20]), np.array([15, 40, 35, 5, 1]),
              np.ones(5))[:, 0]
assert s[0] == 0 and s[1] == 1 and s[2] == 1 and s[3] == 1 and np.isnan(s[4])
for unit in ("ns", "us", "ms", "s"):
    assert secs(pd.Series(pd.to_datetime(["1970-01-02T00:00:00Z"]).as_unit(unit)))[0] == 86400
print(f"point-in-time and stale-open tests passed for {len(hist)} history features; counting and unit checks passed")

# %% [markdown]
# ## 4. Rows, weights and splits
#
# - **Scored rows:** known outcome, mature, diff not `final`, outside contributor.
# - **Training rows:** scored rows whose at-open diff is exact, each weighted by (scored rows of its repo and class) /
#   (exact ones among them), times 1/sqrt(PRs in its repo) so the biggest repos do not dominate.
# - **By PR:** 20% of PRs held out by a hash of the head commit (PRs sharing a head stay together).
# - **By repo:** 5 folds by a hash of the repo; each repo is scored by a model that never saw it.

# %%
known = (d["y"] >= 0).to_numpy()
not_final = (d["open_snapshot"] != "final").to_numpy() if "open_snapshot" in d else np.ones(len(d), bool)
exact = (d["open_snapshot"] == "exact").to_numpy() if "open_snapshot" in d else np.ones(len(d), bool)
scored = known & d["mature"].to_numpy() & not_final & d["outside"].to_numpy()
train_ok = scored & exact
cnt = pd.DataFrame({"r": d["repo"], "y": d["y"], "x": exact})[scored].groupby(["r", "y"])["x"].agg(["size", "sum"])
WX = np.nan_to_num((cnt["size"] / cnt["sum"].clip(lower=1)).reindex(
    pd.MultiIndex.from_arrays([d["repo"], d["y"]])).to_numpy(), nan=1.0)
key = [s if isinstance(s, str) and s else f"{r}#{n}" for s, r, n in zip(d["head_sha"], d["repo"], d["pr_number"])]
U_PR = np.array([h(k, "pr") for k in key])
U_VAL = np.array([h(k, "val") for k in key])
FOLD = np.array([int(h(r.lower(), "fold") * FOLDS) % FOLDS for r in d["repo"]])
REPO, Y = d["repo"].to_numpy(), d["y"].to_numpy()
summary = pd.DataFrame({
    "PRs": [len(d), int(known.sum()), int((known & d["mature"]).sum()), int((known & d["mature"] & not_final).sum()),
            int(scored.sum()), int(train_ok.sum())]},
    index=["in the table", "known outcome", "+ mature (90 days)", "+ diff not final", "+ outside contributor (scored)",
           "+ exact diff (trained on)"])
print(summary.to_string())
print("\nclass mix of scored rows:", dict(zip(CLASSES, np.round(np.bincount(Y[scored], minlength=3) / scored.sum(), 3))))

# %% [markdown]
# ## 5. The model
#
# For the outcome, per seed: LightGBM, XGBoost and logistic regression are each trained on the training rows, early
# stopped (the trees) on the validation rows and calibrated there by **vector scaling** (softmax(z / T + b)). Their
# calibrated probabilities are blended **geometrically** (a weighted mean of log probabilities) with weights, a
# temperature and biases fitted on the same validation rows. Seeds are averaged at scoring time.
#
# The other outputs are LightGBM models on the same features:
# - **why a PR goes nowhere** (5 reasons, goes-nowhere PRs only), vector-scaled;
# - **post-merge labels** (bad as the team defines it, bad as the miner defines it, revert, hotfix, SZZ bug), merged PRs
#   whose label was observed, heavily regularised and Platt-scaled (a binary model with few positives is easily
#   overconfident). With too few observed positives to train on, a label falls back to its training base rate, so
#   the output is never blank.
# - For repos the model has not seen (the by-repo evaluation and `new_repos`), the reason probabilities are **half
#   model, half training base rate**. Why a PR dies is labelled by repo-specific rules and an LLM, and the unshrunk
#   reason model was worse than the base rate on unseen repos (skill -0.02 in a by-repo test; half and half gave
#   +0.014). A share fitted on validation repos came out near 1 (the calibration is fitted on the same repos), so
#   the share is fixed. It was chosen on these same repos, so the by-repo reason scores are slightly optimistic.
#
# Output conventions (as `sample_output.csv`):
# - `p_label_merged = p_clean + p_rework`
# - `p_label_rework = p_rework / p_label_merged` (rework, given that it merges)
# - `p_label_<reason>` and `p_close_reason_<reason>` = `p_reason_<reason>` (given that it goes nowhere)

# %%
class Encoder:
    """Raw columns -> the model matrix: numbers as floats, yes/no as 1/0, categories with the training levels."""

    def __init__(self, levels=None):
        self.levels = levels or {}

    def fit(self, df):
        self.levels = {c: sorted(df[c].dropna().astype(str).unique().tolist()) for c in CATEGORICAL}
        return self

    def transform(self, df):
        out = {}
        for c in FEATURES:
            s = df[c] if c in df else pd.Series(np.nan, index=df.index)
            if self.levels.get(c):
                out[c] = pd.Categorical(s.where(s.isna(), s.astype(str)), categories=self.levels[c])
            elif c in CATEGORICAL:      # no value seen in training: unknown everywhere (XGBoost needs a level)
                out[c] = np.full(len(df), np.nan)
            elif s.dtype == object or pd.api.types.is_bool_dtype(s):
                out[c] = pd.Series(_flag(s), index=df.index) if s.dtype == object else s.astype(float)
            else:
                out[c] = pd.to_numeric(s, errors="coerce").astype(float)
        return pd.DataFrame(out, index=df.index)


class Dense:
    """For logistic regression: signed log1p, standardised, missing flags, one-hot categories. Saved as JSON."""

    def fit(self, X):
        self.num = [c for c in X.columns if not isinstance(X[c].dtype, pd.CategoricalDtype)]
        self.cat = {c: list(X[c].cat.categories) for c in X.columns if c not in self.num}
        Z = self._log(X[self.num])
        self.mu, self.sd = Z.mean().fillna(0), Z.std().replace(0, 1).fillna(1)
        self.miss = [c for c in self.num if X[c].isna().any()]
        return self

    @staticmethod
    def _log(Z):
        return np.sign(Z) * np.log1p(np.abs(Z))

    def transform(self, X):
        Z = ((self._log(X[self.num]) - self.mu) / self.sd).clip(-6, 6)
        parts = [Z.fillna(0).to_numpy(np.float32), X[self.miss].isna().to_numpy(np.float32)]
        for c, levels in self.cat.items():
            codes = pd.Categorical(X[c].astype(object), categories=levels).codes
            oh = np.zeros((len(X), len(levels)), np.float32)
            oh[np.flatnonzero(codes >= 0), codes[codes >= 0]] = 1
            parts.append(oh)
        return np.hstack(parts)

    def to_dict(self):
        return {"num": self.num, "cat": self.cat, "mu": self.mu.tolist(), "sd": self.sd.tolist(), "miss": self.miss}

    @classmethod
    def from_dict(cls, o):
        e = cls()
        e.num, e.cat, e.miss = o["num"], o["cat"], o["miss"]
        e.mu, e.sd = pd.Series(o["mu"], index=e.num), pd.Series(o["sd"], index=e.num)
        return e


def repo_weights(repo):
    n = pd.Series(repo).map(pd.Series(repo).value_counts()).to_numpy(float)
    w = n ** -0.5
    return w * len(w) / w.sum()


def fit_vector(Z, y, w):
    K = Z.shape[1]
    Yk = np.eye(K)[y]

    def nll(t):
        return -(w * (Yk * log_softmax(Z / np.exp(t[0]) + np.r_[0.0, t[1:]], axis=1)).sum(1)).sum() / w.sum()
    return minimize(nll, np.zeros(K), method="L-BFGS-B").x


def apply_vector(Z, t):
    return softmax(Z / np.exp(t[0]) + np.r_[0.0, t[1:]], axis=1)


def fit_platt(z, y, w):
    def nll(t):
        q = np.clip(expit(t[0] * z + t[1]), 1e-9, 1 - 1e-9)
        return -(w * (y * np.log(q) + (1 - y) * np.log(1 - q))).sum() / w.sum()
    return minimize(nll, np.array([1.0, 0.0]), method="L-BFGS-B").x


def fit_blend(Ps, y, w):
    """Geometric blend: softmax(sum_m w_m log P_m) with weights on the simplex, then temperature and biases."""
    k = len(Ps)
    L = [np.log(np.clip(P, 1e-9, 1)) for P in Ps]

    def nll(th):
        wm = softmax(np.r_[0.0, th[:k - 1]])
        Z = np.log(np.clip(softmax(sum(a * l for a, l in zip(wm, L)), axis=1), 1e-9, 1))
        Q = log_softmax(Z / np.exp(th[k - 1]) + np.r_[0.0, th[k:]], axis=1)
        return -(w * Q[np.arange(len(y)), y]).sum() / w.sum()
    return minimize(nll, np.zeros(k + 2), method="L-BFGS-B").x


def apply_blend(Ps, th):
    k = len(Ps)
    wm = softmax(np.r_[0.0, th[:k - 1]])
    Z = np.log(np.clip(softmax(sum(a * np.log(np.clip(P, 1e-9, 1)) for a, P in zip(wm, Ps)), axis=1), 1e-9, 1))
    return softmax(Z / np.exp(th[k - 1]) + np.r_[0.0, th[k:]], axis=1)


LGB_OUTCOME = dict(objective="multiclass", num_class=3, learning_rate=0.05, num_leaves=31, min_data_in_leaf=50,
                   feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
                   num_threads=THREADS, verbose=-1)
XGB_OUTCOME = dict(objective="multi:softprob", num_class=3, tree_method="hist", device="cpu", eta=0.05, max_depth=6,
                   min_child_weight=20, subsample=0.8, colsample_bytree=0.7, reg_lambda=5.0, max_cat_to_onehot=8,
                   eval_metric="mlogloss", nthread=THREADS)
LGB_REASON = dict(objective="multiclass", num_class=len(REASONS), learning_rate=0.05, num_leaves=15,
                  min_data_in_leaf=100, feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
                  num_threads=THREADS, verbose=-1)
LGB_BINARY = dict(objective="binary", learning_rate=0.02, num_leaves=7, min_data_in_leaf=300, feature_fraction=0.5,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=50.0, num_threads=THREADS, verbose=-1)
BINARY_TASKS = ("bad", "bad_miner", "revert", "hotfix", "szz_bug")
MEMBERS = ("lgb", "xgb", "logreg")


def _lgb(params, X, y, w, Xv, yv, wv, seed, rounds=6000):
    dtr = lgb.Dataset(X, y, weight=w)
    return lgb.train(dict(params, seed=seed), dtr, rounds, valid_sets=[lgb.Dataset(Xv, yv, weight=wv, reference=dtr)],
                     callbacks=[lgb.early_stopping(150, verbose=False)])


def member_scores(name, m, X):
    """Raw class scores of one outcome member."""
    if name == "lgb":
        return m.predict(X, raw_score=True)
    if name == "xgb":
        booster, n = m
        return np.log(np.clip(booster.predict(xgb.DMatrix(X, enable_categorical=True), iteration_range=(0, n)),
                              1e-9, 1))
    enc, lr = m
    return lr.decision_function(enc.transform(X))


REASON_SHRINK_NEW_REPOS = 0.5   # model share of the reason probabilities for repos the model has not seen


def fit_bundle(X, tr, va, seeds, label="", reason_lam=1.0):
    """Every model for one split: the outcome blend, the reason model and the label models, per seed.
    reason_lam: model share of the reason probabilities (the rest is the training base rate)."""
    t0 = time.time()
    enc = Encoder().fit(d.loc[tr])
    Xe = enc.transform(d) if X is None else X
    w = repo_weights(REPO) * WX            # within-split weights are recomputed below
    bundle = {"encoder": enc, "seeds": {}}
    ytr, yva = Y[tr], Y[va]
    wtr, wva = repo_weights(REPO[tr]) * WX[tr], repo_weights(REPO[va]) * WX[va]
    mix = {}
    first = (d["ah_n"].fillna(0).to_numpy() == 0)
    for kind, m_ in (("first_time", first), ("returning", ~first)):
        sel = tr & m_
        mix[kind] = ((np.bincount(Y[sel], minlength=3) + 1) / (sel.sum() + 3)).tolist()
    bundle["heuristic"] = mix
    for seed in seeds:
        out = {}
        lg = _lgb(LGB_OUTCOME, Xe[tr], ytr, wtr, Xe[va], yva, wva, seed)
        xg = xgb.train(dict(XGB_OUTCOME, seed=seed), xgb.DMatrix(Xe[tr], ytr, weight=wtr, enable_categorical=True),
                       6000, evals=[(xgb.DMatrix(Xe[va], yva, weight=wva, enable_categorical=True), "va")],
                       early_stopping_rounds=150, verbose_eval=False)
        dn = Dense().fit(Xe[tr])
        lr = LogisticRegression(C=0.1, max_iter=2000).fit(dn.transform(Xe[tr]), ytr, sample_weight=wtr)
        models = {"lgb": lg, "xgb": (xg, xg.best_iteration + 1), "logreg": (dn, lr)}
        cal, Pv = {}, []
        for name in MEMBERS:
            Zv = member_scores(name, models[name], Xe[va])
            cal[name] = fit_vector(Zv, yva, wva)
            Pv.append(apply_vector(Zv, cal[name]))
        out["outcome"] = {"models": models, "cal": cal, "blend": fit_blend(Pv, yva, wva)}
        # why it goes nowhere
        rt, rv = tr & np.isfinite(d["t_reason"].to_numpy()), va & np.isfinite(d["t_reason"].to_numpy())
        yr = d["t_reason"].to_numpy()
        rm = _lgb(LGB_REASON, Xe[rt], yr[rt].astype(int), repo_weights(REPO[rt]) * WX[rt], Xe[rv],
                  yr[rv].astype(int), WX[rv], seed)
        rcal = fit_vector(rm.predict(Xe[rv], raw_score=True), yr[rv].astype(int), WX[rv])
        rbase = np.bincount(yr[rt].astype(int), weights=WX[rt], minlength=len(REASONS))
        rbase = rbase / rbase.sum()
        rlam = reason_lam
        out["reason"] = (rm, rcal, rlam, rbase)
        # post-merge labels
        out["binary"] = {}
        for task in BINARY_TASKS:
            t = d[f"t_{task}"].to_numpy(float)
            bt, bv = tr & np.isfinite(t), va & np.isfinite(t)
            if bt.sum() < 500 or t[bt].sum() < 20 or bv.sum() < 100 or t[bv].sum() < 5:
                # too few observed positives for a model: the training base rate (fails open, never blank)
                rate = float(np.average(t[bt], weights=WX[bt])) if bt.any() else float(np.nanmean(t))
                out["binary"][task] = ("const", rate)
                continue
            bm = _lgb(LGB_BINARY, Xe[bt], t[bt], repo_weights(REPO[bt]) * WX[bt], Xe[bv], t[bv], WX[bv], seed)
            pl = fit_platt(bm.predict(Xe[bv], raw_score=True), t[bv], WX[bv])
            rate = float(np.average(t[bt], weights=WX[bt]))
            out["binary"][task] = (bm, pl, 1.0, rate)
        bundle["seeds"][seed] = out
        const = [k for k, v in out["binary"].items() if v[0] == "const"]
        print(f"  {label} seed {seed}: outcome rounds lgb {lg.best_iteration}, xgb {xg.best_iteration + 1}; "
              f"blend weights {np.round(softmax(np.r_[0.0, out['outcome']['blend'][:2]]), 2).tolist()}; "
              f"reason model share {rlam:.2f}; labels at base rate: {const or 'none'} ({time.time() - t0:.0f}s)",
              flush=True)
    return bundle


def predict_bundle(bundle, X, seeds=None):
    """P (n, 3) outcome, R (n, 5) reasons, B {task: (n,)}; averaged over the given seeds (default: all)."""
    seeds = list(bundle["seeds"]) if seeds is None else seeds
    P, R, B = 0, 0, {t: 0 for t in BINARY_TASKS}
    for s in seeds:
        o = bundle["seeds"][s]
        Ps = [apply_vector(member_scores(n, o["outcome"]["models"][n], X), o["outcome"]["cal"][n]) for n in MEMBERS]
        P = P + apply_blend(Ps, o["outcome"]["blend"])
        rm, rt, rlam, rbase = o["reason"]
        R = R + rlam * apply_vector(rm.predict(X, raw_score=True), rt) + (1 - rlam) * np.asarray(rbase)
        for task in BINARY_TASKS:
            b = o["binary"][task]
            B[task] = B[task] + (np.full(len(X), b[1]) if b[0] == "const" else
                                 b[2] * expit(b[1][0] * b[0].predict(X, raw_score=True) + b[1][1]) + (1 - b[2]) * b[3])
    k = len(seeds)
    return P / k, R / k, {t: v / k for t, v in B.items()}


def prediction_columns(P, R, B):
    P = np.clip(P, 1e-6, 1)
    P = P / P.sum(1, keepdims=True)
    R = np.clip(R, 1e-6, 1)
    R = R / R.sum(1, keepdims=True)
    o = {"p_clean": P[:, 0], "p_rework": P[:, 1], "p_nowhere": P[:, 2]}
    for i, r in enumerate(REASONS):
        o[f"p_reason_{r}"] = R[:, i]
    for i, r in enumerate(REASONS):
        o[f"p_close_reason_{CLOSE_NAME[r]}"] = R[:, i]
    o["p_label_merged"] = P[:, 0] + P[:, 1]
    o["p_label_rework"] = P[:, 1] / (P[:, 0] + P[:, 1])
    for i, r in enumerate(REASONS[:4]):
        o[f"p_label_{r}"] = R[:, i]
    for task in ("bad", "revert", "szz_bug", "hotfix", "bad_miner"):
        o[f"p_label_{task}"] = np.broadcast_to(B[task], (len(P),)).astype(float)
    return pd.DataFrame(o)


# sample_output.csv's 56 columns, in its order; extra columns follow them
OUTPUT_COLUMNS = (
    "repo", "pr_number", "outcome_3class", "nowhere_reason", "close_reason", "close_reason_source", "created_at",
    "closed_at", "observed_through", "ai_agent_authored", "domain", "LA", "LD", "repo_outside_closed_count",
    "repo_median_first_response_hours", "author_prs_merged_repo", "title_len", "label_merged", "label_rework",
    "label_abandoned", "label_duplicate", "label_unwanted", "label_ci_technical", "label_bad", "label_bad_observed",
    "label_revert", "revert_window_closed", "label_hotfix", "hotfix_window_closed", "label_szz_bug",
    "szz_window_closed", "label_ci_fail", "ci_observed", "p_clean", "p_rework", "p_nowhere", "p_reason_abandoned",
    "p_reason_duplicate", "p_reason_unwanted", "p_reason_ci_technical", "p_reason_unknown",
    "p_close_reason_duplicate", "p_close_reason_unwanted", "p_close_reason_ci_technical",
    "p_close_reason_abandoned", "p_close_reason_other", "p_label_merged", "p_label_rework", "p_label_abandoned",
    "p_label_duplicate", "p_label_unwanted", "p_label_ci_technical", "p_label_bad", "p_label_revert",
    "p_label_szz_bug", "latency_ms")
EXTRA_COLUMNS = ("p_label_hotfix", "p_label_bad_miner", "label_bad_miner", "label_bad_miner_observed")


def assemble(rows, preds, latency):
    """The output table: sample_output's columns, then the extras. Label columns are the truth when the input has
    them (blank otherwise). label_bad / label_bad_observed use the team's definition (revert OR hotfix);
    the miner's own label_bad is carried as label_bad_miner next to p_label_bad_miner."""
    out = pd.DataFrame(index=range(len(rows)))
    rows = rows.reset_index(drop=True)
    for c in OUTPUT_COLUMNS + EXTRA_COLUMNS:
        if c in preds:
            out[c] = preds[c].to_numpy()
        elif c == "latency_ms":
            out[c] = np.round(latency, 4)
        elif c == "label_bad":
            out[c] = rows["label_bad_team"].to_numpy()
        elif c == "label_bad_observed":
            out[c] = rows["label_bad_team_observed"].to_numpy()
        elif c == "label_bad_miner":
            out[c] = rows["label_bad"].to_numpy() if "label_bad" in rows else np.nan
        elif c == "label_bad_miner_observed":
            out[c] = rows["label_bad_observed"].to_numpy() if "label_bad_observed" in rows else np.nan
        else:
            out[c] = rows[c].to_numpy() if c in rows else np.nan
    return out


def heuristic(bundle, rows):
    first = rows["ah_n"].fillna(0).to_numpy() == 0
    H = np.where(first[:, None], np.array(bundle["heuristic"]["first_time"]), np.array(bundle["heuristic"]["returning"]))
    return {f"heuristic_p_{c}": H[:, i] for i, c in enumerate(("clean", "rework", "nowhere"))}


def scored_table(bundle, X, idx, seeds, batch=256):
    """Eval rows for idx, once per seed, with latency per PR (model time per batch / batch size)."""
    rows = d.loc[idx].reset_index(drop=True)
    Xi = X.loc[idx]
    parts = []
    for s in seeds:
        lat, preds = np.zeros(len(rows)), []
        for i in range(0, len(rows), batch):
            t1 = time.perf_counter()
            preds.append(prediction_columns(*predict_bundle(bundle, Xi.iloc[i:i + batch], [s])))
            lat[i:i + batch] = (time.perf_counter() - t1) * 1000 / len(preds[-1])
        t = assemble(rows, pd.concat(preds, ignore_index=True), lat)
        t["seed"] = s
        for k, v in heuristic(bundle, rows).items():
            t[k] = v
        t["open_snapshot"] = rows["open_snapshot"].to_numpy() if "open_snapshot" in rows else "exact"
        parts.append(t)
    return pd.concat(parts, ignore_index=True)


print("model code ready")

# %% [markdown]
# ## 6. Evaluation
#
# Each split writes `eval/<split>/predictions.csv` (one row per test PR per seed) and runs the team's
# `eval_script.py` on it. The CSV keeps PRs whose diff was *recovered*: the eval script cannot reweight, so its
# numbers are the conservative ones. Section 6.3 also gives the fair view (exact diffs, reweighted to each repo's
# class mix) and plain-language numbers: accuracy, and how often the model is right when it is confident.

# %%
def run_eval(table, name, train_idx):
    out = os.path.join(EVAL, name)
    os.makedirs(out, exist_ok=True)
    csv = os.path.join(out, "predictions.csv")
    table.drop(columns=["open_snapshot"]).to_csv(csv, index=False)
    d.loc[train_idx, ["repo", "pr_number", "outcome_3class", "head_sha", "created_at", "closed_at"]].to_csv(
        os.path.join(out, "train.csv"), index=False)
    with open(os.path.join(out, "features_used.txt"), "w") as fh:
        fh.write("\n".join(FEATURES) + "\n")
    if not os.path.exists(EVAL_SCRIPT):
        print(f"eval script not found at {EVAL_SCRIPT}; skipped")
        return None
    r = subprocess.run([sys.executable, EVAL_SCRIPT, "--csv", csv, "--out", out, "--model", f"setup_G_{name}",
                        "--train", os.path.join(out, "train.csv"),
                        "--features-used", os.path.join(out, "features_used.txt")], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise RuntimeError("eval_script.py failed")
    s = json.load(open(os.path.join(out, "summary.json")))
    rpt = open(os.path.join(out, "report.txt")).read()
    i = rpt.find("12. CHECKLIST")
    print(f"eval_script.py on {s['prs']:,} PRs: skill {s['metrics']['skill']:.4f} "
          f"(95% range down to {s['metrics']['skill_lo']:.4f}), calibration error "
          f"{s['metrics']['calibration_error']:.4f}")
    print(rpt[i:rpt.find("FILES", i)].rstrip() if i >= 0 else "")
    return s


X_ALL = None    # each split encodes with its own training rows' category levels

# %% [markdown]
# ### 6.1 By PR (repos seen in training)

# %%
te = scored & (U_PR < 0.2)
rest = train_ok & (U_PR >= 0.2)
va, tr = rest & (U_VAL < 0.15), rest & (U_VAL >= 0.15)
print(f"train {tr.sum():,}, validation {va.sum():,}, test {te.sum():,} PRs")
b_pr = fit_bundle(None, tr, va, SEEDS, "by PR")
X_pr = b_pr["encoder"].transform(d)
T_pr = scored_table(b_pr, X_pr, np.flatnonzero(te), SEEDS)
S_pr = run_eval(T_pr, "by_pr", np.flatnonzero(tr | va))

# %% [markdown]
# ### 6.2 By repo (repos never seen)

# %%
parts = []
for k in range(FOLDS):
    te_k = scored & (FOLD == k)
    rest = train_ok & (FOLD != k)
    reps = sorted(set(REPO[rest]), key=lambda r: h(r, f"val{k}"))
    vrep = reps[:max(2, round(0.15 * len(reps)))]
    va_k = rest & np.isin(REPO, vrep)
    tr_k = rest & ~va_k
    print(f"fold {k}: test repos {sorted(set(REPO[te_k]))}")
    b_k = fit_bundle(None, tr_k, va_k, SEEDS, f"fold {k}", reason_lam=REASON_SHRINK_NEW_REPOS)
    parts.append(scored_table(b_k, b_k["encoder"].transform(d), np.flatnonzero(te_k), SEEDS))
T_repo = pd.concat(parts, ignore_index=True)
S_repo = run_eval(T_repo, "by_repo", np.flatnonzero(train_ok))

# %% [markdown]
# ### 6.3 The fair view and plain-language numbers

# %%
def plain(table, base_rows):
    """Skill on exact diffs reweighted to each repo's class mix, and accuracy-style numbers, averaged over seeds."""
    rows = []
    for s, t in table.groupby("seed"):
        y = t["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
        P = t[["p_clean", "p_rework", "p_nowhere"]].to_numpy()
        repo = t["repo"].to_numpy()
        for view in ("all", "exact"):
            if view == "all":
                w = np.ones(len(t))
            else:
                ex = (t["open_snapshot"] == "exact").to_numpy()
                c = pd.DataFrame({"r": repo, "y": y, "x": ex}).groupby(["r", "y"])["x"].agg(["size", "sum"])
                w = np.where(ex, (c["size"] / c["sum"].clip(lower=1)).reindex(
                    pd.MultiIndex.from_arrays([repo, y])).to_numpy(), 0.0)
            base = np.bincount(Y[base_rows], minlength=3) / base_rows.sum()
            Yk = np.eye(3)[y]
            g = pd.DataFrame({"r": repo, "b": ((P - Yk) ** 2).sum(1) * w, "rb": ((base - Yk) ** 2).sum(1) * w,
                              "w": w}).groupby("r").sum()
            g = g[g["w"] > 0]
            conf = (P.max(1) >= 0.7) & (w > 0)
            rows.append({"seed": s, "view": view,
                         "skill": 1 - (g["b"] / g["w"]).mean() / (g["rb"] / g["w"]).mean(),
                         "accuracy": np.average(P.argmax(1) == y, weights=w),
                         "always_most_common": np.average(y == np.bincount(y, weights=w).argmax(), weights=w),
                         "auroc_clean": roc_auc_score(y == 0, P[:, 0], sample_weight=w),
                         "auroc_rework": roc_auc_score(y == 1, P[:, 1], sample_weight=w),
                         "auroc_nowhere": roc_auc_score(y == 2, P[:, 2], sample_weight=w),
                         "share_confident": np.average(conf[w > 0], weights=w[w > 0]),
                         "accuracy_when_confident": np.average(P.argmax(1)[conf] == y[conf], weights=w[conf]),
                         "latency_p95_ms": float(np.percentile(t["latency_ms"], 95))})
    return pd.DataFrame(rows).groupby("view").mean(numeric_only=True).drop(columns="seed")


fair = pd.concat({"by PR": plain(T_pr, tr | va), "by repo": plain(T_repo, train_ok)})
fair.round(3)

# %% [markdown]
# ## 7. Final models
#
# Both are trained on every training row. `seen_repos` is early-stopped and calibrated on 15% of PRs; `new_repos` on
# whole held-out repos (15%, at least two), so its calibration reflects repos the model has not seen. Saved without
# pickles: LightGBM as text, XGBoost as UBJSON, logistic regression and every calibration as JSON. A round trip
# checks that the saved models reproduce the trained ones.

# %%
def save_bundle(b, path, info):
    os.makedirs(path, exist_ok=True)
    for f in os.listdir(path):
        os.remove(os.path.join(path, f))
    meta = {"features": FEATURES, "categorical_levels": b["encoder"].levels, "heuristic": b["heuristic"],
            "reasons": REASONS, "binary_tasks": BINARY_TASKS, "members": MEMBERS, "seeds": {}, **info}
    for s, o in b["seeds"].items():
        m = o["outcome"]["models"]
        m["lgb"].save_model(os.path.join(path, f"outcome_lgb_s{s}.txt"))
        m["xgb"][0].save_model(os.path.join(path, f"outcome_xgb_s{s}.ubj"))
        dn, lr = m["logreg"]
        json.dump({"dense": dn.to_dict(), "coef": lr.coef_.tolist(), "intercept": lr.intercept_.tolist()},
                  open(os.path.join(path, f"outcome_logreg_s{s}.json"), "w"))
        o["reason"][0].save_model(os.path.join(path, f"reason_lgb_s{s}.txt"))
        bins, const = {}, {}
        for task, bt in o["binary"].items():
            if bt[0] == "const":
                const[task] = bt[1]
            else:
                bt[0].save_model(os.path.join(path, f"label_{task}_lgb_s{s}.txt"))
                bins[task] = list(map(float, bt[1])) + [float(bt[2]), float(bt[3])]
        meta["seeds"][str(s)] = {"xgb_rounds": m["xgb"][1],
                                 "calibration": {n: list(map(float, o["outcome"]["cal"][n])) for n in MEMBERS},
                                 "blend": list(map(float, o["outcome"]["blend"])),
                                 "reason_calibration": list(map(float, o["reason"][1])),
                                 "reason_shrink": float(o["reason"][2]), "reason_base_rate": list(map(float, o["reason"][3])),
                                 "label_platt": bins,
                                 "label_base_rate": const}
    json.dump(meta, open(os.path.join(path, "meta.json"), "w"), indent=1)


def load_bundle(path):
    meta = json.load(open(os.path.join(path, "meta.json")))
    b = {"encoder": Encoder(meta["categorical_levels"]), "heuristic": meta["heuristic"], "seeds": {}, "meta": meta}
    for s, m in meta["seeds"].items():
        s = int(s)
        bx = xgb.Booster()
        bx.load_model(os.path.join(path, f"outcome_xgb_s{s}.ubj"))
        lj = json.load(open(os.path.join(path, f"outcome_logreg_s{s}.json")))
        lr = LogisticRegression()
        lr.coef_, lr.intercept_, lr.classes_ = np.array(lj["coef"]), np.array(lj["intercept"]), np.arange(3)
        models = {"lgb": lgb.Booster(model_file=os.path.join(path, f"outcome_lgb_s{s}.txt")),
                  "xgb": (bx, m["xgb_rounds"]), "logreg": (Dense.from_dict(lj["dense"]), lr)}
        b["seeds"][s] = {
            "outcome": {"models": models, "cal": {n: np.array(m["calibration"][n]) for n in MEMBERS},
                        "blend": np.array(m["blend"])},
            "reason": (lgb.Booster(model_file=os.path.join(path, f"reason_lgb_s{s}.txt")),
                       np.array(m["reason_calibration"]), m["reason_shrink"], np.array(m["reason_base_rate"])),
            "binary": {t: ((lgb.Booster(model_file=os.path.join(path, f"label_{t}_lgb_s{s}.txt")),
                            np.array(m["label_platt"][t][:2]), m["label_platt"][t][2], m["label_platt"][t][3])
                           if t in m["label_platt"] else
                           ("const", m["label_base_rate"][t]))
                       for t in BINARY_TASKS}}
    return b


finals = {}
for name in ("seen_repos", "new_repos"):
    if name == "seen_repos":
        va_f = train_ok & (U_VAL < 0.15)
    else:
        reps = sorted(set(REPO[train_ok]), key=lambda r: h(r, "final_val"))
        va_f = train_ok & np.isin(REPO, reps[:max(2, round(0.15 * len(reps)))])
    tr_f = train_ok & ~va_f
    b = fit_bundle(None, tr_f, va_f, SEEDS, name,
                   reason_lam=1.0 if name == "seen_repos" else REASON_SHRINK_NEW_REPOS)
    info = {"name": name, "trained_on": os.path.basename(DATA), "train_prs": int(tr_f.sum()),
            "validation_prs": int(va_f.sum()), "validation": "PR hash" if name == "seen_repos" else
            f"held-out repos: {sorted(set(REPO[va_f]))}", "built": time.strftime("%Y-%m-%d %H:%M"),
            "fair_skill": {k: float(fair.loc[(k, "exact"), "skill"]) for k in ("by PR", "by repo")}}
    path = os.path.join(MODELS, name)
    save_bundle(b, path, info)
    loaded = load_bundle(path)
    sample = np.flatnonzero(scored)[:3000]
    Xs = b["encoder"].transform(d.iloc[sample])
    gap = max(float(np.abs(np.asarray(a_) - np.asarray(b_)).max())
              for a_, b_ in zip(prediction_columns(*predict_bundle(b, Xs)).to_numpy().T,
                                prediction_columns(*predict_bundle(loaded, Xs)).to_numpy().T))
    size = sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path)) / 2 ** 20
    print(f"{name}: saved to {path} ({size:.1f} MB); round trip largest difference {gap:.1e}")
    assert gap < 1e-6, "the saved model does not reproduce the trained one"
    finals[name] = loaded

# %% [markdown]
# ## 8. Scoring freshly mined data
#
# `score(table, model_dir)` is the whole production path: the miner's dataset table in, predictions out in
# `sample_output.csv`'s schema (plus the extras). The table must contain every PR of each repo (closed ones too):
# the history features are built from the PRs that closed before each one opened. Latency per PR = the feature
# step's time divided by the number of PRs, plus the model's time per batch of 256 divided by the batch size.
#
# The demonstration below scores this same table, so its predictions are **in-sample** (not an evaluation; section 6
# is the evaluation). It checks the output schema and measures latency.

# %%
def score(table: pd.DataFrame, model_dir: str, rows: str = "outside", batch: int = 256) -> pd.DataFrame:
    """Freshly mined dataset table -> one prediction row per PR (outside contributors by default; rows="all")."""
    b = load_bundle(model_dir)
    t0 = time.perf_counter()
    dd = prepare(table)
    feature_ms = (time.perf_counter() - t0) * 1000 / max(len(dd), 1)
    if rows == "outside":
        dd = dd[dd["outside"]]
    dd = dd.reset_index(drop=True)
    Xd = b["encoder"].transform(dd)
    lat, preds = np.zeros(len(dd)), []
    for i in range(0, len(dd), batch):
        t1 = time.perf_counter()
        preds.append(prediction_columns(*predict_bundle(b, Xd.iloc[i:i + batch])))
        lat[i:i + batch] = (time.perf_counter() - t1) * 1000 / len(preds[-1]) + feature_ms
    return assemble(dd, pd.concat(preds, ignore_index=True), lat)


demo = score(raw, os.path.join(MODELS, "seen_repos"))
demo_path = os.path.join(PRED, "in_sample_seen_repos.csv")
demo.to_csv(demo_path, index=False)
sample_cols = list(OUTPUT_COLUMNS)
sample_csv = os.environ.get("RIFFLE_SAMPLE_OUTPUT", os.path.join("tools", "sample_output.csv"))
if os.path.exists(sample_csv):
    sample_cols = list(pd.read_csv(sample_csv, nrows=0).columns)
missing_cols = [c for c in sample_cols if c not in demo.columns]
assert not missing_cols, f"missing sample_output columns: {missing_cols}"
assert list(demo.columns[:len(sample_cols)]) == sample_cols, "sample_output's columns must come first, in order"
pc = demo[["p_clean", "p_rework", "p_nowhere"]].sum(1)
print(f"wrote {demo_path}: {len(demo):,} PRs, {demo.shape[1]} columns (sample_output's {len(sample_cols)} first, "
      f"then {', '.join(EXTRA_COLUMNS)}); outcome probabilities add up to 1 (max error {np.abs(pc - 1).max():.1e}); "
      f"latency median {demo['latency_ms'].median():.3f} ms, p95 {np.percentile(demo['latency_ms'], 95):.3f} ms")
demo.head(3).T

# %% [markdown]
# ## 9. Summary

# %%
rows = []
for split, S in (("by PR", S_pr), ("by repo", S_repo)):
    f = fair.loc[(split, "exact")]
    rows.append({"split": split,
                 "eval_script skill": S["metrics"]["skill"] if S else np.nan,
                 "eval_script calibration error": S["metrics"]["calibration_error"] if S else np.nan,
                 "fair-view skill": f["skill"], "accuracy": f["accuracy"],
                 "always most common": f["always_most_common"], "share confident (>= 70%)": f["share_confident"],
                 "accuracy when confident": f["accuracy_when_confident"],
                 "AUROC clean / rework / nowhere": f"{f['auroc_clean']:.3f} / {f['auroc_rework']:.3f} / "
                                                   f"{f['auroc_nowhere']:.3f}",
                 "latency p95 ms (model)": f["latency_p95_ms"]})
pd.DataFrame(rows).set_index("split").T

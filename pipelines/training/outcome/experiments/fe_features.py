"""
fe_features.py - our own feature engineering for the at-open outcome model (feature experiments, pass 1 onwards).

numpy and pandas only, so the pipeline notebook can copy it unchanged. Every feature is point-in-time: a past PR's
outcome counts only if that PR closed strictly before this PR opened. test_fe_features.py checks this with a cut test.

    d = prepare(raw_dataset)            # known outcomes, cleaned columns, all engineered features
    groups = feature_groups(d)          # name -> list of columns, for the experiment configs
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")
DAY = 86400
MATURITY_DAYS = 90

# Columns that know the future (see DECISIONS.md, "Leaks found").
LEAKS = ("author_association",                                  # today's status, not at open
         "body_len", "body_headings", "body_checklist_items", "body_has_test_plan", "has_linked_issue",
         "agent_boilerplate",                                   # body read at mining time
         "max_sim_open_prs", "max_sim_recent_merged",           # title + body-at-mining-time similarity
         "ai_agent_authored", "agent_name",                     # partly from body boilerplate
         "author_prs_merged_repo")                              # blank only on merged PRs; replaced by ah_*
LLM = ("llm_touches_authn_authz", "authz_check_change", "changes_public_api_signature", "error_handling_change",
       "exception_swallowed", "db_schema_or_query_change", "transaction_boundary_change", "input_validation_change",
       "config_or_feature_flag_change", "feature_flag_guarding", "security_sensitive_ops", "resource_lifecycle_change",
       "null_handling_change", "numeric_money_precision", "time_handling_change", "retry_timeout_idempotency_change",
       "refactor_vs_behavior", "change_intent", "unrelated_change_share", "tests_added_for_behavior_change",
       "test_code_alignment", "satd_added", "semantic_risk_1to5")
LLM_KEEP = ("semantic_risk_1to5", "change_intent", "refactor_vs_behavior", "tests_added_for_behavior_change",
            "unrelated_change_share")
QUIET = {"none", "n_a", "no", "pure_refactor", "aligned", "False", "false"}


def secs(s: pd.Series) -> np.ndarray:
    """Seconds since 1970, whatever unit the timestamps are stored in (parquet gives microseconds, not ns)."""
    t = pd.to_datetime(s, utc=True, errors="coerce")
    return ((t - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy(np.int64)


# ---------------------------------------------------------------- point-in-time counting
OFF = np.int64(1) << 36          # key * OFF + seconds; seconds since 1970 stay far below 2**36


def _own_keys(key):
    """Unknown keys (< 0) each get a key of their own, so their events never count for anyone else."""
    key = np.asarray(key, np.int64)
    k = key.copy()
    bad = key < 0
    k[bad] = key.max() + 1 + np.arange(int(bad.sum()))
    return key, k


def prior_sum(key, t_query, t_event, W, window=None):
    """For each row i: the sum of W[j] over rows j with key[j] == key[i] and t_event[j] < t_query[i]
    (and t_event[j] >= t_query[i] - window when given). key < 0 means unknown: the result is NaN."""
    key, k = _own_keys(key)
    W = np.asarray(W, float).reshape(len(key), -1)
    ev = k * OFF + t_event
    order = np.argsort(ev, kind="stable")
    evs = ev[order]
    cum = np.vstack([np.zeros((1, W.shape[1])), np.cumsum(W[order], axis=0)])
    q = k * OFF + t_query
    out = cum[np.searchsorted(evs, q, side="left")]
    lo = k * OFF if window is None else np.maximum(q - window, k * OFF)
    out = out - cum[np.searchsorted(evs, lo, side="left")]
    out[key < 0] = np.nan
    return out


def prev_event_gap(key, t_query, t_event):
    """Seconds from the latest event of the same key strictly before t_query (NaN when none)."""
    key, k = _own_keys(key)
    ev = k * OFF + t_event
    evs = np.sort(ev)
    pos = np.searchsorted(evs, k * OFF + t_query, side="left") - 1
    prev = evs[np.clip(pos, 0, None)]
    ok = (pos >= 0) & (prev // OFF == k) & (key >= 0)
    return np.where(ok, t_query - (prev - k * OFF), np.nan).astype(float)


def first_event_gap(key, t_query, t_event):
    """Seconds since the key's first event, if that event is strictly before t_query (NaN otherwise)."""
    key, k = _own_keys(key)
    s = pd.Series(t_event).groupby(k).transform("min").to_numpy()
    return np.where((s < t_query) & (np.asarray(key) >= 0), t_query - s, np.nan).astype(float)


def prior_last_k(key, t_query, t_event, W, k):
    """Sum of W over the key's last k events strictly before t_query, and how many events that was (<= k)."""
    key, kk = _own_keys(key)
    W = np.asarray(W, float).reshape(len(key), -1)
    ev = kk * OFF + t_event
    order = np.argsort(ev, kind="stable")
    evs = ev[order]
    cum = np.vstack([np.zeros((1, W.shape[1])), np.cumsum(W[order], axis=0)])
    hi = np.searchsorted(evs, kk * OFF + t_query, side="left")
    lo = np.maximum(hi - k, np.searchsorted(evs, kk * OFF, side="left"))
    out, n = cum[hi] - cum[lo], (hi - lo).astype(float)
    out[key < 0], n[key < 0] = np.nan, np.nan
    return out, n


def _rates(counts, n, prior, k):
    """Class rates shrunk towards `prior` with strength k: (count + k * prior) / (n + k)."""
    return (counts + k * prior) / (n[:, None] + k)


# ---------------------------------------------------------------- the table
def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Rows with a known outcome, cleaned, with every engineered feature. History is computed over every known PR
    (including bots, maintainers and `final` rows: their outcome is real even when their diff is not)."""
    d = raw[raw["outcome_3class"].isin(CLASSES)].reset_index(drop=True).copy()
    d["y"] = d["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).astype(int)
    d["t_open"] = secs(d["created_at"])
    d["t_close"] = np.maximum(secs(d["closed_at"]), d["t_open"])   # a close stamped before the open is not earlier
    cut = d.groupby("repo")["observed_through"].transform(lambda s: s.dropna().max())
    d["t_obs"] = secs(d["observed_through"].fillna(cut))
    d["mature"] = (d["t_obs"] - d["t_open"]) >= MATURITY_DAYS * DAY
    bot = d["author_is_bot"].fillna(False).astype(bool)
    maint = d["author_confirmed_maintainer"].fillna(False).astype(bool)
    d["outside"] = ~bot & ~maint
    email = d["author_email"].where(d["author_email"].notna(), None)
    d["author_key"] = email.map(lambda e: e.strip().lower() if isinstance(e, str) and e.strip() else None)

    # B. repairs: a blank head_from_fork is a fork deleted later; at webhook time the fork exists
    d["head_from_fork"] = d["head_from_fork"].map({True: 1.0, False: 0.0}).fillna(1.0)

    _consolidated(d)
    _history(d)
    _context(d)
    return d


def _consolidated(d):
    """C. fewer, clearer columns for the redundant blocks (experience, size, LLM)."""
    exp = pd.to_numeric(d["EXP"], errors="coerce")
    d["c_exp_zero"] = (exp == 0).astype(float).where(exp.notna())
    d["c_exp_specialisation"] = pd.to_numeric(d["SEXP"], errors="coerce") / (exp + 1)
    la, ld = pd.to_numeric(d["LA"], errors="coerce"), pd.to_numeric(d["LD"], errors="coerce")
    d["c_churn"] = la + ld
    d["c_delete_share"] = ld / (la + ld).where((la + ld) > 0)
    d["c_files_per_dir"] = pd.to_numeric(d["NF"], errors="coerce") / pd.to_numeric(d["ND"], errors="coerce").clip(lower=1)
    ran = d["semantic_risk_1to5"].notna()
    flags = np.zeros(len(d))
    for c in LLM:
        if c in LLM_KEEP or c not in d.columns:
            continue
        s = d[c]
        flags += s.notna().to_numpy() & ~s.astype(str).isin(QUIET).to_numpy()
    d["c_llm_ran"] = ran.astype(float)
    d["c_llm_flags"] = np.where(ran, flags, np.nan)


def _history(d):
    """D. point-in-time history from PRs closed before this one opened."""
    t, tc, y = d["t_open"].to_numpy(), d["t_close"].to_numpy(), d["y"].to_numpy()
    # a PR with no closed_at (1,339 goes-nowhere PRs) never adds an outcome or an open/closed count to anyone's
    # history: when its outcome became known is unknown. (Before this fix it counted as closed at open: a leak.)
    hist_ok = pd.to_datetime(d["closed_at"], utc=True, errors="coerce").notna().to_numpy()
    Y = np.eye(3)[y] * hist_ok[:, None]
    ones = np.ones(len(d))
    ones_h = hist_ok.astype(float)
    repo = pd.factorize(d["repo"])[0]
    ar = pd.factorize(d["repo"] + "\x00" + d["author_key"].fillna("\x01"))[0]
    ar[d["author_key"].isna().to_numpy()] = -1
    au = pd.factorize(d["author_key"])[0]                       # -1 where unknown

    # repo, last 365 days: the prior that author rates shrink towards
    r365 = prior_sum(repo, t, tc, Y, 365 * DAY)
    prior = (r365 + 1.0) / (r365.sum(1, keepdims=True) + 3.0)

    # D1. author in this repo
    a = prior_sum(ar, t, tc, Y)
    an = a.sum(1)
    opened = prior_sum(ar, t, t, ones)[:, 0]
    d["ah_n"] = an
    d["ah_merged"] = a[:, 0] + a[:, 1]
    d["ah_nowhere"] = a[:, 2]
    rate = _rates(a, an, prior, 3.0)
    d["ah_clean_rate"], d["ah_rework_rate"], d["ah_nowhere_rate"] = rate.T
    d["ah_open_now"] = prior_sum(ar, t, t, ones_h)[:, 0] - prior_sum(ar, t, tc, ones_h)[:, 0]
    d["ah_first_pr"] = ((an == 0) & (opened == 0)).astype(float)
    d["ah_days_since_prev_pr"] = prev_event_gap(ar, t, t) / DAY
    d["ah_tenure_days"] = first_event_gap(ar, t, t) / DAY
    d.loc[d["author_key"].isna(), [c for c in d.columns if c.startswith("ah_")]] = np.nan

    # D2. the repo's recent behaviour
    r90 = prior_sum(repo, t, tc, Y, 90 * DAY)
    rn = r90.sum(1)
    d["rh90_n"] = rn
    rr = np.where(rn[:, None] >= 5, r90 / np.maximum(rn, 1)[:, None], np.nan)
    d["rh90_clean_rate"], d["rh90_rework_rate"], d["rh90_nowhere_rate"] = rr.T
    d["rh_open_now"] = prior_sum(repo, t, t, ones_h)[:, 0] - prior_sum(repo, t, tc, ones_h)[:, 0]
    d["rh30_opened"] = prior_sum(repo, t, t, ones, 30 * DAY)[:, 0]
    ttc = np.log1p((tc - t) / 3600.0)
    s = prior_sum(repo, t, tc, np.c_[ttc * hist_ok, ones_h], 90 * DAY)
    d["rh90_mean_log_hours_to_close"] = np.where(s[:, 1] >= 5, s[:, 0] / np.maximum(s[:, 1], 1), np.nan)
    # how the repo treated outside first-timers and outside authors, last 180 days (shrunk to the repo's mix)
    base = np.where(np.isnan(rr), prior, rr)
    for name, mask in (("ft", d["outside"].to_numpy() & (an == 0)), ("out", d["outside"].to_numpy())):
        c = prior_sum(repo, t, tc, Y * mask[:, None], 180 * DAY)
        n = c.sum(1)
        r = _rates(c, n, base, 5.0)
        d[f"rh180_{name}_n"] = n
        d[f"rh180_{name}_clean_rate"], d[f"rh180_{name}_nowhere_rate"] = r[:, 0], r[:, 2]

    # D4 (pass 2). the author's recent behaviour in this repo: last outcome, last 3 and 5, last 90 and 365 days
    last, n1 = prior_last_k(ar, t, tc, Y, 1)
    d["ar_last_outcome"] = np.where(n1 == 1, last.argmax(1), np.nan)
    for k in (3, 5):
        c, n = prior_last_k(ar, t, tc, Y, k)
        r = _rates(c, n, prior, 2.0)
        d[f"ar_last{k}_nowhere_rate"], d[f"ar_last{k}_clean_rate"] = r[:, 2], r[:, 0]
    for days in (90, 365):
        c = prior_sum(ar, t, tc, Y, days * DAY)
        n = c.sum(1)
        r = _rates(c, n, prior, 3.0)
        d[f"ar{days}_n"] = n
        d[f"ar{days}_nowhere_rate"], d[f"ar{days}_clean_rate"] = r[:, 2], r[:, 0]
    d.loc[d["author_key"].isna(), [c for c in d.columns if c.startswith("ar")]] = np.nan

    # D5 (pass 2). outside first-timers over the last 90 days (the 180-day version is in D2)
    ft = d["outside"].to_numpy() & (an == 0)
    c = prior_sum(repo, t, tc, Y * ft[:, None], 90 * DAY)
    n = c.sum(1)
    r = _rates(c, n, base, 5.0)
    d["rf90_ft_n"] = n
    d["rf90_ft_clean_rate"], d["rf90_ft_nowhere_rate"] = r[:, 0], r[:, 2]

    # D6 (pass 2). size relative to the repo's PRs merged in the last 90 days (log churn minus their mean)
    lc = np.log1p(pd.to_numeric(d["LA"], errors="coerce").fillna(0).to_numpy()
                  + pd.to_numeric(d["LD"], errors="coerce").fillna(0).to_numpy())
    merged = (y < 2) * hist_ok.astype(float)                     # a stale-open row is never a known merge
    s = prior_sum(repo, t, tc, np.c_[lc * merged, lc ** 2 * merged, merged], 90 * DAY)
    mu = s[:, 0] / np.maximum(s[:, 2], 1)
    sd = np.sqrt(np.maximum(s[:, 1] / np.maximum(s[:, 2], 1) - mu ** 2, 1e-6))
    ok = s[:, 2] >= 10
    d["s_rel_size_z"] = np.where(ok, (lc - mu) / sd, np.nan)
    d["s_rel_size_vs_merged"] = np.where(ok, lc - mu, np.nan)

    # D3. the author across every repo in the data (needs an org-wide view; see DECISIONS.md)
    x = prior_sum(au, t, tc, Y)
    xn = x.sum(1)
    d["xh_n_other_repos"] = xn - an
    xr = _rates(x, xn, prior, 3.0)
    d["xh_merged_rate"] = xr[:, 0] + xr[:, 1]
    d["xh_nowhere_rate"] = xr[:, 2]
    d.loc[d["author_key"].isna(), [c for c in d.columns if c.startswith("xh_")]] = np.nan


def _context(d):
    """E. when the PR was opened (UTC; the author's timezone is unknown)."""
    ts = pd.to_datetime(d["t_open"], unit="s", utc=True)
    d["e_hour_utc"] = ts.dt.hour.astype(float)
    d["e_dow_utc"] = ts.dt.day_of_week.astype(float)


def feature_groups(d: pd.DataFrame, allowed: list[str]) -> dict[str, list[str]]:
    """The column groups the experiment configs combine. `allowed` = the contract's model inputs."""
    pre = lambda p: [c for c in d.columns if c.startswith(p)]
    return {
        "allowed": [c for c in allowed if c in d.columns],
        "leaks": [c for c in LEAKS if c in d.columns],
        "llm_detail": [c for c in LLM if c not in LLM_KEEP],
        "redundant": ["REXP", "la_norm", "ld_norm"],
        "consolidated": pre("c_"),
        "author_history": pre("ah_"),
        "repo_history": pre("rh"),
        "context": pre("e_"),
        "cross_repo": pre("xh_"),
        "author_recent": pre("ar"),
        "repo_ft90": pre("rf90_"),
        "rel_size": pre("s_rel"),
    }

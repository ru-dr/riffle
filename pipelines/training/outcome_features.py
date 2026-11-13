"""
outcome_features.py - point-in-time features for the at-open outcome model (ADR 0022).

    import outcome_features as OF
    d = OF.build_features(dataset)             # the miner's dataset table: one row per closed PR, every repo
    X = d[list(OF.FEATURES)]                   # 161 inputs: 131 miner columns + 30 engineered
    rows = OF.training_rows(d)                 # which rows to score and train on, and their weights

Every engineered feature is as at the PR's open. A past PR counts in a history only if it closed strictly before
this PR opened. A PR with no `closed_at` (a stale-open row: still open when mined, labelled abandoned at mining)
never lends its outcome to anyone. `tests/test_outcome_features.py` checks both.

History is computed over every PR in the table, so pass the whole table (all PRs of each repo), not a split.
numpy and pandas only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")
DAY = 86400
MATURITY_DAYS = 90          # an outcome counts as settled 90 days after the PR opened

# Columns a model must not read (ADR 0022). Each is present in the miner's table and allowed by the contract, but
# carries the outcome for a model that scores at open.
LEAKS = (
    "author_association",            # GitHub's status of the author at mining time, not at open; alone +0.12 skill
    "body_len", "body_headings", "body_checklist_items", "body_has_test_plan", "has_linked_issue",
    "agent_boilerplate",             # the body is read at mining time and includes edits made after open
    "max_sim_open_prs", "max_sim_recent_merged",     # similarity computed on that mining-time body
    "ai_agent_authored", "agent_name",               # partly detected from body watermarks
    "author_prs_merged_repo",        # blank only on merged PRs, so a blank says "merged"
)
# The 18 detail answers of the miner's LLM flags, compressed into c_llm_ran and c_llm_flags. Kept as inputs: the
# risk score, change intent, refactor vs behaviour, tests added and unrelated share (in MINER_FEATURES).
LLM_DETAIL = (
    "llm_touches_authn_authz", "authz_check_change", "changes_public_api_signature", "error_handling_change",
    "exception_swallowed", "db_schema_or_query_change", "transaction_boundary_change", "input_validation_change",
    "config_or_feature_flag_change", "feature_flag_guarding", "security_sensitive_ops", "resource_lifecycle_change",
    "null_handling_change", "numeric_money_precision", "time_handling_change", "retry_timeout_idempotency_change",
    "test_code_alignment", "satd_added",
)
QUIET = {"none", "n_a", "no", "pure_refactor", "aligned", "False", "false"}     # LLM answers that flag nothing

MINER_FEATURES = (
    'author_confirmed_maintainer', 'LA', 'LD', 'NF', 'ND', 'NS', 'Entropy', 'frac_generated_lines',
    'frac_test_lines', 'lang_mix', 'n_files_added', 'n_files_removed', 'n_files_renamed', 'patch_missing_files',
    'touches_migration', 'touches_lockfile', 'touches_dep_manifest', 'touches_ci_config', 'touches_infra',
    'touches_auth_path', 'touches_codeowners_or_riffle_yml', 'test_only', 'docs_only', 'LT', 'AGE',
    'max_file_revert_density', 'mean_file_revert_density', 'max_revert_decay_density',
    'min_days_since_last_revert', 'max_file_fix_density', 'mean_file_fix_density', 'max_fix_decay_density',
    'min_days_since_last_fix', 'max_file_churn_90d', 'max_file_churn_365d', 'max_recent_merge_burst_7d',
    'max_hotfix_count_365d', 'sum_hotfix_count_365d', 'max_test_cochange_ratio',
    'max_file_post_merge_ci_fail_rate', 'mean_file_age_days', 'max_NUC', 'max_NDEV', 'max_file_complexity_ccn',
    'sum_file_complexity_ccn', 'max_file_top_author_share', 'max_file_minor_contributor_count',
    'max_abandoned_lines_share', 'max_file_hotspot_score', 'min_file_hotspot_rank', 'min_mod_line_age_days_min',
    'median_mod_line_age_days_median', 'max_mod_lines_distinct_authors', 'mean_mod_lines_self_authored_share',
    'max_mod_lines_prior_fix_share', 'max_hunk_fix_proximity', 'mean_hunk_fix_proximity', 'cochange_degree',
    'cochange_partners_missing', 'expected_test_cochange_missing', 'author_revert_rate', 'author_file_familiarity',
    'author_days_since_last_change', 'author_post_merge_ci_fail_rate', 'repo_revert_base_rate', 'FIX',
    'author_is_bot', 'EXP', 'SEXP', 'pr_size_pctile_repo', 'nf_pctile_repo', 'entropy_pctile_repo',
    'base_behind_by', 'concurrent_open_prs_same_files', 'concurrent_open_prs_same_hunks',
    'n_topk_hotspots_touched', 'touches_topk_hotspot', 'min_hotspot_rank_touched', 'hardcoded_secret_added',
    'new_env_or_config_key', 'observability_delta', 'bound_or_comparison_edit', 'unused_import_or_dead_code_added',
    'dependency_major_bump_count', 'migration_reversibility', 'ccn_delta', 'max_ccn_changed_func',
    'n_funcs_modified', 'semgrep_findings_new', 'semgrep_high_conf_security_new', 'semgrep_cwe_top25_new',
    'n_deps_added', 'n_deps_upgraded', 'n_deps_downgraded', 'n_major_bumps', 'osv_vulns_introduced',
    'osv_vulns_fixed', 'ghsa_max_severity_introduced', 'ghsa_malware_dep', 'vuln_has_fix_available',
    'osv_ghsa_disagreement', 'api_contract_change', 'path_size_pctile', 'title_len', 'has_milestone',
    'targets_default_branch', 'head_from_fork', 'head_branch_agent_prefix', 'author_account_age_days',
    'has_contributing', 'has_agents_md', 'declared_hotfix', 'refactor_vs_behavior', 'change_intent',
    'unrelated_change_share', 'tests_added_for_behavior_change', 'semantic_risk_1to5', 'similar_pr_bad_rate',
    'similar_pr_count', 'revealed_path_scrutiny', 'path_failure_mode_revert', 'path_failure_mode_ci',
    'path_failure_mode_hotfix', 'path_detection_lag_days', 'release_cycle_position', 'repo_outside_merge_rate',
    'repo_outside_closed_count', 'agent_recent_merge_rate', 'agent_recent_closed_count',
    'repo_median_first_response_hours', 'repo_agent_pr_load_30d',
)
ENGINEERED = (
    'c_exp_zero', 'c_exp_specialisation', 'c_churn', 'c_delete_share', 'c_files_per_dir', 'c_llm_ran',
    'c_llm_flags', 'ah_n', 'ah_merged', 'ah_nowhere', 'ah_clean_rate', 'ah_rework_rate', 'ah_nowhere_rate',
    'ah_open_now', 'ah_first_pr', 'ah_days_since_prev_pr', 'ah_tenure_days', 'rh90_n', 'rh90_clean_rate',
    'rh90_rework_rate', 'rh90_nowhere_rate', 'rh_open_now', 'rh30_opened', 'rh90_mean_log_hours_to_close',
    'rh180_ft_n', 'rh180_ft_clean_rate', 'rh180_ft_nowhere_rate', 'rh180_out_n', 'rh180_out_clean_rate',
    'rh180_out_nowhere_rate',
)
FEATURES = MINER_FEATURES + ENGINEERED
CATEGORICAL = ("migration_reversibility", "ghsa_max_severity_introduced", "api_contract_change",
               "refactor_vs_behavior", "change_intent", "tests_added_for_behavior_change")

# ---------------------------------------------------------------- point-in-time counting
OFF = np.int64(1) << 36          # key * OFF + seconds: one sortable number per (key, time)
NEVER = np.int64(2) ** 34        # a close time far in the future (about the year 2514)


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
    (and t_event[j] >= t_query[i] - window when given). Unknown key: NaN. Sorts once, then binary search."""
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


def flag(s: pd.Series) -> np.ndarray:
    """True / False / unknown (NaN) from bools, 0/1 or strings. Unknown stays NaN: null is not a pass."""
    m = s.map(lambda v: 1.0 if v is True or v == 1 or str(v).lower() == "true" else
              0.0 if v is False or v == 0 or str(v).lower() == "false" else np.nan)
    return m.to_numpy(float)


# ---------------------------------------------------------------- the table
def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    """The miner's dataset table -> a copy with every engineered feature and the row facts `training_rows` uses.

    Adds: the 30 ENGINEERED columns; `y` (0, 1, 2 for CLASSES, -1 unknown); `t_open`, `t_close`, `t_obs` (seconds);
    `mature` (opened at least 90 days before the data was observed); `outside` (not a bot, not a confirmed
    maintainer); `author_key` (lower-cased commit email). Miner columns in MINER_FEATURES that the table lacks are
    added as NaN (unknown), and a blank `head_from_fork` becomes a fork (the fork was deleted later; when the
    webhook fires it exists). Nothing else is changed.
    """
    d = raw.reset_index(drop=True).copy()
    d["y"] = d["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).fillna(-1).astype(int)
    known = (d["y"] >= 0).to_numpy()
    d["t_open"] = secs(d["created_at"])
    tc = secs(d["closed_at"]) if "closed_at" in d else np.full(len(d), np.iinfo(np.int64).min)
    tc = np.where(tc < 0, NEVER, tc)                              # not closed (or unknown): far in the future
    d["t_close"] = np.maximum(tc, d["t_open"].to_numpy())         # a close stamped before the open is not earlier
    obs = pd.to_datetime(d.get("observed_through"), utc=True, errors="coerce") if "observed_through" in d else None
    cut = (obs.groupby(d["repo"]).transform("max") if obs is not None else pd.Series(pd.NaT, index=d.index))
    fallback = pd.to_datetime(d["closed_at"], utc=True, errors="coerce").groupby(d["repo"]).transform("max")
    d["observed_through"] = (obs if obs is not None else cut).fillna(cut).fillna(fallback)
    d["t_obs"] = secs(d["observed_through"])
    d["mature"] = (d["t_obs"] - d["t_open"]) >= MATURITY_DAYS * DAY
    flag0 = lambda c: (np.nan_to_num(flag(d[c])) == 1) if c in d else np.zeros(len(d), bool)
    d["outside"] = ~flag0("author_is_bot") & ~flag0("author_confirmed_maintainer")
    d["author_key"] = d["author_email"].map(lambda e: e.strip().lower() if isinstance(e, str) and e.strip() else None)
    for c in MINER_FEATURES:
        if c not in d:
            d[c] = np.nan
    d["head_from_fork"] = pd.Series(flag(d["head_from_fork"])).fillna(1.0).to_numpy()

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
    # a PR with no close time (a stale-open row) adds no outcome and no open/closed count to anyone's history: when
    # its outcome became known is unknown. It still counts as having opened.
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
    return d


@dataclass
class Rows:
    scored: np.ndarray      # rows to evaluate on: known outcome, mature, diff not `final`, outside contributor
    train: np.ndarray       # rows to train on: scored rows whose at-open diff is exact
    weight: np.ndarray      # per row: (scored rows of its repo and class) / (exact ones among them); 1 elsewhere


def training_rows(d: pd.DataFrame) -> Rows:
    """Which rows to score and train on (ADR 0022). About 42% of rework PRs have a recovered (not exact) at-open
    diff against almost no clean PRs, and a model can tell recovered from exact; in production the diff is always
    exact. So train on exact diffs only, reweighted so every repo keeps its full class mix. Multiply `weight` by
    `repo_weights` for the weights we trained with."""
    known = (d["y"] >= 0).to_numpy()
    not_final = (d["open_snapshot"] != "final").to_numpy() if "open_snapshot" in d else np.ones(len(d), bool)
    exact = (d["open_snapshot"] == "exact").to_numpy() if "open_snapshot" in d else np.ones(len(d), bool)
    scored = known & d["mature"].to_numpy() & not_final & d["outside"].to_numpy()
    cnt = pd.DataFrame({"r": d["repo"], "y": d["y"], "x": exact})[scored].groupby(["r", "y"])["x"].agg(["size", "sum"])
    w = np.nan_to_num((cnt["size"] / cnt["sum"].clip(lower=1)).reindex(
        pd.MultiIndex.from_arrays([d["repo"], d["y"]])).to_numpy(), nan=1.0)
    return Rows(scored=scored, train=scored & exact, weight=w)


def repo_weights(repo) -> np.ndarray:
    """1 / sqrt(PRs in the repo), scaled to mean 1, so the biggest repos do not dominate."""
    n = pd.Series(repo).map(pd.Series(repo).value_counts()).to_numpy(float)
    w = n ** -0.5
    return w * len(w) / w.sum()

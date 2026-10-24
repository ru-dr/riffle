"""
frames.py — the one place the miner's three tables become training frames.

The miner writes three tables on purpose (one row per PR, per snapshot, per head
commit) and they are joined here, at load time, not stored merged:

    dataset          every closed PR              -> breakage frame
    snapshots        a PR at a moment while open  -> merge-outcome frame
    commit_features  one row per head commit      -> optional join onto snapshots

    t = load_tables("dataset_merged.parquet")          # also finds _snapshots / _commit_features
    f = build_frames(t, by="pr", test_frac=0.2)
    f.breakage_train, f.outcome_train, f.outcome_train["snapshot_weight"], f.inputs(f.outcome_train)

Rules it enforces, so each consumer doesn't have to remember them:
  - The train/test split is by PR (or repo) and decided once from a stable hash of
    the lower-cased repo and PR number, so every table puts a PR on the same side and
    a PR's snapshots never straddle it. PRs that share a head commit (a reopen or a
    duplicate) are one unit, so they never straddle it either.
  - Breakage frame: merged PRs whose label_bad was fully observed (label_bad_observed).
  - Outcome frame: snapshots without near_decided, weighted by snapshot_weight.
  - Rows whose at-open diff was not recoverable (open_snapshot == "final") get their
    diff features nulled, because those may leak (ADR 0010).
  - Columns the contract marks as outcomes, plus ids, provenance and bookkeeping, are
    never inputs (inputs()). The snapshot table and its dataset must still match (check_fresh).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import pandas as pd

# Columns computed from the PR's diff range (mine_repo.mine_pr). When the at-open diff could not
# be recovered (open_snapshot == "final") that range is the final one, so each of these may
# carry what the PR became (ADR 0010) and is nulled. The contract can say so itself with an
# `x-riffle-diff-derived: true` keyword on the column; when it does, that is read in addition
# to this list (diff_derived_columns). The list is the complete set from reading mine_pr:
# diff stats and path flags, base-size and per-file history aggregates, blame, hotspots,
# percentiles of the diff's size, scanners, dependency and API checks, repo-local path
# features, and the LLM flags that read only the diff.
DIFF_FEATURES = (
    "AGE", "Entropy", "FIX", "LA", "LD", "LT", "ND", "NF", "NS", "REXP", "SEXP", "api_contract_change",
    "author_days_since_last_change", "author_file_familiarity", "authz_check_change",
    "bound_or_comparison_edit", "ccn_delta", "change_intent", "changes_public_api_signature",
    "cochange_degree", "cochange_partners_missing", "config_or_feature_flag_change",
    "db_schema_or_query_change", "dependency_major_bump_count", "docs_only", "entropy_pctile_repo",
    "error_handling_change", "exception_swallowed", "expected_test_cochange_missing",
    "feature_flag_guarding", "frac_generated_lines", "frac_test_lines", "ghsa_malware_dep",
    "ghsa_max_severity_introduced", "hardcoded_secret_added", "input_validation_change", "la_norm",
    "lang_mix", "ld_norm", "llm_touches_authn_authz", "max_NDEV", "max_NUC", "max_abandoned_lines_share",
    "max_ccn_changed_func", "max_file_churn_365d", "max_file_churn_90d", "max_file_complexity_ccn",
    "max_file_fix_density", "max_file_hotspot_score", "max_file_minor_contributor_count",
    "max_file_post_merge_ci_fail_rate", "max_file_revert_density", "max_file_top_author_share",
    "max_fix_decay_density", "max_hotfix_count_365d", "max_hunk_fix_proximity",
    "max_mod_lines_distinct_authors", "max_mod_lines_prior_fix_share", "max_recent_merge_burst_7d",
    "max_revert_decay_density", "max_test_cochange_ratio", "mean_file_age_days", "mean_file_fix_density",
    "mean_file_revert_density", "mean_hunk_fix_proximity", "mean_mod_lines_self_authored_share",
    "median_mod_line_age_days_median", "migration_reversibility", "min_days_since_last_fix",
    "min_days_since_last_revert", "min_file_hotspot_rank", "min_hotspot_rank_touched",
    "min_mod_line_age_days_min", "n_deps_added", "n_deps_downgraded", "n_deps_upgraded", "n_files_added",
    "n_files_removed", "n_files_renamed", "n_funcs_modified", "n_major_bumps", "n_topk_hotspots_touched",
    "new_env_or_config_key", "nf_pctile_repo", "null_handling_change", "numeric_money_precision",
    "observability_delta", "osv_ghsa_disagreement", "osv_vulns_fixed", "osv_vulns_introduced",
    "patch_missing_files", "path_detection_lag_days", "path_failure_mode_ci", "path_failure_mode_hotfix",
    "path_failure_mode_revert", "path_size_pctile", "pr_size_pctile_repo", "refactor_vs_behavior",
    "resource_lifecycle_change", "retry_timeout_idempotency_change", "revealed_path_scrutiny", "satd_added",
    "security_sensitive_ops", "semantic_risk_1to5", "semgrep_cwe_top25_new", "semgrep_findings_new",
    "semgrep_high_conf_security_new", "sum_file_complexity_ccn", "sum_hotfix_count_365d",
    "test_code_alignment", "test_only", "tests_added_for_behavior_change", "time_handling_change",
    "touches_auth_path", "touches_ci_config", "touches_codeowners_or_riffle_yml", "touches_dep_manifest",
    "touches_infra", "touches_lockfile", "touches_migration", "touches_topk_hotspot",
    "transaction_boundary_change", "unrelated_change_share", "unused_import_or_dead_code_added",
    "vuln_has_fix_available")
# snapshot columns that identify, weight or filter a row; never inputs
SNAPSHOT_NON_INPUTS = ("snapshot_id", "snapshot_at", "snapshot_index", "trigger", "trigger_role",
                       "snapshot_head_sha", "snapshot_base_sha", "head_found", "near_decided",
                       "snapshot_weight", "moment_bucket")
ID_COLUMNS = ("repo", "pr_number", "head_sha", "base_sha", "author_email", "created_at", "closed_at",
              "merged_at", "mined_at", "observed_through")
# row metadata and provenance that are not features: how the row was made, not what the PR is.
# (Other "Row metadata" columns in the contract, such as LT, AGE, FIX, title_len or
# has_milestone, are real features and stay inputs.)
METADATA_NON_INPUTS = ("merge_via", "open_snapshot", "miner_version", "miner_commit", "llm_model",
                       "llm_prompt_version", "domain")
KEY = ("repo", "pr_number")
DATASET_READ = KEY + ("head_sha", "label_merged", "label_bad_observed")
SNAPSHOT_READ = KEY + ("snapshot_head_sha", "near_decided", "outcome_3class", "snapshot_weight")
COMMIT_READ = KEY + ("head_sha", "at_open")
CHECKED = ("miner_version", "mined_at", "head_sha", "outcome_3class")   # same as the miner's consistency.py


def _contracts_dir() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        cand = os.path.join(here, "contracts")
        if os.path.isdir(cand):
            return cand
        here = os.path.dirname(here)
    return None


def _schema_props(name: str) -> dict:
    d = _contracts_dir()
    path = os.path.join(d, name) if d else None
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("properties", {})


def non_input_columns() -> set[str]:
    """Columns that must never be model inputs: those the contract describes as an
    outcome ("never a model input") or marks `x-riffle-non-input: true`, plus ids,
    provenance and the snapshot bookkeeping columns. Row metadata that is a real
    feature (LT, AGE, title_len ...) is not dropped."""
    out = set(ID_COLUMNS) | set(SNAPSHOT_NON_INPUTS) | set(METADATA_NON_INPUTS)
    for name in ("mined_dataset.schema.json", "mined_snapshot.schema.json"):
        for col, spec in _schema_props(name).items():
            if not isinstance(spec, dict):
                continue
            if "never a model input" in spec.get("description", "") or spec.get("x-riffle-non-input") is True:
                out.add(col)
    return out


def diff_derived_columns() -> set[str]:
    """Columns computed from the diff range: the schemas' `x-riffle-diff-derived: true`
    columns, plus DIFF_FEATURES (the list kept until the contract carries the keyword)."""
    out = set(DIFF_FEATURES)
    for name in ("mined_dataset.schema.json", "mined_snapshot.schema.json"):
        out |= {c for c, spec in _schema_props(name).items()
                if isinstance(spec, dict) and spec.get("x-riffle-diff-derived") is True}
    return out


def _read(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    if os.path.getsize(path) == 0:                 # an empty table written without rows
        return pd.DataFrame()
    return pd.read_json(path, lines=True)


def _with_columns(df: pd.DataFrame | None, cols: tuple[str, ...]) -> pd.DataFrame | None:
    """An empty table may carry no columns at all (older runs wrote it so); give it the
    ones the loader reads so it behaves as an empty table instead of raising KeyError."""
    if df is None or not df.empty:
        return df
    return df.assign(**{c: pd.Series(dtype=object) for c in cols if c not in df.columns})


def stale_prs(dataset: pd.DataFrame, snapshots: pd.DataFrame) -> dict:
    """PRs whose copied columns in `snapshots` differ from `dataset` (a re-mine
    without rebuilding the snapshots), and PRs missing from `dataset`."""
    key = ["repo", "pr_number"]
    if any(c not in df.columns for df in (dataset, snapshots) for c in key):
        return {"missing": [], "changed": []}      # a column-less empty table has nothing to compare
    cols = [c for c in CHECKED if c in dataset.columns and c in snapshots.columns]
    snap = snapshots.drop_duplicates(key).set_index(key)[cols]
    data = dataset.drop_duplicates(key).set_index(key)[cols]
    both = snap.index.intersection(data.index)
    a, b = snap.loc[both], data.loc[both]
    differs = ~((a == b) | (a.isna() & b.isna())).all(axis=1)
    return {"missing": list(snap.index.difference(data.index)), "changed": list(both[differs.to_numpy()])}


@dataclass
class Tables:
    dataset: pd.DataFrame
    snapshots: pd.DataFrame | None = None
    commit_features: pd.DataFrame | None = None


def load_tables(dataset_path: str, snapshots_path: str | None = None,
                commit_features_path: str | None = None, check_fresh: bool = True) -> Tables:
    """Read the dataset and, next to it, `<stem>_snapshots` and `<stem>_commit_features`
    (override either path to read others). Raises if the snapshots no longer match
    the dataset they were built from, unless check_fresh is False."""
    stem, ext = os.path.splitext(dataset_path)
    snaps, commits = snapshots_path or f"{stem}_snapshots{ext}", commit_features_path or f"{stem}_commit_features{ext}"
    t = Tables(_with_columns(_read(dataset_path), DATASET_READ),
               _with_columns(_read(snaps) if os.path.isfile(snaps) else None, SNAPSHOT_READ),
               _with_columns(_read(commits) if os.path.isfile(commits) else None, COMMIT_READ))
    if check_fresh and t.snapshots is not None:
        bad = stale_prs(t.dataset, t.snapshots)
        if bad["missing"] or bad["changed"]:
            raise ValueError(f"the snapshots no longer match the dataset ({len(bad['changed'])} PRs changed, "
                             f"{len(bad['missing'])} missing); rebuild them with snapshots.py")
    if check_fresh:
        if t.commit_features is not None and not t.commit_features.empty:
            # commit rows carry no mined_at, so the best freshness check is that their PRs are still mined
            have = set(zip(t.dataset["repo"], t.dataset["pr_number"]))
            gone = {k for k in zip(t.commit_features["repo"], t.commit_features["pr_number"]) if k not in have}
            if gone:
                raise ValueError(f"the commit features no longer match the dataset ({len(gone)} PRs missing); "
                                 f"rebuild them with snapshots.py")
    return t


def _unit(df: pd.DataFrame, by: str) -> pd.Series:
    """The split unit of each row: the lower-cased repo, or repo and PR number."""
    repo = df["repo"].astype(str).str.lower()
    return repo if by == "repo" else repo + "#" + df["pr_number"].astype(str)


def split_groups(tables: list[tuple[pd.DataFrame | None, tuple[str, ...]]], by: str = "pr") -> dict[str, str]:
    """{unit: canonical unit} joining the units that share a head commit, so a reopened or
    duplicated PR (same head_sha under two PR numbers) is never split across train and
    test. `tables` is [(frame, head sha columns)]. Only units that are joined appear."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for df, sha_cols in tables:
        if df is None or df.empty or any(c not in df.columns for c in ("repo", "pr_number")):
            continue
        unit = _unit(df, by)
        for col in sha_cols:
            if col not in df.columns:
                continue
            for u, sha in zip(unit, df[col]):
                if isinstance(sha, str) and sha:
                    a, b = find(u), find("sha:" + sha)
                    if a != b:
                        parent[b] = a
    comp: dict[str, list[str]] = {}
    for x in list(parent):
        if not x.startswith("sha:"):
            comp.setdefault(find(x), []).append(x)
    return {u: min(members) for members in comp.values() if len(members) > 1 for u in members}


def is_test(df: pd.DataFrame, by: str = "pr", test_frac: float = 0.2, seed: int = 0,
            groups: dict[str, str] | None = None) -> pd.Series:
    """True for rows on the test side. A stable hash of the lower-cased repo (by="repo")
    or of repo and PR number (by="pr"), so any table, any run and any PC agree, and a
    repo spelled differently on two PCs lands on one side. `groups` (split_groups) maps
    units that must stay together to one of them."""
    keys = _unit(df, by)
    if groups:
        keys = keys.map(lambda k: groups.get(k, k))
    h = keys.map(lambda k: int(hashlib.sha256(f"{seed}:{k}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF)
    return h < test_frac


@dataclass
class Frames:
    breakage_train: pd.DataFrame
    breakage_test: pd.DataFrame
    outcome_train: pd.DataFrame | None = None
    outcome_test: pd.DataFrame | None = None
    non_inputs: set = field(default_factory=set)

    def inputs(self, df: pd.DataFrame) -> list[str]:
        """The columns of `df` a model may read as features."""
        return [c for c in df.columns if c not in self.non_inputs and not c.startswith("label_")
                and not c.startswith("days_to_")]


def _mask_final(df: pd.DataFrame) -> pd.DataFrame:
    if "open_snapshot" not in df.columns:
        return df
    df = df.copy()
    final = df["open_snapshot"] == "final"
    for c in (c for c in sorted(diff_derived_columns()) if c in df.columns):
        # mask() widens a bool or int column to hold the nulls instead of failing on it
        df[c] = df[c].mask(final)
    return df


def build_frames(t: Tables, by: str = "pr", test_frac: float = 0.2, seed: int = 0,
                 with_commit_features: bool = False, mask_final: bool = True,
                 renormalize_weights: bool = True) -> Frames:
    """The breakage frame (merged PRs with an observed label_bad) and, when snapshots
    are present, the merge-outcome frame (snapshots, near_decided excluded). Splits
    are made on the same hash everywhere, so a PR is on one side in every frame. With
    renormalize_weights, each PR's kept snapshots are rescaled to add up to 1 again."""
    d = _with_columns(t.dataset, DATASET_READ)
    groups = split_groups([(d, ("head_sha",)), (t.snapshots, ("head_sha", "snapshot_head_sha")),
                           (t.commit_features, ("head_sha",))], by)
    d = d[(d["label_merged"] == True) & (d["label_bad_observed"] == True)]          # noqa: E712
    if mask_final:
        d = _mask_final(d)
    test = is_test(d, by, test_frac, seed, groups)
    f = Frames(d[~test].reset_index(drop=True), d[test].reset_index(drop=True), non_inputs=non_input_columns())
    if t.snapshots is not None:
        s = _with_columns(t.snapshots, SNAPSHOT_READ)
        s = s[s["near_decided"] != True]                          # noqa: E712
        s = s[s["outcome_3class"].notna()].copy()
        if renormalize_weights:
            # filtering took snapshots out of some PRs; scale the rest so each PR still adds up to 1
            s["snapshot_weight"] = s["snapshot_weight"] / s.groupby(["repo", "pr_number"])["snapshot_weight"].transform("sum")
        if mask_final:
            s = _mask_final(s)
        if with_commit_features and t.commit_features is not None and not t.commit_features.empty:
            c = t.commit_features[t.commit_features["at_open"] == False].drop(           # noqa: E712
                columns=["base_sha", "first_seen", "at_open", "error"], errors="ignore")
            c = c.rename(columns={"head_sha": "snapshot_head_sha"})
            c = c.rename(columns={k: f"commit_{k}" for k in c.columns if k not in ("repo", "pr_number", "snapshot_head_sha")})
            s = s.merge(c, on=["repo", "pr_number", "snapshot_head_sha"], how="left")
        stest = is_test(s, by, test_frac, seed, groups)
        f.outcome_train, f.outcome_test = s[~stest].reset_index(drop=True), s[stest].reset_index(drop=True)
    return f

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
  - The train/test split is by PR (or repo) and decided once from a stable hash,
    so every table puts a PR on the same side and a PR's snapshots never straddle it.
  - Breakage frame: merged PRs whose label_bad was fully observed (label_bad_observed).
  - Outcome frame: snapshots without near_decided, weighted by snapshot_weight.
  - Rows whose at-open diff was not recoverable (open_snapshot == "final") get their
    diff features nulled, because those may leak (ADR 0010).
  - Columns the contract marks as outcomes, row metadata or ids are never inputs
    (inputs()). The snapshot table and its dataset must still match (check_fresh).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import pandas as pd

# code features computed from a diff; nulled when the at-open diff isn't recoverable
DIFF_FEATURES = (
    "Entropy", "LA", "LD", "ND", "NF", "NS", "api_contract_change", "bound_or_comparison_edit", "ccn_delta",
    "dependency_major_bump_count", "docs_only", "frac_generated_lines", "frac_test_lines", "ghsa_malware_dep",
    "ghsa_max_severity_introduced", "hardcoded_secret_added", "lang_mix", "max_ccn_changed_func",
    "migration_reversibility", "n_deps_added", "n_deps_downgraded", "n_deps_upgraded", "n_files_added",
    "n_files_removed", "n_files_renamed", "n_funcs_modified", "n_major_bumps", "new_env_or_config_key",
    "observability_delta", "osv_ghsa_disagreement", "osv_vulns_fixed", "osv_vulns_introduced",
    "patch_missing_files", "semgrep_cwe_top25_new", "semgrep_findings_new", "semgrep_high_conf_security_new",
    "test_only", "touches_auth_path", "touches_ci_config", "touches_codeowners_or_riffle_yml",
    "touches_dep_manifest", "touches_infra", "touches_lockfile", "touches_migration",
    "unused_import_or_dead_code_added", "vuln_has_fix_available")
# snapshot columns that identify, weight or filter a row; never inputs
SNAPSHOT_NON_INPUTS = ("snapshot_id", "snapshot_at", "snapshot_index", "trigger", "trigger_role",
                       "snapshot_head_sha", "snapshot_base_sha", "head_found", "near_decided",
                       "snapshot_weight", "moment_bucket")
ID_COLUMNS = ("repo", "pr_number", "head_sha", "base_sha", "author_email", "created_at", "closed_at",
              "merged_at", "mined_at", "observed_through")
CHECKED = ("miner_version", "mined_at", "head_sha", "outcome_3class")   # same as the miner's consistency.py


def _contracts_dir() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        cand = os.path.join(here, "contracts")
        if os.path.isdir(cand):
            return cand
        here = os.path.dirname(here)
    return None


def non_input_columns() -> set[str]:
    """Columns that must never be model inputs: those the contract describes as an
    outcome or row metadata, plus ids and the snapshot bookkeeping columns."""
    out = set(ID_COLUMNS) | set(SNAPSHOT_NON_INPUTS)
    d = _contracts_dir()
    for name in ("mined_dataset.schema.json", "mined_snapshot.schema.json"):
        path = os.path.join(d, name) if d else None
        if not path or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            props = json.load(fh).get("properties", {})
        for col, spec in props.items():
            text = spec.get("description", "") if isinstance(spec, dict) else ""
            if "never a model input" in text or text.startswith("Row metadata"):
                out.add(col)
    return out


def _read(path: str) -> pd.DataFrame:
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_json(path, lines=True)


def stale_prs(dataset: pd.DataFrame, snapshots: pd.DataFrame) -> dict:
    """PRs whose copied columns in `snapshots` differ from `dataset` (a re-mine
    without rebuilding the snapshots), and PRs missing from `dataset`."""
    cols = [c for c in CHECKED if c in dataset.columns and c in snapshots.columns]
    key = ["repo", "pr_number"]
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
    t = Tables(_read(dataset_path),
               _read(snaps) if os.path.isfile(snaps) else None,
               _read(commits) if os.path.isfile(commits) else None)
    if check_fresh and t.snapshots is not None:
        bad = stale_prs(t.dataset, t.snapshots)
        if bad["missing"] or bad["changed"]:
            raise ValueError(f"the snapshots no longer match the dataset ({len(bad['changed'])} PRs changed, "
                             f"{len(bad['missing'])} missing); rebuild them with snapshots.py")
    return t


def is_test(df: pd.DataFrame, by: str = "pr", test_frac: float = 0.2, seed: int = 0) -> pd.Series:
    """True for rows on the test side. A stable hash of the repo (by="repo") or of
    repo and PR number (by="pr"), so any table, any run and any PC agree."""
    keys = df["repo"].astype(str) if by == "repo" else df["repo"].astype(str) + "#" + df["pr_number"].astype(str)
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
    for c in (c for c in DIFF_FEATURES if c in df.columns):
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
    d = t.dataset
    d = d[(d["label_merged"] == True) & (d["label_bad_observed"] == True)]          # noqa: E712
    if mask_final:
        d = _mask_final(d)
    test = is_test(d, by, test_frac, seed)
    f = Frames(d[~test].reset_index(drop=True), d[test].reset_index(drop=True), non_inputs=non_input_columns())
    if t.snapshots is not None:
        s = t.snapshots[t.snapshots["near_decided"] != True]                          # noqa: E712
        s = s[s["outcome_3class"].notna()].copy()
        if renormalize_weights:
            # filtering took snapshots out of some PRs; scale the rest so each PR still adds up to 1
            s["snapshot_weight"] = s["snapshot_weight"] / s.groupby(["repo", "pr_number"])["snapshot_weight"].transform("sum")
        if mask_final:
            s = _mask_final(s)
        if with_commit_features and t.commit_features is not None:
            c = t.commit_features[t.commit_features["at_open"] == False].drop(           # noqa: E712
                columns=["base_sha", "first_seen", "at_open", "error"], errors="ignore")
            c = c.rename(columns={"head_sha": "snapshot_head_sha"})
            c = c.rename(columns={k: f"commit_{k}" for k in c.columns if k not in ("repo", "pr_number", "snapshot_head_sha")})
            s = s.merge(c, on=["repo", "pr_number", "snapshot_head_sha"], how="left")
        stest = is_test(s, by, test_frac, seed)
        f.outcome_train, f.outcome_test = s[~stest].reset_index(drop=True), s[stest].reset_index(drop=True)
    return f

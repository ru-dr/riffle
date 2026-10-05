"""
aggregate.py — collapse variable per-file feature dicts into a fixed-length
vector, per the catalog's `agg` column (max / mean / sum / count).

A PR touches 1..N files; the model needs the same columns every time. We emit
max_/mean_/sum_ variants for numeric per-file features. `max_*` usually carries
the most signal ("how bad is the worst file"), but we keep mean/sum too and let
the model choose.
"""
from __future__ import annotations

import statistics

# which aggregations to emit per per-file feature (from the catalog `agg` column)
AGG_SPEC: dict[str, tuple[str, ...]] = {
    "file_revert_density": ("max", "mean"),
    "revert_decay_density": ("max",),
    "days_since_last_revert": ("min",),
    "file_fix_density": ("max", "mean"),
    "fix_decay_density": ("max",),
    "days_since_last_fix": ("min",),
    "file_churn_90d": ("max",),
    "file_churn_365d": ("max",),
    "recent_merge_burst_7d": ("max",),
    "hotfix_count_365d": ("max", "sum"),
    "test_cochange_ratio": ("max",),
    "file_post_merge_ci_fail_rate": ("max",),
    "file_age_days": ("mean",),
    "NUC": ("max",),
    "NDEV": ("max",),
    "file_complexity_ccn": ("max", "sum"),
    "file_hotspot_score": ("max",),
    "file_hotspot_rank": ("min",),   # min rank = the hottest file the PR touches
    "file_top_author_share": ("max",),
    "file_minor_contributor_count": ("max",),
    "abandoned_lines_share": ("max",),
    # line-level (Source 3)
    "mod_line_age_days_min": ("min",),
    "mod_line_age_days_median": ("median",),
    "mod_lines_distinct_authors": ("max",),
    "mod_lines_self_authored_share": ("mean",),
    "mod_lines_prior_fix_share": ("max",),
    "hunk_fix_proximity": ("max", "mean"),
}

_INTERNAL = {"_last_change_days", "_loc"}


def _apply(agg: str, values: list[float]):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    if agg == "max":
        return max(vals)
    if agg == "min":
        return min(vals)
    if agg == "sum":
        return sum(vals)
    if agg == "mean":
        return round(statistics.mean(vals), 4)
    if agg == "median":
        return round(statistics.median(vals), 4)
    return None


def aggregate_per_file(per_file: dict[str, dict]) -> dict:
    """
    per_file: {path: {feature: value, ...}, ...}
    returns:  {max_feature: ..., mean_feature: ..., ...}
    """
    if not per_file:
        return {}
    # gather each feature's values across files
    by_feature: dict[str, list] = {}
    for feats in per_file.values():
        for k, v in feats.items():
            if k in _INTERNAL:
                continue
            by_feature.setdefault(k, []).append(v)

    out: dict = {}
    for feat, values in by_feature.items():
        specs = AGG_SPEC.get(feat, ("max",))  # default max if unspecified
        for agg in specs:
            out[f"{agg}_{feat}"] = _apply(agg, values)
    return out

"""frames.py: the v6.13.3 audit fixes (inputs, final-row masking, the split, empty tables)."""
from __future__ import annotations

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import frames  # noqa: E402


def _dataset_props():
    return frames._schema_props("mined_dataset.schema.json")


def test_real_features_in_the_row_metadata_group_stay_inputs():
    non = frames.non_input_columns()
    for keep in ("LT", "AGE", "FIX", "la_norm", "title_len", "has_milestone", "body_len", "author_association",
                 "head_from_fork", "declared_hotfix", "author_prs_merged_repo", "LA", "EXP"):
        assert keep not in non, keep
    for drop in ("miner_version", "miner_commit", "llm_model", "llm_prompt_version", "domain", "repo", "head_sha",
                 "open_snapshot", "merge_via", "outcome_3class", "nowhere_reason", "ci_observed", "close_reason",
                 "post_open_commits", "label_bad", "created_at", "snapshot_id", "near_decided"):
        assert drop in non, drop


def test_every_diff_derived_column_is_in_the_contract_and_the_llm_diff_flags_are_covered():
    props = _dataset_props()
    assert set(frames.DIFF_FEATURES) <= set(props)
    llm_diff = {c for c, v in props.items() if str(v.get("description", "")).startswith("Source 6 LLM flag (diff only)")}
    assert llm_diff and llm_diff <= frames.diff_derived_columns()
    for c in ("la_norm", "LT", "AGE", "pr_size_pctile_repo", "nf_pctile_repo", "entropy_pctile_repo",
              "max_file_hotspot_score", "min_file_hotspot_rank", "max_hunk_fix_proximity", "mean_file_fix_density",
              "median_mod_line_age_days_median", "cochange_degree", "touches_topk_hotspot", "path_size_pctile",
              "semantic_risk_1to5"):
        assert c in frames.diff_derived_columns(), c


def test_a_schema_keyword_adds_to_the_masked_columns(monkeypatch):
    monkeypatch.setattr(frames, "_schema_props", lambda name: {"brand_new": {"x-riffle-diff-derived": True}})
    assert "brand_new" in frames.diff_derived_columns() and "LA" in frames.diff_derived_columns()


def test_final_rows_lose_every_diff_derived_column_and_exact_rows_keep_them():
    cols = sorted(frames.diff_derived_columns())
    df = pd.DataFrame({"open_snapshot": ["final", "exact"], "repo": ["o/a", "o/a"], "title_len": [5, 6],
                       **{c: [1, 1] for c in cols}})
    out = frames._mask_final(df)
    assert out.loc[0, cols].isna().all() and out.loc[1, cols].notna().all()
    assert list(out["title_len"]) == [5, 6]


def _frames(n=40, shared=None):
    rows = [{"repo": "O/Repo", "pr_number": i, "label_merged": True, "label_bad_observed": True,
             "head_sha": f"h{i}", "open_snapshot": "exact", "outcome_3class": "lands_clean",
             "miner_version": "1", "mined_at": "t"} for i in range(n)]
    return pd.DataFrame(rows)


def test_split_ignores_repo_case_and_keeps_shared_heads_together():
    d = _frames()
    lower = d.assign(repo="o/repo")
    assert list(frames.is_test(d)) == list(frames.is_test(lower))
    # reopened duplicates: a PR number per head, same head sha
    dup = pd.concat([d, d.assign(pr_number=d.pr_number + 1000, repo="o/repo")], ignore_index=True)
    groups = frames.split_groups([(dup, ("head_sha",))])
    side = frames.is_test(dup, groups=groups)
    key = dup.pr_number % 1000
    for k, g in side.groupby(key):
        assert g.nunique() == 1, k
    assert side.any() and not side.all()
    # without the grouping some pair would straddle
    plain = frames.is_test(dup)
    assert any(g.nunique() > 1 for _k, g in plain.groupby(key))


def test_chained_heads_form_one_component():
    d = pd.DataFrame({"repo": ["o/a"] * 3, "pr_number": [1, 2, 3], "head_sha": ["x", "x", "y"]})
    s = pd.DataFrame({"repo": ["o/a", "o/a"], "pr_number": [2, 3], "snapshot_head_sha": ["z", "z"]})
    g = frames.split_groups([(d, ("head_sha",)), (s, ("snapshot_head_sha",))])
    assert len({g["o/a#1"], g["o/a#2"], g["o/a#3"]}) == 1


@pytest.mark.parametrize("ext", ["jsonl", "parquet"])
def test_empty_tables_load_and_build_empty_frames(tmp_path, ext):
    if ext == "parquet":
        pytest.importorskip("pyarrow")
    base = tmp_path / f"dataset.{ext}"
    for name in (f"dataset.{ext}", f"dataset_snapshots.{ext}", f"dataset_commit_features.{ext}"):
        p = tmp_path / name
        if ext == "jsonl":
            p.write_text("")
        else:
            pd.DataFrame().to_parquet(p)              # no columns at all, as older runs wrote it
    t = frames.load_tables(str(base))
    f = frames.build_frames(t, with_commit_features=True)
    assert f.breakage_train.empty and f.breakage_test.empty and f.outcome_train.empty and f.outcome_test.empty


def test_commit_rows_of_prs_missing_from_the_dataset_are_stale(tmp_path):
    pd.DataFrame([{"repo": "o/a", "pr_number": 1, "label_merged": True, "label_bad_observed": True,
                   "head_sha": "h"}]).to_json(tmp_path / "dataset.jsonl", orient="records", lines=True)
    pd.DataFrame([{"repo": "o/a", "pr_number": 9, "head_sha": "x", "at_open": False}]).to_json(
        tmp_path / "dataset_commit_features.jsonl", orient="records", lines=True)
    with pytest.raises(ValueError, match="commit features no longer match"):
        frames.load_tables(str(tmp_path / "dataset.jsonl"))
    assert frames.load_tables(str(tmp_path / "dataset.jsonl"), check_fresh=False).commit_features is not None

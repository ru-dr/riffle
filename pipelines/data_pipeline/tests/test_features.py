import json

import pandas as pd
import pytest
import yaml
from frames import good_frames

from scripts import features, preprocess


@pytest.fixture
def clean():
    return preprocess.clean(good_frames())[0]


def test_only_closed_pull_requests_become_rows(clean):
    out, summary = features.build(clean, test_fraction=0.2)
    assert len(out) == int((clean["state"] == "closed").sum())
    assert summary["open_excluded"] == int((clean["state"] == "open").sum())


def test_the_label_is_merged_vs_closed_without_merge(clean):
    out, _ = features.build(clean, 0.2)
    closed = clean[clean["state"] == "closed"].set_index("id")
    expected = closed["merged_at"].notna().astype(int)
    assert out.set_index("id")["merged"].equals(expected.reindex(out["id"].values))
    assert set(out["merged"]) == {0, 1}


def test_nothing_from_after_creation_leaks_into_the_output(clean):
    out, _ = features.build(clean, 0.2)
    forbidden = {"closed_at", "merged_at", "state", "hours_open", "title", "body", "user"}
    assert not forbidden & set(out.columns)
    assert list(out.columns) == features.OUTPUT_COLUMNS


def test_split_is_by_time_with_the_newest_pull_requests_in_test(clean):
    out, summary = features.build(clean, 0.2)
    assert out["created_at"].is_monotonic_increasing
    train_end = out.loc[out["split"] == "train", "created_at"].max()
    test_start = out.loc[out["split"] == "test", "created_at"].min()
    assert train_end <= test_start
    assert summary["train"] + summary["test"] == summary["rows"]
    assert summary["test"] > 0


def test_missing_language_and_license_become_unknown_not_zero(clean):
    clean = clean.copy()
    clean.loc[clean.index[0], ["language", "license"]] = None
    out, _ = features.build(clean, 0.2)
    first = out[out["id"] == clean.loc[clean.index[0], "id"]]
    if len(first):  # the first row may be an open PR, which is excluded
        assert first.iloc[0]["language"] == "unknown" and first.iloc[0]["license_family"] == "unknown"


def test_no_body_is_zero_characters_with_a_flag(clean):
    clean = clean.copy()
    target = clean.index[clean["state"] == "closed"][0]
    clean.loc[target, "body_chars"] = 0
    out, _ = features.build(clean, 0.2)
    row = out[out["id"] == clean.loc[target, "id"]].iloc[0]
    assert row["body_chars"] == 0 and row["has_body"] == 0


def test_time_features_use_utc(clean):
    clean = clean.copy()
    target = clean.index[clean["state"] == "closed"][0]
    clean.loc[target, "created_at"] = pd.Timestamp("2025-03-08T23:30:00Z")  # a Saturday
    out, _ = features.build(clean, 0.2)
    row = out[out["id"] == clean.loc[target, "id"]].iloc[0]
    assert (row["hour_utc"], row["weekday"], row["is_weekend"]) == (23, 5, 1)


@pytest.mark.parametrize(
    "value,family",
    [("MIT", "permissive"), ("Apache-2.0", "permissive"), ("BSD-3-Clause", "permissive"), ("GPL-3.0", "copyleft"),
     ("AGPL-3.0", "copyleft"), ("MPL-2.0", "copyleft"), ("NOASSERTION", "unknown"), (None, "unknown"),
     (float("nan"), "unknown"), ("Proprietary-X", "other")],
)
def test_license_families(value, family):
    assert features.license_family(value) == family


def test_stars_buckets_match_the_boundaries(clean):
    clean = clean.copy()
    ids = clean.index[clean["state"] == "closed"][:4]
    clean.loc[ids, "stars"] = [199, 200, 4999, 5000]
    out, _ = features.build(clean, 0.2)
    got = out.set_index("id").loc[clean.loc[ids, "id"], "stars_bucket"].tolist()
    assert got == ["100-199", "200-999", "1k-4.9k", "5k+"]


def test_empty_input_gives_empty_output_and_no_cutoff(clean):
    out, summary = features.build(clean.iloc[0:0], 0.2)
    assert out.empty and summary["split_cutoff"] is None and summary["merged_rate"] is None


@pytest.mark.parametrize("bad", [0, 1, -0.1, 1.5, "0.2", None])
def test_test_fraction_must_be_between_zero_and_one(tmp_path, bad):
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump({"features": {"test_fraction": bad}}), encoding="utf-8")
    with pytest.raises(ValueError, match="test_fraction"):
        features.load_test_fraction(path)


def test_default_test_fraction_is_twenty_percent(tmp_path):
    path = tmp_path / "params.yaml"
    path.write_text("other: 1\n", encoding="utf-8")
    assert features.load_test_fraction(path) == 0.2


def test_run_writes_files_and_main_needs_preprocess_first(tmp_path, monkeypatch, clean):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    params = tmp_path / "params.yaml"
    params.write_text(yaml.safe_dump({"features": {"test_fraction": 0.25}}), encoding="utf-8")
    args = ["--params", str(params), "--processed", str(tmp_path / "p"), "--reports", str(tmp_path / "r")]
    assert features.main(args) == 1  # preprocess has not run
    (tmp_path / "p").mkdir()
    clean.to_parquet(tmp_path / "p" / "pull_requests_clean.parquet")
    assert features.main(args) == 0
    assert len(pd.read_parquet(tmp_path / "p" / "features.parquet")) == int((clean["state"] == "closed").sum())
    assert json.loads((tmp_path / "r" / "features.json").read_text(encoding="utf-8"))["rows"] > 0

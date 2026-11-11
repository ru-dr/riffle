import pandas as pd
import pytest
from frames import synthetic_features

from scripts import make_sample, slices


def test_the_sample_has_the_requested_rows_in_time_order_with_the_same_columns():
    f = synthetic_features(2000)
    sample = make_sample.make_sample(f, rows=500, seed=0)
    assert len(sample) == 500 and sample["created_at"].is_monotonic_increasing
    assert list(sample.columns) == list(f.columns)
    assert sample["id"].is_unique


def test_the_same_seed_gives_the_same_sample_and_a_different_seed_does_not():
    f = synthetic_features(2000)
    a, b = make_sample.make_sample(f, 500, 0), make_sample.make_sample(f, 500, 0)
    assert a["id"].tolist() == b["id"].tolist()
    assert a["id"].tolist() != make_sample.make_sample(f, 500, 1)["id"].tolist()


def test_rows_keep_their_split_and_label():
    f = synthetic_features(2000).set_index("id")
    sample = make_sample.make_sample(f.reset_index(), 500, 0).set_index("id")
    assert (sample["split"] == f.loc[sample.index, "split"]).all()
    assert (sample["merged"] == f.loc[sample.index, "merged"]).all()


def test_asking_for_more_rows_than_exist_returns_everything():
    f = synthetic_features(300)
    assert len(make_sample.make_sample(f, rows=10_000, seed=0)) == 300


def test_zero_rows_is_refused():
    with pytest.raises(ValueError, match="at least 1"):
        make_sample.make_sample(synthetic_features(100), rows=0, seed=0)


def test_main_writes_features_parquet_and_needs_the_features_stage_first(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    args = ["--processed", str(tmp_path / "p"), "--out", str(tmp_path / "s"), "--rows", "400"]
    assert make_sample.main(args) == 1
    (tmp_path / "p").mkdir()
    synthetic_features(2000).to_parquet(tmp_path / "p" / "features.parquet")
    assert make_sample.main(args) == 0
    assert len(pd.read_parquet(tmp_path / "s" / "features.parquet")) == 400


def test_the_sample_is_a_valid_input_for_the_slices_stage(tmp_path):
    sample = make_sample.make_sample(synthetic_features(6000), rows=3000, seed=0)
    result = slices.run_analysis(sample, {"min_group_size": 50})
    assert result["rows"]["test"] > 0 and set(result["overall"]) == set(slices.VARIANTS)

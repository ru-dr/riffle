import json

import pandas as pd
import pytest
import yaml
from frames import good_frames, good_pull_request

from scripts import data_io, validate


SMALL = {"pull_request": 1, "repository": 1}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_good_data_passes_and_writes_all_three_reports(tmp_path):
    assert validate.run(good_frames(), tmp_path, iqr_factor=3.0, min_rows=SMALL) == 0
    for name in ("schema_stats.json", "validation.json", "anomalies.json"):
        assert (tmp_path / name).is_file()
    assert read(tmp_path / "anomalies.json")["errors"] == 0
    assert all(table["success"] for table in read(tmp_path / "validation.json").values())


def test_a_warning_does_not_fail_the_stage(tmp_path):
    frames = good_frames()
    frames["pull_request"] = good_pull_request(rows=2000)
    frames["pull_request"].loc[1, "closed_at"] = "2000-01-01T00:00:00Z"
    frames["repository"] = pd.concat([frames["repository"]] * 1)
    frames["pull_request"]["repo_id"] = 1 + frames["pull_request"]["id"] % 30
    assert validate.run(frames, tmp_path, iqr_factor=3.0, min_rows=SMALL) == 0
    report = read(tmp_path / "anomalies.json")
    assert report["errors"] == 0 and report["warnings"] >= 1


def test_an_unknown_agent_fails_great_expectations(tmp_path):
    frames = good_frames()
    frames["pull_request"].loc[0, "agent"] = "SkyNet"
    assert validate.run(frames, tmp_path, iqr_factor=3.0, min_rows=SMALL) == 1
    failed = [r for r in read(tmp_path / "validation.json")["pull_request"]["results"] if not r["success"]]
    assert [(r["expectation"], r["column"]) for r in failed] == [("expect_column_values_to_be_in_set", "agent")]


def test_duplicate_ids_fail_great_expectations(tmp_path):
    frames = good_frames()
    frames["repository"].loc[1, "id"] = 1
    assert validate.run(frames, tmp_path, iqr_factor=3.0, min_rows=SMALL) == 1


def test_a_schema_violation_fails_and_skips_great_expectations_for_that_table(tmp_path):
    frames = good_frames()
    frames["repository"] = frames["repository"].drop(columns=["stars"])
    assert validate.run(frames, tmp_path, iqr_factor=3.0, min_rows=SMALL) == 1
    assert "repository" not in read(tmp_path / "validation.json")
    assert read(tmp_path / "anomalies.json")["errors"] >= 1


def test_the_profile_lists_rates_and_hides_private_columns():
    stats = validate.profile(good_frames()["pull_request"])
    assert stats["rows"] == 30
    assert stats["columns"]["closed_at"]["null_rate"] == pytest.approx(8 / 30, abs=0.01)
    assert "top_values" in stats["columns"]["agent"]
    assert "top_values" not in stats["columns"]["user"]  # a username must not land in a report
    assert stats["columns"]["number"]["min"] == 1.0


def test_params_need_a_commit_and_a_positive_factor(tmp_path):
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump({"aidev": {"commit": "a" * 40}}), encoding="utf-8")
    assert validate.load_params(path) == {"commit": "a" * 40, "iqr_factor": 3.0, "min_rows": {}}
    path.write_text(yaml.safe_dump({"aidev": {"commit": "a" * 40}, "validation": {"iqr_factor": 0}}), encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        validate.load_params(path)
    path.write_text("other: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aidev.commit"):
        validate.load_params(path)


def test_main_fails_cleanly_when_the_raw_data_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    params = tmp_path / "params.yaml"
    params.write_text(yaml.safe_dump({"aidev": {"commit": "a" * 40}}), encoding="utf-8")
    code = validate.main(["--params", str(params), "--raw", str(tmp_path / "none"), "--reports", str(tmp_path / "r")])
    assert code == 1
    assert "validate failed" in (tmp_path / "logs" / "validate.log").read_text(encoding="utf-8")


def test_load_raw_names_the_missing_table(tmp_path):
    with pytest.raises(data_io.MissingTable, match="pull_request.parquet"):
        data_io.load_raw(tmp_path, "a" * 40)


def test_main_runs_end_to_end_on_parquet_files(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    commit = "b" * 40
    raw = tmp_path / "raw" / commit
    raw.mkdir(parents=True)
    for table, df in good_frames().items():
        df.to_parquet(raw / f"{table}.parquet")
    params = tmp_path / "params.yaml"
    params.write_text(yaml.safe_dump({"aidev": {"commit": commit}, "validation": {"min_rows": SMALL}}), encoding="utf-8")
    code = validate.main(["--params", str(params), "--raw", str(tmp_path / "raw"), "--reports", str(tmp_path / "r")])
    assert code == 0
    assert (tmp_path / "r" / "anomalies.json").is_file()

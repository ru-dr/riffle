import json

import pandas as pd
import pytest
import yaml
from frames import good_frames, good_pull_request, good_repository

from scripts import data_io, preprocess


def test_clean_data_loses_no_rows_and_drops_private_columns():
    frames = good_frames()
    clean, quarantine = preprocess.clean(frames)
    assert len(clean) == len(frames["pull_request"]) and quarantine.empty
    assert not {"user", "user_id", "repo_url", "html_url"} & set(clean.columns)
    assert list(clean.columns) == preprocess.CLEAN_COLUMNS


def test_timestamps_become_utc_datetimes_and_nulls_stay_null():
    clean, _ = preprocess.clean(good_frames())
    for column in preprocess.TIME_COLUMNS:  # the resolution (ns or us) depends on the pandas version
        assert isinstance(clean[column].dtype, pd.DatetimeTZDtype) and str(clean[column].dt.tz) == "UTC"
    open_rows = clean[clean["state"] == "open"]
    assert open_rows["closed_at"].isna().all()  # an open PR has no closed_at; it is not filled in


def test_repository_columns_are_joined_on_repo_id():
    clean, _ = preprocess.clean(good_frames())
    row = clean.iloc[0]
    assert row["repo_name"] == "o/r0" and row["language"] == "Python" and row["stars"] == 100


def test_a_contradictory_row_is_quarantined_with_its_reason():
    frames = good_frames()
    frames["pull_request"].loc[1, "closed_at"] = "2000-01-01T00:00:00Z"
    clean, quarantine = preprocess.clean(frames)
    assert len(clean) == 29 and len(quarantine) == 1
    assert quarantine.iloc[0]["id"] == 1001
    assert quarantine.iloc[0]["reason"] == "closed_at before created_at"


def test_one_row_can_have_two_reasons_and_is_still_counted_once():
    frames = good_frames()
    frames["pull_request"].loc[1, ["closed_at", "merged_at"]] = "2000-01-01T00:00:00Z"
    clean, quarantine = preprocess.clean(frames)
    assert len(quarantine) == 1
    assert quarantine.iloc[0]["reason"] == "closed_at before created_at; merged_at before created_at"
    assert len(clean) + len(quarantine) == 30


def test_a_duplicate_pull_request_keeps_the_first():
    frames = good_frames()
    frames["pull_request"].loc[5, ["repo_id", "number"]] = frames["pull_request"].loc[4, ["repo_id", "number"]].values
    clean, quarantine = preprocess.clean(frames)
    assert quarantine["id"].tolist() == [1005] and 1004 in clean["id"].tolist()


def test_a_pull_request_with_an_unknown_repository_is_quarantined_not_dropped():
    frames = good_frames()
    frames["pull_request"].loc[3, "repo_id"] = 999_999
    clean, quarantine = preprocess.clean(frames)
    assert quarantine["reason"].tolist() == ["repository not found"]
    assert len(clean) == 29


def test_quarantine_holds_ids_and_dates_only():
    frames = good_frames()
    frames["pull_request"].loc[1, "closed_at"] = "2000-01-01T00:00:00Z"
    _, quarantine = preprocess.clean(frames)
    assert list(quarantine.columns) == preprocess.QUARANTINE_COLUMNS
    assert "user" not in quarantine.columns and "title" not in quarantine.columns


def test_titles_are_trimmed():
    frames = good_frames()
    frames["pull_request"].loc[0, "title"] = "  padded  "
    clean, _ = preprocess.clean(frames)
    assert clean.loc[clean["id"] == 1000, "title"].iloc[0] == "padded"


def test_an_empty_table_gives_empty_outputs():
    frames = {"pull_request": good_pull_request().iloc[0:0], "repository": good_repository()}
    clean, quarantine = preprocess.clean(frames)
    assert clean.empty and quarantine.empty
    assert preprocess.summary(0, clean, quarantine)["rows_quarantined"] == 0


def test_run_writes_files_and_a_summary(tmp_path):
    frames = good_frames()
    frames["pull_request"].loc[1, "closed_at"] = "2000-01-01T00:00:00Z"
    result = preprocess.run(frames, tmp_path / "p", tmp_path / "r")
    assert result == {
        "rows_in": 30,
        "rows_clean": 29,
        "rows_quarantined": 1,
        "quarantined_by_reason": {"closed_at before created_at": 1},
    }
    assert len(pd.read_parquet(tmp_path / "p" / "pull_requests_clean.parquet")) == 29
    assert json.loads((tmp_path / "r" / "preprocess.json").read_text(encoding="utf-8")) == result


def test_main_runs_end_to_end_and_fails_cleanly_without_data(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    commit = "c" * 40
    params = tmp_path / "params.yaml"
    params.write_text(yaml.safe_dump({"aidev": {"commit": commit}}), encoding="utf-8")
    args = ["--params", str(params), "--raw", str(tmp_path / "raw"), "--processed", str(tmp_path / "p"),
            "--reports", str(tmp_path / "r")]
    assert preprocess.main(args) == 1  # nothing acquired yet
    raw = tmp_path / "raw" / commit
    raw.mkdir(parents=True)
    for table, df in good_frames().items():
        df.to_parquet(raw / f"{table}.parquet")
    assert preprocess.main(args) == 0
    assert (tmp_path / "p" / "pull_requests_clean.parquet").is_file()


def test_data_io_error_names_the_missing_file(tmp_path):
    with pytest.raises(data_io.MissingTable):
        data_io.load_raw(tmp_path, "c" * 40)

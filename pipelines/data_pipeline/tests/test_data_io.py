import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from frames import good_frames

from scripts import data_io


@pytest.fixture
def raw(tmp_path):
    folder = tmp_path / ("d" * 40)
    folder.mkdir()
    for table, df in good_frames().items():
        df.to_parquet(folder / f"{table}.parquet")
    return tmp_path, "d" * 40


def test_text_is_cut_to_one_character_and_nulls_stay_null(raw):
    root, commit = raw
    full = data_io.load_raw(root, commit)["pull_request"]
    full.loc[3, "body"] = None
    full.to_parquet(root / commit / "pull_request.parquet")
    slim = data_io.load_raw(root, commit, text=False)["pull_request"]
    assert slim["body"].isna().sum() == 1 and pd.isna(slim.loc[3, "body"])
    assert slim.loc[0, "title"] == "c" and slim.loc[0, "user"] == "b"
    assert slim["body"].dropna().str.len().max() == 1


def test_other_columns_and_row_count_are_unchanged(raw):
    root, commit = raw
    full = data_io.load_raw(root, commit)
    slim = data_io.load_raw(root, commit, text=False)
    for table in full:
        assert len(slim[table]) == len(full[table]) and list(slim[table].columns) == list(full[table].columns)
    assert slim["pull_request"]["html_url"].equals(full["pull_request"]["html_url"])
    assert slim["repository"]["stars"].equals(full["repository"]["stars"])


def test_many_batches_give_the_same_rows(raw, monkeypatch):
    root, commit = raw
    monkeypatch.setattr(data_io, "BATCH_ROWS", 7)
    slim = data_io.load_raw(root, commit, text=False)["pull_request"]
    assert len(slim) == 30 and slim["id"].is_unique


def test_an_empty_table_still_loads(tmp_path):
    folder = tmp_path / ("e" * 40)
    folder.mkdir()
    for table, df in good_frames().items():
        df.iloc[0:0].to_parquet(folder / f"{table}.parquet")
    assert data_io.load_raw(tmp_path, "e" * 40, text=False)["pull_request"].empty


def test_a_text_column_of_the_wrong_type_is_left_alone(tmp_path):
    folder = tmp_path / ("f" * 40)
    folder.mkdir()
    frames = good_frames()
    pq.write_table(pa.table({"id": [1, 2], "title": [10, 20]}), folder / "pull_request.parquet")
    frames["repository"].to_parquet(folder / "repository.parquet")
    slim = data_io.load_raw(tmp_path, "f" * 40, text=False)["pull_request"]
    assert slim["title"].tolist() == [10, 20]  # not cut, so the schema check can report the wrong type

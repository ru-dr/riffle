import pandas as pd
import pytest
from frames import good_frames, good_pull_request, good_repository

from scripts import anomalies


def kinds(found):
    return {(a.kind, a.table, a.column, a.severity) for a in found}


def test_clean_data_has_no_anomalies():
    assert anomalies.detect(good_frames()) == []


# schema violations


def test_a_missing_and_an_extra_column_are_schema_errors():
    repo = good_repository().drop(columns=["stars"]).assign(surprise=1)
    found = anomalies.schema_violations(repo, "repository")
    assert kinds(found) == {
        ("schema", "repository", "stars", "error"),
        ("schema", "repository", "surprise", "error"),
    }


def test_a_column_of_the_wrong_type_is_a_schema_error():
    repo = good_repository()
    repo["stars"] = repo["stars"].astype(float) + 0.5
    found = anomalies.schema_violations(repo, "repository")
    assert [(a.column, a.severity) for a in found] == [("stars", "error")]


def test_a_table_with_schema_errors_skips_the_other_checks():
    pr = good_pull_request().drop(columns=["closed_at"])
    found = anomalies.detect({"pull_request": pr, "repository": good_repository()})
    assert {a.kind for a in found} == {"schema"}


# missing values


def test_nulls_above_the_tolerated_rate_are_an_error():
    pr = good_pull_request()
    pr.loc[:5, "title"] = None  # title tolerates none
    found = anomalies.missing_values(pr, "pull_request")
    assert kinds(found) == {("missing", "pull_request", "title", "error")}
    assert found[0].count == 6
    assert found[0].examples == [1000, 1001, 1002, 1003, 1004]


def test_nulls_within_the_tolerated_rate_are_fine():
    pr = good_pull_request()  # closed_at is null for the open quarter, which the schema allows
    assert anomalies.missing_values(pr, "pull_request") == []


def test_an_empty_table_does_not_crash():
    empty = good_repository().iloc[0:0]
    assert anomalies.missing_values(empty, "repository") == []


# invalid formats


@pytest.mark.parametrize(
    "column,bad",
    [
        ("created_at", "2025-01-01 10:00:00"),
        ("closed_at", "yesterday"),
        ("merged_at", "2025-13-01"),
        ("html_url", "http://example.com/x"),
    ],
)
def test_badly_formatted_values_are_errors(column, bad):
    pr = good_pull_request()
    pr.loc[1, column] = bad
    found = anomalies.invalid_formats(pr, "pull_request")
    assert [(a.column, a.count, a.severity) for a in found] == [(column, 1, "error")]


def test_a_null_is_not_a_format_error():
    pr = good_pull_request()
    pr.loc[1, "merged_at"] = None
    assert anomalies.invalid_formats(pr, "pull_request") == []


def test_a_bad_repository_name_is_an_error():
    repo = good_repository()
    repo.loc[2, "full_name"] = "no-slash"
    assert [a.column for a in anomalies.invalid_formats(repo, "repository")] == ["full_name"]


# outliers


def test_an_extreme_value_is_a_warning():
    stars = pd.Series([100 + i for i in range(100)] + [5_000_000_000])
    found = anomalies.outliers(stars, "repository", "stars", factor=3.0)
    assert [(a.count, a.severity) for a in found] == [(1, "warn")]
    assert found[0].examples == [5_000_000_000.0]


def test_a_heavy_tail_alone_is_not_flagged_on_the_log_scale():
    stars = pd.Series([int(1.2**i) for i in range(1, 60)])
    assert anomalies.outliers(stars, "repository", "stars", factor=3.0) == []


def test_too_few_values_gives_no_outliers():
    assert anomalies.outliers(pd.Series([1, 2, 1_000_000]), "repository", "stars", factor=3.0) == []


def test_nulls_are_ignored_by_the_outlier_check():
    stars = pd.Series([100 + i for i in range(50)] + [None] * 10)
    assert anomalies.outliers(stars, "repository", "stars", factor=3.0) == []


# contradictions


def test_closed_before_created_is_a_warning_when_rare():
    pr = good_pull_request(rows=2000)
    pr.loc[1, "closed_at"] = "2000-01-01T00:00:00Z"
    found = anomalies.consistency(pr)
    assert [(a.message, a.count, a.severity) for a in found] == [("closed_at before created_at", 1, "warn")]


def test_the_same_contradiction_is_an_error_when_common():
    pr = good_pull_request(rows=30)
    pr.loc[1, "closed_at"] = "2000-01-01T00:00:00Z"  # 1 of 30 is far above 0.1%
    assert anomalies.consistency(pr)[0].severity == "error"


def test_open_pull_request_with_a_closed_at_is_flagged():
    pr = good_pull_request()
    row = pr.index[pr["state"] == "open"][0]
    pr.loc[row, "closed_at"] = "2025-03-01T00:00:00Z"
    messages = {a.message for a in anomalies.consistency(pr)}
    assert "state is open but closed_at is set" in messages


def test_closed_pull_request_without_closed_at_is_flagged():
    pr = good_pull_request()
    row = pr.index[pr["state"] == "closed"][0]
    pr.loc[row, "closed_at"] = None
    assert "state is closed but closed_at is empty" in {a.message for a in anomalies.consistency(pr)}


# references


def test_a_pull_request_pointing_at_an_unknown_repository_is_an_error():
    pr = good_pull_request()
    pr.loc[3, "repo_id"] = 999_999
    found = anomalies.referential(pr, good_repository())
    assert [(a.count, a.severity) for a in found] == [(1, "error")]


def test_detect_reports_each_kind_together():
    pr = good_pull_request()
    pr.loc[0, "html_url"] = "nope"
    pr.loc[1, "repo_id"] = 999_999
    found = anomalies.detect({"pull_request": pr, "repository": good_repository()})
    assert {"format", "reference"} <= {a.kind for a in found}

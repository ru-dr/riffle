"""The pr_features message carries one PR in the shape the model was trained on (ADR 0027).

Its envelope is checked against pr_features.schema.json, and its row against
mined_dataset.schema.json: the same contract the miner's training rows must meet. The
x-riffle-code-checks of pr_features are enforced here for the fixture.

Run: python -m pytest contracts/tests
"""
import json
import pathlib

import jsonschema
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
FIXTURE = ROOT / "fixtures" / "pr_features" / "pull_request.opened.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def message():
    return load(FIXTURE)


def errors(schema, doc):
    v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)
    return [f"{list(e.path)}: {e.message}" for e in v.iter_errors(doc)]


def test_the_message_matches_pr_features(message):
    assert errors(load(CONTRACTS / "pr_features.schema.json"), message) == []


def test_the_row_is_a_mined_dataset_row(message):
    assert errors(load(CONTRACTS / "mined_dataset.schema.json"), message["row"]) == []


def test_the_row_is_about_the_same_pr(message):
    row = message["row"]
    assert (row["repo"], row["pr_number"], row["head_sha"]) == (message["repo"], message["pr_number"], message["head_sha"])
    assert row["miner_version"] == message["miner_version"]


def test_an_open_pr_has_no_outcome(message):
    row = message["row"]
    for column in ("outcome_3class", "closed_at", "merged_at", "label_bad", "label_revert", "label_hotfix"):
        assert row.get(column) is None, column


def test_disabled_sources_leave_their_columns_empty(message):
    row = message["row"]
    assert set(message["disabled_sources"]) == {"llm", "semgrep", "osv"}
    for column in ("semantic_risk_1to5", "change_intent", "refactor_vs_behavior", "semgrep_findings_new",
                   "osv_vulns_introduced", "ghsa_max_severity_introduced"):
        assert row.get(column) is None, column


def test_the_miner_version_is_one_the_miner_has_had(message):
    version = (ROOT / "pipelines" / "ingestion" / "repo_miner" / "version.py").read_text(encoding="utf-8")
    major = lambda v: int(v.split(".")[0])
    current = [line.split('"')[1] for line in version.splitlines() if line.strip().startswith(("VERSION", "__version__")) and '"' in line]
    assert current, "version.py has no version string"
    assert major(message["miner_version"]) <= major(current[0])

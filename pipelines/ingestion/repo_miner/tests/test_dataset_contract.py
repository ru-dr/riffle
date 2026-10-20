"""The miner's output matches contracts/mined_dataset.schema.json both ways.

Mines a small throwaway repo offline (squash-merged "(#N)" commits, git-only
PR discovery) with different stages switched off, then checks that every row
has exactly the contract's columns (none extra, none missing), validates
against the schema, and round-trips through the flat form (ADR 0012).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = MINER.parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "mined_dataset.schema.json").read_text())

OFFLINE = ["--no-network", "--pr-discovery", "git", "--no-llm", "--min-age-days", "0"]
STAGE_SETS = {
    "stages-on": [],
    "stages-off": ["--no-scanners", "--no-api-contract", "--no-szz", "--no-merge-outcome",
                   "--no-declared-labels", "--no-review-history", "--no-ci-history"],
}


def _git(repo, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@example.com",
               GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@example.com")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    r = tmp_path_factory.mktemp("syn")
    _git(r, "init", "-q", "-b", "main")
    (r / "a.py").write_text("def f():\n    return 1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init", date="2025-01-01T00:00:00Z")
    for i in (1, 2, 3):
        with open(r / "a.py", "a") as fh:
            fh.write(f"def g{i}():\n    return {i}\n")
        _git(r, "commit", "-qam", f"change {i} (#{i})", date=f"2025-0{i + 1}-01T00:00:00Z")
    _git(r, "remote", "add", "origin", "https://github.com/example/syn")
    return r


@pytest.fixture(scope="module", params=sorted(STAGE_SETS))
def rows(request, repo, tmp_path_factory):
    out = tmp_path_factory.mktemp("out") / "rows.jsonl"
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "LLM_API_KEY")}
    subprocess.run([sys.executable, str(MINER / "mine_repo.py"), "--repo", str(repo), "--out", str(out),
                    "--snapshot-dir", str(out.parent / "snapshots"), "--health-dir", str(out.parent / "health"),
                    *OFFLINE, *STAGE_SETS[request.param]],
                   cwd=MINER, env=env, check=True, capture_output=True, timeout=600)
    got = [json.loads(line) for line in out.read_text().splitlines()]
    assert got, "the miner wrote no rows"
    return got


def test_columns_match_contract_both_ways(rows):
    contract = set(SCHEMA["properties"])
    # optional properties exist so older rows still validate; the miner
    # writes every property, optional ones included
    assert set(SCHEMA["required"]) <= contract
    for row in rows:
        assert sorted(set(row) - contract) == [], "columns the contract doesn't list"
        assert sorted(contract - set(row)) == [], "contract columns the miner didn't write"


def test_rows_validate(rows):
    jsonschema = pytest.importorskip("jsonschema")
    v = jsonschema.Draft202012Validator(SCHEMA)
    for row in rows:
        errors = [f"{'/'.join(map(str, e.path))}: {e.message}" for e in v.iter_errors(row)]
        assert errors == []


def test_rows_flatten_losslessly(rows):
    sys.path.insert(0, str(CONTRACTS))
    import flatten
    for row in rows:
        assert flatten.unflatten(flatten.flatten(row), SCHEMA) == row

"""A failed LLM call never leaves a row with empty LLM columns: the miner
retries that PR in place, and if it still fails it stops the repo (exit 3)
without writing --out, so a rerun resumes from that PR.

Uses a fake `claude` on PATH that fails a set number of times, on a small
throwaway repo mined offline.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))
from features.llm_flags import FLAG_SCHEMA  # noqa: E402

VALUE = {"bool": False, "int": 0, "ordinal_1_5": 1, "ordinal_0_3": 0}
FLAGS = {k: (spec[0] if isinstance(spec, list) else VALUE[spec]) for k, spec in FLAG_SCHEMA.items()}

FAKE_CLAUDE = """#!/bin/sh
for a in "$@"; do [ "$a" = "/usage" ] && { echo '{"result": ""}'; exit 0; }; done
cat > /dev/null
n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$COUNTER"
if [ $n -le $FAIL_N ]; then echo '{"is_error": true, "result": "Internal server error"}'; exit 1; fi
echo '{"structured_output": FLAGS}'
"""


def _git(repo, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@example.com",
               GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@example.com")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "syn"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("def f():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init", date="2025-01-01T00:00:00Z")
    for i in (1, 2, 3):
        with open(repo / "a.py", "a") as fh:
            fh.write(f"def g{i}():\n    return {i}\n")
        _git(repo, "commit", "-qam", f"change {i} (#{i})", date=f"2025-0{i + 1}-01T00:00:00Z")
    _git(repo, "remote", "add", "origin", "https://github.com/example/syn")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "claude"
    fake.write_text(FAKE_CLAUDE.replace("FLAGS", json.dumps(FLAGS)))
    fake.chmod(0o755)
    return repo, bindir, tmp_path


def _mine(repo, bindir, tmp, fail_n):
    out = tmp / "rows.jsonl"
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "LLM_API_KEY")}
    env.update(PATH=f"{bindir}{os.pathsep}{env['PATH']}", COUNTER=str(tmp / "count"),
               FAIL_N=str(fail_n), LLM_RETRY_BACKOFF_S="0", LLM_CALL_RETRY_S="0",
               LLM_BACKEND="claude_cli", RIFFLE_NO_PLAN_USAGE="1")
    p = subprocess.run([sys.executable, str(MINER / "mine_repo.py"), "--repo", str(repo),
                        "--out", str(out), "--no-network", "--pr-discovery", "git",
                        "--min-age-days", "0", "--llm", "--llm-scope", "all",
                        "--workers", "1", "--llm-concurrency", "1", "--no-szz",
                        "--snapshot-dir", str(tmp / "snapshots"), "--health-dir", str(tmp / "health")],
                       cwd=MINER, env=env, capture_output=True, text=True, timeout=600)
    return p, out


def test_failed_call_is_retried_and_the_row_keeps_its_llm_columns(setup):
    repo, bindir, tmp = setup
    # the first PR's call fails 3 times (all of claude -p's quick retries),
    # then the row retry succeeds
    p, out = _mine(repo, bindir, tmp, fail_n=3)
    assert p.returncode == 0, p.stderr[-2000:]
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 3
    for r in rows:
        assert all(r[k] is not None for k in FLAG_SCHEMA), r["pr_number"]
    assert "retry 1/3" in p.stderr


def test_llm_that_stays_down_stops_the_repo_without_empty_rows(setup):
    repo, bindir, tmp = setup
    p, out = _mine(repo, bindir, tmp, fail_n=10_000)
    assert p.returncode == 3, p.stderr[-2000:]
    assert not out.exists() or not out.read_text().strip()
    ckpt = pathlib.Path(str(out) + ".partial.jsonl")
    saved = [json.loads(line) for line in ckpt.read_text().splitlines()] if ckpt.exists() else []
    rows = [r for r in saved if "pr_number" in r]
    assert all(all(r[k] is not None for k in FLAG_SCHEMA) for r in rows)
    assert "Stopping this repo so no row is written without it" in p.stderr

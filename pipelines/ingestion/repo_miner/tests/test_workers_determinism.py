"""The worker count changes speed, not data.

Mines one throwaway repo offline with 1 worker and with 4 and requires the
same rows in the same order. Only the run-time stamps may differ.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
OFFLINE = ["--no-network", "--pr-discovery", "git", "--no-llm", "--min-age-days", "0"]
RUN_STAMPS = {"mined_at", "observed_through"}


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
    for name in ("a.py", "b.py", "c.py"):
        (r / name).write_text("def f():\n    return 1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init", date="2025-01-01T00:00:00Z")
    for i in range(1, 13):
        # PRs of different sizes touching different files, so workers finish out of order
        for name in ("a.py", "b.py", "c.py")[: 1 + i % 3]:
            with open(r / name, "a") as fh:
                fh.write("".join(f"def g{i}_{k}():\n    return {k}\n" for k in range(1 + (i * 7) % 11)))
        msg = f"fix bug {i} (#{i})" if i % 4 == 0 else f"change {i} (#{i})"
        _git(r, "commit", "-qam", msg, date=f"2025-{(i - 1) % 9 + 2:02d}-{i:02d}T00:00:00Z")
    _git(r, "remote", "add", "origin", "https://github.com/example/syn")
    return r


def _mine(repo, tmp_path, workers):
    out = tmp_path / f"w{workers}" / "rows.jsonl"
    out.parent.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "LLM_API_KEY")}
    subprocess.run([sys.executable, str(MINER / "mine_repo.py"), "--repo", str(repo), "--out", str(out),
                    "--snapshot-dir", str(out.parent / "snapshots"), "--health-dir", str(out.parent / "health"),
                    "--workers", str(workers), *OFFLINE],
                   cwd=MINER, env=env, check=True, capture_output=True, timeout=900)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    return [{k: v for k, v in r.items() if k not in RUN_STAMPS} for r in rows]


def test_one_worker_and_four_give_the_same_rows(repo, tmp_path):
    one = _mine(repo, tmp_path, 1)
    four = _mine(repo, tmp_path, 4)
    assert len(one) >= 12
    assert [r["pr_number"] for r in one] == [r["pr_number"] for r in four]
    for a, b in zip(one, four):
        diff = {k: (a[k], b.get(k)) for k in a if a[k] != b.get(k)}
        assert not diff, f"PR #{a['pr_number']} differs by worker count: {diff}"

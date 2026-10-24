"""A PC that restarted on a newer miner version is still one PC in the store's run list."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner")))

import app as store_app  # noqa: E402
import index  # noqa: E402


def test_a_pc_with_two_miner_versions_is_listed_once(tmp_path):
    ix = index.Index(str(tmp_path / "i.sqlite"))
    ix.put_run("pc1of6-abc", "6.12.1/abc", {"prs_done": 1019})
    ix.put_run("pc2of6-abc", "6.12.1/abc", {"prs_done": 5})
    ix.put_run("pc1of6-abc", "6.12.5/abc", {"prs_done": 2024})
    runs = ix.latest_runs()
    assert [r["pc"] for r in runs] == ["pc1of6-abc", "pc2of6-abc"]
    first = runs[0]
    assert first["status"]["prs_done"] == 2024                    # the newest upload stands for the PC
    assert first["versions"] == ["6.12.5/abc", "6.12.1/abc"]      # the older run is still reachable
    assert len(ix.runs()) == 3                                    # nothing is deleted




def test_a_restart_does_not_count_the_checkpoint_as_mined(tmp_path):
    import time
    ix = index.Index(str(tmp_path / "i.sqlite"))
    now = time.time()
    for ago, reason, prs in ((7200, "started", 0), (7190, "repo started", 2000),      # first run: 2,000 PRs checkpointed in time
                             (3600, "stopped", 2000),
                             (1800, "started", 0), (1790, "repo started", 2000),     # restart: the checkpoint is counted in at once
                             (900, "heartbeat", 2100), (60, "heartbeat", 2250)):    # 250 PRs mined since
        ix.db.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?)", ("pc1", "6.12.5/abc", now - ago, reason, prs, 0, "o/a"))
    ix.db.commit()
    svc = store_app.Service.__new__(store_app.Service)
    svc.index = ix
    d = svc.activity(1)
    assert d["pcs"][0]["prs"] == 250

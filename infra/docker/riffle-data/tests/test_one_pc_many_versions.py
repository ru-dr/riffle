"""A PC that restarted on a newer miner version is still one PC in the store's run list."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

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

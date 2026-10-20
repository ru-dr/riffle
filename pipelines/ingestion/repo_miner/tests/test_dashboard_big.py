"""The web view's big-picture mode is on the page and its script parses."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard  # noqa: E402

IDS = ("big", "bg_word", "bg_clock", "bg_pct", "bg_bar", "bg_cpu", "bg_mem", "bg_prs",
       "bg_eta", "bg_elapsed", "bg_rate", "bg_repo", "bg_repos", "bg_alert", "bg_log", "bg_seen", "b_big", "bg_ver", "bg_hint", "bg_now_lbl")


def test_big_picture_elements_are_on_the_page():
    for i in IDS:
        assert f'id="{i}"' in dashboard.PAGE, i
    assert "#big" in dashboard.PAGE and "wakeLock" in dashboard.PAGE
    assert "(max-width:899px)" in dashboard.PAGE           # the portrait phone layout
    assert "#b_big{display:none}" not in dashboard.PAGE    # no longer hidden on phones
    assert "bigAllowed" not in dashboard.PAGE
    assert dashboard.PAGE.count("<svg class=\"lockup") == 2     # header lockup and the big picture's icon
    mark = dashboard.MARK_SVG
    assert 0 < mark.count("<path") < dashboard.LOCKUP_SVG.count("<path")           # icon only, no wordmark


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_script_parses():
    js = dashboard.PAGE.split("<script>")[1].split("</script>")[0]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
    p = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
    os.unlink(f.name)
    assert p.returncode == 0, p.stderr

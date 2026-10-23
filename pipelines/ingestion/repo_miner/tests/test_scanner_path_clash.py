"""A PR whose changed paths clash in one exported tree (a file `icons` and a file `icons/a.svg`) is still mined."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features import scanners  # noqa: E402


def test_a_path_that_is_a_file_and_a_directory_does_not_fail_the_scan(monkeypatch):
    monkeypatch.setattr(scanners, "_have", lambda tool: True)
    monkeypatch.setattr(scanners.gitio, "file_content_at", lambda repo, sha, path: "x = 1\n")

    class Done:
        returncode = 0
        stdout = "{}"
    seen = {}

    def fake_run(cmd, **kw):
        seen["tree"] = sorted(os.path.relpath(os.path.join(d, f), cmd[-1])
                              for d, _, fs in os.walk(cmd[-1]) for f in fs)
        return Done()
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = scanners._semgrep_scan("repo", "sha", ["pkg/public/icons", "pkg/public/icons/a.svg", "pkg/other.py"])
    assert out == []                                       # no exception: NotADirectoryError was the bug
    assert "pkg/other.py" in seen["tree"] and len(seen["tree"]) == 2    # the clashing second path is left out

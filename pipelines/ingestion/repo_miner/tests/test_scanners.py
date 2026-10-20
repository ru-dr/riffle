"""scanners.py: the semgrep base scan is skipped when the head has no findings, with identical results."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from features import scanners  # noqa: E402


def _finding(rule, path="a.py", line="eval(x)", sev="security"):
    return {"check_id": rule, "path": path, "extra": {"lines": line, "metadata": {
        "category": sev, "confidence": "HIGH", "cwe": ["CWE-94: Code Injection"]}}}


def _reference(head, base):
    """The original algorithm: always scan both, new = head minus base."""
    seen = {scanners._finding_key(f) for f in base}
    return [f for f in head if scanners._finding_key(f) not in seen]


def _run(monkeypatch, by_sha):
    calls = []
    monkeypatch.setattr(scanners, "_semgrep_scan", lambda repo, sha, changed: calls.append(sha) or by_sha[sha])
    out = scanners._semgrep_new("r", "base", "head", ["a.py"], config.Settings())
    return out, calls


def test_clean_head_skips_the_base_scan(monkeypatch):
    out, calls = _run(monkeypatch, {"head": [], "base": [_finding("r1")]})
    assert calls == ["head"]
    assert out == {"semgrep_findings_new": 0, "semgrep_high_conf_security_new": 0, "semgrep_cwe_top25_new": False}


def test_head_findings_are_compared_with_the_base(monkeypatch):
    head, base = [_finding("r1"), _finding("r2", line="os.system(x)")], [_finding("r1")]
    out, calls = _run(monkeypatch, {"head": head, "base": base})
    assert calls == ["head", "base"]
    assert out["semgrep_findings_new"] == len(_reference(head, base)) == 1
    assert out["semgrep_high_conf_security_new"] == 1 and out["semgrep_cwe_top25_new"] is True


def test_results_match_the_original_algorithm_on_many_cases(monkeypatch):
    import itertools
    pool = [_finding(f"r{i}", line=f"l{i}") for i in range(3)]
    for h, b in itertools.product(range(8), range(8)):
        head = [f for i, f in enumerate(pool) if h >> i & 1]
        base = [f for i, f in enumerate(pool) if b >> i & 1]
        out, _ = _run(monkeypatch, {"head": head, "base": base})
        assert out["semgrep_findings_new"] == len(_reference(head, base)), (h, b)


# ---- scan paths ------------------------------------------------------------------

import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

import gitio  # noqa: E402


def _fake_run(monkeypatch, calls):
    """Semgrep that reports one eval() finding in every file it is given."""
    def run(cmd, **kw):
        calls.append(cmd)
        root = cmd[-1]
        res = [{"check_id": "r1", "path": os.path.join(root, n), "extra": {"lines": "eval(x)", "metadata": {}}}
               for n in os.listdir(root)]
        return subprocess.CompletedProcess(cmd, 1, stdout=__import__("json").dumps({"results": res}), stderr="")
    monkeypatch.setattr(scanners, "_have", lambda tool: True)
    monkeypatch.setattr(scanners.subprocess, "run", run)
    monkeypatch.setattr(gitio, "file_content_at", lambda repo, sha, path: None if path.startswith("gone") else "x\n")


def test_finding_paths_are_relative_so_base_and_head_compare_equal(monkeypatch):
    """The old code scanned base and head in different temp directories, so no finding ever matched
    and every finding in the changed files counted as new."""
    calls = []
    _fake_run(monkeypatch, calls)
    assert [f["path"] for f in scanners._semgrep_scan("r", "head", ["t.py"])] == ["t.py"]
    out = scanners._semgrep_new("r", "base", "head", ["t.py"], config.Settings())
    assert out["semgrep_findings_new"] == 0 and len(calls) == 3      # one direct scan, then head and base


def test_nothing_to_scan_never_starts_semgrep(monkeypatch):
    calls = []
    _fake_run(monkeypatch, calls)
    assert scanners._semgrep_scan("r", "sha", ["gone.py"]) == [] and calls == []


@pytest.mark.skipif(shutil.which("semgrep") is None or not os.environ.get("RIFFLE_SEMGREP_IT"),
                    reason="needs semgrep and network; set RIFFLE_SEMGREP_IT=1")
def test_a_finding_that_was_already_there_is_not_new(tmp_path):
    repo = str(tmp_path)
    run = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)   # noqa: E731
    run("init", "-q"); run("config", "user.email", "a@b"); run("config", "user.name", "t")
    (tmp_path / "t.py").write_text("import os\ndef f(x):\n    return eval(x)\n")
    run("add", "."); run("commit", "-q", "-m", "one")
    (tmp_path / "t.py").write_text("import os\n# a comment\ndef f(x):\n    return eval(x)\n")
    run("commit", "-qam", "two")
    assert scanners._semgrep_new(repo, "HEAD~1", "HEAD", ["t.py"], config.Settings())["semgrep_findings_new"] == 0

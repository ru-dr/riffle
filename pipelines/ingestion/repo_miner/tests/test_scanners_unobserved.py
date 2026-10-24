"""scanners.py / api_contract.py: failures are null, findings compare as multisets, exports are contained."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import gitio  # noqa: E402
from features import api_contract, scanners  # noqa: E402

NULLS = {"semgrep_findings_new": None, "semgrep_high_conf_security_new": None, "semgrep_cwe_top25_new": None}


def _f(rule="r1", line="eval(x)", path="a.py"):
    return {"check_id": rule, "path": path, "extra": {"lines": line, "metadata": {}}}


def _by_sha(monkeypatch, by_sha):
    monkeypatch.setattr(scanners, "_semgrep_scan", lambda repo, sha, changed, renames=None: by_sha[sha])


def test_failed_head_or_base_scan_is_null(monkeypatch):
    st = config.Settings()
    _by_sha(monkeypatch, {"head": None, "base": []})
    assert scanners._semgrep_new("r", "base", "head", ["a.py"], st) == NULLS
    _by_sha(monkeypatch, {"head": [_f()], "base": None})
    assert scanners._semgrep_new("r", "base", "head", ["a.py"], st) == NULLS


def test_duplicate_findings_count_as_a_multiset(monkeypatch):
    _by_sha(monkeypatch, {"head": [_f(), _f(), _f()], "base": [_f()]})
    assert scanners._semgrep_new("r", "base", "head", ["a.py"], config.Settings())["semgrep_findings_new"] == 2


def test_finding_key_ignores_line_numbers_and_whitespace_but_not_the_code():
    a = {**_f(line="eval( x )"), "start": {"line": 3}}
    b = {**_f(line="eval(  x )"), "start": {"line": 40}}
    assert scanners._finding_key(a) == scanners._finding_key(b)
    assert scanners._finding_key(a) != scanners._finding_key(_f(line="eval(y)"))


def test_requires_login_lines_are_read_from_the_exported_file():
    f = {"check_id": "r", "path": "a.py", "start": {"line": 2}, "end": {"line": 2},
         "extra": {"lines": "requires login"}, "_text": "a = 1\neval(x)\n"}
    assert scanners._finding_text(f) == "eval(x)"


def _patch_run(monkeypatch, rc=1, out=None, seen=None):
    monkeypatch.setattr(scanners, "_have", lambda t: True)
    monkeypatch.setattr(scanners.gitio, "file_content_at", lambda repo, sha, path: "x = 1\n")

    def run(cmd, **kw):
        if seen is not None:
            seen["cmd"] = cmd
            seen["tree"] = sorted(os.path.relpath(os.path.join(d, f), cmd[-1])
                                  for d, _, fs in os.walk(cmd[-1]) for f in fs)
        return subprocess.CompletedProcess(cmd, rc, stdout=out if out is not None else "{}", stderr="")
    monkeypatch.setattr(scanners.subprocess, "run", run)


def test_scan_failures_are_none_not_empty(monkeypatch):
    _patch_run(monkeypatch, rc=2)
    assert scanners._semgrep_scan("r", "s", ["a.py"]) is None
    _patch_run(monkeypatch, rc=0, out="not json")
    assert scanners._semgrep_scan("r", "s", ["a.py"]) is None
    monkeypatch.setattr(scanners, "_have", lambda t: False)
    assert scanners._semgrep_scan("r", "s", ["a.py"]) is None
    _patch_run(monkeypatch, rc=0, out="{}")
    assert scanners._semgrep_scan("r", "s", ["a.py"]) == []


def test_ignore_files_and_escaping_paths_are_not_exported(monkeypatch):
    seen = {}
    _patch_run(monkeypatch, seen=seen)
    scanners._semgrep_scan("r", "s", [".semgrepignore", "pkg/.gitignore", "../evil.py", "/abs.py", "ok.py"])
    assert seen["tree"] == ["ok.py"]
    assert "--metrics=off" in seen["cmd"] and "auto" in seen["cmd"]


def test_local_rules_dir_pins_the_config(monkeypatch, tmp_path):
    monkeypatch.setenv("RIFFLE_SEMGREP_RULES", str(tmp_path))
    seen = {}
    _patch_run(monkeypatch, seen=seen)
    scanners._semgrep_scan("r", "s", ["ok.py"])
    assert str(tmp_path) in seen["cmd"] and "auto" not in seen["cmd"]


def test_symlink_in_tree_cannot_redirect_a_write(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    assert not scanners._contained(str(tmp_path / "w"), str(outside / "f"))
    work = tmp_path / "w"
    work.mkdir()
    (work / "link").symlink_to(outside)
    assert not scanners._contained(str(work), str(work / "link" / "f.py"))
    assert scanners._contained(str(work), str(work / "d" / "f.py"))


def test_renamed_file_reads_base_at_the_old_path(monkeypatch):
    calls = []
    monkeypatch.setattr(scanners, "_have", lambda t: True)

    def content(repo, sha, path):
        calls.append((sha, path))
        return "x\n"
    monkeypatch.setattr(scanners.gitio, "file_content_at", content)
    monkeypatch.setattr(scanners.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr=""))
    scanners._semgrep_scan("r", "base", ["new.py"], {"new.py": "old.py"})
    assert calls == [("base", "old.py")]


def test_lizard_counts_deleted_functions_and_renames(monkeypatch):
    pytest.importorskip("lizard")
    src = {("base", "old.py"): "def a():\n    if x:\n        pass\n\ndef b():\n    pass\n",
           ("head", "new.py"): "def b():\n    pass\n"}
    monkeypatch.setattr(scanners.gitio, "file_content_at", lambda r, sha, p: src.get((sha, p)))
    out = scanners._lizard_delta("r", "base", "head", ["new.py"], {"new.py": "old.py"})
    assert out["n_funcs_modified"] == 1 and out["ccn_delta"] == -2.0


# ---- api_contract -----------------------------------------------------------------

def _contract(monkeypatch, verdicts, files=None):
    """verdicts: {path: verdict} returned by the diff tool for each spec."""
    files = files or {}
    monkeypatch.setattr(api_contract.deps_cve, "_read_at",
                        lambda repo, sha, path: (files.get((sha, path), "x"), True))
    monkeypatch.setattr(api_contract, "_diff_one", lambda kind, b, h, path, st: verdicts[path])
    return api_contract.compute("r", "b", "h", list(verdicts), config.Settings())["api_contract_change"]


def test_an_unobserved_spec_is_not_hidden_by_an_additive_one(monkeypatch):
    assert _contract(monkeypatch, {"a.proto": "additive", "b.proto": None}) is None
    assert _contract(monkeypatch, {"a.proto": "additive", "b.proto": "breaking"}) == "breaking"
    assert _contract(monkeypatch, {"a.proto": None, "b.proto": "breaking"}) == "breaking"
    assert _contract(monkeypatch, {"a.proto": "additive"}) == "additive"


def test_unreadable_git_is_not_a_removed_spec(monkeypatch):
    monkeypatch.setattr(api_contract.deps_cve, "_read_at", lambda r, s, p: (None, False))
    assert api_contract.compute("r", "b", "h", ["a.proto"], config.Settings())["api_contract_change"] is None


def _fake_tool(monkeypatch, rc, out, err=""):
    monkeypatch.setattr(api_contract, "which", lambda t: "/bin/" + t)
    monkeypatch.setattr(api_contract.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err))


def test_oasdiff_failure_is_not_a_verdict(monkeypatch):
    _fake_tool(monkeypatch, 102, "", "Error: failed to load base spec")
    assert api_contract._oasdiff("a", "b", "x.yaml") is None
    _fake_tool(monkeypatch, 0, "not json")
    assert api_contract._oasdiff("a", "b", "x.yaml") is None
    _fake_tool(monkeypatch, 0, "[]")
    assert api_contract._oasdiff("a", "b", "x.yaml") == "additive"
    _fake_tool(monkeypatch, 0, json.dumps([{"id": "x"}]))
    assert api_contract._oasdiff("a", "b", "x.yaml") == "breaking"


def test_buf_distinguishes_breaking_from_compile_errors(monkeypatch):
    line = lambda t: json.dumps({"path": "m.proto", "type": t, "message": "removed the word deleted"})
    _fake_tool(monkeypatch, 100, line("FIELD_SAME_TYPE") + "\n")
    assert api_contract._buf_breaking("a", "b", "m.proto") == "breaking"
    _fake_tool(monkeypatch, 100, line("COMPILE") + "\n")
    assert api_contract._buf_breaking("a", "b", "m.proto") is None
    _fake_tool(monkeypatch, 1, "", "deleted removed breaking")
    assert api_contract._buf_breaking("a", "b", "m.proto") is None
    _fake_tool(monkeypatch, 0, "")
    assert api_contract._buf_breaking("a", "b", "m.proto") == "additive"

"""deps_cve.py: point-in-time advisories, unobserved manifests, version parsing and GHSA replies."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from features import deps_cve  # noqa: E402

PR_OPEN = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _vuln(vid, published, fixed=True, withdrawn=None):
    v = {"id": vid, "published": published,
         "affected": [{"ranges": [{"events": [{"introduced": "0"}] + ([{"fixed": "9.9.9"}] if fixed else [])}]}]}
    if withdrawn:
        v["withdrawn"] = withdrawn
    return v


def _run(monkeypatch, files, osv, as_of=None, renames=None, st=None):
    """files: {(sha, path): text}; osv: {(eco, name, ver): [records]}."""
    monkeypatch.setattr(deps_cve.gitio, "file_content_at", lambda repo, sha, path: files.get((sha, path)))
    monkeypatch.setattr(deps_cve, "_read_at",
                        lambda repo, sha, path: (files.get((sha, path)), True))
    monkeypatch.setattr(deps_cve, "_osv_query_all", lambda pts, st: ({p: osv.get(p, []) for p in pts}, set()))
    st = st or config.Settings()
    changed = sorted({p for _, p in files})
    return deps_cve.compute("r", "base", "head", changed, st, as_of=as_of, renames=renames)


PKG_B = json.dumps({"dependencies": {"left": "1.0.0"}})
PKG_H = json.dumps({"dependencies": {"left": "1.1.0"}})
FILES = {("base", "package.json"): PKG_B, ("head", "package.json"): PKG_H}


def test_without_as_of_nothing_is_filtered(monkeypatch):
    osv = {("npm", "left", "1.1.0"): [_vuln("A", "2030-01-01T00:00:00Z")]}
    out = _run(monkeypatch, FILES, osv)
    assert out["osv_vulns_introduced"] == 1 and out["vuln_has_fix_available"] is True


def test_advisories_published_after_the_pr_do_not_count(monkeypatch):
    osv = {("npm", "left", "1.1.0"): [_vuln("A", "2024-07-01T00:00:00Z"), _vuln("B", "2023-01-01T00:00:00Z", fixed=False)],
           ("npm", "left", "1.0.0"): [_vuln("C", "2025-01-01T00:00:00Z")]}
    out = _run(monkeypatch, FILES, osv, as_of=PR_OPEN)
    assert out["osv_vulns_introduced"] == 1          # B only
    assert out["osv_vulns_fixed"] == 0               # C did not exist yet
    assert out["vuln_has_fix_available"] is False    # B has no fix at all


def test_fix_availability_is_unknown_when_it_cannot_be_dated(monkeypatch):
    osv = {("npm", "left", "1.1.0"): [_vuln("B", "2023-01-01T00:00:00Z", fixed=True)]}
    out = _run(monkeypatch, FILES, osv, as_of=PR_OPEN)
    assert out["osv_vulns_introduced"] == 1 and out["vuln_has_fix_available"] is None


def test_withdrawn_and_undatable_advisories(monkeypatch):
    osv = {("npm", "left", "1.1.0"): [_vuln("W", "2023-01-01T00:00:00Z", withdrawn="2023-02-01T00:00:00Z")]}
    assert _run(monkeypatch, FILES, osv, as_of=PR_OPEN)["osv_vulns_introduced"] == 0
    osv = {("npm", "left", "1.1.0"): [{"id": "U", "affected": []}]}
    out = _run(monkeypatch, FILES, osv, as_of=PR_OPEN)
    assert out["osv_vulns_introduced"] is None and out["n_deps_upgraded"] == 1


def test_unreadable_manifest_is_null_not_zero(monkeypatch):
    files = {("base", "pyproject.toml"): "[project]\n", ("head", "pyproject.toml"): "[project]\n"}
    out = _run(monkeypatch, files, {})
    assert out["n_deps_added"] is None and out["osv_vulns_introduced"] is None
    files = {("base", "package.json"): "{", ("head", "package.json"): PKG_H}
    assert _run(monkeypatch, files, {})["n_deps_upgraded"] is None


def test_git_read_failure_is_not_an_empty_manifest(monkeypatch):
    monkeypatch.setattr(deps_cve.gitio, "file_content_at", lambda *a: None)
    monkeypatch.setattr(deps_cve, "_osv_query_all", lambda pts, st: ({}, set()))
    out = deps_cve.compute("/nonexistent-repo", "base", "head", ["package.json"], config.Settings())
    assert out["n_deps_added"] is None


def test_renamed_manifest_is_read_at_its_old_path(monkeypatch):
    files = {("base", "old/package.json"): PKG_B, ("head", "new/package.json"): PKG_H}
    out = _run(monkeypatch, files, {}, renames={"new/package.json": "old/package.json"})
    assert out["n_deps_upgraded"] == 1 and out["n_deps_added"] == 0


def test_workspaces_with_the_same_dependency_both_count(monkeypatch):
    files = {}
    for d in ("a", "b"):
        files[("base", f"{d}/package.json")] = PKG_B
        files[("head", f"{d}/package.json")] = PKG_H
    assert _run(monkeypatch, files, {})["n_deps_upgraded"] == 2


def test_lockfile_beats_manifest_range_in_one_directory(monkeypatch):
    lock = lambda v: json.dumps({"lockfileVersion": 3, "packages": {"": {}, "node_modules/left": {"version": v}}})
    files = {("base", "package.json"): PKG_B, ("head", "package.json"): PKG_H,
             ("base", "package-lock.json"): lock("1.0.5"), ("head", "package-lock.json"): lock("1.1.5")}
    out = _run(monkeypatch, files, {})
    assert out["n_deps_upgraded"] == 1


def test_package_json_specs_that_are_not_a_version_are_unresolved():
    p = deps_cve._parse_file(json.dumps({"dependencies": {
        "a": "^1.2.3", "b": ">=1.0.0 <2.0.0", "c": "workspace:*", "d": "git+https://x/y.git", "e": "1.x"}}),
        "npm", "package.json")
    assert p == {"a": "1.2.3", "b": None, "c": None, "d": None, "e": None}


def test_lockfile_formats():
    v1 = json.dumps({"lockfileVersion": 1, "dependencies": {"x": {"version": "1.0.0", "dependencies": {"y": {"version": "2.0.0"}}},
                                                           "z": {"version": "github:a/b#abc"}}})
    assert deps_cve._parse_file(v1, "npm", "package-lock.json") == {"x": "1.0.0", "y": "2.0.0", "z": None}
    yarn = 'lodash@^4.17.0, lodash@^4.17.1:\n  version "4.17.21"\n\n"@s/p@^1":\n  version "1.2.3"\n'
    assert deps_cve._parse_file(yarn, "npm", "yarn.lock") == {"lodash": "4.17.21", "@s/p": "1.2.3"}
    assert deps_cve._parse_file("x", "npm", "pnpm-lock.yaml") is None
    poetry = '[[package]]\nname = "Foo_Bar"\nversion = "1.2.3"\n'
    assert deps_cve._parse_file(poetry, "PyPI", "poetry.lock") == {"foo-bar": "1.2.3"}


def test_multi_version_lock_keeps_the_highest_regardless_of_order():
    a = '[[package]]\nname = "x"\nversion = "1.0.0"\n[[package]]\nname = "x"\nversion = "2.0.0"\n'
    b = '[[package]]\nname = "x"\nversion = "2.0.0"\n[[package]]\nname = "x"\nversion = "1.0.0"\n'
    assert deps_cve._parse_file(a, "crates.io", "Cargo.lock") == deps_cve._parse_file(b, "crates.io", "Cargo.lock") == {"x": "2.0.0"}


def test_pypi_names_are_normalised_and_extras_read():
    t = "Foo_Bar[extra]==1.0\nzope.interface==5.0 ; python_version>'3'\n"
    assert deps_cve._parse_file(t, "PyPI", "requirements.txt") == {"foo-bar": "1.0", "zope-interface": "5.0"}
    assert deps_cve._ecosystem_of("requirements-dev.txt") == "PyPI"


def test_go_files():
    gomod = "module m\n\ngo 1.21\n\nrequire (\n\ta.com/x v1.2.3 // indirect\n)\nrequire b.com/y v0.1.0\nreplace c => d v1\n"
    assert deps_cve._parse_file(gomod, "Go", "go.mod") == {"a.com/x": "1.2.3", "b.com/y": "0.1.0"}
    gosum = "a.com/x v1.2.3 h1:aaa=\na.com/x v1.2.3/go.mod h1:bbb=\n"
    assert deps_cve._parse_file(gosum, "Go", "go.sum") == {"a.com/x": "1.2.3"}


def test_version_comparison_sees_prereleases_and_four_parts():
    k = deps_cve._vkey
    assert k("1.0.0-rc1") < k("1.0.0") and k("1.2.3.4") > k("1.2.3") and k("1.0") == k("1.0.0")
    assert k("1.0.0-alpha") < k("1.0.0-beta") and k("1.0.0-rc.2") < k("1.0.0-rc.10")
    assert deps_cve._version_in_range("1.0.0-rc1", "< 1.0.0") is True


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ghsa(monkeypatch, body, when=None):
    monkeypatch.setattr(deps_cve.urllib.request, "urlopen", lambda req, timeout=0: _Resp(body))
    st = config.Settings()
    st.github_token = "t"
    return deps_cve._ghsa_vulnerable("npm", "left", "1.1.0", st, when)


def _node(rng, pub="2023-01-01T00:00:00Z", wd=None):
    return {"vulnerableVersionRange": rng, "advisory": {"publishedAt": pub, "withdrawnAt": wd}}


def test_ghsa_unreadable_replies_are_unobserved(monkeypatch):
    ok = {"data": {"securityVulnerabilities": {"pageInfo": {"hasNextPage": False}, "nodes": [_node(">= 1.0.0, < 2.0.0")]}}}
    assert _ghsa(monkeypatch, ok) is True
    assert _ghsa(monkeypatch, {"errors": [{"message": "x"}]}) is None
    assert _ghsa(monkeypatch, {"data": {"securityVulnerabilities": {"pageInfo": {"hasNextPage": True}, "nodes": []}}}) is None
    assert _ghsa(monkeypatch, {"data": {"securityVulnerabilities": {"pageInfo": {}, "nodes": [_node("weird")]}}}) is None
    assert _ghsa(monkeypatch, {"data": {"securityVulnerabilities": {"pageInfo": {}, "nodes": []}}}) is False


def test_ghsa_respects_as_of(monkeypatch):
    late = {"data": {"securityVulnerabilities": {"pageInfo": {}, "nodes": [_node("< 2.0.0", pub="2025-01-01T00:00:00Z")]}}}
    assert _ghsa(monkeypatch, late, PR_OPEN) is False
    assert _ghsa(monkeypatch, late) is True

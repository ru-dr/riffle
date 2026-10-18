"""
scanners.py — Source 4d static analysis on the diff.

lizard: cyclomatic complexity of changed functions, base vs head (ccn_delta,
        max_ccn_changed_func, n_funcs_modified).
semgrep: new findings introduced by the PR (diff-new = head findings minus base
         findings on changed files), by severity, plus a high-confidence
         security count and a Top-25 CWE flag.

Both are optional (Settings toggles). If a tool isn't installed the features
come back as None so the model treats them as missing rather than zero.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import os

import config
import gitio


def compute(repo: str, base: str, head: str, changed: list[str],
            st: config.Settings) -> dict:
    out: dict = {}
    if st.enable_scanners:
        out.update(_lizard_delta(repo, base, head, changed))
    else:
        out.update({"ccn_delta": None, "max_ccn_changed_func": None,
                    "n_funcs_modified": None})
    if st.enable_scanners and st.enable_semgrep:
        out.update(_semgrep_new(repo, base, head, changed, st))
    else:
        out.update({
            "semgrep_findings_new": None, "semgrep_high_conf_security_new": None,
            "semgrep_cwe_top25_new": None,
        })
    return out


def _lizard_delta(repo: str, base: str, head: str, changed: list[str]) -> dict:
    try:
        import lizard
    except Exception:
        return {"ccn_delta": None, "max_ccn_changed_func": None,
                "n_funcs_modified": None}

    total_delta = 0.0
    max_ccn = 0
    n_funcs = 0
    for path in changed:
        lang = config.lang_of(path)
        if lang is None:
            continue
        base_src = gitio.file_content_at(repo, base, path) or ""
        head_src = gitio.file_content_at(repo, head, path) or ""
        try:
            b = lizard.analyze_file.analyze_source_code(path, base_src)
            h = lizard.analyze_file.analyze_source_code(path, head_src)
        except Exception:
            continue
        b_by = {f.long_name: f.cyclomatic_complexity for f in b.function_list}
        h_by = {f.long_name: f.cyclomatic_complexity for f in h.function_list}
        for name, ccn in h_by.items():
            prev = b_by.get(name, 0)
            if ccn != prev:
                n_funcs += 1
                total_delta += (ccn - prev)
                max_ccn = max(max_ccn, ccn)
    return {
        "ccn_delta": round(total_delta, 2),
        "max_ccn_changed_func": max_ccn,
        "n_funcs_modified": n_funcs,
    }


_TOP25_CWE = {
    "CWE-79", "CWE-787", "CWE-89", "CWE-352", "CWE-22", "CWE-125", "CWE-78",
    "CWE-416", "CWE-862", "CWE-434", "CWE-94", "CWE-20", "CWE-77", "CWE-287",
    "CWE-269", "CWE-502", "CWE-200", "CWE-863", "CWE-918", "CWE-119", "CWE-476",
    "CWE-798", "CWE-190", "CWE-400", "CWE-306",
}


def _semgrep_scan(repo: str, sha: str, changed: list[str]) -> list[dict]:
    """Run semgrep on the checked-out `sha` for the changed files."""
    if not _have("semgrep"):
        return []
    with tempfile.TemporaryDirectory() as work:
        # export the tree at sha for just the changed files
        for path in changed:
            content = gitio.file_content_at(repo, sha, path)
            if content is None:
                continue
            dest = os.path.join(work, path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8", errors="replace") as fh:
                fh.write(content)
        proc = subprocess.run(
            ["semgrep", "scan", "--config", "auto", "--json", "--quiet", work],
            capture_output=True, text=True,
        )
        if proc.returncode not in (0, 1):  # 1 = findings present
            return []
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return []
        return data.get("results", [])


def _finding_key(f: dict) -> tuple:
    return (f.get("check_id"), f.get("path"),
            (f.get("extra", {}) or {}).get("lines", "")[:80])


def _semgrep_new(repo: str, base: str, head: str, changed: list[str],
                 st: config.Settings) -> dict:
    base_f = {_finding_key(f) for f in _semgrep_scan(repo, base, changed)}
    head_r = _semgrep_scan(repo, head, changed)
    new = [f for f in head_r if _finding_key(f) not in base_f]
    high_sec = 0
    cwe25 = False
    for f in new:
        extra = f.get("extra", {}) or {}
        md = extra.get("metadata", {}) or {}
        if (md.get("category") == "security"
                and str(md.get("confidence", "")).upper() == "HIGH"):
            high_sec += 1
        for cwe in _as_list(md.get("cwe")):
            code = str(cwe).split(":")[0].strip().upper()
            if code in _TOP25_CWE:
                cwe25 = True
    return {
        "semgrep_findings_new": len(new),
        "semgrep_high_conf_security_new": high_sec,
        "semgrep_cwe_top25_new": cwe25,
    }


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _have(tool: str) -> bool:
    from shutil import which
    return which(tool) is not None

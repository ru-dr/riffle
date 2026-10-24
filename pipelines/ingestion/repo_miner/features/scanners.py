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
import os
import subprocess
import tempfile
import threading
from collections import Counter

import config
import gitio
import runlog


_NULL_CCN = {"ccn_delta": None, "max_ccn_changed_func": None, "n_funcs_modified": None}
_NULL_SEMGREP = {"semgrep_findings_new": None, "semgrep_high_conf_security_new": None,
                 "semgrep_cwe_top25_new": None}


def compute(repo: str, base: str, head: str, changed: list[str],
            st: config.Settings, renames: dict | None = None) -> dict:
    """renames: {new_path: old_path} for files the PR renamed, so the base side is read at the old
    path instead of looking like a brand-new file. None keeps every path as it is."""
    out: dict = {}
    if st.enable_scanners:
        out.update(_lizard_delta(repo, base, head, changed, renames))
    else:
        out.update(_NULL_CCN)
    if st.enable_scanners and st.enable_semgrep:
        out.update(_semgrep_new(repo, base, head, changed, st, renames))
    else:
        out.update(_NULL_SEMGREP)
    return out


def _lizard_delta(repo: str, base: str, head: str, changed: list[str],
                  renames: dict | None = None) -> dict:
    try:
        import lizard
    except Exception:
        return dict(_NULL_CCN)

    renames = renames or {}
    total_delta = 0.0
    max_ccn = 0
    n_funcs = 0
    for path in changed:
        lang = config.lang_of(path)
        if lang is None:
            continue
        base_src = gitio.file_content_at(repo, base, renames.get(path, path)) or ""
        head_src = gitio.file_content_at(repo, head, path) or ""
        try:
            b = lizard.analyze_file.analyze_source_code(path, base_src)
            h = lizard.analyze_file.analyze_source_code(path, head_src)
        except Exception:
            return dict(_NULL_CCN)      # a file lizard cannot read: the totals would undercount
        b_by = {f.long_name: f.cyclomatic_complexity for f in b.function_list}
        h_by = {f.long_name: f.cyclomatic_complexity for f in h.function_list}
        for name, ccn in h_by.items():
            prev = b_by.get(name, 0)
            if ccn != prev:
                n_funcs += 1
                total_delta += (ccn - prev)
                max_ccn = max(max_ccn, ccn)
        for name, prev in b_by.items():
            if name not in h_by:        # a deleted function is a change too: its complexity is gone
                n_funcs += 1
                total_delta -= prev
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


# files that change what semgrep scans or ignores. A PR must not be able to hide its own findings
# by editing them, so they are never exported into the scan tree.
_IGNORE_FILES = (".semgrepignore", ".gitignore", ".ignore")
_SEMGREP_SLOTS = threading.BoundedSemaphore(max(1, int(os.environ.get("RIFFLE_SEMGREP_PARALLEL", "2") or 2)))
_SEMGREP_TIMEOUT_S = 900


def _semgrep_config() -> list[str]:
    """--config for the scan. RIFFLE_SEMGREP_RULES (a local rules file or directory) pins the rule
    set and keeps the scan offline; otherwise `auto`, which fetches rules from semgrep.dev (the rule
    set then changes over time, and the fetch leaves the machine). Metrics are always off."""
    local = os.environ.get("RIFFLE_SEMGREP_RULES", "").strip()
    if local and os.path.exists(local):
        return ["--config", local]
    return ["--config", "auto"]


def _contained(work: str, dest: str) -> bool:
    root = os.path.realpath(work)
    real = os.path.realpath(dest)
    return real != root and real.startswith(root + os.sep)


def _semgrep_scan(repo: str, sha: str, changed: list[str],
                  renames: dict | None = None) -> list[dict] | None:
    """Findings for the `changed` files as they are at `sha` ([] = scanned, found none). None when
    the scan could not be done (semgrep missing, crashed, timed out or printed unreadable output):
    that is unobserved, not clean. Each finding's path is relative to the exported tree, so base
    and head findings compare equal; the temporary directories differ, and an absolute path would
    never match. `renames` is {new_path: old_path}; given, it maps a changed path to the path it
    had at `sha` when `sha` is the base (the caller passes it for the base scan only)."""
    if not _have("semgrep"):
        return None
    renames = renames or {}
    with tempfile.TemporaryDirectory() as work:
        # export the tree at sha for just the changed files
        written = 0
        texts: dict[str, str] = {}
        for path in changed:
            src = renames.get(path, path)
            if os.path.basename(path) in _IGNORE_FILES or os.path.basename(src) in _IGNORE_FILES:
                continue
            content = gitio.file_content_at(repo, sha, src)
            if content is None:
                continue
            dest = os.path.join(work, path)
            if not _contained(work, dest):
                continue                # `..`, an absolute path or a symlink out of the tree
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8", errors="replace") as fh:
                    fh.write(content)
            except OSError:
                # a path that is a file here and a directory there (a symlink `icons` beside `icons/a.svg`)
                # cannot be written to one tree: leave that file out rather than fail the whole PR
                continue
            texts[path] = content
            written += 1
        if not written:
            return []          # nothing at this sha (files deleted or added on the other side): nothing to scan
        # one semgrep start costs about 4 s (rules fetched and loaded for `auto`), so it is the PR's
        # biggest cost; the span shows up in the trace as "semgrep". Batching several PRs into one
        # process was tried and measured no faster: starts already run in parallel across cores.
        cmd = ["semgrep", "scan", *_semgrep_config(), "--metrics=off", "--disable-version-check",
               "--jobs", "1", "--json", "--quiet", work]
        try:
            with _SEMGREP_SLOTS:        # several workers each starting semgrep can exhaust RAM
                with runlog.span("semgrep", "scan", files=written, slow_s=runlog.SLOW_CALL_S):
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                          timeout=_SEMGREP_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode not in (0, 1):  # 1 = findings present
            return None
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        out = []
        for f in data.get("results", []):
            rel = os.path.relpath(f.get("path", ""), work)
            out.append({**f, "path": rel, "_text": texts.get(rel)})
        return out


def _finding_text(f: dict) -> str:
    """The matched source, whitespace-normalised. Semgrep prints "requires login" instead of the
    lines when it is not logged in, so then the lines are cut from the exported file."""
    extra = f.get("extra", {}) or {}
    lines = extra.get("lines") or ""
    if not lines or lines.strip().lower() == "requires login":
        src = f.get("_text")
        start, end = (f.get("start") or {}).get("line"), (f.get("end") or {}).get("line")
        if src is not None and isinstance(start, int) and isinstance(end, int):
            lines = "\n".join(src.splitlines()[start - 1:end])
        else:
            lines = ""
    return " ".join(lines.split())


def _finding_key(f: dict) -> tuple:
    """Same rule, file and matched code. The line number is left out: it moves when code is added
    above, and a moved finding is not a new one."""
    return (f.get("check_id"), f.get("path"), _finding_text(f))


def _semgrep_new(repo: str, base: str, head: str, changed: list[str],
                 st: config.Settings, renames: dict | None = None) -> dict:
    # new = head findings that the base did not have, so a head with none needs no base scan.
    # Most PRs have none (80% of the vite pilot), which halves the semgrep starts.
    head_r = _semgrep_scan(repo, head, changed)
    if head_r is None:
        return dict(_NULL_SEMGREP)
    if head_r:
        base_r = _semgrep_scan(repo, base, changed, renames) if renames else _semgrep_scan(repo, base, changed)
        if base_r is None:
            return dict(_NULL_SEMGREP)  # without the base every head finding would look new
        # a multiset: three identical findings at head against one at base are two new, not none
        remaining = Counter(_finding_key(f) for f in base_r)
        new = []
        for f in head_r:
            k = _finding_key(f)
            if remaining[k] > 0:
                remaining[k] -= 1
            else:
                new.append(f)
    else:
        new = []
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

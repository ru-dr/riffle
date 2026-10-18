"""
diff_stats.py — Source 4a/4b + the diff-derived Kamei metrics (Source 2a,
the per-PR ones).  All computed from the base...head diff; no history lookup.
"""
from __future__ import annotations

import math
import os
from collections import Counter

import config
import gitio


def _entropy(per_file_changes: list[int]) -> float:
    total = sum(per_file_changes)
    if total <= 0 or len(per_file_changes) <= 1:
        return 0.0
    ent = 0.0
    for c in per_file_changes:
        if c <= 0:
            continue
        p = c / total
        ent -= p * math.log2(p)
    return ent / math.log2(len(per_file_changes))  # normalized 0..1


def compute(repo: str, base: str, head: str, st: config.Settings) -> dict:
    rows = gitio.diff_numstat(repo, base, head)
    # filter generated/vendored out of risk-relevant counts, but keep a frac
    kept, gen_lines = [], 0
    total_lines = 0
    for a, d, path in rows:
        flags = config.path_flags(path)
        total_lines += a + d
        if flags["is_generated"]:
            gen_lines += a + d
        else:
            kept.append((a, d, path))

    la = sum(a for a, _, _ in kept)
    ld = sum(d for _, d, _ in kept)
    files = [p for _, _, p in kept]
    dirs = {os.path.dirname(p) for p in files}
    subsystems = {config.subsystem_of(p) for p in files}
    per_file_total = [a + d for a, d, _ in kept]

    test_lines = sum(a + d for a, d, p in kept if config.path_flags(p)["is_test"])
    langs = Counter(config.lang_of(p) for p in files if config.lang_of(p))

    out = {
        # Kamei diffusion/size (per-PR)
        "LA": la,
        "LD": ld,
        "NF": len(files),
        "ND": len(dirs),
        "NS": len(subsystems),
        "Entropy": round(_entropy(per_file_total), 4),
        # diff shape (4a)
        "frac_generated_lines": round(gen_lines / total_lines, 4) if total_lines else 0.0,
        "frac_test_lines": round(test_lines / (la + ld), 4) if (la + ld) else 0.0,
        "lang_mix": len(langs),
        "n_files_added": 0, "n_files_removed": 0, "n_files_renamed": 0,
        # ground truth: git marks binary / too-large files with "-\t-" in
        # numstat (no textual diff). Count those raw markers directly — not the
        # coerced numbers the rest of the pipeline sees (where "-" became 0),
        # and not "0\t0" rows, which are real zero-change renames/mode changes.
        "patch_missing_files": _count_undiffable(repo, base, head),
    }

    # file status counts + missing patches
    status = gitio._run(
        repo, "diff", "--name-status", "-M", f"{base}...{head}", check=False
    )
    for line in status.splitlines():
        if not line.strip():
            continue
        code = line.split("\t")[0]
        if code.startswith("A"):
            out["n_files_added"] += 1
        elif code.startswith("D"):
            out["n_files_removed"] += 1
        elif code.startswith("R"):
            out["n_files_renamed"] += 1

    # high-risk change kinds (4b) — any() across changed files
    agg_flags = {k: False for k in config.PATH_RULES}
    for p in files:
        pf = config.path_flags(p)
        for k in config.PATH_RULES:
            agg_flags[k] = agg_flags[k] or pf[k]
    out.update(agg_flags)
    out["test_only"] = bool(files) and all(config.path_flags(p)["is_test"] for p in files)
    out["docs_only"] = bool(files) and all(config.path_flags(p)["is_doc"] for p in files)

    # la_norm / ld_norm need LT (base size of modified files) — filled by history
    return out


def _count_undiffable(repo: str, base: str, head: str) -> int:
    """
    Count files git couldn't produce a textual diff for (binary or too large).
    git's numstat prints a literal '-' in both the added and deleted columns
    for exactly these files, so we read the raw numstat and count rows whose
    first two tab-separated fields are both '-'. This is git's own ground-truth
    marker; '0\\t0' rows (pure renames / mode changes) are NOT counted.
    """
    out = gitio._run(repo, "diff", "--numstat", "-M", f"{base}...{head}",
                     check=False)
    count = 0
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and cols[0] == "-" and cols[1] == "-":
            count += 1
    return count

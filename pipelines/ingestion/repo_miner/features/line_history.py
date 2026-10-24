"""
line_history.py — Source 3, line-level blame history.

For each changed file, blame the exact lines this PR deletes/modifies (as of
the base commit) and derive: age of those lines, how many distinct people
wrote them, the author's own share, and how close past bug-fixes came to them.
These are the only history features anchored to specific lines, so they also
drive inline tags. Novel as inputs — validate with an ablation before claiming
lift.
"""
from __future__ import annotations

from datetime import datetime

import config
import gitio


def _age_days(base_time: datetime, t: datetime) -> int:
    """Days from a line's author time to the base commit's time. Never negative:
    an author time can be later than the base's committer time (a rebased or
    clock-skewed commit), and a line cannot be younger than its base."""
    return max(0, (base_time - t).days)


def per_file_line_features(repo: str, base: str, head: str, path: str,
                           author_email: str, idx: dict,
                           st: config.Settings, old_path: str | None = None) -> dict:
    """Line-level history of one changed file. `old_path` is its name at base
    when the PR renames it (gitio.rename_map): the modified lines and the blame
    are both of the base-side file, which only has that name."""
    lp = old_path or path
    base_time = idx["base_time"]
    mod_lines = gitio.deleted_or_modified_lines(repo, base, head, path, old_path)
    blame = gitio.blame_file(repo, base, lp)
    if not mod_lines or not blame:
        return {
            "mod_line_age_days_min": None, "mod_line_age_days_median": None,
            "mod_lines_distinct_authors": 0, "mod_lines_self_authored_share": 0.0,
            "mod_lines_prior_fix_share": 0.0, "hunk_fix_proximity": 0.0,
        }

    ages, authors, self_hits, fix_hits = [], set(), 0, 0
    a = author_email.lower()

    # build a set of line numbers that a past fix touched, per file
    fix_lines = _fix_touched_lines(repo, base, lp, idx, st)

    for ln in mod_lines:
        bl = blame.get(ln)
        if bl is None:
            continue
        ages.append(_age_days(base_time, bl.author_time))
        authors.add(bl.author_email)
        if bl.author_email == a:
            self_hits += 1
        # prior fix share: was this line's last-touching commit a fix?
        if _commit_is_fix(repo, bl.orig_commit, st):
            fix_hits += 1

    n = len([ln for ln in mod_lines if ln in blame])
    # hunk_fix_proximity: recency-decayed count of past fixes whose lines fall
    # within +/-k of any modified line
    prox = 0.0
    if fix_lines:
        k = st.hunk_proximity_k
        mod_set = set(mod_lines)
        for fl, weight in fix_lines.items():
            if any(abs(fl - ml) <= k for ml in mod_set):
                prox += weight

    import statistics
    return {
        "mod_line_age_days_min": min(ages) if ages else None,
        "mod_line_age_days_median": statistics.median(ages) if ages else None,
        "mod_lines_distinct_authors": len(authors),
        "mod_lines_self_authored_share": round(self_hits / n, 4) if n else 0.0,
        "mod_lines_prior_fix_share": round(fix_hits / n, 4) if n else 0.0,
        "hunk_fix_proximity": round(prox, 4),
    }


_FIX_CACHE: dict[str, bool] = {}


def _commit_is_fix(repo: str, sha: str, st: config.Settings) -> bool:
    if sha in _FIX_CACHE:
        return _FIX_CACHE[sha]
    subj = gitio._run(repo, "show", "-s", "--format=%s", sha, check=False).strip()
    val = config.is_fix_text(subj)
    _FIX_CACHE[sha] = val
    return val


def _fix_touched_lines(repo: str, base: str, path: str, idx: dict,
                       st: config.Settings) -> dict[int, float]:
    """
    Map {base-version line number -> decayed weight} for lines that past
    bug-fix commits modified in this file. Approximated by blaming the file
    at base and weighting lines whose owning commit was a fix, by recency.
    """
    blame = gitio.blame_file(repo, base, path)
    base_time = idx["base_time"]
    out: dict[int, float] = {}
    for ln, bl in blame.items():
        if _commit_is_fix(repo, bl.orig_commit, st):
            age = _age_days(base_time, bl.author_time)
            out[ln] = 0.5 ** (age / st.decay_half_life_days)
    return out

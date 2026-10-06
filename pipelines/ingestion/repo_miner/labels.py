"""
labels.py — the training targets (not inference features).

For each merged PR the miner records WHEN each outcome happened, not just
whether it happened inside a fixed window:
  - days_to_revert:   days from merge to the first revert of this PR
  - days_to_hotfix:   days from merge to the first hotfix/rollback commit
                      touching the same files
  - days_to_szz_fix:  days from merge to the earliest later fix commit that
                      repaired a line this PR introduced (SZZ, needs --szz),
                      bounded by szz_maturity_days
Revert and hotfix are searched up to `label_lookahead_days` (90, the contract's
max window). A null delay means "not observed by the observation cutoff".

Booleans are kept for convenience, using the default windows:
  label_revert = days_to_revert <= revert_window_days (30)
  label_hotfix = days_to_hotfix <= hotfix_window_days (7)
  label_szz_bug = any later fix found (within maturity window)
  label_ci_fail = required CI failed on the merge commit — TRI-STATE:
                  True (failed) / False (passed) / None (no CI verdict observed).
                  None is NOT a pass; use `ci_observed` to filter.
  label_bad = revert | ci_fail(observed True) | hotfix
Observability columns let training drop or downweight immature / unobserved rows:
  ci_observed            — a required-CI verdict existed on the merge commit
  revert_window_closed   — the 30d revert window had fully elapsed by the cutoff
  hotfix_window_closed   — the 7d hotfix window had fully elapsed by the cutoff
  label_bad_observed     — every constituent of label_bad was fully observed
  observed_through       — the cutoff up to which outcomes could be seen
Each row also carries `mined_at`. Any other window is a training-time comparison
on the days_to_* columns.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import config
import gitio


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _epoch_ts(ct: str) -> datetime:
    return datetime.fromtimestamp(int(ct), tz=timezone.utc)


def _days(later: datetime, earlier: datetime) -> float:
    return round((later - earlier).total_seconds() / 86400.0, 2)


def _lookahead(st: config.Settings) -> int:
    return getattr(st, "label_lookahead_days", 90)


def revert_delay(repo: str, pr: dict, st: config.Settings) -> float | None:
    """
    Days from merge to the first commit that reverts this PR (or None).

    Matching is STRICT: the revert must reference the merge commit's SHA, or
    quote this PR's exact title in the canonical `Revert "<title>"` form. The
    old loose rule (PR title appearing anywhere in the revert body) made short
    titles like "Fix typo" or "Update README" match unrelated reverts.
    """
    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        return None
    merged_at = _parse_ts(pr["merged_at"])
    until = merged_at + timedelta(days=_lookahead(st))
    out = gitio._run(
        repo, "log", "--all", f"--since={merged_at.isoformat()}",
        f"--until={until.isoformat()}", "--pretty=%H%x1f%ct%x1f%s%n%b%x1e",
        check=False,
    )
    short = merge_sha[:7]
    title = (pr.get("title") or "").strip().lower()
    best = None
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f", 2)
        if len(parts) < 3:
            continue
        _sha, ct, msg = parts
        if not config.is_revert_text(msg):
            continue
        m = msg.lower()
        matched = (
            short in msg or merge_sha in msg
            or (title and f'revert "{title}"' in m)
        )
        if not matched:
            continue
        try:
            when = _epoch_ts(ct)
        except (ValueError, OSError, OverflowError):
            continue
        d = _days(when, merged_at)
        if d >= 0 and (best is None or d < best):
            best = d
    return best


# shared files that co-change in nearly every PR — a shared hit on one of these
# is not evidence that a later hotfix targeted THIS PR's change
def _significant_shared(files: set[str]) -> set[str]:
    out = set()
    for f in files:
        pf = config.path_flags(f)
        if pf["is_generated"] or pf["is_doc"] or pf["touches_lockfile"]:
            continue
        out.add(f)
    return out


def hotfix_delay(repo: str, pr: dict, changed: list[str],
                 st: config.Settings) -> float | None:
    """
    Days from merge to the first hotfix/rollback commit touching the same files.
    Requires at least one *significant* shared file (not just CHANGELOG, a doc,
    or a lockfile), so a hotfix that merely bumps the changelog or a lockfile
    doesn't get pinned to every PR that touched those too.
    """
    merged_at = _parse_ts(pr["merged_at"])
    until = merged_at + timedelta(days=_lookahead(st))
    out = gitio._run(
        repo, "log", "--all", f"--since={merged_at.isoformat()}",
        f"--until={until.isoformat()}", "--pretty=%H%x1f%ct%x1f%s", check=False,
    )
    changed_set = set(changed)
    best = None
    for rec in out.splitlines():
        parts = rec.split("\x1f", 2)
        if len(parts) < 3:
            continue
        sha, ct, subj = parts
        if not config.is_hotfix_text(subj):
            continue
        shared = changed_set & set(gitio.changed_files_of_commit(repo, sha))
        if not shared or not _significant_shared(shared):
            continue
        try:
            when = _epoch_ts(ct)
        except (ValueError, OSError, OverflowError):
            continue
        d = _days(when, merged_at)
        if d >= 0 and (best is None or d < best):
            best = d
    return best


def ci_fail_labels(api, pr: dict) -> tuple[str | None, str | None]:
    """(required_conclusion, optional_conclusion) for the merge commit's CI, each
    'failure' | 'success' | None. None means no verdict was observed (no CI on
    push, retention expired, unreadable) — it is NOT a pass. 'required' is the
    gating build/test; 'optional' is auxiliary checks (CodeQL, coverage, etc.).
    Only an observed required 'failure' should feed label_bad."""
    if api is None:
        return (None, None)
    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        return (None, None)
    c = api.check_conclusion(merge_sha)
    return (c.get("required"), c.get("optional"))


def build_szz_index(repo: str, st: config.Settings,
                    cache_path: str | None = None,
                    on_progress=None) -> dict:
    """
    SZZ fix-tracing: {bug_introducing_sha: [(fix_sha, fix_time_iso), ...]}.
    For every fix commit, blame the lines it modifies at the fix's parent to
    find the commit that last touched them — that commit is bug-introducing.
    Keeping the fix sha and time lets the label (a) exclude fixes made inside
    the same PR and (b) record how long after merge the fix came. Cached per
    repo. Each fix commit blames only its changed lines (-L), fix commits run
    in a process pool (st.workers), and finished ones are checkpointed to
    <cache>.partial.jsonl so a crash resumes. on_progress(done, total), if
    given, is called as fix commits finish (for the TUI). Blame honors the run's
    configure_blame() flags (-w/-M and optional ignore-revs), so formatting
    sweeps and moves are less likely to be mis-blamed as bug-introducing.
    Noisy by nature (~0.6 precision) — an additional, softer label.

    LIMITATION (unchanged): only deleted/modified lines are blamed, so a fix
    that ONLY adds lines (e.g. a missing null-check) traces to nothing. This is
    an inherent SZZ blind spot, documented for the label-coverage analysis.
    """
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):            # old caches were plain lists
                return {k: [tuple(x) for x in v] for k, v in data.items()}
        except (OSError, json.JSONDecodeError):
            pass

    # fix commits in log order, as (sha, parent, fix_time, paths)
    if on_progress:
        on_progress(0, 0)                       # 0/0 = still scanning history
    tasks = []
    for c, rows in gitio.iter_commits_with_files(repo, "HEAD"):
        if not c.parents or not config.is_fix_text(c.subject):
            continue
        fix_time = c.committed.astimezone(timezone.utc).isoformat()
        tasks.append((c.sha, c.parents[0], fix_time, [p for _a, _d, p in rows]))

    # Crash-safe checkpoint: each fix commit's bug-introducing shas are appended
    # to <cache>.partial.jsonl as it finishes, and a re-run skips those.
    ckpt = (cache_path + ".partial.jsonl") if cache_path else None
    done: dict[str, list[str]] = {}
    if ckpt and os.path.isfile(ckpt):
        try:
            with open(ckpt, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                        done[rec["sha"]] = rec["bugs"]
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue              # torn last line from a crash
        except OSError:
            pass

    todo = [t for t in tasks if t[0] not in done]
    if on_progress:
        on_progress(len(done), len(tasks))
    if todo:
        print(f"[szz] {len(tasks)} fix commits ({len(done)} checkpointed), "
              f"blaming {len(todo)} with {max(1, min(st.workers, len(todo)))} workers...",
              file=sys.stderr, flush=True)
        t0 = time.time()
        blame_calls = blame_fails = 0
        log_every = max(1, len(todo) // 100)        # a log line per 1%
        emit_every = max(1, len(todo) // 1000)      # a TUI update per 0.1%
        ck = None
        if ckpt:
            os.makedirs(os.path.dirname(ckpt) or ".", exist_ok=True)
            ck = open(ckpt, "a", encoding="utf-8")
        try:
            workers = max(1, min(st.workers, len(todo)))
            with ProcessPoolExecutor(
                    max_workers=workers, initializer=gitio.set_blame_settings,
                    initargs=gitio.blame_settings()) as pool:
                futs = [pool.submit(_szz_fix_bugs, repo, sha, parent, paths)
                        for sha, parent, _t, paths in todo]
                for n, fut in enumerate(as_completed(futs), 1):
                    sha, bugs, b_calls, b_fails = fut.result()
                    blame_calls += b_calls
                    blame_fails += b_fails
                    done[sha] = bugs
                    if ck:
                        ck.write(json.dumps({"sha": sha, "bugs": bugs}) + "\n")
                        ck.flush()
                    if on_progress and (n % emit_every == 0 or n == len(todo)):
                        on_progress(len(done), len(tasks))
                    if n % log_every == 0 or n == len(todo):
                        el = time.time() - t0
                        eta = el / n * (len(todo) - n)
                        print(f"[szz] {len(done)}/{len(tasks)} fix commits blamed "
                              f"({100 * len(done) // len(tasks)}%, "
                              f"{n / el if el else 0:.1f}/s, ETA {eta / 60:.0f}m)",
                              file=sys.stderr, flush=True)
        finally:
            if ck:
                ck.close()
        if blame_fails:
            print(f"[szz] WARNING: {blame_fails} of {blame_calls} blames failed "
                  f"({100 * blame_fails // max(1, blame_calls)}%); those fixes trace to "
                  f"nothing, so SZZ labels for this repo are undercounted",
                  file=sys.stderr, flush=True)

    # Assemble in log order so the index (keys and list order) is exactly what
    # the serial loop produced, however the workers finished.
    index: dict = {}
    for sha, _parent, fix_time, _paths in tasks:
        for bug in done.get(sha, []):
            fixes = index.setdefault(bug, [])
            if (sha, fix_time) not in fixes:
                fixes.append((sha, fix_time))

    if len(tasks) >= 100 and not index:
        print(f"[szz] WARNING: {len(tasks)} fix commits traced to 0 bug-introducing "
              f"commits; that almost always means blame is failing, not a clean repo",
              file=sys.stderr, flush=True)

    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(index, fh)
            os.replace(tmp, cache_path)
            if ckpt and os.path.isfile(ckpt):
                os.remove(ckpt)
        except OSError:
            pass
    return index


def _szz_fix_bugs(repo: str, sha: str, parent: str,
                  paths: list[str]) -> tuple[str, list[str], int, int]:
    """One fix commit's bug-introducing commits, in first-seen order (path
    order, then line order). Runs in a worker process: blames only the lines
    the fix deletes or modifies, at the fix's parent. Also returns how many
    blames ran and failed, so the parent can warn instead of failing silently."""
    before = gitio.blame_stats()
    bugs: list[str] = []
    seen: set[str] = set()
    for path in paths:
        old_lines = gitio.deleted_or_modified_lines(repo, parent, sha, path)
        if not old_lines:
            continue
        blame = gitio.blame_lines(repo, parent, path, old_lines)
        for ln in old_lines:
            bl = blame.get(ln)
            if bl and bl.orig_commit and bl.orig_commit not in seen:
                seen.add(bl.orig_commit)
                bugs.append(bl.orig_commit)
    after = gitio.blame_stats()
    return sha, bugs, after["calls"] - before["calls"], after["fails"] - before["fails"]


def szz_delay(repo: str, pr: dict, base: str, head: str,
              szz_index: dict | None, st: config.Settings) -> float | None:
    """Days from merge to the earliest later fix of a line this PR introduced,
    bounded by szz_maturity_days so older PRs don't accumulate an unbounded
    label rate (which would make the label drift with age). Fixes that are part
    of the PR itself, or dated before the merge, don't count."""
    if not szz_index:
        return None
    pr_commits = gitio.commits_in_range(repo, base, head)
    merged_at = _parse_ts(pr["merged_at"])
    cap = getattr(st, "szz_maturity_days", 90)
    best = None
    for bug in pr_commits & szz_index.keys():
        for fix_sha, fix_iso in szz_index[bug]:
            if fix_sha in pr_commits:
                continue                      # fix inside the same PR
            d = _days(_parse_ts(fix_iso), merged_at)
            if d < 0:
                continue
            if cap and d > cap:
                continue                      # beyond the maturity window
            if best is None or d < best:
                best = d
    return best


def _within(delay: float | None, window: int) -> bool:
    return delay is not None and delay <= window


def build_labels(repo: str, pr: dict, changed: list[str], api,
                 snapshot: datetime, st: config.Settings,
                 szz_index: dict | None = None,
                 obs_cutoff: datetime | None = None,
                 pr_base: str | None = None,
                 pr_head: str | None = None) -> dict:
    d_rev = revert_delay(repo, pr, st)
    d_hot = hotfix_delay(repo, pr, changed, st)
    # SZZ range: prefer the corrected PR range from the orchestrator (the merge
    # commit's parent / branch tip), falling back to the API base/head.
    base = pr_base or (pr.get("base") or {}).get("sha")
    head = pr_head or pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha")
    d_szz = szz_delay(repo, pr, base, head, szz_index, st) if (base and head) else None
    ci_req, ci_opt = ci_fail_labels(api, pr)          # 'failure'|'success'|None

    merged_at = _parse_ts(pr["merged_at"])
    obs = obs_cutoff or snapshot
    obs_days = (obs - merged_at).total_seconds() / 86400.0
    rev_window_closed = obs_days >= st.revert_window_days
    hot_window_closed = obs_days >= st.hotfix_window_days

    rev = _within(d_rev, st.revert_window_days)
    hot = _within(d_hot, st.hotfix_window_days)
    ci_observed = ci_req is not None
    ci_fail = (ci_req == "failure")

    # label_bad counts only OBSERVED positives — an unobserved CI verdict does
    # NOT get silently counted as a pass that then forces label_bad False.
    bad = bool(rev or hot or ci_fail)
    return {
        # when each outcome happened (null = not observed by the cutoff)
        "days_to_revert": d_rev,
        "days_to_hotfix": d_hot,
        "days_to_szz_fix": d_szz,
        # booleans at the default windows
        "label_revert": rev,
        # tri-state: True failed / False passed / None unobserved (see ci_observed)
        "label_ci_fail": ci_fail if ci_observed else None,
        "label_ci_fail_optional": (ci_opt == "failure") if ci_opt is not None else None,
        "label_hotfix": hot,
        "label_szz_bug": d_szz is not None,    # any later fix (SZZ, needs --szz)
        "label_bad": bad,
        # observability — lets training drop/downweight immature or unobserved rows
        "ci_observed": ci_observed,
        "revert_window_closed": rev_window_closed,
        "hotfix_window_closed": hot_window_closed,
        "label_bad_observed": bool(ci_observed and rev_window_closed and hot_window_closed),
        # when this row was extracted, and how far forward outcomes could be seen
        "mined_at": snapshot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_through": obs.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

"""
mine_repo.py — orchestrator.

Turns one cloned repo into a table with one row per merged PR: every v1 feature
from Sources 2-6 (riffle.yml / Source 1 excluded by design) plus the v1 labels.

Diff range (fixed): the PR's changes are read from the MERGE COMMIT's structure,
not from the API `base.sha` (which can be stale and would pull in unrelated
commits that landed on main between PR open and merge):
  - 2-parent merge commit -> diff the PR branch against the parents' fork point
    (base = merge-base(p1, p2), head = p2), so no interleaved main commits leak in
  - 1-parent squash / normal -> base = merge^1 (the squash's net diff IS the PR)
Features anchored to that base stay point-in-time; `base_behind_by` separately
measures how far the ORIGINAL API base lagged main (an integration-staleness and
contamination signal). Rebase-merge repos with multi-commit PRs undercount to the
last commit — see notes.

Usage:
    python mine_repo.py --repo /path/to/clone --owner ORG --name REPO \
        --out /path/to/out.parquet [--max-prs N] [--llm] [--no-network]

Requires: a local clone (with history) and, for PR/CI features + labels, network
access to the GitHub API (GITHUB_TOKEN recommended for rate limits).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict, deque, Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import config
import gitio
import aggregate
import progress
import runlog
import health
import scope
import snapshot_capture
from version import __version__
from features import diff_stats, history, line_history, deterministic, scanners, deps_cve, llm_flags, api_contract, repo_local, merge_signals
import flaky
import maintainers
from labels import (CI_SIDECAR, POST_MERGE_COLUMNS, build_labels, build_szz_index, szz_cache_covers,
                    szz_window)
from merge_labels import (COLUMNS as MERGE_LABEL_COLUMNS, at_open, build_merge_labels,
                          classify_close, close_transcript)
from github_api import GitHubAPI


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _pr_author_identity(repo: str, sha: str) -> tuple[str, str]:
    """(email, name) of a commit's git author, mailmap-canonical (%aE/%aN) so a
    person's two emails collapse to one identity. Caller passes the commit whose
    author is the PR author (see _feat_head)."""
    out = gitio._run(repo, "show", "-s", "--format=%aE%x1f%aN", sha,
                     check=False).strip()
    email, _, name = out.partition("\x1f")
    return email.lower().strip(), name.strip()


_BOT_MARKERS = ("[bot]", "dependabot", "renovate", "github-actions", "greenkeeper")
# specific bot identifiers (not bare first names) so a human named e.g. "Devin"
# isn't flagged as an AI agent; matched against the author EMAIL
_AGENT_MARKERS = ("copilot", "cursoragent", "devin-ai", "codex-bot", "chatgpt-codex",
                  "sweep-ai", "noreply@anthropic.com", "google-labs-jules")


def _is_bot_identity(email: str, name: str) -> bool:
    s = f"{email} {name}".lower()
    return any(m in s for m in _BOT_MARKERS)


def _is_agent_identity(email: str, name: str) -> bool:
    # AI coding agents only (proposal 1.3): a plain "[bot]" account (Dependabot,
    # Renovate, github-actions) is an automation bot, not an agent
    e = (email or "").lower()
    return any(a in e for a in _AGENT_MARKERS)


def _feat_head(repo: str, pr: dict) -> str | None:
    """The SHA whose git author is the PR author and whose diff is the PR's
    change. For a 2-parent merge that's the merged branch tip (p2); for a
    squash / normal merge it's the merge commit itself. For a PR that never
    merged it's the PR's head (its merge_commit_sha is GitHub's test merge)."""
    if pr.get("_unmerged"):
        return (pr.get("head") or {}).get("sha")
    head = pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha")
    if not head:
        return None
    parents = gitio.commit_parents(repo, head)
    return parents[1] if len(parents) >= 2 else head


def _pr_diff_range(repo: str, pr: dict,
                   base_resolver=None) -> tuple[str | None, str | None]:
    """
    (base, head) for the PR's TRUE diff, free of commits that landed on main
    between open and merge:
      - 2-parent merge commit -> (merge-base(p1,p2), p2): the PR branch vs its
        fork point (equivalently `git diff p1...p2`)
      - 1-parent squash/normal -> (merge^1, merge): the merge's own net change
      - 1-parent REBASE merge   -> (head~N, merge): N = PR commit count, supplied
        by base_resolver so the diff covers all N replayed commits, not just the
        last one. base_resolver returns None for squash/normal (use merge^1).
      - root commit (no parents) -> (API base.sha, merge)
      - closed-unmerged PR -> (merge-base(base, head), head), set by mine_repo
    """
    if pr.get("_unmerged"):
        return pr.get("_git_base"), (pr.get("head") or {}).get("sha")
    head = pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha")
    if not head:
        return None, None
    parents = gitio.commit_parents(repo, head)
    if len(parents) >= 2:
        fork = gitio.merge_base(repo, parents[0], parents[1])
        return fork, parents[1]
    if len(parents) == 1:
        # git-primary discovery supplies the parent of the PR's OLDEST landed
        # commit, so a multi-commit landed PR diffs its whole span, not just the
        # last commit. Falls back to the rebase resolver, then to merge^1.
        git_base = pr.get("_git_base")
        if git_base:
            return git_base, head
        rebase_base = base_resolver(pr, head) if base_resolver else None
        return (rebase_base or parents[0]), head
    return (pr.get("base") or {}).get("sha"), head


def _looks_rebased(repo: str, head_sha: str, n: int) -> bool:
    """Confirm that head~1..head~(n-1) are the PR author's own commits (as a
    rebase replay would be), not unrelated main commits (as they'd be if a
    single squash commit were mistaken for a rebase). Used only in repos that
    allow BOTH squash and rebase, to pick the right base per PR."""
    pr_author = _pr_author_identity(repo, head_sha)[0]
    if not pr_author:
        return False
    hits = 0
    for k in range(1, n):                      # head~1 .. head~(n-1)
        sha = gitio.rev_parse_safe(repo, f"{head_sha}~{k}")
        if not sha:
            return False
        if _pr_author_identity(repo, sha)[0] == pr_author:
            hits += 1
    return hits >= max(1, (n - 1) // 2)        # majority share the PR author


def _make_base_resolver(repo: str, api, prs: list[dict], st: config.Settings):
    """
    Build the per-PR base resolver for 1-parent merges (#3: per-repo sniff +
    gated per-PR commit count). It returns head~N for rebase-merged PRs and
    None (meaning "use merge^1") for squash/normal.

    Decision, made once per repo:
      1) one call to repo settings for the allowed merge methods;
      2) a local parent-count sniff over up to 50 fetched PRs.
    Per-PR commit-count calls are paid ONLY when the repo is plausibly
    rebase-style (not merge-dominant AND rebase allowed), so squash- and
    merge-commit repos cost nothing extra. In repos that allow both squash and
    rebase, each multi-commit PR gets a cheap local author-overlap confirm.
    """
    methods = api.repo_merge_methods() if api else {}
    rebase_allowed = methods.get("rebase", True)   # unknown -> assume possible
    squash_allowed = methods.get("squash", True)

    sample = prs[:50]
    p2 = 0
    for pr in sample:
        h = pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha")
        if h and len(gitio.commit_parents(repo, h)) >= 2:
            p2 += 1
    merge_dominant = bool(sample) and (p2 / len(sample)) >= 0.5

    do_rebase = (not merge_dominant) and rebase_allowed and api is not None
    need_confirm = do_rebase and squash_allowed    # mixed repo -> confirm per PR
    style = ("merge" if merge_dominant else
             "rebase" if (do_rebase and not need_confirm) else
             "rebase+squash (confirm per PR)" if need_confirm else "squash/normal")
    print(f"[merge-style] {style}"
          + ("" if not do_rebase else "  (will fetch per-PR commit counts)"),
          file=sys.stderr)

    count_cache: dict = {}
    prefetched: list = []

    def resolver(pr: dict, head_sha: str):
        if not do_rebase:
            return None                        # squash/normal/merge -> merge^1
        num = pr.get("number")
        if num is None:
            return None
        if not prefetched:
            prefetched.append(True)            # one GraphQL pass for the whole list, not a REST call per PR
            nums = [p["number"] for p in prs if p.get("number") is not None]
            try:
                count_cache.update({k: v for k, v in api.commit_counts(nums).items() if v is not None})
            except Exception:                  # noqa: BLE001  (the per-PR REST call below still works)
                pass
        if num not in count_cache:
            count_cache[num] = api.pr_commit_count(num)
        n = count_cache[num]
        if not n or n <= 1:
            return None                        # single commit -> merge^1 is base
        cand = gitio.rev_parse_safe(repo, f"{head_sha}~{n}")
        if not cand:
            return None
        if need_confirm and not _looks_rebased(repo, head_sha, n):
            return None                        # actually a squash -> merge^1
        return cand

    return resolver


def _discover_prs_git(repo: str, owner: str, name: str, api,
                      st: config.Settings, default_branch: str) -> list[dict]:
    """
    Git-primary PR discovery (pr_discovery == "git"). Enumerate merged PRs by
    parsing the default branch's commit messages for PR->commit links (merge
    commits, squash `(#N)`, Closes/Resolves #N, PyTorch/Node landed-commit
    markers), then ENRICH each with one API call for the fields the commit log
    can't give (created_at, base ref, head/fork, labels, author_association,
    draft, milestone, authoritative merged_at). No closed-PR list pagination; a
    single `git log` drives discovery, so it is cheap and uniform across merge
    styles and works offline for discovery.

    Coverage caveats (documented, not bugs): PRs whose landed commits carry NO
    recognizable PR reference are missed (common with plain rebase merges);
    PRs merged to non-default branches are missed; a stray commit that merely
    mentions #N could in principle be mis-linked (the patterns are specific, so
    this is rare). When the API is off, labels and the enrichment fields are
    absent and metadata is approximated from git.
    """
    index = gitio.landed_commit_index(repo, default_branch, owner, name)
    items: list[tuple] = []
    for num, hits in index.items():
        if not hits:
            continue
        head_sha, head_time = hits[0]      # newest landed commit for this PR
        first_sha = hits[-1][0]            # oldest linked commit for this PR
        items.append((num, head_sha, head_time, first_sha))
    items.sort(key=lambda x: x[2], reverse=True)   # newest-merged first
    if st.min_age_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=st.min_age_days)
        items = [it for it in items if it[2] <= cutoff]
    if st.max_prs:
        items = items[: st.max_prs]

    prs: list[dict] = []
    for num, head_sha, head_time, first_sha in items:
        git_base = gitio.rev_parse_safe(repo, f"{first_sha}^1")
        merged_iso = head_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        full_msg = gitio._run(repo, "show", "-s", "--format=%s%n%b", head_sha,
                              check=False)
        lines = full_msg.splitlines()
        subject = lines[0] if lines else ""
        body = "\n".join(lines[1:]).strip()
        pr: dict = {
            "number": num,
            "merge_commit_sha": head_sha,
            "merged_at": merged_iso,
            "merge_via": "landed_commit",
            "base": {"sha": git_base, "ref": default_branch},
            "head": {"sha": head_sha},
            "title": subject,
            "body": body,
            "created_at": None,
            "labels": [],
            "author_association": None,
            "draft": False,
            "milestone": None,
            # parent of the OLDEST landed commit -> full span for multi-commit
            # landed PRs (honored by _pr_diff_range for the 1-parent case)
            "_git_base": git_base,
        }
        # enrich from the API where available; keep git-derived values on failure
        if api is not None:
            full = api.fetch_pr(num)
            if full:
                pr["created_at"] = full.get("created_at") or pr["created_at"]
                if full.get("merged_at"):
                    pr["merged_at"] = full["merged_at"]
                    pr["merge_via"] = "github"
                if (full.get("base") or {}).get("sha"):
                    pr["base"] = full["base"]
                if full.get("head"):
                    pr["head"] = full["head"]
                if full.get("title"):
                    pr["title"] = full["title"]
                if full.get("body") is not None:
                    pr["body"] = full["body"]
                pr["labels"] = full.get("labels") or []
                pr["author_association"] = full.get("author_association")
                pr["draft"] = bool(full.get("draft"))
                pr["milestone"] = full.get("milestone")
        # approximate open time from the oldest PR commit when the API didn't give one
        if not pr["created_at"]:
            try:
                t = gitio.commit_time(repo, first_sha)
                pr["created_at"] = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except gitio.GitError:
                pass
        prs.append(pr)
    return prs


def _build_author_pr_times(repo: str, prs: list[dict]) -> dict[str, list[datetime]]:
    """
    git-author-email -> sorted list of that author's merged-PR timestamps. Keyed
    on the PR-author identity (via _feat_head, the same identity EXP and the
    other author features use), so a row's author signals are all about one
    person. Costs one local `git show` per PR; no API calls. Used to count an
    author's PRs merged *before* a given PR opened — point-in-time, no leakage.
    """
    idx: dict[str, list[datetime]] = defaultdict(list)
    for pr in prs:
        fh = _feat_head(repo, pr)
        merged = pr.get("merged_at")
        if not fh or not merged:
            continue
        email, _ = _pr_author_identity(repo, fh)
        if email:
            idx[email].append(_parse_ts(merged))
    for email in idx:
        idx[email].sort()
    return idx


def _author_prs_merged_repo(author_email: str, cutoff_raw: str | None,
                            author_pr_times: dict | None):
    """
    How many PRs this git author had already merged in this repo, as of when
    THIS PR opened (created_at) — point-in-time. None when we lack the PR list
    or the author/time can't be resolved (treated as missing, not zero).
    """
    if not author_pr_times or not author_email or not cutoff_raw:
        return None
    times = author_pr_times.get(author_email)
    if not times:
        return 0
    return bisect.bisect_left(times, _parse_ts(cutoff_raw))


def _build_concurrency(repo: str, prs: list[dict],
                       base_resolver=None, workers: int = 1) -> dict[int, dict]:
    """
    For each PR, count OTHER PRs that were open at the moment it opened and
    touch the same files (and, more finely, the same lines). Point-in-time by
    construction: a PR Q counts for P only if Q opened at/before P and was still
    open when P opened. Files/lines come from the local clone, from each PR's
    at-open diff where known (ADR 0010), else its final diff — no extra API.

    Note: computed over the fetched PR set, so a small --max-prs cap undercounts
    (it only sees PRs in that window); a full mine sees all merged PRs. PRs that
    closed without merging aren't in the list and are not counted.
    """
    def _one(pr: dict) -> dict | None:
        created = pr.get("created_at")
        closed = pr.get("merged_at") or pr.get("closed_at")
        if not closed and pr.get("_stale_open"):
            closed = "9999-12-31T00:00:00Z"            # still open
        snap = pr.get("_open") or {}
        base, head = ((snap["base"], snap["head"]) if snap.get("head")
                      else _pr_diff_range(repo, pr, base_resolver))
        num = pr.get("number")
        if not (created and closed and base and head and num is not None):
            return None
        try:
            lines = gitio.changed_lines_by_file(repo, base, head)
        except Exception:
            lines = {}
        return {"num": num, "created": _parse_ts(created), "close": _parse_ts(closed),
                "files": set(lines.keys()), "lines": lines}

    # one `git diff -U0` per PR, in parallel (the work is git subprocesses)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        meta = [m for m in pool.map(_one, prs) if m is not None]

    # only PRs sharing a file can count for each other: index files -> PRs
    by_file: dict[str, list[int]] = defaultdict(list)
    for j, q in enumerate(meta):
        for f in q["files"]:
            by_file[f].append(j)
    out: dict[int, dict] = {}
    for i, p in enumerate(meta):
        same_files = same_hunks = 0
        for j in {j for f in p["files"] for j in by_file[f]}:
            if j == i:
                continue
            q = meta[j]
            # was Q open at the instant P opened?
            if q["created"] <= p["created"] < q["close"]:
                shared = p["files"] & q["files"]
                same_files += 1
                if any(p["lines"].get(f) and q["lines"].get(f)
                       and (p["lines"][f] & q["lines"][f]) for f in shared):
                    same_hunks += 1
        out[p["num"]] = {
            "concurrent_open_prs_same_files": same_files,
            "concurrent_open_prs_same_hunks": same_hunks,
        }
    return out


def _pr_metadata(pr: dict) -> dict:
    """Source 4c PR metadata from the API PR object. NOTE: author_is_bot and
    ai_agent_authored are NOT set here — they're derived in mine_pr from the git
    author of the PR's commits (the same identity as EXP/author_*). Only
    author_association stays submitter-based (it has no git equivalent).

    DROPPED (do not re-add without a point-in-time source):
      - labels_at_open / label_is_hotfix / label_security: the list endpoint
        returns labels AS OF MINE TIME, not as of open. Reactive tags
        ('regression', 'reverted', 'security', 'hotfix') added AFTER a PR goes
        bad would leak the outcome into these 'features'. Rebuild from the
        issue-events timeline (labeled/unlabeled before created_at) if you want
        them back. The hotfix/revert/security OUTCOME labels are unaffected —
        they are computed from git history in labels.py, not from these tags.
      - n_commits: the list endpoint never returns `commits`, so it was always
        null (the real count is fetched via pr_commit_count only where the
        rebase base needs it).
      - is_draft: every merged PR is non-draft at mine time, so it was a constant.
    """
    return {
        "title_len": len(pr.get("title") or ""),
        "body_len": len(pr.get("body") or ""),
        "has_milestone": pr.get("milestone") is not None,
        "targets_default_branch": None,   # filled by caller (needs default branch)
        # None when the head repo is gone (a deleted fork): unknown, not "same repo"
        "head_from_fork": (None if not ((pr.get("head") or {}).get("repo") or {}).get("full_name")
                           else pr["head"]["repo"]["full_name"]
                           != ((pr.get("base") or {}).get("repo") or {}).get("full_name")),
        "author_association": pr.get("author_association"),
    }


def _retry_llm(repo: str, row: dict, num, st: config.Settings) -> dict:
    """Ask the LLM again for a PR whose call failed, waiting longer each time
    (plan limits are waited out inside fetch). Returns the last fetch result."""
    fetched = {"status": "fail"}
    for attempt in range(1, st.llm_row_retries + 1):
        wait = st.llm_retry_backoff_s * attempt
        print(f"[llm] PR #{num}: LLM call failed; retry {attempt}/{st.llm_row_retries} in "
              f"{wait:.0f}s (the miner waits, nothing is written without it)",
              file=sys.stderr, flush=True)
        time.sleep(wait)
        fetched = llm_flags.fetch(repo, row["base_sha"], row["head_sha"], st)
        if fetched.get("status") != "fail":
            return fetched
    return fetched


PROVENANCE_COLUMNS = ("miner_version", "miner_commit", "llm_model", "llm_prompt_version", "domain")
_MINER_COMMIT: str | None = None


def _miner_commit() -> str:
    """The miner's own git commit (12 chars), "-dirty" when its files have
    uncommitted changes, or "unknown" outside a git checkout."""
    global _MINER_COMMIT
    if _MINER_COMMIT is None:
        here = os.path.dirname(os.path.abspath(__file__))
        try:
            sha = subprocess.run(["git", "-C", here, "rev-parse", "--short=12", "HEAD"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            dirty = subprocess.run(["git", "-C", here, "status", "--porcelain", "--", here],
                                   capture_output=True, text=True, timeout=10).stdout.strip()
            _MINER_COMMIT = (sha + ("-dirty" if dirty else "")) if sha else "unknown"
        except (OSError, subprocess.SubprocessError):
            _MINER_COMMIT = "unknown"
    return _MINER_COMMIT


def _provenance(st: config.Settings) -> dict:
    return {"miner_version": __version__, "miner_commit": _miner_commit(),
            "llm_model": st.llm_model if st.enable_llm else None,
            "llm_prompt_version": llm_flags.prompt_version() if st.enable_llm else None,
            "domain": st.domain}


CAPTURE = "__capture__"
OUTSIDE_ASSOCIATIONS = scope.OUTSIDE_ASSOCIATIONS


def _llm_in_scope(pr: dict, row: dict, st: config.Settings) -> bool:
    """Whether this row gets LLM calls under st.llm_scope. "training" keeps the
    rows the merge model trains on: outside contributors who are not bots,
    plus a fixed llm_sample_pct sample of the rest (the same PRs on every run,
    from a hash of repo and PR number). An unknown association (no API) counts
    as outside, so git-only runs keep their LLM columns."""
    if st.llm_scope == "all":
        return True
    # author_is_bot already excludes AI agents, which v0.4 is about
    is_bot = bool(row.get("author_is_bot")) or (
        (pr.get("user") or {}).get("type") == "Bot" and not row.get("ai_agent_authored"))
    return scope.is_training(row.get("repo"), pr.get("number"), pr.get("author_association"),
                             is_bot, row.get("author_confirmed_maintainer"), st.llm_sample_pct)


def _llm_skip_reason(pr: dict, row: dict, changed: list[str], st: config.Settings) -> str | None:
    """Why this row's diff gets no LLM call, or None to call it."""
    if not _llm_in_scope(pr, row, st):
        return "scope"
    if st.llm_skip_trivial:
        if row.get("docs_only") or row.get("test_only"):
            return "trivial"
        if changed and all(config.path_flags(p)["touches_lockfile"] or config.path_flags(p)["is_generated"]
                           for p in changed):
            return "trivial"
    return None


def _close_reason_from_llm(pr: dict, row: dict, events: list[dict], ci_head: str | None,
                           st: config.Settings) -> str | None:
    """The classifier's close_reason for an unmerged PR, or None. Rules come
    first: with close_reason_llm "other_only" the LLM is asked only when they
    find no reason. Rows outside llm_scope never ask."""
    if not st.enable_llm or st.close_reason_classifier != "llm" or not _llm_in_scope(pr, row, st):
        return None
    if st.close_reason_llm != "always":
        if build_merge_labels(pr, events, ci_head, st, None).get("close_reason") != "other":
            return None
    if close_transcript(pr, events) is None:
        return None                      # nothing human to read: the rules decide
    for attempt in range(st.llm_row_retries + 1):
        reason = classify_close(pr, events, st)
        if reason is not None:
            return reason
        if attempt < st.llm_row_retries:
            time.sleep(st.llm_retry_backoff_s * (attempt + 1))
    # no answer: stop the repo rather than label this PR by the rules alone
    raise llm_flags.LLMUnavailable(f"close-reason classifier gave no answer for PR "
                                   f"#{pr.get('number')} after {st.llm_row_retries} retries")


def mine_pr(repo: str, pr: dict, api, default_branch: str,
            snapshot: datetime, st: config.Settings,
            author_pr_times: dict | None = None,
            concurrency: dict | None = None,
            ci_map: dict | None = None,
            szz_shas: dict | None = None,
            obs_cutoff: datetime | None = None,
            base_resolver=None) -> dict | None:
    # Two ranges (ADR 0010). The final range (see _pr_diff_range: what landed,
    # immune to a stale API base.sha) keys the post-merge labels. Features use
    # the PR as it stood when it opened (pr["_open"], set by
    # _resolve_open_snapshots), which is what the scorer sees; when that can't be
    # recovered they fall back to the final range and open_snapshot says so.
    final_base, final_head = _pr_diff_range(repo, pr, base_resolver)
    merge_sha = pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha")
    if not final_base or not final_head:
        return None
    try:
        gitio.rev_parse(repo, final_base)
        gitio.rev_parse(repo, final_head)
    except gitio.GitError:
        return None  # clone too shallow / commit missing
    snap = pr.get("_open") or {}
    if snap.get("head"):
        base_sha, head_sha, snapshot_kind = snap["base"], snap["head"], snap["kind"]
    else:
        base_sha, head_sha, snapshot_kind = final_base, final_head, "final"

    runlog.step("diff")
    numstat = gitio.diff_numstat(repo, base_sha, head_sha)
    changed = [p for _, _, p in numstat]
    if not changed and snapshot_kind != "final":
        base_sha, head_sha, snapshot_kind = final_base, final_head, "final"
        numstat = gitio.diff_numstat(repo, base_sha, head_sha)
        changed = [p for _, _, p in numstat]
    if not changed:
        return None
    if snapshot_kind == "final":
        final_numstat = numstat
    else:
        final_numstat = gitio.diff_numstat(repo, final_base, final_head)
    final_changed = [p for _, _, p in final_numstat] or changed

    # PR-author identity (mailmap-canonical) from the commit whose author is the
    # PR author — NOT the merge commit, whose author is the maintainer who merged
    feat_head = _feat_head(repo, pr) or head_sha
    head_email, head_name = _pr_author_identity(repo, feat_head)

    # the PR's timeline, fetched once: merge-outcome labels, and the title,
    # milestone and labels as they were at open (the PR object holds today's)
    events = pr.get("_events")
    if events is None and api is not None and pr.get("number") is not None and (
            st.enable_merge_outcome or st.enable_declared_labels):
        events = api.pr_timeline(pr["number"])
    opened = pr.get("created_at")
    meta_open = (at_open(pr, events, _parse_ts(opened) + timedelta(seconds=st.rework_grace_s))
                 if events is not None and opened else None)
    row: dict = {
        "repo": f"{api.owner}/{api.repo}" if api else os.path.basename(repo),
        "pr_number": pr.get("number"),
        "base_sha": base_sha, "head_sha": head_sha,
        "merged_at": pr.get("merged_at"),
        "merge_via": pr.get("merge_via", "github"),   # github | landed_commit
        "author_email": head_email,
        # exact | recovered | approx | final: how the at-open diff was found
        "open_snapshot": snapshot_kind,
        # the author had merged or closed someone else's PR before this one
        # opened (maintainers.py); null when no timelines were read
        "author_confirmed_maintainer": pr.get("_author_maintainer"),
    }
    # with merge-outcome mining off these stay null, like every other stage's
    # columns, so each row keeps contracts/mined_dataset.schema.json's columns
    row["created_at"] = pr.get("created_at") if st.enable_merge_outcome else None
    row["closed_at"] = pr.get("closed_at") if st.enable_merge_outcome else None

    # --- Source 4a/4b: diff stats + high-risk paths
    runlog.step("diff_stats")
    row.update(diff_stats.compute(repo, base_sha, head_sha, st))

    # --- Source 2: repo history (build index once, per-file, author, coupling)
    runlog.step("history_index")
    idx = history.build_repo_index(repo, base_sha, st, ci_map, paths=changed)
    runlog.step("per_file")
    per_file: dict[str, dict] = {}
    for path in changed:
        pf = history.per_file_features(repo, base_sha, path, idx, st)
        lf = line_history.per_file_line_features(
            repo, base_sha, head_sha, path, row["author_email"], idx, st)
        pf.update(lf)
        per_file[path] = pf

    # LT + la_norm/ld_norm from base sizes
    lt = sum(f.get("_loc", 0) for f in per_file.values())
    row["LT"] = lt
    row["la_norm"] = round(row["LA"] / lt, 4) if lt else 0.0
    row["ld_norm"] = round(row["LD"] / lt, 4) if lt else 0.0
    # AGE = mean days since last change across changed files
    ages = [f["_last_change_days"] for f in per_file.values()
            if f.get("_last_change_days") is not None]
    row["AGE"] = round(sum(ages) / len(ages), 2) if ages else None

    runlog.step("aggregate")
    row.update(aggregate.aggregate_per_file(per_file))
    row.update(history.cochange_features(changed, idx))
    row.update(history.test_expectation_features(changed, per_file, st))
    row.update(history.author_features(row["author_email"], changed, idx, st))
    row["repo_revert_base_rate"] = round(idx["repo_revert_base_rate"], 4)
    # FIX (2a): is this PR itself a bug fix (by title / head-commit keyword)
    head_subject = gitio._run(repo, "show", "-s", "--format=%s", feat_head,
                              check=False).strip()
    row["FIX"] = history.is_fix_pr(meta_open["title"] if meta_open else pr.get("title", ""),
                                   head_subject, st)
    # real PR-based author experience (not the commit-count proxy); point-in-time,
    # keyed on the PR-author identity (same identity as EXP)
    row["author_prs_merged_repo"] = _author_prs_merged_repo(
        head_email, pr.get("created_at") or pr.get("merged_at"), author_pr_times)
    # bot / agent flags from the git author of the code being merged (not the
    # maintainer who merged), so they agree with author_email / EXP
    # Two independent flags (proposal 1.3). ai_agent_authored uses agent_name's
    # signals too (account, head-branch prefix, body boilerplate);
    # author_is_bot is automation only, so an agent PR is never a bot PR.
    login = (pr.get("user") or {}).get("login")
    agent = merge_signals.agent_name(login, (pr.get("head") or {}).get("ref"), head_email, pr.get("body"))
    row["ai_agent_authored"] = _is_agent_identity(head_email, head_name) or agent is not None
    row["author_is_bot"] = (not row["ai_agent_authored"]) and (
        _is_bot_identity(head_email, head_name) or (pr.get("user") or {}).get("type") == "Bot"
        or str(login or "").lower().endswith("[bot]"))

    # Kamei author experience (EXP/REXP/SEXP)
    row.update(history.experience_features(row["author_email"], changed, idx))
    # repo-relative percentiles (how big/spread this PR is for THIS repo)
    row.update(history.percentile_features(
        row["LA"] + row["LD"], row["NF"], row["Entropy"], idx))
    # integration staleness at open: how many base-branch commits the PR's
    # fork point was behind the base branch when the PR opened
    row["base_behind_by"] = None
    if snapshot_kind != "final":
        row["base_behind_by"] = snap.get("behind")

    # concurrent open PRs touching the same files / hunks (integration risk)
    conc = (concurrency or {}).get(pr.get("number"))
    row["concurrent_open_prs_same_files"] = conc["concurrent_open_prs_same_files"] if conc else None
    row["concurrent_open_prs_same_hunks"] = conc["concurrent_open_prs_same_hunks"] if conc else None

    # hotspots touched
    row.update(_hotspot_touch(per_file, idx, st))

    # --- Source 5: deterministic checks
    runlog.step("deterministic")
    row.update(deterministic.compute(repo, base_sha, head_sha, st))

    # --- Source 4d: scanners (optional)
    runlog.step("scanners")
    row.update(scanners.compute(repo, base_sha, head_sha, changed, st))

    # --- Source 4e: deps/CVE (optional/network)
    runlog.step("deps_cve")
    row.update(deps_cve.compute(repo, base_sha, head_sha, changed, st))

    # --- Source 5: API contract diff (oasdiff/buf/graphql-inspector, optional)
    runlog.step("api_contract")
    row.update(api_contract.compute(repo, base_sha, head_sha, changed, st))

    # --- Source 6: LLM flags (optional)
    # --- repo-local mined features (contracts/mined_features.schema.json):
    # path size here; the PR-history columns are filled per repo afterwards
    row["path_size_pctile"] = repo_local.path_size_pctile(repo, base_sha, numstat, st)
    runlog.step("reviews")
    reviews = None
    if api is not None and st.enable_review_history and pr.get("number") is not None:
        reviews = api.review_events(pr["number"], st.scrutiny_exclude_bot_reviews)
    row[repo_local.SIDECAR] = repo_local.sidecar(pr, numstat, reviews, final_numstat)

    # fetched here (worker thread); resolved in PR order by mine_repo()
    runlog.step("llm")
    llm_skip = _llm_skip_reason(pr, row, changed, st)
    row["__llm__"] = llm_flags.fetch(repo, base_sha, head_sha, st, skip=llm_skip)

    # --- Source 4c: PR metadata (needs the API PR object)
    md = _pr_metadata(pr)
    if meta_open:
        md["title_len"] = len(meta_open["title"])
        md["has_milestone"] = meta_open["has_milestone"]

    # --- merge-model signals from the AIDev catalog (features/merge_signals.py)
    login = (pr.get("user") or {}).get("login")
    head_ref = (pr.get("head") or {}).get("ref")
    runlog.step("merge_signals")
    md.update(merge_signals.body_features(pr.get("body")))
    md["agent_name"] = merge_signals.agent_name(login, head_ref, head_email, pr.get("body"))
    md["head_branch_agent_prefix"] = (None if head_ref is None else
                                      bool(merge_signals._BRANCH_PREFIX_RE.match(head_ref)))
    created_acct = api.user_created_at(login) if api is not None else None
    md["author_account_age_days"] = (
        round((_parse_ts(opened) - _parse_ts(created_acct)).total_seconds() / 86400, 1)
        if created_acct and opened else None)
    md.update(merge_signals.repo_files_at(repo, base_sha))
    md["targets_default_branch"] = (pr.get("base", {}) or {}).get("ref") == default_branch
    row.update(md)
    row[merge_signals.SIDECAR] = merge_signals.sidecar(
        pr, row, events, meta_open["title"] if meta_open else None)

    # declared_hotfix: leak-free replacement for the dropped label_is_hotfix.
    # Was an urgent/hotfix LABEL present AT OPEN (point-in-time, from the
    # timeline API)? Optional (one timeline call per PR); None when disabled or
    # the timeline is unreadable. This is an author/triager prior set BEFORE
    # review — it does not read post-merge labels, so it can't leak the outcome.
    declared = None
    if getattr(st, "enable_declared_labels", False) and events is not None and opened:
        labs = at_open(pr, events, _parse_ts(opened)
                       + timedelta(seconds=st.declared_label_grace_s))["labels"]
        declared = any(("hotfix" in l or "rollback" in l) for l in labs)
    row["declared_hotfix"] = declared

    # --- Labels (revert/CI/hotfix key on the real merge commit; SZZ on the
    # corrected PR range). obs_cutoff bounds how far forward outcomes were seen.
    # A PR that never merged has no post-merge outcome: those columns are null.
    if pr.get("_unmerged"):
        row.update({k: None for k in POST_MERGE_COLUMNS})
        row["mined_at"] = snapshot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row["observed_through"] = None
    else:
        runlog.step("labels")
        row.update(build_labels(repo, pr, final_changed, api, snapshot, st, szz_shas,
                                obs_cutoff=obs_cutoff, pr_base=final_base, pr_head=final_head))

    # --- Merge-outcome labels (ADR 0009), from the PR's timeline
    if st.enable_merge_outcome:
        ci_head = llm_reason = None
        if api is not None and pr.get("number") is not None:
            if pr.get("_unmerged") and not pr.get("_stale_open"):
                ci_head = api.check_conclusion(final_head).get("required")
                if events is not None:
                    llm_reason = _close_reason_from_llm(pr, row, events, ci_head, st)
        row.update(build_merge_labels(pr, events, ci_head, st, llm_reason))
    else:
        row.update({c: None for c in MERGE_LABEL_COLUMNS})

    # --- provenance (proposal 1.7, 1.8) and domain (ADR 0011), on every row
    runlog.step("labels_done")
    row.update(_provenance(st))

    # --- snapshot capture (snapshot_capture.py): raw timeline and the heads the
    # PR had while open, for the rows the model trains on
    if st.enable_snapshots and _llm_in_scope(pr, row, st):
        runlog.step("capture")
        t_cap = time.monotonic()
        row[CAPTURE] = snapshot_capture.capture_pr(repo, pr, row, events, st)
        snapshot_capture.note(row[CAPTURE], time.monotonic() - t_cap)
    return row


def _hotspot_touch(per_file, idx, st) -> dict:
    # real repo-relative membership: is a changed file among the repo's top-k
    # hotspots (ranked across ALL files at base), not just "has any churn".
    topk = idx.get("hotspot_topk", set())
    rank_map = idx.get("hotspot_rank", {})
    changed = list(per_file.keys())
    touched = [p for p in changed if p in topk]
    ranks = [rank_map.get(p) for p in changed if rank_map.get(p) is not None]
    return {
        "n_topk_hotspots_touched": len(touched),
        "touches_topk_hotspot": len(touched) > 0,
        "min_hotspot_rank_touched": min(ranks) if ranks else None,
    }


def _landed_resolver(repo: str, owner: str, name: str, branch: str):
    """
    Build a resolver that turns a closed-but-not-merged PR into a mergeable one
    by finding the commit(s) on the default branch that landed it. The index is
    built lazily (one git log) the first time it's needed. Returns None for PRs
    with no linked commit (genuinely rejected PRs), so they stay excluded.
    """
    cache: dict = {}

    def resolve(pr: dict):
        if "idx" not in cache:
            cache["idx"] = gitio.landed_commit_index(repo, branch, owner, name)
        hits = cache["idx"].get(pr.get("number")) or []
        created = pr.get("created_at")
        if created:
            opened = _parse_ts(created)
            hits = [h for h in hits if h[1] >= opened]   # landed after the PR opened
        if not hits:
            return None
        head_sha, head_time = hits[0]                    # newest landed commit
        first_sha = hits[-1][0]                          # oldest landed commit
        base_sha = gitio.rev_parse_safe(repo, f"{first_sha}^1")
        if not base_sha:
            return None
        landed = dict(pr)
        landed["merged_at"] = head_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        landed["merge_commit_sha"] = head_sha
        landed["base"] = dict(pr.get("base") or {}, sha=base_sha)
        landed["merge_via"] = "landed_commit"
        return landed

    return resolve


def _open_snapshot(repo: str, pr: dict, pushes: list | None, default_branch: str,
                   grace_s: int) -> dict | None:
    """The PR as it stood when it opened (ADR 0010): {"base", "head", "kind",
    "behind"}, or None when it can't be recovered (features then use the
    final diff).
    head  the newest commit on the PR branch's first-parent line committed at
          or before open + grace. When a force-push after open replaced the
          branch, the walk starts from the head that push replaced (GraphQL
          beforeCommit). The walk stops at GitHub's base.sha (the base tip
          before merge, which never holds the PR's own commits), so commits
          landed on main with their original dates can't be mistaken for it.
    base  where that head forked from the base branch.
    kind  exact (no force-push after open, known from GraphQL) | recovered
          (walked from a replaced head) | approx (force-push history unknown)"""
    final = (pr.get("head") or {}).get("sha")
    opened = pr.get("created_at")
    if not final or not opened or not gitio.rev_parse_safe(repo, f"{final}^{{commit}}"):
        return None
    cut = _parse_ts(opened) + timedelta(seconds=max(0, grace_s))
    api_base = (pr.get("base") or {}).get("sha")
    if not api_base or not gitio.rev_parse_safe(repo, f"{api_base}^{{commit}}"):
        return None
    start, kind = final, ("approx" if pushes is None else "exact")
    later = [b for t, b in (pushes or []) if t and _parse_ts(t) > cut]
    if later:
        if not later[0] or not gitio.rev_parse_safe(repo, f"{later[0]}^{{commit}}"):
            return None
        start, kind = later[0], "recovered"
    head = next((sha for sha, ct in gitio.first_parent_chain(repo, start, api_base)
                 if ct <= cut.timestamp()), None)
    if not head:
        return None
    try:
        base = gitio.merge_base(repo, api_base, head)
    except gitio.GitError:
        return None
    return {"base": base, "head": head, "kind": kind,
            "behind": gitio.count_before(repo, base, api_base, cut)}


def _resolve_open_snapshots(repo: str, prs: list[dict], api, default_branch: str,
                            st: config.Settings) -> None:
    """Set pr["_open"] on every PR whose at-open state can be recovered.
    Fetches each PR's final head (refs/pull/N/head, no local refs) when it isn't
    in the clone yet (squash and rebase merges rewrite it), asks GraphQL for
    force-pushes, and fetches the heads those pushes replaced."""
    nums = [p["number"] for p in prs if p.get("number") is not None]
    have = gitio.existing_commits(repo, [(p.get("head") or {}).get("sha") for p in prs])
    missing = [p["number"] for p in prs if p.get("number") is not None
               and (p.get("head") or {}).get("sha") and p["head"]["sha"] not in have]
    if missing:
        gitio.fetch_pr_heads(repo, missing)
    pushes = api.force_pushes(nums) if api is not None else {}
    gitio.fetch_shas(repo, [b for evs in pushes.values() if evs for _t, b in evs if b])
    kinds: Counter = Counter()
    # each snapshot is a handful of git subprocesses: run them in parallel
    with ThreadPoolExecutor(max_workers=max(1, st.workers)) as pool:
        snaps = list(pool.map(lambda pr: _open_snapshot(
            repo, pr, pushes.get(pr.get("number")), default_branch, st.rework_grace_s), prs))
    for pr, snap in zip(prs, snaps):
        pr["_pushes"] = pushes.get(pr.get("number"))   # None: unknown (snapshot capture)
        if snap:
            pr["_open"] = snap
        kinds[snap["kind"] if snap else "final"] += 1
    print(f"[open-snapshot] {dict(kinds)} (final = at-open diff not recoverable; "
          f"features use the final diff; PRs whose at-open diff turns out empty "
          f"are downgraded to final later, see open_snapshot in health)", file=sys.stderr)


# ---- pinned PR selection (resume) -----------------------------------------
# The first run of a repo saves the PRs it selected; a resumed run loads them
# instead of asking the API again, so new merges since the stop can't shift
# what is mined (which would orphan the checkpoint's rows and change the SZZ
# relevant set). Removed after a clean write and by --remine-repo.

def selection_path(st: config.Settings, owner: str, name: str) -> str:
    return os.path.join(st.ci_cache_dir, f"{owner}_{name}.selection.json")


def _selection_params(st: config.Settings) -> dict:
    return {"window_from": st.window_from, "window_to": st.window_to, "max_prs": st.max_prs,
            "min_age_days": st.min_age_days, "lead_in_days": st.lead_in_days,
            "merge_outcome": st.enable_merge_outcome, "max_unmerged": st.max_unmerged,
            "merge_signal": st.merge_signal, "abandon_days": st.abandon_days,
            "miner_major": __version__.split(".")[0]}


def load_selection(path: str, st: config.Settings) -> dict | None:
    """The pinned selection if it was made with these settings, else None."""
    try:
        with open(path, encoding="utf-8") as fh:
            sel = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return sel if sel.get("params") == _selection_params(st) else None


def save_selection(path: str, st: config.Settings, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"params": _selection_params(st), **data}, fh, default=str)
        os.replace(tmp, path)
    except OSError:
        pass


def update_selection(path: str, extra: dict) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            sel = json.load(fh)
        sel.update(extra)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sel, fh, default=str)
        os.replace(tmp, path)
    except (OSError, json.JSONDecodeError):
        pass


def _prefetch_timelines(api, prs: list[dict], st: config.Settings) -> None:
    """pr["_events"] for every PR that doesn't have it yet, in parallel."""
    need = [p for p in prs if p.get("_events") is None and p.get("number") is not None]
    if not need:
        return
    progress.emit("stage", repo=f"{api.owner}/{api.repo}", stage="timelines")
    with ThreadPoolExecutor(max_workers=max(1, min(8, st.workers))) as pool:
        for pr, events in zip(need, pool.map(lambda p: api.pr_timeline(p["number"]), need)):
            pr["_events"] = events
    print(f"[timeline] read {len(need)} PR timelines", file=sys.stderr)


def _lead_in_sidecars(api, st: config.Settings) -> list[dict]:
    """Merge-signal inputs for the lead-in PRs (api.lead_in, before the mined
    block): metadata and first response only, never mined into rows."""
    lead = getattr(api, "lead_in", None) or []
    if not lead or not st.lead_in_days:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(8, st.workers))) as pool:
        timelines = list(pool.map(lambda p: api.pr_timeline(p["number"]), lead))
    out = []
    for pr, events in zip(lead, timelines):
        pr["_events"] = events                # also maintainer evidence
        login = (pr.get("user") or {}).get("login")
        agent = merge_signals.agent_name(login, (pr.get("head") or {}).get("ref"), None, pr.get("body"))
        out.append(merge_signals.sidecar(pr, {"agent_name": agent}, events, None))
    print(f"[lead-in] {len(out)} PRs before the block give its first PRs a history "
          f"({st.lead_in_days} days, metadata only)", file=sys.stderr)
    return out


def _stale_open_prs(api, merged: list[dict], st: config.Settings) -> list[dict]:
    """PRs still open after st.abandon_days with no human non-author comment
    or review: abandoned, so unmerged rows (ADR 0009). Only from the creation
    window of the merged PRs mined. Open PRs that did get a response have no
    outcome yet and are left out."""
    if not merged:
        return []
    oldest = getattr(api, "block_start", None) or min(p.get("created_at") or "" for p in merged)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=st.abandon_days)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if st.window_from and st.window_to:
        cutoff = min(cutoff, st.window_to + ("T23:59:59Z" if len(st.window_to) == 10 else ""))
    span = getattr(api, "merged_span", None)
    if span and not st.window_from:       # legacy mode: only PRs opened before the newest kept merge, like the unmerged rows
        cutoff = min(cutoff, span[1])
    if getattr(api, "use_landed", False):
        return []                     # closed PRs skipped there; open ones would skew the mix
    cands = api.open_prs(oldest, cutoff)
    if not cands:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(8, st.workers))) as pool:
        timelines = list(pool.map(lambda p: api.pr_timeline(p["number"]), cands))
    out = []
    for pr, events in zip(cands, timelines):
        if events is None:
            continue
        author = (pr.get("user") or {}).get("login", "").lower()
        responded = any(
            ev.get("event") in ("commented", "reviewed")
            and (ev.get("user") or {}).get("type") != "Bot"
            and not str((ev.get("user") or {}).get("login", "")).lower().endswith("[bot]")
            and str((ev.get("user") or {}).get("login", "")).lower() != author
            for ev in events)
        if not responded:
            pr.update(_unmerged=True, _stale_open=True, _events=events, merge_via=None)
            out.append(pr)
    return out


def _szz_relevant_commits(repo: str, prs: list[dict], base_resolver, workers: int) -> set[str]:
    """Every commit of every merged PR's final range: the only commits whose
    SZZ entries a label reads (szz_delay intersects the index with exactly
    these), so SZZ can skip blames that can't reach them."""
    def _one(pr):
        base, head = _pr_diff_range(repo, pr, base_resolver)
        return gitio.commits_in_range(repo, base, head) if base and head else set()
    merged = [p for p in prs if p.get("merged_at") and not p.get("_unmerged")]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return set().union(*pool.map(_one, merged)) if merged else set()


def _prepare_unmerged(repo: str, prs: list[dict], default_branch: str) -> list[dict]:
    """Fetch the commits of closed-unmerged PRs (no local refs, see
    gitio.fetch_pr_heads) and set each one's diff base to where its branch
    forked from the base commit GitHub recorded (or the default branch when
    that commit is gone). PRs whose head can't be fetched are dropped."""
    gitio.fetch_pr_heads(repo, [p["number"] for p in prs if p.get("number") is not None])
    have = gitio.existing_commits(repo, [(p.get("head") or {}).get("sha") for p in prs])
    out = []
    for pr in prs:
        head = (pr.get("head") or {}).get("sha")
        if not head or head not in have:
            continue
        base = (pr.get("base") or {}).get("sha")
        if not base or not gitio.rev_parse_safe(repo, f"{base}^{{commit}}"):
            base = default_branch
        try:
            pr["_git_base"] = gitio.merge_base(repo, base, head)
        except gitio.GitError:
            continue
        pr["merge_commit_sha"] = None          # GitHub's test merge, not in the clone
        out.append(pr)
    return out


def mine_repo(repo: str, owner: str, name: str, st: config.Settings,
              out_path: str | None = None) -> list[dict]:
    default_branch = gitio.default_branch(repo)
    snapshot = datetime.now(timezone.utc)
    # how far forward outcomes can actually be observed in THIS clone: the newest
    # commit time on the default branch. Reverts/hotfixes after this aren't in the
    # clone, so a row is only "mature" up to here — not up to now().
    try:
        obs_cutoff = gitio.commit_time(repo, default_branch)
    except gitio.GitError:
        obs_cutoff = snapshot
    if obs_cutoff > snapshot:
        obs_cutoff = snapshot

    # blame configuration: ignore whitespace + detect moves, and honor the repo's
    # own .git-blame-ignore-revs (formatting sweeps) so line ownership isn't
    # mis-attributed. Clears the blame cache so it takes effect this run.
    repo_ignore = os.path.join(repo, ".git-blame-ignore-revs")
    gitio.configure_blame(
        ignore_revs_file=repo_ignore if os.path.isfile(repo_ignore) else st.blame_ignore_revs)

    api = GitHubAPI(owner, name, st) if st.enable_github_api else None
    rows: list[dict] = []
    unmerged: list[dict] = []
    discovery = getattr(st, "pr_discovery", "api")

    runlog.set_repo(f"{owner}/{name}")
    runlog.start_heartbeat()
    runlog.info("repo", "start", repo_path=repo, default_branch=default_branch,
                workers=st.workers, llm=st.enable_llm, szz=st.enable_szz,
                snapshots=st.enable_snapshots, max_prs=st.max_prs)
    runlog.stage("fetching PRs")
    progress.emit("stage", repo=f"{owner}/{name}", stage="fetching PRs")
    lead_in: list[dict] = []          # lead-in merge-signal inputs (window mode)
    if discovery == "git":
        # git-primary: discover merged PRs from the commit log; the API only
        # enriches each one (no closed-PR list pagination, uniform across merge
        # styles, discovery works offline).
        prs = _discover_prs_git(repo, owner, name, api, st, default_branch)
        note = "" if api is not None else \
            " (API off: no labels; metadata approximated from git)"
        print(f"[discover] git-primary: {len(prs)} merged PRs from commit "
              f"messages{note}", file=sys.stderr)
        if st.enable_merge_outcome:
            print("[merge-outcome] git-primary discovery finds merged PRs only; "
                  "use --pr-discovery api to mine closed-unmerged PRs", file=sys.stderr)
    else:
        if api is None:
            print("[warn] GitHub API disabled and pr_discovery=api: no PR list, "
                  "no labels. Use --pr-discovery git to mine from the clone, or "
                  "provide a token.", file=sys.stderr)
            return rows
        resolver = _landed_resolver(repo, owner, name, default_branch)
        sel_path = selection_path(st, owner, name)
        sel = load_selection(sel_path, st)
        if sel is not None:
            # a resumed run mines exactly the PRs the first run picked, so the
            # checkpoint, the SZZ relevant set and the shard split all still fit
            prs, stale, lead_in = sel["merged"], sel["stale"], sel["lead_sidecars"]
            api.unmerged, api.lead_in = sel["unmerged"], sel["lead_in"]
            api.block_start, api.use_landed = sel.get("block_start"), sel.get("use_landed", False)
            api.incomplete = sel.get("incomplete", False)
            print(f"[resume] PR selection pinned by the first run ({sel_path})", file=sys.stderr)
        else:
            prs = list(api.window_prs(resolver=resolver) if st.window_from
                       else api.merged_prs(resolver=resolver))
            lead_in = _lead_in_sidecars(api, st) if st.window_from else []
            stale = _stale_open_prs(api, prs, st) if st.enable_merge_outcome else []
            save_selection(sel_path, st, {
                "merged": prs, "unmerged": getattr(api, "unmerged", []), "stale": stale,
                "lead_in": getattr(api, "lead_in", None) or [], "lead_sidecars": lead_in,
                "block_start": getattr(api, "block_start", None),
                "use_landed": getattr(api, "use_landed", False),
                "incomplete": getattr(api, "incomplete", False)})
        print(f"[fetch] {len(prs)} merged PRs to process", file=sys.stderr)
        if st.enable_merge_outcome:
            unmerged = _prepare_unmerged(repo, api.unmerged + stale, default_branch)
            print(f"[merge-outcome] {len(unmerged)} of {len(api.unmerged) + len(stale)} "
                  f"unmerged PRs have fetchable commits ({len(stale)} still open past "
                  f"{st.abandon_days} days with no response)", file=sys.stderr)
        if getattr(api, "incomplete", False):
            print("[warn] PR list is TRUNCATED (an API page failed); label coverage "
                  "for this repo is partial — record this in the coverage analysis.",
                  file=sys.stderr)

    # required-status-check list (precise CI gating) — needs the API.
    # 403/None on public repos you don't own -> fall back to the name denylist.
    runlog.stage("required checks")
    if api is not None:
        api.load_required_contexts(default_branch)
        if api.required_contexts:
            print(f"[ci] using {len(api.required_contexts)} required status checks "
                  f"from branch protection (precise)", file=sys.stderr)
        else:
            print("[ci] branch protection not readable; using built-in optional-check "
                  "denylist (heuristic)", file=sys.stderr)

    # the rebase sniff reads merge commits, so it sees merged PRs only; the
    # unmerged PRs join after it (author counts skip them: never merged)
    runlog.stage("merge style")
    base_resolver = _make_base_resolver(repo, api, prs, st)
    if unmerged:
        prs = prs + unmerged
    if api is not None and st.enable_review_history:
        runlog.stage("reviews prefetch")                    # GraphQL, 10 PRs a call; the rest go through REST
        got = api.prefetch_reviews([p["number"] for p in prs if p.get("number") is not None],
                                   st.scrutiny_exclude_bot_reviews)
        print(f"[reviews] {got} of {len(prs)} PRs' review activity read through GraphQL", file=sys.stderr)
    # every PR's timeline, read once up front (mine_pr reuses pr["_events"]):
    # maintainers are confirmed from all of them, point-in-time (maintainers.py)
    runlog.stage("timelines")
    evidence = maintainers.Evidence()
    sel_path = selection_path(st, owner, name) if api is not None else None
    saved = (load_selection(sel_path, st) or {}).get("evidence") if sel_path else None
    if saved is not None:
        evidence = maintainers.Evidence({k: _parse_ts(v) for k, v in saved.items()})
    elif api is not None and (st.enable_merge_outcome or st.enable_declared_labels):
        _prefetch_timelines(api, prs, st)
        evidence = maintainers.Evidence.from_prs(prs + (getattr(api, "lead_in", None) or []))
        if sel_path:
            update_selection(sel_path, {"evidence": {k: v.strftime("%Y-%m-%dT%H:%M:%SZ")
                                                     for k, v in evidence.first.items()}})
    for pr in prs:
        pr["_author_maintainer"] = (evidence.confirmed((pr.get("user") or {}).get("login"),
                                                       _parse_ts(pr["created_at"]) if pr.get("created_at") else None)
                                    if evidence.first or api is not None else None)
    for pr, sc in zip(getattr(api, "lead_in", None) or [], lead_in):
        if evidence.confirmed((pr.get("user") or {}).get("login"),
                              _parse_ts(pr["created_at"]) if pr.get("created_at") else None):
            sc["outsider"] = False
    runlog.stage("author index")
    author_pr_times = _build_author_pr_times(repo, prs)
    if api is not None:
        runlog.stage("open snapshots")
        progress.emit("stage", repo=f"{owner}/{name}", stage="open snapshots")
        _resolve_open_snapshots(repo, prs, api, default_branch, st)
    runlog.stage("concurrency")
    concurrency = _build_concurrency(repo, prs, base_resolver, st.workers)
    ci_map = {}
    if st.enable_ci_history and api is not None:
        cache_path = os.path.join(st.ci_cache_dir, f"{owner}_{name}.json")
        print("[ci-history] building historical CI-fail store (cached after first run)...",
              file=sys.stderr, flush=True)
        runlog.stage("ci history")
        progress.emit("stage", repo=f"{owner}/{name}", stage="ci history")
        ci_map = api.build_ci_conclusion_map(cache_path)
        print(f"[ci-history] {len(ci_map)} commits with a CI verdict", file=sys.stderr)
    reuse_path = os.path.join(st.ci_cache_dir, f"{owner}_{name}.llm_reuse.json")
    if st.enable_llm and os.path.isfile(reuse_path):
        n_reuse = llm_flags.load_reuse(reuse_path)
        print(f"[llm] re-mine: reusing {n_reuse} earlier LLM answers for unchanged PRs "
              f"(no new calls for those)", file=sys.stderr, flush=True)
    # Load the repo-wide commit history now, under a visible stage, instead of
    # silently inside the first PR (~1.5 min on a Kubernetes-sized repo). SZZ
    # reads HEAD's commits from it too, rather than running its own full log.
    runlog.stage("loading history")
    progress.emit("stage", repo=f"{owner}/{name}", stage="loading history")
    gitio._numstat_cache(repo)

    szz_shas = None
    if st.enable_szz:
        runlog.stage("szz")
        szz_cache = os.path.join(st.ci_cache_dir, f"{owner}_{name}.szz.json")
        print("[szz] tracing fix-forward defects (slow; cached after first run)...",
              file=sys.stderr, flush=True)
        window = szz_window(prs, st)
        relevant = _szz_relevant_commits(repo, prs, base_resolver, st.workers)
        szz_cached = szz_cache_covers(szz_cache, window, repo, relevant)
        szz_shas = build_szz_index(
            repo, st, szz_cache,
            on_progress=lambda d, n: progress.emit(
                "szz", repo=f"{owner}/{name}", done=d, total=n),
            window=window, relevant=relevant)
        print(f"[szz] {len(szz_shas)} bug-introducing commits identified"
              f"{' (from cache)' if szz_cached else ''}", file=sys.stderr)
        progress.emit("szz", repo=f"{owner}/{name}", finished=True, cached=szz_cached,
                      bugs=len(szz_shas))


    # crash-safe checkpoint + resume: rows are flushed to <out>.partial.jsonl as
    # they're computed, so a crash mid-repo doesn't lose hours of work; a re-run
    # skips PRs already in the checkpoint. main() removes it after a clean write.
    repo_key = f"{owner}/{name}"
    ckpt = (out_path + ".partial.jsonl") if out_path else None
    if out_path and os.path.dirname(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    done: set = set()
    if ckpt and os.path.isfile(ckpt):
        try:
            with open(ckpt, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    if r.get("repo") == repo_key and r.get("pr_number") is not None:
                        done.add(r["pr_number"])
                        rows.append(r)
            if done:
                print(f"[resume] {len(done)} PRs already checkpointed; skipping them",
                      file=sys.stderr)
        except (OSError, json.JSONDecodeError):
            pass

    skipped: Counter = Counter()
    pr_secs_total = 0.0                       # worker time on mined PRs (capture share)
    ckpt_fh = open(ckpt, "a", encoding="utf-8") if ckpt else None
    capture = (snapshot_capture.Writer(st.snapshot_dir or os.path.join("snapshots_raw", f"{owner}_{name}"),
                                       resume=bool(done))
               if st.enable_snapshots else None)
    if capture and not done:
        for sc in lead_in:                    # assembly recomputes history from these too
            capture.write(None, sc)

    def _work(pr: dict):
        t0, w0 = time.monotonic(), llm_flags.quota_wait_s()

        def took():
            # worker time without any plan-quota pause, which is waiting, not mining
            waited = llm_flags.quota_wait_s() - w0
            return time.monotonic() - t0 - waited, waited
        with runlog.pr_scope(pr.get("number"), waited=llm_flags.quota_wait_s):
            try:
                r = mine_pr(repo, pr, api, default_branch, snapshot, st,
                            author_pr_times, concurrency, ci_map, szz_shas,
                            obs_cutoff=obs_cutoff, base_resolver=base_resolver)
                return ("ok", r, None, *took())
            except gitio.GitError as e:
                runlog.set_status("git-error")
                return ("git", None, e, *took())
            except Exception as e:                          # noqa: BLE001
                runlog.set_status("exc:" + type(e).__name__)
                runlog.warn("pr", "mining failed", exc=type(e).__name__, err=str(e)[:200])
                return ("exc", None, e, *took())

    def _commit(pr_rows: list[dict]):
        for pr_row in pr_rows:
            cap = pr_row.pop(CAPTURE, None)
            if capture:
                capture.write(cap, pr_row.get(merge_signals.SIDECAR))
            rows.append(pr_row)
            if ckpt_fh:
                ckpt_fh.write(json.dumps(pr_row, default=str) + "\n")
                ckpt_fh.flush()

    # PRs are mined in parallel worker threads (mostly git subprocess time) but
    # consumed strictly in PR order, so row order, the checkpoint, and the LLM
    # circuit breaker behave exactly as in a sequential run.
    todo = [(i, pr) for i, pr in enumerate(prs, 1) if pr.get("number") not in done]
    runlog.stage("mining PRs", total=len(prs), todo=len(todo), resumed=len(done))
    workers = max(1, st.workers)
    window = max(2 * workers, 2 * st.llm_concurrency if st.enable_llm else 0)
    progress.emit("repo_start", repo=repo_key, total=len(prs), resumed=len(done),
                  workers=workers, llm=st.enable_llm)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pr")
    inflight: deque = deque()
    feed = iter(todo)

    def _fill():
        while len(inflight) < window:
            nxt = next(feed, None)
            if nxt is None:
                return
            inflight.append((nxt[0], nxt[1], pool.submit(_work, nxt[1])))

    try:
        _fill()
        while inflight:
            i, pr, fut = inflight.popleft()
            kind, r, err, secs, waited = fut.result()
            _fill()
            num = pr.get("number")
            print(f"[pr {i}/{len(prs)}] #{num} ({secs:.1f}s"
                  f"{f', +{waited:.0f}s quota wait' if waited >= 1 else ''})",
                  file=sys.stderr, flush=True)
            if kind == "git":
                skipped["git-error"] += 1
                print(f"[skip] PR #{num}: git: {err}", file=sys.stderr)
                progress.emit("pr", repo=repo_key, i=i, num=num, secs=secs, status="skip",
                              reason="git-error")
                continue
            if kind == "exc" and isinstance(err, llm_flags.LLMUnavailable):
                print(f"[llm] PR #{num}: {err}. Stopping this repo so no row is written without "
                      f"its LLM answer; {len(rows)} rows kept in the checkpoint, and a rerun "
                      f"resumes from PR #{num}.", file=sys.stderr, flush=True)
                progress.emit("breaker", repo=repo_key, num=num, msg=str(err), kept=len(rows), dropped=0)
                st.llm_breaker_tripped = True
                break
            if kind == "exc":
                skipped[f"exc:{type(err).__name__}"] += 1
                print(f"[skip] PR #{num}: {err}", file=sys.stderr)
                progress.emit("pr", repo=repo_key, i=i, num=num, secs=secs, status="skip",
                              reason=f"exc:{type(err).__name__}")
                continue
            if not r:
                skipped["no-rows (no diff / missing commit / shallow clone)"] += 1
                progress.emit("pr", repo=repo_key, i=i, num=num, secs=secs, status="skip",
                              reason="no-rows")
                continue
            fetched = r.pop("__llm__", None) or {"status": "off"}
            if fetched.get("status") == "fail":
                # the miner waits here (later PRs queue behind this one), so a
                # row is never written with LLM columns left empty by a failure
                fetched = _retry_llm(repo, r, num, st)
            if fetched.get("status") == "fail":
                print(f"[llm] PR #{num}: no LLM answer after {st.llm_row_retries} retries. "
                      f"Stopping this repo so no row is written without it; {len(rows)} rows "
                      f"kept in the checkpoint, and a rerun resumes from PR #{num}.",
                      file=sys.stderr, flush=True)
                progress.emit("breaker", repo=repo_key, num=num, msg="LLM retries exhausted",
                              kept=len(rows), dropped=0)
                st.llm_breaker_tripped = True
                break
            r.update(llm_flags.resolve(fetched, st))
            pr_secs_total += secs or 0.0
            progress.emit("pr", repo=repo_key, i=i, num=num, secs=secs, status="ok",
                          llm="reused" if fetched.get("reused") else fetched.get("status"),
                          llm_secs=fetched.get("secs"),
                          llm_usage=llm_flags.usage_snapshot() if st.enable_llm else None)
            _commit([r])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if ckpt_fh:
            ckpt_fh.close()
        if capture:
            capture.close()
            evidence.save(capture.dir)        # snapshot roles use it too
    if st.enable_snapshots:
        cs = snapshot_capture.take_stats()
        if cs["prs"]:
            share = cs["secs"] / pr_secs_total if pr_secs_total else 0.0
            print(f"[snapshots] captured {cs['prs']} PRs · {cs['heads'] / cs['prs']:.2f} heads/PR "
                  f"({(cs['heads'] - cs['prs']) / cs['prs']:.2f} extra) · capture took "
                  f"{cs['secs']:.0f}s, {share:.0%} of PR mining time "
                  f"(~{share / (1 - share) if share < 1 else 0:.0%} slower than without)",
                  file=sys.stderr)

    # repo-local PR-history features need every PR's outcome, so they're
    # computed once all rows are in (strictly point-in-time per row)
    runlog.stage("repo-wide labels")
    if not st.llm_breaker_tripped:
        n_flaky = flaky.apply(rows, st)
        if n_flaky:
            print(f"[ci] {n_flaky} CI failures came only from flaky checks; not counted "
                  f"as label_ci_fail", file=sys.stderr)
        repo_local.compute_repo(rows, repo_local.release_tags(repo, st), st)
        merge_signals.compute_repo(rows, st, with_unmerged=st.enable_merge_outcome,
                                   lead_in=lead_in)
    for r in rows:
        r.pop(repo_local.SIDECAR, None)
        r.pop(merge_signals.SIDECAR, None)
        r.pop(CI_SIDECAR, None)
        for col in merge_signals.COLUMNS:
            r.setdefault(col, None)
        for col in repo_local.COLUMNS:
            r.setdefault(col, 0 if col == "similar_pr_count" else None)

    if skipped:
        total = sum(skipped.values())
        detail = ", ".join(f"{k}={v}" for k, v in skipped.most_common())
        print(f"[skips] {total}/{len(prs)} PRs produced no row — {detail}",
              file=sys.stderr)
    runlog.stage(None)
    runlog.info("repo", "done", rows=len(rows), skipped=dict(skipped))
    LAST_SKIPS.clear()
    LAST_SKIPS.update(skipped)
    return rows


# why PRs of the last mined repo produced no row (reason -> count); the health check reads it
LAST_SKIPS: dict = {}


def _infer_owner_name(repo: str):
    """Parse OWNER/REPO from the clone's origin remote URL, if present."""
    try:
        url = gitio._run(repo, "remote", "get-url", "origin", check=False).strip()
    except Exception:
        return None, None
    if not url:
        return None, None
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2)
    return None, None


def main():
    config.stop_on_break()
    ap = argparse.ArgumentParser(description="Extract Riffle v1 features from a clone.")
    ap.add_argument("--repo", default=None, help="path to local clone (required unless --llm-test)")
    ap.add_argument("--owner", default=None,
                    help="GitHub org/user (auto-detected from origin remote if omitted)")
    ap.add_argument("--name", default=None,
                    help="repo name (auto-detected from origin remote if omitted)")
    ap.add_argument("--out", default=None,
                    help="output .parquet or .jsonl (defaults to <owner>_<n>.parquet)")
    ap.add_argument("--max-prs", type=int, default=None)
    ap.add_argument("--min-age-days", type=int, default=90,
                    help="skip PRs merged in the last N days so labels are mature "
                         "(default 90: covers the 30-day maturity and 90-day SZZ windows; 0 = off)")
    ap.add_argument("--llm", action=argparse.BooleanOptionalAction, default=True,
                    help="LLM semantic flags (Source 6) and the close-reason classifier "
                         "(default on; the run stops when no backend is usable; use --no-llm to skip). Default "
                         "backend is headless Claude Code (`claude -p`); LLM_BACKEND=api uses "
                         "LLM_API_KEY")
    ap.add_argument("--llm-model", default=None,
                    help="model for the LLM flags, e.g. sonnet, opus, haiku (default: LLM_MODEL or sonnet)")
    ap.add_argument("--llm-test", action="store_true",
                    help="make one LLM call on a sample diff and report; no mining")
    ap.add_argument("--no-network", action="store_true",
                    help="disable GitHub API + OSV (git-only; no labels)")
    ap.add_argument("--no-scanners", action="store_true")
    ap.add_argument("--no-api-contract", action="store_true",
                    help="skip oasdiff/buf/graphql-inspector spec diffing")
    ap.add_argument("--ci-history", action=argparse.BooleanOptionalAction, default=True,
                    help="historical CI-fail-rate features (API pre-pass, <=400 calls, cached; "
                         "default on)")
    ap.add_argument("--szz", action=argparse.BooleanOptionalAction, default=True,
                    help="SZZ fix-tracing label (windowed to the mined PRs, cached; default on)")
    ap.add_argument("--declared-labels", action=argparse.BooleanOptionalAction, default=True,
                    help="declared_hotfix feature (labels at open, from the timeline the "
                         "merge-outcome labels fetch anyway; default on)")
    ap.add_argument("--merge-outcome", action=argparse.BooleanOptionalAction, default=True,
                    help="merge-outcome target (ADR 0009, default on): also mine "
                         "closed-unmerged PRs and add label_merged / rework / close-reason "
                         "columns from each PR's timeline (1-2 extra calls per PR). "
                         "--no-merge-outcome mines merged PRs for label_bad only")
    ap.add_argument("--max-unmerged", type=int, default=None,
                    help="cap unmerged rows per repo, newest first (default: every "
                         "unmerged PR in the merged PRs' window, the repo's real mix)")
    ap.add_argument("--pr-discovery", choices=["api", "git"], default="api",
                    help="how to enumerate merged PRs: 'api' (PR-list endpoint, "
                         "default) or 'git' (parse commit messages for PR links; "
                         "API only enriches). 'git' avoids closed-PR pagination "
                         "and works offline for discovery.")
    ap.add_argument("--no-review-history", action="store_true",
                    help="skip PR review events (2 API calls/PR); revealed_path_scrutiny -> null")
    ap.add_argument("--workers", type=int, default=None,
                    help="PRs mined in parallel (default: RIFFLE_WORKERS or min(6, CPUs))")
    ap.add_argument("--llm-concurrency", type=int, default=None,
                    help="max concurrent LLM calls (default: LLM_CONCURRENCY or 4)")
    ap.add_argument("--llm-scope", choices=["training", "all"], default=None,
                    help="rows that get LLM calls: training = outside contributors (not bots) "
                         "plus a 5%% sample of the rest (default); all = every row")
    ap.add_argument("--llm-sample-pct", type=int, default=None,
                    help="with --llm-scope training, %% of other rows still sent (default 5)")
    ap.add_argument("--close-reason-llm", choices=["other_only", "always"], default=None,
                    help="other_only: ask the LLM only when the rules find no close reason "
                         "(default); always: every unmerged PR with human activity")
    ap.add_argument("--llm-max-diff-chars", type=int, default=None,
                    help="cap on the diff sent to the LLM after lockfiles and generated "
                         "files are cut (default 25000)")
    ap.add_argument("--llm-quota-wait", action=argparse.BooleanOptionalAction, default=None,
                    help="wait for the Claude plan to reset instead of failing calls (default on)")
    ap.add_argument("--snapshots", action=argparse.BooleanOptionalAction, default=True,
                    help="capture what PR snapshots need (raw timeline, head commits while "
                         "open and their code features) for rows in --llm-scope (default on)")
    ap.add_argument("--snapshot-dir", default=None,
                    help="where the capture files go (default snapshots_raw/<owner>_<name>)")
    ap.add_argument("--window-from", default=None,
                    help="date-window selection: every closed PR created on or after this "
                         "date (YYYY-MM-DD), merged and unmerged; --max-prs then caps the "
                         "newest contiguous block")
    ap.add_argument("--window-to", default=None, help="end of the date window (YYYY-MM-DD)")
    ap.add_argument("--lead-in-days", type=int, default=None,
                    help="with a window: days before the block read as metadata only, so "
                         "its first PRs have a history (default 180; 0 = off)")
    ap.add_argument("--cap-on", choices=["all", "training"], default=None,
                    help="with a window: what --max-prs counts. training = only the PRs the "
                         "model trains on (outside contributors plus the sample), so "
                         "--max-prs 10000 gives about 10,000 of them (default all)")
    ap.add_argument("--full-repo", action="store_true",
                    help="mine the repo's whole history: every closed PR ever opened, merged and "
                         "unmerged, with no date window, no --max-prs cap and no lead-in. PRs too "
                         "recent for mature labels are still left out (--min-age-days)")
    ap.add_argument("--domain", default=None, help="dataset domain written on every row (ADR 0011)")
    ap.add_argument("--health-dir", default="health", help="where per-repo health JSON goes")
    ap.add_argument("--snapshot-max-heads", type=int, default=None,
                    help="head commits per PR with code features (default 5)")
    ap.add_argument("--append", action="store_true",
                    help="append rows to --out (one growing dataset); dedups by "
                         "(repo, pr_number), keeping the newest extraction")
    args = ap.parse_args()

    if args.llm_test:
        st = config.Settings()
        if args.llm_model:
            st.llm_model = args.llm_model
        ok, msg = llm_flags.self_test(st)
        print(msg)
        sys.exit(0 if ok else 1)
    if not args.repo:
        ap.error("--repo is required")

    # auto-detect owner/name from the clone's remote when not given
    owner, name = args.owner, args.name
    if not owner or not name:
        inf_owner, inf_name = _infer_owner_name(args.repo)
        owner = owner or inf_owner
        name = name or inf_name
    if not owner or not name:
        ap.error("could not determine --owner/--name (no github origin remote "
                 "found); pass them explicitly.")

    out = args.out or f"{owner}_{name}.parquet"

    st = config.Settings()
    st.max_prs = args.max_prs
    st.min_age_days = args.min_age_days
    st.enable_llm = args.llm
    if args.no_scanners:
        st.enable_scanners = False
    if args.no_api_contract:
        st.enable_api_contract = False
    st.enable_ci_history = args.ci_history
    st.enable_szz = args.szz
    st.enable_declared_labels = args.declared_labels
    if st.enable_llm:
        why = llm_flags.unavailable_reason(st)
        if why:
            # on by default, and null LLM columns would make this dataset differ
            # from one mined with a working backend: stop, don't degrade
            sys.exit(f"[llm] unavailable: {why}. The LLM flags and close-reason classifier are "
                     f"part of the default dataset. Fix the backend, or pass --no-llm to mine "
                     f"without them on purpose (those columns come back null).")
    st.enable_merge_outcome = args.merge_outcome
    st.max_unmerged = args.max_unmerged
    st.pr_discovery = args.pr_discovery
    if args.llm_model:
        st.llm_model = args.llm_model
    if args.no_review_history:
        st.enable_review_history = False
    if args.workers is not None:
        st.workers = args.workers
    elif not os.getenv("RIFFLE_WORKERS"):
        full = min(6, os.cpu_count() or 2)
        if st.workers < full:
            print(f"[workers] {st.workers} instead of {full}: {config.available_ram_gb():.1f} GB "
                  f"RAM free, ~{config.WORKER_RAM_GB} GB per worker (--workers overrides)",
                  file=sys.stderr)
    if args.llm_concurrency is not None:
        st.llm_concurrency = args.llm_concurrency
    st.enable_snapshots = args.snapshots
    if args.full_repo:
        if args.window_from or args.window_to or args.max_prs or args.lead_in_days or args.cap_on:
            sys.exit("--full-repo mines everything; it can't be combined with "
                     "--window-from/--window-to, --max-prs, --lead-in-days or --cap-on")
        args.window_from, args.window_to = config.FULL_REPO_FROM, config.FULL_REPO_TO
        args.lead_in_days = 0      # nothing precedes the first PR
    if bool(args.window_from) != bool(args.window_to):
        sys.exit("--window-from and --window-to go together")
    st.window_from, st.window_to = args.window_from, args.window_to
    if args.cap_on:
        if args.cap_on == "training" and not args.window_from:
            sys.exit("--cap-on training needs a date window (--window-from/--window-to)")
        st.cap_on = args.cap_on
    if args.lead_in_days is not None:
        st.lead_in_days = args.lead_in_days
    st.domain = args.domain
    if args.snapshot_dir:
        st.snapshot_dir = args.snapshot_dir
    for opt in ("llm_scope", "llm_sample_pct", "close_reason_llm", "llm_max_diff_chars",
                "llm_quota_wait", "snapshot_max_heads"):
        if getattr(args, opt) is not None:
            setattr(st, opt, getattr(args, opt))
    if args.no_network:
        st.enable_github_api = False
        st.enable_deps_cve = False

    tok = "set" if st.github_token else "not set (public rate limits, no labels)"
    print(f"[info] {owner}/{name}  |  GITHUB_TOKEN: {tok}  |  -> {out}",
          file=sys.stderr)
    rows = mine_repo(args.repo, owner, name, st, out_path=out)
    if st.llm_breaker_tripped:
        # don't write a partial repo into --out; the checkpoint holds the clean
        # rows and a rerun (once the LLM is back) resumes from it
        print(f"[llm-breaker] {out} not written; resume by rerunning the same "
              f"command. Exiting with code 3.", file=sys.stderr)
        progress.emit("repo_done", repo=f"{owner}/{name}", rows=len(rows), status="breaker")
        sys.exit(3)
    total, ok = _write(rows, out, append=args.append)
    # per-repo health check (proposal Part 3): summary + flags, saved as JSON
    in_scope = (lambda r: st.enable_llm and _llm_skip_reason(
        {"author_association": r.get("author_association"), "number": r.get("pr_number"), "user": {}},
        r, [], st) is None)
    health.report(rows, f"{owner}/{name}", args.health_dir, in_llm_scope=in_scope, skipped=dict(LAST_SKIPS))
    reuse_path = os.path.join(st.ci_cache_dir, f"{owner}_{name}.llm_reuse.json")
    if ok and os.path.isfile(reuse_path):
        os.remove(reuse_path)                       # one-shot: the re-mine is done
    usage = llm_flags.usage_snapshot() if st.enable_llm else None
    if usage:
        print(f"[llm] {owner}/{name}: {llm_flags.usage_summary(usage)}", file=sys.stderr, flush=True)
    progress.emit("repo_done", repo=f"{owner}/{name}", rows=len(rows), total=total,
                  status="ok" if ok else "write-failed", llm_usage=usage)
    # remove the per-repo checkpoint and pinned selection only after a clean,
    # complete write
    if ok and out:
        for f in (out + ".partial.jsonl", selection_path(st, owner, name)):
            try:
                if os.path.isfile(f):
                    os.remove(f)
            except OSError:
                pass
    if args.append:
        print(f"[done] +{len(rows)} rows this run, {total} total -> {out}")
    else:
        print(f"[done] {len(rows)} rows -> {out}")


def _merge_with_existing(rows: list[dict], out: str) -> list[dict]:
    """
    Load rows already in `out`, add the new `rows`, and de-duplicate by
    (repo, pr_number) keeping the LAST occurrence — so re-mining a repo/PR
    updates its row rather than duplicating it. Returns the merged list.

    RAISES on any read failure instead of silently overwriting: the caller then
    writes to a recovery sidecar and leaves `out` intact, so a transient read
    error (locked/corrupt file) can never clobber the whole accumulated dataset.
    """
    if out.endswith(".jsonl"):
        with open(out, encoding="utf-8") as fh:
            existing = [json.loads(line) for line in fh if line.strip()]
    else:
        import pandas as pd
        df = pd.read_csv(out) if out.endswith(".csv") else pd.read_parquet(out)
        df = df.astype(object).where(pd.notnull(df), None)  # NaN -> None
        existing = df.to_dict("records")

    merged: dict = {}
    for r in existing + rows:                    # new rows come last -> they win
        merged[(r.get("repo"), r.get("pr_number"))] = r
    return list(merged.values())


def _write(rows: list[dict], out: str, append: bool = False) -> tuple[int, bool]:
    """Write rows to `out`. With append=True, merge into any existing file first
    (dedup by repo+pr_number). Returns (total_rows_written, ok). On an append
    read failure, writes a recovery sidecar and leaves `out` UNCHANGED (ok=False)
    rather than overwriting the accumulated dataset."""
    if out.endswith(".parquet"):
        try:
            import pandas  # noqa: F401
            import pyarrow  # noqa: F401
        except ImportError:
            # fall back BEFORE the append merge, so every repo appends to the
            # same .jsonl instead of each one overwriting it
            out = out.rsplit(".", 1)[0] + ".jsonl"
            print(f"[warn] pandas/pyarrow not installed; writing {out} instead "
                  f"(pip install pandas pyarrow for parquet)", file=sys.stderr)
    if append and os.path.isfile(out) and rows:
        try:
            rows = _merge_with_existing(rows, out)
        except Exception as e:                                   # noqa: BLE001
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            recover = f"{out}.recover-{ts}.jsonl"
            with open(recover, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, default=str) + "\n")
            print(f"[error] couldn't read existing {out} to append ({e}); wrote "
                  f"THIS run to {recover} and LEFT {out} UNCHANGED. Merge manually.",
                  file=sys.stderr)
            return 0, False

    if out.endswith(".jsonl"):
        with open(out, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, default=str) + "\n")
        return len(rows), True
    if out.endswith(".csv"):
        _write_csv(rows, out)
        return len(rows), True
    try:
        import pandas as pd
        pd.DataFrame(rows).to_parquet(out, index=False)
    except Exception:
        alt = out.rsplit(".", 1)[0] + ".jsonl"
        with open(alt, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, default=str) + "\n")
        print(f"[warn] parquet unavailable; wrote {alt}", file=sys.stderr)
        return len(rows), True
    return len(rows), True


def _write_csv(rows: list[dict], out: str):
    """
    Write rows to CSV. None becomes an empty cell (so pandas reads it back as
    NaN, not the string 'None'); the header is the union of all keys so rows
    with different columns still line up. Uses the stdlib csv module — no
    pandas needed.
    """
    import csv
    if not rows:
        open(out, "w").close()
        return
    # stable column order: first row's keys, then any extras seen later
    cols = list(rows[0].keys())
    seen = set(cols)
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Stop / skip from the TUI: finished PRs are already in the checkpoint
        print("[stop] interrupted; finished PRs stay in the checkpoint, so a rerun resumes",
              file=sys.stderr, flush=True)
        sys.exit(130)

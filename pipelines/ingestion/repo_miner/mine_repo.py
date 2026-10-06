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
import json
import os
import re
import sys
import time
from collections import defaultdict, deque, Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import config
import gitio
import aggregate
import progress
from features import diff_stats, history, line_history, deterministic, scanners, deps_cve, llm_flags, api_contract, repo_local
from labels import build_labels, build_szz_index
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
_AGENT_MARKERS = ("copilot", "cursoragent", "devin-ai", "codex-bot", "sweep-ai",
                  "[bot]")


def _is_bot_identity(email: str, name: str) -> bool:
    s = f"{email} {name}".lower()
    return any(m in s for m in _BOT_MARKERS)


def _is_agent_identity(email: str, name: str) -> bool:
    e = (email or "").lower()
    n = (name or "").lower()
    return any(a in e for a in _AGENT_MARKERS) or "[bot]" in n


def _feat_head(repo: str, pr: dict) -> str | None:
    """The SHA whose git author is the PR author and whose diff is the PR's
    change. For a 2-parent merge that's the merged branch tip (p2); for a
    squash / normal merge it's the merge commit itself."""
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
    """
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

    def resolver(pr: dict, head_sha: str):
        if not do_rebase:
            return None                        # squash/normal/merge -> merge^1
        num = pr.get("number")
        if num is None:
            return None
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
                       base_resolver=None) -> dict[int, dict]:
    """
    For each PR, count OTHER PRs that were open at the moment it opened and
    touch the same files (and, more finely, the same lines). Point-in-time by
    construction: a PR Q counts for P only if Q opened at/before P and was still
    open when P opened. Files/lines come from the local clone via the corrected
    PR diff range — no extra API.

    Note: computed over the fetched PR set, so a small --max-prs cap undercounts
    (it only sees PRs in that window); a full mine sees all merged PRs. PRs that
    closed without merging aren't in the list and are not counted.
    """
    meta: list[dict | None] = []
    for pr in prs:
        created = pr.get("created_at")
        closed = pr.get("merged_at") or pr.get("closed_at")
        base, head = _pr_diff_range(repo, pr, base_resolver)
        num = pr.get("number")
        if not (created and closed and base and head and num is not None):
            meta.append(None)
            continue
        try:
            lines = gitio.changed_lines_by_file(repo, base, head)
        except Exception:
            lines = {}
        meta.append({
            "num": num, "created": _parse_ts(created), "close": _parse_ts(closed),
            "files": set(lines.keys()), "lines": lines,
        })

    out: dict[int, dict] = {}
    for i, p in enumerate(meta):
        if p is None:
            continue
        same_files = same_hunks = 0
        for j, q in enumerate(meta):
            if q is None or i == j:
                continue
            # was Q open at the instant P opened?
            if q["created"] <= p["created"] < q["close"]:
                shared = p["files"] & q["files"]
                if shared:
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
        "head_from_fork": (pr.get("head", {}) or {}).get("repo", {}) is not None and
                          (pr.get("head", {}).get("repo", {}) or {}).get("full_name")
                          != (pr.get("base", {}).get("repo", {}) or {}).get("full_name"),
        "author_association": pr.get("author_association"),
    }


def mine_pr(repo: str, pr: dict, api, default_branch: str,
            snapshot: datetime, st: config.Settings,
            author_pr_times: dict | None = None,
            concurrency: dict | None = None,
            ci_map: dict | None = None,
            szz_shas: dict | None = None,
            obs_cutoff: datetime | None = None,
            base_resolver=None) -> dict | None:
    # corrected PR diff/feature range (see _pr_diff_range) — immune to a stale
    # API base.sha pulling in unrelated main commits
    base_sha, head_sha = _pr_diff_range(repo, pr, base_resolver)
    merge_sha = pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha")
    if not base_sha or not head_sha:
        return None
    try:
        gitio.rev_parse(repo, base_sha)
        gitio.rev_parse(repo, head_sha)
    except gitio.GitError:
        return None  # clone too shallow / commit missing

    numstat = gitio.diff_numstat(repo, base_sha, head_sha)
    changed = [p for _, _, p in numstat]
    if not changed:
        return None

    # PR-author identity (mailmap-canonical) from the commit whose author is the
    # PR author — NOT the merge commit, whose author is the maintainer who merged
    feat_head = _feat_head(repo, pr) or head_sha
    head_email, head_name = _pr_author_identity(repo, feat_head)
    row: dict = {
        "repo": f"{api.owner}/{api.repo}" if api else os.path.basename(repo),
        "pr_number": pr.get("number"),
        "base_sha": base_sha, "head_sha": head_sha,
        "merged_at": pr.get("merged_at"),
        "merge_via": pr.get("merge_via", "github"),   # github | landed_commit
        "author_email": head_email,
    }

    # --- Source 4a/4b: diff stats + high-risk paths
    row.update(diff_stats.compute(repo, base_sha, head_sha, st))

    # --- Source 2: repo history (build index once, per-file, author, coupling)
    idx = history.build_repo_index(repo, base_sha, st, ci_map)
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

    row.update(aggregate.aggregate_per_file(per_file))
    row.update(history.cochange_features(changed, idx))
    row.update(history.test_expectation_features(changed, per_file, st))
    row.update(history.author_features(row["author_email"], changed, idx, st))
    row["repo_revert_base_rate"] = round(idx["repo_revert_base_rate"], 4)
    # FIX (2a): is this PR itself a bug fix (by title / head-commit keyword)
    head_subject = gitio._run(repo, "show", "-s", "--format=%s", feat_head,
                              check=False).strip()
    row["FIX"] = history.is_fix_pr(pr.get("title", ""), head_subject, st)
    # real PR-based author experience (not the commit-count proxy); point-in-time,
    # keyed on the PR-author identity (same identity as EXP)
    row["author_prs_merged_repo"] = _author_prs_merged_repo(
        head_email, pr.get("created_at") or pr.get("merged_at"), author_pr_times)
    # bot / agent flags from the git author of the code being merged (not the
    # maintainer who merged), so they agree with author_email / EXP
    row["author_is_bot"] = _is_bot_identity(head_email, head_name)
    row["ai_agent_authored"] = _is_agent_identity(head_email, head_name)

    # Kamei author experience (EXP/REXP/SEXP)
    row.update(history.experience_features(row["author_email"], changed, idx))
    # repo-relative percentiles (how big/spread this PR is for THIS repo)
    row.update(history.percentile_features(
        row["LA"] + row["LD"], row["NF"], row["Entropy"], idx))
    # integration staleness: how far the ORIGINAL API base lagged main at merge
    # time (also a contamination indicator — large values flag stale bases). Uses
    # the API base.sha deliberately, not the corrected point-in-time base.
    api_base = (pr.get("base") or {}).get("sha")
    row["base_behind_by"] = None
    parent = gitio.rev_parse_safe(repo, f"{merge_sha}^1") if merge_sha else None
    if api_base and parent:
        row["base_behind_by"] = gitio.commits_behind(repo, api_base, parent)

    # concurrent open PRs touching the same files / hunks (integration risk)
    conc = (concurrency or {}).get(pr.get("number"))
    row["concurrent_open_prs_same_files"] = conc["concurrent_open_prs_same_files"] if conc else None
    row["concurrent_open_prs_same_hunks"] = conc["concurrent_open_prs_same_hunks"] if conc else None

    # hotspots touched
    row.update(_hotspot_touch(per_file, idx, st))

    # --- Source 5: deterministic checks
    row.update(deterministic.compute(repo, base_sha, head_sha, st))

    # --- Source 4d: scanners (optional)
    row.update(scanners.compute(repo, base_sha, head_sha, changed, st))

    # --- Source 4e: deps/CVE (optional/network)
    row.update(deps_cve.compute(repo, base_sha, head_sha, changed, st))

    # --- Source 5: API contract diff (oasdiff/buf/graphql-inspector, optional)
    row.update(api_contract.compute(repo, base_sha, head_sha, changed, st))

    # --- Source 6: LLM flags (optional)
    # --- repo-local mined features (contracts/mined_features.schema.json):
    # path size here; the PR-history columns are filled per repo afterwards
    row["path_size_pctile"] = repo_local.path_size_pctile(repo, base_sha, numstat, st)
    reviews = None
    if api is not None and st.enable_review_history and pr.get("number") is not None:
        reviews = api.review_events(pr["number"], st.scrutiny_exclude_bot_reviews)
    row[repo_local.SIDECAR] = repo_local.sidecar(pr, numstat, reviews)

    # fetched here (worker thread); resolved in PR order by mine_repo()
    row["__llm__"] = llm_flags.fetch(repo, base_sha, head_sha, st)

    # --- Source 4c: PR metadata (needs the API PR object)
    md = _pr_metadata(pr)
    md["targets_default_branch"] = (pr.get("base", {}) or {}).get("ref") == default_branch
    row.update(md)

    # declared_hotfix: leak-free replacement for the dropped label_is_hotfix.
    # Was an urgent/hotfix LABEL present AT OPEN (point-in-time, from the
    # timeline API)? Optional (one timeline call per PR); None when disabled or
    # the timeline is unreadable. This is an author/triager prior set BEFORE
    # review — it does not read post-merge labels, so it can't leak the outcome.
    declared = None
    if api is not None and getattr(st, "enable_declared_labels", False):
        created = pr.get("created_at")
        if created and pr.get("number") is not None:
            labs = api.labels_at(pr["number"], created, st.declared_label_grace_s)
            if labs is not None:
                declared = any(("hotfix" in l or "rollback" in l) for l in labs)
    row["declared_hotfix"] = declared

    # --- Labels (revert/CI/hotfix key on the real merge commit; SZZ on the
    # corrected PR range). obs_cutoff bounds how far forward outcomes were seen.
    row.update(build_labels(repo, pr, changed, api, snapshot, st, szz_shas,
                            obs_cutoff=obs_cutoff, pr_base=base_sha, pr_head=head_sha))
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
    discovery = getattr(st, "pr_discovery", "api")

    if discovery == "git":
        # git-primary: discover merged PRs from the commit log; the API only
        # enriches each one (no closed-PR list pagination, uniform across merge
        # styles, discovery works offline).
        prs = _discover_prs_git(repo, owner, name, api, st, default_branch)
        note = "" if api is not None else \
            " (API off: no labels; metadata approximated from git)"
        print(f"[discover] git-primary: {len(prs)} merged PRs from commit "
              f"messages{note}", file=sys.stderr)
    else:
        if api is None:
            print("[warn] GitHub API disabled and pr_discovery=api: no PR list, "
                  "no labels. Use --pr-discovery git to mine from the clone, or "
                  "provide a token.", file=sys.stderr)
            return rows
        prs = list(api.merged_prs(
            resolver=_landed_resolver(repo, owner, name, default_branch)))
        print(f"[fetch] {len(prs)} merged PRs to process", file=sys.stderr)
        if getattr(api, "incomplete", False):
            print("[warn] PR list is TRUNCATED (an API page failed); label coverage "
                  "for this repo is partial — record this in the coverage analysis.",
                  file=sys.stderr)

    # required-status-check list (precise CI gating) — needs the API.
    # 403/None on public repos you don't own -> fall back to the name denylist.
    if api is not None:
        api.load_required_contexts(default_branch)
        if api.required_contexts:
            print(f"[ci] using {len(api.required_contexts)} required status checks "
                  f"from branch protection (precise)", file=sys.stderr)
        else:
            print("[ci] branch protection not readable; using built-in optional-check "
                  "denylist (heuristic)", file=sys.stderr)

    author_pr_times = _build_author_pr_times(repo, prs)
    base_resolver = _make_base_resolver(repo, api, prs, st)
    concurrency = _build_concurrency(repo, prs, base_resolver)
    ci_map = {}
    if st.enable_ci_history and api is not None:
        cache_path = os.path.join(st.ci_cache_dir, f"{owner}_{name}.json")
        print("[ci-history] building historical CI-fail store (cached after first run)...",
              file=sys.stderr, flush=True)
        ci_map = api.build_ci_conclusion_map(cache_path)
        print(f"[ci-history] {len(ci_map)} commits with a CI verdict", file=sys.stderr)
    szz_shas = None
    if st.enable_szz:
        szz_cache = os.path.join(st.ci_cache_dir, f"{owner}_{name}.szz.json")
        print("[szz] tracing fix-forward defects (slow; cached after first run)...",
              file=sys.stderr, flush=True)
        szz_shas = build_szz_index(repo, st, szz_cache)
        print(f"[szz] {len(szz_shas)} bug-introducing commits identified", file=sys.stderr)

    # crash-safe checkpoint + resume: rows are flushed to <out>.partial.jsonl as
    # they're computed, so a crash mid-repo doesn't lose hours of work; a re-run
    # skips PRs already in the checkpoint. main() removes it after a clean write.
    repo_key = f"{owner}/{name}"
    ckpt = (out_path + ".partial.jsonl") if out_path else None
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
    ckpt_fh = open(ckpt, "a", encoding="utf-8") if ckpt else None
    llm_pending: list[dict] = []

    def _work(pr: dict):
        t0 = time.monotonic()
        try:
            r = mine_pr(repo, pr, api, default_branch, snapshot, st,
                        author_pr_times, concurrency, ci_map, szz_shas,
                        obs_cutoff=obs_cutoff, base_resolver=base_resolver)
            return "ok", r, None, time.monotonic() - t0
        except gitio.GitError as e:
            return "git", None, e, time.monotonic() - t0
        except Exception as e:                          # noqa: BLE001
            return "exc", None, e, time.monotonic() - t0

    def _commit(pr_rows: list[dict]):
        for pr_row in pr_rows:
            rows.append(pr_row)
            if ckpt_fh:
                ckpt_fh.write(json.dumps(pr_row, default=str) + "\n")
                ckpt_fh.flush()

    # PRs are mined in parallel worker threads (mostly git subprocess time) but
    # consumed strictly in PR order, so row order, the checkpoint, and the LLM
    # circuit breaker behave exactly as in a sequential run.
    todo = [(i, pr) for i, pr in enumerate(prs, 1) if pr.get("number") not in done]
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
            kind, r, err, secs = fut.result()
            _fill()
            num = pr.get("number")
            print(f"[pr {i}/{len(prs)}] #{num} ({secs:.1f}s)", file=sys.stderr, flush=True)
            if kind == "git":
                skipped["git-error"] += 1
                print(f"[skip] PR #{num}: git: {err}", file=sys.stderr)
                progress.emit("pr", repo=repo_key, i=i, num=num, secs=secs, status="skip",
                              reason="git-error")
                continue
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
            try:
                r.update(llm_flags.resolve(fetched, st))
            except llm_flags.LLMUnavailable as e:
                print(f"[llm-breaker] TRIPPED at PR #{num}: {e}. Stopping this repo; "
                      f"{len(llm_pending)} held rows dropped, {len(rows)} clean rows "
                      f"kept in the checkpoint.", file=sys.stderr, flush=True)
                progress.emit("breaker", repo=repo_key, num=num, msg=str(e),
                              kept=len(rows), dropped=len(llm_pending))
                st.llm_breaker_tripped = True
                break
            progress.emit("pr", repo=repo_key, i=i, num=num, secs=secs, status="ok",
                          llm=fetched.get("status"), llm_secs=fetched.get("secs"))
            if fetched.get("status") == "fail":
                # LLM failed for this PR: hold the row until the next success
                # proves the failure was transient (then keep it with null
                # flags). If the breaker trips first, it's dropped.
                llm_pending.append(r)
            else:
                _commit(llm_pending + [r])
                llm_pending.clear()
        if not st.llm_breaker_tripped:
            # trailing LLM failures that never reached the breaker: keep them
            _commit(llm_pending)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if ckpt_fh:
            ckpt_fh.close()

    # repo-local PR-history features need every PR's outcome, so they're
    # computed once all rows are in (strictly point-in-time per row)
    if not st.llm_breaker_tripped:
        repo_local.compute_repo(rows, repo_local.release_tags(repo, st), st)
    for r in rows:
        r.pop(repo_local.SIDECAR, None)
        for col in repo_local.COLUMNS:
            r.setdefault(col, 0 if col == "similar_pr_count" else None)

    if skipped:
        total = sum(skipped.values())
        detail = ", ".join(f"{k}={v}" for k, v in skipped.most_common())
        print(f"[skips] {total}/{len(prs)} PRs produced no row — {detail}",
              file=sys.stderr)
    return rows


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
    ap = argparse.ArgumentParser(description="Extract Riffle v1 features from a clone.")
    ap.add_argument("--repo", default=None, help="path to local clone (required unless --llm-test)")
    ap.add_argument("--owner", default=None,
                    help="GitHub org/user (auto-detected from origin remote if omitted)")
    ap.add_argument("--name", default=None,
                    help="repo name (auto-detected from origin remote if omitted)")
    ap.add_argument("--out", default=None,
                    help="output .parquet or .jsonl (defaults to <owner>_<n>.parquet)")
    ap.add_argument("--max-prs", type=int, default=None)
    ap.add_argument("--llm", action="store_true",
                    help="enable LLM semantic flags (Source 6); default backend is headless "
                         "Claude Code (`claude -p`); LLM_BACKEND=api uses LLM_API_KEY")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-model", default=None,
                    help="model for the LLM flags, e.g. sonnet, opus, haiku (default: LLM_MODEL or sonnet)")
    ap.add_argument("--llm-test", action="store_true",
                    help="make one LLM call on a sample diff and report; no mining")
    ap.add_argument("--no-network", action="store_true",
                    help="disable GitHub API + OSV (git-only; no labels)")
    ap.add_argument("--no-scanners", action="store_true")
    ap.add_argument("--no-api-contract", action="store_true",
                    help="skip oasdiff/buf/graphql-inspector spec diffing")
    ap.add_argument("--ci-history", action="store_true",
                    help="build historical CI-fail-rate features (slow API pre-pass, cached)")
    ap.add_argument("--szz", action="store_true",
                    help="add the SZZ fix-tracing label (catches fix-forward defects; slow, cached)")
    ap.add_argument("--declared-labels", action="store_true",
                    help="add the declared_hotfix feature (at-open hotfix label via "
                         "the timeline API; 1 extra call per PR)")
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
    if args.llm:
        st.enable_llm = True
    if args.no_llm:
        st.enable_llm = False
    if args.no_scanners:
        st.enable_scanners = False
    if args.no_api_contract:
        st.enable_api_contract = False
    if args.ci_history:
        st.enable_ci_history = True
    if args.szz:
        st.enable_szz = True
    if args.declared_labels:
        st.enable_declared_labels = True
    st.pr_discovery = args.pr_discovery
    if args.llm_model:
        st.llm_model = args.llm_model
    if args.no_review_history:
        st.enable_review_history = False
    if args.workers is not None:
        st.workers = args.workers
    if args.llm_concurrency is not None:
        st.llm_concurrency = args.llm_concurrency
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
    progress.emit("repo_done", repo=f"{owner}/{name}", rows=len(rows), total=total,
                  status="ok" if ok else "write-failed")
    # remove the per-repo checkpoint only after a clean, complete write
    if ok and out:
        ckpt = out + ".partial.jsonl"
        try:
            if os.path.isfile(ckpt):
                os.remove(ckpt)
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
    main()

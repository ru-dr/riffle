"""
github_api.py — GitHub REST access for the things a bare clone can't give:
the list of merged PRs (with base/head SHAs), PR metadata/labels, and the CI
check conclusion on each merge commit. Used for PR-level features (Source 4c
metadata, concurrency) and for labels (revert/CI/hotfix).

Handles auth via GITHUB_TOKEN, primary rate-limit sleeping, and ETag-free
simple pagination. Kept dependency-free (urllib) on purpose.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

import config

API = "https://api.github.com"

# Auxiliary (non-gating) checks whose failure should NOT count as a build break.
# Matched as case-insensitive substrings of the check-run / workflow name. Used
# ONLY as the fallback when branch-protection required-checks aren't readable
# (the common case when mining public repos). Intentionally broad across common
# CI ecosystems, but deliberately EXCLUDES ambiguous checks that are often
# genuinely gating (lint, test, build, typecheck, visual-regression, bundlesize,
# pre-commit) — marking one of those optional would hide a real build failure.
_OPTIONAL_CHECK_MARKERS = (
    # security / SAST / SCA / secret scanners (report findings, rarely block merge)
    "codeql", "scorecard", "snyk", "sonarcloud", "sonarqube", "sonar",
    "semgrep", "trivy", "grype", "anchore", "checkmarx", "veracode", "fossa",
    "mend", "whitesource", "dependency review", "dependency-review", "dependabot",
    "gitleaks", "trufflehog", "secret scanning", "secret-scan", "ossar", "tfsec",
    "kics", "bandit", "osv-scanner", "clair", "datadog",
    # coverage / code-quality reporters
    "coverage", "codecov", "coveralls", "code climate", "codeclimate", "codacy",
    "codefactor", "deepsource", "lgtm", "codebeat", "qlty", "diff-cover",
    # deploy previews / hosting (preview builds, not merge gates)
    "netlify", "vercel", "cloudflare pages", "surge", "deploy preview",
    "amplify", "heroku review", "azure static web apps",
    # housekeeping / automation bots
    "labeler", "size-label", "size/", "stale", "greeting", "welcome",
    "auto-assign", "auto assign", "release-drafter", "release drafter",
    "all-contributors", "all contributors", "mergeable", "imgbot",
    "restyled", "review-assign", "triage",
    # sign-off / commit-message / PR-metadata checks (not build breaks)
    "cla/", "license/cla", "contributor-license", "cla-assistant", "cla check",
    "dco", "commitlint", "semantic-pull-request", "semantic pull request",
    "semantic pr", "pr title", "pr-title", "conventional", "lint-pr", "pr-lint",
    "title-check",
    # docs / changelog builders
    "readthedocs", "read the docs", "changelog", "docs preview", "netlify/docs",
)


def _is_optional_check(name: str) -> bool:
    return any(m in name for m in _OPTIONAL_CHECK_MARKERS)


def _reduce_conclusions(concls: list) -> str | None:
    """failure if any failed; success if some ran and none failed; else None.
    Treats timed_out / startup_failure / action_required as failures (they are
    red builds), and neutral / skipped / success as non-failures."""
    FAIL = {"failure", "timed_out", "startup_failure", "action_required"}
    OK = {"success", "neutral", "skipped"}
    if any(c in FAIL for c in concls):
        return "failure"
    if concls and all(c in OK for c in concls):
        return "success"
    return None


class GitHubAPI:
    def __init__(self, owner: str, repo: str, st: config.Settings):
        self.owner = owner
        self.repo = repo
        self.st = st
        self.token = st.github_token
        # Set of required-status-check contexts from branch protection, lowercased,
        # or None if not readable (needs admin; public repos you don't own 403).
        # When None, check classification falls back to the name denylist.
        self.required_contexts: set | None = None
        self._req_ctx_loaded = False
        # Set True if PR pagination stops early because a page failed to fetch
        # (rate limit exhausted, network) rather than reaching the real end, so
        # callers know the PR list may be truncated rather than complete.
        self.incomplete = False

    def load_required_contexts(self, branch: str) -> set | None:
        """
        Fetch the branch's required status-check contexts (the DEFINITIVE list of
        gating checks). Returns a lowercased set, or None if branch protection
        isn't readable — which is the normal case when mining public repos you
        don't own (the endpoint needs admin/owner permission and returns 403).
        Own-repo / installed-app runs get the precise list. Cached per instance.
        A permission 403 here is NOT a rate limit, so this uses a direct request
        (not _get) to avoid the rate-limit retry loop.
        """
        if self._req_ctx_loaded:
            return self.required_contexts
        self._req_ctx_loaded = True
        url = (f"{API}/repos/{self.owner}/{self.repo}/branches/"
               f"{urllib.parse.quote(branch)}/protection/required_status_checks")
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.st.request_timeout) as r:
                data = json.loads(r.read() or "null")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                json.JSONDecodeError, ValueError):
            self.required_contexts = None      # 403/404/etc -> fall back to denylist
            return None
        contexts = set((data or {}).get("contexts") or [])
        for c in ((data or {}).get("checks") or []):     # newer API shape
            if c.get("context"):
                contexts.add(c["context"])
        self.required_contexts = {c.lower() for c in contexts} if contexts else None
        return self.required_contexts

    def _is_optional(self, name: str) -> bool:
        """Is this check auxiliary (non-gating)? Uses the repo's real required-
        status-checks when we have them; otherwise the name denylist."""
        rc = self.required_contexts
        if rc is None:
            return _is_optional_check(name)             # heuristic fallback
        # required if the check name corresponds to any required context
        for ctx in rc:
            if name == ctx or ctx in name or name in ctx:
                return False
        return True

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "riffle-repo-data-extractor"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path: str, params: dict | None = None) -> tuple[int, object, dict]:
        url = f"{API}{path}"
        if params:
            q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
            url = f"{url}?{q}"
        req = urllib.request.Request(url, headers=self._headers())
        net_fails = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=self.st.request_timeout) as r:
                    hdrs = dict(r.headers)
                    return r.status, json.loads(r.read() or "null"), hdrs
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    remaining = e.headers.get("X-RateLimit-Remaining")
                    retry_after = e.headers.get("Retry-After")
                    reset = e.headers.get("X-RateLimit-Reset")
                    # secondary rate limit: honor Retry-After, then retry
                    if retry_after:
                        time.sleep(min(int(retry_after) + 1, 300))
                        continue
                    # primary rate limit (quota exhausted): wait for the reset
                    # window and retry, rather than silently returning no data
                    # (which would mislabel a whole block of PRs). Bounded per
                    # wait so a bad clock can't hang us forever.
                    if remaining == "0" and reset:
                        wait = max(1, int(reset) - int(time.time())) + 1
                        print(f"[api] rate limit hit; waiting {min(wait, 3600)}s",
                              file=sys.stderr, flush=True)
                        time.sleep(min(wait, 3600))
                        continue
                    # a 403 without rate-limit signals is a genuine permission
                    # error (private endpoint, forbidden) — report it, don't loop
                    if e.code == 403:
                        return 403, None, {}
                    time.sleep(5)
                    continue
                if e.code == 404:
                    return 404, None, {}
                if e.code >= 500:
                    net_fails += 1
                    if net_fails > 8:
                        print(f"[api] {e.code} persisted after {net_fails} retries "
                              f"on {path}; treating as unobserved", file=sys.stderr)
                        return e.code, None, {}
                    time.sleep(min(60, 2 ** net_fails))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                net_fails += 1
                if net_fails > 8:
                    print(f"[api] network error persisted after {net_fails} retries "
                          f"on {path} ({type(e).__name__}); treating as unobserved",
                          file=sys.stderr)
                    return 0, None, {}
                time.sleep(min(60, 2 ** net_fails))

    # -- merged PRs ---------------------------------------------------------
    def merged_prs(self, resolver=None) -> list[dict]:
        """
        Return merged PRs, most-recently-MERGED first, honoring max_prs.

        The list endpoint can't sort by merge date, so we fetch closed PRs in
        creation order (chronological — unlike 'updated', which drags in old PRs
        that got a recent comment), collect the merged ones, then sort by
        merged_at. A small over-fetch beyond the cap covers created-vs-merged
        reordering near the boundary.

        Landed-commit fallback: some repos never use GitHub's merge button (merge
        scripts, merge bots), so their PRs are only "closed". With
        st.merge_signal = "auto" (default), if under half of the first page is
        merged via GitHub, each closed-unmerged PR is passed to `resolver`, which
        links it to the commit(s) that landed it and returns a PR dict with
        merged_at / merge_commit_sha / base filled in (or None if unlinked).
        "github" disables the fallback; "landed" always uses it.
        """
        cutoff = None
        if getattr(self.st, "min_age_days", 0):
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.st.min_age_days)
                      ).strftime("%Y-%m-%dT%H:%M:%SZ")
        mode = getattr(self.st, "merge_signal", "auto")
        use_landed = resolver is not None and mode == "landed"
        collected: list[dict] = []
        page = 1
        # page limit scales with the cap (x3 covers closed-but-unmerged PRs);
        # skipping recent PRs means paging past them, so allow far more pages
        # (the loop still stops as soon as enough old-enough PRs are found)
        max_pages = (max(50, self.st.max_prs * 3 // 100 + 2)
                     if self.st.max_prs and not cutoff else 2000)
        while page <= max_pages:
            status, data, _ = self._get(
                f"/repos/{self.owner}/{self.repo}/pulls",
                {"state": "closed", "per_page": 100, "page": page,
                 "sort": "created", "direction": "desc"},
            )
            if status != 200:
                # an error (not a natural empty page) — the list is truncated,
                # not complete. Flag it so downstream label coverage isn't
                # silently computed over a partial PR set.
                self.incomplete = True
                print(f"[api] PR list fetch failed on page {page} "
                      f"(status {status}); PR list is TRUNCATED", file=sys.stderr)
                break
            if not data:
                break
            if page == 1 and mode == "auto" and resolver is not None:
                share = sum(1 for p in data if p.get("merged_at")) / max(len(data), 1)
                use_landed = share < 0.5
                if use_landed:
                    print(f"[merge] only {share:.0%} of recent PRs merged via GitHub; "
                          "linking closed PRs to their landed commits",
                          file=sys.stderr, flush=True)
            for pr in data:
                if pr.get("merged_at"):
                    pr["merge_via"] = "github"
                    cand = pr
                elif use_landed:
                    cand = resolver(pr)
                else:
                    cand = None
                if cand and cutoff and (cand.get("merged_at") or "") > cutoff:
                    cand = None          # merged too recently for mature labels
                if cand:
                    collected.append(cand)
            if self.st.max_prs and len(collected) >= self.st.max_prs + 100:
                break            # enough for a recency cap (+1 page buffer)
            if cutoff and page % 10 == 0:
                print(f"[fetch] page {page}: {len(collected)} PRs merged before "
                      f"{cutoff[:10]} so far", file=sys.stderr, flush=True)
            if len(data) < 100:
                break            # last page
            page += 1

        collected.sort(key=lambda p: p.get("merged_at") or "", reverse=True)
        if self.st.max_prs:
            collected = collected[: self.st.max_prs]
        return collected

    def pr_labels(self, pr: dict) -> list[str]:
        return [l["name"] for l in pr.get("labels", [])]

    def repo_merge_methods(self) -> dict:
        """Allowed merge methods from the repo settings (ONE call per repo). Keys
        merge/squash/rebase -> bool. Empty dict if unreadable, in which case the
        caller assumes every method is possible. Used to decide whether a repo is
        plausibly rebase-style before paying any per-PR commit-count calls."""
        status, data, _ = self._get(f"/repos/{self.owner}/{self.repo}")
        if status != 200 or not isinstance(data, dict):
            return {}
        return {
            "merge": bool(data.get("allow_merge_commit", True)),
            "squash": bool(data.get("allow_squash_merge", True)),
            "rebase": bool(data.get("allow_rebase_merge", True)),
        }

    def pr_commit_count(self, number: int) -> int | None:
        """Number of commits in a PR (the single-PR endpoint returns `commits`,
        which the list endpoint omits). Used to recover a rebase-merged PR's base
        as head~N. One call per PR; callers cache. None if unreadable."""
        status, data, _ = self._get(f"/repos/{self.owner}/{self.repo}/pulls/{number}")
        if status != 200 or not isinstance(data, dict):
            return None
        c = data.get("commits")
        return int(c) if isinstance(c, int) else None

    def review_events(self, number: int, exclude_bots: bool = True) -> dict | None:
        """
        Review activity on a PR, as timestamped events so callers can apply a
        point-in-time cutoff: {"comments": [iso...], "changes_requested": [iso...],
        "rounds": [iso...]}. A round is one submitted review (any state); review
        comments are inline comments. Two paginated calls per PR. None if the
        API is unreadable (the scrutiny feature then treats the PR as unknown).
        """
        def _is_bot(user) -> bool:
            user = user or {}
            return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")

        def _pages(kind: str):
            items = []
            for page in range(1, 11):                     # cap 1000 items per kind
                status, data, _ = self._get(
                    f"/repos/{self.owner}/{self.repo}/pulls/{number}/{kind}",
                    {"per_page": 100, "page": page})
                if status != 200 or not isinstance(data, list):
                    return None if page == 1 else items
                items.extend(data)
                if len(data) < 100:
                    break
            return items

        reviews = _pages("reviews")
        comments = _pages("comments")
        if reviews is None or comments is None:
            return None
        out = {"comments": [], "changes_requested": [], "rounds": []}
        for r in reviews:
            if exclude_bots and _is_bot(r.get("user")):
                continue
            t = r.get("submitted_at")
            if not t or r.get("state") == "PENDING":
                continue
            out["rounds"].append(t)
            if r.get("state") == "CHANGES_REQUESTED":
                out["changes_requested"].append(t)
        for c in comments:
            if exclude_bots and _is_bot(c.get("user")):
                continue
            if c.get("created_at"):
                out["comments"].append(c["created_at"])
        return out

    def fetch_pr(self, number: int) -> dict | None:
        """Full PR object from the single-PR endpoint, used to ENRICH git-primary
        discovery with fields the commit log can't supply: created_at, base ref,
        head/fork info, labels, author_association, draft, milestone, and the
        authoritative merged_at. None if unreadable (caller keeps git-derived
        values)."""
        status, data, _ = self._get(f"/repos/{self.owner}/{self.repo}/pulls/{number}")
        if status != 200 or not isinstance(data, dict):
            return None
        return data

    def labels_at(self, number: int, cutoff_iso: str,
                  grace_s: int = 120) -> set | None:
        """
        The set of label names present on the PR as of `cutoff_iso` (+ grace_s
        seconds), rebuilt POINT-IN-TIME from the issue timeline. Replays
        labeled/unlabeled events in chronological order and ignores any change
        after the cutoff, so reactive post-merge tags ('regression', 'reverted',
        a later 'hotfix') cannot leak into an at-open feature. The small grace
        catches labels applied by the author/triage at open (recorded a few
        seconds after creation) while staying days away from any outcome.
        Returns a lowercased set (possibly empty), or None if the timeline is
        unreadable. One paginated call per PR — callers gate it behind a flag.
        """
        from datetime import datetime, timedelta
        try:
            cutoff = (datetime.fromisoformat(cutoff_iso.replace("Z", "+00:00"))
                      + timedelta(seconds=max(0, grace_s)))
        except (ValueError, AttributeError):
            return None
        active: set = set()
        saw = False
        page = 1
        while page <= 20:                       # up to 2000 timeline events
            status, data, _ = self._get(
                f"/repos/{self.owner}/{self.repo}/issues/{number}/timeline",
                {"per_page": 100, "page": page})
            if status != 200 or not isinstance(data, list) or not data:
                break
            saw = True
            for ev in data:
                etype = ev.get("event")
                if etype not in ("labeled", "unlabeled"):
                    continue
                ts = ev.get("created_at")
                if not ts:
                    continue
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when > cutoff:
                    continue
                lab = (ev.get("label") or {}).get("name")
                if not lab:
                    continue
                if etype == "labeled":
                    active.add(lab.lower())
                else:
                    active.discard(lab.lower())
            if len(data) < 100:
                break
            page += 1
        return active if saw else None

    def check_conclusion(self, sha: str) -> dict:
        """
        CI conclusion on a commit, split into gating ('required') vs auxiliary
        ('optional') checks. Returns {"required": c, "optional": c} where each c
        is 'failure' | 'success' | None (None = no verdict observed, NOT a pass).
        Auxiliary checks (CodeQL, Scorecards, dependency-review, coverage bots,
        etc.) are classified as optional so they don't corrupt the build-failure
        label — only a real build/test failure should count as label_ci_fail.

        Reads BOTH the Checks API (GitHub Actions etc., paginated) AND the legacy
        commit-status API (Jenkins/Buildkite/CircleCI/Travis), merging them — a
        repo can gate on either, and reading only one would miss failures.
        """
        req: list = []
        opt: list = []
        saw_any = False

        # 1) Checks API (GitHub Actions, paginated — big matrices exceed one page)
        page = 1
        while page <= 20:                       # up to 2000 check-runs
            status, data, _ = self._get(
                f"/repos/{self.owner}/{self.repo}/commits/{sha}/check-runs",
                {"per_page": 100, "page": page})
            if status != 200 or not data:
                break
            runs = data.get("check_runs", [])
            if not runs:
                break
            saw_any = True
            for r in runs:
                c = r.get("conclusion")
                if not c:
                    continue
                name = (r.get("name") or "").lower()
                (opt if self._is_optional(name) else req).append(c)
            if len(runs) < 100:
                break
            page += 1

        # 2) legacy commit statuses — merge in, don't replace
        s2, d2, _ = self._get(
            f"/repos/{self.owner}/{self.repo}/commits/{sha}/status")
        if s2 == 200 and d2 and d2.get("statuses"):
            for stt in d2["statuses"]:
                state = stt.get("state")
                c = ("failure" if state in ("failure", "error")
                     else "success" if state == "success" else None)
                if not c:
                    continue
                name = (stt.get("context") or "").lower()
                (opt if self._is_optional(name) else req).append(c)
                saw_any = True

        if not saw_any:
            return {"required": None, "optional": None}
        return {"required": _reduce_conclusions(req),
                "optional": _reduce_conclusions(opt)}

    # -- persistent getter (waits through rate limits) ----------------------
    def _get_persistent(self, path: str, params: dict | None = None):
        """
        Like _get, but on a rate-limit (403/429) it waits ~60s and retries
        indefinitely instead of giving up — for the historical CI pre-pass,
        which is long-running and must survive rate-limit windows. Transient
        network errors get a short bounded backoff.
        """
        url = f"{API}{path}"
        if params:
            q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
            url = f"{url}?{q}"
        req = urllib.request.Request(url, headers=self._headers())
        net_fails = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=self.st.request_timeout) as r:
                    return r.status, json.loads(r.read() or "null")
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    print("[ci-history] rate limit reached; waiting 60s and retrying...",
                          file=__import__("sys").stderr, flush=True)
                    time.sleep(60)
                    continue
                if e.code == 404:
                    return 404, None
                if e.code >= 500:
                    time.sleep(5)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                net_fails += 1
                if net_fails > 10:
                    return 0, None
                time.sleep(min(60, 2 ** net_fails))

    def build_ci_conclusion_map(self, cache_path: str | None = None) -> dict:
        """
        {commit_sha: 'failure' | 'success'} for the repo's workflow runs, built
        from the Actions workflow-runs list endpoint (100 runs/call — far cheaper
        than one check-runs call per commit). Cached to `cache_path` as JSON, so
        the expensive pre-pass is paid once per repo. Only GitHub Actions is
        covered; commits with no runs are simply absent (feature = None there).
        """
        if cache_path and os.path.isfile(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                pass

        raw: dict[str, list] = {}
        page = 1
        while page <= 400:                          # safety cap: 40k runs
            status, data = self._get_persistent(
                f"/repos/{self.owner}/{self.repo}/actions/runs",
                {"per_page": 100, "page": page},
            )
            if status != 200 or not data:
                break
            runs = data.get("workflow_runs", [])
            if not runs:
                break
            for run in runs:
                sha = run.get("head_sha")
                if not sha:
                    continue
                # exclude pre-merge PR-branch runs: this feature is the POST-merge
                # CI-fail rate, so count only runs triggered after a commit landed
                # (push / merge_group / schedule), not 'pull_request' event runs
                # (which are the PR's own pre-merge iteration and would leak the
                # outcome into a post-merge signal).
                if run.get("event") == "pull_request":
                    continue
                # only gating build/test workflows count toward the historical
                # CI-fail rate — auxiliary checks (CodeQL etc.) are excluded, so
                # the feature matches label_ci_fail (required-only)
                if self._is_optional((run.get("name") or "").lower()):
                    continue
                raw.setdefault(sha, []).append(run.get("conclusion"))
            if len(runs) < 100:
                break
            page += 1
            print(f"[ci-history] fetched {sum(len(v) for v in raw.values())} runs "
                  f"across {len(raw)} commits...", file=__import__("sys").stderr, flush=True)

        ci: dict[str, str] = {}
        for sha, concls in raw.items():
            if any(c == "failure" for c in concls):
                ci[sha] = "failure"
            elif any(c == "success" for c in concls):
                ci[sha] = "success"
            # else: only neutral/skipped/cancelled -> leave out (unknown)

        if cache_path:
            try:
                os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as fh:
                    json.dump(ci, fh)
            except OSError:
                pass
        return ci

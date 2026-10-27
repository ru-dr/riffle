"""
github_api.py — GitHub REST access for the things a bare clone can't give:
the list of merged PRs (with base/head SHAs), PR metadata/labels, and the CI
check conclusion on each merge commit. Used for PR-level features (Source 4c
metadata, concurrency) and for labels (revert/CI/hotfix).

Handles auth via GITHUB_TOKEN, primary rate-limit sleeping, and ETag-free
simple pagination. Kept dependency-free (urllib) on purpose.
"""
from __future__ import annotations

import email.utils
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

import api_cache
import config
import progress
import runlog
import scope

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
    # preview / pre-release publishing jobs (a failed preview publish is not a broken build)
    "preview",
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


RATE_WAIT_FILE = os.path.join("ci_cache", ".rate_wait")     # the default place; a run keeps it under its ci_cache_dir


def _note_rate_wait(seconds: float, cache_dir: str | None = None) -> None:
    """Leave the time the quota comes back for the batch driver, which reports it to the team
    store ("waiting for GitHub until 14:05" rather than a PC that looks stuck). Lives under the
    run's ci_cache_dir (the cwd's ci_cache when none is given). Never raises."""
    path = os.path.join(cache_dir, ".rate_wait") if cache_dir else RATE_WAIT_FILE
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time() + seconds)))
    except OSError:
        pass


# One gate for the whole process: the quota belongs to the token, not to a thread, so when one
# thread is told to wait the others pause with it instead of each finding the limit in turn.
_GATE_LOCK = threading.Lock()
_GATE_UNTIL = 0.0


def _gate_wait() -> None:
    """Block while some thread is waiting out a rate limit."""
    with _GATE_LOCK:
        left = _GATE_UNTIL - time.time()
    if left > 0:
        time.sleep(min(left, 3600.0))


def _rate_sleep(seconds: float) -> None:
    """Wait out a rate limit, and hold every other request back for as long."""
    global _GATE_UNTIL
    until = time.time() + seconds
    with _GATE_LOCK:
        _GATE_UNTIL = max(_GATE_UNTIL, until)
    time.sleep(seconds)
    with _GATE_LOCK:
        if _GATE_UNTIL <= until:                 # nobody asked for a longer wait meanwhile
            _GATE_UNTIL = 0.0


class _TokenPool:
    """The tokens of one owner, with the time each one's quota comes back, per resource ("core" REST or
    "graphql": GitHub counts them apart). Shared by every thread of the process. Tokens are only ever
    named by their position in logs."""

    def __init__(self, tokens):
        self.tokens = list(tokens)
        self._until: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def pick(self, res: str) -> str | None:
        """The first token with quota left for `res`, or None when every one is out."""
        now = time.time()
        with self._lock:
            return next((t for t in self.tokens if self._until.get((t, res), 0.0) <= now), None)

    def block(self, token: str, res: str, seconds: float) -> None:
        with self._lock:
            self._until[(token, res)] = max(self._until.get((token, res), 0.0), time.time() + seconds)

    def wait_s(self, res: str) -> float:
        """Seconds until the first token is usable again."""
        now = time.time()
        with self._lock:
            return max(0.0, min(self._until.get((t, res), 0.0) for t in self.tokens) - now)

    def position(self, token: str) -> int:
        return self.tokens.index(token) + 1


_POOLS: dict[tuple, _TokenPool] = {}
_POOLS_LOCK = threading.Lock()


def _pool_for(tokens) -> _TokenPool:
    """One pool per set of tokens for the whole process, so every GitHubAPI shares what it learns."""
    key = tuple(tokens)
    with _POOLS_LOCK:
        return _POOLS.setdefault(key, _TokenPool(key))


MAX_SECONDARY_WAITS = 10        # Retry-After / secondary-limit waits in a row on one request before it is given up
MAX_NO_SIGNAL_429 = 5           # a 429 that says nothing about when to come back: this many backoffs


def _retry_after_s(value) -> float | None:
    """Retry-After as seconds: a number of seconds or an HTTP-date. None when absent or unreadable."""
    if value is None:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, IndexError):
        return None


def _rate_signal(e: urllib.error.HTTPError) -> tuple[str | None, float]:
    """What a 403/429 says about a rate limit: ("secondary", seconds) for Retry-After or a
    secondary-limit message in the body, ("primary", seconds) for an exhausted quota with its
    reset time, (None, 0) when it says nothing (a permission error, or a bare 429)."""
    hdrs = e.headers
    get = hdrs.get if hdrs is not None else (lambda k: None)
    ra = _retry_after_s(get("Retry-After"))
    if ra is not None:
        return "secondary", min(ra + 1, 300.0)
    if get("X-RateLimit-Remaining") == "0" and get("X-RateLimit-Reset"):
        try:
            return "primary", float(min(max(1, int(get("X-RateLimit-Reset")) - int(time.time())) + 1, 3600))
        except ValueError:
            pass
    if e.code == 403:
        try:
            msg = (e.read() or b"").decode("utf-8", "replace").lower()
        except Exception:                                    # noqa: BLE001
            msg = ""
        if "secondary rate limit" in msg or "abuse detection" in msg or "rate limit" in msg:
            return "secondary", 60.0
    return None, 0.0


class GitHubAPI:
    def __init__(self, owner: str, repo: str, st: config.Settings):
        self.owner = owner
        self.repo = repo
        self.st = st
        chain = list(dict.fromkeys(t for t in [st.github_token, *getattr(st, "github_tokens", ())] if t))
        self.token = chain[0] if chain else None
        self._pool = _pool_for(chain) if len(chain) > 1 else None      # None: one token, the plain path
        # Set of required-status-check contexts from branch protection, lowercased,
        # or None if not readable (needs admin; public repos you don't own 403).
        # When None, check classification falls back to the name denylist.
        self.required_contexts: set | None = None
        self._req_ctx_loaded = False
        # Set True if PR pagination stops early because a page failed to fetch
        # (rate limit exhausted, network) rather than reaching the real end, so
        # callers know the PR list may be truncated rather than complete.
        self.incomplete = False
        # closed-unmerged PRs found by merged_prs() (enable_merge_outcome only)
        self.unmerged: list[dict] = []
        self.merged_span: tuple[str, str] | None = None   # legacy mode: (oldest, newest) merge time of the kept merged PRs
        self.use_landed = False
        self._user_cache: dict[str, str | None] = {}
        self._gql_blocked_until = 0.0                                     # GraphQL quota gone until then
        self._gql_lock = threading.Lock()
        self._user_inflight: dict[str, threading.Event] = {}
        self._disk = api_cache.open_for(st.ci_cache_dir, owner, repo)     # None when switched off
        self._review_cache: dict[tuple[int, bool], dict] = {}
        self._user_lock = threading.Lock()

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
        except (OSError, http.client.HTTPException, json.JSONDecodeError, ValueError):
            self.required_contexts = None      # 403/404, cut body, etc -> fall back to denylist
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

    def _headers(self, token: str | None = None) -> dict:
        h = {"Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "riffle-repo-data-extractor"}
        token = token or self.token
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _acquire(self, res: str) -> str | None:
        """The token for the next request. With several, the first that has quota left; when every one is
        out, wait for the first to come back (all threads wait together, see _rate_sleep)."""
        pool = getattr(self, "_pool", None)
        if pool is None:
            return self.token
        while True:
            tok = pool.pick(res)
            if tok:
                return tok
            wait = pool.wait_s(res) + 1
            print(f"[api] all {len(pool.tokens)} tokens are out of quota; waiting {int(wait)}s", file=sys.stderr, flush=True)
            runlog.warn("api", "every token is out of quota; waiting for the first reset", tokens=len(pool.tokens),
                        resource=res, wait_s=int(wait))
            progress.emit("rate_limit", repo=f"{self.owner}/{self.repo}", wait=int(wait))
            _note_rate_wait(wait, self.st.ci_cache_dir)
            _rate_sleep(wait)

    def _spent(self, tok: str | None, res: str, wait: float, path: str) -> bool:
        """A token was told its quota is gone. With several tokens: block it, say which, and return True so the
        caller retries with the next one. With one: False, and the caller waits as before."""
        pool = getattr(self, "_pool", None)
        if pool is None or tok is None:
            return False
        pool.block(tok, res, wait)
        runlog.warn("api", "token quota used up; using the next token", path=path, token=pool.position(tok),
                    of=len(pool.tokens), resource=res, back_in_s=int(wait))
        return True

    def _note_remaining(self, tok: str | None, res: str, headers) -> None:
        """Switch before the 403: a response that says 0 requests are left blocks that token until its reset."""
        pool = getattr(self, "_pool", None)
        if pool is None or tok is None:
            return
        try:
            if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
                pool.block(tok, res, max(1, int(headers.get("X-RateLimit-Reset")) - int(time.time())) + 1)
        except (ValueError, TypeError, AttributeError):
            pass

    def _get(self, path: str, params: dict | None = None,
             body: dict | None = None) -> tuple[int, object, dict]:
        """GET `path` (or POST `body` as JSON, for GraphQL), with rate-limit
        waits and retries."""
        url = f"{API}{path}"
        if params:
            q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
            url = f"{url}?{q}"
        data = json.dumps(body).encode() if body is not None else None
        res = "graphql" if path == "/graphql" else "core"
        net_fails = 0
        waits = 0
        while True:
            _gate_wait()
            tok = self._acquire(res)
            req = urllib.request.Request(url, headers=self._headers(tok), data=data,
                                         method="POST" if data is not None else "GET")
            try:
                with runlog.span("api", "request", path=path, attempt=net_fails + 1,
                                 slow_s=runlog.SLOW_CALL_S, err_level="DEBUG"):
                    with urllib.request.urlopen(req, timeout=self.st.request_timeout) as r:
                        self._note_remaining(tok, res, r.headers)
                        hdrs = dict(r.headers)
                        runlog.debug("api", "response", path=path, status=r.status,
                                     ratelimit_remaining=hdrs.get("X-RateLimit-Remaining"))
                        return r.status, json.loads(r.read() or "null"), hdrs
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    kind, wait = _rate_signal(e)
                    if kind == "secondary":
                        waits += 1
                        if waits > MAX_SECONDARY_WAITS:
                            runlog.warn("api", "secondary rate limit persisted; treating as unobserved",
                                        path=path, code=e.code)
                            return e.code, None, {}
                        runlog.warn("api", "secondary rate limit; waiting", path=path, code=e.code,
                                    wait_s=int(wait))
                        progress.emit("rate_limit", repo=f"{self.owner}/{self.repo}", wait=int(wait))
                        _rate_sleep(wait)
                        continue
                    # primary rate limit (quota exhausted): wait for the reset
                    # window and retry, rather than silently returning no data
                    # (which would mislabel a whole block of PRs). Bounded per
                    # wait so a bad clock can't hang us forever.
                    if kind == "primary":
                        if self._spent(tok, res, wait, path):
                            continue
                        print(f"[api] rate limit hit; waiting {int(wait)}s", file=sys.stderr, flush=True)
                        runlog.warn("api", "primary rate limit; waiting for reset", path=path,
                                    code=e.code, wait_s=int(wait))
                        progress.emit("rate_limit", repo=f"{self.owner}/{self.repo}", wait=int(wait))
                        _note_rate_wait(wait, self.st.ci_cache_dir)
                        _rate_sleep(wait)
                        continue
                    # a 403 without rate-limit signals is a genuine permission
                    # error (private endpoint, forbidden) — report it, don't loop
                    if e.code == 403:
                        runlog.warn("api", "403 without rate-limit headers; treating as unobserved",
                                    path=path)
                        return 403, None, {}
                    waits += 1                      # a bare 429: back off, a bounded number of times
                    if waits > MAX_NO_SIGNAL_429:
                        runlog.warn("api", "429 without Retry-After persisted; treating as unobserved",
                                    path=path)
                        return 429, None, {}
                    runlog.warn("api", "429 without Retry-After; backing off", path=path, retry=waits,
                                wait_s=min(60, 5 * 2 ** (waits - 1)))
                    time.sleep(min(60, 5 * 2 ** (waits - 1)))
                    continue
                if e.code == 404:
                    return 404, None, {}
                if e.code >= 500:
                    net_fails += 1
                    runlog.warn("api", "server error; backing off", path=path, code=e.code,
                                retry=net_fails, wait_s=min(60, 2 ** net_fails))
                    if net_fails > (2 if path == "/graphql" else 8):
                        print(f"[api] {e.code} persisted after {net_fails} retries "
                              f"on {path}; treating as unobserved", file=sys.stderr)
                        return e.code, None, {}
                    time.sleep(min(60, 2 ** net_fails))
                    continue
                # any other 4xx (401 unauthenticated, 422 ...) is permanent for
                # this request: report it as unobserved, never kill the run
                runlog.warn("api", "permanent client error; treating as unobserved", path=path,
                            code=e.code)
                return e.code, None, {}
            except (urllib.error.URLError, OSError, http.client.HTTPException,
                    json.JSONDecodeError) as e:      # incl. IncompleteRead, resets, cut bodies
                net_fails += 1
                runlog.warn("api", "network error; backing off", path=path, exc=type(e).__name__,
                            retry=net_fails, wait_s=min(60, 2 ** net_fails))
                if net_fails > (2 if path == "/graphql" else 8):      # every GraphQL caller has a REST fallback: give up fast
                    print(f"[api] network error persisted after {net_fails} retries "
                          f"on {path} ({type(e).__name__}); treating as unobserved",
                          file=sys.stderr)
                    return 0, None, {}
                time.sleep(min(60, 2 ** net_fails))

    # -- merged PRs ---------------------------------------------------------
    def window_prs(self, resolver=None) -> list[dict]:
        """
        Date-window selection (proposal 1.2): every closed PR CREATED inside
        [st.window_from, st.window_to], merged and unmerged together, so every
        repo covers the same span. With st.max_prs, the newest contiguous block
        of that many PRs is kept (by creation time), not the newest merged ones.
        Drafts and PRs merged or closed too recently for mature labels
        (st.min_age_days) are left out and don't count toward the cap.

        Also collects the lead-in (proposal 1.4): closed PRs created in the
        st.lead_in_days before the kept block, as metadata only. They are not
        mined; they give the first PRs in the block a history.

        Returns the merged PRs (like merged_prs); the unmerged ones are left in
        self.unmerged, the lead-in in self.lead_in, and the block's first
        creation time in self.block_start.
        """
        st = self.st
        w_from = st.window_from + ("T00:00:00Z" if len(st.window_from) == 10 else "")
        w_to = st.window_to + ("T23:59:59Z" if len(st.window_to) == 10 else "")
        cutoff = ((datetime.now(timezone.utc) - timedelta(days=st.min_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                  if st.min_age_days else None)
        mode = getattr(st, "merge_signal", "auto")
        use_landed = resolver is not None and mode == "landed"
        kept: list[dict] = []          # eligible PRs in the window, newest created first
        counted = 0                    # toward max_prs: every kept PR, or training PRs only
        lead: list[dict] = []
        block_start = None
        lead_days = timedelta(days=max(0, st.lead_in_days))
        page = 1
        while True:
            status, data, _ = self._get(
                f"/repos/{self.owner}/{self.repo}/pulls",
                {"state": "closed", "per_page": 100, "page": page,
                 "sort": "created", "direction": "desc"})
            if status != 200:
                self.incomplete = True
                print(f"[api] PR list fetch failed on page {page} (status {status}); "
                      f"PR list is TRUNCATED", file=sys.stderr)
                break
            if not data:
                break
            if page == 1 and mode == "auto" and resolver is not None:
                share = sum(1 for p in data if p.get("merged_at")) / max(len(data), 1)
                closed = [p for p in data if not p.get("merged_at")]
                linked = sum(1 for p in closed if resolver(p)) if share < 0.5 else 0
                use_landed = share < 0.5 and linked >= 0.5 * max(len(closed), 1)
                if use_landed:
                    print(f"[merge] only {share:.0%} of recent PRs merged via GitHub; linking "
                          f"closed PRs to their landed commits", file=sys.stderr, flush=True)
            done = False
            for pr in data:
                created = pr.get("created_at") or ""
                if created > w_to:
                    continue
                start = block_start or w_from
                lead_from = (datetime.fromisoformat(start.replace("Z", "+00:00")) - lead_days
                             ).strftime("%Y-%m-%dT%H:%M:%SZ")
                if created < start:
                    if created >= lead_from:
                        if not pr.get("draft"):
                            lead.append(pr)
                        continue
                    done = True
                    break
                if pr.get("draft"):
                    continue
                if pr.get("merged_at"):
                    pr["merge_via"] = "github"
                    cand = pr
                elif use_landed:
                    cand = resolver(pr)
                elif st.enable_merge_outcome and not (resolver is not None and resolver(pr)):
                    pr["merge_via"], pr["_unmerged"] = None, True
                    cand = pr
                else:
                    cand = None
                when = (cand or {}).get("merged_at") or (cand or {}).get("closed_at") or ""
                if cand and cutoff and when > cutoff:
                    cand = None              # outcome too recent to be mature
                if cand:
                    kept.append(cand)
                    if st.cap_on == "training":
                        counted += scope.pr_list_is_training(pr, f"{self.owner}/{self.repo}",
                                                             st.llm_sample_pct)
                    else:
                        counted += 1
                    if st.max_prs and counted >= st.max_prs and block_start is None:
                        block_start = created    # cap reached: the block starts here
            progress.emit("stage", repo=f"{self.owner}/{self.repo}", stage=f"fetching PRs p{page}")
            if page % 10 == 0:
                print(f"[fetch] page {page}: {len(kept)} PRs in the window, {len(lead)} lead-in",
                      file=sys.stderr, flush=True)
            if done or len(data) < 100:
                break
            page += 1
        if block_start:
            kept = [p for p in kept if (p.get("created_at") or "") >= block_start]
        self.block_start = block_start or (min((p.get("created_at") or "" for p in kept), default=None))
        if use_landed and st.enable_merge_outcome:
            print("[merge-outcome] landed-commit repo: closed-unmerged PRs skipped "
                  "(not-merged is ambiguous here)", file=sys.stderr)
        self.unmerged = [p for p in kept if p.get("_unmerged")]
        if st.max_unmerged is not None and len(self.unmerged) > st.max_unmerged:
            self.unmerged = sorted(self.unmerged, key=lambda p: p.get("created_at") or "",
                                   reverse=True)[:st.max_unmerged]
        self.lead_in = lead
        self.use_landed = use_landed
        merged = [p for p in kept if not p.get("_unmerged")]
        merged.sort(key=lambda p: p.get("merged_at") or "", reverse=True)
        train = sum(scope.pr_list_is_training(p, f"{self.owner}/{self.repo}", st.llm_sample_pct)
                    for p in kept)
        print(f"[window] {w_from[:10]}..{w_to[:10]}: {len(merged)} merged + {len(self.unmerged)} "
              f"unmerged PRs ({train} training PRs; block from {(self.block_start or '-')[:10]}), "
              f"{len(lead)} lead-in", file=sys.stderr, flush=True)
        return merged

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
        # closed-unmerged PRs for the merge-outcome target (ADR 0009). Skipped in
        # landed-commit repos: there a "closed" PR may have landed outside GitHub,
        # so not-merged can't be told apart from merged-elsewhere.
        unmerged: list[dict] = []
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
                # A low GitHub-merge share alone also fits a repo flooded with
                # rejected (often AI) PRs. Landed-commit mode needs most of the
                # closed-unmerged PRs to actually link to a landed commit.
                closed = [p for p in data if not p.get("merged_at")]
                linked = sum(1 for p in closed if resolver(p)) if share < 0.5 else 0
                use_landed = share < 0.5 and linked >= 0.5 * max(len(closed), 1)
                if use_landed:
                    print(f"[merge] only {share:.0%} of recent PRs merged via GitHub and "
                          f"{linked}/{len(closed)} closed PRs link to landed commits; "
                          "linking closed PRs to their landed commits",
                          file=sys.stderr, flush=True)
                elif share < 0.5:
                    print(f"[merge] {share:.0%} of recent PRs merged via GitHub, but only "
                          f"{linked}/{len(closed)} closed PRs link to landed commits: "
                          "treating them as not merged", file=sys.stderr, flush=True)
            for pr in data:
                if pr.get("merged_at"):
                    pr["merge_via"] = "github"
                    cand = pr
                elif use_landed:
                    cand = resolver(pr)
                else:
                    cand = None
                    # not merged via GitHub: a not-merged row, unless it is a
                    # draft (never offered for review) or it landed another way
                    # (a commit on the default branch links to it)
                    if (self.st.enable_merge_outcome and not pr.get("draft")
                            and not (cutoff and (pr.get("closed_at") or "") > cutoff)
                            and not (resolver is not None and resolver(pr))):
                        unmerged.append(pr)
                if cand and cutoff and (cand.get("merged_at") or "") > cutoff:
                    cand = None          # merged too recently for mature labels
                if cand:
                    collected.append(cand)
            if self.st.max_prs and len(collected) >= self.st.max_prs + 100:
                break            # enough for a recency cap (+1 page buffer)
            progress.emit("stage", repo=f"{self.owner}/{self.repo}",
                          stage=f"fetching PRs p{page}")
            if cutoff and page % 10 == 0:
                print(f"[fetch] page {page}: {len(collected)} PRs merged before "
                      f"{cutoff[:10]} so far", file=sys.stderr, flush=True)
            if len(data) < 100:
                break            # last page
            page += 1

        collected.sort(key=lambda p: p.get("merged_at") or "", reverse=True)
        if self.st.max_prs:
            collected = collected[: self.st.max_prs]
        if use_landed and self.st.enable_merge_outcome:
            print("[merge-outcome] landed-commit repo: closed-unmerged PRs skipped "
                  "(not-merged is ambiguous here)", file=sys.stderr)
        # keep the unmerged PRs closed in the same span as the kept merged ones (from the oldest kept merge to the newest),
        # so the merged / not-merged mix is the repo's real one. The cap only shortens the span of merged PRs: unmerged PRs
        # closed before or after it would have no merged counterparts and skew the mix (a repo with 20 merged PRs in three
        # weeks came out with 168 unmerged ones from the three months before).
        self.merged_span = None
        if collected:
            merged_times = [p.get("merged_at") or "" for p in collected]
            self.merged_span = (min(merged_times), max(merged_times))
            unmerged = [p for p in unmerged if self.merged_span[0] <= (p.get("closed_at") or "") <= self.merged_span[1]]
        cap = self.st.max_unmerged
        if cap is not None and len(unmerged) > cap:
            print(f"[merge-outcome] {len(unmerged)} closed-unmerged PRs in the window; "
                  f"keeping the newest {cap} (--max-unmerged)", file=sys.stderr)
            unmerged = sorted(unmerged, key=lambda p: p.get("created_at") or "",
                              reverse=True)[:cap]
        for p in unmerged:
            p["merge_via"] = None
            p["_unmerged"] = True
        self.unmerged = unmerged
        self.use_landed = use_landed
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
        hit = self._disk.get(f"cc:{number}") if self._disk else None
        if isinstance(hit, int):
            return hit
        status, data, _ = self._get(f"/repos/{self.owner}/{self.repo}/pulls/{number}")
        if status != 200 or not isinstance(data, dict):
            return None
        c = data.get("commits")
        if isinstance(c, int) and self._disk:
            self._disk.put(f"cc:{number}", c)
        return int(c) if isinstance(c, int) else None

    GQL_RATE_TYPES = ("RATE_LIMITED", "RATE_LIMIT")

    def _gql(self, query: str, strict: bool = False) -> dict | None:
        """POST one GraphQL query. The `data` object, or None when it cannot be used: no token, the
        quota is gone (then GraphQL is left alone until it resets, and callers read through REST), a
        failed call or an errors-only answer. GitHub answers an exhausted quota with HTTP 200 and an
        error of type RATE_LIMIT (or RATE_LIMITED), never with a 403. `strict`: any error at all gives
        None (a query whose parts must all be there); otherwise a part that failed is null in the
        data and the caller sees which."""
        if not self.token or time.time() < self._gql_blocked_until:
            return None
        status, data, _ = self._get("/graphql", body={"query": query})
        if status != 200 or not isinstance(data, dict):
            return None
        errs = data.get("errors") or []
        if any(e.get("type") in self.GQL_RATE_TYPES or e.get("code") == "graphql_rate_limit" for e in errs):
            until = self._gql_reset_time()
            with self._gql_lock:
                self._gql_blocked_until = max(self._gql_blocked_until, until)     # threads race here: never move it back
            runlog.warn("api", "graphql quota used up; reading through REST until it resets",
                        wait_s=int(until - time.time()))
            return None
        if errs and strict:
            return None
        return data.get("data") if isinstance(data.get("data"), dict) else None

    def _gql_reset_time(self) -> float:
        """When the GraphQL quota comes back (the rate-limit endpoint is free), else two minutes on."""
        try:
            status, data, _ = self._get("/rate_limit")
            reset = ((data or {}).get("resources") or {}).get("graphql", {}).get("reset")
            if status == 200 and isinstance(reset, (int, float)) and reset > time.time():
                return float(reset)
        except Exception:                                   # noqa: BLE001
            pass
        return time.time() + 120.0

    def commit_counts(self, numbers: list[int], batch: int = 50) -> dict[int, int | None]:
        """{pr_number: commit count} from GraphQL, `batch` PRs per call (one point of the
        GraphQL budget, which is separate from REST's), instead of one REST call per PR.
        None for a PR that could not be read; callers fall back to `pr_commit_count`."""
        out: dict[int, int | None] = {n: None for n in numbers}
        if self._disk:
            for n in numbers:
                hit = self._disk.get(f"cc:{n}")
                if isinstance(hit, int):
                    out[n] = hit
            numbers = [n for n in numbers if out[n] is None]
        if not self.token:
            return out                                # GraphQL needs auth
        for i in range(0, len(numbers), batch):
            part = numbers[i:i + batch]
            fields = " ".join(f"p{n}: pullRequest(number: {int(n)}) {{ commits {{ totalCount }} }}"
                              for n in part)
            q = (f'query {{ repository(owner: {json.dumps(self.owner)}, '
                 f'name: {json.dumps(self.repo)}) {{ {fields} }} }}')
            repo_data = (self._gql(q) or {}).get("repository")
            for n in part:
                node = (repo_data or {}).get(f"p{n}")
                c = ((node or {}).get("commits") or {}).get("totalCount")
                if isinstance(c, int):
                    out[n] = c
                    if self._disk:
                        self._disk.put(f"cc:{n}", c)
        return out

    def review_events(self, number: int, exclude_bots: bool = True) -> dict | None:
        """
        Review activity on a PR, as timestamped events so callers can apply a
        point-in-time cutoff: {"comments": [iso...], "changes_requested": [iso...],
        "rounds": [iso...]}. A round is one submitted review (any state); review
        comments are inline comments. Two paginated calls per PR. None if the
        API is unreadable (the scrutiny feature then treats the PR as unknown).
        """
        with self._user_lock:
            hit = self._review_cache.get((number, exclude_bots))
        if hit is None and self._disk:
            hit = self._disk.get(f"rv:{number}:{int(exclude_bots)}")
        if hit is not None:
            return {k: list(v) for k, v in hit.items()}

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
                    return None                           # a page that failed: the list is cut, not whole
                items.extend(data)
                if len(data) < 100:
                    return items
            return None                                   # a full tenth page: more may follow, so unknown, not cut

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
        out = {k: sorted(v) for k, v in out.items()}
        if self._disk:
            self._disk.put(f"rv:{number}:{int(exclude_bots)}", out)
        return out

    def review_events_batch(self, numbers: list[int], exclude_bots: bool = True,
                            batch: int = 10, reserve: int = 0) -> dict[int, dict | None]:
        """`review_events` for many PRs through GraphQL: `batch` PRs per call instead of two REST
        calls per PR. The same dict as `review_events` gives, built the same way. A PR whose
        reviews, threads or comments do not fit one page (100), or whose batch failed, is None
        here: callers use `review_events` (REST) for it, so nothing is ever approximated. GraphQL
        charges by the pages a query could return, not the ones it does (reviews, then a thread's
        comments for each of up to `first` threads), so the limits are small: about 0.2 of the
        hourly 5,000 points a PR, against 1 with the full 100s. It stops once less than `reserve`
        points are left, so a bulk read cannot spend what the checks need."""
        out: dict[int, dict | None] = {n: None for n in numbers}
        if not self.token:
            return out

        def _is_bot(a) -> bool:
            return bool(a) and (a.get("__typename") == "Bot" or str(a.get("login", "")).endswith("[bot]"))

        for i in range(0, len(numbers), batch):
            part = numbers[i:i + batch]
            fields = " ".join(
                f"p{n}: pullRequest(number: {int(n)}) {{ "
                f"reviews(first: 30) {{ pageInfo {{ hasNextPage }} nodes {{ state submittedAt "
                f"author {{ __typename login }} }} }} "
                f"reviewThreads(first: 20) {{ pageInfo {{ hasNextPage }} nodes {{ "
                f"comments(first: 50) {{ pageInfo {{ hasNextPage }} nodes {{ createdAt "
                f"author {{ __typename login }} }} }} }} }} }}"
                for n in part)
            q = (f'query {{ rateLimit {{ remaining }} repository(owner: {json.dumps(self.owner)}, '
                 f'name: {json.dumps(self.repo)}) {{ {fields} }} }}')
            data = self._gql(q)
            if data is None:
                if self._gql_blocked_until > time.time():
                    break                                  # the quota is gone: REST reads the rest
                continue
            left = (data.get("rateLimit") or {}).get("remaining")
            low = bool(reserve) and isinstance(left, int) and left < reserve     # this batch is read, the next ones are not
            repo_data = data.get("repository")
            for n in part:
                node = (repo_data or {}).get(f"p{n}")
                if not node:
                    continue
                rv, th = node.get("reviews") or {}, node.get("reviewThreads") or {}
                threads = th.get("nodes") or []
                if ((rv.get("pageInfo") or {}).get("hasNextPage") or (th.get("pageInfo") or {}).get("hasNextPage")
                        or any(((t.get("comments") or {}).get("pageInfo") or {}).get("hasNextPage") for t in threads)):
                    continue                                  # too big for one page: REST reads it whole
                ev = {"comments": [], "changes_requested": [], "rounds": []}
                for r in rv.get("nodes") or []:
                    if exclude_bots and _is_bot(r.get("author")):
                        continue
                    t = r.get("submittedAt")
                    if not t or r.get("state") == "PENDING":
                        continue
                    ev["rounds"].append(t)
                    if r.get("state") == "CHANGES_REQUESTED":
                        ev["changes_requested"].append(t)
                for t in threads:
                    for c in (t.get("comments") or {}).get("nodes") or []:
                        if exclude_bots and _is_bot(c.get("author")):
                            continue
                        if c.get("createdAt"):
                            ev["comments"].append(c["createdAt"])
                out[n] = {k: sorted(v) for k, v in ev.items()}      # chronological, as review_events returns
            if low:
                break
        return out

    def prefetch_reviews(self, numbers: list[int], exclude_bots: bool = True) -> int:
        """Read the review activity of `numbers` through GraphQL into a cache that `review_events`
        answers from, so a repo's PRs cost a few GraphQL calls rather than two REST calls each.
        PRs GraphQL could not read whole are left out and go through REST as before."""
        todo = [n for n in numbers if not (self._disk and self._disk.get(f"rv:{n}:{int(exclude_bots)}") is not None)]
        got = {n: ev for n, ev in self.review_events_batch(todo, exclude_bots, reserve=2000).items() if ev is not None}
        if self._disk:
            for n, ev in got.items():
                self._disk.put(f"rv:{n}:{int(exclude_bots)}", ev)
        with self._user_lock:
            for n, ev in got.items():
                self._review_cache[(n, exclude_bots)] = ev
        return len(got)

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
            if status != 200 or not isinstance(data, list):
                return None                     # a page that failed: the replay would miss events
            if not data:
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
        else:
            return None                         # 2000 events and more may follow: unknown, not cut
        return active if saw else None

    def open_prs(self, created_from: str, created_to: str) -> list[dict]:
        """Open PRs created in [created_from, created_to] (ISO, UTC), newest
        first. Stops paging once PRs are older than the window."""
        out: list[dict] = []
        for page in range(1, 101):
            status, data, _ = self._get(
                f"/repos/{self.owner}/{self.repo}/pulls",
                {"state": "open", "per_page": 100, "page": page,
                 "sort": "created", "direction": "desc"})
            if status != 200 or not data:
                break
            for pr in data:
                c = pr.get("created_at") or ""
                if created_from <= c <= created_to and not pr.get("draft"):
                    out.append(pr)
            if len(data) < 100 or (data[-1].get("created_at") or "") < created_from:
                break
        return out

    def user_created_at(self, login: str | None) -> str | None:
        """An account's creation time (static, so point-in-time safe). One
        call per distinct login, cached; None if unreadable or a bot app."""
        if not login:
            return None
        while True:
            with self._user_lock:
                if login in self._user_cache:
                    return self._user_cache[login]
                wait_for = self._user_inflight.get(login)
                if wait_for is None:
                    self._user_inflight[login] = threading.Event()      # this thread reads it; the others wait
                    break
            wait_for.wait()                      # another thread is on it: then read its answer from the cache
        created = None
        try:
            created = self._disk.get(f"us:{login}") if self._disk else None       # an account's creation date never changes
            if created is None:
                status, data, _ = self._get(f"/users/{urllib.parse.quote(login)}")
                created = data.get("created_at") if status == 200 and isinstance(data, dict) else None
                if created and self._disk:
                    self._disk.put(f"us:{login}", created)
            with self._user_lock:
                self._user_cache[login] = created
        finally:
            with self._user_lock:
                self._user_inflight.pop(login, threading.Event()).set()
        return created

    OPEN_TIMELINE_TTL_S = 3600      # how long the timeline of a PR that has not closed is remembered

    def pr_timeline(self, number: int) -> list[dict] | None:
        """The PR's issue timeline (reviews, comments, commits, force-pushes,
        labels, duplicate marks, close events), oldest first. Used only for the
        merge-outcome labels, which are outcomes, so nothing is cut at open.
        None if unreadable, if any page fails, or past 2000 events: the lost
        part would be the newest events (the close), so a partial timeline
        would mislabel the PR rather than leave it unobserved."""
        hit = self._disk.get(f"tl:{number}") if self._disk else None
        if hit is not None:
            return hit
        events: list[dict] = []
        for page in range(1, 21):
            status, data, _ = self._get(
                f"/repos/{self.owner}/{self.repo}/issues/{number}/timeline",
                {"per_page": 100, "page": page})
            if status != 200 or not isinstance(data, list):
                return None
            events.extend(data)
            if len(data) < 100:
                if self._disk:
                    # a PR still open (no close, or reopened since) is not final: a short life, not 14 days
                    last = next((e.get("event") for e in reversed(events)
                                 if e.get("event") in ("closed", "merged", "reopened")), None)
                    final = last in ("closed", "merged")
                    self._disk.put(f"tl:{number}", events, ttl=None if final else self.OPEN_TIMELINE_TTL_S,
                                   keep=api_cache.CLOSED_KEEP_S if final else None)
                return events
        return None

    def force_pushes(self, numbers: list[int], batch: int = 25) -> dict[int, list | None]:
        """{pr_number: [(pushed_at_iso, before_sha), ...] oldest first} from
        GraphQL HeadRefForcePushedEvent, which (unlike the REST timeline) keeps
        the head a force-push replaced. Batched with aliases, so 25 PRs cost one
        GraphQL call (a separate budget from REST). None for a PR whose batch
        failed: unknown, not "no force-push"."""
        out: dict[int, list | None] = {}
        if not self.token:
            return {n: None for n in numbers}     # GraphQL needs auth
        total = len(numbers)
        if self._disk:                            # a restart does not ask again for what an earlier try already read
            for n in numbers:
                hit = self._disk.get(f"fp:{n}")
                if isinstance(hit, list):
                    out[n] = [(e[0], e[1]) for e in hit]
            numbers = [n for n in numbers if n not in out]
            if out:
                runlog.info("stage", "progress", name="open snapshots", phase="force-pushes", done=len(out), total=total,
                            cached=len(out))
        t0, calls = time.time(), 0
        for i in range(0, len(numbers), batch):
            part = numbers[i:i + batch]
            fields = " ".join(
                f"p{n}: pullRequest(number: {int(n)}) {{ timelineItems("
                f"itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT], first: 100) {{ pageInfo {{ hasNextPage }} nodes {{ "
                f"... on HeadRefForcePushedEvent {{ createdAt beforeCommit {{ oid }} }} }} }} }}"
                for n in part)
            q = (f'query {{ repository(owner: {json.dumps(self.owner)}, '
                 f'name: {json.dumps(self.repo)}) {{ {fields} }} }}')
            for attempt in range(2):                # a used-up quota is waited out here (few calls, and an unknown
                data = self._gql(q)                 # force-push would be a different answer), unlike the bulk reads
                if data is not None or self._gql_blocked_until <= time.time():
                    break
                wait = min(3600.0, self._gql_blocked_until - time.time()) + 1
                runlog.warn("api", "graphql quota used up; waiting for it", wait_s=int(wait))
                time.sleep(wait)
            repo_data = (data or {}).get("repository")             # None: unknown, not "no force-push"
            for n in part:
                node = (repo_data or {}).get(f"p{n}")
                if node is None or ((node.get("timelineItems") or {}).get("pageInfo") or {}).get("hasNextPage"):
                    out[n] = None                 # batch failed, PR not visible, or more than the 100 read: unknown
                else:
                    out[n] = [(e.get("createdAt"), (e.get("beforeCommit") or {}).get("oid"))
                              for e in (node.get("timelineItems") or {}).get("nodes") or []
                              if e]
                    if self._disk:                # only an answer that was read: unknown (None) is never remembered
                        self._disk.put(f"fp:{n}", [list(e) for e in out[n]], ttl=6 * 3600)
            calls += 1
            if calls % 20 == 0 or i + batch >= len(numbers):
                done = total - len(numbers) + min(i + batch, len(numbers))
                el = max(1e-6, time.time() - t0)
                left = (len(numbers) - min(i + batch, len(numbers))) * el / max(1, min(i + batch, len(numbers)))
                runlog.info("stage", "progress", name="open snapshots", phase="force-pushes", done=done, total=total,
                            secs_per_call=round(el / calls, 1), eta_s=int(left))
                print(f"[open-snapshot] force-push lookups {done:,}/{total:,} PRs "
                      f"({el / calls:.1f}s a call, about {int(left // 60)} min left)", file=sys.stderr, flush=True)
        return out

    def _suites_for_events(self, sha: str, events: tuple[str, ...]) -> set[int] | None:
        return self._suites_detail(sha, events)[0]

    def _suites_detail(self, sha: str, events: tuple[str, ...]) -> tuple[set[int] | None, str]:
        """(suites, why): why is "ok", "unreadable" (the runs call failed) or "cut" (a full last page).
        Check-suite ids of the GitHub Actions workflow runs on `sha` that were started
        by one of `events`, or None when the runs can't be read (callers then filter nothing).
        A check run lands on whichever commit was the default branch's head when an
        unrelated event fired (an issue labelled, a schedule, a /command comment), so the
        commit's check list mixes real CI with automation; the run's event tells them apart."""
        suites: set[int] = set()
        for page in range(1, 11):                             # up to 1000 workflow runs on one commit
            status, data, _ = self._get(f"/repos/{self.owner}/{self.repo}/actions/runs",
                                        {"head_sha": sha, "per_page": 100, "page": page})
            if status != 200 or not isinstance(data, dict):
                return None, "unreadable"
            runs = data.get("workflow_runs") or []
            suites.update(r["check_suite_id"] for r in runs
                          if r.get("event") in events and r.get("check_suite_id") is not None)
            if len(runs) < 100:
                return suites, "ok"
        return None, "cut"                                    # a full last page: runs may be missing, so unreadable, not cut

    def _check_runs_rest(self, sha: str, events) -> tuple[dict, bool, bool] | None:
        """({check name: [(completed_at, id, conclusion), ...]}, whether any check run had a verdict, whether
        the list is unfiltered), or None when the check runs cannot be read whole (a page failed, more than
        2000, or the workflow-run list to filter by was cut): a part of the list is not the list. When the
        workflow runs cannot be read at all the filter is skipped (every check run counts, the documented
        fallback) and the list is flagged unfiltered: a failure still stands, but a pass cannot be claimed."""
        saw_any = False
        by_name: dict[str, list] = {}
        suites, why = self._suites_detail(sha, events) if events else (None, "ok")
        unfiltered = bool(events) and suites is None
        if unfiltered and why == "cut":
            return None
        page = 1
        while page <= 20:                       # up to 2000 check-runs
            status, data, _ = self._get(
                f"/repos/{self.owner}/{self.repo}/commits/{sha}/check-runs",
                {"per_page": 100, "page": page, "filter": "all"})
            if status != 200 or not isinstance(data, dict):
                return None
            runs = data.get("check_runs", [])
            if not runs:
                break
            for r in runs:
                if not r.get("conclusion"):
                    continue
                if (suites is not None and (r.get("app") or {}).get("slug") == "github-actions"
                        and (r.get("check_suite") or {}).get("id") not in suites):
                    continue                    # automation on this commit, not its CI
                saw_any = True
                name = (r.get("name") or "").lower()
                by_name.setdefault(name, []).append(
                    (r.get("completed_at") or r.get("started_at") or "", r.get("id") or 0,
                     r["conclusion"]))
            if len(runs) < 100:
                break
            page += 1
        else:
            return None
        return by_name, saw_any, unfiltered

    def _statuses_rest(self, sha: str) -> list | None:
        """Every legacy commit status on `sha` (the combined-status call returns 30 a page unless asked
        for more, and is paged), or None when they cannot be read whole."""
        out: list = []
        for page in range(1, 11):
            status, data, _ = self._get(f"/repos/{self.owner}/{self.repo}/commits/{sha}/status",
                                        {"per_page": 100, "page": page})
            if status != 200 or not isinstance(data, dict):
                return None
            part = data.get("statuses") or []
            out.extend(part)
            total = data.get("total_count")
            if len(part) < 100 or (isinstance(total, int) and len(out) >= total):
                return out
        return None

    def _checks_graphql(self, sha: str, events) -> tuple[dict, list] | None:
        """The same data `_check_runs_rest` and the status call give, from one GraphQL query:
        ({check name: [(completed_at, id, conclusion)]}, [{"state", "context"}]). None when it
        cannot be read whole (no token, a failed query, a page that is full), so REST decides."""
        if not self.token or sha is None:
            return None
        q = ('query { repository(owner: %s, name: %s) { object(oid: %s) { ... on Commit { '
             'checkSuites(first: 30) { pageInfo { hasNextPage } nodes { databaseId app { slug } '
             'workflowRun { event } checkRuns(first: 100, filterBy: {checkType: ALL}) { '
             'pageInfo { hasNextPage } nodes { databaseId name conclusion completedAt startedAt } } } } '
             'status { contexts { context state } } } } } }'
             % (json.dumps(self.owner), json.dumps(self.repo), json.dumps(sha)))
        data = self._gql(q, strict=True)
        if data is None:
            return None
        commit = ((data.get("repository") or {}).get("object"))
        if not isinstance(commit, dict) or "checkSuites" not in commit:
            return None
        suites = commit["checkSuites"] or {}
        if (suites.get("pageInfo") or {}).get("hasNextPage"):
            return None
        wanted = {e.lower() for e in events} if events else None
        by_name: dict[str, list] = {}
        for suite in suites.get("nodes") or []:
            runs = suite.get("checkRuns") or {}
            if (runs.get("pageInfo") or {}).get("hasNextPage"):
                return None
            actions = ((suite.get("app") or {}).get("slug")) == "github-actions"
            if wanted is not None and actions:
                ev = ((suite.get("workflowRun") or {}).get("event") or "").lower()
                if ev not in wanted:
                    continue                    # automation on this commit, not its CI
            for r in runs.get("nodes") or []:
                if not r.get("conclusion"):
                    continue
                by_name.setdefault((r.get("name") or "").lower(), []).append(
                    (r.get("completedAt") or r.get("startedAt") or "", r.get("databaseId") or 0,
                     r["conclusion"].lower()))
        ctxs = ((commit.get("status") or {}).get("contexts")) or []
        if len(ctxs) > 30:                      # REST's status call returns 30 and is not paged: keep its answer
            return None
        return by_name, [{"state": (c.get("state") or "").lower(), "context": c.get("context")} for c in ctxs]

    def check_conclusion(self, sha: str, events: tuple[str, ...] | None = None) -> dict:
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

        events: count a GitHub Actions check run only when its workflow was started by
        one of these events (the merge commit's real CI is its `push` runs; issue,
        schedule and comment workflows are not). Check runs from other apps (CircleCI,
        Buildkite) have no workflow event and are always counted. None counts everything.
        """
        req: list = []
        opt: list = []
        saw_any = False
        checks: list[dict] = []      # required checks: latest verdict, rerun-pass

        # One GraphQL call per commit replaces the Actions-runs, check-runs and status calls (3-4 REST
        # calls); anything it cannot give whole (too many runs, a failed query) is read through REST.
        gql = self._checks_graphql(sha, events)
        by_name: dict[str, list] = {}
        unfiltered = False           # every check run counted because the workflow events could not be read
        if gql is not None:
            by_name, statuses = gql
            saw_any = bool(by_name)
        else:
            rest = self._check_runs_rest(sha, events)
            if rest is None:
                return {"required": None, "optional": None, "checks": []}      # unreadable is not a verdict
            by_name, saw_any, unfiltered = rest
            statuses = None
        FAIL = {"failure", "timed_out", "startup_failure", "action_required"}
        for name, runs in by_name.items():
            runs.sort()
            latest = runs[-1][2]
            if self._is_optional(name):
                opt.append(latest)
            else:
                req.append(latest)
                checks.append({"name": name, "conclusion": latest,
                               "rerun_pass": latest == "success" and any(c in FAIL for *_x, c in runs[:-1])})

        # 2) legacy commit statuses — merge in, don't replace
        if statuses is None:
            statuses = self._statuses_rest(sha)
            if statuses is None:
                return {"required": None, "optional": None, "checks": []}
        if statuses:
            for stt in statuses:
                state = stt.get("state")
                c = ("failure" if state in ("failure", "error")
                     else "success" if state == "success" else None)
                if not c:
                    continue
                name = (stt.get("context") or "").lower()
                if self._is_optional(name):
                    opt.append(c)
                else:
                    req.append(c)
                    checks.append({"name": name, "conclusion": c, "rerun_pass": False})
                saw_any = True

        if not saw_any:
            return {"required": None, "optional": None, "checks": []}
        checks.sort(key=lambda c: c["name"])             # one order whichever API answered
        required, optional = _reduce_conclusions(req), _reduce_conclusions(opt)
        if unfiltered:                   # automation may be mixed in: a failure is still a failure, a pass is not proven
            required = required if required == "failure" else None
            optional = optional if optional == "failure" else None
        return {"required": required, "optional": optional, "checks": checks}

    # -- persistent getter (waits through rate limits) ----------------------
    def _get_persistent(self, path: str, params: dict | None = None):
        """
        Like _get, but an exhausted quota is waited out (until its reset) and retried
        indefinitely instead of giving up: the historical CI pre-pass is long-running and must
        survive rate-limit windows. The rate-limit signals are read as _get reads them
        (Retry-After as seconds or a date, X-RateLimit-Remaining/Reset, a secondary-limit
        message in the body); a secondary limit or a bare 429 is retried a bounded number of
        times, and a 403 with no rate-limit signal (Actions disabled, SSO) is returned as
        (403, None), never looped on. Transient network errors and 5xx get a bounded backoff,
        then (code, None): the caller keeps its checkpoint and a rerun resumes. A body cut
        mid-read (IncompleteRead on a ~1 MB runs page) is a network error, not a crash.
        """
        url = f"{API}{path}"
        if params:
            q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
            url = f"{url}?{q}"
        net_fails = 0
        waits = 0
        while True:
            _gate_wait()
            tok = self._acquire("core")
            req = urllib.request.Request(url, headers=self._headers(tok))
            try:
                with urllib.request.urlopen(req, timeout=self.st.request_timeout) as r:
                    self._note_remaining(tok, "core", r.headers)
                    return r.status, json.loads(r.read() or "null")
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    kind, wait = _rate_signal(e)
                    if kind == "primary":
                        if self._spent(tok, "core", wait, path):
                            continue
                        print(f"[ci-history] rate limit reached; waiting {int(wait)}s and retrying...",
                              file=sys.stderr, flush=True)
                        progress.emit("rate_limit", repo=f"{self.owner}/{self.repo}", wait=int(wait))
                        _note_rate_wait(wait, self.st.ci_cache_dir)
                        _rate_sleep(wait)
                        continue
                    if kind == "secondary":
                        waits += 1
                        if waits > MAX_SECONDARY_WAITS:
                            return e.code, None
                        progress.emit("rate_limit", repo=f"{self.owner}/{self.repo}", wait=int(wait))
                        _rate_sleep(wait)
                        continue
                    if e.code == 403:
                        runlog.warn("api", "403 without rate-limit signals; treating as unobserved",
                                    path=path)
                        return 403, None
                    waits += 1
                    if waits > MAX_NO_SIGNAL_429:
                        return 429, None
                    time.sleep(min(60, 5 * 2 ** (waits - 1)))
                    continue
                if e.code == 404:
                    return 404, None
                if e.code >= 500:
                    net_fails += 1
                    runlog.warn("api", "server error; backing off", path=path, code=e.code,
                                retry=net_fails, wait_s=min(60, 2 ** net_fails))
                    if net_fails > 10:
                        return e.code, None
                    time.sleep(min(60, 2 ** net_fails))
                    continue
                raise
            except (urllib.error.URLError, OSError, http.client.HTTPException,
                    json.JSONDecodeError) as e:      # incl. IncompleteRead, resets, cut bodies
                net_fails += 1
                runlog.warn("api", "network error; backing off", path=path, exc=type(e).__name__,
                            retry=net_fails, wait_s=min(60, 2 ** net_fails))
                if net_fails > 10:
                    return 0, None
                time.sleep(min(60, 2 ** net_fails))

    CI_PAGE_CAP = 400           # GitHub lists at most 400 pages of 100 runs (40,000) per query; page 401 is an HTTP 422
    CI_EMPTY_WEEKS = 26         # this many weeks in a row with no runs: there is nothing older
    CI_WINDOW_WORKERS = 4       # pages of one date window read side by side
    CI_FILTER_CAP = 1000        # a date-filtered list returns 1,000 runs at most, whatever its total_count says

    def _ci_page(self, params: dict):
        """One page of the repo's workflow runs; (0, None) when GitHub refuses it (a 4xx such as 422)."""
        try:
            return self._get_persistent(f"/repos/{self.owner}/{self.repo}/actions/runs", params)
        except urllib.error.HTTPError:
            return 0, None

    def _ci_window(self, start: str, end: str, take, depth: int = 0) -> int | None:
        """Feed every workflow run created in [start, end] to `take`; the number of runs, or None when a
        page could not be read or the window cannot be read whole. A window with more runs than a
        filtered list can return (CI_FILTER_CAP) is halved until it fits; one that still does not fit
        when it can be halved no more (a second wide, or too deep) is None, never a truncated read.
        The first page, fetched to learn the total, is the one a window that fits is read from."""
        status, data = self._ci_page({"per_page": 100, "page": 1, "created": f"{start}..{end}"})
        if status != 200 or not isinstance(data, dict):
            return None
        total = int(data.get("total_count") or 0)
        if total == 0:
            return 0
        if total > self.CI_FILTER_CAP:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            s0 = datetime.strptime(start, fmt).replace(tzinfo=timezone.utc)
            e0 = datetime.strptime(end, fmt).replace(tzinfo=timezone.utc)
            if depth >= 20 or (e0 - s0) < timedelta(seconds=2):
                runlog.warn("ci-history", "a window has more runs than a list returns and cannot be split",
                            start=start, end=end, total=total)
                return None
            mid = (s0 + (e0 - s0) / 2).replace(microsecond=0)
            a = self._ci_window(start, mid.strftime(fmt), take, depth + 1)
            if a is None:
                return None
            b = self._ci_window((mid + timedelta(seconds=1)).strftime(fmt), end, take, depth + 1)
            return None if b is None else a + b
        runs = data.get("workflow_runs", [])
        take(runs)
        page, seen = 2, len(runs)
        planned = min(-(-total // 100), self.CI_FILTER_CAP // 100)       # pages the first answer says there are
        if len(runs) == 100 and planned >= 2:
            # the other pages of a window are known now: read them side by side (one page took ~5 s, a busy week
            # is up to 10 of them), then feed them in page order. The runs are the same ones.
            pages = list(range(2, planned + 1))
            with ThreadPoolExecutor(max_workers=max(1, min(self.CI_WINDOW_WORKERS, len(pages)))) as pool:
                got = list(pool.map(lambda pg: self._ci_page(
                    {"per_page": 100, "page": pg, "created": f"{start}..{end}"}), pages))
            for status, data in got:
                if status != 200 or not isinstance(data, dict):
                    return None
                runs = data.get("workflow_runs", [])
                take(runs)
                seen += len(runs)
            page = planned + 1
        while len(runs) == 100 and page * 100 <= self.CI_FILTER_CAP:
            status, data = self._ci_page({"per_page": 100, "page": page, "created": f"{start}..{end}"})
            if status != 200 or not isinstance(data, dict):
                return None
            runs = data.get("workflow_runs", [])
            take(runs)
            seen += len(runs)
            page += 1
        return seen

    CI_MAP_VERSION = 2          # the cache file's format; 1 (a bare {sha: verdict}) is from before the date-window walk and may be cut short
    CI_MAP_TTL_S = 7 * 86400    # a cached map older than this is rebuilt: runs since then are not in it

    def _load_ci_map(self, cache_path: str) -> tuple[dict | None, float]:
        """(map, built-at) from the cache file, or (None, 0) for a missing, unreadable or old-format one
        (a bare {sha: verdict} file from before 6.13.0 may be a truncated map; it is only read to be discarded)."""
        try:
            with open(cache_path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError):
            return None, 0.0
        if (isinstance(d, dict) and d.get("v") == self.CI_MAP_VERSION and isinstance(d.get("map"), dict)
                and isinstance(d.get("built"), (int, float))):
            return d["map"], float(d["built"])
        return None, 0.0

    def build_ci_conclusion_map(self, cache_path: str | None = None, covers_until: str | None = None) -> dict:
        """
        {commit_sha: 'failure' | 'success'} for the repo's workflow runs, built
        from the Actions workflow-runs list endpoint (100 runs/call — far cheaper
        than one check-runs call per commit). Cached to `cache_path` as JSON, so
        the expensive pre-pass is paid once per repo. Only GitHub Actions is
        covered; commits with no runs are simply absent (feature = None there).

        The cache file is {"v", "built", "map"}: a file of another version is a miss, and one built more
        than CI_MAP_TTL_S ago (or before `covers_until`, an ISO time the caller needs runs up to, such as
        the newest PR's merge) is rebuilt, so a later run over newer PRs does not read a map that stops
        before them. If that rebuild cannot finish, the older complete map is returned rather than a
        partial one.
        """
        stale: dict | None = None
        if cache_path and os.path.isfile(cache_path):
            cached, built = self._load_ci_map(cache_path)
            if cached is not None:
                built_iso = datetime.fromtimestamp(built, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if time.time() - built <= self.CI_MAP_TTL_S and not (covers_until and covers_until > built_iso):
                    return cached
                stale = cached

        # Pages are checkpointed to <cache>.pages.json every few pages, so a
        # stopped run resumes at the next page instead of page 1. The final
        # cache is written only when the walk reached the end of the list: a
        # failed page keeps the checkpoint and is retried by the next run,
        # rather than a truncated map being cached as complete.
        pages_path = (cache_path + ".pages.json") if cache_path else None
        raw: dict[str, list] = {}
        page = 1
        ck_oldest = ck_win_end = None
        ck_empty = 0
        if pages_path and os.path.isfile(pages_path):
            try:
                with open(pages_path, encoding="utf-8") as fh:
                    ck = json.load(fh)
                raw, page = ck["raw"], int(ck["next_page"])
                ck_oldest, ck_win_end, ck_empty = ck.get("oldest"), ck.get("win_end"), int(ck.get("empty") or 0)
                print(f"[ci-history] resuming at page {page} ({len(raw)} commits checkpointed)",
                      file=__import__("sys").stderr, flush=True)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                raw, page = {}, 1
        complete = False
        oldest = ck_oldest if page > 1 else None     # the oldest run's creation time seen so far
        win_end = ck_win_end
        empty_streak = ck_empty
        if page > self.CI_PAGE_CAP and oldest is None and win_end is None:
            page = self.CI_PAGE_CAP                  # an older checkpoint did not keep it: read the last page again (duplicates change nothing)

        def take(runs: list) -> None:
            nonlocal oldest
            for run in runs:
                made = run.get("created_at")
                if made and (oldest is None or made < oldest):
                    oldest = made
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

        # 1) the newest runs, straight down the list. GitHub stops the list at CI_PAGE_CAP pages
        # (40,000 runs; the next page is an HTTP 422), so on a big repo this reaches back only a few months.
        capped = False
        while win_end is None:
            if page > self.CI_PAGE_CAP:
                capped = True
                break
            status, data = self._ci_page({"per_page": 100, "page": page})      # a 4xx (401, 422) is a stop, not a crash: the checkpoint is kept below
            if status != 200 or not data:
                break
            runs = data.get("workflow_runs", [])
            if not runs:
                complete = True
                break
            take(runs)
            if len(runs) < 100:
                complete = True
                break
            page += 1
            if pages_path and page % 10 == 0:
                self._save_ci_pages(pages_path, raw, page, oldest=oldest)
            print(f"[ci-history] fetched {sum(len(v) for v in raw.values())} runs "
                  f"across {len(raw)} commits...", file=__import__("sys").stderr, flush=True)

        # 2) older runs, by creation date: a date-filtered list has its own cap, so a week at a time
        # reaches back as far as the repo has runs. Stops at the first long stretch with none, or at
        # the year Actions began.
        if capped or win_end is not None:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            end = datetime.strptime(win_end or oldest, fmt).replace(tzinfo=timezone.utc) if (win_end or oldest) else None
            floor = datetime(2019, 10, 1, tzinfo=timezone.utc)      # GitHub Actions did not exist before this
            ok = end is not None
            while ok and end > floor and empty_streak < self.CI_EMPTY_WEEKS:
                start = end - timedelta(days=7)
                n = self._ci_window(start.strftime(fmt), end.strftime(fmt), take)
                if n is None:
                    ok = False                                       # a failed read: keep the checkpoint, the next run resumes
                    break
                empty_streak = empty_streak + 1 if n == 0 else 0
                end = start
                win_end = end.strftime(fmt)
                if pages_path:
                    self._save_ci_pages(pages_path, raw, page, oldest=oldest, win_end=win_end, empty=empty_streak)
                print(f"[ci-history] back to {win_end[:10]}: {sum(len(v) for v in raw.values())} runs "
                      f"across {len(raw)} commits...", file=__import__("sys").stderr, flush=True)
            complete = ok

        ci: dict[str, str] = {}
        for sha, concls in raw.items():
            if any(c == "failure" for c in concls):
                ci[sha] = "failure"
            elif any(c == "success" for c in concls):
                ci[sha] = "success"
            # else: only neutral/skipped/cancelled -> leave out (unknown)

        if cache_path:
            if complete:
                try:
                    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
                    tmp = cache_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump({"v": self.CI_MAP_VERSION, "built": time.time(), "map": ci}, fh)
                    os.replace(tmp, cache_path)
                    if os.path.isfile(pages_path):
                        os.remove(pages_path)
                except OSError:
                    pass
            else:
                self._save_ci_pages(pages_path, raw, page, oldest=oldest, win_end=win_end, empty=empty_streak)
                print(f"[ci-history] stopped ({'at ' + win_end[:10] if win_end else 'at page ' + str(page)}); "
                      f"map not cached, a rerun resumes from there", file=__import__("sys").stderr, flush=True)
                if stale is not None:
                    return stale
        return ci

    @staticmethod
    def _save_ci_pages(path: str, raw: dict, next_page: int, oldest: str | None = None,
                       win_end: str | None = None, empty: int = 0) -> None:
        tmp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"next_page": next_page, "raw": raw, "oldest": oldest,
                           "win_end": win_end, "empty": empty}, fh)
            os.replace(tmp, path)
        except OSError:
            pass

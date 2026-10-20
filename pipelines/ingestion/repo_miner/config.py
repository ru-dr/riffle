"""
config.py — tunables and deterministic path rules.

Everything the extractor needs to know that isn't code: which paths count as
migrations/auth/etc., time windows for decay features, and toggles for the
slow/optional stages (LLM, scanners, API).
"""
from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass, field


# --- .env auto-loading ------------------------------------------------------
# Load a .env file (KEY=VALUE lines) into the environment at import time, so
# GITHUB_TOKEN / LLM_API_KEY are picked up automatically without the user
# exporting them in the shell. A real shell env var always wins over .env.
# Looks in the package directory first, then the current working directory.
def _parse_env_file(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


def _load_dotenv() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, ".env"),
                      os.path.join(os.getcwd(), ".env")):
        if os.path.isfile(candidate):
            _parse_env_file(candidate)
            return


_load_dotenv()


# ---- High-risk path rules (Source 4b) --------------------------------------
PATH_RULES: dict[str, list[str]] = {
    "touches_migration": [r"(^|/)migrations?/", r"\.sql$", r"(^|/)db/migrate/"],
    "touches_lockfile": [
        r"(^|/)package-lock\.json$", r"(^|/)yarn\.lock$", r"(^|/)pnpm-lock\.yaml$",
        r"(^|/)poetry\.lock$", r"(^|/)Pipfile\.lock$", r"(^|/)go\.sum$",
        r"(^|/)Cargo\.lock$", r"(^|/)Gemfile\.lock$",
        r"(^|/)composer\.lock$", r"(^|/)pubspec\.lock$", r"(^|/)mix\.lock$",
    ],
    "touches_dep_manifest": [
        r"(^|/)package\.json$", r"(^|/)requirements[^/]*\.txt$",
        r"(^|/)pyproject\.toml$", r"(^|/)go\.mod$", r"(^|/)pom\.xml$",
        r"(^|/)build\.gradle$", r"(^|/)Cargo\.toml$", r"(^|/)Gemfile$",
        r"(^|/)composer\.json$", r"(^|/)pubspec\.yaml$", r"(^|/)mix\.exs$",
        r"\.(csproj|vbproj|fsproj)$",
    ],
    "touches_ci_config": [
        r"(^|/)\.github/workflows/", r"(^|/)\.gitlab-ci\.yml$",
        r"(^|/)Jenkinsfile$", r"(^|/)\.circleci/",
    ],
    "touches_infra": [
        r"\.tf$", r"(^|/)helm/", r"(^|/)k8s/", r"(^|/)Dockerfile",
        r"(^|/)docker-compose[^/]*\.yml$",
    ],
    # token-boundaried so it no longer matches "author", "authors.md", "authority"
    "touches_auth_path": [
        r"(^|/|_|-)(auth|authn|authz|login|logout|signin|signup|session|"
        r"oauth|jwt|permission|rbac|credential|password|secret)(s|_|-|/|\.|$)"
    ],
    "touches_codeowners_or_riffle_yml": [
        r"(^|/)CODEOWNERS$", r"(^|/)riffle\.ya?ml$",
    ],
}

TEST_PATTERNS = [r"(^|/)tests?/", r"_test\.", r"\.test\.", r"(^|/)test_", r"\.spec\."]
DOC_PATTERNS = [r"\.md$", r"\.rst$", r"(^|/)docs?/"]
GENERATED_PATTERNS = [
    r"\.pb\.go$", r"_pb2\.pyi?$", r"_pb2_grpc\.py$",      # protobuf (go/python)
    r"\.g\.dart$", r"\.freezed\.dart$",                    # dart codegen
    r"\.generated\.(ts|js|go|cs)$", r"generated(\.go|_.*\.go)$",
    r"__generated__", r"(^|/)__snapshots__/", r"\.snap$",   # snapshots
    r"(^|/)dist/", r"(^|/)build/", r"(^|/)vendor/",
    r"(^|/)third_party/", r"\.min\.(js|css)$", r"(^|/)node_modules/",
]

EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".java": "java", ".go": "go", ".rb": "ruby",
    ".rs": "rust", ".c": "c", ".cpp": "cpp", ".cc": "cpp", ".h": "c",
    ".cs": "csharp", ".php": "php", ".kt": "kotlin", ".scala": "scala",
}

# Memory per mining worker and for the rest of the process. One Oct 2026 run
# peaked at 5.5 GB with 6 workers on a large repo.
WORKER_RAM_GB = 0.8
BASE_RAM_GB = 1.5


def available_ram_gb() -> float | None:
    """Memory free for new work (MemAvailable on Linux, psutil elsewhere),
    or None when it can't be read."""
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 ** 3
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 ** 2
    except OSError:
        pass
    return None


def default_workers(ram_gb: float | None = None, cpus: int | None = None) -> int:
    """min(6, CPUs), lowered so the workers fit in the free RAM."""
    n = min(6, cpus or os.cpu_count() or 2)
    ram = available_ram_gb() if ram_gb is None else ram_gb
    if ram is not None:
        n = min(n, int((ram - BASE_RAM_GB) // WORKER_RAM_GB))
    return max(1, n)


# ---- Tunables --------------------------------------------------------------
@dataclass
class Settings:
    # windows (days)
    churn_windows: tuple[int, ...] = (90, 365)
    decay_half_life_days: float = 90.0
    revert_window_days: int = 30           # for label maturity
    hotfix_window_days: int = 7
    label_lookahead_days: int = 90         # how far after merge to look for reverts/hotfixes
    szz_maturity_days: int = 90            # cap on days_to_szz_fix (bounds SZZ by age)
    recent_burst_days: int = 7
    hotspot_top_k: int = 20
    blame_ignore_revs: str | None = None   # path to .git-blame-ignore-revs
    hunk_proximity_k: int = 5              # +/- lines counted as "near" a past fix

    # repo-local mined features (contracts/mined_features.schema.json); defaults
    # mirror the rules.mining / labels blocks in contracts/org_rules.schema.json
    maturity_days: int = 30                # labels.maturity_days: a past PR counts once this old
    similar_prs_k: int = 5
    similar_prs_min_file_overlap: float = 0.2
    similar_prs_lookback_days: int = 365
    similar_prs_min_neighbours: int = 3
    scrutiny_lookback_days: int = 180
    scrutiny_exclude_bot_reviews: bool = True
    scrutiny_min_prior_prs: int = 5
    failure_mode_lookback_days: int = 365
    failure_mode_min_bad_changes: int = 2
    path_size_lookback_days: int = 365
    path_size_min_prior_changes: int = 10
    release_tag_pattern: str = r"^v?\d+\.\d+(\.\d+)?$"
    release_min_releases: int = 3
    release_interval_stat: str = "median"  # median | mean

    # author smoothing (empirical-Bayes toward repo mean)
    author_smoothing_alpha: float = 5.0

    # stage toggles (slow/optional)
    enable_scanners: bool = True           # lizard + semgrep
    enable_semgrep: bool = True
    enable_deps_cve: bool = True           # OSV + GHSA (network)
    enable_api_contract: bool = True       # oasdiff / buf / graphql-inspector
    enable_llm: bool = True                # on by default; --no-llm, or off when no backend is usable
    enable_github_api: bool = True         # PR list, CI checks, labels
    enable_ci_history: bool = True         # historical CI-fail-rate store (<=400 API calls, cached)
    enable_szz: bool = True                # SZZ fix-tracing label (windowed, cached)
    enable_review_history: bool = True     # review events for revealed_path_scrutiny (2 API calls/PR)
    enable_declared_labels: bool = True    # declared_hotfix feature (from the PR timeline)
    declared_label_grace_s: int = 120      # include labels applied within Ns of open
    # merge-outcome target (ADR 0009): also mine closed-unmerged PRs and label
    # every row from its timeline (1-2 timeline calls/PR, +1 check call per
    # unmerged PR, and a fetch of each unmerged PR's head ref). On by default so
    # one run feeds both models; --no-merge-outcome mines breakage rows only.
    enable_merge_outcome: bool = True
    close_reason_classifier: str = "llm"   # llm (with --llm; rules fallback) | rules
    max_unmerged: int | None = None        # cap unmerged rows per repo (None = the window's real mix)
    abandon_days: int = 60
    merge_signal_lookback_days: int = 365  # prior PRs counted by the repo-level merge signals
    merge_signal_min_prs: int = 5          # fewer prior PRs than this -> null rate                 # still open this long with no human response = abandoned
    rework_grace_s: int = 300              # commits/force-pushes this soon after open aren't rework
    stale_bot_logins: tuple[str, ...] = ("stale[bot]", "stale-bot", "stalebot")
    unwanted_labels: tuple[str, ...] = ("wontfix", "won't fix", "invalid", "not planned",
                                        "out of scope", "declined", "rejected", "spam")
    ci_cache_dir: str = "ci_cache"         # where the per-repo CI conclusion cache lives

    # networking
    github_token: str | None = field(default_factory=lambda: os.getenv("GITHUB_TOKEN"))
    # LLM backend: "claude_cli" (headless `claude -p`, uses your Claude Code
    # login — no API key) or "api" (OpenAI-compatible endpoint + LLM_API_KEY)
    llm_backend: str = field(default_factory=lambda: os.getenv("LLM_BACKEND", "claude_cli"))
    llm_api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: os.getenv(
        "LLM_MODEL",
        "sonnet" if os.getenv("LLM_BACKEND", "claude_cli") == "claude_cli" else "claude-sonnet-5"))
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.anthropic.com/v1/"))
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "180")))
    # circuit breaker: abort the run after this many consecutive LLM failures
    llm_breaker_threshold: int = field(
        default_factory=lambda: int(os.getenv("LLM_BREAKER_THRESHOLD", "5")))
    llm_breaker_tripped: bool = False      # set by the miner when the breaker trips
    llm_concurrency: int = field(default_factory=lambda: int(os.getenv("LLM_CONCURRENCY", "4")))
    # Which rows get LLM calls. "training": outside-contributor PRs (not bots)
    # plus a fixed llm_sample_pct sample of the rest, the rows the merge model
    # trains on; "all": every row. Rows left out keep null LLM columns.
    llm_scope: str = field(default_factory=lambda: os.getenv("LLM_SCOPE", "training"))
    llm_sample_pct: int = 5
    # skip the LLM for diffs with nothing to judge: docs-only, tests-only,
    # lockfile/generated-only (their flags would all be the neutral value)
    llm_skip_trivial: bool = True
    # diff sent to the LLM: context lines, and a size cap after lockfiles and
    # generated/minified/vendored/snapshot files are cut out
    llm_diff_context: int = 1
    llm_max_diff_chars: int = 25000
    # close-reason classifier: "other_only" asks the LLM only when the rules
    # find no reason (duplicate/unwanted/ci_technical/abandoned); "always"
    # asks for every unmerged PR with human activity
    close_reason_llm: str = "other_only"
    # headless Claude plan limits: when a limit is near (or a call fails on
    # one), wait for the reset instead of failing calls into the breaker
    llm_quota_wait: bool = True
    llm_quota_pause_pct: float = 95.0      # pause at this % used
    llm_quota_poll_s: int = 300            # re-read the plan usage at most this often
    llm_quota_max_wait_h: float = 12.0     # give up (fail calls) after waiting this long
    # a PR whose LLM call fails is retried this many times (waits grow by
    # llm_retry_backoff_s) before the repo stops; no row is written without
    # the LLM columns it is in scope for
    llm_row_retries: int = 3
    llm_retry_backoff_s: float = field(default_factory=lambda: float(os.getenv("LLM_RETRY_BACKOFF_S", "60")))
    # wait between the quick retries inside one claude -p call (x attempt)
    llm_call_retry_s: float = field(default_factory=lambda: float(os.getenv("LLM_CALL_RETRY_S", "10")))
    # PR snapshots (snapshot_capture.py, snapshots.py): for rows in llm_scope,
    # store the raw PR, timeline and force-pushes, and the code features of up
    # to snapshot_max_heads head commits the PR had while open
    # flaky-check detection (rules.mining.flaky_detection, flaky.py)
    flaky_detection: bool = True
    flaky_min_rerun_passes: int = 3
    flaky_lookback_days: int = 90
    enable_snapshots: bool = True
    snapshot_max_heads: int = 5
    snapshot_dir: str | None = None        # default snapshots_raw/<owner>_<name>
    # PRs mined in parallel (threads; the work is mostly git subprocesses).
    # Each worker may run lizard/semgrep, so the default is capped by free RAM
    # (default_workers); RIFFLE_WORKERS or --workers overrides it.
    workers: int = field(default_factory=lambda: int(
        os.getenv("RIFFLE_WORKERS") or default_workers()))
    request_timeout: float = 20.0
    max_prs: int | None = None             # cap PRs per repo (None = all)
    # date-window selection (proposal 1.2): every closed PR created in
    # [window_from, window_to] (YYYY-MM-DD), merged and unmerged together; with
    # max_prs, the newest contiguous block of that many. None = the older
    # "newest max_prs merged PRs" selection.
    window_from: str | None = None
    window_to: str | None = None
    # history lead-in (proposal 1.4): PRs created this many days before the
    # kept block, as metadata only, so its first PRs have a history
    lead_in_days: int = 180
    # dataset domain (ADR 0011), written on every row; from the repos file tag
    domain: str | None = None
    # skip PRs merged in the last N days, so the cap picks PRs old enough for
    # their outcome labels to be mature (0 = off). 90 covers maturity_days (30)
    # and szz_maturity_days (90).
    min_age_days: int = 90
    merge_signal: str = "auto"             # auto | github | landed (closed-PR -> landed-commit fallback)
    pr_discovery: str = "api"              # api (PR-list endpoint) | git (commit-message linkage, API enriches)

    # fix/revert message heuristics (kept for reference; matching now goes through
    # the word-boundary helpers is_fix_text / is_revert_text / is_hotfix_text below,
    # so "prefix", "suffix", "fixture", "debug", "dispatch" no longer false-match)
    fix_keywords: tuple[str, ...] = ("fix", "bug", "defect", "patch", "hotfix")
    revert_prefixes: tuple[str, ...] = ('revert "', "revert:")
    # hotfix label == urgent fix; cherry-pick / backport are a DIFFERENT concept
    # (porting a change to another branch) and are deliberately excluded here
    hotfix_keywords: tuple[str, ...] = ("hotfix", "rollback")


DEFAULT = Settings()


def _match_any(path: str, patterns: list[str]) -> bool:
    return any(re.search(p, path, re.IGNORECASE) for p in patterns)


def _union(patterns: list[str]) -> re.Pattern:
    """One compiled alternation per rule — same matches as trying each pattern."""
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


_PATH_RULES_RE = {name: _union(pats) for name, pats in PATH_RULES.items()}
_TEST_RE = _union(TEST_PATTERNS)
_DOC_RE = _union(DOC_PATTERNS)
_GENERATED_RE = _union(GENERATED_PATTERNS)


@functools.lru_cache(maxsize=200_000)
def path_flags(path: str) -> dict[str, bool]:
    """Path classification flags. Memoized (called ~per file per commit per PR
    in the history walk); callers treat the returned dict as read-only."""
    flags = {name: bool(rx.search(path)) for name, rx in _PATH_RULES_RE.items()}
    flags["is_test"] = bool(_TEST_RE.search(path))
    flags["is_doc"] = bool(_DOC_RE.search(path))
    flags["is_generated"] = bool(_GENERATED_RE.search(path))
    return flags


def lang_of(path: str) -> str | None:
    for ext, lang in EXT_LANG.items():
        if path.endswith(ext):
            return lang
    return None


# ---- Word-boundary keyword matching ---------------------------------------
# Substring matching ("fix" in subject) wrongly fires on prefix / suffix /
# fixture / debug / dispatch / "patch release". These match whole tokens (with
# common inflections) instead. Underscores and digits count as boundaries, so
# "bugfix_test" still matches but "debugger" does not.
_FIX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"bug(?:s|fix(?:e[sd])?)?|"      # bug, bugs, bugfix, bugfixes
    r"fix(?:e[sd]|ing)?|"           # fix, fixes, fixed, fixing
    r"defects?|"
    r"patch(?:e[sd]|ing)?|"
    r"hotfix(?:e[sd])?"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_HOTFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:hotfix(?:e[sd])?|rollback)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_REVERT_RE = re.compile(r'^\s*revert[\s:"]', re.IGNORECASE)


def is_fix_text(s: str | None) -> bool:
    """True if the text names a bug/fix as a whole word (not prefix/fixture/etc)."""
    return bool(s) and _FIX_RE.search(s) is not None


def is_hotfix_text(s: str | None) -> bool:
    """True for an urgent-fix subject (hotfix/rollback). Cherry-pick/backport excluded."""
    return bool(s) and _HOTFIX_RE.search(s) is not None


def is_revert_text(s: str | None) -> bool:
    """True for a git revert commit subject/body."""
    s = (s or "").lstrip()
    return _REVERT_RE.match(s) is not None or "this reverts commit" in s.lower()


# ---- Subsystem resolution --------------------------------------------------
# The top-level directory is a poor subsystem key for repos that put everything
# under a single generic container (src/, lib/, pkg/, internal/, ...), where it
# collapses NS and SEXP to 1. For those, use the first TWO components so the
# real subsystem is captured; otherwise the first component.
_GENERIC_TOP = {
    "src", "lib", "libs", "pkg", "pkgs", "packages", "internal", "app", "apps",
    "source", "sources", "modules", "cmd", "crates",
}


@functools.lru_cache(maxsize=200_000)
def subsystem_of(path: str) -> str:
    """Best-effort subsystem for a path (used by NS, SEXP, percentiles)."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    if len(parts) >= 2 and parts[0].lower() in _GENERIC_TOP:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]

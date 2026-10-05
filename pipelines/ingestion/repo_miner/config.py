"""
config.py — tunables and deterministic path rules.

Everything the extractor needs to know that isn't code: which paths count as
migrations/auth/etc., time windows for decay features, and toggles for the
slow/optional stages (LLM, scanners, API).
"""
from __future__ import annotations

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

    # author smoothing (empirical-Bayes toward repo mean)
    author_smoothing_alpha: float = 5.0

    # stage toggles (slow/optional)
    enable_scanners: bool = True           # lizard + semgrep
    enable_semgrep: bool = True
    enable_deps_cve: bool = True           # OSV + GHSA (network)
    enable_api_contract: bool = True       # oasdiff / buf / graphql-inspector
    enable_llm: bool = False               # off by default; set true + key to use
    enable_github_api: bool = True         # PR list, CI checks, labels
    enable_ci_history: bool = False        # historical CI-fail-rate store (slow pre-pass)
    enable_szz: bool = False               # SZZ fix-tracing label (slow pre-pass)
    enable_declared_labels: bool = False   # declared_hotfix feature (1 timeline call/PR)
    declared_label_grace_s: int = 120      # include labels applied within Ns of open
    ci_cache_dir: str = "ci_cache"         # where the per-repo CI conclusion cache lives

    # networking
    github_token: str | None = field(default_factory=lambda: os.getenv("GITHUB_TOKEN"))
    llm_api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-5"))
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.anthropic.com/v1/"))
    request_timeout: float = 20.0
    max_prs: int | None = None             # cap PRs per repo (None = all)
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


def path_flags(path: str) -> dict[str, bool]:
    flags = {name: _match_any(path, pats) for name, pats in PATH_RULES.items()}
    flags["is_test"] = _match_any(path, TEST_PATTERNS)
    flags["is_doc"] = _match_any(path, DOC_PATTERNS)
    flags["is_generated"] = _match_any(path, GENERATED_PATTERNS)
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


def subsystem_of(path: str) -> str:
    """Best-effort subsystem for a path (used by NS, SEXP, percentiles)."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    if len(parts) >= 2 and parts[0].lower() in _GENERIC_TOP:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]

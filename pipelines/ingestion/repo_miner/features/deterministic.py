"""
deterministic.py — Source 5: things a parser/regex answers exactly, so we
don't ask the LLM. Operates on the added lines of the base...head diff.

Kept intentionally lightweight and language-agnostic (regex over added lines)
so it runs without per-language toolchains. Where a real linter/type-checker
is available in your environment you should swap these heuristics for it;
the interfaces stay the same.
"""
from __future__ import annotations

import re

import config
import gitio

_SECRET_PATTERNS = [
    r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
    r"AKIA[0-9A-Z]{16}",                      # AWS access key id
    r"gh[pousr]_[A-Za-z0-9]{36,}",            # GitHub tokens
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
]
_ENV_PATTERNS = [
    r"os\.environ(?:\.get)?\(", r"process\.env\.", r"getenv\(",
    r"System\.getenv\(",
]
_OBSERV_PATTERNS = [
    r"\b(log(?:ger|ging)?|console\.(log|error|warn)|print|logrus|slog|zap)\b",
    r"\b(metrics?|statsd|prometheus|trace|span|opentelemetry)\b",
]
_COMPARE_EDIT = re.compile(r"[<>]=?|==|!=|\+\s*1\b|-\s*1\b|\[\s*i\s*[-+]\s*1\s*\]")


def _added_lines(repo: str, base: str, head: str) -> list[tuple[str, str]]:
    """Return (path, added_line_text) for every '+' line in the diff."""
    out = gitio._run(repo, "diff", "-M", f"{base}...{head}", check=False)
    added, cur_path = [], None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur_path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((cur_path or "", line[1:]))
    return added


def _removed_lines(repo: str, base: str, head: str) -> list[tuple[str, str]]:
    out = gitio._run(repo, "diff", "-M", f"{base}...{head}", check=False)
    removed, cur_path = [], None
    for line in out.splitlines():
        if line.startswith("--- a/"):
            cur_path = line[6:]
        elif line.startswith("-") and not line.startswith("---"):
            removed.append((cur_path or "", line[1:]))
    return removed


def compute(repo: str, base: str, head: str, st: config.Settings) -> dict:
    added = _added_lines(repo, base, head)
    removed = _removed_lines(repo, base, head)

    def _is_noise(path: str) -> bool:
        pf = config.path_flags(path)
        return pf["is_test"] or pf["is_doc"] or pf["is_generated"]

    # secrets / unused-imports / comparison-edits are noise-prone in tests,
    # docs and generated code (fixtures, sample keys) — score them on real
    # source only so a test fixture doesn't inflate hardcoded_secret_added.
    code_added = [(p, t) for p, t in added if not _is_noise(p)]
    add_text_code = [t for _, t in code_added]
    add_text_all = [t for _, t in added]

    secret_ct = sum(
        1 for t in add_text_code if any(re.search(p, t) for p in _SECRET_PATTERNS)
    )
    # env/config keys can legitimately appear in config files, so score on all
    env_ct = sum(1 for t in add_text_all if any(re.search(p, t) for p in _ENV_PATTERNS))

    obs_added = sum(1 for t in add_text_code if any(re.search(p, t) for p in _OBSERV_PATTERNS))
    obs_removed = sum(
        1 for p, t in removed
        if not _is_noise(p) and any(re.search(pt, t) for pt in _OBSERV_PATTERNS)
    )

    compare_edits = sum(1 for t in add_text_code if _COMPARE_EDIT.search(t))

    # unused import (very rough, python/js) — added import with symbol not used
    unused = _rough_unused_imports(code_added)

    # dependency major bumps — compare removed vs added manifest versions
    major_bumps = _major_bumps(added, removed)

    return {
        "hardcoded_secret_added": secret_ct,
        "new_env_or_config_key": env_ct,
        "observability_delta": obs_removed - obs_added,   # net removed
        "bound_or_comparison_edit": compare_edits,
        "unused_import_or_dead_code_added": unused,
        "dependency_major_bump_count": major_bumps,
        "migration_reversibility": _migration_reversibility(added),
    }


def _rough_unused_imports(added: list[tuple[str, str]]) -> int:
    imports, bodies = [], []
    for path, t in added:
        s = t.strip()
        if s.startswith(("import ", "from ")) or re.match(r"^\s*import\s+\{", t):
            m = re.search(r"import\s+([A-Za-z0-9_]+)", s)
            if m:
                imports.append((m.group(1), path))
        else:
            bodies.append(t)
    body_text = "\n".join(bodies)
    return sum(1 for name, _ in imports if name not in body_text)


_DEP_VER = re.compile(r'([A-Za-z0-9_.\-/@]+)\D*?(\d+)\.(\d+)\.(\d+)')


def _dep_versions(pairs: list[tuple[str, str]]) -> dict[str, int]:
    """{package-name -> major version} parsed from manifest/lockfile lines."""
    out: dict[str, int] = {}
    for path, t in pairs:
        pf = config.path_flags(path)
        if not (pf["touches_dep_manifest"] or pf["touches_lockfile"]):
            continue
        m = _DEP_VER.search(t)
        if m:
            out[m.group(1).lower()] = int(m.group(2))
    return out


def _major_bumps(added: list[tuple[str, str]], removed: list[tuple[str, str]]) -> int:
    """Count packages whose MAJOR version increased (removed->added), not every
    version line. Approximate (name/version parsing is coarse across ecosystems),
    but no longer fires on every pinned dependency in a lockfile churn."""
    old = _dep_versions(removed)
    new = _dep_versions(added)
    return sum(1 for name, maj in new.items() if name in old and maj > old[name])


def _migration_reversibility(added: list[tuple[str, str]]):
    """destructive / irreversible / reversible / None."""
    destructive = re.compile(r"(?i)\b(drop\s+(table|column)|truncate|delete\s+from)\b")
    saw_migration = False
    for path, t in added:
        if config.path_flags(path)["touches_migration"]:
            saw_migration = True
            if destructive.search(t):
                return "destructive"
    return "reversible" if saw_migration else None

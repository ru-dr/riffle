"""
api_contract.py — Source 5 `api_contract_change`.

When a PR changes an API spec (OpenAPI / protobuf / GraphQL), classify the
change as additive (non-breaking) or breaking by diffing the base-version spec
against the head-version spec with the format's purpose-built tool:

  - OpenAPI  -> oasdiff
  - protobuf -> buf breaking
  - GraphQL  -> graphql-inspector

Same pattern as scanners.py: pull both versions with gitio.file_content_at,
write to temp files/dirs, shell out, parse the verdict. Emits ONE feature,
`api_contract_change`, aggregated to the max severity across all spec files in
the PR:

    None       -> no spec file changed, OR no matching tool installed (missing)
    "additive" -> spec changed, no breaking changes found
    "breaking" -> at least one breaking change found

Every tool is optional; if it isn't on PATH the affected spec degrades to None
(treated as missing), never a misleading value.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from shutil import which

import config
import gitio

# severity ordering for aggregation
_RANK = {None: -1, "additive": 0, "breaking": 1}
_UNRANK = {0: "additive", 1: "breaking"}


def compute(repo: str, base: str, head: str, changed: list[str],
            st: config.Settings) -> dict:
    if not getattr(st, "enable_api_contract", True):
        return {"api_contract_change": None}

    best = -1                       # highest severity seen; -1 = nothing usable
    saw_verdict = False
    for path in changed:
        if not _candidate_ext(path):
            continue                # cheap prefilter: skip non-spec files
        head_content = gitio.file_content_at(repo, head, path)
        base_content = gitio.file_content_at(repo, base, path)
        sniff = head_content if head_content is not None else base_content
        kind = _spec_kind(path, sniff)
        if kind is None:
            continue

        if head_content is None and base_content is not None:
            verdict = "breaking"        # spec removed entirely — contract gone
        elif base_content is None:
            verdict = "additive"        # brand-new spec — nothing to break
        else:
            verdict = _diff_one(kind, base_content, head_content, path, st)

        if verdict is not None:
            saw_verdict = True
            best = max(best, _RANK[verdict])

    if not saw_verdict:
        return {"api_contract_change": None}
    return {"api_contract_change": _UNRANK.get(best)}


def _candidate_ext(path: str) -> bool:
    return path.lower().endswith(
        (".proto", ".graphql", ".gql", ".yaml", ".yml", ".json"))


# --------------------------------------------------------------------------
# spec-file classification
# --------------------------------------------------------------------------
def _spec_kind(path: str, head_content: str | None) -> str | None:
    """Return 'openapi' | 'proto' | 'graphql' | None for a changed file."""
    low = path.lower()
    if low.endswith(".proto"):
        return "proto"
    if low.endswith((".graphql", ".gql")):
        return "graphql"
    if low.endswith((".yaml", ".yml", ".json")):
        # content-sniff: only OpenAPI/Swagger specs, not arbitrary yaml/json
        if head_content and re.search(r'("?openapi"?\s*:|"?swagger"?\s*:)',
                                      head_content[:2000]):
            return "openapi"
    return None


# --------------------------------------------------------------------------
# per-format diffing
# --------------------------------------------------------------------------
def _diff_one(kind: str, base_content: str, head_content: str, path: str,
              st: config.Settings) -> str | None:
    if kind == "openapi":
        return _oasdiff(base_content, head_content, path)
    if kind == "graphql":
        return _graphql_inspector(base_content, head_content)
    if kind == "proto":
        return _buf_breaking(base_content, head_content, path)
    return None


def _write_temp(content: str, suffix: str) -> str:
    fd, name = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(content)
    return name


def _oasdiff(base_content: str, head_content: str, path: str) -> str | None:
    if not which("oasdiff"):
        return None
    ext = ".json" if path.lower().endswith(".json") else ".yaml"
    b = _write_temp(base_content, ext)
    h = _write_temp(head_content, ext)
    try:
        proc = subprocess.run(
            ["oasdiff", "breaking", b, h, "-f", "json"],
            capture_output=True, text=True,
        )
        # oasdiff prints a JSON array of breaking changes; empty => additive.
        out = (proc.stdout or "").strip()
        if not out:
            return "additive"
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            # tool ran but output wasn't parseable JSON; fall back on exit code
            return "breaking" if proc.returncode not in (0,) else "additive"
        if isinstance(data, list):
            return "breaking" if len(data) > 0 else "additive"
        if isinstance(data, dict):
            # some versions wrap results; look for a non-empty breaking list
            items = data.get("breakingChanges") or data.get("changes") or []
            return "breaking" if items else "additive"
        return "additive"
    except Exception:
        return None
    finally:
        _cleanup(b, h)


def _graphql_inspector(base_content: str, head_content: str) -> str | None:
    if not which("graphql-inspector"):
        return None
    b = _write_temp(base_content, ".graphql")
    h = _write_temp(head_content, ".graphql")
    try:
        proc = subprocess.run(
            ["graphql-inspector", "diff", b, h],
            capture_output=True, text=True,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        low = text.lower()
        if "breaking" in low:
            return "breaking"
        # graphql-inspector exits non-zero when breaking changes exist
        if proc.returncode not in (0,):
            return "breaking"
        return "additive"
    except Exception:
        return None
    finally:
        _cleanup(b, h)


def _buf_breaking(base_content: str, head_content: str, path: str) -> str | None:
    """
    buf compares modules, not lone files, so we materialize each version in its
    own temp dir with a minimal buf.yaml and run `buf breaking`. If buf isn't
    installed or the module can't be built (common for isolated historical
    files), degrade to None.
    """
    if not which("buf"):
        return None
    fname = os.path.basename(path)
    base_dir = tempfile.mkdtemp()
    head_dir = tempfile.mkdtemp()
    try:
        for d, content in ((base_dir, base_content), (head_dir, head_content)):
            with open(os.path.join(d, fname), "w", encoding="utf-8",
                      errors="replace") as fh:
                fh.write(content)
            with open(os.path.join(d, "buf.yaml"), "w", encoding="utf-8") as fh:
                fh.write("version: v1\n")
        proc = subprocess.run(
            ["buf", "breaking", head_dir, "--against", base_dir],
            capture_output=True, text=True,
        )
        # buf exits non-zero and lists changes when breaking changes are found.
        if proc.returncode == 0:
            return "additive"
        out = (proc.stdout or "") + (proc.stderr or "")
        # distinguish "breaking changes found" from "buf failed to build"
        if re.search(r"(breaking|wire|changed|deleted|removed)", out, re.IGNORECASE):
            return "breaking"
        return None            # build/config error, not a real verdict
    except Exception:
        return None
    finally:
        _rmtree(base_dir)
        _rmtree(head_dir)


def _cleanup(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def _rmtree(d: str) -> None:
    import shutil
    try:
        shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass

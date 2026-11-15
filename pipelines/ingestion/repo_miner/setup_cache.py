"""
setup_cache.py: results of the per-repo setup stages that cost hours on a big repo and used to be redone on every start.

`author index`, `concurrency` and the SZZ "relevant commits" set (which the SZZ cache check needs before it can say
"from cache") each walk every PR of the repo through git. For n8n (23k PRs) that was about 2,700 s, 1,100 s and 4,700 s
on every restart, so each interrupted run paid about 2.5 h before it mined a PR. Their answers only change when the PR
list or the clone's history changes, so they are kept in one file per repo, `ci_cache/<owner>_<name>.setup.json`:

    {"v": 1, "sections": {"author_index": {"key": "<sha256>", "data": ...}, "concurrency": {...}, "szz_relevant": {...}}}

A section is used only when its key matches. The key is a hash of: this file's version, the section's name, the repo's
HEAD and whether the clone is shallow (a deeper clone or a fetch changes what git answers), the merge style the base
resolver chose, and, for every PR, the fields those stages read (number, timestamps, merge/head/base SHAs, unmerged
and stale-open flags, the at-open head and base). Anything else misses and the stage runs as before. Output is the
same either way: the cache stores what the stage returned.

Never fatal: an unreadable, torn or foreign file is a miss, and a failed write is a warning. Written atomically.
`--remine-repo` and a clean repo write remove the file, like the pinned selection.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import gitio

VERSION = 1


def path_for(cache_dir: str, owner: str, name: str) -> str:
    return os.path.join(cache_dir, f"{owner}_{name}.setup.json")


def _pr_row(pr: dict) -> list:
    opened = pr.get("_open") or {}
    return [pr.get("number"), pr.get("created_at"), pr.get("merged_at"), pr.get("closed_at"),
            pr.get("merge_commit_sha"), (pr.get("head") or {}).get("sha"), (pr.get("base") or {}).get("sha"),
            bool(pr.get("_unmerged")), pr.get("_git_base"), bool(pr.get("_stale_open")),
            opened.get("head"), opened.get("base")]


def key_for(section: str, repo: str, prs: list[dict], style: str | None = None) -> str:
    """The hash a section's data is stored under (see the module docstring)."""
    shallow = os.path.exists(os.path.join(repo, ".git", "shallow")) or os.path.exists(os.path.join(repo, "shallow"))
    h = hashlib.sha256()
    h.update(json.dumps([VERSION, section, gitio.rev_parse_safe(repo, "HEAD"), shallow, style],
                        separators=(",", ":")).encode())
    for pr in prs:
        h.update(json.dumps(_pr_row(pr), separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("v") != VERSION or not isinstance(doc.get("sections"), dict):
        return {}
    return doc["sections"]


def load(path: str, section: str, key: str):
    """The stored data of `section` if it was saved under `key`, else None."""
    entry = _read(path).get(section)
    if isinstance(entry, dict) and entry.get("key") == key and "data" in entry:
        return entry["data"]
    return None


def save(path: str, section: str, key: str, data) -> bool:
    """Store `data` for `section`, keeping the other sections. False (and a warning) when it cannot be written."""
    sections = _read(path)
    sections[section] = {"key": key, "data": data}
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"v": VERSION, "sections": sections}, fh, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError) as e:
        print(f"[warn] setup cache not written ({type(e).__name__}: {e}); the stage runs again next time",
              file=sys.stderr, flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False

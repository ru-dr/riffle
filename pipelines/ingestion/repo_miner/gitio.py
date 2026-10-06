"""
gitio.py — point-in-time git helpers.

Every function here answers a question *as of a given commit* (the PR's base
commit), so no information from after the PR opened can leak into a feature.
Built on raw `git` via subprocess to keep dependencies minimal and behavior
explicit. All commands run with `-C repo_path` so no chdir side effects.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterable


class GitError(RuntimeError):
    pass


def _run(repo: str, *args: str, check: bool = True) -> str:
    """Run a git command in `repo` and return stdout (text)."""
    cmd = ["git", "-C", repo, *args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise GitError(f"{' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.stdout


def rev_parse(repo: str, ref: str) -> str:
    return _run(repo, "rev-parse", ref).strip()


def default_branch(repo: str) -> str:
    """Best-effort default branch name."""
    try:
        out = _run(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip()
        return out.rsplit("/", 1)[-1]
    except GitError:
        for cand in ("main", "master"):
            try:
                rev_parse(repo, cand)
                return cand
            except GitError:
                continue
    return "HEAD"


def _parse_git_time(s: str) -> datetime:
    """
    Parse a git ISO-8601 commit time, tolerating the corrupt timezone offsets
    that appear in some old repo histories (e.g. '...+518:00'). Falls back to
    parsing just the datetime part as UTC, then to the epoch, rather than
    letting one bad commit crash a whole PR.

    NOTE: the extractor now reads commit times as Unix epoch seconds (%ct / %at
    via _parse_git_epoch), which have no timezone string to corrupt. This ISO
    parser is kept only for any remaining ISO inputs.
    """
    s = s.strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        try:
            return datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.fromtimestamp(0, tz=timezone.utc)


def _parse_git_epoch(s: str) -> datetime:
    """
    Parse a git commit time given as Unix epoch seconds (the %ct/%at format).
    This is an integer git stores natively, so it is immune to the corrupt
    timezone-offset strings that broke ISO parsing on some old histories.
    Falls back to the epoch only if the value is truly unparseable.
    """
    try:
        return datetime.fromtimestamp(int(s.strip()), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def commit_time(repo: str, sha: str) -> datetime:
    ts = _run(repo, "show", "-s", "--format=%ct", sha).strip()
    return _parse_git_epoch(ts)


def merge_base(repo: str, a: str, b: str) -> str:
    return _run(repo, "merge-base", a, b).strip()


def commit_parents(repo: str, sha: str) -> list[str]:
    """Parent SHAs of `sha` (2 for a merge commit, 1 for squash/rebase/normal,
    0 for a root commit). Used to pick the correct PR diff range per merge type."""
    out = _run(repo, "rev-list", "--parents", "-n", "1", sha, check=False).strip()
    parts = out.split()
    return parts[1:] if len(parts) > 1 else []


# --------------------------------------------------------------------------
# Commit walking
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Commit:
    sha: str
    author_email: str
    author_name: str
    committed: datetime
    subject: str
    parents: list[str] = field(default_factory=list)


_LOG_FMT = "%H%x1f%aE%x1f%aN%x1f%ct%x1f%P%x1f%s%x1e"


def iter_commits_with_files(
    repo: str, rev: str = "HEAD", before: datetime | None = None,
) -> list[tuple["Commit", list[tuple[int, int, str]]]]:
    """
    Like iter_commits, but returns each commit paired with its numstat rows
    (added, deleted, path), fetched in a SINGLE `git log --numstat` call instead
    of one `git show` per commit. On a large-history repo this replaces thousands
    of subprocess spawns with one — minutes to seconds, especially on Windows.
    """
    # \x1e starts each commit record; then the format fields; then numstat lines
    # %aE/%aN = mailmap-canonical author (so one person's two emails merge into
    # one identity); %ct = committer time as Unix epoch (corruption-proof).
    if before is not None:
        # Fast path: parse the whole repo's `git log --numstat` once, then per
        # call only list the reachable SHAs (cheap rev-list) and look them up.
        # Each commit's numstat doesn't depend on how it was reached, so the
        # result is identical to a direct `git log <rev> --before=...`.
        cache = _numstat_cache(repo)
        shas = _run(repo, "rev-list", f"--before={before.isoformat()}", rev).split()
        if all(sha in cache for sha in shas):
            return [cache[sha] for sha in shas]
    args = ["log", "--numstat", "-M", f"--format={_NUMSTAT_FMT}"]
    if before is not None:
        args.append(f"--before={before.isoformat()}")
    args.append(rev)
    return _parse_numstat_log(_run(repo, *args))


_NUMSTAT_FMT = "\x1e%H\x1f%aE\x1f%aN\x1f%ct\x1f%P\x1f%s"
_NUMSTAT_CACHE: dict[str, dict] = {}
_NUMSTAT_LOCK = threading.Lock()


def _numstat_cache(repo: str) -> dict:
    """{sha: (Commit, numstat rows)} for every commit on every ref, built once
    per repo per process."""
    key = os.path.abspath(repo)
    with _NUMSTAT_LOCK:
        return _numstat_cache_locked(repo, key)


def _numstat_cache_locked(repo: str, key: str) -> dict:
    if key not in _NUMSTAT_CACHE:
        _NUMSTAT_CACHE[key] = {
            c.sha: (c, rows) for c, rows in _stream_numstat_log(
                repo, "log", "--all", "--numstat", "-M", f"--format={_NUMSTAT_FMT}")}
    return _NUMSTAT_CACHE[key]


def _stream_numstat_log(repo: str, *args: str):
    """Yield parsed (Commit, rows) records from a `git log --numstat` as git
    writes them, instead of holding the whole output (hundreds of MB on a large
    repo) in memory as one string first. Same records as _parse_numstat_log on
    the same output; raises GitError on failure like _run(check=True)."""
    cmd = ["git", "-C", repo, *args]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err,
                                text=True, encoding="utf-8", errors="replace")
        rec: list[str] = []
        for line in proc.stdout:
            if line.startswith("\x1e"):
                if rec:
                    parsed = _parse_numstat_record("".join(rec))
                    if parsed:
                        yield parsed
                rec = [line[1:]]
            else:
                rec.append(line)
        if rec:
            parsed = _parse_numstat_record("".join(rec))
            if parsed:
                yield parsed
        if proc.wait() != 0:
            err.seek(0)
            raise GitError(f"{' '.join(cmd)}\n{err.read().decode(errors='replace').strip()}")


def _parse_numstat_log(out: str) -> list[tuple["Commit", list[tuple[int, int, str]]]]:
    result: list[tuple[Commit, list[tuple[int, int, str]]]] = []
    for rec in out.split("\x1e"):
        parsed = _parse_numstat_record(rec)
        if parsed:
            result.append(parsed)
    return result


def _parse_numstat_record(rec: str) -> tuple["Commit", list[tuple[int, int, str]]] | None:
    """One \\x1e-delimited `git log --numstat` record -> (Commit, rows)."""
    if not rec.strip():
        return None
    lines = rec.split("\n")
    parts = lines[0].split("\x1f")
    if len(parts) < 6:
        return None
    sha, ae, an, cI, par, subj = parts[:6]
    rows: list[tuple[int, int, str]] = []
    for ln in lines[1:]:
        ln = ln.rstrip("\r")
        if not ln.strip():
            continue
        cols = ln.split("\t")
        if len(cols) != 3:
            continue
        a, d, path = cols
        added = 0 if a == "-" else int(a)
        deleted = 0 if d == "-" else int(d)
        if " => " in path:
            path = _resolve_rename(path)
        rows.append((added, deleted, path))
    commit = Commit(
        sha=sha, author_email=ae.lower().strip(), author_name=an,
        committed=_parse_git_epoch(cI),
        subject=subj, parents=par.split() if par else [],
    )
    return commit, rows


def iter_commits(
    repo: str, rev: str = "HEAD", paths: Iterable[str] | None = None,
    before: datetime | None = None, max_count: int | None = None,
) -> list[Commit]:
    """
    Commits reachable from `rev`, newest first. If `before` is given, only
    commits strictly before that timestamp are returned (point-in-time gate).
    """
    args = ["log", f"--pretty=format:{_LOG_FMT}"]
    if before is not None:
        # git wants a parseable date; ISO works
        args.append(f"--before={before.isoformat()}")
    if max_count is not None:
        args.append(f"--max-count={max_count}")
    args.append(rev)
    if paths:
        args.append("--")
        args.extend(paths)
    out = _run(repo, *args)
    commits: list[Commit] = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 6:
            continue
        sha, ae, an, cI, par, subj = parts[:6]
        commits.append(
            Commit(
                sha=sha, author_email=ae.lower().strip(), author_name=an,
                committed=_parse_git_epoch(cI),
                subject=subj, parents=par.split() if par else [],
            )
        )
    return commits


def numstat(repo: str, sha: str) -> list[tuple[int, int, str]]:
    """(added, deleted, path) per file for a single commit vs its first parent."""
    out = _run(
        repo, "show", "--numstat", "--format=", "-M", sha, check=False
    )
    rows: list[tuple[int, int, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        a, d, path = cols
        added = 0 if a == "-" else int(a)
        deleted = 0 if d == "-" else int(d)
        # handle rename "old => new"
        if " => " in path:
            path = _resolve_rename(path)
        rows.append((added, deleted, path))
    return rows


def _resolve_rename(path: str) -> str:
    """New path from a numstat rename: "a/{b => c}/d.py" or "old.py => new.py".
    The braces, when present, are the ones around the arrow, so a path with
    literal braces elsewhere (e.g. "tests/ui/{foo}.rs => tests/ui/bar.rs")
    still resolves."""
    i = path.find(" => ")
    if i < 0:
        return path
    lb = path.rfind("{", 0, i)
    rb = path.find("}", i)
    if lb != -1 and rb != -1 and "}" not in path[lb:i]:
        return f"{path[:lb]}{path[i + 4:rb]}{path[rb + 1:]}".replace("//", "/")
    return path[i + 4:]


def diff_numstat(repo: str, base: str, head: str) -> list[tuple[int, int, str]]:
    """(added, deleted, path) for the whole base..head range (the PR diff)."""
    out = _run(repo, "diff", "--numstat", "-M", f"{base}...{head}", check=False)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        a, d, path = cols
        rows.append((0 if a == "-" else int(a),
                     0 if d == "-" else int(d),
                     _resolve_rename(path)))
    return rows


def changed_files(repo: str, base: str, head: str) -> list[str]:
    return [p for _, _, p in diff_numstat(repo, base, head)]


def tree_blobs(repo: str, sha: str) -> dict[str, str]:
    """{path: blob sha} for every file in the tree at `sha` (one git call)."""
    out = _run(repo, "ls-tree", "-r", "-z", sha, check=False)
    blobs: dict[str, str] = {}
    for ent in out.split("\0"):
        meta, _, path = ent.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            blobs[path] = parts[2]
    return blobs


def file_content_at(repo: str, sha: str, path: str) -> str | None:
    out = subprocess.run(
        ["git", "-C", repo, "show", f"{sha}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        return None
    return out.stdout


def file_loc_at(repo: str, sha: str, path: str) -> int:
    content = file_content_at(repo, sha, path)
    if content is None:
        return 0
    return content.count("\n") + (0 if content.endswith("\n") or not content else 1)


# --------------------------------------------------------------------------
# Blame (line-level history) — as of a base commit
# --------------------------------------------------------------------------
@dataclass(slots=True)
class BlameLine:
    final_lineno: int
    orig_commit: str
    author_email: str
    author_time: datetime


# Blame flags, configured once per run via configure_blame(). Defaults improve
# attribution accuracy: -w ignores whitespace-only changes, -M detects lines
# moved within the file, so a reformat/move is credited to the real author
# instead of the mover. -C (cross-file copy detection) is OFF by default because
# it is very slow on large histories; enable it per run if needed.
_BLAME_FLAGS: list[str] = ["-w", "-M"]
_BLAME_IGNORE_REVS: str | None = None


def configure_blame(ignore_revs_file: str | None = None,
                    ignore_whitespace: bool = True,
                    detect_moves: bool = True,
                    detect_copies: bool = False) -> None:
    """Set blame flags for this run and clear the blame cache so they take
    effect. Call once (e.g. at the start of mine_repo) before any blame."""
    global _BLAME_FLAGS, _BLAME_IGNORE_REVS
    flags: list[str] = []
    if ignore_whitespace:
        flags.append("-w")
    if detect_moves:
        flags.append("-M")
    if detect_copies:
        flags.append("-C")
    _BLAME_FLAGS = flags
    # Absolute: blame runs as `git -C <repo>`, which resolves a relative
    # --ignore-revs-file against the repo, not our cwd. A relative path made
    # every blame fail silently for repos that ship .git-blame-ignore-revs.
    _BLAME_IGNORE_REVS = (os.path.abspath(ignore_revs_file)
                          if ignore_revs_file and os.path.isfile(ignore_revs_file)
                          else None)
    blame_file.cache_clear()


@lru_cache(maxsize=64)
def blame_file(repo: str, sha: str, path: str) -> dict[int, BlameLine]:
    """
    Porcelain blame of `path` as of `sha`. Returns {lineno: BlameLine}.
    Used for mod_line_age, distinct authors, self-authored share, line-level
    ownership, etc. Cached: the same (sha, path) is blamed by both the history
    ownership features and the Source-3 line features within one PR, so we run
    git blame once and share it. Callers must treat the returned dict as
    read-only (they do). Blame flags (-w/-M, optional ignore-revs) come from
    configure_blame() so moves/whitespace/known-noise commits don't skew
    line ownership.
    """
    return _blame(repo, sha, path, [])


def blame_settings() -> tuple[list[str], str | None]:
    """The current blame flags and ignore-revs file, so a worker process (which
    does not inherit configure_blame()'s globals under spawn/forkserver) can be
    given the same settings via set_blame_settings()."""
    return list(_BLAME_FLAGS), _BLAME_IGNORE_REVS


def set_blame_settings(flags: list[str], ignore_revs: str | None) -> None:
    global _BLAME_FLAGS, _BLAME_IGNORE_REVS
    _BLAME_FLAGS = list(flags)
    _BLAME_IGNORE_REVS = ignore_revs
    blame_file.cache_clear()


# Blame failures used to vanish into an empty result, which silently emptied
# line features and SZZ labels. Count them, and warn once per process.
_BLAME_STATS = {"calls": 0, "fails": 0, "first_error": None}


def _note_blame_failure(path: str, stderr: str) -> None:
    _BLAME_STATS["fails"] += 1
    if _BLAME_STATS["first_error"] is None:
        msg = (stderr or "").strip().splitlines()
        _BLAME_STATS["first_error"] = f"{path}: {msg[0] if msg else 'no error text'}"
        print(f"[blame] warning: git blame failed ({_BLAME_STATS['first_error']}); "
              f"affected lines get no blame data", file=sys.stderr, flush=True)


def blame_stats() -> dict:
    return dict(_BLAME_STATS)


def _line_ranges(lines: Iterable[int], pad: int = 0,
                 gap: int = 3) -> list[tuple[int, int]]:
    """Sorted, merged (start, end) ranges covering `lines`, each widened by
    `pad` lines; ranges closer than `gap` lines are joined so one -L covers a
    whole hunk cluster."""
    ranges: list[tuple[int, int]] = []
    for ln in sorted(set(lines)):
        lo, hi = max(1, ln - pad), ln + pad
        if ranges and lo <= ranges[-1][1] + gap:
            ranges[-1] = (ranges[-1][0], max(hi, ranges[-1][1]))
        else:
            ranges.append((lo, hi))
    return ranges


# Context lines blamed around each requested line. -M scores a moved block by
# the size of the blame entry it sits in, so a bare -L range can make a short
# moved block miss the threshold and be blamed on the mover instead. ±20 lines
# matched full-file blame on every line in the flask/itsdangerous fix history.
BLAME_LINES_PAD = 20


def blame_lines(repo: str, sha: str, path: str, lines: Iterable[int],
                pad: int = BLAME_LINES_PAD) -> dict[int, BlameLine]:
    """
    Blame only `lines` of `path` as of `sha` (one -L per merged range, one git
    call), padded by `pad` context lines. Same flags as blame_file(). Without
    -M the requested lines always match a full-file blame; with -M the padding
    makes that hold in practice (see BLAME_LINES_PAD). Uncached: SZZ blames
    each (parent, path) once, so a cache would never hit and would only evict
    blame_file() entries. Falls back to a full blame if git rejects a range
    (a start past the end of the file; an end past it is clamped by git).
    """
    ranges = _line_ranges(lines, pad)
    if not ranges:
        return {}
    result = _blame(repo, sha, path, ranges)
    if result is None:
        result = _blame(repo, sha, path, []) or {}
    return result


def _blame(repo: str, sha: str, path: str,
           ranges: list[tuple[int, int]]) -> dict[int, BlameLine] | None:
    """Run porcelain blame (whole file when `ranges` is empty). Returns {} for a
    failed whole-file blame and None for a failed ranged one."""
    cmd = ["git", "-C", repo, "blame", "--line-porcelain", *_BLAME_FLAGS]
    if _BLAME_IGNORE_REVS:
        cmd += ["--ignore-revs-file", _BLAME_IGNORE_REVS]
    for start, end in ranges:
        cmd += ["-L", f"{start},{end}"]
    cmd += [sha, "--", path]
    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    _BLAME_STATS["calls"] += 1
    if out.returncode != 0:
        _note_blame_failure(path, out.stderr)
        return None if ranges else {}
    result: dict[int, BlameLine] = {}
    cur_commit = None
    cur_email = None
    cur_time = None
    final_lineno = None
    for line in out.stdout.splitlines():
        if not line:
            continue
        # header line: <sha> <orig_lineno> <final_lineno> [<num_lines>]
        parts = line.split()
        if (len(parts) >= 3 and len(parts[0]) == 40
                and all(c in "0123456789abcdef" for c in parts[0])):
            cur_commit = parts[0]
            try:
                final_lineno = int(parts[2])
            except ValueError:
                final_lineno = None
        elif line.startswith("author-mail "):
            cur_email = line[len("author-mail "):].strip().strip("<>").lower()
        elif line.startswith("author-time "):
            cur_time = datetime.fromtimestamp(int(line.split()[1]), tz=timezone.utc)
        elif line.startswith("\t"):
            if final_lineno is not None and cur_commit is not None:
                result[final_lineno] = BlameLine(
                    final_lineno=final_lineno, orig_commit=cur_commit,
                    author_email=cur_email or "", author_time=cur_time or _epoch(),
                )
    return result


def deleted_or_modified_lines(repo: str, base: str, head: str, path: str) -> list[int]:
    """
    Line numbers *in the base version* of `path` that this PR deletes or
    modifies. These are the lines we blame for line-level history.
    """
    out = _run(
        repo, "diff", "-U0", "-M", f"{base}...{head}", "--", path, check=False
    )
    old_lines: list[int] = []
    for line in out.splitlines():
        if line.startswith("@@"):
            # @@ -a,b +c,d @@
            try:
                seg = line.split(" ")[1]  # -a,b
                start = int(seg[1:].split(",")[0])
                count = 1
                if "," in seg:
                    count = int(seg.split(",")[1])
                old_lines.extend(range(start, start + count))
            except (IndexError, ValueError):
                continue
    return old_lines


def _epoch() -> datetime:
    return datetime.fromtimestamp(0, tz=timezone.utc)


@lru_cache(maxsize=1)
def _warn_shallow(repo: str) -> bool:
    return False


def changed_files_of_commit(repo: str, sha: str) -> list[str]:
    return [p for _, _, p in numstat(repo, sha)]


def rev_parse_safe(repo: str, ref: str) -> str | None:
    """rev-parse that returns None instead of raising when the ref doesn't
    resolve (e.g. a merge commit's second parent on a squash merge)."""
    out = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet", ref],
        capture_output=True, text=True,
    )
    sha = out.stdout.strip()
    return sha or None


def commits_behind(repo: str, base: str, target: str) -> int | None:
    """Number of commits reachable from `target` but not from `base` — i.e. how
    far `base` is behind `target`. Returns None if the range can't be computed."""
    out = _run(repo, "rev-list", "--count", f"{base}..{target}", check=False).strip()
    try:
        return int(out)
    except ValueError:
        return None


def commits_in_range(repo: str, base: str, head: str) -> set:
    """The set of commit SHAs introduced by base..head (a PR's own commits)."""
    out = _run(repo, "rev-list", f"{base}..{head}", check=False)
    return set(out.split())


def changed_lines_by_file(repo: str, base: str, head: str) -> dict[str, set[int]]:
    """
    {head-side path -> set of base-side line numbers the PR modifies}, parsed
    from a -U0 diff. Used to detect hunk-level overlap between concurrent PRs.
    A file with no capturable hunks (binary) still appears with an empty set so
    it counts for file-level overlap.
    """
    out = _run(repo, "diff", "-U0", "-M", f"{base}...{head}", check=False)
    result: dict[str, set[int]] = {}
    cur: str | None = None
    for line in out.splitlines():
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[-1] if " b/" in line else None
            if cur:
                result.setdefault(cur, set())
        elif line.startswith("@@") and cur is not None:
            try:
                seg = line.split(" ")[1]           # -a,b
                start = int(seg[1:].split(",")[0])
                cnt = int(seg.split(",")[1]) if "," in seg else 1
                result[cur].update(range(start, start + max(cnt, 1)))
            except (IndexError, ValueError):
                continue
    return result


def landed_commit_index(repo: str, branch: str, owner: str, name: str) -> dict:
    """
    For repos that land PRs outside GitHub's merge button (merge scripts, merge
    bots, rebase-and-push), map PR number -> [(sha, commit_time)] for commits on
    the default branch that reference that PR, newest first. Recognised links:
      - "Pull Request resolved: https://github.com/OWNER/NAME/pull/N"  (PyTorch)
      - "PR-URL: https://github.com/OWNER/NAME/pull/N"                 (Node.js)
      - "Closes #N" / "Closed #N" / "Resolves #N"                      (Spark etc.)
      - subject line ending in "(#N)"                                   (squash convention)
    One `git log --first-parent` call; only consulted for closed-unmerged PRs.
    """
    import re
    slug = re.escape(f"{owner}/{name}")
    url_pat = re.compile(
        rf"(?:pull request resolved|pr-url):\s*https?://github\.com/{slug}/pull/(\d+)",
        re.IGNORECASE)
    kw_pat = re.compile(
        rf"\b(?:close[sd]?|resolve[sd]?)\s+(?:{slug})?#(\d+)\b", re.IGNORECASE)
    subj_pat = re.compile(r"\(#(\d+)\)\s*$")
    # classic GitHub merge-commit subject: "Merge pull request #N from ..."
    merge_pat = re.compile(r"merge pull request #(\d+)", re.IGNORECASE)

    out = _run(repo, "log", branch, "--first-parent",
               "--format=%H%x1f%ct%x1f%B%x1e", check=False)
    index: dict = {}
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f", 2)
        if len(parts) < 3:
            continue
        sha, ctime, body = parts[0].strip(), parts[1].strip(), parts[2]
        when = _parse_git_epoch(ctime)
        nums = set(url_pat.findall(body)) | set(kw_pat.findall(body))
        nums |= set(merge_pat.findall(body))
        subject = body.strip().splitlines()[0] if body.strip() else ""
        nums |= set(subj_pat.findall(subject))
        for n in nums:
            index.setdefault(int(n), []).append((sha, when))   # git log is newest-first
    return index

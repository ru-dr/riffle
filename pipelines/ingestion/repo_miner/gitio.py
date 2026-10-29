"""
gitio.py — point-in-time git helpers.

Every function here answers a question *as of a given commit* (the PR's base
commit), so no information from after the PR opened can leak into a feature.
Built on raw `git` via subprocess to keep dependencies minimal and behavior
explicit. All commands run with `-C repo_path` so no chdir side effects, and
with `core.quotePath=false` so non-ASCII paths come back as written (names with
a tab, newline, quote or backslash are still C-quoted by git, which is why the
numstat / name-status readers use `-z` and the patch reader unquotes).

Every call has a timeout (see git_timeout). A call that times out is killed and
raises GitTimeout (a GitError), so a hang becomes "unobserved" for the PR that
hit it instead of a stalled repo or a silent 0.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterable, NamedTuple

import config
import runlog


# A stop hook: while set, no git subprocess is started. mine_repo shares its
# own STOP event here, so a Stop ends in-flight workers at their next git call.
STOP = threading.Event()


class GitStopped(Exception):
    """A git call was refused because STOP is set. Not a GitError, so no caller
    that tolerates git failures swallows it as one."""


def _check_stop() -> None:
    if STOP.is_set():
        raise GitStopped()


class GitError(RuntimeError):
    pass


class GitTimeout(GitError):
    """A git call ran past its timeout and was killed. The answer is unknown."""


class FetchError(GitError):
    """A fetch failed for a reason other than a ref being gone (network, auth,
    rate limit, timeout). The refs it covered are unobserved, not missing.
    `specs` are the refspecs/SHAs that were not fetched."""

    def __init__(self, message: str, specs: list[str] | None = None):
        super().__init__(message)
        self.specs = list(specs or [])


# gc.auto and maintenance.auto off: a fetch of thousands of PR heads must not start an automatic repack
# of a big clone (minutes of memory and disk with no output, which looks like a hang)
_GIT_CONFIG = ("-c", "core.quotePath=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false")

# Timeouts in seconds, read at call time so a run (or a test) can change them
# with the environment. 0 or a negative value turns a timeout off.
DEFAULT_TIMEOUT_S = 300.0        # RIFFLE_GIT_TIMEOUT_S: any ordinary git call
BLAME_TIMEOUT_S = 120.0          # RIFFLE_GIT_BLAME_TIMEOUT_S: one blame
LONG_TIMEOUT_S = 3600.0          # RIFFLE_GIT_LONG_TIMEOUT_S: whole-history walks, fetches
MAX_BLOB_BYTES = 4 * 1024 * 1024  # RIFFLE_MAX_BLOB_BYTES: bigger files are not read into memory
_UNSET = object()


def _env_seconds(name: str, default: float) -> float | None:
    raw = os.environ.get(name)
    if raw is not None:
        try:
            default = float(raw)
        except ValueError:
            pass
    return default if default > 0 else None


def git_timeout() -> float | None:
    return _env_seconds("RIFFLE_GIT_TIMEOUT_S", DEFAULT_TIMEOUT_S)


def blame_timeout() -> float | None:
    return _env_seconds("RIFFLE_GIT_BLAME_TIMEOUT_S", BLAME_TIMEOUT_S)


def long_timeout() -> float | None:
    return _env_seconds("RIFFLE_GIT_LONG_TIMEOUT_S", LONG_TIMEOUT_S)


def max_blob_bytes() -> int | None:
    raw = os.environ.get("RIFFLE_MAX_BLOB_BYTES")
    try:
        v = int(raw) if raw is not None else MAX_BLOB_BYTES
    except ValueError:
        v = MAX_BLOB_BYTES
    return v if v > 0 else None


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and anything it spawned (git helpers, ssh), so a helper
    cannot keep the output pipe open after git itself is gone. The process must
    have been started with start_new_session=True."""
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        proc.kill()


def _git_cmd(repo: str, *args: str) -> list[str]:
    return ["git", *_GIT_CONFIG, "-C", repo, *args]


def _run(repo: str, *args: str, check: bool = True, timeout=_UNSET) -> str:
    """Run a git command in `repo` and return stdout (text). Raises GitTimeout
    (even with check=False: a timed-out answer is unknown, not empty)."""
    _check_stop()
    cmd = _git_cmd(repo, *args)
    limit = git_timeout() if timeout is _UNSET else timeout
    try:
        with runlog.span("git", "call", cmd=args[0] if args else "", slow_s=runlog.SLOW_CALL_S):
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=limit, stdin=subprocess.DEVNULL, start_new_session=True,
            )
    except subprocess.TimeoutExpired:
        runlog.debug("git", "timeout", cmd=args[0] if args else "", after_s=limit)
        raise GitTimeout(f"timed out after {limit:.0f}s: {' '.join(cmd)}") from None
    if proc.returncode != 0:
        runlog.debug("git", "non-zero exit", cmd=args[0] if args else "", rc=proc.returncode,
                     err=proc.stderr.strip()[:200])
    if check and proc.returncode != 0:
        raise GitError(f"{' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.stdout


def rev_parse(repo: str, ref: str) -> str:
    return _run(repo, "rev-parse", ref).strip()


def default_branch(repo: str) -> str:
    """Best-effort default branch name."""
    try:
        out = _run(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip()
        # only the remote prefix comes off: a branch like "release/1.x" keeps its slash
        return out.removeprefix("refs/remotes/origin/")
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
    # (old_path, new_path) for each rename git detected in this commit's own
    # diff (-M), filled by the numstat log readers; the history walk uses it
    # to join a file's history across a rename
    renames: list[tuple[str, str]] = field(default_factory=list)


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
    args = ["log", "--numstat", "-z", "-M", f"--format={_NUMSTAT_FMT}"]
    if before is not None:
        args.append(f"--before={before.isoformat()}")
    args.append(rev)
    return _parse_numstat_log(_run(repo, *args, timeout=long_timeout()))


_NUMSTAT_FMT = "\x1e%H\x1f%aE\x1f%aN\x1f%ct\x1f%P\x1f%s"
# repo -> {sha: (Commit, rows)}. Each entry is a whole repo's history, so only
# the most recent repos are kept (a run mines one repo at a time).
_NUMSTAT_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_NUMSTAT_MAX_REPOS = 2
_NUMSTAT_LOCK = threading.Lock()


def _numstat_cache(repo: str) -> dict:
    """{sha: (Commit, numstat rows)} for every commit on every ref, built once
    per repo per process."""
    key = os.path.abspath(repo)
    with _NUMSTAT_LOCK:
        return _numstat_cache_locked(repo, key)


def _numstat_cache_locked(repo: str, key: str) -> dict:
    if key in _NUMSTAT_CACHE:
        _NUMSTAT_CACHE.move_to_end(key)
    else:
        _NUMSTAT_CACHE[key] = {
            c.sha: (c, rows) for c, rows in _stream_numstat_log(
                repo, "log", "--all", "--numstat", "-z", "-M", f"--format={_NUMSTAT_FMT}")}
        while len(_NUMSTAT_CACHE) > _NUMSTAT_MAX_REPOS:
            _NUMSTAT_CACHE.popitem(last=False)
    return _NUMSTAT_CACHE[key]


def _stream_numstat_log(repo: str, *args: str):
    """Yield parsed (Commit, rows) records from a `git log --numstat -z` as git
    writes them, instead of holding the whole output (hundreds of MB on a large
    repo) in memory as one string first. Same records as _parse_numstat_log on
    the same output; raises GitError on failure like _run(check=True). The git
    process is killed and reaped if the consumer stops early or an error
    escapes, so it never outlives this generator."""
    _check_stop()
    cmd = _git_cmd(repo, *args)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                text=True, encoding="utf-8", errors="replace")
        try:
            buf = ""
            while True:
                chunk = proc.stdout.read(1 << 20)
                if not chunk:
                    break
                buf += chunk
                *recs, buf = buf.split("\x1e")
                for rec in recs:
                    parsed = _parse_numstat_record(rec)
                    if parsed:
                        yield parsed
            parsed = _parse_numstat_record(buf)
            if parsed:
                yield parsed
            if proc.wait() != 0:
                err.seek(0)
                raise GitError(f"{' '.join(cmd)}\n{err.read().decode(errors='replace').strip()}")
        finally:
            if proc.poll() is None:
                _kill_tree(proc)
            proc.wait()
            proc.stdout.close()


def _parse_numstat_log(out: str) -> list[tuple["Commit", list[tuple[int, int, str]]]]:
    result: list[tuple[Commit, list[tuple[int, int, str]]]] = []
    for rec in out.split("\x1e"):
        parsed = _parse_numstat_record(rec)
        if parsed:
            result.append(parsed)
    return result


class NumstatEntry(NamedTuple):
    added: int
    deleted: int
    path: str                    # the new path for a rename
    old_path: str | None = None  # set for a rename
    binary: bool = False         # git printed "-\t-": no textual diff


def _parse_numstat_z(blob: str) -> list[NumstatEntry]:
    """Entries of a `--numstat -z` listing: `added TAB deleted TAB path NUL`, or
    for a rename `added TAB deleted TAB NUL old NUL new NUL`. Paths are verbatim
    (no quoting), so names with tabs, newlines or ` => ` read back as written."""
    toks = blob.split("\0")
    out: list[NumstatEntry] = []
    i = 0
    while i < len(toks):
        tok = toks[i].lstrip("\n")
        i += 1
        if not tok:
            continue
        cols = tok.split("\t", 2)
        if len(cols) != 3:
            continue
        a, d, path = cols
        old = None
        if path == "":
            if i + 1 >= len(toks):
                break
            old, path = toks[i], toks[i + 1]
            i += 2
        try:
            added = 0 if a == "-" else int(a)
            deleted = 0 if d == "-" else int(d)
        except ValueError:
            continue
        out.append(NumstatEntry(added, deleted, path, old, a == "-" and d == "-"))
    return out


def _parse_numstat_record(rec: str) -> tuple["Commit", list[tuple[int, int, str]]] | None:
    """One \\x1e-delimited `git log --numstat -z` record -> (Commit, rows)."""
    if not rec.strip("\0\n "):
        return None
    head, _, rest = rec.partition("\0")
    head = head.split("\n", 1)[0]
    parts = head.split("\x1f")
    if len(parts) < 6:
        return None
    sha, ae, an, cI, par, subj = parts[:6]
    rows: list[tuple[int, int, str]] = []
    renames: list[tuple[str, str]] = []
    for e in _parse_numstat_z(rest):
        rows.append((e.added, e.deleted, e.path))
        if e.old_path is not None:
            renames.append((e.old_path, e.path))
    commit = Commit(
        sha=sha, author_email=ae.lower().strip(), author_name=an,
        committed=_parse_git_epoch(cI),
        subject=subj, parents=par.split() if par else [], renames=renames,
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
    """(added, deleted, path) per file for a single commit vs its first parent
    (a merge commit shows nothing here; see numstat_first_parent)."""
    out = _run(repo, "show", "--numstat", "-z", "--format=", "-M", sha, check=False)
    return [(e.added, e.deleted, e.path) for e in _parse_numstat_z(out)]


def numstat_first_parent(repo: str, sha: str) -> list[tuple[int, int, str]]:
    """(added, deleted, path) of what `sha` changed against its first parent:
    for a merge commit, the whole merged branch. [] for a root commit."""
    parents = commit_parents(repo, sha)
    if not parents:
        return []
    out = _run(repo, "diff", "--numstat", "-z", "-M", parents[0], sha, check=False)
    return [(e.added, e.deleted, e.path) for e in _parse_numstat_z(out)]


def _resolve_rename(path: str) -> str:
    """New path from a (non -z) numstat rename: "a/{b => c}/d.py" or
    "old.py => new.py". The braces, when present, are the ones around the arrow,
    so a path with literal braces elsewhere (e.g. "tests/ui/{foo}.rs =>
    tests/ui/bar.rs") still resolves. The readers here use -z and do not need
    this; it stays for callers that hold a rendered numstat line."""
    i = path.find(" => ")
    if i < 0:
        return path
    lb = path.rfind("{", 0, i)
    rb = path.find("}", i)
    if lb != -1 and rb != -1 and "}" not in path[lb:i]:
        return f"{path[:lb]}{path[i + 4:rb]}{path[rb + 1:]}".replace("//", "/")
    return path[i + 4:]


# --------------------------------------------------------------------------
# One diff per (base, head), read once
# --------------------------------------------------------------------------
@dataclass(slots=True)
class FileDiff:
    """One file of a patch. Line lists hold content without the +/- marker."""
    old_path: str | None
    new_path: str
    status: str = "M"            # M A D R C
    binary: bool = False
    hunks: list[tuple[int, int, int, int]] = field(default_factory=list)  # old start/count, new start/count
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _unquote_c(s: str) -> str:
    """A git C-quoted path ("a\\tb", "d\\303\\251.txt") back to text."""
    if not (len(s) >= 2 and s[0] == '"' and s[-1] == '"'):
        return s
    esc = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}
    body, out, i = s[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < len(body) and j < i + 4 and body[j] in "01234567":
                    j += 1
                out.append(int(body[i + 1:j], 8) & 0xFF)
                i = j
                continue
            out.append(esc.get(nxt, ord(nxt)))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", "replace")


def _header_paths(s: str) -> tuple[str | None, str | None]:
    """(old, new) from the text after `diff --git `, when it can be told. With
    the a/ b/ prefixes forced, an unrenamed file reads `a/P b/P` (P may hold
    spaces); a renamed one is resolved from its rename lines instead."""
    if s.startswith('"'):
        m = re.fullmatch(r'("(?:[^"\\]|\\.)*") ("(?:[^"\\]|\\.)*")', s)
        if m:
            return _strip_ab(_unquote_c(m.group(1))), _strip_ab(_unquote_c(m.group(2)))
        return None, None
    h = (len(s) - 1) // 2
    if len(s) % 2 == 1 and s[h] == " " and s[:2] == "a/" and s[h + 1:h + 3] == "b/" \
            and s[2:h] == s[h + 3:]:
        return s[2:h], s[h + 3:]
    m = re.fullmatch(r'a/(.*) ("(?:[^"\\]|\\.)*")', s)
    if m:
        return m.group(1), _strip_ab(_unquote_c(m.group(2)))
    return None, None


def _strip_ab(p: str) -> str:
    return p[2:] if p[:2] in ("a/", "b/") else p


def _patch_name(s: str) -> str | None:
    """The path on a `--- ` / `+++ ` line (None for /dev/null)."""
    s = s.rstrip("\t")        # git pads a name that has a space with a trailing tab
    if s == "/dev/null":
        return None
    return _strip_ab(_unquote_c(s))


def parse_patch(text: str) -> list[FileDiff]:
    """Parse `git diff` output (with --src-prefix=a/ --dst-prefix=b/) into files.
    A hunk's body is read by the line counts in its @@ header, never by the
    first character of each line, so a content line that itself starts with
    `--`, `++` or `diff --git` is content, not a header."""
    lines = text.split("\n")
    n, i = len(lines), 0
    out: list[FileDiff] = []
    cur: FileDiff | None = None
    rename_from = rename_to = None
    while i < n:
        line = lines[i]
        i += 1
        if line.startswith("diff --git "):
            old, new = _header_paths(line[11:])
            cur = FileDiff(old_path=old, new_path=new or old or "")
            out.append(cur)
            rename_from = rename_to = None
            continue
        if cur is None:
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                continue
            os_, oc, ns, nc = (int(m.group(1)), 1 if m.group(2) is None else int(m.group(2)),
                               int(m.group(3)), 1 if m.group(4) is None else int(m.group(4)))
            cur.hunks.append((os_, oc, ns, nc))
            orem, nrem = oc, nc
            while (orem > 0 or nrem > 0) and i < n:
                body = lines[i]
                i += 1
                c = body[:1]
                if c == "\\":
                    continue
                if c == "+":
                    nrem -= 1
                    cur.added.append(body[1:].rstrip("\r"))
                elif c == "-":
                    orem -= 1
                    cur.removed.append(body[1:].rstrip("\r"))
                else:
                    orem -= 1
                    nrem -= 1
            while i < n and lines[i].startswith("\\"):
                i += 1
        elif line.startswith("rename from "):
            rename_from = _unquote_c(line[12:])
            cur.status, cur.old_path = "R", rename_from
        elif line.startswith("rename to "):
            rename_to = _unquote_c(line[10:])
            cur.status, cur.new_path = "R", rename_to
        elif line.startswith("copy from "):
            cur.status, cur.old_path = "C", _unquote_c(line[10:])
        elif line.startswith("copy to "):
            cur.status, cur.new_path = "C", _unquote_c(line[8:])
        elif line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith("--- "):
            name = _patch_name(line[4:])
            if name is not None:
                cur.old_path = name
            elif cur.status == "M":
                cur.status = "A"
        elif line.startswith("+++ "):
            name = _patch_name(line[4:])
            if name is not None:
                cur.new_path = name
            else:
                cur.status = "D"
                cur.new_path = cur.old_path or cur.new_path
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            cur.binary = True
    return out


def _name_status_z(blob: str) -> list[tuple[str, str | None, str]]:
    """(status code, old path or None, path) from `--name-status -z`: `M NUL path
    NUL`, or `R100 NUL old NUL new NUL`."""
    toks = blob.split("\0")
    out, i = [], 0
    while i < len(toks):
        code = toks[i].strip("\n")
        i += 1
        if not code:
            continue
        if code[0] in "RC":
            if i + 1 >= len(toks):
                break
            out.append((code, toks[i], toks[i + 1]))
            i += 2
        else:
            if i >= len(toks):
                break
            out.append((code, None, toks[i]))
            i += 1
    return out


class PRDiff:
    """The three views of one `base...head` diff (numstat, name-status, a -U0
    patch), each read from git at most once however many features ask."""

    def __init__(self, repo: str, base: str, head: str):
        self.repo, self.base, self.head = repo, base, head
        self._numstat: list[NumstatEntry] | None = None
        self._name_status: list[tuple[str, str | None, str]] | None = None
        self._patch: list[FileDiff] | None = None

    @property
    def numstat_entries(self) -> list[NumstatEntry]:
        if self._numstat is None:
            out = _run(self.repo, "diff", "--numstat", "-z", "-M", f"{self.base}...{self.head}",
                       check=False)
            self._numstat = _parse_numstat_z(out)
        return self._numstat

    @property
    def name_status(self) -> list[tuple[str, str | None, str]]:
        if self._name_status is None:
            out = _run(self.repo, "diff", "--name-status", "-z", "-M",
                       f"{self.base}...{self.head}", check=False)
            self._name_status = _name_status_z(out)
        return self._name_status

    @property
    def patch(self) -> list[FileDiff]:
        if self._patch is None:
            self._patch = _patch_files(self.repo, self.base, self.head)
        return self._patch


def _patch_files(repo: str, base: str, head: str) -> list[FileDiff]:
    out = _run(repo, "diff", "-U0", "-M", "--no-color", "--no-ext-diff", "--no-textconv",
               "--src-prefix=a/", "--dst-prefix=b/", f"{base}...{head}", check=False)
    return parse_patch(out)


_PR_DIFFS: "OrderedDict[tuple, PRDiff]" = OrderedDict()
_PR_DIFFS_MAX = 64
_PR_DIFFS_LOCK = threading.Lock()


def pr_diff(repo: str, base: str, head: str) -> PRDiff:
    """The shared PRDiff for (repo, base, head). SHAs never change, so entries
    never go stale; the cache is a small LRU."""
    key = (os.path.abspath(repo), base, head)
    with _PR_DIFFS_LOCK:
        d = _PR_DIFFS.get(key)
        if d is None:
            d = _PR_DIFFS[key] = PRDiff(repo, base, head)
            while len(_PR_DIFFS) > _PR_DIFFS_MAX:
                _PR_DIFFS.popitem(last=False)
        else:
            _PR_DIFFS.move_to_end(key)
        return d


def diff_numstat(repo: str, base: str, head: str) -> list[tuple[int, int, str]]:
    """(added, deleted, path) for the whole base..head range (the PR diff)."""
    return [(e.added, e.deleted, e.path) for e in pr_diff(repo, base, head).numstat_entries]


def count_undiffable(repo: str, base: str, head: str) -> int:
    """Files git could not produce a textual diff for (binary or too large): the
    rows whose numstat columns are both a literal '-'. Pure renames and mode
    changes ('0\\t0') are not counted."""
    return sum(1 for e in pr_diff(repo, base, head).numstat_entries if e.binary)


def diff_name_status(repo: str, base: str, head: str) -> list[tuple[str, str | None, str]]:
    """(status code, old path or None, path) per file of the base...head diff."""
    return list(pr_diff(repo, base, head).name_status)


def rename_map(repo: str, base: str, head: str) -> dict[str, str]:
    """{new path: old path} for every file the base...head diff renames. A
    renamed file's base-side name is the old one, so every lookup against the
    base commit (history, blame, size, complexity) must use it."""
    return {new: old for code, old, new in pr_diff(repo, base, head).name_status
            if code.startswith("R") and old}


def changed_files(repo: str, base: str, head: str) -> list[str]:
    return [p for _, _, p in diff_numstat(repo, base, head)]


_TREE_BLOBS: "OrderedDict[tuple, dict[str, str]]" = OrderedDict()
_TREE_BLOBS_MAX = 8
_TREE_BLOBS_LOCK = threading.Lock()


def tree_blobs(repo: str, sha: str) -> dict[str, str]:
    """{path: blob sha} for every file in the tree at `sha` (one git call,
    cached by tree sha so commits with the same tree share it). The dict is
    shared: callers must not modify it."""
    tree = _run(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}", check=False).strip()
    key = (os.path.abspath(repo), tree)
    if tree:
        with _TREE_BLOBS_LOCK:
            hit = _TREE_BLOBS.get(key)
            if hit is not None:
                _TREE_BLOBS.move_to_end(key)
                return hit
    out = _run(repo, "ls-tree", "-r", "-z", tree or sha, check=False)
    blobs: dict[str, str] = {}
    for ent in out.split("\0"):
        meta, _, path = ent.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            blobs[path] = parts[2]
    if tree:
        with _TREE_BLOBS_LOCK:
            _TREE_BLOBS[key] = blobs
            while len(_TREE_BLOBS) > _TREE_BLOBS_MAX:
                _TREE_BLOBS.popitem(last=False)
    return blobs


class BlobRead(NamedTuple):
    content: str | None   # None when absent or over the size cap
    loc: int | None       # lines (counted even when the content is over the cap); None when absent
    status: str           # "ok" | "absent" | "too_large"


def read_blob(repo: str, spec: str, cap: int | None | object = _UNSET) -> BlobRead:
    """Read one blob (`<sha>:<path>` or a blob sha) in one pass: its text, capped
    at `cap` bytes (default max_blob_bytes()), and its line count, which is
    counted over the whole blob even when the text is dropped for size. Raises
    GitTimeout if git stalls; a blob that does not exist is `absent`."""
    _check_stop()
    limit = max_blob_bytes() if cap is _UNSET else cap
    cmd = _git_cmd(repo, "cat-file", "blob", spec)
    t_s = git_timeout()
    with runlog.span("git", "call", cmd="cat-file", slow_s=runlog.SLOW_CALL_S):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        timed_out = threading.Event()

        def _kill():
            timed_out.set()
            _kill_tree(proc)

        timer = threading.Timer(t_s, _kill) if t_s else None
        if timer:
            timer.start()
        try:
            kept: list[bytes] = []
            size = newlines = 0
            last = b""
            over = False
            while True:
                chunk = proc.stdout.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                newlines += chunk.count(b"\n")
                last = chunk[-1:]
                if not over:
                    if limit is not None and size > limit:
                        over = True
                        kept = []
                    else:
                        kept.append(chunk)
            rc = proc.wait()
        finally:
            if timer:
                timer.cancel()
            if proc.poll() is None:
                _kill_tree(proc)
                proc.wait()
            proc.stdout.close()
    if timed_out.is_set():
        raise GitTimeout(f"timed out after {t_s:.0f}s: {' '.join(cmd)}")
    if rc != 0:
        return BlobRead(None, None, "absent")
    loc = newlines + (0 if size == 0 or last == b"\n" else 1)
    if over:
        return BlobRead(None, loc, "too_large")
    return BlobRead(b"".join(kept).decode("utf-8", "replace"), loc, "ok")


def file_content_at(repo: str, sha: str, path: str) -> str | None:
    """The file's text at `sha`, or None when it is absent or over the size cap
    (max_blob_bytes)."""
    return read_blob(repo, f"{sha}:{path}").content


def file_loc_at(repo: str, sha: str, path: str) -> int:
    """Lines in the file at `sha`; 0 when it is absent."""
    return read_blob(repo, f"{sha}:{path}").loc or 0


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


class _BlameCache:
    """LRU of whole-file blames, bounded by entries AND total lines held: one
    blame of a huge file can outweigh dozens of small ones, so a count alone
    does not bound memory."""

    def __init__(self, max_entries: int = 64, max_lines: int = 400_000):
        self.max_entries, self.max_lines = max_entries, max_lines
        self._d: "OrderedDict[tuple, dict]" = OrderedDict()
        self._lines = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            val = self._d.get(key)
            if val is not None:
                self._d.move_to_end(key)
            return val

    def put(self, key, val: dict) -> None:
        with self._lock:
            if key in self._d:
                self._lines -= len(self._d.pop(key))
            self._d[key] = val
            self._lines += len(val)
            while self._d and (len(self._d) > self.max_entries
                               or (self._lines > self.max_lines and len(self._d) > 1)):
                _, old = self._d.popitem(last=False)
                self._lines -= len(old)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._lines = 0


_BLAME_CACHE = _BlameCache()


def blame_file(repo: str, sha: str, path: str) -> dict[int, BlameLine]:
    """
    Porcelain blame of `path` as of `sha`. Returns {lineno: BlameLine}.
    Used for mod_line_age, distinct authors, self-authored share, line-level
    ownership, etc. Cached: the same (sha, path) is blamed by both the history
    ownership features and the Source-3 line features within one PR, so we run
    git blame once and share it. Callers must treat the returned dict as
    read-only (they do). Blame flags (-w/-M, optional ignore-revs) come from
    configure_blame() so moves/whitespace/known-noise commits don't skew
    line ownership. Only a non-empty result is cached: an empty one is a failed
    blame, a missing file or an empty file, and the first two must be retried,
    not remembered.
    """
    key = (repo, sha, path)
    hit = _BLAME_CACHE.get(key)
    if hit is not None:
        _BLAME_TL.failed = False
        return hit
    result = _blame(repo, sha, path, [])
    if result:
        _BLAME_CACHE.put(key, result)
    return result


blame_file.cache_clear = _BLAME_CACHE.clear   # type: ignore[attr-defined]


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
    _BLAME_TIMED_OUT.clear()


# Blame failures used to vanish into an empty result, which silently emptied
# line features and SZZ labels. Count them, and warn once per process.
_BLAME_STATS = {"calls": 0, "fails": 0, "absent": 0, "timeouts": 0, "first_error": None}

# Whether the last blame on this thread failed (timeout or git error), so a caller can tell a
# failed blame (unobserved: null) from an empty one (a measured zero). Reset by every call.
_BLAME_TL = threading.local()
# (repo, path, ranged) whose blame timed out (a ranged blame is cheap where a whole-file one is not): a huge generated file times out every time, so the
# next PR touching it fails at once instead of waiting out the limit again (a PR of eight
# bundles once took 966 s). Per process, so a new run tries again.
_BLAME_TIMED_OUT: set[tuple[str, str, bool]] = set()


def blame_failed() -> bool:
    """True when the last blame_file/blame_lines call on this thread failed. An empty result with
    this False is a real one (no such file, empty file)."""
    return bool(getattr(_BLAME_TL, "failed", False))


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
    cmd = _git_cmd(repo, "blame", "--line-porcelain", *_BLAME_FLAGS)
    if _BLAME_IGNORE_REVS:
        cmd += ["--ignore-revs-file", _BLAME_IGNORE_REVS]
    for start, end in ranges:
        cmd += ["-L", f"{start},{end}"]
    cmd += [sha, "--", path]
    _check_stop()
    _BLAME_TL.failed = False
    if (repo, path, bool(ranges)) in _BLAME_TIMED_OUT:
        _BLAME_TL.failed = True
        _BLAME_STATS["fails"] += 1
        return {}
    limit = blame_timeout()
    _BLAME_STATS["calls"] += 1
    try:
        with runlog.span("git", "call", cmd="blame", slow_s=runlog.SLOW_CALL_S):
            out = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=limit, stdin=subprocess.DEVNULL,
            )
    except subprocess.TimeoutExpired:
        # unobserved, like any failed blame; {} for a ranged call too, so the
        # caller does not retry the whole file and wait out a second timeout
        _BLAME_STATS["timeouts"] += 1
        _BLAME_TIMED_OUT.add((repo, path, bool(ranges)))
        _BLAME_TL.failed = True
        _note_blame_failure(path, f"timed out after {limit:.0f}s")
        return {}
    if out.returncode != 0:
        if "no such path" in (out.stderr or ""):
            # Expected, not a failure: the file doesn't exist at that commit
            # (a PR that adds it, blamed at its base). It has no history, which
            # is exactly what an empty result means. No full-blame retry.
            _BLAME_STATS["absent"] += 1
            return {}
        _BLAME_TL.failed = True
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


def _old_lines(f: FileDiff) -> list[int]:
    lines: list[int] = []
    for os_, oc, _ns, _nc in f.hunks:
        lines.extend(range(os_, os_ + oc))
    return lines


def deleted_or_modified_lines(repo: str, base: str, head: str, path: str,
                              old_path: str | None = None) -> list[int]:
    """
    Line numbers *in the base version* of `path` that this PR deletes or
    modifies. These are the lines we blame for line-level history.

    `path` is the head-side name. A file the PR renames shows no old lines
    unless `old_path` (its base-side name, see rename_map) is given: without it
    the rename's source is hidden and the file reads as added.
    """
    for f in pr_diff(repo, base, head).patch:
        if f.new_path != path:
            continue
        if f.status in ("R", "C") and (old_path is None or f.old_path != old_path):
            return []
        return _old_lines(f)
    return []


def deleted_or_modified_lines_many(repo: str, base: str, head: str,
                                   paths: list[str]) -> dict[str, list[int]]:
    """deleted_or_modified_lines for many paths from ONE `git diff`, instead of
    one subprocess per path. Rename-aware: a renamed path (status R) gets the
    lines its edit deletes or modifies, numbered in the OLD file, so the caller
    blames the old name (rename_map) at `base`. A copy (C) names no source in
    rename_map and still reads as added, [].
    Read uncached: SZZ asks about each (parent, commit) pair once."""
    found: dict[str, list[int]] = {}
    for f in _patch_files(repo, base, head):
        found[f.new_path] = [] if f.status == "C" else _old_lines(f)
    return {p: found.get(p, []) for p in paths}


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
    _check_stop()
    try:
        out = subprocess.run(
            _git_cmd(repo, "rev-parse", "--verify", "--quiet", ref),
            capture_output=True, text=True, timeout=git_timeout(), stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise GitTimeout(f"timed out: git rev-parse {ref}") from None
    sha = out.stdout.strip()
    return sha or None


def head_commits_with_files(repo: str) -> list[tuple["Commit", list[tuple[int, int, str]]]]:
    """iter_commits_with_files(repo, "HEAD"), answered from the repo-wide
    numstat cache when it is loaded (one cheap rev-list for HEAD's commits and
    their order), so SZZ doesn't run and hold a second full-history
    `git log --numstat`. Same commits, rows and order; falls back to the
    direct log if any commit is missing from the cache."""
    cache = _numstat_cache(repo)
    shas = _run(repo, "rev-list", "HEAD").split()
    if all(sha in cache for sha in shas):
        return [cache[sha] for sha in shas]
    return iter_commits_with_files(repo, "HEAD")


def rename_pairs(repo: str) -> list[tuple[str, str]]:
    """(old_path, new_path) for every rename on any ref, in one log. Uses a
    looser similarity (30%) than blame's default and includes merge commits,
    so it finds a superset of the renames blame follows: callers use it to
    decide what blame could reach, where finding too many is harmless."""
    out = _run(repo, "log", "--all", "-m", "-M30%", "--diff-filter=R",
               "--name-status", "-z", "--format=", check=False, timeout=long_timeout())
    return [(old, new) for code, old, new in _name_status_z(out)
            if code.startswith("R") and old is not None]


def existing_commits(repo: str, shas: list[str]) -> set[str]:
    """The subset of `shas` that are commits in the clone, in one
    `git cat-file --batch-check` instead of a rev-parse per SHA."""
    shas = [s for s in dict.fromkeys(shas) if s]
    if not shas:
        return set()
    _check_stop()
    try:
        proc = subprocess.run(_git_cmd(repo, "cat-file", "--batch-check"),
                              input="\n".join(shas) + "\n", capture_output=True, text=True,
                              timeout=git_timeout())
    except subprocess.TimeoutExpired:
        raise GitTimeout("timed out: git cat-file --batch-check") from None
    return {line.split()[0] for line in proc.stdout.splitlines()
            if len(line.split()) >= 2 and line.split()[1] == "commit"}


# What git says when the remote simply does not have a ref or object. Anything
# else (network, auth, rate limit, a timeout) says nothing about the ref.
_MISSING_REF = ("couldn't find remote ref", "not our ref", "unadvertised object",
                "no such remote ref", "did not match any", "remote ref does not exist")
FETCH_RETRIES = 2
FETCH_RETRY_DELAY_S = 2.0


def _ref_missing(err: Exception) -> bool:
    msg = str(err).lower()
    return not isinstance(err, GitTimeout) and any(m in msg for m in _MISSING_REF)


def _fetch_specs(repo: str, specs: list[str]) -> None:
    """Fetch refspecs/SHAs from origin without local refs. One missing ref
    fails a whole fetch, so when git says a ref is missing the batch is split in
    halves (log n rounds per bad ref) and a single missing ref is skipped
    (callers check what arrived). Any other failure is retried, then raises
    FetchError naming the specs not fetched: the refs are unobserved, not gone."""
    if not specs:
        return
    err: GitError | None = None
    for attempt in range(FETCH_RETRIES + 1):
        try:
            _run(repo, "fetch", "--quiet", "--no-tags", "origin", *specs,
                 timeout=long_timeout())
            return
        except GitError as e:
            if _ref_missing(e):
                if len(specs) == 1:
                    return                        # this one is gone; callers check
                mid = len(specs) // 2
                failed: list[str] = []
                for half in (specs[:mid], specs[mid:]):
                    try:
                        _fetch_specs(repo, half)
                    except FetchError as fe:
                        failed.extend(fe.specs)
                if failed:
                    raise FetchError(f"fetch failed for {len(failed)} ref(s)", failed)
                return
            err = e
            if attempt < FETCH_RETRIES:
                time.sleep(FETCH_RETRY_DELAY_S * (attempt + 1))
    raise FetchError(f"fetch failed for {len(specs)} ref(s): "
                     f"{str(err).splitlines()[-1] if err else ''}", specs)


def _fetch_all(repo: str, specs: list[str], chunk: int, strict: bool) -> list[str]:
    """Fetch `specs` in chunks; the specs that could not be fetched (and not
    because they are gone). With strict, the first such failure raises."""
    failed: list[str] = []
    for i in range(0, len(specs), chunk):
        try:
            _fetch_specs(repo, specs[i:i + chunk])
        except FetchError as e:
            if strict:
                raise
            failed.extend(e.specs)
    if failed:
        print(f"[git] warning: could not fetch {len(failed)} ref(s) (network or server "
              f"error, not missing refs); they are unobserved", file=sys.stderr, flush=True)
    return failed


def fetch_pr_heads(repo: str, numbers: list[int], chunk: int = 200,
                   strict: bool = False) -> list[int]:
    """Fetch PR commits (refs/pull/N/head) from origin WITHOUT creating local
    refs: the objects land in the clone, but nothing points at them, so
    `git log --all` scans (history cache, revert and hotfix search) never see
    unmerged work. Callers check heads with existing_commits. Chunked to keep
    the command line short. Returns the PR numbers whose fetch FAILED for a
    reason other than the ref being gone: their heads are unobserved, so a
    caller must not read "not in the clone" as "gone". With strict, raises
    FetchError instead."""
    prefix = "refs/pull/"
    failed = _fetch_all(repo, [f"{prefix}{n}/head" for n in numbers], chunk, strict)
    return [int(s[len(prefix):].split("/")[0]) for s in failed]


def fetch_shas(repo: str, shas: list[str], chunk: int = 200,
               strict: bool = False) -> list[str]:
    """Fetch commits by SHA from origin, without local refs (GitHub serves
    commits a force-push left behind). Skips SHAs already in the clone. Returns
    the SHAs whose fetch failed for a reason other than being gone (see
    fetch_pr_heads)."""
    have = existing_commits(repo, shas)
    todo = [s for s in dict.fromkeys(shas) if s and s not in have]
    return _fetch_all(repo, todo, chunk, strict)


def count_before(repo: str, base: str, tip: str, when: datetime) -> int | None:
    """Commits in base..tip committed at or before `when`: how far the base
    branch had moved past `base` by then. None if git fails."""
    out = _run(repo, "rev-list", "--count", f"--before={int(when.timestamp())}",
               f"{base}..{tip}", check=False).strip()
    return int(out) if out.isdigit() else None


def first_parent_chain(repo: str, head: str, stop: str | None = None,
                       limit: int = 1000) -> list[tuple[str, int]]:
    """(sha, committer epoch) along head's first-parent line, newest first,
    excluding commits reachable from `stop`. For a PR branch this is the
    branch's own history, without commits merged in from the base."""
    args = ["log", "--first-parent", f"--max-count={limit}", "--format=%H %ct", head]
    if stop:
        args.append(f"^{stop}")
    out = []
    for line in _run(repo, *args, check=False).splitlines():
        sha, _, ct = line.partition(" ")
        if sha and ct.isdigit():
            out.append((sha, int(ct)))
    return out


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
    {head-side path -> set of base-side line numbers the PR modifies}, from the
    -U0 patch. Used to detect hunk-level overlap between concurrent PRs.
    A file with no capturable hunks (binary) still appears with an empty set so
    it counts for file-level overlap. Paths come from the patch's own file
    lines, so a name containing " b/" or a special character reads correctly.
    """
    result: dict[str, set[int]] = {}
    for f in pr_diff(repo, base, head).patch:
        lines = result.setdefault(f.new_path, set())
        for os_, oc, _ns, _nc in f.hunks:
            lines.update(range(os_, os_ + max(oc, 1)))
    return result


def landed_commit_index(repo: str, branch: str, owner: str, name: str) -> dict:
    """
    For repos that land PRs outside GitHub's merge button (merge scripts, merge
    bots, rebase-and-push), map PR number -> [(sha, commit_time)] for commits on
    the default branch that reference that PR, newest first. Recognised links:
      - "Pull Request resolved: https://github.com/OWNER/NAME/pull/N"  (PyTorch)
      - "PR-URL: https://github.com/OWNER/NAME/pull/N"                 (Node.js)
      - "Merge pull request #N" and a subject ending "(#N)"            (GitHub)
      - "Closes #N" / "Closed #N" / "Resolves #N"                      (Spark etc.)
    One `git log --first-parent` call; only consulted for closed-unmerged PRs.

    A link is strong (URL, merge subject, "(#N)") or weak ("Closes #N", which
    also names the issue a later commit fixes). When a PR has any strong hit
    its weak hits are dropped, so a later commit that only says "Closes #N"
    cannot stretch the PR's landed range. A revert commit is only linked to its
    own PR (merge subject or "(#N)"): the numbers in its body name the PR it
    undoes, not the one it is.
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
               "--format=%H%x1f%ct%x1f%B%x1e", check=False, timeout=long_timeout())
    strong_idx: dict = {}
    weak_idx: dict = {}
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f", 2)
        if len(parts) < 3:
            continue
        sha, ctime, body = parts[0].strip(), parts[1].strip(), parts[2]
        when = _parse_git_epoch(ctime)
        subject = body.strip().splitlines()[0] if body.strip() else ""
        strong = set(merge_pat.findall(subject)) | set(subj_pat.findall(subject))
        reverting = config.is_revert_text(body)
        weak: set = set()
        if not reverting:
            strong |= set(url_pat.findall(body)) | set(merge_pat.findall(body))
            weak = set(kw_pat.findall(body))
        for n in strong:
            strong_idx.setdefault(int(n), []).append((sha, when))   # git log is newest-first
        for n in weak - strong:
            weak_idx.setdefault(int(n), []).append((sha, when))
    index = dict(strong_idx)
    for n, hits in weak_idx.items():
        if n not in index:
            index[n] = hits
    return index


# --------------------------------------------------------------------------
# Long network commands (clone): a stall watchdog instead of a total timeout
# --------------------------------------------------------------------------
CLONE_STALL_S = 600.0     # RIFFLE_CLONE_STALL_S: no output for this long = stalled


def run_with_stall_watchdog(cmd: list[str], stall_s: float | None | object = _UNSET,
                            cwd: str | None = None) -> tuple[int, str]:
    """Run `cmd` (a git clone or fetch with --progress, which writes progress to
    stderr) and kill it when it prints nothing for `stall_s` seconds. A big
    clone may legitimately take hours, so total time is not limited, only
    silence is. Returns (returncode, last stderr text); raises GitTimeout on a
    stall, after killing and reaping the process."""
    _check_stop()
    limit = _env_seconds("RIFFLE_CLONE_STALL_S", CLONE_STALL_S) if stall_s is _UNSET else stall_s
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    last = [time.monotonic()]
    tail = bytearray()

    def _pump():
        while True:
            chunk = proc.stderr.read1(4096) if hasattr(proc.stderr, "read1") else proc.stderr.read(4096)
            if not chunk:
                return
            last[0] = time.monotonic()
            tail.extend(chunk)
            del tail[:-2000]

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    try:
        while True:
            try:
                rc = proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                if limit and time.monotonic() - last[0] > limit:
                    _kill_tree(proc)
                    proc.wait()
                    t.join(timeout=5)
                    raise GitTimeout(f"no output for {limit:.0f}s, killed: {' '.join(cmd)}")
        t.join(timeout=5)
        return rc, tail.decode("utf-8", "replace")
    finally:
        if proc.poll() is None:
            _kill_tree(proc)
            proc.wait()
        proc.stderr.close()

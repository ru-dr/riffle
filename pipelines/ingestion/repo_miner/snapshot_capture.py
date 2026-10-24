"""
snapshot_capture.py — what PR snapshots need from mining (step 1 of 2).

Snapshots are training rows describing a PR at later moments while it was
open (snapshots.py builds them). Most of what they need can be rebuilt from
stored data at any time, but two things can't once a repo's clone is deleted
and force-pushed commits expire on GitHub:

- the head commits the PR had while it was open, and the code features of
  each, computed against its fork point;
- the raw PR object, timeline and force-pushes.

So, during mining, each in-scope PR (llm_scope "training": outside
contributors plus a sample) gets one capture record:

  pr          the PR object, trimmed
  events      the issue timeline, trimmed
  pushes      [(pushed_at, before_sha)] force-pushes, or None if unreadable
  head_line   [(time, sha)] each moment the PR's head changed while open
  merge_outcome  optional: whether merge-outcome mining was on (--no-merge-outcome)
  heads       up to snapshot_max_heads of those heads, each with the code
              features (diff sizes, path flags, deterministic checks,
              scanners, dependencies, API contract) of fork-point...head

The head at a moment t follows ADR 0010's rule for the head at open: the
newest commit, on the first-parent line that was live at t, committed at or
before t. The live line starts at the head that the first force-push after t
replaced, or at the PR's final head. Commit time stands in for push time,
which GitHub doesn't record for ordinary pushes.

Git history and blame features are not recomputed per head: they describe
the repo at the fork point and change little while a PR is open.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import config
import gitio
from features import api_contract, deps_cve, deterministic, diff_stats, scanners


def _ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- trimming what is stored ------------------------------------------------

_USER_KEYS = ("login", "type")
_EVENT_KEYS = ("event", "created_at", "submitted_at", "author_association", "state", "sha",
               "commit_id")
BODY_CHARS = 4000


def _user(u) -> dict | None:
    return {k: u.get(k) for k in _USER_KEYS} if isinstance(u, dict) else None


def slim_event(ev: dict) -> dict:
    out = {k: ev[k] for k in _EVENT_KEYS if ev.get(k) is not None}
    for k in ("actor", "user"):
        if ev.get(k):
            out[k] = _user(ev[k])
    if ev.get("event") == "reviewed" and ev.get("id") is not None:
        out["id"] = ev["id"]                  # lets a later review_dismissed event point at it
    if isinstance(ev.get("dismissed_review"), dict):
        d = ev["dismissed_review"]            # the review's state before it was dismissed
        out["dismissed_review"] = {"review_id": d.get("review_id"), "state": d.get("state")}
    if ev.get("label"):
        out["label"] = {"name": (ev["label"] or {}).get("name")}
    for k in ("committer", "author"):
        if isinstance(ev.get(k), dict) and ev[k].get("date"):
            out[k] = {"date": ev[k]["date"]}
    if ev.get("parents") is not None:
        out["parents"] = len(ev["parents"])
    if ev.get("rename"):
        out["rename"] = {k: ev["rename"].get(k) for k in ("from", "to")}
    if ev.get("body"):
        out["body"] = ev["body"][:BODY_CHARS]
    return out


def slim_pr(pr: dict) -> dict:
    return {
        "number": pr.get("number"), "title": pr.get("title"),
        "body": (pr.get("body") or "")[:BODY_CHARS] or None,
        "created_at": pr.get("created_at"), "closed_at": pr.get("closed_at"),
        "merged_at": pr.get("merged_at"), "draft": pr.get("draft"),
        "user": _user(pr.get("user")), "author_association": pr.get("author_association"),
        "base": {k: (pr.get("base") or {}).get(k) for k in ("ref", "sha")},
        "head": {k: (pr.get("head") or {}).get(k) for k in ("ref", "sha")},
        "stale_open": bool(pr.get("_stale_open")),
    }


# ---- heads over time ----------------------------------------------------------

class HeadLine:
    """The PR's head commit as a function of time (see module docstring)."""

    def __init__(self, repo: str, final: str, api_base: str, pushes: list | None):
        self.repo, self.final, self.api_base = repo, final, api_base
        self.pushes = sorted(((_ts(t), b) for t, b in (pushes or []) if _ts(t) and b),
                             key=lambda x: x[0])
        self._chains: dict[str, list[tuple[str, float]]] = {}

    def _chain(self, start: str) -> list[tuple[str, float]]:
        if start not in self._chains:
            self._chains[start] = list(gitio.first_parent_chain(self.repo, start, self.api_base))
        return self._chains[start]

    def _start_at(self, t: datetime) -> str:
        later = [b for when, b in self.pushes if when > t]
        return later[0] if later else self.final

    def at(self, t: datetime) -> str | None:
        start = self._start_at(t)
        if not gitio.rev_parse_safe(self.repo, f"{start}^{{commit}}"):
            return None
        return next((sha for sha, ct in self._chain(start) if ct <= t.timestamp()), None)

    def changes(self, opened: datetime, until: datetime) -> list[tuple[datetime, str]]:
        """[(time, sha)] each moment in [opened, until] the head changed."""
        moments = {opened}
        for when, _ in self.pushes:
            if opened < when <= until:
                moments.add(when)
        starts = {self._start_at(m) for m in moments} | {self.final}
        for start in starts:
            if not gitio.rev_parse_safe(self.repo, f"{start}^{{commit}}"):
                continue
            for _sha, ct in self._chain(start):
                when = datetime.fromtimestamp(ct, tz=timezone.utc)
                if opened < when <= until:
                    moments.add(when)
        line: list[tuple[datetime, str]] = []
        for m in sorted(moments):
            sha = self.at(m)
            if sha and (not line or line[-1][1] != sha):
                line.append((m, sha))
        return line


def pick_heads(line: list[tuple[datetime, str]], cap: int) -> list[tuple[datetime, str]]:
    """Up to cap distinct heads from the line: the first (at open), the last,
    and the rest evenly spread in time."""
    seen: dict[str, datetime] = {}
    for when, sha in line:
        seen.setdefault(sha, when)
    distinct = sorted(seen.items(), key=lambda kv: kv[1])
    if len(distinct) <= cap:
        return [(w, s) for s, w in distinct]
    keep = {0, len(distinct) - 1}
    inner = cap - 2
    for k in range(1, inner + 1):
        keep.add(round(k * (len(distinct) - 1) / (inner + 1)))
    return [(distinct[i][1], distinct[i][0]) for i in sorted(keep)][:cap]


def code_features(repo: str, base: str, head: str, st: config.Settings, as_of=None) -> dict:
    """The per-diff features of base...head, the same functions mine_pr uses.
    as_of is the moment the head was the PR's head: advisories published after
    it are not counted (deps_cve), so a snapshot does not see the future."""
    changed = [p for _, _, p in gitio.diff_numstat(repo, base, head)]
    renames = gitio.rename_map(repo, base, head)
    out: dict = {}
    out.update(diff_stats.compute(repo, base, head, st))
    out.update(deterministic.compute(repo, base, head, st))
    out.update(scanners.compute(repo, base, head, changed, st, renames=renames))
    out.update(deps_cve.compute(repo, base, head, changed, st, as_of=as_of, renames=renames))
    out.update(api_contract.compute(repo, base, head, changed, st, renames=renames))
    return out


def capture_pr(repo: str, pr: dict, row: dict, events: list[dict] | None,
               st: config.Settings) -> dict | None:
    """The capture record for one PR, or None when there is nothing to
    capture (no timeline, or the head line can't be read)."""
    if events is None:
        return None
    opened = _ts(pr.get("created_at"))
    final = (pr.get("head") or {}).get("sha")
    api_base = (pr.get("base") or {}).get("sha")
    if not opened or not final or not api_base:
        return None
    if not gitio.rev_parse_safe(repo, f"{api_base}^{{commit}}"):
        return None
    until = _ts(pr.get("merged_at") or pr.get("closed_at")) or datetime.now(timezone.utc)
    pushes = pr.get("_pushes")
    hl = HeadLine(repo, final, api_base, pushes)
    # the first moment is open + the rework grace, the same cut as ADR 0010
    start = opened + timedelta(seconds=max(0, st.rework_grace_s))
    try:
        line = hl.changes(start, max(start, until))
    except gitio.GitError:
        return None
    heads = []
    for when, sha in pick_heads(line, st.snapshot_max_heads):
        rec = {"sha": sha, "first_seen": _iso(when)}
        if sha == row.get("head_sha"):
            rec["base_sha"] = row.get("base_sha")
            rec["at_open"] = True             # its features are the mined row's
        else:
            try:
                base = gitio.merge_base(repo, api_base, sha)
                rec["base_sha"] = base
                rec["features"] = code_features(repo, base, sha, st, as_of=when)
            except gitio.GitError as e:
                rec["error"] = str(e)[:200]
        heads.append(rec)
    return {
        "repo": row.get("repo"), "pr_number": pr.get("number"),
        "pr": slim_pr(pr), "events": [slim_event(e) for e in events],
        "pushes": None if pushes is None else [list(p) for p in pushes],
        "head_line": [[_iso(w), s] for w, s in line], "heads": heads,
        "open_head": row.get("head_sha"), "open_snapshot": row.get("open_snapshot"),
        # optional: older captures lack it and assemble as merge outcomes on. With
        # --no-merge-outcome every mined PR merged, so merge rates stay null in snapshots too
        "merge_outcome": bool(st.enable_merge_outcome),
    }


# ---- what capture cost (logged per repo) -----------------------------------------

_STATS_LOCK = threading.Lock()
STATS = {"prs": 0, "heads": 0, "secs": 0.0}


def note(record: dict | None, secs: float) -> None:
    with _STATS_LOCK:
        STATS["secs"] += secs
        if record:
            STATS["prs"] += 1
            STATS["heads"] += len(record.get("heads") or [])


def take_stats() -> dict:
    """The counters since the last call, then reset (one repo per miner run)."""
    with _STATS_LOCK:
        out = dict(STATS)
        STATS.update(prs=0, heads=0, secs=0.0)
    return out


# ---- files -------------------------------------------------------------------

CAPTURE_FILE = "capture.jsonl"     # one record per in-scope PR
INDEX_FILE = "pr_index.jsonl"      # one merge-signal sidecar per mined PR


def heal_tail(path: str) -> None:
    """Make an append-only jsonl file safe to append to. A run killed mid-write
    leaves a last line without its newline; the next record would be glued onto
    it. A complete record that only lacks the newline gets one; a fragment that
    does not parse is cut off (its row is re-mined on resume)."""
    try:
        size = os.path.getsize(path)
        if not size:
            return
        with open(path, "rb+") as fh:
            fh.seek(max(0, size - 1))
            if fh.read(1) == b"\n":
                return
            fh.seek(0)
            data = fh.read()
            cut = data.rfind(b"\n") + 1
            try:
                json.loads(data[cut:].decode("utf-8"))
                fh.seek(0, os.SEEK_END)
                fh.write(b"\n")
            except ValueError:
                fh.truncate(cut)
    except OSError:
        pass


def trim_jsonl(path: str, keep: set, key: str) -> set:
    """Rewrite a jsonl file (atomically) with only the records whose `key` is in
    `keep`, the last record per key winning, and no torn lines. Returns the keys
    kept. A missing file is nothing to trim."""
    if not os.path.isfile(path):
        return set()
    last: dict = {}
    order: list = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            k = rec.get(key) if isinstance(rec, dict) else None
            if k in keep:
                if k in last:
                    order.remove(k)
                last[k] = line if line.endswith("\n") else line + "\n"
                order.append(k)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(last[k] for k in order)
    os.replace(tmp, path)
    return set(last)


class Writer:
    """Appends capture records and the repo's PR index under one directory per
    repo. Rows are committed in PR order with the miner's checkpoint, so a
    resumed run continues the same files; a fresh run starts them over.

    On resume, `keep` (the PR numbers the checkpoint holds, plus the lead-in
    PRs) trims both files to those PRs first: a capture written just before a
    crash, whose row never reached the checkpoint, is dropped and then written
    again when the PR is re-mined, so no PR appears twice. have_index and
    have_capture say which PRs the files then hold."""

    def __init__(self, directory: str, resume: bool, keep: set | None = None):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        cap_path = os.path.join(directory, CAPTURE_FILE)
        idx_path = os.path.join(directory, INDEX_FILE)
        self.have_capture: set = set()
        self.have_index: set = set()
        if resume:
            if keep is not None:
                self.have_capture = trim_jsonl(cap_path, keep, "pr_number")
                self.have_index = trim_jsonl(idx_path, keep, "number")
            else:
                heal_tail(cap_path)
                heal_tail(idx_path)
        mode = "a" if resume else "w"
        self._cap = open(cap_path, mode, encoding="utf-8")
        self._idx = open(idx_path, mode, encoding="utf-8")

    def write(self, capture: dict | None, sidecar: dict | None) -> None:
        # idempotent per PR: a PR already in a file is not written to it twice
        if capture and (capture.get("pr_number") is None
                        or capture["pr_number"] not in self.have_capture):
            self._cap.write(json.dumps(capture, default=str) + "\n")
            self._cap.flush()
            self.have_capture.add(capture.get("pr_number"))
        if sidecar and (sidecar.get("number") is None
                        or sidecar["number"] not in self.have_index):
            self._idx.write(json.dumps(sidecar, default=str) + "\n")
            self._idx.flush()
            self.have_index.add(sidecar.get("number"))

    def close(self) -> None:
        self._cap.close()
        self._idx.close()


def read_jsonl(path: str) -> list[dict]:
    """The records of a jsonl file. A line that does not parse is skipped: a run killed
    mid-write leaves a torn last line, and the PR it belonged to is re-mined on resume."""
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out

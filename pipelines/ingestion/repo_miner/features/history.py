"""
history.py — Source 2 repo-history features, all as of the PR's base commit.

These are the "knows this repo" signals: per-file revert/fix/churn history,
complexity, hotspots, ownership, coupling, author behavior, repo baselines,
and concurrency. Everything walks commits strictly before the base commit's
time so nothing from the future leaks in.

Per-file values are returned as dicts keyed by path; aggregate.py collapses
them to fixed max/mean/sum columns. Repo-level scalars are returned flat.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import statistics
import threading
from collections import defaultdict, Counter, OrderedDict
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from itertools import chain

import config
import gitio


def _is_fix(subject: str, st: config.Settings) -> bool:
    return config.is_fix_text(subject)


def _is_revert(subject: str, st: config.Settings) -> bool:
    return config.is_revert_text(subject)


def _is_hotfix(subject: str, st: config.Settings) -> bool:
    return config.is_hotfix_text(subject)


def _decay(days: float, half_life: float) -> float:
    return 0.5 ** (days / half_life) if half_life > 0 else 0.0


_BOT_MARKERS = ("[bot]", "dependabot", "renovate", "github-actions", "greenkeeper")


def _is_bot_email(email: str) -> bool:
    """An automation account by its commit email, the same markers mine_repo
    uses for author_is_bot. Bots are not people who know a file, so they are
    left out of NDEV and the line-ownership features."""
    e = (email or "").lower()
    return any(m in e for m in _BOT_MARKERS)


class _BoundedCache:
    """A small thread-safe LRU, so a cache keyed by file content cannot grow
    for the length of a run."""

    def __init__(self, max_entries: int):
        self.max_entries = max_entries
        self._d: "OrderedDict" = OrderedDict()
        self._lock = threading.Lock()

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._d

    def __getitem__(self, key):
        with self._lock:
            self._d.move_to_end(key)
            return self._d[key]

    def __setitem__(self, key, val) -> None:
        with self._lock:
            self._d[key] = val
            self._d.move_to_end(key)
            while len(self._d) > self.max_entries:
                self._d.popitem(last=False)

    def get(self, key, default=None):
        with self._lock:
            if key not in self._d:
                return default
            self._d.move_to_end(key)
            return self._d[key]

    def __len__(self) -> int:
        return len(self._d)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


def _pop_values(rows: list[tuple[int, int, str]]) -> tuple[int, int, float] | None:
    """(size, file count, entropy) of one commit for the repo-wide percentile
    populations, over its non-generated files (the same files diff_stats counts
    for a PR, so the population is comparable to the PR); None when it has none
    (an empty/merge commit, or one that only touched generated files)."""
    kept = [r for r in rows if not config.path_flags(r[2])["is_generated"]]
    if not kept:
        return None
    return sum(a + d for a, d, _ in kept), len(kept), _commit_entropy(kept)


def _commit_has_test(paths) -> bool:
    return any(config.path_flags(p)["is_test"] for p in paths)


def build_repo_index(repo: str, base: str, st: config.Settings,
                     ci_map: dict | None = None,
                     paths: list[str] | None = None,
                     old_paths: dict[str, str] | None = None) -> dict:
    """
    Per-file and per-author aggregates over every commit reachable from `base`
    and committed at or before it, reused for every file in the PR. The index
    answers "as of base" from one per-repo structure built once (RepoHistory),
    so a PR costs time in its own files and author, not in the repo's whole
    history. Values are those build_repo_index_full computes by walking the
    history for this one base (tests hold the two equal), except that a file's
    history is joined across the renames in it.

    paths / old_paths: accepted for the walking version (see there); the shared
    index answers any path on demand.

    ci_map: optional {commit_sha: 'failure'|'success'} from the historical CI
    pre-pass; when present, per-file and per-author post-merge CI-fail rates are
    accumulated point-in-time.
    """
    try:
        q = repo_history(repo).query(base, st, ci_map)
    except _NotInUniverse:
        q = None
    if q is not None:
        return q.index()
    return build_repo_index_full(repo, base, st, ci_map, paths, old_paths)


def build_repo_index_full(repo: str, base: str, st: config.Settings,
                          ci_map: dict | None = None,
                          paths: list[str] | None = None,
                          old_paths: dict[str, str] | None = None) -> dict:
    """
    One pass over all commits up to `base` building per-file and per-author
    aggregates: the reference implementation (and the fallback when `base` is
    not in the repo-wide commit cache). Does not join history across renames.

    paths: when given (the PR's changed files), per-file aggregates and the
    co-change matrix are kept only for these paths (and their base-side names
    in old_paths) — the only ones the feature functions read — instead of
    every file in history. Values for the kept paths, author aggregates and the
    repo-wide hotspot ranking are unchanged.

    Merge commits are not authored work: they count for no author total,
    EXP/REXP, revert base rate or population. Their CI verdict is still
    attributed: to the merger, and to the files of the merge's first-parent
    diff.
    """
    ci_map = ci_map or {}
    base_time = gitio.commit_time(repo, base)
    entries = gitio.iter_commits_with_files(repo, base, before=base_time)
    n_commits_total = 0

    file_commits: dict[str, list] = defaultdict(list)   # path -> [(time, sha, subj, author)]
    file_authors: dict[str, set] = defaultdict(set)
    file_revert_ct: dict[str, int] = defaultdict(int)
    file_fix_ct: dict[str, int] = defaultdict(int)
    file_decay_fix: dict[str, float] = defaultdict(float)
    file_decay_revert: dict[str, float] = defaultdict(float)
    file_last_revert: dict[str, datetime] = {}
    file_last_fix: dict[str, datetime] = {}
    file_hotfix_365d: dict[str, int] = defaultdict(int)     # hotfix commits, last year
    file_test_cochange: dict[str, int] = defaultdict(int)   # commits where a test co-changed
    file_ci_fail: dict[str, int] = defaultdict(int)         # commits to file whose CI failed
    file_ci_total: dict[str, int] = defaultdict(int)        # commits to file with a CI verdict
    cochange: dict[str, Counter] = defaultdict(Counter)  # type: ignore

    year_ago = base_time - timedelta(days=365)
    keep = None if paths is None else set(paths) | set((old_paths or {}).values())
    churn365: dict[str, int] = {}

    author_commits: dict[str, int] = defaultdict(int)
    author_last: dict[str, datetime] = {}
    author_revert: dict[str, int] = defaultdict(int)
    author_total_authored: dict[str, int] = defaultdict(int)
    author_rexp: dict[str, float] = defaultdict(float)          # recency-weighted exp
    author_subsys: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    author_ci_fail: dict[str, int] = defaultdict(int)
    author_ci_total: dict[str, int] = defaultdict(int)

    # repo-wide distributions (per non-merge commit) for percentile features
    size_pop: list[int] = []
    nf_pop: list[int] = []
    entropy_pop: list[float] = []

    repo_revert_total = 0

    for c, rows in entries:
        is_merge = len(c.parents) > 1
        ci_verdict = ci_map.get(c.sha)                  # 'failure' | 'success' | None
        if ci_verdict in ("failure", "success"):
            author_ci_total[c.author_email] += 1
            if ci_verdict == "failure":
                author_ci_fail[c.author_email] += 1
        if is_merge:
            # what the merge brought in is its first-parent diff, which log
            # does not show: the CI verdict on it belongs to those files
            if ci_verdict in ("failure", "success"):
                for _a, _d, p in gitio.numstat_first_parent(repo, c.sha):
                    if keep is not None and p not in keep:
                        continue
                    file_ci_total[p] += 1
                    if ci_verdict == "failure":
                        file_ci_fail[p] += 1
            continue
        n_commits_total += 1
        paths_c = [p for _, _, p in rows]
        is_fix = _is_fix(c.subject, st)
        is_rev = _is_revert(c.subject, st)
        is_hotfix = _is_hotfix(c.subject, st)
        commit_has_test = _commit_has_test(paths_c)
        if is_rev:
            repo_revert_total += 1
            author_revert[c.author_email] += 1
        author_commits[c.author_email] += 1
        author_total_authored[c.author_email] += 1
        if c.author_email not in author_last or c.committed > author_last[c.author_email]:
            author_last[c.author_email] = c.committed
        age_days = (base_time - c.committed).days

        # author experience: recency-weighted (Kamei REXP) + per-subsystem (SEXP)
        author_rexp[c.author_email] += 1.0 / (max(age_days, 0) / 365.0 + 1.0)
        for sub in {config.subsystem_of(p) for p in paths_c}:
            author_subsys[c.author_email][sub] += 1

        pv = _pop_values(rows)
        if pv:
            size_pop.append(pv[0])
            nf_pop.append(pv[1])
            entropy_pop.append(pv[2])

        recent = c.committed >= year_ago
        for p in paths_c:
            if p not in churn365:
                churn365[p] = 0
            if recent:
                churn365[p] += 1
            if keep is not None and p not in keep:
                continue
            file_commits[p].append((c.committed, c.sha, c.subject, c.author_email))
            file_authors[p].add(c.author_email)
            if is_hotfix and c.committed >= year_ago:
                file_hotfix_365d[p] += 1
            if commit_has_test and not config.path_flags(p)["is_test"]:
                file_test_cochange[p] += 1
            if ci_verdict in ("failure", "success"):
                file_ci_total[p] += 1
                if ci_verdict == "failure":
                    file_ci_fail[p] += 1
            if is_rev:
                file_revert_ct[p] += 1
                file_decay_revert[p] += _decay(age_days, st.decay_half_life_days)
                if p not in file_last_revert or c.committed > file_last_revert[p]:
                    file_last_revert[p] = c.committed
            if is_fix:
                file_fix_ct[p] += 1
                file_decay_fix[p] += _decay(age_days, st.decay_half_life_days)
                if p not in file_last_fix or c.committed > file_last_fix[p]:
                    file_last_fix[p] = c.committed
        # co-change: files touched together
        for p in paths_c:
            if keep is not None and p not in keep:
                continue
            for q in paths_c:
                if p != q:
                    cochange[p][q] += 1

    # --- repo-wide hotspot ranking (point-in-time, as of base) --------------
    # Hotspot = churn(365d) x complexity. Rank EVERY file in the repo, not just
    # the changed ones, so `touches_topk_hotspot` is a real repo-relative signal
    # rather than a per-PR proxy. Only files with recent churn can be hotspots,
    # so we score complexity for just those (a file with zero recent churn has
    # hotspot 0 whatever its complexity) — keeps this cheap on large repos.
    hotspot_scores: dict[str, float] = {}
    blobs = gitio.tree_blobs(repo, base)
    for p, churn in churn365.items():
        if churn <= 0:
            continue
        if config.lang_of(p) is None:   # lizard can't score it: skip the read
            continue
        blob = blobs.get(p)
        if blob is None:             # file not present at base (deleted/renamed)
            continue
        ccn = _blob_ccn(repo, blob, p)
        if not ccn:                  # None (no tool / unsupported) or 0
            continue
        hotspot_scores[p] = churn * ccn

    ranked = sorted(hotspot_scores.items(), key=lambda kv: kv[1], reverse=True)
    hotspot_rank = {p: i + 1 for i, (p, _) in enumerate(ranked)}       # 1 = hottest
    hotspot_topk = {p for p, _ in ranked[: st.hotspot_top_k]}

    return {
        "base_time": base_time,
        "file_commits": file_commits,
        "file_authors": file_authors,
        "file_revert_ct": file_revert_ct,
        "file_fix_ct": file_fix_ct,
        "file_decay_fix": file_decay_fix,
        "file_decay_revert": file_decay_revert,
        "file_last_revert": file_last_revert,
        "file_last_fix": file_last_fix,
        "file_hotfix_365d": file_hotfix_365d,
        "file_test_cochange": file_test_cochange,
        "file_ci_fail": file_ci_fail,
        "file_ci_total": file_ci_total,
        "author_ci_fail": author_ci_fail,
        "author_ci_total": author_ci_total,
        "repo_ci_total": sum(author_ci_total.values()),
        "repo_ci_fail": sum(author_ci_fail.values()),
        "cochange": cochange,
        "author_commits": author_commits,
        "author_last": author_last,
        "author_revert": author_revert,
        "author_total_authored": author_total_authored,
        "author_rexp": author_rexp,
        "author_subsys": author_subsys,
        "size_pop": size_pop,
        "nf_pop": nf_pop,
        "entropy_pop": entropy_pop,
        "repo_revert_base_rate": (repo_revert_total / n_commits_total) if n_commits_total else 0.0,
        "n_commits_total": n_commits_total,
        "hotspot_scores": hotspot_scores,
        "hotspot_rank": hotspot_rank,
        "hotspot_topk": hotspot_topk,
    }


# --------------------------------------------------------------------------
# The shared, per-repo history: built once, queried "as of base"
# --------------------------------------------------------------------------
class _NotInUniverse(Exception):
    """The base commit (or a commit git lists) is not in the repo-wide cache."""


_MISSING = object()


class _Lazy(Mapping):
    """A read-only mapping whose values are computed on first use and
    remembered. A key whose function returns _MISSING is absent (so .get()
    gives the caller's default, as an absent key in a plain dict would).
    Iteration covers only the keys asked for so far."""

    def __init__(self, fn):
        self._fn, self._c = fn, {}

    def __getitem__(self, k):
        if k not in self._c:
            v = self._fn(k)
            if v is _MISSING:
                raise KeyError(k)
            self._c[k] = v
        return self._c[k]

    def __iter__(self):
        return iter(self._c)

    def __len__(self):
        return len(self._c)


class _PopIndex:
    """Values (one per commit with any non-generated file), in position order
    (newest commit first), with sorted blocks so "how many commits in a time
    range are below x" costs a few hundred bisects rather than a scan."""
    B = 256

    def __init__(self, row_pos: list[int], vals: list):
        self.row_pos, self.vals = row_pos, vals
        self.blocks = [sorted(vals[j:j + self.B]) for j in range(0, len(vals), self.B)]

    def count_less(self, r0: int, x) -> int:
        """Values at indexes r0.. that are < x."""
        n = len(self.vals)
        if r0 >= n:
            return 0
        bj = r0 // self.B
        end = min(n, (bj + 1) * self.B)
        total = sum(1 for v in self.vals[r0:end] if v < x)
        for j in range(bj + 1, len(self.blocks)):
            total += bisect.bisect_left(self.blocks[j], x)
        return total


class _Pop:
    """The population of one percentile feature as of one base: the commits
    reachable from it, with the same len() / ranking a plain list gives."""

    def __init__(self, pop: _PopIndex, i0: int, excluded_vals: list):
        self._pop, self._ex = pop, excluded_vals
        self._r0 = bisect.bisect_left(pop.row_pos, i0)
        self._n = len(pop.vals) - self._r0 - len(excluded_vals)

    def __len__(self) -> int:
        return self._n

    def count_less(self, x) -> int:
        return self._pop.count_less(self._r0, x) - sum(1 for v in self._ex if v < x)


class _FileRec:
    __slots__ = ("commits", "authors", "revert_ct", "fix_ct", "decay_fix", "decay_revert",
                 "last_revert", "last_fix", "hotfix", "test_co", "ci_fail", "ci_total",
                 "cochange")

    def __init__(self):
        self.commits: list = []
        self.authors: set = set()
        self.revert_ct = self.fix_ct = self.hotfix = self.test_co = 0
        self.ci_fail = self.ci_total = 0
        self.decay_fix = self.decay_revert = 0.0
        self.last_revert = self.last_fix = None
        self.cochange: Counter = Counter()


class _AuthorRec:
    __slots__ = ("total", "revert", "rexp", "subs", "ci_fail", "ci_total")

    def __init__(self):
        self.total = self.revert = self.ci_fail = self.ci_total = 0
        self.rexp = 0.0
        self.subs: dict[str, int] = defaultdict(int)


class RepoHistory:
    """Every commit of a repo (the numstat cache: all refs), newest first, with
    the indexes that answer the history features for any base commit.

    "As of base B" is the commits reachable from B and committed at or before
    B's time. That is every commit committed at or before that time (a prefix
    of the time-sorted list, found by bisect) minus the ones not reachable from
    B, which one `git rev-list --all --before=T ^B` names (usually few: other
    branches). Per-path and per-author position lists then answer a feature
    from only the commits that touch that path or author."""

    def __init__(self, repo: str):
        self.repo = repo
        cache = gitio._numstat_cache(repo)
        items = sorted(cache.values(), key=lambda cr: -int(cr[0].committed.timestamp()))
        self.n = len(items)
        self.cm = [c for c, _ in items]
        self.rows = [r for _, r in items]
        self.ts = [int(c.committed.timestamp()) for c in self.cm]
        self.neg_ts = [-t for t in self.ts]
        self.pos = {c.sha: i for i, c in enumerate(self.cm)}
        self.paths = [tuple(p for _, _, p in r) for r in self.rows]
        self.merge = [len(c.parents) > 1 for c in self.cm]
        self.is_fix = [_is_fix(c.subject, None) for c in self.cm]
        self.is_rev = [_is_revert(c.subject, None) for c in self.cm]
        self.is_hot = [_is_hotfix(c.subject, None) for c in self.cm]
        n = self.n
        self.suf_nonmerge = [0] * (n + 1)
        self.suf_rev = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            nm = not self.merge[i]
            self.suf_nonmerge[i] = self.suf_nonmerge[i + 1] + (1 if nm else 0)
            self.suf_rev[i] = self.suf_rev[i + 1] + (1 if nm and self.is_rev[i] else 0)
        self.path_pos: dict[str, list[int]] = defaultdict(list)
        self.author_pos: dict[str, list[int]] = defaultdict(list)
        self.renames_into: dict[str, list[tuple[int, str]]] = defaultdict(list)
        row_pos: list[int] = []
        pv: list[tuple] = []
        for i, c in enumerate(self.cm):
            for p in self.paths[i]:
                self.path_pos[p].append(i)
            self.author_pos[c.author_email].append(i)
            for old, new in c.renames:
                self.renames_into[new].append((i, old))
            if not self.merge[i]:
                v = _pop_values(self.rows[i])
                if v:
                    row_pos.append(i)
                    pv.append(v)
        self.pop_pos = row_pos
        self.pops = {name: _PopIndex(row_pos, [v[k] for v in pv])
                     for k, name in enumerate(("size", "nf", "entropy"))}
        self._pop_vals = {i: v for i, v in zip(row_pos, pv)}
        self._has_test: dict[int, bool] = {}
        self._subs: dict[int, frozenset] = {}
        self._lock = threading.Lock()
        self._ci_key: tuple | None = None
        self._ci = None
        self._excl: "OrderedDict[tuple, frozenset]" = OrderedDict()
        self._full: dict[str, str] = {}

    def has_test(self, i: int) -> bool:
        v = self._has_test.get(i)
        if v is None:
            v = self._has_test[i] = _commit_has_test(self.paths[i])
        return v

    def subs(self, i: int) -> frozenset:
        v = self._subs.get(i)
        if v is None:
            v = self._subs[i] = frozenset(config.subsystem_of(p) for p in self.paths[i])
        return v

    def ci_tables(self, ci_map: dict | None) -> "_CiTables":
        ci_map = ci_map or {}
        with self._lock:
            if self._ci is not None and (self._ci_key[0] is ci_map or self._ci_key[1] == ci_map):
                return self._ci
            self._ci = _CiTables(self, ci_map)
            self._ci_key = (ci_map, dict(ci_map))
            return self._ci

    def excluded(self, full: str, base_time: datetime) -> frozenset:
        """Positions of the commits committed at or before base_time that are
        NOT reachable from `full`."""
        key = (full, int(base_time.timestamp()))
        with self._lock:
            hit = self._excl.get(key)
        if hit is not None:
            return hit
        out = gitio._run(self.repo, "rev-list", "--all", f"--before={base_time.isoformat()}",
                         f"^{full}")
        try:
            ex = frozenset(self.pos[s] for s in out.split())
        except KeyError:
            raise _NotInUniverse() from None
        with self._lock:
            self._excl[key] = ex
            while len(self._excl) > 16:
                self._excl.popitem(last=False)
        return ex

    def reach(self, base: str):
        """(full sha, position, base time, excluded positions) for `base`, or
        None when it is not in the cache (a caller then walks the history)."""
        full = self._full.get(base)
        if full is None:
            full = gitio._run(self.repo, "rev-parse", "--verify", "--quiet",
                              f"{base}^{{commit}}", check=False).strip()
            if full:
                self._full[base] = full
        i = self.pos.get(full)
        if i is None:
            return None
        base_time = self.cm[i].committed
        return full, i, base_time, self.excluded(full, base_time)

    def query(self, base: str, st: config.Settings, ci_map: dict | None) -> "_Query | None":
        r = self.reach(base)
        if r is None:
            return None
        full, _i, base_time, ex = r
        return _Query(self, full, base_time, ex, st, self.ci_tables(ci_map))


class _CiTables:
    """CI verdicts by commit position, and for merge commits the files of their
    first-parent diff (a merge's own log entry lists no files)."""

    def __init__(self, h: RepoHistory, ci_map: dict):
        n = h.n
        self.verdict: list[str | None] = [None] * n
        self.suf_total = [0] * (n + 1)
        self.suf_fail = [0] * (n + 1)
        for sha, v in ci_map.items():
            i = h.pos.get(sha)
            if i is not None and v in ("failure", "success"):
                self.verdict[i] = v
        for i in range(n - 1, -1, -1):
            v = self.verdict[i]
            self.suf_total[i] = self.suf_total[i + 1] + (1 if v else 0)
            self.suf_fail[i] = self.suf_fail[i + 1] + (1 if v == "failure" else 0)
        self.merge_by_path: dict[str, list[int]] = defaultdict(list)
        for i in range(n):
            if h.merge[i] and self.verdict[i]:
                for p in {p for _a, _d, p in gitio.numstat_first_parent(h.repo, h.cm[i].sha)}:
                    self.merge_by_path[p].append(i)


class _Query:
    """One base commit's view of a RepoHistory; index() is the dict the feature
    functions read."""

    def __init__(self, h: RepoHistory, full: str, base_time: datetime,
                 excluded: frozenset, st: config.Settings, ci: _CiTables):
        self.h, self.base, self.base_time, self.E, self.st, self.ci = \
            h, full, base_time, excluded, st, ci
        self.i0 = bisect.bisect_left(h.neg_ts, -int(base_time.timestamp()))
        self.year_ago_ts = int((base_time - timedelta(days=365)).timestamp())
        self._frec: dict = {}
        self._arec: dict = {}

    # -- per file ---------------------------------------------------------
    def file_rec(self, p: str) -> _FileRec | None:
        if p in self._frec:
            return self._frec[p]
        h, E, i0 = self.h, self.E, self.i0
        rec = _FileRec()
        segs = [(p, i0)]
        seen = {(p, i0)}
        k = 0
        found = False
        while k < len(segs):
            path, lo = segs[k]
            k += 1
            plist = h.path_pos.get(path)
            if plist:
                for pos in plist[bisect.bisect_left(plist, lo):]:
                    if pos not in E:
                        self._add(rec, path, pos)
                        found = True
            for rpos, old in h.renames_into.get(path, ()):
                if rpos < lo or rpos in E:
                    continue
                key = (old, bisect.bisect_left(h.neg_ts, -h.ts[rpos]))
                if key not in seen:
                    seen.add(key)
                    segs.append(key)
        for path, lo in segs:
            mlist = self.ci.merge_by_path.get(path)
            if mlist:
                for pos in mlist[bisect.bisect_left(mlist, lo):]:
                    if pos not in E:
                        found = True
                        rec.ci_total += 1
                        if self.ci.verdict[pos] == "failure":
                            rec.ci_fail += 1
        out = rec if found else None
        self._frec[p] = out
        return out

    def _add(self, rec: _FileRec, path: str, pos: int) -> None:
        h, st = self.h, self.st
        cm = h.cm[pos]
        rec.commits.append((cm.committed, cm.sha, cm.subject, cm.author_email))
        rec.authors.add(cm.author_email)
        age_days = (self.base_time - cm.committed).days
        if h.is_hot[pos] and h.ts[pos] >= self.year_ago_ts:
            rec.hotfix += 1
        if h.has_test(pos) and not config.path_flags(path)["is_test"]:
            rec.test_co += 1
        v = self.ci.verdict[pos]
        if v:
            rec.ci_total += 1
            if v == "failure":
                rec.ci_fail += 1
        if h.is_rev[pos]:
            rec.revert_ct += 1
            rec.decay_revert += _decay(age_days, st.decay_half_life_days)
            if rec.last_revert is None or cm.committed > rec.last_revert:
                rec.last_revert = cm.committed
        if h.is_fix[pos]:
            rec.fix_ct += 1
            rec.decay_fix += _decay(age_days, st.decay_half_life_days)
            if rec.last_fix is None or cm.committed > rec.last_fix:
                rec.last_fix = cm.committed
        for q in h.paths[pos]:
            if q != path:
                rec.cochange[q] += 1

    def _file_map(self, attr: str):
        def get(p):
            rec = self.file_rec(p)
            if rec is None:
                return _MISSING
            v = getattr(rec, attr)
            return _MISSING if v is None else v
        return _Lazy(get)

    # -- per author -------------------------------------------------------
    def author_rec(self, a: str) -> _AuthorRec | None:
        if a in self._arec:
            return self._arec[a]
        h, E = self.h, self.E
        lst = h.author_pos.get(a)
        rec = None
        if lst:
            rec = _AuthorRec()
            seen = False
            for pos in lst[bisect.bisect_left(lst, self.i0):]:
                if pos in E:
                    continue
                seen = True
                v = self.ci.verdict[pos]
                if v:
                    rec.ci_total += 1
                    if v == "failure":
                        rec.ci_fail += 1
                if h.merge[pos]:
                    continue
                rec.total += 1
                if h.is_rev[pos]:
                    rec.revert += 1
                age_days = (self.base_time - h.cm[pos].committed).days
                rec.rexp += 1.0 / (max(age_days, 0) / 365.0 + 1.0)
                for sub in h.subs(pos):
                    rec.subs[sub] += 1
            if not seen:
                rec = None
        self._arec[a] = rec
        return rec

    def _author_map(self, attr: str):
        def get(a):
            rec = self.author_rec(a)
            return _MISSING if rec is None else getattr(rec, attr)
        return _Lazy(get)

    def _author_last(self, a: str):
        h, E = self.h, self.E
        lst = h.author_pos.get(a, ())
        for pos in lst[bisect.bisect_left(lst, self.i0):] if lst else ():
            if pos not in E and not h.merge[pos]:
                return h.cm[pos].committed        # positions run newest first
        return _MISSING

    # -- repo-wide --------------------------------------------------------
    def _counts(self):
        h, E, i0 = self.h, self.E, self.i0
        n = h.suf_nonmerge[i0] - sum(1 for e in E if not h.merge[e])
        rev = h.suf_rev[i0] - sum(1 for e in E if not h.merge[e] and h.is_rev[e])
        ci_total = self.ci.suf_total[i0] - sum(1 for e in E if self.ci.verdict[e])
        ci_fail = self.ci.suf_fail[i0] - sum(1 for e in E if self.ci.verdict[e] == "failure")
        return n, rev, ci_total, ci_fail

    def _pop(self, name: str) -> _Pop:
        h = self.h
        k = {"size": 0, "nf": 1, "entropy": 2}[name]
        ex = [h._pop_vals[e][k] for e in self.E if e in h._pop_vals]
        return _Pop(h.pops[name], self.i0, ex)

    def _hotspots(self, repo: str):
        h, E, lo = self.h, self.E, self.i0
        hi = max(lo, bisect.bisect_right(h.neg_ts, -self.year_ago_ts))
        cand = set(chain.from_iterable(h.paths[lo:hi]))
        ecount: Counter = Counter()
        for e in E:
            if lo <= e < hi:
                ecount.update(h.paths[e])
        blobs = gitio.tree_blobs(repo, self.base)
        items = []
        for p in cand:
            plist = h.path_pos[p]
            churn = bisect.bisect_left(plist, hi) - bisect.bisect_left(plist, lo) - ecount.get(p, 0)
            if churn <= 0 or config.lang_of(p) is None:
                continue
            blob = blobs.get(p)
            if blob is None:
                continue
            ccn = _blob_ccn(repo, blob, p)
            if not ccn:
                continue
            # a tie in score keeps the order the files first appear in the walk
            first = next(pos for pos in plist[bisect.bisect_left(plist, lo):] if pos not in E)
            items.append(((first, h.paths[first].index(p)), p, churn * ccn))
        items.sort(key=lambda it: it[0])
        ranked = sorted(((p, s) for _k, p, s in items), key=lambda kv: kv[1], reverse=True)
        scores = {p: s for _k, p, s in items}
        return (scores, {p: i + 1 for i, (p, _) in enumerate(ranked)},
                {p for p, _ in ranked[: self.st.hotspot_top_k]})

    def index(self) -> dict:
        n, rev, ci_total, ci_fail = self._counts()
        scores, rank, topk = self._hotspots(self.h.repo)
        return {
            "base_time": self.base_time,
            "file_commits": self._file_map("commits"),
            "file_authors": self._file_map("authors"),
            "file_revert_ct": self._file_map("revert_ct"),
            "file_fix_ct": self._file_map("fix_ct"),
            "file_decay_fix": self._file_map("decay_fix"),
            "file_decay_revert": self._file_map("decay_revert"),
            "file_last_revert": self._file_map("last_revert"),
            "file_last_fix": self._file_map("last_fix"),
            "file_hotfix_365d": self._file_map("hotfix"),
            "file_test_cochange": self._file_map("test_co"),
            "file_ci_fail": self._file_map("ci_fail"),
            "file_ci_total": self._file_map("ci_total"),
            "author_ci_fail": self._author_map("ci_fail"),
            "author_ci_total": self._author_map("ci_total"),
            "repo_ci_total": ci_total,
            "repo_ci_fail": ci_fail,
            "cochange": self._file_map("cochange"),
            "author_commits": self._author_map("total"),
            "author_last": _Lazy(self._author_last),
            "author_revert": self._author_map("revert"),
            "author_total_authored": self._author_map("total"),
            "author_rexp": self._author_map("rexp"),
            "author_subsys": self._author_map("subs"),
            "size_pop": self._pop("size"),
            "nf_pop": self._pop("nf"),
            "entropy_pop": self._pop("entropy"),
            "repo_revert_base_rate": (rev / n) if n else 0.0,
            "n_commits_total": n,
            "hotspot_scores": scores,
            "hotspot_rank": rank,
            "hotspot_topk": topk,
        }


_HISTORIES: "OrderedDict[str, RepoHistory]" = OrderedDict()
_HISTORIES_LOCK = threading.Lock()
_HISTORIES_MAX = 2


def repo_history(repo: str) -> RepoHistory:
    """The RepoHistory of `repo`, built once per process (the first call waits
    for it; later PRs reuse it)."""
    import os
    key = os.path.abspath(repo)
    with _HISTORIES_LOCK:
        h = _HISTORIES.get(key)
        if h is None:
            h = _HISTORIES[key] = RepoHistory(repo)
            while len(_HISTORIES) > _HISTORIES_MAX:
                _HISTORIES.popitem(last=False)
        else:
            _HISTORIES.move_to_end(key)
        return h


def clear_repo_history() -> None:
    with _HISTORIES_LOCK:
        _HISTORIES.clear()


def per_file_features(repo: str, base: str, path: str, idx: dict,
                      st: config.Settings, old_path: str | None = None) -> dict:
    """Source 2b/2c/2d/2e per-file features for one changed file, as of base.

    `path` is the head-side name; `old_path` is its name at base when the PR
    renames it (gitio.rename_map). Everything read from the base side (history,
    blame, size, complexity, hotspot rank) uses that name: the new one does
    not exist there.
    """
    lp = old_path or path
    base_time = idx["base_time"]
    commits = idx["file_commits"].get(lp, [])
    n = len(commits)
    revert_ct = idx["file_revert_ct"].get(lp, 0)
    fix_ct = idx["file_fix_ct"].get(lp, 0)
    times = [t for t, _, _, _ in commits]

    last_change = max(times) if times else None
    age_days = (base_time - min(times)).days if times else 0.0
    last_change_days = (base_time - last_change).days if last_change else None

    # Ownership by LINES, not commits: blame the file at base and count who
    # wrote each surviving line. This is the true line-level ownership the
    # reference specifies (Bird et al.), replacing the earlier commit-share
    # proxy. Powers file_top_author_share, file_minor_contributor_count, and
    # abandoned_lines_share. The blame call is cached (gitio.blame_file), so
    # sharing it with the Source-3 line features costs nothing extra.
    blame = gitio.blame_file(repo, base, lp)
    blame_unobserved = not blame and gitio.blame_failed()
    # bots own no lines worth reporting: ownership is among people
    line_authors = [bl.author_email for bl in blame.values()
                    if not _is_bot_email(bl.author_email)]
    total_lines = len(line_authors)
    if total_lines:
        la_counts = Counter(line_authors)
        top_share = la_counts.most_common(1)[0][1] / total_lines
        minor = sum(1 for _, cnt in la_counts.items() if cnt / total_lines < 0.05)
        # abandoned = share of LINES whose author is inactive >180d as of base
        abandoned_lines = 0
        for email, cnt in la_counts.items():
            la = idx["author_last"].get(email)
            if la and (base_time - la).days > 180:
                abandoned_lines += cnt
        abandoned_share = abandoned_lines / total_lines
    elif blame_unobserved:
        # the blame timed out or errored: unobserved, so null, never a measured zero
        top_share, minor, abandoned_share = None, None, None
    else:
        # binary/empty/unblameable file: no line ownership to report
        top_share, minor, abandoned_share = 0.0, 0, 0.0

    last_rev = idx["file_last_revert"].get(lp)
    last_fix = idx["file_last_fix"].get(lp)

    # one read of the file at base serves both its size and its complexity
    blob = gitio.read_blob(repo, f"{base}:{lp}")
    ccn = _lizard_ccn_from_content(blob.content, lp) if blob.content is not None else None

    feats = {
        "file_revert_density": revert_ct / n if n else 0.0,
        "revert_decay_density": idx["file_decay_revert"].get(lp, 0.0),
        "days_since_last_revert": (base_time - last_rev).days if last_rev else None,
        "file_fix_density": fix_ct / n if n else 0.0,
        "fix_decay_density": idx["file_decay_fix"].get(lp, 0.0),
        "days_since_last_fix": (base_time - last_fix).days if last_fix else None,
        "file_churn_90d": _windowed_churn(times, base_time, 90),
        "file_churn_365d": _windowed_churn(times, base_time, 365),
        "recent_merge_burst_7d": _windowed_churn(times, base_time, 7),
        "hotfix_count_365d": idx["file_hotfix_365d"].get(lp, 0),
        "test_cochange_ratio": round(idx["file_test_cochange"].get(lp, 0) / n, 4) if n else 0.0,
        "file_post_merge_ci_fail_rate": (
            round(idx["file_ci_fail"].get(lp, 0) / idx["file_ci_total"][lp], 4)
            if idx["file_ci_total"].get(lp) else None),
        "file_age_days": age_days,
        "NUC": n,                                   # unique prior changes
        "NDEV": len({a for a in idx["file_authors"].get(lp, set()) if not _is_bot_email(a)}),
        "file_complexity_ccn": ccn,
        "file_top_author_share": None if top_share is None else round(top_share, 4),
        "file_minor_contributor_count": minor,
        "abandoned_lines_share": None if abandoned_share is None else round(abandoned_share, 4),
        "_last_change_days": last_change_days,      # internal, for AGE
        "_loc": blob.loc or 0,                      # for LT / hotspot
    }
    # hotspot score = churn(365) * complexity  (classic)
    feats["file_hotspot_score"] = feats["file_churn_365d"] * (ccn or 0)
    # repo-relative rank of this file among all repo hotspots (1 = hottest);
    # None if the file has no recent churn / complexity to rank on.
    feats["file_hotspot_rank"] = idx.get("hotspot_rank", {}).get(lp)
    # co-change degree + missing partners handled at PR level (needs the diff set)
    return feats


def _windowed_churn(times: list[datetime], base_time: datetime, days: int) -> int:
    cutoff = base_time - timedelta(days=days)
    return sum(1 for t in times if t >= cutoff)


def _lizard_ccn(repo: str, sha: str, path: str) -> float | None:
    """Max cyclomatic complexity of the file at `sha`, via lizard (optional)."""
    content = gitio.file_content_at(repo, sha, path)
    if content is None:
        return None
    return _lizard_ccn_from_content(content, path)


# complexity per (blob sha, path): a file version shared by many PRs' base
# commits is read from git and scored once
_BLOB_CCN_CACHE = _BoundedCache(100_000)


def _blob_ccn(repo: str, blob: str, path: str) -> float | None:
    key = (blob, path)
    hit = _BLOB_CCN_CACHE.get(key, _MISSING)
    if hit is not _MISSING:
        return hit
    r = gitio.read_blob(repo, blob)
    if r.status == "absent":
        return None                # a failed read is unobserved: not scored, not cached as 0.0
    ccn = _lizard_ccn_from_content(r.content, path) if r.content is not None else None
    _BLOB_CCN_CACHE[key] = ccn
    return ccn


# cache complexity by (content-hash, path) so a file version that is identical
# across many PRs' base commits is parsed only once.
_CCN_CACHE = _BoundedCache(100_000)


def _lizard_ccn_from_content(content: str, path: str) -> float | None:
    """Max cyclomatic complexity for file `content`, cached by content hash."""
    lang = config.lang_of(path)
    if lang is None:
        return None
    h = hashlib.md5(content.encode("utf-8", "replace")).hexdigest()
    key = (h, path)
    if key in _CCN_CACHE:
        return _CCN_CACHE[key]
    try:
        import lizard
    except Exception:
        _CCN_CACHE[key] = None
        return None
    try:
        analysis = lizard.analyze_file.analyze_source_code(path, content)
        val = (0.0 if not analysis.function_list
               else max(f.cyclomatic_complexity for f in analysis.function_list))
    except Exception:
        val = None
    _CCN_CACHE[key] = val
    return val


def author_features(author_email: str, touched_paths: list[str], idx: dict,
                    st: config.Settings, old_paths: dict[str, str] | None = None) -> dict:
    """Source 2g author-behavior features, smoothed toward repo mean.
    old_paths ({new: base-side name}, see gitio.rename_map) maps a renamed
    file to the name its history is under."""
    if old_paths:
        touched_paths = [old_paths.get(p, p) for p in touched_paths]
    a = author_email.lower()
    total = idx["author_total_authored"].get(a, 0)
    reverts = idx["author_revert"].get(a, 0)
    base_rate = idx["repo_revert_base_rate"]
    alpha = st.author_smoothing_alpha
    # empirical-Bayes shrink toward repo base rate
    smoothed = (reverts + alpha * base_rate) / (total + alpha) if (total + alpha) else base_rate

    fam_hits = sum(1 for p in touched_paths if a in idx["file_authors"].get(p, set()))
    familiarity = fam_hits / len(touched_paths) if touched_paths else 0.0

    last = idx["author_last"].get(a)
    days_since = (idx["base_time"] - last).days if last else None
    # post-merge CI-fail rate, empirical-Bayes smoothed toward repo mean; None
    # when we have no CI history for this author.
    ci_total = idx["author_ci_total"].get(a, 0)
    ci_fail = idx["author_ci_fail"].get(a, 0)
    repo_ci_total = idx["repo_ci_total"] if "repo_ci_total" in idx else sum(idx["author_ci_total"].values())
    repo_ci_fail = idx["repo_ci_fail"] if "repo_ci_fail" in idx else sum(idx["author_ci_fail"].values())
    if ci_total > 0:
        repo_ci_rate = (repo_ci_fail / repo_ci_total) if repo_ci_total else 0.0
        ci_fail_rate = round((ci_fail + alpha * repo_ci_rate) / (ci_total + alpha), 4)
    else:
        ci_fail_rate = None
    return {
        "author_revert_rate": round(smoothed, 4),
        "author_file_familiarity": round(familiarity, 4),
        "author_days_since_last_change": days_since,
        "author_post_merge_ci_fail_rate": ci_fail_rate,
        # author_prs_merged_repo is set in the orchestrator from the PR list
        # (real merged-PR count, point-in-time), not here.
    }


def cochange_features(changed: list[str], idx: dict,
                      old_paths: dict[str, str] | None = None) -> dict:
    """cochange_degree + cochange_partners_missing across the changed set.
    Partners are named as they are in history, so with old_paths ({new:
    base-side name}) the changed files are looked up and compared by those."""
    if old_paths:
        changed = [old_paths.get(p, p) for p in changed]
    changed_set = set(changed)
    degrees, missing = [], []
    for p in changed:
        partners = idx["cochange"].get(p)
        if not partners:
            degrees.append(0)
            continue
        strong = [q for q, c in partners.items() if c >= 3]
        degrees.append(len(strong))
        miss = sum(1 for q in strong if q not in changed_set)
        missing.append(miss)
    return {
        "cochange_degree": max(degrees) if degrees else 0,
        "cochange_partners_missing": max(missing) if missing else 0,
    }


import math as _math


def _commit_entropy(rows: list[tuple[int, int, str]]) -> float:
    """Normalized change entropy for a single commit's numstat rows (0..1),
    matching diff_stats._entropy so the population is comparable to the PR."""
    # only files with changed lines: a binary or rename-only file has no share
    # of the change and must not widen the denominator
    per_file = [c for c in (a + d for a, d, _ in rows) if c > 0]
    total = sum(per_file)
    if len(per_file) <= 1:
        return 0.0
    ent = 0.0
    for c in per_file:
        p = c / total
        ent -= p * _math.log2(p)
    return ent / _math.log2(len(per_file))


def experience_features(author_email: str, changed: list[str], idx: dict) -> dict:
    """Kamei EXP / REXP / SEXP for the PR author, as of base.
      EXP  = number of prior commits by the author
      REXP = recency-weighted prior commits (recent count more)
      SEXP = prior commits by the author in the touched subsystems
    """
    a = author_email.lower()
    exp = idx["author_total_authored"].get(a, 0)
    rexp = round(idx["author_rexp"].get(a, 0.0), 4)
    touched_subs = {config.subsystem_of(p) for p in changed}
    subs = idx["author_subsys"].get(a, {})
    sexp = sum(subs.get(s, 0) for s in touched_subs)
    return {"EXP": exp, "REXP": rexp, "SEXP": sexp}


def _pctile_rank(pop: list, x) -> float | None:
    """Fraction of the population strictly less than x (0..1), or None if the
    population is empty or x is unavailable."""
    if not pop or x is None:
        return None
    if hasattr(pop, "count_less"):
        return round(pop.count_less(x) / len(pop), 4)
    return round(sum(1 for v in pop if v < x) / len(pop), 4)


def percentile_features(pr_size, pr_nf, pr_entropy, idx: dict) -> dict:
    """Where this PR sits in the repo's own distribution of change size, file
    count, and spread — the 'repo-relative' signals. Population is per-commit
    (a point-in-time proxy for the repo's PR-size distribution)."""
    return {
        "pr_size_pctile_repo": _pctile_rank(idx.get("size_pop", []), pr_size),
        "nf_pctile_repo": _pctile_rank(idx.get("nf_pop", []), pr_nf),
        "entropy_pctile_repo": _pctile_rank(idx.get("entropy_pop", []), pr_entropy),
    }


def test_expectation_features(changed: list[str], per_file: dict, st: config.Settings) -> dict:
    """expected_test_cochange_missing: this PR changes a source file that usually
    ships with a test change (test_cochange_ratio >= 0.5), but the PR itself
    includes no test file — i.e. 'you probably forgot the test'."""
    pr_has_test = any(config.path_flags(f)["is_test"] for f in changed)
    if pr_has_test:
        return {"expected_test_cochange_missing": False}
    missing = False
    for p in changed:
        if config.path_flags(p)["is_test"]:
            continue
        ratio = (per_file.get(p) or {}).get("test_cochange_ratio", 0.0) or 0.0
        if ratio >= 0.5:
            missing = True
            break
    return {"expected_test_cochange_missing": missing}


def is_fix_pr(pr_title: str, head_subject: str, st: config.Settings) -> bool:
    """FIX (2a): is this PR a bug fix, by keyword on its title / head commit."""
    return config.is_fix_text(f"{pr_title or ''} {head_subject or ''}")

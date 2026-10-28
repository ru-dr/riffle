"""
stages.py — the miner's per-repo stages, and what a run's trace says about them.

STAGES lists the stages `mine_repo.py` passes to `runlog.stage()`, in order. The
Airflow DAG (pipelines/dags/riffle_miner.py) draws one node per entry, and
tests/test_stages.py fails if this list and the `runlog.stage("...")` calls in
mine_repo.py drift apart.

stage_report() reads the structured trace (logs/*.trace.jsonl, see runlog.py)
and says, per stage, how many repos started and finished it, the time it took,
and the errors logged while it ran. It only reads; the miner never calls it.
Standard library only, so the DAG can import it without pandas.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field

# (trace name, what it produces). Order is the order mine_repo.py runs them.
STAGES: tuple[tuple[str, str], ...] = (
    ("fetching PRs", "choose the PRs that become rows"),
    ("required checks", "read the default branch's required status checks"),
    ("merge style", "decide merge-commit, squash or rebase"),
    ("timelines", "read every PR's issue timeline; merge labels and maintainer evidence"),
    ("author index", "per-author merged-PR times for point-in-time author features"),
    ("open snapshots", "resolve each PR's head as it was when it opened (ADR 0010)"),
    ("concurrency", "count PRs open at the same time on the same files"),
    ("ci history", "per-commit CI verdicts from workflow runs"),
    ("loading history", "parse the repo's git log once; build the history index"),
    ("szz", "SZZ index for label_szz_bug and days_to_szz_fix"),
    ("reviews prefetch", "GraphQL review history within the remaining quota"),
    ("mining PRs", "features and labels per PR, in worker threads"),
    ("repo-wide labels", "flaky correction and the repo-wide columns"),
)
STAGE_NAMES = tuple(n for n, _ in STAGES)

# The steps one PR goes through inside "mining PRs" (mine_repo.py `_step("...")`, trace key `pr.<name>`), run by
# several worker threads at once. Their times are summed over PRs, so a step can total more than the stage's wall time.
PR_STEPS: tuple[tuple[str, str], ...] = (
    ("diff", "the PR's diff range"),
    ("diff_stats", "size, spread and path flags"),
    ("history_index", "repo history at the base commit"),
    ("per_file", "per-file history and blame"),
    ("aggregate", "collapse per-file features"),
    ("deterministic", "secrets, off-by-one, migrations"),
    ("scanners", "lizard and semgrep"),
    ("deps_cve", "dependency changes and CVEs (OSV, GHSA)"),
    ("api_contract", "OpenAPI, protobuf and GraphQL diffs"),
    ("reviews", "review activity"),
    ("llm", "LLM flags (claude -p), up to --llm-concurrency at once"),
    ("merge_signals", "merge-model signals"),
    ("labels", "breakage labels"),
    ("capture", "PR snapshot capture"),
)
PR_STEP_NAMES = tuple(n for n, _ in PR_STEPS)


@dataclass
class StageReport:
    name: str
    started: set[str] = field(default_factory=set)      # repos that began the stage
    finished: set[str] = field(default_factory=set)     # repos that completed it
    dur_s: float = 0.0                                  # summed over repos
    slowest: tuple[str, float] | None = None            # (repo, seconds)
    errors_by_repo: dict[str, int] = field(default_factory=dict)   # ERROR events logged inside the stage, per repo; a repo's start of the stage resets its own

    @property
    def errors(self) -> int:
        return sum(self.errors_by_repo.values())

    @property
    def running(self) -> set[str]:
        return self.started - self.finished

    def status(self) -> str:
        if self.errors:
            return "failed"
        if self.running:
            return "running"
        return "done" if self.finished else "pending"


def read_events(lines: Iterable[str]) -> Iterable[dict]:
    """JSON objects from trace lines; a torn or foreign line is skipped, not an error."""
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            yield ev


def stage_report(events: Iterable[dict], only: str | None = None) -> dict[str, StageReport]:
    """Fold trace events (one or many runs, in file order) into a report per stage in STAGES, for every repo or just
    `only`. A repo that is mined again counts once: its latest start replaces the earlier one."""
    out = {n: StageReport(n) for n in STAGE_NAMES}
    current: dict[str, str] = {}                        # repo -> stage it is in now
    for ev in events:
        repo = ev.get("repo")
        if not isinstance(repo, str) or not repo or (only and repo != only):
            continue
        kind, name = ev.get("stage"), ev.get("name")
        if not isinstance(name, (str, type(None))):
            continue
        if kind == "stage" and name in out:
            rep = out[name]
            if ev.get("msg") == "start":
                rep.started.add(repo)
                rep.finished.discard(repo)
                rep.errors_by_repo.pop(repo, None)              # a new pass over the stage: an earlier try's error is not this one's
                current[repo] = name
            elif ev.get("msg") == "done":
                rep.finished.add(repo)
                dur = float(ev.get("dur_s") or 0)
                rep.dur_s += dur
                if rep.slowest is None or dur > rep.slowest[1]:
                    rep.slowest = (repo, dur)
                if current.get(repo) == name:
                    del current[repo]
        elif ev.get("lvl") == "ERROR" and current.get(repo) in out:
            cur = out[current[repo]]
            cur.errors_by_repo[repo] = cur.errors_by_repo.get(repo, 0) + 1
    return out


# ---------------------------------------------------------------- one repo, live
def parse_t(text) -> float | None:
    """Seconds since the epoch from a trace timestamp (2026-10-27T03:50:01.715Z), or None."""
    from datetime import datetime, timezone
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


@dataclass
class StepStat:
    name: str
    n: int = 0                  # PRs that went through the step
    secs: float = 0.0           # their time in it, summed over worker threads
    in_flight: int = 0          # workers in it at the last heartbeat


@dataclass
class RepoView:
    repo: str
    stages: dict[str, StageReport]
    steps: dict[str, StepStat]
    prs_done: int = 0
    prs_total: int | None = None            # PRs the "mining PRs" stage was given (todo + resumed)
    workers: int | None = None
    llm_calls: int = 0
    llm_secs: float = 0.0
    in_flight: list[dict] = field(default_factory=list)     # {"pr", "stage", "for_s"} from the last heartbeat
    repo_done: bool = False
    errors: int = 0
    last_t: float | None = None
    progress: dict[str, dict] = field(default_factory=dict)  # stage name -> its latest `progress` event (long stages report as they go)

    def stage(self, name: str) -> StageReport:
        return self.stages[name]


def repo_view(events: Iterable[dict], repo: str) -> RepoView:
    """Everything the trace says about `repo`: its stage statuses, how far "mining PRs" is, how long the PCs'
    worker threads spent in each PR step, the LLM calls, and what the workers were doing at the last heartbeat."""
    events = [e for e in events if e.get("repo") == repo]
    view = RepoView(repo, stage_report(events, only=repo), {n: StepStat(n) for n in PR_STEP_NAMES})
    for ev in events:
        view.last_t = parse_t(ev.get("t")) or view.last_t
        kind, msg = ev.get("stage"), ev.get("msg")
        if kind == "repo" and msg == "start":
            view.workers = ev.get("workers") or view.workers
            view.repo_done = False
            view.prs_done, view.llm_calls, view.llm_secs, view.errors = 0, 0, 0.0, 0     # a fresh pass over the repo
            for st in view.steps.values():
                st.n, st.secs = 0, 0.0
        elif kind == "repo" and msg == "done":
            view.repo_done = True
            view.in_flight = []
        elif kind == "stage" and msg == "start" and ev.get("name") == "mining PRs":
            if ev.get("total") is not None:
                view.prs_total = ev["total"]
            view.prs_done = ev.get("resumed") or 0
        elif kind == "pr" and msg == "done":
            view.prs_done += 1
            for key, rec in (ev.get("stages") or {}).items():
                if key == "llm":
                    view.llm_calls += int(rec.get("n") or 0)
                    view.llm_secs += float(rec.get("s") or 0)
                elif key.startswith("pr.") and key[3:] in view.steps:
                    st = view.steps[key[3:]]
                    st.n += int(rec.get("n") or 0)
                    st.secs += float(rec.get("s") or 0)
        elif kind == "stage" and msg == "progress" and ev.get("name"):
            view.progress[ev["name"]] = ev
        elif kind == "heartbeat":
            view.in_flight = list(ev.get("in_flight") or [])
        if ev.get("lvl") == "ERROR":
            view.errors += 1
    for st in view.steps.values():
        st.in_flight = sum(1 for w in view.in_flight if w.get("stage") == f"pr.{st.name}")
    return view


def run_state(events: Iterable[dict]) -> dict:
    """The batch as a whole: its repos in order (the `batch repos` event, else the repos seen), whether the
    batch ended, and when the trace last moved."""
    repos: list[str] = []
    seen: list[str] = []
    state = {"batch_done": False, "aborted": False, "last_t": None}
    for ev in events:
        state["last_t"] = parse_t(ev.get("t")) or state["last_t"]
        if ev.get("stage") == "batch" and ev.get("msg") == "start":
            repos, seen = [], []                                # a newer run in the same trace replaces the older
            state["batch_done"] = state["aborted"] = False
        elif ev.get("stage") == "batch" and ev.get("msg") == "repos":
            repos = list(ev.get("repos") or [])
        elif ev.get("stage") == "batch" and ev.get("msg") == "done":
            state["batch_done"], state["aborted"] = True, bool(ev.get("aborted"))
        elif ev.get("repo") and ev["repo"] not in seen:
            seen.append(ev["repo"])
    state["repos"] = repos or seen
    return state

"""The repo miner's pipeline, drawn live: one chain per repo, one node per stage, and the steps of a PR beside them.

Mining does not run here. It runs on the team's PCs (ADR 0015); each repo goes through its stages inside one
`mine_repo.py` process. The miner writes a structured trace (runlog.py) and syncs it to the store, and this DAG
follows that trace. Start it before, during or after a run. Per repo it shows:

    fetching PRs > required checks > ... > timelines > ... > szz > reviews prefetch > mining PRs > repo-wide labels
                                                                         \\> diff, diff_stats, ..., llm, ..., capture

Every node is a sensor that waits for what the trace says about its repo, so while a run is going the graph moves:
a stage is running (waiting) until the repo finishes it, then green. The nodes under "mining PRs" are the steps one
PR goes through, run by the worker threads in parallel; each one's log says how many PRs have passed it, the time
spent, and how many workers are in it now (the `llm` node counts the parallel LLM calls). A step the run never
used (LLM off, no scanners) ends skipped. Red means an ERROR was logged while the repo was in that stage; skipped
also means the run stopped, or the repo finished, without reaching the node.

When every repo is green: gate, gather the PCs' files, merge_shards.py, check against contracts/, and publish with DVC.
The code is in pipelines/ingestion/repo_miner (stages.py, merge_shards.py, publish.py); this file only schedules it.
Blank `split_id` follows the newest run in the store.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.sdk import Param, PokeReturnValue, dag, get_current_context, task, task_group

MINER = os.environ.get("RIFFLE_MINER_DIR", "/opt/riffle/ingestion/repo_miner")
STORE = os.environ.get("RIFFLE_STORE_DIR", "/opt/riffle/store")
WORK = os.environ.get("RIFFLE_WORK_DIR", "/opt/riffle/work")
sys.path.insert(0, MINER)

import stages  # noqa: E402  (needs MINER on the path)

POKE_S = 30
STAGE_ORDER = {n: i for i, (n, _) in enumerate(stages.STAGES)}
WEEK_S = 7 * 24 * 3600


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ---------------------------------------------------------------- reading the store
def _which_split(version: str, split: str) -> str:
    """`split` if given, else the split of the newest trace in the store ("" when there is none). Every task that
    follows "the newest run" uses this one answer, so gather never mixes the files of two splits."""
    if split.strip():
        return split.strip()
    files = glob.glob(os.path.join(STORE, "*", version, "*", "logs", "*.trace.jsonl"))
    return max(files, key=os.path.getmtime).split(os.sep)[-3] if files else ""


def _trace_files(version: str, split: str) -> list[str]:
    """The newest trace of each PC for one split: `split` if given, else the split of the newest trace."""
    split = _which_split(version, split)
    files = glob.glob(os.path.join(STORE, "*", version, split or "*", "logs", "*.trace.jsonl"))
    if not files:
        return []
    newest_per_pc: dict[str, str] = {}
    for f in files:
        pc = f.split(os.sep)[-5]
        if pc not in newest_per_pc or os.path.getmtime(f) > os.path.getmtime(newest_per_pc[pc]):
            newest_per_pc[pc] = f
    return sorted(newest_per_pc.values())


def _runs(version: str, split: str) -> list[list[dict]]:
    out = []
    for path in _trace_files(version, split):
        with open(path, encoding="utf-8", errors="replace") as fh:
            out.append(list(stages.read_events(fh)))
    return out


def _plan(runs: list[list[dict]]) -> tuple[list[str], bool, float | None]:
    """(repos of every PC's run, every run ended, newest event time)."""
    repos, ended, last = [], bool(runs), None
    for events in runs:
        st = stages.run_state(events)
        repos += [r for r in st["repos"] if r not in repos]
        ended = ended and st["batch_done"]
        last = max(filter(None, (last, st["last_t"])), default=None)
    return repos, ended, last


def _view(runs: list[list[dict]], repo: str) -> stages.RepoView:
    return stages.repo_view(next((e for e in runs if any(x.get("repo") == repo for x in e)), []), repo)


def _label(repo: str) -> None:
    get_current_context()["repo_label"] = repo       # the grid shows repo names, not 0, 1, 2


def _run_over(runs, last, idle_min: int) -> str:
    """Why nothing more will happen to this run, or ''."""
    _, ended, _ = _plan(runs)
    if ended:
        return "the run ended"
    if last and time.time() - last > int(idle_min) * 60:
        return f"the trace has been quiet for {idle_min} min (the run was stopped?)"
    return ""


# ---------------------------------------------------------------- nodes
def _stage_node(name: str, what: str):
    @task.sensor(task_id=_slug(name), poke_interval=POKE_S, timeout=WEEK_S, mode="reschedule", trigger_rule="none_failed",
                 doc_md=f"`{name}`: {what}", map_index_template="{{ repo_label }}")
    def node(version: str, split: str, repo: str, idle_min: int) -> PokeReturnValue:
        _label(repo)
        runs = _runs(version, split)
        view = _view(runs, repo)
        rep = view.stage(name)
        status = rep.status()
        extra = ""
        if name == "mining PRs":
            extra = f" · PRs {view.prs_done}/{view.prs_total or '?'} · {view.workers or '?'} workers"
        elif name in view.progress:                         # a long stage that reports as it goes (open snapshots)
            pg = view.progress[name]
            extra = f" · {pg.get('phase', 'working')} {pg.get('done', 0):,}/{pg.get('total', 0):,}"
            if pg.get("secs_per_call"):
                extra += f" · {pg['secs_per_call']}s a call, about {int(pg.get('eta_s', 0)) // 60} min left"
            if pg.get("phase") == "snapshots":
                extra += f" · {view.workers or '?'} workers"
        print(f"[{repo}] {name}: {status}, {rep.dur_s:.0f}s{extra}")
        if status == "failed":
            raise AirflowFailException(f"{rep.errors} ERROR event(s) while {repo} was in '{name}'")
        if status == "done":
            return PokeReturnValue(is_done=True)
        if view.repo_done:
            raise AirflowSkipException(f"{repo} finished without running '{name}'")
        if status == "pending" and any(r.status() != "pending" for n, r in view.stages.items()
                                       if STAGE_ORDER[n] > STAGE_ORDER[name]):
            raise AirflowSkipException(f"'{name}' was not run: a later stage started without it (the option is off)")
        over = _run_over(runs, _plan(runs)[2], idle_min)
        if over:
            raise AirflowSkipException(f"'{name}' was not reached: {over}")
        return PokeReturnValue(is_done=False)
    return node


def _step_node(name: str, what: str):
    @task.sensor(task_id="pr_" + _slug(name), poke_interval=POKE_S, timeout=WEEK_S, mode="reschedule", trigger_rule="none_failed",
                 doc_md=f"PR step `{name}`: {what}", map_index_template="{{ repo_label }}")
    def node(version: str, split: str, repo: str, idle_min: int) -> PokeReturnValue:
        _label(repo)
        runs = _runs(version, split)
        view = _view(runs, repo)
        st, mining = view.steps[name], view.stage("mining PRs")
        extra = f" · {view.llm_calls} LLM calls, {view.llm_secs:.0f}s" if name == "llm" else ""
        print(f"[{repo}] {name}: {st.n} PRs through it, {st.secs:.0f}s in worker threads, "
              f"{st.in_flight} of {view.workers or '?'} workers in it now{extra}")
        if mining.status() == "done" or view.repo_done:
            if st.n == 0:
                raise AirflowSkipException(f"no PR used '{name}' in {repo} (the option was off, or nothing to do)")
            return PokeReturnValue(is_done=True)
        if mining.status() == "failed":
            raise AirflowFailException(f"'mining PRs' failed for {repo}")
        over = _run_over(runs, _plan(runs)[2], idle_min)
        if over:
            raise AirflowSkipException(f"stopped while mining: {over}")
        return PokeReturnValue(is_done=False)
    return node


def _versions_in(parquet: str) -> list[str]:
    """The miner versions named in a dataset's own `miner_version` column, sorted (what the rows say, not what was asked)."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    col = pq.read_table(parquet, columns=["miner_version"]).column("miner_version")
    return sorted(str(v) for v in pc.unique(col).to_pylist() if v is not None)


def _run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


@dag(
    dag_id="riffle_miner",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=2,
    max_active_tasks=64,
    tags=["miner", "ingestion"],
    params={
        "miner_version": Param("7.0.2", type="string", description="miner version of the run to follow"),
        "split_id": Param("", type="string", description="8-hex split ID the PCs print at start; blank = the newest run"),
        "idle_min": Param(30, type="integer", description="a trace quiet this long means the run was stopped"),
        "dvc_remote": Param("", type="string", description="DVC remote, e.g. gs://bucket/riffle; blank skips publish"),
    },
)
def riffle_miner():
    version, split = "{{ params.miner_version }}", "{{ params.split_id }}"
    idle = "{{ params.idle_min }}"

    @task.sensor(poke_interval=POKE_S, timeout=WEEK_S, mode="reschedule")
    def plan(version: str, split: str) -> PokeReturnValue:
        """Wait for a run's trace to say which repos it will mine, then draw one chain per repo."""
        repos, _, _ = _plan(_runs(version, split))
        print(f"repos in the run: {repos or 'none yet (waiting for the miner to start and sync)'}")
        return PokeReturnValue(is_done=bool(repos), xcom_value=repos)

    @task_group(group_id="repo")
    def repo_chain(repo: str):
        before_mining = [(n, w) for n, w in stages.STAGES if n not in ("mining PRs", "repo-wide labels")]
        prev = None
        for name, what in before_mining:
            node = _stage_node(name, what)(version, split, repo, idle)
            if prev is not None:
                prev >> node
            prev = node
        mining = _stage_node("mining PRs", "features and labels per PR, in worker threads")(version, split, repo, idle)
        final = _stage_node("repo-wide labels", "flaky correction and the repo-wide columns")(version, split, repo, idle)
        steps = [_step_node(n, w)(version, split, repo, idle) for n, w in stages.PR_STEPS]
        prev >> [mining, *steps]
        mining >> final
        return final

    @task(trigger_rule="all_done")
    def gate(version: str, split: str):
        """Merging needs every repo finished, with no stage failed."""
        runs = _runs(version, split)
        repos, ended, _ = _plan(runs)
        problems = {}
        for repo in repos:
            view = _view(runs, repo)
            bad = [n for n, r in view.stages.items() if r.status() == "failed"]
            if bad or not view.repo_done:
                problems[repo] = bad or "not finished"
        if problems or not ended:
            raise AirflowFailException(f"not ready to merge: {problems or 'the run has not ended'}")

    @task
    def gather(version: str, split: str) -> str:
        """Copy every PC's data files into one directory: from the store's disk, or from the bucket
        when RIFFLE_BUCKET is set (datasets live only there)."""
        split = _which_split(version, split)
        if not split:
            raise AirflowFailException(f"no run of miner {version} in the store")
        out = os.path.join(WORK, version, split, get_current_context()["run_id"], "shards")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        bucket = os.environ.get("RIFFLE_BUCKET")
        want = split
        if bucket:
            from google.cloud import storage
            for blob in storage.Client().list_blobs(bucket, match_glob=f"*/{version}/{want}/data/*"):
                if not blob.name.endswith("/"):
                    blob.download_to_filename(os.path.join(out, os.path.basename(blob.name)))
        else:
            for d in sorted(glob.glob(os.path.join(STORE, "*", version, want, "data"))):
                for f in os.listdir(d):
                    shutil.copy2(os.path.join(d, f), out)
        if not os.listdir(out):
            raise AirflowFailException(f"no data files for {version}/{want}")
        return out

    @task
    def merge(shards: str) -> str:
        """merge_shards.py takes the dataset name the PCs used with --out; here it is read from their files
        (<name>_pc1of3.parquet), so a run that did not use the default name merges too."""
        names = os.listdir(shards)
        stems = {m[1] for f in names if (m := re.fullmatch(r"(.+)_pc\d+of\d+\.parquet", f))}
        if not stems:                                       # one PC (a plain run, or --shard 1/1): nothing to join
            plain = [f[:-len(".parquet")] for f in names if f.endswith(".parquet")
                     and not re.search(r"_(snapshots|commit_features|merged.*)\.parquet$", f)]
            if len(plain) != 1:
                raise AirflowFailException(f"found no sharded datasets and {len(plain)} plain ones in {shards}")
            name = plain[0]
            for suffix in ("", "_snapshots", "_commit_features"):
                src = os.path.join(shards, f"{name}{suffix}.parquet")
                if not os.path.isfile(src):
                    raise AirflowFailException(f"{name}{suffix}.parquet is missing from {shards}")
                shutil.copy2(src, os.path.join(shards, f"{name}_merged{suffix}.parquet"))
            seen = _versions_in(os.path.join(shards, f"{name}.parquet"))
            with open(os.path.join(shards, f"{name}_merged.merge.json"), "w", encoding="utf-8") as fh:
                json.dump({"miner_version": seen, "single_pc": True, "source": f"{name}.parquet"}, fh)
            print(f"single-PC run '{name}': its tables are the merged set (miner versions in its rows: {seen})")
            return shards
        if len(stems) != 1:
            raise AirflowFailException(f"expected the PCs' datasets to share one name, found {sorted(stems)} in {shards}")
        _run([sys.executable, os.path.join(MINER, "merge_shards.py"), "--out", f"{stems.pop()}.parquet", "--dir", shards])
        return shards

    @task
    def check(shards: str, version: str):
        _run([sys.executable, os.path.join(MINER, "publish.py"), "check", "--dir", shards, "--version", version])

    @task
    def publish(shards: str, version: str, split: str, remote: str) -> str:
        if not remote:
            raise AirflowSkipException("no dvc_remote given")
        allowed = [a.strip() for a in os.environ.get("RIFFLE_DVC_ALLOWED_REMOTES", "").split(",") if a.strip()]
        if not remote.startswith("gs://") or (allowed and not any(remote.startswith(a) for a in allowed)):
            raise AirflowFailException(f"dvc_remote {remote!r} is not an allowed gs:// address (RIFFLE_DVC_ALLOWED_REMOTES)")
        ws = os.path.join(WORK, "dvc", version, _which_split(version, split) or "all", get_current_context()["run_id"])
        os.makedirs(ws, exist_ok=True)
        _run([sys.executable, os.path.join(MINER, "publish.py"), "push", "--dir", shards,
              "--workspace", ws, "--remote", remote, "--version", version])
        with open(os.path.join(ws, "mined.dvc"), encoding="utf-8") as fh:
            pointer = fh.read()
        print(f"Commit this as data/mined.dvc:\n{pointer}")
        return pointer

    repos = plan(version, split)
    chains = repo_chain.expand(repo=repos)
    g = gate(version, split)
    chains >> g
    shards = gather(version, split)
    g >> shards
    merged = merge(shards)
    checked = check(merged, version)
    publish(merged, version, split, "{{ params.dvc_remote }}").set_upstream(checked)


riffle_miner()

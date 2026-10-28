"""Flow diagrams of the Riffle repo miner (vector PDF). Facts: pipelines/ingestion/repo_miner/ARCHITECTURE.md and stages.py."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

import os
OUT = os.path.dirname(os.path.abspath(__file__)) + "/"
C = {"in": "#eef1f5", "drv": "#dbe9fb", "git": "#dff3e6", "api": "#fde9c9", "cpu": "#ece3fb", "llm": "#fbdcdc",
     "out": "#e2f1d6", "ext": "#f3f3f3", "lab": "#fff1b8"}
EDGE = "#4a5160"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 6.6})


def canvas(w, h):
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, text, kind="drv", bold=False, size=None, ls="-"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06", fc=C[kind], ec=EDGE, lw=0.7, ls=ls))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size or 6.6, fontweight="bold" if bold else "normal",
            linespacing=1.25)
    return (x, y, w, h)


def group(ax, x, y, w, h, title):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.0,rounding_size=0.08", fc="none", ec="#8a92a0", lw=0.8, ls=(0, (4, 3))))
    ax.text(x + 0.08, y + h - 0.07, title, ha="left", va="top", fontsize=6.4, color="#555d6b", fontstyle="italic")


def side(b, s):
    x, y, w, h = b
    return {"t": (x + w / 2, y + h), "b": (x + w / 2, y), "l": (x, y + h / 2), "r": (x + w, y + h / 2)}[s]


def arrow(ax, a, sa, b, sb, label=None, ls="-", rad=0.0, lx=0, ly=0):
    p, q = side(a, sa), side(b, sb)
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=7, lw=0.8, color=EDGE, ls=ls,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=0))
    if label:
        ax.text((p[0] + q[0]) / 2 + lx, (p[1] + q[1]) / 2 + ly, label, fontsize=5.8, color="#3a4150", ha="center", va="center",
                bbox=dict(fc="white", ec="none", pad=0.6))


def legend(ax, x, y, items):
    for i, (k, t) in enumerate(items):
        ax.add_patch(FancyBboxPatch((x + i * 1.18, y), 0.16, 0.12, boxstyle="round,pad=0.0", fc=C[k], ec=EDGE, lw=0.5))
        ax.text(x + i * 1.18 + 0.22, y + 0.06, t, fontsize=5.8, va="center")


# ------------------------------------------------------------------ 1: end to end
def fig1():
    W, H = 7.3, 9.0
    fig, ax = canvas(W, H)
    ax.text(0.1, H - 0.18, "Riffle repo miner: from a repos file to training tables", fontsize=9, fontweight="bold", va="top")
    legend(ax, 0.1, H - 0.52, [("in", "input"), ("drv", "batch driver"), ("git", "local git"), ("api", "GitHub API"),
                               ("out", "output"), ("ext", "other service")])

    i1 = box(ax, 0.1, 7.55, 2.2, 0.62, "repos file\nURL  [N PRs]  [~N commits]\n[domain: name]", "in")
    i2 = box(ax, 2.55, 7.55, 2.0, 0.62, ".env\nGITHUB_TOKEN(s), LLM backend\nRIFFLE_SYNC_URL / TOKEN", "in")
    i3 = box(ax, 4.8, 7.55, 2.4, 0.62, "flags  (lead's shared settings\nfrom the store + this PC's own:\n--shard K/N, --workers, --clone-dir)", "in")

    d1 = box(ax, 0.1, 6.45, 7.1, 0.7,
             "build_dataset.py  (batch driver, one per PC)\n"
             "validate flags  >  read repos file  >  --only-domain filter  >  balanced_shards(K of N) by PRs or commits  >  split ID (8 hex)\n"
             "output name tagged with the PC: dataset_pc1of6.parquet", "drv", bold=False)
    for b in (i1, i2, i3):
        arrow(ax, b, "b", d1, "t")

    group(ax, 0.1, 3.55, 5.0, 2.65, "for each repo in this PC's share (one subprocess per repo bounds memory)")
    s1 = box(ax, 0.3, 5.45, 1.5, 0.5, "already in --out?\nskip (finished)", "drv")
    s2 = box(ax, 2.0, 5.45, 1.5, 0.5, "git clone\nfull history, retried,\nstall killed", "git")
    s3 = box(ax, 3.7, 5.45, 1.25, 0.5, "mine_repo.py\n(subprocess,\nresumes from\ncheckpoint)", "drv", size=6.2)
    s4 = box(ax, 3.7, 4.55, 1.25, 0.5, "delete clone\n(only on success)", "git")
    s5 = box(ax, 2.0, 4.55, 1.5, 0.5, "sync to store\nlogs, trace, health,\ndataset, run.json", "api")
    s6 = box(ax, 0.3, 4.55, 1.5, 0.5, "next repo\nStop = SIGINT, keep\ncheckpoint, exit 130", "drv", size=6.2)
    arrow(ax, d1, "b", s1, "t")
    arrow(ax, s1, "r", s2, "l")
    arrow(ax, s2, "r", s3, "l")
    arrow(ax, s3, "b", s4, "t")
    arrow(ax, s4, "l", s5, "r")
    arrow(ax, s5, "l", s6, "r")
    ax.text(2.6, 3.72, "exit code 3 (LLM could not answer) aborts the whole batch, no retry", fontsize=5.8, color="#7a2a2a", ha="center")
    ax.annotate("", xy=(0.3, 4.55), xytext=(0.3, 5.45), arrowprops=dict(arrowstyle="-|>", color=EDGE, lw=0.7, ls=":", connectionstyle="arc3,rad=0.0"))

    st = box(ax, 5.4, 4.7, 1.8, 1.4, "team store (GCP VM)\nPUT /v1/files/...\nwrite-only token per PC\nbucket + DuckDB\nLoki traces", "ext")
    arrow(ax, s5, "r", st, "l", ls="--", ly=0.0)
    ui = box(ax, 5.4, 3.55, 1.8, 0.85, "store pages, Loki,\nGrafana, Airflow\nriffle_miner DAG\n(read run traces)", "ext", size=6.2)
    arrow(ax, st, "b", ui, "t", ls="--")

    be = box(ax, 0.1, 2.75, 5.0, 0.55, "batch end:  assemble snapshots (snapshots.py)  >  shard manifest (*.shard.json)  >  batch done\n(batch incomplete if any file failed to send)", "drv")
    arrow(ax, (0.1, 3.55, 5.0, 0.0), "b", be, "t")

    o1 = box(ax, 0.1, 1.7, 1.6, 0.7, "dataset\none row per closed PR\nmined_dataset", "out")
    o2 = box(ax, 1.85, 1.7, 1.6, 0.7, "snapshots\none row per PR moment\nmined_snapshot", "out")
    o3 = box(ax, 3.6, 1.7, 1.5, 0.7, "commit features\none row per head commit\nmined_commit_features", "out", size=6.0)
    for o in (o1, o2, o3):
        arrow(ax, be, "b", o, "t")
    o4 = box(ax, 5.4, 1.7, 1.8, 0.7, "also kept locally:\nout.partial.jsonl checkpoint\nhealth/*.json, logs/*.trace", "out", size=6.0)
    arrow(ax, be, "r", o4, "t", ls=":", rad=-0.2)

    m1 = box(ax, 0.1, 0.55, 1.5, 0.7, "merge_shards.py\njoin the PCs' files\n(dedupe repo, PR)", "drv")
    m2 = box(ax, 1.8, 0.55, 1.5, 0.7, "publish.py check\ncolumns match\ncontracts/ schemas", "drv")
    m3 = box(ax, 3.5, 0.55, 1.3, 0.7, "publish.py push\ndvc add + push\nmined.dvc pointer", "drv")
    m4 = box(ax, 5.0, 0.55, 1.15, 0.7, "frames.py\njoin 3 tables\nsplit by PR\nmask leaky cols", "drv", size=6.0)
    m5 = box(ax, 6.3, 0.55, 0.9, 0.7, "slices.py\nlabel balance\nper slice", "drv", size=6.0)
    arrow(ax, o2, "b", m1, "t", rad=0.0)
    for a, b in ((m1, m2), (m2, m3), (m3, m4), (m4, m5)):
        arrow(ax, a, "r", b, "l")
    ax.text(0.1, 0.25, "All steps after the batch are driven by the Airflow DAG riffle_miner (merge, check, publish); mining itself runs on the PCs.",
            fontsize=5.9, color="#444b58")
    fig.savefig(OUT + "flow_miner_overview.pdf")
    plt.close(fig)


# ------------------------------------------------------------------ 2: one repo
STAGES = [
    ("0  setup", "default branch, obs_cutoff, blame flags, GitHubAPI", "git"),
    ("1  fetching PRs", "choose the PRs that become rows (REST, 100 per call)", "api"),
    ("2  required checks", "branch protection's required status checks", "api"),
    ("3  merge style", "merge-commit, squash or rebase", "api"),
    ("4  timelines", "every PR's issue timeline (up to 8 parallel); merge labels, maintainers", "api"),
    ("5  author index", "per-author merged-PR times (local git show)", "git"),
    ("6  open snapshots", "PR head as it was when opened (ADR 0010); GraphQL force-pushes", "api"),
    ("7  concurrency", "PRs open at the same time on the same files", "git"),
    ("8  ci history", "per-commit CI verdicts from workflow runs (cached 7 days)", "api"),
    ("9  loading history", "git log --numstat once; build the history index", "git"),
    ("10 szz", "SZZ index for label_szz_bug (process pool)", "cpu"),
    ("11 reviews prefetch", "GraphQL review history within quota", "api"),
    ("12 mining PRs", "features + labels per PR in worker threads  (figure 3)", "cpu"),
    ("13 repo-wide labels", "flaky correction, repo-local, merge signals", "git"),
]


def fig2():
    W, H = 7.3, 9.9
    fig, ax = canvas(W, H)
    ax.text(0.1, H - 0.18, "One repository: mine_repo.py, stage by stage", fontsize=9, fontweight="bold", va="top")
    legend(ax, 0.1, H - 0.52, [("git", "local git"), ("api", "GitHub API"), ("cpu", "thread/process pool"), ("out", "output")])
    top = H - 0.95
    bh, gap = 0.4, 0.17
    boxes = []
    for i, (n, d, k) in enumerate(STAGES):
        y = top - i * (bh + gap) - bh
        b = box(ax, 0.1, y, 1.65, bh, n, k, bold=True, size=6.8)
        ax.text(1.85, y + bh / 2, d, fontsize=6.3, va="center")
        boxes.append(b)
        if i:
            arrow(ax, boxes[i - 1], "b", b, "t")
    # annotations at right
    ax.text(5.05, boxes[1][1] + 0.35, "resume: loads the\npinned selection\n(ci_cache/*.selection.json)", fontsize=5.8, color="#555", va="center")
    ax.text(5.05, boxes[4][1] + 0.3, "5,000 REST calls/hour;\nquota sleep is not a hang", fontsize=5.8, color="#555", va="center")
    ax.text(5.05, boxes[8][1] + 0.3, "cache: ci_cache/<repo>.json", fontsize=5.8, color="#555", va="center")
    # end outputs
    y = boxes[-1][1] - 0.2 - 0.62
    e1 = box(ax, 0.1, y, 2.2, 0.62, "write dataset (atomic os.replace)\ndedupe (repo, pr_number)\nnewest extraction wins", "out")
    e2 = box(ax, 2.5, y, 2.0, 0.62, "health check (health.py)\nrow count, bot share, outside\nPRs, LLM and CI coverage", "out")
    e3 = box(ax, 4.7, y, 2.5, 0.62, "only after a clean write:\nremove checkpoint and pinned\nselection; keep ci_cache", "out")
    arrow(ax, boxes[-1], "b", e1, "t")
    arrow(ax, e1, "r", e2, "l")
    arrow(ax, e2, "r", e3, "l")
    # side loop for resume
    ax.text(5.05, boxes[12][1] + 0.12, "checkpoint line per PR\n(out.partial.jsonl) - a Stop\nor crash resumes here", fontsize=5.8, color="#555", va="center")
    fig.savefig(OUT + "flow_miner_repo.pdf")
    plt.close(fig)


# ------------------------------------------------------------------ 3: one PR
def fig3():
    W, H = 7.3, 9.3
    fig, ax = canvas(W, H)
    ax.text(0.1, H - 0.18, "One PR becomes one row: mine_pr and the ordered consumer", fontsize=9, fontweight="bold", va="top")
    legend(ax, 0.1, H - 0.52, [("git", "local git"), ("api", "GitHub API"), ("cpu", "computed"), ("llm", "LLM"), ("lab", "label"), ("out", "output")])

    pool = box(ax, 0.1, 8.0, 7.1, 0.5, "ThreadPoolExecutor: --workers N threads, a window of max(2N, 2 x llm_concurrency) PRs in flight, one mine_pr per PR", "drv")

    group(ax, 0.1, 3.55, 4.6, 4.2, "mine_pr (inside a worker thread; every step is timed)")
    a = box(ax, 0.25, 7.05, 1.9, 0.5, "1 ranges\nfinal range (what landed) and\nat-open range (ADR 0010)", "git", size=6.0)
    b = box(ax, 2.35, 7.05, 2.2, 0.5, "2 identity + 3 timeline\nmailmap author of the PR's own head;\ntitle/labels rebuilt as at open", "api", size=6.0)
    c = box(ax, 0.25, 6.2, 1.9, 0.62, "4 diff_stats\nLA, LD, NF, ND, NS, Entropy,\npath flags (migration, CI, auth)", "git", size=6.0)
    d = box(ax, 2.35, 6.2, 2.2, 0.62, "5 history index at the base commit\n+ blame of modified lines\n(per file; big PRs capped)", "git", size=6.0)
    e = box(ax, 0.25, 5.35, 1.9, 0.62, "6a aggregate.py\nmax / mean / sum / min\nper-file to fixed columns", "cpu", size=6.0)
    f = box(ax, 2.35, 5.35, 2.2, 0.62, "6b author, co-change, percentiles,\nEXP / REXP / SEXP, bot and\nagent flags, concurrency", "cpu", size=6.0)
    g = box(ax, 0.25, 4.5, 1.9, 0.62, "7 on the at-open diff:\ndeterministic, scanners\n(lizard, semgrep), deps + CVE,\nAPI contract diff", "cpu", size=5.8)
    h = box(ax, 2.35, 4.5, 2.2, 0.62, "8 review events (2 API calls)\n10 PR metadata + merge signals\n(body, agent name, branch prefix)", "api", size=6.0)
    i = box(ax, 0.25, 3.7, 1.9, 0.6, "9 LLM fetch in the worker\n(claude -p / OpenAI API,\nsemaphore llm_concurrency)", "llm", size=5.9)
    j = box(ax, 2.35, 3.7, 2.2, 0.6, "14 snapshot capture\n(snapshots_raw/<repo>/)\nfor rows in LLM scope", "git", size=6.0)
    for p, q in ((a, c), (b, d), (c, e), (d, f), (e, g), (f, h), (g, i), (h, j)):
        arrow(ax, p, "b", q, "t")
    arrow(ax, a, "r", b, "l")

    lab = box(ax, 4.95, 6.2, 2.25, 1.35,
              "labels from the FINAL range\n(after the PR; null if unobserved)\n"
              "11 breakage: revert, hotfix,\nCI fail, SZZ bug > label_bad\n"
              "12 merge outcome: merged,\nrework, duplicate, unwanted,\nabandoned, CI technical", "lab", size=6.0)
    arrow(ax, pool, "b", a, "t")
    arrow(ax, pool, "b", lab, "t", rad=-0.0)
    meta = box(ax, 4.95, 5.35, 2.25, 0.62, "13 provenance:\nminer_version, miner_commit,\nllm_model, prompt version, domain", "cpu", size=6.0)
    arrow(ax, lab, "b", meta, "t")
    note = ax.text(4.95, 4.35, "Point in time: features see the PR\nas it opened and history before its\nfork point. Only labels look forward.\nA PR with no diff, a missing commit\nor a shallow clone is a counted skip,\nnot a row.", fontsize=5.9, color="#444b58", va="top")

    cons = box(ax, 0.1, 2.5, 7.1, 0.7,
               "ordered consumer (main thread): results are taken in PR order\n"
               "skip handling  >  pop LLM result, retry a failed answer in place (later PRs wait)  >  resolve flags  >  append row  >  write checkpoint line (flushed)", "drv")
    arrow(ax, (0.1, 3.55, 4.6, 0.0), "b", cons, "t")
    rw = box(ax, 0.1, 1.45, 3.4, 0.7, "repo-wide pass (needs every PR's outcome)\nflaky.apply (corrects label_ci_fail, label_bad)\nrepo_local columns, merge_signals columns", "cpu")
    out = box(ax, 3.8, 1.45, 3.4, 0.7, "dataset row (one per closed PR)\nplus snapshots and commit features\nassembled at batch end", "out")
    arrow(ax, cons, "b", rw, "t")
    arrow(ax, rw, "r", out, "l")
    ax.text(0.1, 1.05, "Rows are the same whatever the thread timing: results are consumed in PR order, so a resumed run reproduces the same file.",
            fontsize=5.9, color="#444b58")
    ax.text(0.1, 0.8, "Checkpoint stamp (_ckpt) records the flags that shape the data; a resumed run re-mines a PR whose stamp differs.",
            fontsize=5.9, color="#444b58")
    fig.savefig(OUT + "flow_miner_pr.pdf")
    plt.close(fig)


fig1(); fig2(); fig3()
print("ok")

"""
store_report.py — charts of every PC's mining progress, read from the team store (ADR 0015).

    python store_report.py [--out store_report.html] [--figures DIR] [--json runs.json] [--no-admin]

Reads RIFFLE_SYNC_URL and RIFFLE_READ_TOKEN from .env (the same file the miner uses; a PC's write
token cannot read) and calls GET /api/runs, which holds the latest run.json of each PC. It downloads
no tables, so it is cheap and safe to run often.

With RIFFLE_ADMIN_TOKEN also set (the lead's token) it reads GET /v1/admin/overview too. That lists
every PC token with its state (mining, stopped, silent, never) and shows the PCs that have never
uploaded as fully pending. --no-admin skips it.

Mined and pending, per PC:
  planned        the PC's share of the split plan (every PR each repo ever had; run.json's prs_total)
  mined          rows already written (exact)
  left, started  PRs the repo being mined (or stopped mid-way) selected and has not written (exact)
  pending        planned minus mined; includes repos not started, whose PRs are not selected yet
The miner keeps the closed PRs inside its window (finished repos kept a median of about 89% of their
planned PRs), so the final total will be lower than planned. The repos file is found from the run's
split id, or given with --repos-file; without it a PC that never uploaded has no planned share.

Writes one self-contained HTML file (inline SVG, no library, light and dark). --figures DIR also
writes the charts as PDF files for LaTeX (needs matplotlib). Read-only: it never writes to the
store. --json saves the raw reply, and --from-json renders a saved reply offline.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATES = ("done", "mining", "stopped", "failed", "skipped", "pending")
COLOURS = {"done": "#2f9e63", "mining": "#3b82f6", "stopped": "#e0a030", "failed": "#d64545",
           "skipped": "#8a8f98", "pending": "#c9ccd2"}


def _env(name: str) -> str:
    """name from the process environment, else from .env next to this file."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        with open(os.path.join(HERE, ".env"), encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.strip().partition("=")
                if k == name and v.strip():
                    return v.strip().strip("\"'")
    except OSError:
        pass
    return ""


def fetch_runs(url: str, token: str, timeout: float = 30.0) -> list[dict]:
    req = urllib.request.Request(url.rstrip("/") + "/api/runs", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if not isinstance(data, list):
        raise ValueError("the store did not answer with a list of runs")
    return data


def fetch_admin(url: str, token: str, timeout: float = 30.0) -> dict:
    """GET /v1/admin/overview: every PC token with its state, repos done, PRs mined and last upload (lead's admin token)."""
    req = urllib.request.Request(url.rstrip("/") + "/v1/admin/overview", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if not isinstance(data, dict) or not isinstance(data.get("pcs"), list):
        raise ValueError("the store did not answer with an overview")
    return data


def apply_admin(runs: list[dict], overview: dict, plan: dict[str, int] | None = None) -> list[dict]:
    """Add the PCs the admin overview lists but that never uploaded (no run.json yet), each with its share of the
    plan's PRs and repos, so the charts show them as fully pending instead of leaving them out. A PC that uploaded keeps
    its run; the admin state (mining, stopped, silent) is attached as `admin_state`."""
    plan = plan or {}
    have = {r.get("pc") for r in runs}
    by_pc = {p.get("pc"): p for p in overview.get("pcs", [])}
    runs = [dict(r, admin_state=(by_pc.get(r.get("pc")) or {}).get("state")) for r in runs]
    never = sorted(pc for pc in by_pc if pc not in have)
    if never:
        known_prs = sum(int((r.get("status") or {}).get("prs_total") or 0) for r in runs)
        known_repos = sum(len((r.get("status") or {}).get("repos") or {}) for r in runs)
        n_repos = max((sp.get("n_repos") or 0 for sp in overview.get("splits", [])), default=0)
        prs_each = round(max(0, sum(plan.values()) - known_prs) / len(never)) if plan else None
        repos_each = round(max(0, n_repos - known_repos) / len(never)) if n_repos else 0
        for pc in never:
            m = re.match(r"pc(\d+)of(\d+)", pc or "")
            runs.append({"pc": pc, "admin_state": by_pc[pc].get("state"), "status": {
                "pc": f"{m.group(1)}/{m.group(2)}" if m else pc, "reason": "never uploaded", "repos": {},
                "repos_total": repos_each, "prs_total": prs_each, "synced_at": ""}})
    return runs


def parse_plan(path: str) -> dict[str, int]:
    """repo (lower case) -> its total PR count, from the `[N PRs]` note in a repos file."""
    plan = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*(?:https://github\.com/)?([\w.-]+/[\w.-]+)\s*#.*?\[([\d,]+) PRs\]", line)
                if m:
                    plan[m.group(1).lower()] = int(m.group(2).replace(",", ""))
    except OSError:
        pass
    return plan


def find_plan(runs: list[dict]) -> dict[str, int]:
    """The repos file the runs used: joined_repos/<split_id>/<repos_file> next to this script."""
    for r in runs:
        s = r.get("status") or {}
        name, split = (s.get("settings") or {}).get("repos_file"), s.get("split_id")
        if name and split:
            plan = parse_plan(os.path.join(HERE, "joined_repos", str(split), os.path.basename(name)))
            if plan:
                return plan
    return {}


def summarise(runs: list[dict], plan: dict[str, int] | None = None) -> dict:
    """pcs (one per PC, sorted by its number), repos (rows per repo across PCs) and totals.

    Per PC: mined, left_started (exact), pending_plan (repos not started, from the plan) and
    est_not_started (pending_plan times `kept`, the median share of a repo's plan count that finished
    repos kept). Without a plan the not-started parts are None."""
    plan = plan or {}
    pcs, repos, seen_plan, total = [], {}, 0, None
    in_progress = []
    kept_all = []
    for r in runs:
        for name, v in ((r.get("status") or {}).get("repos") or {}).items():
            if v.get("state") == "done" and v.get("rows") and plan.get(name.lower()):
                kept_all.append(v["rows"] / plan[name.lower()])
    kept = statistics.median(kept_all) if kept_all else None
    for r in runs:
        s = r.get("status") or {}
        reps = s.get("repos") or {}
        states = {k: 0 for k in STATES}
        left_started = pending_plan = 0
        for name, v in reps.items():
            st = v.get("state") if v.get("state") in states else "pending"
            states[st] += 1
            seen_plan += plan.get(name.lower(), 0)
            if st in ("mining", "stopped"):
                left_started += max(0, int(v.get("selected") or 0) - int(v.get("rows") or 0))
                if v.get("selected"):
                    in_progress.append({"repo": name, "pc": s.get("pc") or r.get("pc"), "rows": int(v.get("rows") or 0),
                                        "left": max(0, int(v["selected"]) - int(v.get("rows") or 0))})
            elif st == "pending":
                pending_plan += plan.get(name.lower(), 0)
        if not reps and s.get("repos_total"):
            states["pending"] = int(s["repos_total"])
        rows = sum(int(v.get("rows") or 0) for v in reps.values())
        planned = s.get("prs_total")
        for name, v in reps.items():
            if v.get("rows"):
                e = repos.setdefault(name, {"repo": name, "rows": 0, "pcs": set(), "state": v.get("state")})
                e["rows"] += int(v["rows"])
                e["pcs"].add(r.get("pc"))
        have_plan = bool(plan) and kept is not None
        pcs.append({"pc": s.get("pc") or r.get("pc"), "name": r.get("pc"), "host": s.get("host") or "",
                    "version": s.get("miner_version") or "", "reason": s.get("reason") or "",
                    "states": states, "rows": rows, "left_started": left_started, "admin_state": r.get("admin_state"),
                    "planned": planned, "pending": max(0, int(planned) - rows) if planned else None,
                    "pending_plan": pending_plan if plan else None,
                    "est_not_started": round(pending_plan * kept) if have_plan else None,
                    "prs_total": s.get("prs_total"), "synced_at": s.get("synced_at") or ""})
    pcs.sort(key=lambda p: (str(p["pc"]).split("/")[0].zfill(3), str(p["name"])))
    top = sorted(repos.values(), key=lambda e: -e["rows"])
    unreported = None
    if plan and kept is not None:
        total = sum(plan.values())
        unreported = {"plan": max(0, total - seen_plan), "est": round(max(0, total - seen_plan) * kept)}
    in_progress.sort(key=lambda e: -(e["rows"] + e["left"]))
    return {"pcs": pcs, "in_progress": in_progress, "repos": [{**e, "pcs": sorted(e["pcs"])} for e in top], "kept": kept, "plan_total": total,
            "unreported": unreported, "rows": sum(p["rows"] for p in pcs),
            "left_started": sum(p["left_started"] for p in pcs),
            "planned": sum(int(p["planned"] or 0) for p in pcs), "pending": sum(p["pending"] or 0 for p in pcs),
            "repos_done": sum(p["states"]["done"] for p in pcs), "repos_total": sum(sum(p["states"].values()) for p in pcs),
            "est_not_started": sum(p["est_not_started"] or 0 for p in pcs) + (unreported["est"] if unreported else 0)}


def _bars(items, width=700, label_w=250, row_h=24):
    """Horizontal stacked bars; items = [(label, [(value, colour, title)])]."""
    top = max((sum(v for v, _, _ in segs) for _, segs in items), default=0) or 1
    inner = width - label_w - 70
    out = [f'<svg viewBox="0 0 {width} {row_h * len(items) + 6}" role="img" class="chart">']
    for i, (label, segs) in enumerate(items):
        y, x = i * row_h + 3, label_w
        out.append(f'<text x="{label_w - 8}" y="{y + 15}" text-anchor="end" class="lab">{html.escape(label)}</text>')
        for v, colour, title in segs:
            if v <= 0:
                continue
            w = max(1.0, inner * v / top)
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{row_h - 7}" fill="{colour}"><title>{html.escape(title)}</title></rect>')
            x += w
        out.append(f'<text x="{x + 6:.1f}" y="{y + 15}" class="val">{sum(v for v, _, _ in segs):,}</text>')
    return "".join(out) + "</svg>"


def render(runs: list[dict], source: str = "", plan: dict[str, int] | None = None) -> str:
    s = summarise(runs, plan)
    pcs = s["pcs"]
    items = []
    for p in pcs:
        planned = int(p["planned"] or 0)
        rest = max(0, planned - p["rows"] - p["left_started"])
        items.append((f'PC {p["pc"]}', [(p["rows"], COLOURS["done"], f'{p["rows"]:,} PRs mined'),
                                       (p["left_started"], COLOURS["mining"], f'{p["left_started"]:,} PRs left in the repo being mined'),
                                       (rest, COLOURS["pending"], f'{rest:,} PRs in repos not yet selected')]))
    c_prs = _bars(items)
    c_state = _bars([(f'PC {p["pc"]}', [(p["states"][k], COLOURS[k], f'{p["states"][k]} repos {k}') for k in STATES]) for p in pcs])
    c_repo = _bars([(e["repo"], [(e["rows"], COLOURS["done"], f'{e["rows"]:,} rows from {", ".join(e["pcs"])}')]) for e in s["repos"][:15]])
    legend = "".join(f'<span><i style="background:{COLOURS[k]}"></i>{k}</span>' for k in STATES)
    rows = "".join(
        f'<tr><td>PC {html.escape(str(p["pc"]))}</td><td>{html.escape(p["host"])}</td><td>{html.escape(p["version"])}</td>'
        f'<td>{html.escape(p["reason"])}</td><td class="n">{p["rows"]:,}</td><td>{html.escape(p["synced_at"] or "never")}</td></tr>' for p in pcs)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riffle store report</title><style>
:root{{--bg:#fff;--fg:#16181d;--mut:#667;--line:#dfe2e7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111316;--fg:#e8eaee;--mut:#9aa1ac;--line:#2a2e35}}}}
body{{margin:0 auto;max-width:760px;padding:24px 16px;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}}
h1{{font-size:20px;margin:0}} h2{{font-size:15px;margin:28px 0 6px}} p{{color:var(--mut);margin:4px 0 8px}}
.chart{{width:100%;height:auto}} .lab,.val{{font-size:12px;fill:var(--fg)}} .val{{fill:var(--mut)}}
.legend span{{margin-right:12px;font-size:12px;color:var(--mut)}} .legend i{{display:inline-block;width:10px;height:10px;margin-right:4px;border-radius:2px}}
table{{border-collapse:collapse;width:100%;font-size:12px}} td,th{{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}} .n{{text-align:right}}
</style></head><body>
<h1>Riffle store report</h1>
<p>{s["rows"]:,} of {s["planned"]:,} planned PRs mined ({(s["rows"] / s["planned"] if s["planned"] else 0):.1%}), {s["pending"]:,} pending; {s["repos_done"]} of {s["repos_total"]} repositories done. {html.escape(source)}</p>
<h2>PRs mined and left, per PC</h2><div class="legend"><span><i style="background:{COLOURS["done"]}"></i>mined</span><span><i style="background:{COLOURS["mining"]}"></i>left in repo being mined</span><span><i style="background:{COLOURS["pending"]}"></i>pending, not yet selected</span></div><p>Planned is each PC's share of the split plan: every PR a repo ever had. The miner keeps the closed PRs inside its window, so the final total will be somewhat lower.</p>{c_prs}
<h2>Repos by state, per PC</h2><div class="legend">{legend}</div>{c_state}
<h2>Rows per repo, all PCs (top 15)</h2>{c_repo}
<h2>PCs</h2><table><tr><th>PC</th><th>Host</th><th>Miner</th><th>State</th><th>Rows</th><th>Last sync (UTC)</th></tr>{rows}</table>
</body></html>"""


def figures(s: dict, outdir: str) -> list[str]:
    """Write the three charts as PDF files into outdir (vector, for LaTeX). Needs matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "text.color": "#16181d", "axes.edgecolor": "none"})
    pcs = s["pcs"]

    def frame(n, height_per=0.34):
        fig, ax = plt.subplots(figsize=(7.0, max(1.6, height_per * n + 0.9)))
        for side in ("top", "right", "left", "bottom"):
            ax.spines[side].set_visible(False)
        ax.tick_params(length=0, labelsize=9)
        ax.xaxis.set_visible(False)
        ax.invert_yaxis()
        return fig, ax

    def stack(ax, labels, series, colours, names, share=False):
        """Stacked horizontal bars. share=True scales every bar to its own total (so PRs and repositories can sit together)."""
        if share:
            tots = [sum(v[i] for v in series) or 1 for i in range(len(labels))]
            series = [[v / t for v, t in zip(vals, tots)] for vals in series]
        left = [0] * len(labels)
        for vals, c, nm in zip(series, colours, names):
            ax.barh(labels, vals, left=left, color=c, height=0.62, label=nm)
            left = [a + b for a, b in zip(left, vals)]
        top = max(left) or 1
        if share:
            ax.set_xlim(0, 1)
            return
        for i, tot in enumerate(left):
            ax.text(tot + top * 0.012, i, f"{tot:,}", va="center", fontsize=8.5, color="#667")
        ax.set_xlim(0, top * 1.12)

    out = []

    def save(fig, name):
        fig.tight_layout()
        out.append(os.path.join(outdir, name))
        fig.savefig(out[-1])
        plt.close(fig)

    labels = [f'PC {p["pc"]}   {(p["rows"] / p["planned"] if p["planned"] else 0):.0%}' for p in pcs]
    mined = [p["rows"] for p in pcs]
    cur = [p["left_started"] for p in pcs]
    rest = [max(0, int(p["planned"] or 0) - p["rows"] - p["left_started"]) for p in pcs]
    fig, ax = frame(len(labels))
    stack(ax, labels, [mined, cur, rest], [COLOURS["done"], COLOURS["mining"], COLOURS["pending"]],
          ["mined", "left in the repo being mined", "pending, not yet selected"])
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False, fontsize=8, ncol=3)
    save(fig, "store_prs.pdf")

    tot_m, tot_c = sum(mined), sum(cur)
    tot_r = sum(rest)
    fig, ax = frame(2, 0.55)
    stack(ax, ["PRs", "Repositories"], [[tot_m, s["repos_done"]],
                                         [tot_c, sum(p["states"]["mining"] + p["states"]["stopped"] for p in pcs)],
                                         [tot_r, sum(p["states"]["pending"] for p in pcs)]],
          [COLOURS["done"], COLOURS["mining"], COLOURS["pending"]], ["mined / done", "in progress", "pending"], share=True)
    for i, vals in enumerate(([tot_m, tot_c, tot_r],
                              [s["repos_done"], sum(p["states"]["mining"] + p["states"]["stopped"] for p in pcs),
                               sum(p["states"]["pending"] for p in pcs)])):
        t, x = sum(vals) or 1, 0.0
        for v in vals:
            if v / t > 0.07:
                ax.text(x + v / t / 2, i, f"{v:,}", ha="center", va="center", fontsize=8, color="#16181d")
            x += v / t
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False, fontsize=8, ncol=3)
    save(fig, "store_total.pdf")

    prog = s["in_progress"]
    if prog:
        fig, ax = frame(len(prog), 0.5)
        stack(ax, [f'{e["repo"]} (PC {e["pc"]})' for e in prog], [[e["rows"] for e in prog], [e["left"] for e in prog]],
              [COLOURS["done"], COLOURS["mining"]], ["mined", "left in this repo"])
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False, fontsize=8, ncol=2)
        save(fig, "store_inprogress.pdf")

    fig, ax = frame(len(pcs))
    stack(ax, [f'PC {p["pc"]}' for p in pcs], [[p["states"][k] for p in pcs] for k in STATES],
          [COLOURS[k] for k in STATES], list(STATES))
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False, fontsize=8, ncol=6)
    fig.tight_layout()
    out.append(os.path.join(outdir, "store_states.pdf"))
    fig.savefig(out[-1])
    plt.close(fig)

    top = s["repos"][:15]
    fig, ax = frame(len(top), 0.26)
    stack(ax, [e["repo"] for e in top], [[e["rows"] for e in top]], [COLOURS["done"]], ["rows"])
    fig.tight_layout()
    out.append(os.path.join(outdir, "store_repos.pdf"))
    fig.savefig(out[-1])
    plt.close(fig)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="store_report.html")
    ap.add_argument("--json", help="also save the store's raw reply here")
    ap.add_argument("--repos-file", help="the repos file of the split (default: joined_repos/<split id>/ from the run)")
    ap.add_argument("--figures", metavar="DIR", help="also write the charts as PDF files here (needs matplotlib)")
    ap.add_argument("--no-admin", action="store_true", help="do not read the admin overview even if RIFFLE_ADMIN_TOKEN is set")
    ap.add_argument("--from-json", help="render a saved reply instead of calling the store")
    a = ap.parse_args(argv)
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as fh:
            runs, source = json.load(fh), f"From {os.path.basename(a.from_json)}."
    else:
        url, token = _env("RIFFLE_SYNC_URL"), _env("RIFFLE_READ_TOKEN")
        if not url or not token:
            print("store_report: set RIFFLE_SYNC_URL and RIFFLE_READ_TOKEN (a read token) in .env", file=sys.stderr)
            return 2
        try:
            runs, source = fetch_runs(url, token), "From the team store."
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"store_report: cannot read the store: {e}", file=sys.stderr)
            return 1
    plan = parse_plan(a.repos_file) if a.repos_file else find_plan(runs)
    admin_tok = _env("RIFFLE_ADMIN_TOKEN") if not a.from_json and not a.no_admin else ""
    if admin_tok:                                   # the lead's admin token: also lists PCs that never uploaded
        try:
            runs = apply_admin(runs, fetch_admin(_env("RIFFLE_SYNC_URL"), admin_tok), plan)
            source += " Admin overview included."
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"store_report: admin overview not read ({e}); PCs that never uploaded are left out", file=sys.stderr)
    if not plan:
        print("store_report: no repos file found, so PRs in repos not started are not estimated (use --repos-file)", file=sys.stderr)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(runs, fh)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(render(runs, source, plan))
    if a.figures:
        try:
            for f in figures(summarise(runs, plan), a.figures):
                print(f"wrote {f}")
        except ImportError:
            print("store_report: --figures needs matplotlib", file=sys.stderr)
            return 1
    print(f"wrote {a.out} ({len(runs)} PCs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

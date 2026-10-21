"""
ui.py — the pages: server-rendered HTML, no scripts, one stylesheet.

Overview: one row per PC (repos done and total, PRs mined, PRs still to mine in
the repos already started, last sync). Run page: one row per repo with its
state, rows, PR count and health flags. Search: grep over the PCs' run logs.
"""
from __future__ import annotations

import html
import time
from urllib.parse import quote, urlencode

LIVE_S = 25 * 60            # a PC that has not synced for this long is "silent"

CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--card:#fff;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--acc:#1d4ed8}
@media(prefers-color-scheme:dark){:root{--bg:#0c0a09;--fg:#e7e5e4;--mut:#a8a29e;--line:#292524;--card:#1c1917;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--acc:#93c5fd}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:20px;margin:8px 0 16px}h2{font-size:15px;margin:24px 0 8px}
nav{display:flex;gap:16px;align-items:center;border-bottom:1px solid var(--line);padding:10px 16px}nav b{margin-right:auto}
a{color:var(--acc)}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:500}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.tot{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:8px}
.tot div{background:var(--card);border:1px solid var(--line);padding:8px 14px;min-width:130px}.tot b{display:block;font-size:20px}
.tot span{color:var(--mut);font-size:12px}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mut{color:var(--mut)}
.bar{height:6px;background:var(--line);min-width:80px}.bar i{display:block;height:100%;background:var(--ok)}
pre{background:var(--card);border:1px solid var(--line);padding:8px;overflow:auto;white-space:pre-wrap;word-break:break-word}
form{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}input,select,button{font:inherit;padding:5px 8px}
@media(max-width:640px){main{padding:12px}th,td{padding:5px 6px}}
"""


def page(title: str, body: str, refresh: int = 0) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">{meta}<style>{CSS}</style></head>'
            f'<body><nav><b>Riffle data</b><a href="/">Overview</a><a href="/search">Log search</a></nav>'
            f'<main>{body}</main></body></html>')


def ago(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "never"
    s = int((now or time.time()) - ts)
    return ("just now" if s < 60 else f"{s // 60} min ago" if s < 3600 else
            f"{s // 3600} h ago" if s < 86400 else f"{s // 86400} d ago")


def state_of(run: dict, now: float | None = None) -> tuple[str, str]:
    """(label, css class): mining, done, stopped or silent."""
    st, age = run["status"], (now or time.time()) - run["updated"]
    reason = st.get("reason")
    if reason == "batch done":
        return "done", "ok"
    if reason == "stopped":
        return "stopped", "warn"
    return ("mining", "ok") if age < LIVE_S else ("silent", "bad")


def _n(v) -> str:
    return "–" if v is None else f"{v:,}"


def overview(runs: list[dict], now: float | None = None) -> str:
    tot = {k: sum(r["status"].get(k) or 0 for r in runs)
           for k in ("repos_total", "repos_done", "repos_failed", "prs_done", "prs_remaining", "repos_pending")}
    cards = "".join(f"<div><b>{_n(v)}</b><span>{html.escape(label)}</span></div>" for label, v in (
        ("PCs", len(runs)), ("repos done", tot["repos_done"]), ("repos total", tot["repos_total"]),
        ("repos not started", tot["repos_pending"]), ("PRs mined", tot["prs_done"]),
        ("PRs left (started repos)", tot["prs_remaining"]), ("repos failed", tot["repos_failed"])))
    rows = []
    for r in runs:
        st = r["status"]
        label, cls = state_of(r, now)
        total, done = st.get("repos_total") or 0, st.get("repos_done") or 0
        pct = int(100 * done / total) if total else 0
        href = "/run?" + urlencode({"pc": r["pc"], "prefix": r["prefix"]})
        rows.append(
            f'<tr><td><a href="{html.escape(href)}">{html.escape(r["pc"])}</a><br>'
            f'<span class="mut">{html.escape(st.get("host") or "")} · miner {html.escape(st.get("miner_version") or "?")}</span></td>'
            f'<td class="{cls}">{label}</td>'
            f'<td>{done}/{total} repos<div class="bar"><i style="width:{pct}%"></i></div></td>'
            f'<td>{html.escape(st.get("current") or "–")}</td>'
            f'<td class="n">{_n(st.get("prs_done"))}</td><td class="n">{_n(st.get("prs_remaining"))}</td>'
            f'<td class="n">{_n(st.get("repos_pending"))}</td><td class="n">{_n(st.get("repos_failed"))}</td>'
            f'<td>{ago(r["updated"], now)}</td></tr>')
    table = ('<table><tr><th>PC</th><th>State</th><th>Progress</th><th>Now mining</th><th class="n">PRs mined</th>'
             '<th class="n">PRs left</th><th class="n">Repos waiting</th><th class="n">Failed</th><th>Last sync</th></tr>'
             + "".join(rows) + "</table>") if rows else '<p class="mut">No PC has synced yet.</p>'
    note = ('<p class="mut">PRs left counts the repo each PC is mining now. A repo that has not started has no '
            'PR selection yet, so it shows under "repos waiting" instead.</p>')
    return page("Riffle data", f'<h1>Mining progress</h1><div class="tot">{cards}</div>{table}{note}', refresh=60)


def run_page(run: dict, health: dict[str, dict], files: list[dict], uris: dict[str, str | None],
             now: float | None = None) -> str:
    st = run["status"]
    label, cls = state_of(run, now)
    rows = []
    for repo, rec in sorted(st.get("repos", {}).items()):
        sel = rec.get("selected")
        left = (max(0, sel - rec.get("rows", 0)) if sel is not None and rec.get("state") == "mining" else None)
        h = health.get(repo, {})
        flags = "".join(f'<div class="warn">{html.escape(f)}</div>' for f in h.get("flags", [])) or (
            '<span class="ok">ok</span>' if h else '<span class="mut">–</span>')
        scls = {"done": "ok", "failed": "bad", "mining": "warn"}.get(rec.get("state"), "mut")
        rows.append(f'<tr><td>{html.escape(repo)}</td><td class="{scls}">{html.escape(rec.get("state", "?"))}</td>'
                    f'<td class="n">{_n(rec.get("rows"))}</td><td class="n">{_n(sel)}</td><td class="n">{_n(left)}</td>'
                    f'<td>{flags}</td></tr>')
    repos = ('<table><tr><th>Repo</th><th>State</th><th class="n">PRs mined</th><th class="n">PRs selected</th>'
             '<th class="n">Left</th><th>Health</th></tr>' + "".join(rows) + "</table>")
    frows = []
    for f in files:
        rel = f["key"]
        short = html.escape(rel.split("/", 1)[1])
        link = (f'<a href="/file/{quote(rel)}">{short}</a>'
                if "/logs/" in rel or "/health/" in rel or rel.endswith("run.json") else short)
        uri = uris.get(rel)
        frows.append(f'<tr><td>{link}</td><td class="n">{f["size"]:,}</td><td>{ago(f["updated"], now)}</td>'
                     f'<td class="mut">{html.escape(uri or "")}</td></tr>')
    ftable = ('<table><tr><th>File</th><th class="n">Bytes</th><th>Updated</th><th>Bucket</th></tr>'
              + "".join(frows) + "</table>")
    head = (f'<h1>{html.escape(run["pc"])} <span class="{cls}">{label}</span></h1>'
            f'<p class="mut">split {html.escape(str(st.get("split_id")))} · miner {html.escape(str(st.get("miner_version")))} '
            f'· last sync {ago(run["updated"], now)} ({html.escape(str(st.get("reason")))}) · '
            f'{_n(st.get("prs_done"))} PRs mined, {_n(st.get("prs_remaining"))} left in the current repo</p>')
    return page(f'{run["pc"]} · Riffle data', f"{head}<h2>Repos</h2>{repos}<h2>Files</h2>{ftable}", refresh=60)


def search_page(q: str, pc: str, regex: bool, pcs: list[str], hits: list[tuple[str, int, str]], note: str = "") -> str:
    opts = '<option value="">all PCs</option>' + "".join(
        f'<option{" selected" if p == pc else ""}>{html.escape(p)}</option>' for p in pcs)
    form = (f'<form action="/search"><input name="q" value="{html.escape(q)}" placeholder="text or regex" size="40">'
            f'<select name="pc">{opts}</select><label><input type="checkbox" name="regex" value="1"'
            f'{" checked" if regex else ""}> regex</label><button>Search</button></form>')
    out = "".join(f'{html.escape(f)}:{n}: {html.escape(line)}\n' for f, n, line in hits)
    body = f"<h1>Log search</h1>{form}"
    if note:
        body += f'<p class="warn">{html.escape(note)}</p>'
    body += (f"<pre>{out}</pre>" if hits else ('<p class="mut">No matches.</p>' if q else ""))
    return page("Log search · Riffle data", body)

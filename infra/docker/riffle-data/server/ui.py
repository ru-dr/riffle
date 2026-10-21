"""
ui.py — the page shell. Every page is the same frame (the site's hairline frame,
the Riffle lockup, one nav) around an empty container; static/app.js fills it from
the JSON endpoints. No inline scripts or styles, so the Content-Security-Policy can
stay strict, and data is only ever put on the page with textContent.
"""
from __future__ import annotations

import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "static", "lockup.svg"), encoding="utf-8") as _fh:
    LOCKUP = _fh.read()

NAV = (("overview", "/", "Overview"), ("data", "/data", "Data"), ("search", "/search", "Log search"))

BODIES = {
    "overview": """
<section class="rule intro"><div class="cap">Mining runs</div><h1>Mining <span class="of">progress</span></h1>
<p id="sub">Every PC that sends its output here, as of its last sync.</p></section>
<section class="rule grid g5" id="kpis"></section>
<section class="rule grid g2 stack">
 <div class="panel"><div class="cap">PRs mined</div><h3>By PC</h3><div class="chart" id="c-prs"></div></div>
 <div class="panel"><div class="cap">Repos</div><h3>By state, per PC</h3><div class="chart" id="c-repos"></div></div>
</section>
<section class="rule section"><div class="shead"><div class="cap">PCs</div><h2>Every machine</h2>
<p>PRs left counts the repo each PC is mining now. A repo that has not started has no PR selection yet, so it counts under repos waiting.</p></div></section>
<section><div class="tablewrap" id="pcs"></div></section>""",
    "run": """
<section class="rule intro"><div class="cap" id="r-cap">Run</div><h1 id="r-title">…</h1><p id="r-sub"></p></section>
<section class="rule grid g5" id="kpis"></section>
<section class="rule grid g2 stack">
 <div class="panel"><div class="cap">Per repo</div><h3>PRs mined per repo</h3><div class="chart" id="c-repos"></div></div>
 <div class="panel"><div class="cap">Health</div><h3>Flags per repo</h3><div class="chart" id="c-health"></div></div>
</section>
<section class="rule section"><div class="shead"><div class="cap">Repos</div><h2>State and health</h2></div></section>
<section><div class="tablewrap" id="repos"></div></section>
<section class="rule section"><div class="shead"><div class="cap">Files</div><h2>Everything this run sent</h2><p>Open a file to read it here. Datasets open as tables with charts.</p></div></section>
<section><div class="tablewrap" id="files"></div></section>""",
    "data": """
<section class="rule intro"><div class="cap">Mined tables</div><h1>Browse the <span class="of">data</span></h1>
<p>The three mined tables, joined over every PC's files. They stay three: one row per PR, per snapshot and per head commit.</p></section>
<section class="rule section"><div class="tools" id="pickers"></div></section>
<section id="explorer"></section>""",
    "view": """
<section class="rule intro"><div class="cap" id="v-cap">File</div><h1 id="v-title">…</h1><p id="v-sub"></p></section>
<section id="viewer"></section>""",
    "search": """
<section class="rule intro"><div class="cap">Logs and traces</div><h1>Search the <span class="of">logs</span></h1>
<p>Plain text or a regular expression, over every PC's run logs and traces.</p></section>
<section class="rule section"><form class="tools" id="sform"><input type="search" name="q" placeholder="text or regex" size="40" aria-label="Search text">
<select name="pc" aria-label="PC"></select><label class="cap"><input type="checkbox" name="regex"> regex</label><button class="btn solid">Search</button></form>
<div id="note" class="cap"></div></section>
<section id="hits"></section>""",
}


def shell(page: str, title: str, authed: bool = True) -> str:
    nav = "".join(f'<a href="{href}"{" aria-current=page" if key == page else ""}>{label}</a>' for key, href, label in NAV)
    right = ('<a class="btn" href="/logout">Sign out</a>' if authed else "")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">'
            f'<title>{html.escape(title)} · Riffle data</title><link rel="icon" type="image/svg+xml" href="/static/favicon.svg">'
            f'<link rel="stylesheet" href="/static/riffle.css"></head><body data-page="{page}"><div class="wrap">'
            f'<header class="top"><a class="brand" href="/" aria-label="Riffle data">{LOCKUP}<span class="tag">Data</span></a>'
            f'<nav class="main" aria-label="Main">{nav}</nav><div>{right}</div></header>'
            f'{BODIES.get(page, "")}<footer class="rule foot">Riffle team store · nothing here is on the scoring path</footer>'
            f'</div><script src="/static/charts.js"></script><script src="/static/app.js"></script></body></html>')


def login(error: str = "") -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">'
            f'<title>Sign in · Riffle data</title><link rel="icon" type="image/svg+xml" href="/static/favicon.svg">'
            f'<link rel="stylesheet" href="/static/riffle.css"></head><body><div class="login">{LOCKUP}'
            f'<div class="cap" style="margin-top:28px">Team store</div><h1 style="font:500 2rem/1.1 var(--sans);letter-spacing:-.035em;margin:10px 0 0">Sign in</h1>'
            f'<p style="color:var(--nickel)">Paste the read token the lead gave you.</p>'
            f'<form method="post" action="/login"><input type="password" name="token" autocomplete="current-password" '
            f'placeholder="rd_…" aria-label="Read token" required autofocus><button class="btn solid">Sign in</button></form>{err}</div></body></html>')

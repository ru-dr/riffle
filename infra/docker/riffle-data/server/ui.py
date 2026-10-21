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

NAV = (("overview", "/", "Overview"), ("data", "/data", "Data"), ("search", "/search", "Log search"), ("admin", "/admin", "Admin"))

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
<section><div id="pcs"></div></section>""",
    "run": """
<section class="rule intro"><div class="cap" id="r-cap">Run</div><h1 id="r-title">…</h1><p id="r-sub"></p></section>
<section class="rule grid g5" id="kpis"></section>
<section class="rule grid g2 stack">
 <div class="panel"><div class="cap">Per repo</div><h3>PRs mined per repo</h3><div class="chart" id="c-repos"></div></div>
 <div class="panel"><div class="cap">Health</div><h3>Flags per repo</h3><div class="chart" id="c-health"></div></div>
</section>
<section class="rule section"><div class="shead"><div class="cap">Repos</div><h2>State and health</h2></div></section>
<section><div id="repos"></div></section>
<section class="rule section"><div class="shead"><div class="cap">Files</div><h2>Everything this run sent</h2><p>Open a file to read it here. Datasets open as tables with charts.</p></div></section>
<section><div id="files"></div></section>""",
    "data": """
<section class="rule intro"><div class="cap">Mined tables</div><h1>Browse the <span class="of">data</span></h1>
<p>The three mined tables, joined over every PC's files. They stay three: one row per PR, per snapshot and per head commit.</p></section>
<section class="rule section"><div class="tools" id="pickers"></div></section>
<section id="explorer"></section>""",
    "view": """
<section class="rule intro"><div class="cap" id="v-cap">File</div><h1 id="v-title">…</h1><p id="v-sub"></p></section>
<section id="viewer"></section>""",
    "admin": """
<section class="rule intro"><div class="cap">Admin</div><h1>Who may <span class="of">upload</span></h1>
<p>The tokens behind every PC and every reader. Revoke one, rotate a whole split, or add a reader. This page cannot read mined data, and the read pages cannot change tokens.</p></section>
<section class="rule grid g5" id="kpis"></section>
<section class="rule section" id="newcodes" hidden><div class="shead"><div class="cap">Shown once</div><h2 id="nc-title">New join codes</h2>
<p id="nc-note"></p></div><div class="tools" id="nc-tools"></div></section>
<section id="nc-table" hidden><div id="nc-rows"></div></section>
<section class="rule section"><div class="shead"><div class="cap">Splits</div><h2>Rotate or revoke a whole split</h2>
<p>Rotating makes new tokens for every PC of the split and stops the old ones at once, then shows the new join codes here, once. New splits are planned from the miner's TUI (Lead tools, F3), because the plan needs the repos file.</p></div></section>
<section><div id="splits"></div></section>
<section class="rule section"><div class="shead"><div class="cap">PC tokens</div><h2>Every machine that may upload</h2></div></section>
<section><div id="pcs"></div></section>
<section class="rule section"><div class="shead"><div class="cap">Who can read</div><h2>Read tokens</h2>
<p>A read token signs in to the pages. Add one per person, and revoke it when they leave.</p></div>
<form class="tools" id="rform"><input type="text" name="name" placeholder="name, e.g. maria" pattern="[a-z0-9][a-z0-9_\\-]{0,39}" size="24" aria-label="Reader name" required><button class="btn solid">Add reader</button></form>
<div id="newreader" class="alert ok" hidden></div></section>
<section><div id="readers"></div></section>
<section class="rule section"><div class="shead"><div class="cap">Admins</div><h2>Admin tokens</h2><p>Made on the VM with <code>manage.py add-admin</code>. Listed here for reference.</p></div></section>
<section><div id="admins"></div></section>""",
    "search": """
<section class="rule intro"><div class="cap">Logs and traces</div><h1>Search the <span class="of">logs</span></h1>
<p>Plain text or a regular expression, over every PC's run logs and traces.</p></section>
<section class="rule section"><form class="toolbar" id="sform">
<div class="grp"><label class="cap" for="sq">Search for</label><input id="sq" type="search" name="q" placeholder="warn, rate limited, a repo…" size="38" autofocus></div>
<div class="grp"><label class="cap" for="spc">In</label><select id="spc" name="pc"></select></div>
<div class="grp"><span class="cap">Match</span><label class="check"><input type="checkbox" name="regex"> regular expression</label></div>
<div class="grp"><span class="cap">&nbsp;</span><button class="btn solid">Search</button></div></form>
<div id="note" class="cap" style="margin-top:12px"></div></section>
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


COPY = {
    "read": ('<section class="auth-copy"><div class="cap">Team store</div><h1>Sign in to the <span class="of">mined data</span></h1>'
             '<p>Progress for every PC, the tables they have mined, and their logs. Read access only: nothing here can start, '
             'stop or change a run.</p>'
             '<dl class="kv"><dt>Token</dt><dd>The read token starts with <code>rd_</code>. The lead makes one with '
             '<code>manage.py add-reader</code>, or on the Admin page.</dd><dt>Session</dt><dd>It stays in this browser for 30 days as a private '
             'cookie. Sign out clears it.</dd></dl></section>'),
    "admin": ('<section class="auth-copy"><div class="cap">Team store · lead only</div><h1>Sign in to <span class="of">admin</span></h1>'
              '<p>Revoke a PC, rotate a split, or add a reader. An admin token cannot read mined data, and a read token cannot sign in here.</p>'
              '<dl class="kv"><dt>Token</dt><dd>The admin token starts with <code>ad_</code>. It was made on the VM with '
              '<code>manage.py add-admin</code>.</dd><dt>Session</dt><dd>It lasts 8 hours in this browser, then asks again.</dd></dl></section>'),
}


def login(error: str = "", throttled: bool = False, admin: bool = False) -> str:
    """The sign-in page: the same frame as the others, a short explanation on the left and the
    form on the right (stacked on a phone). The error is announced to screen readers."""
    msg = ""
    if error:
        msg = (f'<div class="alert{" amber" if throttled else ""}" role="alert"><b>{"Slow down" if throttled else "Not signed in"}</b>'
               f'<span>{html.escape(error)}</span></div>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">'
            f'<title>{"Admin sign in" if admin else "Sign in"} · Riffle data</title><link rel="icon" type="image/svg+xml" href="/static/favicon.svg">'
            f'<link rel="stylesheet" href="/static/riffle.css"></head><body data-page="login"><div class="wrap">'
            f'<header class="top"><a class="brand" href="/" aria-label="Riffle data">{LOCKUP}<span class="tag">Data</span></a>'
            f'<span class="tag">Private</span></header>'
            f'<main class="rule auth grid g2 stack">'
            f'{COPY["admin" if admin else "read"]}'
            f'<section class="auth-form"><form method="post" action="{"/admin/login" if admin else "/login"}" id="loginform" novalidate>'
            f'<label class="cap" for="token">{"Admin" if admin else "Read"} token</label>'
            f'<div class="field"><input id="token" name="token" type="password" autocomplete="current-password" autocapitalize="off" '
            f'spellcheck="false" placeholder="{"ad_…" if admin else "rd_…"}" required autofocus{" aria-invalid=true aria-describedby=err" if error else ""}>'
            f'<button type="button" class="btn" id="reveal" aria-pressed="false" aria-controls="token">Show</button></div>'
            f'{msg.replace("<div ", "<div id=err ", 1) if msg else ""}'
            f'<button class="btn solid wide" id="go">Sign in <span aria-hidden="true">→</span></button>'
            f'<p class="fine">Failed attempts are limited per address.</p></form></section></main>'
            f'<footer class="rule foot">Riffle team store · nothing here is on the scoring path</footer>'
            f'</div><script src="/static/login.js"></script></body></html>')

"""
dashboard.py — read-only web view of the TUI, for other devices on your Wi-Fi.

tui.py starts this on launch and publishes a snapshot of what it shows (run
stats, RAM/CPU, the repo table, the log tail) once a second. Any phone or
laptop on the same network can open the printed URL; the page polls
/api/status every 2 s. Standard library only.

Read-only by design: nothing on the page can start, stop or change a run.
The URL carries a random key, new on every launch, so other people on the
network can't watch without it. It binds to all interfaces of this machine;
don't port-forward it to the internet.

    RIFFLE_DASHBOARD=0        disable
    RIFFLE_DASHBOARD_PORT=N   first port to try (default 8765)
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8765


def lan_ip() -> str:
    """This machine's address on the local network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Dashboard:
    def __init__(self, port: int | None = None):
        self.key = secrets.token_urlsafe(9)
        self._snapshot: dict = {"state": "starting"}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.port = port or int(os.getenv("RIFFLE_DASHBOARD_PORT", DEFAULT_PORT))
        self.host = lan_ip()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?key={self.key}"

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?key={self.key}"

    def publish(self, snapshot: dict) -> None:
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot

    def start(self) -> bool:
        """Bind the first free port from self.port (up to +20) and serve in a
        daemon thread. Returns False if no port could be bound."""
        handler = _make_handler(self)
        for port in range(self.port, self.port + 20):
            try:
                self._server = ThreadingHTTPServer(("0.0.0.0", port), handler)
                self.port = port
                break
            except OSError:
                continue
        else:
            return False
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="dashboard",
                         daemon=True).start()
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def _make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:      # keep the TUI's terminal clean
            pass

        def _authorized(self, qs: dict) -> bool:
            given = (qs.get("key") or [""])[0]
            return hmac.compare_digest(given.encode(), dash.key.encode())

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if not self._authorized(qs):
                self._send(403, b"Forbidden: open the full URL shown in the TUI "
                                b"(it includes ?key=...).\n", "text/plain; charset=utf-8")
                return
            if u.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif u.path == "/api/status":
                body = json.dumps(dash.snapshot(), default=str).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found\n", "text/plain; charset=utf-8")

    return Handler


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Riffle Miner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --sans:"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --bg:#fafafa;--panel:#ffffff;--panel-2:#f4f4f5;--border:#e4e4e7;--border-2:#d4d4d8;
  --text:#09090b;--muted:#71717a;--faint:#a1a1aa;
  --accent:#4f46e5;--accent-soft:#eef2ff;
  --ok:#16a34a;--ok-soft:#dcfce7;--warn:#ca8a04;--warn-soft:#fef9c3;--bad:#dc2626;--bad-soft:#fee2e2;
  --term:#0b0b0e;--term-text:#d4d4d8;--shadow:0 1px 2px rgba(0,0,0,.04),0 4px 16px rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#09090b;--panel:#111113;--panel-2:#18181b;--border:#232326;--border-2:#2e2e33;
  --text:#fafafa;--muted:#a1a1aa;--faint:#71717a;
  --accent:#818cf8;--accent-soft:rgba(129,140,248,.12);
  --ok:#4ade80;--ok-soft:rgba(74,222,128,.12);--warn:#facc15;--warn-soft:rgba(250,204,21,.12);
  --bad:#f87171;--bad-soft:rgba(248,113,113,.12);--term:#050506;--term-text:#d4d4d8;
  --shadow:0 1px 2px rgba(0,0,0,.4);
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 var(--sans);
  -webkit-font-smoothing:antialiased;font-feature-settings:"ss01","cv11"}
.mono,.num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.wrap{max-width:1200px;margin:0 auto;padding:0 20px}

/* header */
header{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 80%,transparent);
  backdrop-filter:saturate(1.4) blur(10px);-webkit-backdrop-filter:saturate(1.4) blur(10px);
  border-bottom:1px solid var(--border)}
header .wrap{display:flex;align-items:center;gap:12px;height:60px}
.logo{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;flex:none;
  background:linear-gradient(135deg,var(--accent),color-mix(in srgb,var(--accent) 55%,#06b6d4));
  color:#fff;font:600 14px var(--mono)}
.brand{display:flex;align-items:baseline;gap:8px;min-width:0}
.brand h1{font-size:15px;font-weight:600;margin:0;letter-spacing:-.01em;white-space:nowrap}
.chip{font:500 11.5px var(--mono);color:var(--muted);border:1px solid var(--border);
  padding:1px 7px;border-radius:6px;background:var(--panel)}
.spacer{flex:1}
.status{display:inline-flex;align-items:center;gap:7px;font-weight:500;font-size:13px;
  padding:4px 10px 4px 9px;border-radius:999px;border:1px solid var(--border);background:var(--panel);
  white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--faint);flex:none;position:relative}
.s-running .dot{background:var(--ok)}
.s-running .dot::after{content:"";position:absolute;inset:-4px;border-radius:50%;
  border:2px solid var(--ok);opacity:.6;animation:pulse 1.8s ease-out infinite}
.s-bad .dot{background:var(--bad)} .s-warn .dot{background:var(--warn)}
@keyframes pulse{from{transform:scale(.5);opacity:.7}to{transform:scale(1.4);opacity:0}}
@media (prefers-reduced-motion:reduce){.s-running .dot::after{animation:none}}
.updated{color:var(--faint);font-size:12px;white-space:nowrap}
.offline{display:none;color:var(--bad);background:var(--bad-soft);border-color:transparent}

main.wrap{padding-top:22px;padding-bottom:48px}
.section-title{display:flex;align-items:center;gap:10px;margin:28px 0 10px;
  font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.section-title .count{font:500 12px var(--mono);color:var(--faint);text-transform:none;letter-spacing:0}

.panel{background:var(--panel);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}

/* hero: overall progress */
.hero{padding:20px 22px;display:grid;grid-template-columns:auto 1fr;gap:6px 28px;align-items:end}
.hero .pct{font:600 44px/1 var(--mono);letter-spacing:-.04em}
.hero .pct small{font-size:20px;color:var(--muted);margin-left:2px}
.hero .sub{color:var(--muted);font-size:13px}
.track{height:8px;background:var(--panel-2);border-radius:999px;overflow:hidden;border:1px solid var(--border)}
.track>i{display:block;height:100%;width:0;border-radius:inherit;
  background:linear-gradient(90deg,var(--accent),color-mix(in srgb,var(--accent) 50%,#06b6d4));
  transition:width .8s cubic-bezier(.2,.8,.2,1)}
.hero .track{grid-column:1/-1;margin-top:14px}

/* KPI tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:12px}
.tile{padding:14px 16px}
.tile .k{font-size:12px;color:var(--muted);font-weight:500}
.tile .v{font:600 22px/1.25 var(--mono);letter-spacing:-.02em;margin-top:4px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.tile .h{font:12px var(--mono);color:var(--faint);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .track{height:5px;margin-top:10px}
.lvl-ok{color:var(--ok)} .lvl-warn{color:var(--warn)} .lvl-bad{color:var(--bad)}
.track.lvl-warn>i{background:var(--warn)} .track.lvl-bad>i{background:var(--bad)} .track.lvl-ok>i{background:var(--ok)}

/* filters */
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.filters button{font:500 12px var(--sans);color:var(--muted);background:var(--panel);
  border:1px solid var(--border);border-radius:999px;padding:3px 10px;cursor:pointer;text-transform:none;letter-spacing:0}
.filters button[aria-pressed="true"]{color:var(--text);border-color:var(--border-2);background:var(--panel-2)}
.filters .n{font-family:var(--mono);color:var(--faint);margin-left:4px}

/* repo list */
.repos{overflow:hidden}
.repo{display:grid;grid-template-columns:minmax(180px,1.3fr) minmax(150px,1.2fr) minmax(170px,1fr) minmax(0,1.8fr);
  gap:16px;align-items:center;padding:12px 18px;border-top:1px solid var(--border)}
.repo:first-child{border-top:0}
.repo .name{display:flex;align-items:center;gap:10px;min-width:0;font:500 13.5px var(--mono)}
.repo .name span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:500;
  padding:2px 9px;border-radius:999px;background:var(--panel-2);color:var(--muted);max-width:100%}
.badge span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st-active .badge{background:var(--accent-soft);color:var(--accent)}
.st-warn .badge{background:var(--warn-soft);color:var(--warn)}
.st-failed .badge{background:var(--bad-soft);color:var(--bad)}
.st-done .badge{background:var(--ok-soft);color:var(--ok)}
.st-active .dot{background:var(--accent)} .st-warn .dot{background:var(--warn)}
.st-failed .dot{background:var(--bad)} .st-done .dot{background:var(--ok)}
.prog{display:flex;align-items:center;gap:10px}
.prog .track{flex:1;height:6px}
.prog .num{font-size:12.5px;color:var(--muted);min-width:64px;text-align:right}
.meta{display:flex;flex-wrap:wrap;gap:4px 14px;font:12px var(--mono);color:var(--faint)}
.meta b{font-weight:500;color:var(--text)}
.meta .ok b{color:var(--ok)}
.repo.queued{padding-top:9px;padding-bottom:9px}
.repo.queued .prog,.repo.queued .meta{visibility:hidden}
.empty{padding:28px;text-align:center;color:var(--muted)}

/* log */
.term{background:var(--term);color:var(--term-text);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.term .bar{display:flex;align-items:center;gap:6px;padding:9px 14px;border-bottom:1px solid #1f1f23}
.term .bar i{width:10px;height:10px;border-radius:50%;background:#3f3f46}
.term .bar span{margin-left:8px;font:12px var(--mono);color:#71717a}
.term pre{margin:0;padding:12px 14px;max-height:380px;overflow:auto;font:12.5px/1.65 var(--mono);
  white-space:pre-wrap;word-break:break-word}
.term .l-warn{color:#facc15} .term .l-bad{color:#f87171} .term .l-ok{color:#4ade80}
.paths{margin-top:14px;font:12px var(--mono);color:var(--faint);word-break:break-all}
.paths div{margin-top:2px}

/* responsive */
@media (max-width:980px){
  .repo{grid-template-columns:1fr 1fr;gap:8px 16px}
  .repo .meta{grid-column:1/-1}
}
@media (max-width:640px){
  .wrap{padding:0 14px}
  header .wrap{height:54px;gap:8px}.updated{display:none}
  .hero{grid-template-columns:1fr;padding:16px}.hero .pct{font-size:36px}
  .tiles{grid-template-columns:1fr 1fr;gap:10px}.tile{padding:12px 13px}.tile .v{font-size:18px}
  .tiles>.tile:last-child:nth-child(odd){grid-column:1/-1}
  .repo{grid-template-columns:1fr;padding:12px 14px}
  .repo.queued{grid-template-columns:1fr auto}
  .repo.queued .prog,.repo.queued .meta{display:none}
  .section-title{flex-wrap:wrap}.filters{margin-left:0;width:100%}
}
</style></head><body>
<header><div class="wrap">
  <div class="logo">R</div>
  <div class="brand"><h1>Riffle miner</h1><span class="chip" id="ver">v—</span></div>
  <div class="spacer"></div>
  <span class="updated" id="updated"></span>
  <span class="status offline" id="stale">offline</span>
  <span class="status" id="state"><span class="dot"></span><span id="state_t">connecting</span></span>
</div></header>

<main class="wrap">
  <section class="panel hero">
    <div class="pct num" id="pct">0<small>%</small></div>
    <div><div class="sub">PRs mined</div><div class="num" style="font-size:18px;font-weight:600" id="prs">—</div></div>
    <div class="track"><i id="pbar"></i></div>
  </section>

  <div class="tiles">
    <div class="panel tile"><div class="k">Elapsed</div><div class="v" id="elapsed">—</div></div>
    <div class="panel tile"><div class="k">ETA</div><div class="v" id="eta">—</div><div class="h" id="rate">—</div></div>
    <div class="panel tile"><div class="k">Repos</div><div class="v" id="repos">—</div></div>
    <div class="panel tile"><div class="k">LLM ok / fail</div><div class="v" id="llm">—</div><div class="h" id="lat"></div></div>
    <div class="panel tile"><div class="k">Breaker</div><div class="v" id="breaker">—</div></div>
  </div>

  <div class="section-title">System</div>
  <div class="tiles" style="margin-top:0">
    <div class="panel tile"><div class="k">Miner RAM</div><div class="v" id="ram">—</div>
      <div class="h" id="peak"></div><div class="track" id="ram_t"><i></i></div></div>
    <div class="panel tile"><div class="k">System memory used</div><div class="v" id="free">—</div>
      <div class="h" id="free_h"></div><div class="track" id="used_t"><i></i></div></div>
    <div class="panel tile"><div class="k">CPU</div><div class="v" id="cpu">—</div>
      <div class="h" id="cpu_h"></div><div class="track" id="cpu_t"><i></i></div></div>
  </div>

  <div class="section-title">Repositories <span class="count" id="rcount"></span>
    <div class="filters" id="filters"></div></div>
  <section class="panel repos" id="rows"><div class="empty">No run yet.</div></section>

  <div class="section-title">Log</div>
  <section class="term"><div class="bar"><i></i><i></i><i></i><span id="logname">miner.log</span></div>
    <pre id="log"></pre></section>
  <div class="paths" id="paths"></div>
</main>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const FILTERS = [["all","All"],["active","Active"],["warn","Attention"],["failed","Failed"],["done","Done"],["queued","Queued"]];
let filter = "all", last = null, lastOk = 0;

function setText(id, v){ $(id).textContent = (v === null || v === undefined || v === "") ? "—" : v; }
function setLvl(id, lvl){ const e=$(id); e.classList.remove("lvl-ok","lvl-warn","lvl-bad"); if (lvl) e.classList.add("lvl-"+lvl); }
function meter(id, frac, lvl){ const t=$(id); t.firstElementChild.style.width = Math.max(0, Math.min(1, frac||0))*100 + "%";
  t.classList.remove("lvl-ok","lvl-warn","lvl-bad"); if (lvl) t.classList.add("lvl-"+lvl); }

function renderFilters(rows){
  const counts = {all: rows.length}; for (const r of rows) counts[r.status] = (counts[r.status]||0) + 1;
  const box = $("filters"); box.replaceChildren();
  for (const [k, label] of FILTERS) {
    if (k !== "all" && !counts[k]) continue;
    const b = el("button", null, label); b.setAttribute("aria-pressed", String(filter === k));
    b.appendChild(el("span", "n", String(counts[k]||0)));
    b.onclick = () => { filter = k; if (last) render(last); };
    box.appendChild(b);
  }
  if (filter !== "all" && !counts[filter]) filter = "all";
}

function repoRow(r){
  const row = el("div", "repo st-" + r.status + (r.status === "queued" ? " queued" : ""));
  const name = el("div", "name"); name.appendChild(el("span", "dot")); name.appendChild(el("span", null, r.name));
  const st = el("div"); const badge = el("span", "badge"); badge.appendChild(el("span", null, r.stage)); st.appendChild(badge);
  const prog = el("div", "prog"); const tr = el("div", "track"); const fill = el("i");
  fill.style.width = (r.total ? Math.min(1, r.done / r.total) : 0) * 100 + "%"; tr.appendChild(fill);
  prog.appendChild(tr); prog.appendChild(el("span", "num", r.prs));
  const meta = el("div", "meta");
  for (const [label, v, cls] of [["SZZ", r.szz, r.szz_level === "ok" ? "ok" : ""], ["ETA", r.eta], ["time", r.time],
      ["rows", r.rows], ["skips", r.skips], ["LLM", r.llm], ["slowest", r.slowest]]) {
    if (v === "—" && label !== "ETA") continue;
    const m = el("span", cls); m.appendChild(document.createTextNode(label + " ")); m.appendChild(el("b", null, v)); meta.appendChild(m);
  }
  row.append(name, st, prog, meta);
  return row;
}

function render(s){
  last = s;
  setText("ver", s.version ? "v" + s.version : "");
  const state = s.state || "idle";
  const sc = state === "running" ? "s-running" : /killed|exit|breaker/.test(state) ? "s-bad" : state === "stopped" ? "s-warn" : "";
  $("state").className = "status " + sc; setText("state_t", state);
  const p = s.progress || 0;
  $("pct").firstChild.nodeValue = (p * 100).toFixed(p < 0.1 ? 1 : 0);
  $("pbar").style.width = p * 100 + "%";
  setText("prs", s.prs); setText("elapsed", s.elapsed); setText("eta", s.eta); setText("rate", s.rate);
  setText("repos", s.repos); setText("llm", s.llm); $("lat").textContent = s.llm_latency ? "avg " + s.llm_latency : "";
  setText("breaker", s.breaker ? "tripped" : (s.state ? "armed" : null)); setLvl("breaker", s.breaker ? "bad" : "ok");
  const r = s.resources || {};
  setText("ram", r.rss); $("peak").textContent = r.peak ? "peak " + r.peak : ""; meter("ram_t", r.rss_frac, r.level);
  setText("free", r.used_frac != null ? Math.round(r.used_frac * 100) + "%" : null); setLvl("free", r.level);
  $("free_h").textContent = r.free ? r.free + " free of " + r.total : ""; meter("used_t", r.used_frac, r.level);
  setText("cpu", r.cpu ? r.cpu.split(" ")[0] : null); $("cpu_h").textContent = r.cpu ? "of " + r.cpu.split(" of ")[1] : "";
  meter("cpu_t", r.cpu_frac);
  const rows = s.rows || [];
  renderFilters(rows);
  $("rcount").textContent = rows.length ? String(rows.length) : "";
  const box = $("rows"); box.replaceChildren();
  const shown = rows.filter(x => filter === "all" || x.status === filter);
  if (!shown.length) box.appendChild(el("div", "empty", rows.length ? "Nothing here." : "No run yet."));
  for (const x of shown) box.appendChild(repoRow(x));
  const pre = $("log"); const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  pre.replaceChildren();
  for (const l of (s.log || [])) pre.appendChild(el("div", l.level ? "l-" + l.level : "", l.text));
  if (atEnd) pre.scrollTop = pre.scrollHeight;
  if (s.log_path) $("logname").textContent = s.log_path.split("/").pop();
  const paths = $("paths"); paths.replaceChildren();
  if (s.out) paths.appendChild(el("div", null, "output  " + s.out));
  if (s.log_path) paths.appendChild(el("div", null, "log     " + s.log_path));
  document.title = (state === "running" ? (p * 100).toFixed(0) + "% · " : "") + "Riffle Miner";
}
function ago(){ if (!lastOk) return; const s = Math.round((Date.now() - lastOk) / 1000);
  $("updated").textContent = s < 3 ? "live" : "updated " + s + "s ago"; }
async function tick(){
  try {
    const res = await fetch("/api/status?key=" + encodeURIComponent(key), {cache: "no-store"});
    if (!res.ok) throw new Error(res.status);
    render(await res.json()); lastOk = Date.now(); $("stale").style.display = "none";
  } catch (e) { $("stale").style.display = "inline-flex"; }
  ago();
}
tick(); setInterval(tick, 2000); setInterval(ago, 1000);
</script></body></html>
"""

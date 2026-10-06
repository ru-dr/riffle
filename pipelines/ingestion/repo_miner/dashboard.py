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


# Riffle brand assets, copied from site/public/brand/lockup-black.svg (fill
# set to currentColor so it follows the theme) and site/public/favicon.svg.
LOCKUP_SVG = r"""<svg class="lockup" role="img" aria-label="Riffle" viewBox="0 0 934 164" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M125.321 0.279359C125.661 0.251859 126.001 0.233858 126.342 0.225358C135.69 0.0278585 145.067 0.167861 154.419 0.141611C160.692 0.124111 167.153 -0.202891 173.407 0.200609C174.27 0.256359 175.826 0.258362 176.477 0.913362C177.049 1.48911 177.401 2.50161 177.35 3.31061C177.184 5.93986 168.864 12.3554 166.742 14.3206C163.456 17.3626 145.998 33.5054 143.458 34.5949C140.316 35.9424 133.896 35.2936 130.355 35.2716L111.651 35.2589C108.955 35.2354 98.7798 34.6211 96.0908 36.1509C94.5468 37.0294 95.7328 39.8296 97.1823 41.1469C104.109 47.4409 111.122 53.6736 118.058 59.9959L169.712 107.154C174.937 111.985 180.521 116.54 185.628 121.528C191.189 126.96 188.133 128.82 181.784 128.744L150.981 128.758C148.334 128.753 135.956 129.153 134.262 127.976C130.868 125.618 125.855 120.763 122.65 117.856L97.0985 94.5806L52.8405 54.3121C48.1643 50.0279 43.3848 45.8561 38.651 41.6396C36.8083 39.9981 35.9123 38.0276 37.3513 35.7851C38.1808 35.2581 40.2353 35.0336 41.2188 35.0224C55.6815 34.8569 70.237 35.2694 84.692 34.8741C86.7295 33.8244 95.0975 25.7429 97.1493 23.8684C102.356 19.2056 107.56 14.4104 112.832 9.83361C116.043 7.04611 121.237 1.00936 125.321 0.279359Z" fill="currentColor"/>
<path d="M177.732 35.0182C179.867 34.8242 183.407 34.9542 185.663 34.9612L199.603 34.9637L216.562 34.9557C220.218 34.9557 224.725 34.8247 228.288 35.369C230.813 35.7545 235.001 40.4595 237.051 42.2982L257.528 60.8652L301.46 100.853C308.79 107.553 316.25 114.225 323.61 120.899C324.145 121.384 325.33 122.4 325.73 122.91C332.075 131 317.715 128.753 313.938 128.744L290.895 128.698C288.815 128.695 280.643 128.504 279.108 128.884C276.663 129.489 267.71 138.272 265.548 140.248L248.742 155.402C246.722 157.235 242.365 161.398 240.416 162.678C238.073 163.988 227.98 163.544 224.899 163.541L203.964 163.575C201.07 163.57 189.465 164.051 187.421 162.998C186.373 161.958 186.028 159.459 187.162 158.294C192.704 152.595 196.16 149.829 202.405 144.251C206.616 140.49 215.352 132.21 220.33 128.668C231.668 127.977 243.448 128.624 254.877 128.424C258.698 128.357 262.548 128.703 266.305 127.991C268.158 127.633 269.063 125.396 267.903 124.202C261.44 117.538 253.237 110.632 246.464 104.447C233.619 92.6177 220.711 80.857 207.74 69.1657L189.261 52.3775C185.353 48.8492 181.433 45.3655 177.577 41.7775C175.284 39.6435 173.258 36.05 177.732 35.0182Z" fill="currentColor"/>
<path d="M450.688 150L426.304 96.2397H406.528V150H387.328V15.5997H437.248C456.448 15.5997 471.808 30.9597 471.808 50.1597V61.6797C471.808 77.6157 461.248 90.8637 446.656 94.8957L471.808 150H450.688ZM406.528 77.0397H437.248C445.888 77.0397 452.608 70.3197 452.608 61.6797V50.1597C452.608 41.5197 445.888 34.7997 437.248 34.7997H406.528V77.0397Z" fill="currentColor"/>
<path d="M486.417 150V130.8H518.097V71.2797H486.417V52.0797H537.297V130.8H568.977V150H486.417ZM515.217 34.7997V13.6797H537.297V34.7997H515.217Z" fill="currentColor"/>
<path d="M589.099 48.2397C589.099 29.0397 604.459 13.6797 623.659 13.6797H641.899V32.8797H623.659C615.019 32.8797 608.299 39.5997 608.299 48.2397V57.8397H641.899V77.0397H608.299V150H589.099V77.0397H563.179V57.8397H589.099V48.2397Z" fill="currentColor"/>
<path d="M673.662 48.2397C673.662 29.0397 689.022 13.6797 708.222 13.6797H726.462V32.8797H708.222C699.582 32.8797 692.862 39.5997 692.862 48.2397V57.8397H726.462V77.0397H692.862V150H673.662V77.0397H647.742V57.8397H673.662V48.2397Z" fill="currentColor"/>
<path d="M721.447 150V130.8H753.127V32.8797H721.447V13.6797H772.327V130.8H805.927V150H721.447Z" fill="currentColor"/>
<path d="M847.441 150C828.433 150 812.881 134.256 812.881 115.248V86.6397C812.881 67.4397 828.433 52.0797 847.441 52.0797H862.801C881.425 52.0797 896.401 66.4797 897.361 84.7197V105.456H832.081V115.248C832.081 123.696 838.993 130.608 847.441 130.608H862.801C871.249 130.608 878.161 123.696 878.161 115.248V113.328H897.361V115.248C897.361 134.256 882.001 150 862.801 150H847.441ZM832.081 90.0957H878.161V86.6397C878.161 77.9997 871.441 71.2797 862.801 71.2797H847.441C838.801 71.2797 832.081 78.1917 832.081 86.6397V90.0957Z" fill="currentColor"/>
</svg>"""
FAVICON_B64 = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0MDAgNDAwIj4KICA8Y2lyY2xlIGN4PSIyMDAiIGN5PSIyMDAiIHI9IjIwMCIgZmlsbD0iIzE2MTcxZCIvPgogIDxnIGZpbGw9IiNmYmZhZjciIHRyYW5zZm9ybT0idHJhbnNsYXRlKDQ4LjAwIDEzMC43Nikgc2NhbGUoMC44NDQ0KSI+PHBhdGggZD0iTTEyMC4zMjEgMC4yNzkzNTlDMTIwLjY2MSAwLjI1MTg1OSAxMjEuMDAxIDAuMjMzODU4IDEyMS4zNDIgMC4yMjUzNThDMTMwLjY5IDAuMDI3ODU4NSAxNDAuMDY3IDAuMTY3ODYxIDE0OS40MTkgMC4xNDE2MTFDMTU1LjY5MiAwLjEyNDExMSAxNjIuMTUzIC0wLjIwMjg5MSAxNjguNDA3IDAuMjAwNjA5QzE2OS4yNyAwLjI1NjM1OSAxNzAuODI2IDAuMjU4MzYyIDE3MS40NzcgMC45MTMzNjJDMTcyLjA0OSAxLjQ4OTExIDE3Mi40MDEgMi41MDE2MSAxNzIuMzUgMy4zMTA2MUMxNzIuMTg0IDUuOTM5ODYgMTYzLjg2NCAxMi4zNTU0IDE2MS43NDIgMTQuMzIwNkMxNTguNDU2IDE3LjM2MjYgMTQwLjk5OCAzMy41MDU0IDEzOC40NTggMzQuNTk0OUMxMzUuMzE2IDM1Ljk0MjQgMTI4Ljg5NiAzNS4yOTM2IDEyNS4zNTUgMzUuMjcxNkwxMDYuNjUxIDM1LjI1ODlDMTAzLjk1NSAzNS4yMzU0IDkzLjc3OTggMzQuNjIxMSA5MS4wOTA4IDM2LjE1MDlDODkuNTQ2OCAzNy4wMjk0IDkwLjczMjggMzkuODI5NiA5Mi4xODIzIDQxLjE0NjlDOTkuMTA4NSA0Ny40NDA5IDEwNi4xMjIgNTMuNjczNiAxMTMuMDU4IDU5Ljk5NTlMMTY0LjcxMiAxMDcuMTU0QzE2OS45MzcgMTExLjk4NSAxNzUuNTIxIDExNi41NCAxODAuNjI4IDEyMS41MjhDMTg2LjE4OSAxMjYuOTYgMTgzLjEzMyAxMjguODIgMTc2Ljc4NCAxMjguNzQ0TDE0NS45ODEgMTI4Ljc1OEMxNDMuMzM0IDEyOC43NTMgMTMwLjk1NiAxMjkuMTUzIDEyOS4yNjIgMTI3Ljk3NkMxMjUuODY4IDEyNS42MTggMTIwLjg1NSAxMjAuNzYzIDExNy42NSAxMTcuODU2TDkyLjA5ODUgOTQuNTgwNkw0Ny44NDA1IDU0LjMxMjFDNDMuMTY0MyA1MC4wMjc5IDM4LjM4NDggNDUuODU2MSAzMy42NTEgNDEuNjM5NkMzMS44MDgzIDM5Ljk5ODEgMzAuOTEyMyAzOC4wMjc2IDMyLjM1MTMgMzUuNzg1MUMzMy4xODA4IDM1LjI1ODEgMzUuMjM1MyAzNS4wMzM2IDM2LjIxODggMzUuMDIyNEM1MC42ODE1IDM0Ljg1NjkgNjUuMjM3IDM1LjI2OTQgNzkuNjkyIDM0Ljg3NDFDODEuNzI5NSAzMy44MjQ0IDkwLjA5NzUgMjUuNzQyOSA5Mi4xNDkzIDIzLjg2ODRDOTcuMzU2MyAxOS4yMDU2IDEwMi41NiAxNC40MTA0IDEwNy44MzIgOS44MzM2MUMxMTEuMDQzIDcuMDQ2MTEgMTE2LjIzNyAxLjAwOTM2IDEyMC4zMjEgMC4yNzkzNTlaIi8+PHBhdGggZD0iTTE3Mi43MzIgMzUuMDE4MkMxNzQuODY3IDM0LjgyNDIgMTc4LjQwNyAzNC45NTQyIDE4MC42NjMgMzQuOTYxMkwxOTQuNjAzIDM0Ljk2MzdMMjExLjU2MiAzNC45NTU3QzIxNS4yMTggMzQuOTU1NyAyMTkuNzI1IDM0LjgyNDcgMjIzLjI4OCAzNS4zNjlDMjI1LjgxMyAzNS43NTQ1IDIzMC4wMDEgNDAuNDU5NSAyMzIuMDUxIDQyLjI5ODJMMjUyLjUyOCA2MC44NjUyTDI5Ni40NiAxMDAuODUzQzMwMy43OSAxMDcuNTUzIDMxMS4yNSAxMTQuMjI1IDMxOC42MSAxMjAuODk5QzMxOS4xNDUgMTIxLjM4NCAzMjAuMzMgMTIyLjQgMzIwLjczIDEyMi45MUMzMjcuMDc1IDEzMSAzMTIuNzE1IDEyOC43NTMgMzA4LjkzOCAxMjguNzQ0TDI4NS44OTUgMTI4LjY5OEMyODMuODE1IDEyOC42OTUgMjc1LjY0MyAxMjguNTA0IDI3NC4xMDggMTI4Ljg4NEMyNzEuNjYzIDEyOS40ODkgMjYyLjcxIDEzOC4yNzIgMjYwLjU0OCAxNDAuMjQ4TDI0My43NDIgMTU1LjQwMkMyNDEuNzIyIDE1Ny4yMzUgMjM3LjM2NSAxNjEuMzk4IDIzNS40MTYgMTYyLjY3OEMyMzMuMDczIDE2My45ODggMjIyLjk4IDE2My41NDQgMjE5Ljg5OSAxNjMuNTQxTDE5OC45NjQgMTYzLjU3NUMxOTYuMDcgMTYzLjU3IDE4NC40NjUgMTY0LjA1MSAxODIuNDIxIDE2Mi45OThDMTgxLjM3MyAxNjEuOTU4IDE4MS4wMjggMTU5LjQ1OSAxODIuMTYyIDE1OC4yOTRDMTg3LjcwNCAxNTIuNTk1IDE5MS4xNiAxNDkuODI5IDE5Ny40MDUgMTQ0LjI1MUMyMDEuNjE2IDE0MC40OSAyMTAuMzUyIDEzMi4yMSAyMTUuMzMgMTI4LjY2OEMyMjYuNjY4IDEyNy45NzcgMjM4LjQ0OCAxMjguNjI0IDI0OS44NzcgMTI4LjQyNEMyNTMuNjk4IDEyOC4zNTcgMjU3LjU0OCAxMjguNzAzIDI2MS4zMDUgMTI3Ljk5MUMyNjMuMTU4IDEyNy42MzMgMjY0LjA2MyAxMjUuMzk2IDI2Mi45MDMgMTI0LjIwMkMyNTYuNDQgMTE3LjUzOCAyNDguMjM3IDExMC42MzIgMjQxLjQ2NCAxMDQuNDQ3QzIyOC42MTkgOTIuNjE3NyAyMTUuNzExIDgwLjg1NyAyMDIuNzQgNjkuMTY1N0wxODQuMjYxIDUyLjM3NzVDMTgwLjM1MyA0OC44NDkyIDE3Ni40MzMgNDUuMzY1NSAxNzIuNTc3IDQxLjc3NzVDMTcwLjI4NCAzOS42NDM1IDE2OC4yNTggMzYuMDUgMTcyLjczMiAzNS4wMTgyWiIvPjwvZz4KPC9zdmc+Cg=="

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Riffle Miner</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,@@FAVICON@@">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* Built to the site's own rules (site/app/globals.css, site/app/docs/page.tsx,
   site/components/docs/status.tsx): square hairline grids inside a framed
   wrapper, Geist medium headings with tight tracking, 11px mono captions,
   and tinted 4px status tags. No cards, no pills, no shadows but the chip. */
:root{
  --sans:"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --bg:#fbfaf7;--surface:#ffffff;--wash:rgba(22,23,29,.045);--stroke:#e5e4e7;
  --ink:#16171d;--nickel:#3b3440;--grey:#867e8e;--accent:#0f7a4f;--live:#00b442;
  --live-soft:rgba(0,180,66,.1);
  --t-green:#16a34a;--t-green-bg:rgba(22,163,74,.12);--t-amber:#d97706;--t-amber-bg:rgba(217,119,6,.12);
  --t-red:#dc2626;--t-red-bg:rgba(220,38,38,.1);--t-blue:#3b82f6;--t-blue-bg:rgba(59,130,246,.12);
  --code-bg:#16171d;--code-ink:#f2f1f4;--code-grey:rgba(242,241,244,.48);--code-stroke:#2c2d34;
  --chip-shadow:0 2px 4px 0 rgba(0,0,0,.05);--chip-outline:1px solid rgba(59,52,64,.06);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#16171d;--surface:#1e1f25;--wash:rgba(255,255,255,.055);--stroke:#2c2d34;
  --ink:#f2f1f4;--nickel:rgba(242,241,244,.72);--grey:rgba(242,241,244,.48);--accent:#7dd3a0;
  --code-bg:#1a1b20;--code-stroke:rgba(255,255,255,.08);
  --chip-shadow:none;--chip-outline:1px solid #2c2d34;
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
::selection{background:rgba(15,122,79,.18)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.cap{font:400 11px/1.4 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--grey)}

/* frame: the wrapper's side borders are the page's vertical rules */
.wrap{margin-inline:auto;position:relative;min-height:100vh}
@media (min-width:48rem){.wrap{max-width:calc(100vw - 2rem);border-left:1px solid var(--stroke);border-right:1px solid var(--stroke)}}
@media (min-width:48rem) and (max-width:79.999rem){.wrap{max-width:91vw}}
@media (min-width:103.5rem){.wrap{max-width:103.5rem}}
/* every horizontal rule ends on the frame with a small inward tick */
.rule{position:relative;border-top:1px solid var(--stroke)}
.rule::before,.rule::after{content:"";position:absolute;top:-5px;width:0;height:0;
  border-top:5px solid transparent;border-bottom:5px solid transparent}
.rule::before{left:0;border-left:5px solid var(--stroke)}
.rule::after{right:0;border-right:5px solid var(--stroke)}

/* header */
header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:20px 24px}
@media (min-width:64rem){header{padding:28px 40px}}
.brand{display:flex;align-items:center;gap:12px;color:var(--ink);text-decoration:none;min-width:0}
.lockup{display:block;height:22px;width:auto;color:var(--ink)}
.tag{display:inline-flex;align-items:center;border-radius:4px;padding:2px 6px;
  font:400 11px var(--mono);letter-spacing:.04em;text-transform:uppercase;
  outline:1px solid var(--stroke);color:var(--grey);white-space:nowrap}
.tag.ver{text-transform:none;letter-spacing:0}
.hright{display:flex;align-items:center;gap:12px}
.chip{display:inline-flex;align-items:center;gap:6px;border-radius:4px;padding:4px 12px;
  font:500 13px var(--mono);letter-spacing:-.03em;background:var(--surface);
  box-shadow:var(--chip-shadow);outline:var(--chip-outline);color:var(--grey);white-space:nowrap}
.chip b{font-weight:500;color:var(--ink)}
.sq{display:inline-flex;border-radius:2px;padding:2px;background:var(--wash)}
.sq i{display:block;width:6px;height:6px;border-radius:1.1px;background:var(--grey)}
.live .sq{background:var(--live-soft)} .live .sq i{background:var(--live);animation:rf-pulse 2.8s ease-in-out infinite}
.stopped .sq{background:var(--t-amber-bg)} .stopped .sq i{background:var(--t-amber)}
.dead .sq{background:var(--t-red-bg)} .dead .sq i{background:var(--t-red)}
@keyframes rf-pulse{50%{opacity:.3}}
@media (prefers-reduced-motion:reduce){.live .sq i{animation:none}}
#updated{font:12px var(--mono);color:var(--grey)}
#stale{display:none}

/* intro: the docs page's lead block */
.intro{padding:48px 24px 40px}
@media (min-width:48rem){.intro{padding:64px 40px 44px}}
.intro h1{margin:20px 0 0;font:500 2.25rem/1.08 var(--sans);letter-spacing:-.035em}
@media (min-width:48rem){.intro h1{font-size:3rem}}
.intro h1 .of{color:var(--grey)}
.intro p{margin:20px 0 0;max-width:40rem;font:17px/1.6 var(--sans);color:var(--nickel)}
.line{margin-top:32px;height:2px;background:var(--stroke);position:relative;overflow:hidden}
.line i{position:absolute;inset:0 auto 0 0;width:0;background:var(--ink);transition:width .8s cubic-bezier(.2,.8,.2,1)}

/* stat strips: one-pixel gaps over the stroke colour give exact hairlines */
.grid{display:grid;gap:1px;background:var(--stroke)}
.grid>*{background:var(--bg)}
.g5{grid-template-columns:repeat(5,1fr)} .g3{grid-template-columns:repeat(3,1fr)}
.stat{display:flex;flex-direction:column;gap:6px;padding:24px;min-width:0}
@media (min-width:48rem){.stat{padding:24px 40px}}
.stat .v{font:500 16px/1.3 var(--sans);letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat .v.num{font:500 22px/1.2 var(--sans);letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.stat .h{font:14px/1.45 var(--sans);color:var(--nickel)}
.meter{margin-top:6px;height:2px;background:var(--stroke);position:relative;overflow:hidden}
.meter i{position:absolute;inset:0 auto 0 0;width:0;background:var(--ink);transition:width .6s ease}
.meter.warn i{background:var(--t-amber)} .meter.bad i{background:var(--t-red)}
.v.warn{color:var(--t-amber)} .v.bad{color:var(--t-red)} .v.good{color:var(--accent)}

/* repositories: the docs section card, with hairline rows */
.section{padding:32px 24px 8px}
@media (min-width:48rem){.section{padding:32px 40px 8px}}
.shead{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}
.shead h2{margin:14px 0 0;font:500 1.35rem/1.2 var(--sans);letter-spacing:-.02em}
.shead p{margin:8px 0 0;font:14px/1.55 var(--sans);color:var(--nickel)}
.tabs{display:flex;flex-wrap:wrap;gap:4px;margin-top:22px}
.tabs button{appearance:none;cursor:pointer;border:0;background:transparent;border-radius:6px;
  padding:5px 10px;font:14px var(--sans);color:var(--nickel);outline:1px solid transparent;transition:background .15s}
.tabs button:hover{background:var(--wash)}
.tabs button[aria-pressed="true"]{background:var(--surface);color:var(--ink);outline-color:var(--stroke)}
.tabs .n{font:11px var(--mono);color:var(--grey);margin-left:6px}
.list{margin:20px 0 0;padding:0;list-style:none;border-top:1px solid var(--stroke)}
.row{display:grid;grid-template-columns:minmax(0,1.2fr) 96px minmax(0,1.3fr) 200px minmax(0,2fr);
  gap:24px;align-items:center;padding:11px 0;border-bottom:1px solid var(--stroke)}
.row .nm{font:14.5px var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .stage{font:14px var(--sans);color:var(--nickel);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .prog{display:flex;align-items:center;gap:12px;min-width:0}
.row .prog .meter{flex:1;margin:0}
.row .prog span{font:12px var(--mono);color:var(--grey);min-width:56px;text-align:right}
.row .facts{display:flex;flex-wrap:wrap;gap:2px 16px;font:12px var(--mono);color:var(--grey);min-width:0}
.row .facts b{font-weight:400;color:var(--ink)}
.st{display:inline-flex;align-items:center;justify-self:start;border-radius:4px;padding:2px 6px;
  font:400 10.5px var(--mono);letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}
.st-active{color:var(--t-blue);background:var(--t-blue-bg)}
.st-warn{color:var(--t-amber);background:var(--t-amber-bg)}
.st-failed{color:var(--t-red);background:var(--t-red-bg)}
.st-done{color:var(--t-green);background:var(--t-green-bg)}
.st-queued{color:var(--nickel);background:var(--wash)}
.row.q .prog,.row.q .facts,.row.q .stage{visibility:hidden}
.row.q .nm{color:var(--nickel)}
.empty{padding:28px 0;color:var(--grey);font-size:14px}

/* log: the site's code panel */
.code{margin-top:20px;background:var(--code-bg);border:1px solid var(--code-stroke);border-radius:4px;overflow:hidden}
.code .bar{display:flex;justify-content:space-between;gap:12px;padding:10px 16px;border-bottom:1px solid var(--code-stroke);
  font:12px var(--mono);color:var(--code-grey)}
.code pre{margin:0;padding:14px 16px;max-height:400px;overflow:auto;color:var(--code-ink);
  font:12.5px/1.75 var(--mono);white-space:pre-wrap;word-break:break-word}
.code .w{color:#e3b341} .code .b{color:#f47174} .code .g{color:#7dd3a0}
.paths{margin:16px 0 0;font:12px/1.7 var(--mono);color:var(--grey);word-break:break-all}
footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:20px 24px;margin-top:32px}
@media (min-width:48rem){footer{padding:20px 40px}}

/* responsive */
@media (max-width:1100px){
  .g5{grid-template-columns:repeat(3,1fr)}
  .g5>.stat:last-child{grid-column:span 2}
  .row{grid-template-columns:minmax(0,1fr) auto;gap:6px 16px}
  .row .stage{grid-column:1/-1;order:3}.row .prog{grid-column:1/-1;order:4}.row .facts{grid-column:1/-1;order:5}
  .row .st{order:2;justify-self:end}
  .row.q .stage,.row.q .prog,.row.q .facts{display:none}
}
@media (max-width:640px){
  .g5,.g3{grid-template-columns:1fr 1fr}
  .g5>.stat:last-child,.g3>.stat:last-child:nth-child(odd){grid-column:1/-1}
  .stat{padding:18px 24px}
  .intro h1{font-size:2rem}.intro p{font-size:16px}
  #updated,.tag.ver{display:none}
  .lockup{height:19px}
}
</style></head><body>
<div class="wrap">
<header>
  <a class="brand" href="#" aria-label="Riffle miner">@@LOCKUP@@<span class="tag">Miner</span><span class="tag ver" id="ver">v—</span></a>
  <div class="hright">
    <span id="updated"></span>
    <span class="chip dead" id="stale"><span class="sq"><i></i></span>offline</span>
    <span class="chip" id="state"><span class="sq"><i></i></span><b id="state_t">connecting</b></span>
  </div>
</header>

<section class="rule intro">
  <div class="cap">Mining run</div>
  <h1 id="headline">Waiting for a run</h1>
  <p id="lede">Start a batch in the TUI. This page follows it live.</p>
  <div class="line"><i id="pbar"></i></div>
</section>

<div class="rule grid g5">
  <div class="stat"><span class="cap">Elapsed</span><span class="v num" id="elapsed">—</span></div>
  <div class="stat"><span class="cap">Time left</span><span class="v num" id="eta">—</span><span class="h" id="rate">—</span></div>
  <div class="stat"><span class="cap">Repositories</span><span class="v num" id="repos">—</span><span class="h">done of total</span></div>
  <div class="stat"><span class="cap">LLM ok / fail</span><span class="v num" id="llm">—</span><span class="h" id="lat">—</span></div>
  <div class="stat"><span class="cap">Circuit breaker</span><span class="v" id="breaker">—</span><span class="h" id="breaker_h">LLM failures stop the batch</span></div>
</div>

<div class="rule grid g3">
  <div class="stat"><span class="cap">Miner memory</span><span class="v num" id="ram">—</span><span class="h" id="peak">—</span><div class="meter" id="ram_t"><i></i></div></div>
  <div class="stat"><span class="cap">System memory</span><span class="v num" id="free">—</span><span class="h" id="free_h">—</span><div class="meter" id="used_t"><i></i></div></div>
  <div class="stat"><span class="cap">CPU</span><span class="v num" id="cpu">—</span><span class="h" id="cpu_h">—</span><div class="meter" id="cpu_t"><i></i></div></div>
</div>

<section class="rule section">
  <div class="shead"><span class="cap">01</span><span class="cap" id="rcount"></span></div>
  <div class="shead" style="display:block"><h2>Repositories</h2><p>Each repository in this PC's share, in mining order. Finished and failed ones sink to the bottom.</p></div>
  <div class="tabs" id="filters"></div>
  <ul class="list" id="rows"><li class="empty">No run yet.</li></ul>
</section>

<section class="rule section" style="margin-top:32px">
  <div class="shead"><span class="cap">02</span><span class="cap" id="logname">log</span></div>
  <div class="shead" style="display:block"><h2>Log</h2><p>The last lines the miner wrote, as the TUI shows them.</p></div>
  <div class="code"><div class="bar"><span id="logfile">miner.log</span><span>tail</span></div><pre id="log"></pre></div>
  <div class="paths" id="paths"></div>
</section>

<footer class="rule"><span class="cap">Riffle miner · read-only view</span><span class="cap" id="foot_ver"></span></footer>
</div>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const TABS = [["all","All"],["active","Active"],["warn","Attention"],["failed","Failed"],["done","Done"],["queued","Queued"]];
const TAG = {active:"Mining", warn:"Attention", failed:"Failed", done:"Done", queued:"Queued"};
let filter = "all", last = null, lastOk = 0;

const dash = v => (v === null || v === undefined || v === "") ? "—" : v;
function setText(id, v){ $(id).textContent = dash(v); }
function tone(id, t){ const e = $(id); e.classList.remove("warn","bad","good"); if (t) e.classList.add(t); }
function meter(id, frac, lvl){ const m = $(id); m.firstElementChild.style.width = Math.max(0, Math.min(1, frac || 0)) * 100 + "%";
  m.classList.remove("warn","bad"); if (lvl === "warn" || lvl === "bad") m.classList.add(lvl); }

function tabs(rows){
  const n = {all: rows.length}; for (const r of rows) n[r.status] = (n[r.status] || 0) + 1;
  if (filter !== "all" && !n[filter]) filter = "all";
  const box = $("filters"); box.replaceChildren();
  for (const [k, label] of TABS) {
    if (k !== "all" && !n[k]) continue;
    const b = el("button", null, label); b.type = "button"; b.setAttribute("aria-pressed", String(filter === k));
    b.appendChild(el("span", "n", String(n[k] || 0)));
    b.onclick = () => { filter = k; if (last) render(last); };
    box.appendChild(b);
  }
}

function row(r){
  const li = el("li", "row" + (r.status === "queued" ? " q" : ""));
  li.appendChild(el("span", "nm", r.name));
  li.appendChild(el("span", "st st-" + r.status, TAG[r.status] || r.status));
  const plain = ["mining", "done", "queued"].includes((r.stage || "").toLowerCase());
  li.appendChild(el("span", "stage", plain ? "" : r.stage));
  const prog = el("span", "prog"); const m = el("span", "meter"); const fill = el("i");
  fill.style.width = (r.total ? Math.min(1, r.done / r.total) : 0) * 100 + "%"; m.appendChild(fill);
  prog.append(m, el("span", null, r.prs)); li.appendChild(prog);
  const facts = el("span", "facts");
  for (const [k, v] of [["szz", r.szz], ["eta", r.eta], ["time", r.time], ["rows", r.rows], ["llm", r.llm], ["slowest", r.slowest]]) {
    if (!v || v === "—" || (k === "rows" && v === "0")) continue;
    const f = el("span"); f.append(k + " ", el("b", null, v)); facts.appendChild(f);
  }
  li.appendChild(facts);
  return li;
}

function render(s){
  last = s;
  const v = s.version ? "v" + s.version : "";
  setText("ver", v); $("foot_ver").textContent = v;
  const state = s.state || "idle";
  $("state").className = "chip " + (state === "running" ? "live" : /killed|exit|breaker/.test(state) ? "dead" : state === "stopped" ? "stopped" : "");
  setText("state_t", state);
  const p = s.progress || 0;
  $("pbar").style.width = p * 100 + "%";
  const h = $("headline"); h.replaceChildren();
  if (s.prs && s.prs.includes("/")) {
    const [d, t] = s.prs.split("/");
    h.append(Number(d).toLocaleString() + " ", el("span", "of", "of " + (t === "?" ? "?" : Number(t).toLocaleString()) + " PRs"));
  } else h.textContent = state === "idle" ? "Waiting for a run" : "Starting";
  $("lede").textContent = s.prs ? [ (p * 100).toFixed(p < 0.1 ? 1 : 0) + "% mined", s.repos && "repositories " + s.repos, s.rate, s.eta && "about " + s.eta + " left" ].filter(Boolean).join(" · ")
                                : "Start a batch in the TUI. This page follows it live.";
  setText("elapsed", s.elapsed); setText("eta", s.eta); setText("rate", s.rate);
  setText("repos", s.repos); setText("llm", s.llm); setText("lat", s.llm_latency ? "average " + s.llm_latency + " per call" : null);
  setText("breaker", s.breaker ? "Tripped" : (s.state && s.state !== "idle" ? "Armed" : null)); tone("breaker", s.breaker ? "bad" : null);
  if (s.breaker) $("breaker_h").textContent = s.breaker;
  const r = s.resources || {};
  setText("ram", r.rss); setText("peak", r.peak ? "peak " + r.peak : null); meter("ram_t", r.rss_frac, r.level);
  setText("free", r.used_frac != null ? Math.round(r.used_frac * 100) + "% used" : null); tone("free", r.level === "ok" ? null : r.level);
  setText("free_h", r.free ? r.free + " free of " + r.total : null); meter("used_t", r.used_frac, r.level);
  setText("cpu", r.cpu ? r.cpu.split(" ")[0] : null); setText("cpu_h", r.cpu ? "of " + r.cpu.split(" of ")[1] + " across all cores" : null);
  meter("cpu_t", r.cpu_frac);
  const rows = s.rows || [];
  tabs(rows);
  $("rcount").textContent = rows.length ? rows.length + " repositories" : "";
  const box = $("rows"); box.replaceChildren();
  const shown = rows.filter(x => filter === "all" || x.status === filter);
  if (!shown.length) box.appendChild(el("li", "empty", rows.length ? "Nothing in this view." : "No run yet."));
  for (const x of shown) box.appendChild(row(x));
  const pre = $("log"); const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  pre.replaceChildren();
  for (const l of (s.log || [])) pre.appendChild(el("div", {warn:"w", bad:"b", ok:"g"}[l.level] || "", l.text));
  if (atEnd) pre.scrollTop = pre.scrollHeight;
  const lf = s.log_path ? s.log_path.split("/").pop() : "miner.log";
  $("logfile").textContent = lf; $("logname").textContent = (s.log || []).length + " lines";
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
""".replace("@@LOCKUP@@", LOCKUP_SVG).replace("@@FAVICON@@", FAVICON_B64)

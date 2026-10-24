"""
dashboard.py — read-only web view of the TUI, for other devices on your Wi-Fi.

tui.py starts this on launch and publishes a snapshot of what it shows (run
stats, RAM/CPU, the repo table, the log tail) once a second. Any phone or
laptop on the same network can open the printed URL; the page polls
/api/status every 2 s. Standard library only.

Read-only by design: nothing on the page can start, stop or change a run.
The URL carries a random key, new on every launch, so other people on the
network can't watch without it. It is plain HTTP (the key travels in the URL, in
the clear) and binds to all interfaces of this machine, so use it on a network you
trust and don't port-forward it to the internet. Each connection has a socket
timeout, so an idle or half-open client can't hold a thread for ever.

    RIFFLE_DASHBOARD=0        disable
    RIFFLE_DASHBOARD_PORT=N   first port to try (default 8765)
    RIFFLE_DASHBOARD_BIND=ADDR  interface to bind (default 0.0.0.0; 127.0.0.1 = this computer only)
"""
from __future__ import annotations

import hmac
import json
import os
import re
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


REQUEST_TIMEOUT = 15.0                              # seconds a connection may sit idle


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
                self._server = ThreadingHTTPServer((os.getenv("RIFFLE_DASHBOARD_BIND") or "0.0.0.0", port), handler)
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
        timeout = REQUEST_TIMEOUT                  # socket timeout per connection (a stalled client frees its thread)

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
# the icon alone (the lockup's first two shapes, the Riffle wordmark left out), for the big picture's corner
MARK_SVG = ('<svg class="lockup mark" role="img" aria-label="Riffle" viewBox="30 0 300 164" fill="none" '
            'xmlns="http://www.w3.org/2000/svg">' + "".join(re.findall(r"<path .*?/>", LOCKUP_SVG, re.S)[:2]) + "</svg>")
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
.tabs{display:flex;flex-wrap:wrap;gap:4px}
.rtools{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin-top:22px}
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
.st-skipped{color:var(--grey);background:transparent;outline:1px solid var(--stroke)}
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
/* toolbar: the docs header's outline buttons (Ask AI / Search docs) */
.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:20px}
.btn{appearance:none;display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 10px;cursor:pointer;
  border-radius:6px;border:1px solid var(--stroke);background:var(--surface);color:var(--ink);
  font:500 13px var(--sans);transition:background .15s,border-color .15s;white-space:nowrap}
.btn:hover{border-color:var(--grey)}
.btn[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.btn[aria-pressed="true"] kbd{color:var(--bg);border-color:rgba(255,255,255,.25)}
kbd{font:400 10.5px var(--mono);color:var(--grey);border:1px solid var(--stroke);border-radius:3px;padding:0 4px;line-height:16px}
.seg{display:inline-flex;border:1px solid var(--stroke);border-radius:6px;overflow:hidden;background:var(--surface)}
.seg button{appearance:none;border:0;background:transparent;cursor:pointer;height:28px;padding:0 10px;
  font:500 13px var(--sans);color:var(--nickel);border-left:1px solid var(--stroke)}
.seg button:first-child{border-left:0}
.seg button[aria-pressed="true"]{background:var(--wash);color:var(--ink)}
.search{display:inline-flex;align-items:center;gap:8px;height:30px;padding:0 10px;min-width:220px;flex:1;max-width:340px;
  border:1px solid var(--stroke);border-radius:6px;background:var(--surface)}
.search input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:var(--ink);font:13px var(--sans)}
.search input::placeholder{color:var(--grey)}
.search:focus-within{border-color:var(--grey)}
.grow{flex:1}
.hits{font:12px var(--mono);color:var(--grey);white-space:nowrap}
.code pre.nowrap{white-space:pre;overflow-x:auto;word-break:normal}
.code pre div{padding:0 2px}
.code mark{background:rgba(227,179,65,.35);color:inherit;border-radius:2px}
.code .bar .acts{display:flex;gap:14px}
.code .bar button{appearance:none;border:0;background:transparent;cursor:pointer;color:var(--code-grey);font:12px var(--mono);padding:0}
.code .bar button:hover{color:var(--code-ink)}
.paused-note{display:none;font:12px var(--mono);color:var(--t-amber)}
body.paused .paused-note{display:inline}
body.expanded .wrap>*:not(.logsec){display:none}
body.expanded .logsec{border-top:0}
body.expanded .code pre{max-height:calc(100vh - 230px)}
.row .nm{cursor:copy}
/* touch screens have no keyboard: drop the key hints and the shortcuts button */
@media (hover:none){kbd,#b_help{display:none}}
/* toast */
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(12px);opacity:0;pointer-events:none;
  background:var(--ink);color:var(--bg);font:500 13px var(--sans);padding:8px 14px;border-radius:6px;transition:all .2s ease;z-index:30}
#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
/* shortcuts overlay */
#help{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:rgba(22,23,29,.35);z-index:40;padding:16px}
#help.on{display:flex}
#help .box{background:var(--bg);border:1px solid var(--stroke);width:min(440px,100%);padding:24px 28px}
#help h3{margin:10px 0 16px;font:500 1.35rem var(--sans);letter-spacing:-.02em}
#help dl{display:grid;grid-template-columns:auto 1fr;gap:9px 18px;margin:0;font-size:14px;color:var(--nickel)}
#help dt{text-align:right}
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

/* Big picture (PC only): one full-screen view read from across a dark room,
   15 ft or more. A four-column grid with named areas: columns 1-3 carry the
   work (status, percent, PR facts, current repo) flush on the left edge,
   column 4 the system and rates (time, CPU, memory, speed, repositories)
   flush on the right edge, top to bottom. Items sharing a row align on their text baseline. Spacing is tight
   inside a group and looser between groups. Type: headline, percent, value,
   label, log. Always dark, no pure white; status is a word as well as a
   colour. Hidden on phones and touch screens. */
#big{--bp-pad-x:4vw;--bp-pad-y:4vh;--bp-col:3vw;
  --bp-tight:1vh;--bp-step:2.2vh;--bp-break:3.6vh;
  --bp-label:clamp(12px,2.3vh,34px);--bp-value:clamp(20px,min(5.6vh,2.9vw),84px);
  --bp-head:clamp(26px,min(7.5vh,4vw),112px);--bp-log:clamp(12px,min(3vh,1.7vw),44px);
  --bp-ink:#e8e6ec;--bp-soft:#9a97a3;--bp-dim:#6f6c78;--bp-rule:#1d1f25;
  position:fixed;inset:0;z-index:50;display:none;background:#0b0c0f;color:var(--bp-ink);
  font-family:var(--sans);padding:var(--bp-pad-y) var(--bp-pad-x) calc(var(--bp-pad-y) * .75);
  grid-template-columns:repeat(4,minmax(0,1fr));column-gap:var(--bp-col);
  grid-template-rows:auto auto auto auto auto auto minmax(0,1fr) auto;
  grid-template-areas:
    "word  word  word  clock"
    "pct   pct   pct   sys"
    "bar   bar   bar   bar"
    "prs   eta   ela   rate"
    "now   now   now   repos"
    "alert alert alert alert"
    "log   log   log   log"
    "brand seen  seen  hint";
  overflow:hidden}
#big.on{display:grid}
#big.dim{filter:brightness(.55)}
#big ::selection{background:rgba(61,220,132,.28)}

/* rows that share a line share a baseline */
#big .word{grid-area:word;align-self:last baseline;font-size:var(--bp-head);line-height:1;font-weight:600;letter-spacing:-.01em}
#big .clock{grid-area:clock;align-self:last baseline}
#big .pct{grid-area:pct;align-self:last baseline;margin-top:var(--bp-step)}
#big .sys{grid-area:sys;align-self:last baseline;margin-top:var(--bp-step);display:flex;flex-direction:column;gap:var(--bp-step);min-width:0}
#big .bar{grid-area:bar;margin-top:var(--bp-step)}
#big .f-prs{grid-area:prs}#big .f-eta{grid-area:eta}#big .f-ela{grid-area:ela}#big .f-rate{grid-area:rate}
#big .f-prs,#big .f-eta,#big .f-ela,#big .f-rate{margin-top:var(--bp-break)}
#big .f-now{grid-area:now}#big .f-repos{grid-area:repos}
#big .f-now,#big .f-repos{margin-top:var(--bp-step)}
#big .alert{grid-area:alert;margin-top:var(--bp-break)}
#big .log{grid-area:log;margin-top:var(--bp-step)}
#big .seen{grid-area:seen;align-self:last baseline;margin-top:var(--bp-step)}
#big .bp-brand{grid-area:brand;align-self:last baseline;margin-top:var(--bp-step);display:flex;align-items:center;
  gap:1.2vh;color:var(--bp-dim);min-width:0}
#big .bp-brand .lockup{height:clamp(14px,2.6vh,30px);width:auto;color:var(--bp-dim)}
#big .bp-brand .bpver{font-family:var(--mono);font-size:var(--bp-label);line-height:1.1;white-space:nowrap}
#big .hint{grid-area:hint;align-self:last baseline;margin-top:var(--bp-step)}

#big .word.run{color:#3ddc84}#big .word.warn{color:#f0b443}#big .word.bad{color:#ff6b6b}#big .word.done{color:#7aa7ff}
#big .pct{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:500;
  font-size:clamp(64px,min(27vh,20vw),400px);line-height:.82;letter-spacing:-.04em}
#big .pct.idle{color:var(--bp-dim)}
#big .bar{height:2.6vh;min-height:12px;background:var(--bp-rule);border-radius:4px;overflow:hidden}
#big .bar i{display:block;height:100%;width:0;background:#3ddc84}
#big .bar.warn i{background:#f0b443}#big .bar.bad i{background:#ff6b6b}
#big .fact{min-width:0;display:flex;flex-direction:column;gap:var(--bp-tight)}
#big .lbl{font-size:var(--bp-label);line-height:1.1;color:var(--bp-soft);letter-spacing:.03em}
#big .val{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:500;font-size:var(--bp-value);
  line-height:1.05;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#big .val.warn{color:#f0b443}#big .val.bad{color:#ff6b6b}
#big .clock .val{color:var(--bp-soft)}
#big .alert{font-size:var(--bp-value);line-height:1.15;font-weight:600;color:#f0b443;min-height:1.15em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#big .alert.bad{color:#ff6b6b}
#big .alert:empty{display:none}
#big .log{min-height:0;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;gap:var(--bp-tight);
  font-family:var(--mono);font-size:var(--bp-log);line-height:1.25;color:var(--bp-soft);
  border-top:1px solid var(--bp-rule);padding-top:var(--bp-step)}
#big .log div{flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#big .log .w{color:#f0b443}#big .log .b{color:#ff6b6b}
#big .seen,#big .hint{font-size:var(--bp-label);line-height:1.1;color:var(--bp-dim)}
/* column 4 sits flush on the right edge, mirroring column 1 on the left */
#big .clock,#big .sys,#big .sys .fact,#big .f-rate,#big .f-repos{align-items:flex-end;text-align:right}
#big .hint{text-align:right}
#big .seen.off{color:#ff6b6b;font-weight:600}
#big .bpbtn{font:inherit;color:var(--bp-soft);background:transparent;border:1px solid var(--bp-rule);
  border-radius:6px;padding:0 14px;min-height:40px;margin-left:8px}
/* phones, portrait: one column of the same things, big status word and percent first, two facts per row,
   facts in the right column flush right like the desktop's fourth column; Dim and Exit are buttons
   because there is no keyboard and an accidental tap must not leave the page */
@media (max-width:899px){
  #big{--bp-tight:.6vh;--bp-step:1.4vh;--bp-break:2.2vh;
    --bp-label:clamp(11px,3.1vw,15px);--bp-value:clamp(18px,6.2vw,30px);
    --bp-head:clamp(24px,8vw,44px);--bp-log:clamp(10px,2.9vw,13px);
    padding:max(2.5vh,env(safe-area-inset-top,0px)) max(5vw,env(safe-area-inset-right,0px))
      max(2vh,env(safe-area-inset-bottom,0px)) max(5vw,env(safe-area-inset-left,0px));
    grid-template-columns:repeat(2,minmax(0,1fr));column-gap:4vw;
    grid-template-rows:auto auto auto auto auto auto auto auto minmax(0,1fr) auto auto;
    grid-template-areas:"word word" "pct pct" "bar bar" "prs eta" "ela rate" "now now" "repos sys"
      "alert alert" "log log" "seen seen" "brand hint"}
  #big .clock{display:none}
  #big .f-now .val{font-size:clamp(14px,4.6vw,22px)}
  #big .alert{font-size:clamp(15px,4.6vw,22px);white-space:normal;line-height:1.2}
  #big .pct{font-size:clamp(64px,27vw,150px);line-height:.85}
  #big .bar{height:1.6vh;min-height:10px}
  #big .f-eta{align-items:flex-end;text-align:right}
  #big .f-repos{align-items:flex-start;text-align:left}
  #big .sys{flex-direction:row;justify-content:flex-end;gap:4vw}
  #big .bp-brand .lockup{height:clamp(16px,3vh,22px)}
  #big .hint{display:flex;align-items:center;justify-content:flex-end}
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

<div class="rule grid g3">
  <div class="stat"><span class="cap">LLM tokens in</span><span class="v num" id="tok_in">—</span><span class="h" id="tok_in_h">exact, this run, all repositories</span></div>
  <div class="stat"><span class="cap">LLM tokens out</span><span class="v num" id="tok_out">—</span><span class="h" id="tok_out_h">—</span></div>
  <div class="stat"><span class="cap">Claude plan</span><span class="v num" id="plan">—</span><span class="h" id="plan_h">from Claude Code's /usage</span><div class="meter" id="plan_t"><i></i></div></div>
</div>

<section class="rule section">
  <div class="shead"><span class="cap">01</span><span class="cap" id="rcount"></span></div>
  <div class="shead" style="display:block"><h2>Repositories</h2><p>Each repository in this PC's share, in mining order. Finished and failed ones sink to the bottom.</p></div>
  <div class="rtools"><div class="tabs" id="filters"></div>
    <label class="search"><span class="cap">find</span><input id="rq" type="search" placeholder="Filter repositories" autocomplete="off"></label></div>
  <ul class="list" id="rows"><li class="empty">No run yet.</li></ul>
</section>

<section class="rule section logsec" style="margin-top:32px">
  <div class="shead"><span class="cap">02</span><span class="cap" id="logname">log</span></div>
  <div class="shead" style="display:block"><h2>Log</h2><p>The last lines the miner wrote, as the TUI shows them. Select any text to copy it; updates hold while you do.</p></div>
  <div class="toolbar">
    <label class="search"><span class="cap">/</span><input id="lq" type="search" placeholder="Search the log" autocomplete="off"></label>
    <div class="seg" id="lvl"><button type="button" data-l="all" aria-pressed="true">All</button><button type="button" data-l="warn">Warnings</button><button type="button" data-l="bad">Errors</button></div>
    <span class="hits" id="hits"></span>
    <span class="grow"></span>
    <button class="btn" type="button" id="b_wrap" aria-pressed="true">Wrap <kbd>w</kbd></button>
    <button class="btn" type="button" id="b_follow" aria-pressed="true">Follow <kbd>f</kbd></button>
    <button class="btn" type="button" id="b_pause" aria-pressed="false">Pause <kbd>p</kbd></button>
    <button class="btn" type="button" id="b_expand" aria-pressed="false">Expand <kbd>e</kbd></button>
  </div>
  <div class="code"><div class="bar"><span id="logfile">miner.log</span><span class="acts">
    <span class="paused-note">paused</span>
    <button type="button" id="b_copy">copy <kbd>c</kbd></button><button type="button" id="b_dl">download</button></span></div>
    <pre id="log"></pre></div>
  <div class="paths" id="paths"></div>
</section>

<footer class="rule"><span class="cap">Riffle miner · read-only view</span>
  <span class="hright"><button class="btn" type="button" id="b_big">Big picture <kbd>b</kbd></button>
  <button class="btn" type="button" id="b_link">Copy link <kbd>l</kbd></button>
  <button class="btn" type="button" id="b_help">Shortcuts <kbd>?</kbd></button><span class="cap" id="foot_ver"></span></span></footer>
</div>
<div id="big" role="region" aria-label="Big picture">
  <div class="word" id="bg_word">CONNECTING</div>
  <div class="fact clock"><span class="lbl">Time</span><span class="val" id="bg_clock">--:--</span></div>
  <div class="pct" id="bg_pct">—</div>
  <div class="sys">
    <div class="fact"><span class="lbl">CPU</span><span class="val" id="bg_cpu">—</span></div>
    <div class="fact"><span class="lbl">Memory used</span><span class="val" id="bg_mem">—</span></div>
  </div>
  <div class="bar" id="bg_bar"><i></i></div>
  <div class="fact f-prs"><span class="lbl">PRs mined</span><span class="val" id="bg_prs">—</span></div>
  <div class="fact f-eta"><span class="lbl">Time left</span><span class="val" id="bg_eta">—</span></div>
  <div class="fact f-ela"><span class="lbl">Elapsed</span><span class="val" id="bg_elapsed">—</span></div>
  <div class="fact f-rate"><span class="lbl">PRs per minute</span><span class="val" id="bg_rate">—</span></div>
  <div class="fact f-now"><span class="lbl" id="bg_now_lbl">Now mining</span><span class="val" id="bg_repo">—</span></div>
  <div class="fact f-repos"><span class="lbl">Repositories</span><span class="val" id="bg_repos">—</span></div>
  <div class="alert" id="bg_alert"></div>
  <div class="log" id="bg_log"></div>
  <div class="bp-brand">@@MARK@@<span class="bpver" id="bg_ver">v—</span></div>
  <div class="seen" id="bg_seen">—</div>
  <div class="hint" id="bg_hint">b or click to exit · d dim</div>
</div>
<div id="toast" role="status" aria-live="polite"></div>
<div id="help" role="dialog" aria-modal="true" aria-labelledby="help_t"><div class="box">
  <div class="cap">Keyboard</div><h3 id="help_t">Shortcuts</h3>
  <dl><dt><kbd>/</kbd></dt><dd>Search the log</dd><dt><kbd>c</kbd></dt><dd>Copy the visible log</dd>
  <dt><kbd>w</kbd></dt><dd>Wrap long lines on / off</dd><dt><kbd>f</kbd></dt><dd>Follow new lines</dd>
  <dt><kbd>p</kbd></dt><dd>Pause / resume live updates</dd><dt><kbd>e</kbd></dt><dd>Expand the log to the full page</dd>
  <dt><kbd>1</kbd>–<kbd>7</kbd></dt><dd>Repository filter tabs</dd><dt><kbd>r</kbd></dt><dd>Filter repositories</dd>
  <dt><kbd>b</kbd></dt><dd>Big picture: full screen, readable from across the room</dd>
  <dt><kbd>d</kbd></dt><dd>Dim the big picture</dd>
  <dt><kbd>l</kbd></dt><dd>Copy this page's link</dd><dt><kbd>Esc</kbd></dt><dd>Close, clear search, leave expanded view</dd></dl>
</div></div>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const TABS = [["all","All"],["active","Active"],["warn","Attention"],["failed","Failed"],["done","Done"],["queued","Queued"],["skipped","Skipped"]];
const TAG = {active:"Mining", warn:"Attention", failed:"Failed", done:"Done", queued:"Queued", skipped:"Skipped"};
const pref = (k, d) => { try { const v = localStorage.getItem("rm." + k); return v === null ? d : v === "1"; } catch (e) { return d; } };
const save = (k, v) => { try { localStorage.setItem("rm." + k, v ? "1" : "0"); } catch (e) {} };
const ui = {filter: "all", rq: "", lq: "", lvl: "all", wrap: pref("wrap", true), follow: pref("follow", true), paused: false, expanded: false};
let last = null, lastOk = 0, sigRows = "", sigLog = "";

const dash = v => (v === null || v === undefined || v === "") ? "—" : v;
function setText(id, v){ $(id).textContent = dash(v); }
function tone(id, t){ const e = $(id); e.classList.remove("warn","bad","good"); if (t) e.classList.add(t); }
function meter(id, frac, lvl){ const m = $(id); m.firstElementChild.style.width = Math.max(0, Math.min(1, frac || 0)) * 100 + "%";
  m.classList.remove("warn","bad"); if (lvl === "warn" || lvl === "bad") m.classList.add(lvl); }
function toast(msg){ const t = $("toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("on"), 1800); }

/* Copy that also works over plain http on the LAN, where navigator.clipboard
   is unavailable: fall back to a hidden textarea + execCommand. */
async function copy(text, what){
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); toast("Copied " + what); return; } } catch (e) {}
  const ta = el("textarea"); ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select(); let ok = false; try { ok = document.execCommand("copy"); } catch (e) {}
  ta.remove(); toast(ok ? "Copied " + what : "Copy blocked by the browser; select the text instead");
}
/* While the reader holds a selection inside the log or the repo list, don't
   rebuild those nodes (a rebuild is what used to wipe the selection). */
function selecting(node){ const s = window.getSelection(); return s && !s.isCollapsed && s.rangeCount && node.contains(s.anchorNode); }

function tabs(rows){
  const n = {all: rows.length}; for (const r of rows) n[r.status] = (n[r.status] || 0) + 1;
  if (ui.filter !== "all" && !n[ui.filter]) ui.filter = "all";
  const box = $("filters"); box.replaceChildren();
  TABS.forEach(([k, label], i) => {
    if (k !== "all" && !n[k]) return;
    const b = el("button", null, label); b.type = "button"; b.title = "Shortcut: " + (i + 1);
    b.setAttribute("aria-pressed", String(ui.filter === k));
    b.appendChild(el("span", "n", String(n[k] || 0)));
    b.onclick = () => { ui.filter = k; sigRows = ""; render(last); };
    box.appendChild(b);
  });
}

function row(r){
  const li = el("li", "row" + (r.status === "queued" ? " q" : ""));
  const nm = el("span", "nm", r.name); nm.title = "Click to copy"; nm.onclick = () => copy(r.name, r.name);
  li.appendChild(nm);
  li.appendChild(el("span", "st st-" + r.status, TAG[r.status] || r.status));
  const plain = ["mining", "done", "queued", "skipped"].includes((r.stage || "").toLowerCase());
  const st = el("span", "stage", plain ? "" : r.stage); st.title = r.stage || ""; li.appendChild(st);
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

function renderRows(rows){
  const box = $("rows");
  const shown = rows.filter(x => (ui.filter === "all" || x.status === ui.filter) && (!ui.rq || x.name.toLowerCase().includes(ui.rq)));
  const sig = JSON.stringify([ui.filter, ui.rq, shown]);
  if (sig === sigRows || selecting(box)) return;
  sigRows = sig; box.replaceChildren();
  if (!shown.length) box.appendChild(el("li", "empty", rows.length ? "Nothing in this view." : "No run yet."));
  for (const x of shown) box.appendChild(row(x));
}

function visibleLog(){
  const q = ui.lq;
  return (last && last.log || []).filter(l =>
    (ui.lvl === "all" || (ui.lvl === "warn" ? (l.level === "warn" || l.level === "bad") : l.level === "bad")) &&
    (!q || l.text.toLowerCase().includes(q)));
}
function renderLog(){
  const pre = $("log");
  pre.classList.toggle("nowrap", !ui.wrap);
  const lines = visibleLog();
  const sig = JSON.stringify([ui.lq, ui.lvl, lines.length, lines.length ? lines[lines.length - 1].text : "", (last && last.log || []).length]);
  $("hits").textContent = (ui.lq || ui.lvl !== "all") ? lines.length + " of " + (last && last.log || []).length + " lines" : "";
  if (sig === sigLog || selecting(pre)) return;
  sigLog = sig;
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  pre.replaceChildren();
  const q = ui.lq;
  for (const l of lines) {
    const d = el("div", {warn:"w", bad:"b", ok:"g"}[l.level] || "");
    if (q) {                                     // highlight matches without innerHTML
      const lo = l.text.toLowerCase(); let i = 0, j;
      while ((j = lo.indexOf(q, i)) !== -1) { d.append(l.text.slice(i, j), el("mark", null, l.text.slice(j, j + q.length))); i = j + q.length; }
      d.append(l.text.slice(i));
    } else d.textContent = l.text;
    pre.appendChild(d);
  }
  if (!lines.length) pre.appendChild(el("div", null, (last && (last.log || []).length) ? "No lines match." : "Nothing logged yet."));
  if (ui.follow && (atEnd || ui.lq === "")) pre.scrollTop = pre.scrollHeight;
}

function render(s){
  if (!s) return;
  last = s;
  const v = s.version ? "v" + s.version : "";
  setText("ver", v); $("foot_ver").textContent = v; $("bg_ver").textContent = v ? "miner " + v : "";
  const state = s.state || "idle";
  $("state").className = "chip " + (state === "running" ? "live" : /killed|exit|breaker/.test(state) ? "dead" : state === "stopped" ? "stopped" : "");
  setText("state_t", ui.paused ? state + " · paused" : state);
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
  setText("breaker", s.breaker ? "Tripped" : s.llm_paused ? "Paused" : (s.state && s.state !== "idle" ? "Armed" : null));
  tone("breaker", s.breaker ? "bad" : s.llm_paused ? "warn" : null);
  if (s.breaker) $("breaker_h").textContent = s.breaker;
  else if (s.llm_paused) $("breaker_h").textContent = s.llm_paused;
  else $("breaker_h").textContent = "LLM failures stop the batch";
  if (s.llm_skipped) setText("lat", (s.llm_latency ? "average " + s.llm_latency + " per call · " : "") + s.llm_skipped.toLocaleString() + " skipped (out of scope or trivial)");
  const r = s.resources || {};
  setText("ram", r.rss); setText("peak", r.peak ? "peak " + r.peak : null); meter("ram_t", r.rss_frac, r.level);
  setText("free", r.used_frac != null ? Math.round(r.used_frac * 100) + "% used" : null); tone("free", r.level === "ok" ? null : r.level);
  setText("free_h", r.free ? r.free + " free of " + r.total : null); meter("used_t", r.used_frac, r.level);
  setText("cpu", r.cpu ? r.cpu.split(" ")[0] : null); setText("cpu_h", r.cpu ? "of " + r.cpu.split(" of ")[1] + " across all cores" : null);
  meter("cpu_t", r.cpu_frac);
  const u = s.llm_usage;
  const fk = n => n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n || 0);
  setText("tok_in", u ? fk(u.tokens_in) : null);
  $("tok_in_h").textContent = u ? "exact · cache read " + fk(u.cache_read) + " · cache write " + fk(u.cache_write) : "exact, this run, all repositories";
  setText("tok_out", u ? fk(u.output) : null);
  $("tok_out_h").textContent = u ? u.calls.toLocaleString() + " calls" + (u.cost_known ? " · $" + u.cost_usd.toFixed(2) + " at API prices" : "") : "—";
  const pl = s.plan_usage || [];
  const ses = pl[0];
  setText("plan", ses ? ses.label + " " + Math.round(ses.pct) + "%" : null);
  tone("plan", ses ? (ses.pct >= 90 ? "bad" : ses.pct >= 70 ? "warn" : null) : null);
  last.plan = pl; planText();
  meter("plan_t", ses ? ses.pct / 100 : 0, ses ? (ses.pct >= 90 ? "bad" : ses.pct >= 70 ? "warn" : null) : null);
  const rows = s.rows || [];
  tabs(rows);
  $("rcount").textContent = rows.length ? rows.length + " repositories" : "";
  renderRows(rows);
  renderLog();
  const lf = s.log_path ? s.log_path.split("/").pop() : "miner.log";
  $("logfile").textContent = lf; $("logname").textContent = (s.log || []).length + " lines";
  const paths = $("paths"); paths.replaceChildren();
  if (s.out) paths.appendChild(el("div", null, "output  " + s.out));
  const asm = s.assembly;
  if (asm) paths.appendChild(el("div", asm.state === "failed" ? "bad" : null, "snapshots  " + (
    asm.state === "running" ? "assembling the snapshot tables..."
    : asm.state === "failed" ? "assembly FAILED: " + (asm.err || "see the log")
    : Number(asm.snapshots).toLocaleString() + " snapshots from " + Number(asm.prs).toLocaleString() +
      " PRs, " + Number(asm.commits).toLocaleString() + " head commits")));
  if (s.log_path) paths.appendChild(el("div", null, "log     " + s.log_path));
  document.title = (state === "running" ? (p * 100).toFixed(0) + "% · " : "") + "Riffle Miner";
}

/* controls */
function press(id, on){ $(id).setAttribute("aria-pressed", String(on)); }
const act = {
  wrap(){ ui.wrap = !ui.wrap; save("wrap", ui.wrap); press("b_wrap", ui.wrap); sigLog = ""; renderLog(); toast(ui.wrap ? "Wrapping long lines" : "Long lines scroll sideways"); },
  follow(){ ui.follow = !ui.follow; save("follow", ui.follow); press("b_follow", ui.follow); if (ui.follow) $("log").scrollTop = $("log").scrollHeight; toast(ui.follow ? "Following new lines" : "Not following"); },
  pause(){ ui.paused = !ui.paused; press("b_pause", ui.paused); document.body.classList.toggle("paused", ui.paused); if (!ui.paused) tick(); render(last); ago(); toast(ui.paused ? "Live updates paused" : "Live updates resumed"); },
  expand(){ ui.expanded = !ui.expanded; press("b_expand", ui.expanded); document.body.classList.toggle("expanded", ui.expanded); window.scrollTo(0, 0); },
  copyLog(){ const sel = window.getSelection(); const t = (sel && !sel.isCollapsed && $("log").contains(sel.anchorNode)) ? sel.toString() : visibleLog().map(l => l.text).join("\n");
    copy(t, t.split("\n").length + " log lines"); },
  download(){ const t = (last && last.log || []).map(l => l.text).join("\n") + "\n";
    const a = el("a"); a.href = URL.createObjectURL(new Blob([t], {type: "text/plain"}));
    a.download = (last && last.log_path ? last.log_path.split("/").pop().replace(/\.log$/, "") : "riffle-miner") + "-tail.log";
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500); },
  link(){ copy(location.href, "link"); },
  help(on){ $("help").classList.toggle("on", on === undefined ? !$("help").classList.contains("on") : on); },
};
press("b_wrap", ui.wrap); press("b_follow", ui.follow);
$("b_wrap").onclick = act.wrap; $("b_follow").onclick = act.follow; $("b_pause").onclick = act.pause;
$("b_expand").onclick = act.expand; $("b_copy").onclick = act.copyLog; $("b_dl").onclick = act.download;
$("b_link").onclick = act.link; $("b_help").onclick = () => act.help(true);
$("help").onclick = e => { if (e.target.id === "help") act.help(false); };
for (const b of $("lvl").querySelectorAll("button")) b.onclick = () => {
  ui.lvl = b.dataset.l; for (const x of $("lvl").querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b)); sigLog = ""; renderLog(); };
$("lq").oninput = e => { ui.lq = e.target.value.trim().toLowerCase(); sigLog = ""; renderLog(); };
$("rq").oninput = e => { ui.rq = e.target.value.trim().toLowerCase(); sigRows = ""; renderRows(last && last.rows || []); };

document.addEventListener("keydown", e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (e.key === "Escape") {
    if (big.on) return bigToggle(false);
    if ($("help").classList.contains("on")) return act.help(false);
    if (typing) { document.activeElement.value = ""; document.activeElement.dispatchEvent(new Event("input")); document.activeElement.blur(); return; }
    if (ui.expanded) return act.expand();
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key;
  if (k === "/") { e.preventDefault(); $("lq").focus(); }
  else if (k === "r") { e.preventDefault(); $("rq").focus(); }
  else if (k === "c") act.copyLog();
  else if (k === "w") act.wrap();
  else if (k === "f") act.follow();
  else if (k === "p") act.pause();
  else if (k === "e") act.expand();
  else if (k === "l") act.link();
  else if (k === "b") bigToggle();
  else if (k === "d" && big.on) bigDim();
  else if (k === "?") act.help();
  else if (/^[1-7]$/.test(k)) { const t = TABS[+k - 1]; const btn = [...$("filters").children].find(b => b.firstChild && b.firstChild.nodeValue === t[1]); if (btn) btn.click(); }
});

/* plan resets tick down live between polls */
function until(t){ const s = Math.max(0, Math.round(t - Date.now() / 1000)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return s <= 0 ? "resetting now" : "resets in " + (h ? h + "h" + String(m).padStart(2, "0") + "m" : m + "m " + (s % 60) + "s"); }
function planText(){
  const pl = (last && last.plan) || [];
  $("plan_h").textContent = pl.length ? pl.map((p, i) => {
    const at = p.resets_at ? new Date(p.resets_at * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}) : null;
    const when = p.resets_at ? until(p.resets_at) + " (" + at + ")" : (p.resets ? "resets " + p.resets.split(" (")[0] : "");
    return (i ? p.label + " " + Math.round(p.pct) + "% · " : "") + when;
  }).join(" · ") : "from Claude Code's /usage";
}
function ago(){ planText(); if (!lastOk && !ui.paused) return; const s = Math.round((Date.now() - lastOk) / 1000);
  $("updated").textContent = ui.paused ? "paused" : (s < 3 ? "live" : "updated " + s + "s ago"); }
async function tick(){
  if (ui.paused) return ago();
  try {
    const res = await fetch("/api/status?key=" + encodeURIComponent(key), {cache: "no-store"});
    if (!res.ok) throw new Error(res.status);
    render(await res.json()); lastOk = Date.now(); $("stale").style.display = "none"; renderBig();
  } catch (e) { $("stale").style.display = "inline-flex"; }
  ago();
}

/* ---- big picture ------------------------------------------------------------ */
const big = {on: false, dim: pref("bigdim", false), lock: null};
async function wake(on){
  try {
    if (on && "wakeLock" in navigator && document.visibilityState === "visible") big.lock = await navigator.wakeLock.request("screen");
    else if (!on && big.lock) { await big.lock.release(); big.lock = null; }
  } catch (e) { big.lock = null; }
}
document.addEventListener("visibilitychange", () => { if (big.on) wake(true); });
function bigToggle(on){
  big.on = on === undefined ? !big.on : on;
  $("big").classList.toggle("on", big.on); $("big").classList.toggle("dim", big.dim);
  document.body.style.overflow = big.on ? "hidden" : "";
  try { history.replaceState(null, "", big.on ? "#big" : location.pathname + location.search); } catch (e) {}
  wake(big.on); renderBig();
}
function bigDim(){ big.dim = !big.dim; save("bigdim", big.dim); $("big").classList.toggle("dim", big.dim); }
function renderBig(){
  if (!big.on) return;
  const s = last || {}, now = new Date();
  $("bg_clock").textContent = now.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  const stale = !lastOk || Date.now() - lastOk > 15000;
  const st = s.state || "idle";
  let word = "WAITING", cls = "";
  if (stale && lastOk) { word = "OFFLINE"; cls = "bad"; }
  else if (s.breaker || /killed|exit|failed|breaker/.test(st)) { word = "STOPPED: ERROR"; cls = "bad"; }
  else if (s.llm_paused) { word = "LLM PAUSED"; cls = "warn"; }
  else if (st === "running" && s.assembly && s.assembly.state === "running") { word = "ASSEMBLING"; cls = "run"; }
  else if (st === "running") { word = "MINING"; cls = "run"; }
  else if (st === "finished") { word = "FINISHED"; cls = "done"; }
  else if (st === "stopped") { word = "STOPPED"; cls = "warn"; }
  const w = $("bg_word"); w.textContent = word;
  for (const c of ["run", "warn", "bad", "done"]) w.classList.toggle(c, c === cls);
  const p = s.progress || 0;
  $("bg_pct").textContent = s.prs ? (p * 100).toFixed(p < 0.1 ? 1 : 0) + "%" : "0%";
  $("bg_pct").classList.toggle("idle", !s.prs);
  const bar = $("bg_bar"); bar.classList.toggle("bad", cls === "bad"); bar.classList.toggle("warn", cls === "warn");
  bar.firstChild.style.width = (p * 100) + "%";
  if (s.prs && s.prs.includes("/")) { const [d, t] = s.prs.split("/");
    $("bg_prs").textContent = Number(d).toLocaleString() + "/" + (t === "?" ? "?" : Number(t).toLocaleString()); }
  else $("bg_prs").textContent = "—";
  $("bg_eta").textContent = s.eta || (st === "finished" ? "done" : "—");
  $("bg_elapsed").textContent = s.elapsed || "—";
  $("bg_rate").textContent = s.rate ? s.rate.replace(/\s*PR\/min$/, "") : "—";
  const rows = s.rows || [];
  const cur = rows.find(r => r.status === "active" || r.status === "warn");
  const asmOn = s.assembly && s.assembly.state === "running";
  $("bg_now_lbl").textContent = asmOn ? "Now building" : "Now mining";
  $("bg_repo").textContent = asmOn ? "snapshot tables" : cur ? cur.name + (cur.total ? " · " + Number(cur.done).toLocaleString() + "/" + Number(cur.total).toLocaleString() : "") : "—";
  $("bg_repos").textContent = s.repos || "—";
  const res = s.resources || {};
  $("bg_cpu").textContent = res.cpu_frac != null ? Math.round(res.cpu_frac * 100) + "%" : "—";
  $("bg_mem").textContent = res.used_frac != null ? Math.round(res.used_frac * 100) + "%" : "—";
  for (const c of ["warn", "bad"]) $("bg_mem").classList.toggle(c, res.level === c);
  const log = s.log || [];
  const flag = [...log].reverse().find(l => /\[health\].* FLAG /.test(l.text));
  const alert = s.breaker ? ["Breaker: " + s.breaker, "bad"]
    : s.llm_paused ? [s.llm_paused, ""]
    : res.level === "bad" ? ["Memory critical: " + (res.free || "") + " free", "bad"]
    : res.level === "warn" ? ["Memory low: " + (res.free || "") + " free", ""]
    : flag ? [flag.text.replace(/^\[health\]\s*/, ""), ""] : ["", ""];
  const a = $("bg_alert"); a.textContent = alert[0]; a.classList.toggle("bad", alert[1] === "bad");
  const box = $("bg_log"); box.replaceChildren();
  for (const l of log.slice(-5)) box.appendChild(el("div", {warn: "w", bad: "b"}[l.level] || "", l.text));
  // whole lines only: drop the oldest until the rest fit
  const top = box.getBoundingClientRect().top;
  while (box.children.length > 1 && box.firstChild.getBoundingClientRect().top < top - 1) box.firstChild.remove();
  const ago = lastOk ? Math.round((Date.now() - lastOk) / 1000) : null;
  const seen = $("bg_seen");
  seen.textContent = ago === null ? "connecting" : stale ? "OFFLINE: no update for " + ago + "s" : "updated " + ago + "s ago";
  seen.classList.toggle("off", Boolean(stale && lastOk));
}
$("b_big").onclick = () => bigToggle(true);
if (window.matchMedia("(hover: none) and (pointer: coarse)").matches) {
  const btn = (text, fn) => { const b = el("button", "bpbtn", text); b.type = "button";
    b.onclick = e => { e.stopPropagation(); fn(); }; return b; };
  $("bg_hint").replaceChildren(btn("Dim", bigDim), btn("Exit", () => bigToggle(false)));
} else $("big").onclick = () => bigToggle(false);
setInterval(renderBig, 1000);
if (location.hash === "#big") bigToggle(true);
tick(); setInterval(tick, 2000); setInterval(ago, 1000);
</script></body></html>
""".replace("@@LOCKUP@@", LOCKUP_SVG).replace("@@MARK@@", MARK_SVG).replace("@@FAVICON@@", FAVICON_B64)

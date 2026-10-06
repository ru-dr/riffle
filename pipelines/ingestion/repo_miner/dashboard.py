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
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
/* Tokens: the site's own palette (site/components/home/tokens.ts and the docs
   themes in site/app/globals.css). */
:root{
  --sans:"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --bg:#fbfaf7;--surface:#ffffff;--wash:rgba(22,23,29,.045);--stroke:#e5e4e7;
  --ink:#16171d;--nickel:#3b3440;--grey:#867e8e;
  --accent:#0f7a4f;--live:#00b442;--accent-soft:rgba(0,180,66,.1);
  --warn:#a16207;--warn-soft:rgba(202,138,4,.12);--bad:#c0262d;--bad-soft:rgba(220,38,38,.09);
  --term:#16171d;--term-ink:#f2f1f4;--term-grey:rgba(242,241,244,.48);--term-stroke:#2c2d34;
  --chip-shadow:0 2px 4px 0 rgba(0,0,0,.05);--chip-outline:1px solid rgba(59,52,64,.06);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#16171d;--surface:#1e1f25;--wash:rgba(255,255,255,.055);--stroke:#2c2d34;
  --ink:#f2f1f4;--nickel:rgba(242,241,244,.72);--grey:rgba(242,241,244,.48);
  --accent:#7dd3a0;--live:#3ddc84;--accent-soft:rgba(125,211,160,.12);
  --warn:#e3b341;--warn-soft:rgba(227,179,65,.12);--bad:#f47174;--bad-soft:rgba(244,113,116,.12);
  --term:#1a1b20;--term-stroke:rgba(255,255,255,.08);
  --chip-shadow:none;--chip-outline:1px solid var(--stroke);
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
::selection{background:rgba(15,122,79,.18)}
.mono,.num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.03em}

/* The site's frame: the wrapper's own side borders are the vertical
   hairlines, and each section rule ends on them with a small inward tick. */
.wrap{margin-inline:auto;position:relative}
@media (min-width:48rem){.wrap{max-width:min(91vw,75rem);border-left:1px solid var(--stroke);border-right:1px solid var(--stroke)}}
.sec{position:relative;border-top:1px solid var(--stroke);padding:28px 24px}
.sec::before,.sec::after{content:"";position:absolute;top:-5px;width:0;height:0;
  border-top:5px solid transparent;border-bottom:5px solid transparent}
.sec::before{left:0;border-left:5px solid var(--stroke)}
.sec::after{right:0;border-right:5px solid var(--stroke)}

/* header */
header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:20px 24px}
.brand{display:flex;align-items:center;gap:12px;min-width:0;color:var(--ink);text-decoration:none}
.lockup{display:block;height:22px;width:auto;color:var(--ink)}
.tag{border-radius:4px;padding:2px 6px;font:500 11px var(--mono);letter-spacing:.04em;text-transform:uppercase;
  outline:1px solid var(--stroke);color:var(--grey);white-space:nowrap}
.tag.ver{text-transform:none;letter-spacing:0}
.right{display:flex;align-items:center;gap:10px}
.chip{display:inline-flex;align-items:center;gap:6px;border-radius:4px;padding:4px 12px;
  font:500 13px var(--mono);letter-spacing:-.03em;background:var(--surface);
  box-shadow:var(--chip-shadow);outline:var(--chip-outline);color:var(--grey);white-space:nowrap}
.chip .ink{color:var(--ink)}
.sq{display:inline-flex;border-radius:2px;padding:2px;background:var(--wash)}
.sq i{display:block;width:6px;height:6px;border-radius:1.1px;background:var(--grey)}
.s-running .sq{background:var(--accent-soft)} .s-running .sq i{background:var(--live);animation:rf-pulse 2.8s ease-in-out infinite}
.s-bad .sq{background:var(--bad-soft)} .s-bad .sq i{background:var(--bad)}
.s-warn .sq{background:var(--warn-soft)} .s-warn .sq i{background:var(--warn)}
@keyframes rf-pulse{50%{opacity:.3}}
@media (prefers-reduced-motion:reduce){.s-running .sq i{animation:none}}
.updated{color:var(--grey);font:12px var(--mono)}
#stale{display:none}

/* section labels: the site's mono caption */
.label{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 14px;
  font:500 12px var(--mono);letter-spacing:.04em;text-transform:uppercase;color:var(--grey)}
.label .count{color:var(--nickel)}

/* hero: overall progress */
.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;flex-wrap:wrap}
.pct{font:600 64px/1 var(--mono);letter-spacing:-.05em;color:var(--ink)}
.pct small{font-size:24px;color:var(--grey);margin-left:2px;letter-spacing:0}
.hero .sub{color:var(--grey);font-size:14px}
.hero .prs{font:600 22px var(--mono);letter-spacing:-.03em}
.track{height:6px;background:var(--wash);border-radius:999px;overflow:hidden}
.track>i{display:block;height:100%;width:0;border-radius:inherit;background:var(--accent);
  transition:width .8s cubic-bezier(.2,.8,.2,1)}
.hero-track{margin-top:20px;height:8px}

/* stat grid: hairline cells, no floating cards */
.stats{display:grid;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--stroke)}
.stats.three{grid-template-columns:repeat(3,1fr)}
.cell{padding:18px 24px;border-left:1px solid var(--stroke);min-width:0}
.cell:first-child{border-left:0}
.cell .k{font:500 12px var(--mono);letter-spacing:.04em;text-transform:uppercase;color:var(--grey)}
.cell .v{font:600 24px/1.25 var(--mono);letter-spacing:-.04em;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell .h{font:13px var(--mono);letter-spacing:-.02em;color:var(--grey);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell .track{margin-top:12px;height:4px}
.lvl-ok{color:var(--accent)} .lvl-warn{color:var(--warn)} .lvl-bad{color:var(--bad)}
.track.lvl-warn>i{background:var(--warn)} .track.lvl-bad>i{background:var(--bad)}

/* filters: the site's button spec (1px stroke, .5rem radius, white fill) */
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto;text-transform:none;letter-spacing:0}
.filters button{font:500 13px var(--sans);color:var(--nickel);background:var(--surface);
  border:1px solid var(--stroke);border-radius:.5rem;padding:4px 10px;cursor:pointer;
  transition:transform .15s ease,box-shadow .15s ease}
.filters button:hover{transform:scale(1.03);box-shadow:0 2px 6px rgba(0,0,0,.05)}
.filters button[aria-pressed="true"]{color:var(--bg);background:var(--ink);border-color:var(--ink)}
.filters .n{font-family:var(--mono);opacity:.6;margin-left:5px}

/* repo list */
.repos{border:1px solid var(--stroke);border-radius:.5rem;background:var(--surface);overflow:hidden}
.repo{display:grid;grid-template-columns:minmax(180px,1.3fr) minmax(150px,1.2fr) minmax(170px,1fr) minmax(0,1.8fr);
  gap:16px;align-items:center;padding:13px 18px;border-top:1px solid var(--stroke)}
.repo:first-child{border-top:0}
.repo .name{display:flex;align-items:center;gap:10px;min-width:0;font:500 14px var(--mono);letter-spacing:-.03em}
.repo .name span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{display:inline-flex;align-items:center;font-size:13px;font-weight:500;padding:2px 8px;
  border-radius:4px;background:var(--wash);color:var(--nickel);max-width:100%}
.badge span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.repo .sq{flex:none}
.st-active .badge{background:var(--accent-soft);color:var(--accent)}
.st-active .sq{background:var(--accent-soft)} .st-active .sq i{background:var(--live);animation:rf-pulse 2.8s ease-in-out infinite}
.st-warn .badge,.st-warn .sq{background:var(--warn-soft)} .st-warn .badge{color:var(--warn)} .st-warn .sq i{background:var(--warn)}
.st-failed .badge,.st-failed .sq{background:var(--bad-soft)} .st-failed .badge{color:var(--bad)} .st-failed .sq i{background:var(--bad)}
.st-done .badge{color:var(--accent)} .st-done .sq i{background:var(--accent)}
.prog{display:flex;align-items:center;gap:10px}
.prog .track{flex:1;height:4px}
.prog .num{font-size:13px;color:var(--grey);min-width:64px;text-align:right}
.meta{display:flex;flex-wrap:wrap;gap:4px 14px;font:12.5px var(--mono);letter-spacing:-.02em;color:var(--grey)}
.meta b{font-weight:500;color:var(--ink)}
.meta .ok b{color:var(--accent)}
.repo.queued{padding-top:10px;padding-bottom:10px}
.repo.queued .prog,.repo.queued .meta{visibility:hidden}
.repo.queued .name{color:var(--nickel)}
.empty{padding:28px;text-align:center;color:var(--grey)}

/* log: the site's dark code block */
.term{background:var(--term);color:var(--term-ink);border:1px solid var(--term-stroke);border-radius:.5rem;overflow:hidden}
.term .bar{display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-bottom:1px solid var(--term-stroke);
  font:12px var(--mono);color:var(--term-grey)}
.term pre{margin:0;padding:12px 14px;max-height:380px;overflow:auto;font:12.5px/1.7 var(--mono);
  white-space:pre-wrap;word-break:break-word}
.term .l-warn{color:#e3b341} .term .l-bad{color:#f47174} .term .l-ok{color:#7dd3a0}
.paths{margin-top:14px;font:12px var(--mono);color:var(--grey);word-break:break-all}
.paths div{margin-top:2px}
footer{padding:18px 24px;border-top:1px solid var(--stroke);font:12px var(--mono);color:var(--grey);
  display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}

/* responsive */
@media (max-width:980px){
  .stats{grid-template-columns:repeat(3,1fr)}
  .stats .cell:nth-child(n+4){border-top:1px solid var(--stroke)}
  .stats .cell:nth-child(4){border-left:0}
  .repo{grid-template-columns:1fr 1fr;gap:8px 16px}.repo .meta{grid-column:1/-1}
}
@media (max-width:640px){
  header{padding:16px}.sec{padding:22px 16px}.updated,.tag.ver{display:none}
  .cell .h{white-space:normal}
  .lockup{height:19px}
  .pct{font-size:48px}
  .stats,.stats.three{grid-template-columns:1fr 1fr}
  .cell{padding:14px 16px}.cell .v{font-size:19px}
  .stats .cell{border-top:1px solid var(--stroke);border-left:1px solid var(--stroke)}
  .stats .cell:nth-child(-n+2){border-top:0}.stats .cell:nth-child(odd){border-left:0}
  .stats .cell:last-child:nth-child(odd){grid-column:1/-1}
  .repo{grid-template-columns:1fr;padding:12px 14px}
  .repo.queued{grid-template-columns:1fr auto}
  .repo.queued .prog,.repo.queued .meta{display:none}
  .filters{margin-left:0;width:100%}
  footer{padding:16px}
}
</style></head><body>
<div class="wrap">
<header>
  <a class="brand" href="#" aria-label="Riffle miner">@@LOCKUP@@<span class="tag">Miner</span><span class="tag ver" id="ver">v—</span></a>
  <div class="right">
    <span class="updated" id="updated"></span>
    <span class="chip s-bad" id="stale"><span class="sq"><i></i></span>offline</span>
    <span class="chip" id="state"><span class="sq"><i></i></span><span class="ink" id="state_t">connecting</span></span>
  </div>
</header>

<section class="sec">
  <div class="hero">
    <div><div class="label" style="margin-bottom:8px">Progress</div><div class="pct" id="pct">0<small>%</small></div></div>
    <div style="text-align:right"><div class="sub">PRs mined</div><div class="prs" id="prs">—</div></div>
  </div>
  <div class="track hero-track"><i id="pbar"></i></div>
</section>

<div class="stats">
  <div class="cell"><div class="k">Elapsed</div><div class="v" id="elapsed">—</div></div>
  <div class="cell"><div class="k">ETA</div><div class="v" id="eta">—</div><div class="h" id="rate">—</div></div>
  <div class="cell"><div class="k">Repos</div><div class="v" id="repos">—</div></div>
  <div class="cell"><div class="k">LLM ok / fail</div><div class="v" id="llm">—</div><div class="h" id="lat"></div></div>
  <div class="cell"><div class="k">Breaker</div><div class="v" id="breaker">—</div></div>
</div>

<div class="stats three">
  <div class="cell"><div class="k">Miner RAM</div><div class="v" id="ram">—</div>
    <div class="h" id="peak"></div><div class="track" id="ram_t"><i></i></div></div>
  <div class="cell"><div class="k">System memory</div><div class="v" id="free">—</div>
    <div class="h" id="free_h"></div><div class="track" id="used_t"><i></i></div></div>
  <div class="cell"><div class="k">CPU</div><div class="v" id="cpu">—</div>
    <div class="h" id="cpu_h"></div><div class="track" id="cpu_t"><i></i></div></div>
</div>

<section class="sec">
  <div class="label">Repositories <span class="count" id="rcount"></span><div class="filters" id="filters"></div></div>
  <div class="repos" id="rows"><div class="empty">No run yet.</div></div>
</section>

<section class="sec">
  <div class="label">Log</div>
  <div class="term"><div class="bar"><span id="logname">miner.log</span><span>tail</span></div><pre id="log"></pre></div>
  <div class="paths" id="paths"></div>
</section>
<footer><span>Riffle miner · read-only view</span><span id="foot_ver"></span></footer>
</div>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const sq = () => { const s = el("span", "sq"); s.appendChild(el("i")); return s; };
const FILTERS = [["all","All"],["active","Active"],["warn","Attention"],["failed","Failed"],["done","Done"],["queued","Queued"]];
let filter = "all", last = null, lastOk = 0;

function setText(id, v){ $(id).textContent = (v === null || v === undefined || v === "") ? "—" : v; }
function setLvl(id, lvl){ const e=$(id); e.classList.remove("lvl-ok","lvl-warn","lvl-bad"); if (lvl) e.classList.add("lvl-"+lvl); }
function meter(id, frac, lvl){ const t=$(id); t.firstElementChild.style.width = Math.max(0, Math.min(1, frac||0))*100 + "%";
  t.classList.remove("lvl-ok","lvl-warn","lvl-bad"); if (lvl && lvl !== "ok") t.classList.add("lvl-"+lvl); }

function renderFilters(rows){
  const counts = {all: rows.length}; for (const r of rows) counts[r.status] = (counts[r.status]||0) + 1;
  if (filter !== "all" && !counts[filter]) filter = "all";
  const box = $("filters"); box.replaceChildren();
  for (const [k, label] of FILTERS) {
    if (k !== "all" && !counts[k]) continue;
    const b = el("button", null, label); b.type = "button"; b.setAttribute("aria-pressed", String(filter === k));
    b.appendChild(el("span", "n", String(counts[k]||0)));
    b.onclick = () => { filter = k; if (last) render(last); };
    box.appendChild(b);
  }
}

function repoRow(r){
  const row = el("div", "repo st-" + r.status + (r.status === "queued" ? " queued" : ""));
  const name = el("div", "name"); name.appendChild(sq()); name.appendChild(el("span", null, r.name));
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
  setText("ver", s.version ? "v" + s.version : ""); $("foot_ver").textContent = s.version ? "v" + s.version : "";
  const state = s.state || "idle";
  const sc = state === "running" ? "s-running" : /killed|exit|breaker/.test(state) ? "s-bad" : state === "stopped" ? "s-warn" : "";
  $("state").className = "chip " + sc; setText("state_t", state);
  const p = s.progress || 0;
  $("pct").firstChild.nodeValue = (p * 100).toFixed(p < 0.1 ? 1 : 0);
  $("pbar").style.width = p * 100 + "%";
  setText("prs", s.prs); setText("elapsed", s.elapsed); setText("eta", s.eta); setText("rate", s.rate);
  setText("repos", s.repos); setText("llm", s.llm); $("lat").textContent = s.llm_latency ? "avg " + s.llm_latency : "";
  setText("breaker", s.breaker ? "tripped" : (s.state ? "armed" : null)); setLvl("breaker", s.breaker ? "bad" : "ok");
  const r = s.resources || {};
  setText("ram", r.rss); $("peak").textContent = r.peak ? "peak " + r.peak : ""; meter("ram_t", r.rss_frac, r.level);
  setText("free", r.used_frac != null ? Math.round(r.used_frac * 100) + "% used" : null); setLvl("free", r.level === "ok" ? null : r.level);
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
""".replace("@@LOCKUP@@", LOCKUP_SVG).replace("@@FAVICON@@", FAVICON_B64)

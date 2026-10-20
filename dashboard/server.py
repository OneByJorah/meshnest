#!/usr/bin/env python3
"""MN Mesh dashboard server. Standard library only.

Serves a live latency comparison of every mesh VPN on this host.

Exposure model: loopback by default. `MN_BIND=tailscale` binds to the node's
Tailscale IPv4 so only your tailnet can reach it. Wildcard binds are refused.
Routes (all GET, read-only):  /  /api/state  /metrics  /healthz  + static assets.
"""

from __future__ import annotations

import ipaddress
import json
import os
import signal
import socket
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import collectors

ROOT = Path(__file__).resolve().parent
try:
    VERSION = (ROOT.parent / "VERSION").read_text().strip()
except OSError:
    VERSION = "dev"

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

HISTORY_LEN = 720          # ~1h at 5s
SERIES_LEN = 90
CGNAT = ipaddress.ip_network("100.64.0.0/10")
ULA = ipaddress.ip_network("fc00::/7")


def env_int(name, default, lo, hi):
    try:
        v = int(os.environ.get(name, default))
    except ValueError:
        return default
    return max(lo, min(hi, v))


BIND = (os.environ.get("MN_BIND") or "127.0.0.1").strip()
PORT = env_int("MN_PORT", 8787, 1, 65535)
INTERVAL = env_int("MN_INTERVAL", 5, 2, 300)
MAX_PEERS = env_int("MN_MAX_PEERS", 24, 1, 200)
PROVIDERS = [p.strip() for p in os.environ.get("MN_PROVIDERS", "tailscale,netbird,zerotier,warp").split(",") if p.strip()]
BASELINE = os.environ.get("MN_BASELINE", "1.1.1.1").strip()
STATE_DIR = Path(os.environ.get("MN_STATE", "/var/lib/meshnest"))

LOCK = threading.Lock()
STATE: dict = {"ready": False}
HISTORY: dict[str, deque] = {}


# --------------------------------------------------------------------------- bind safety

def bind_allowed(bind: str) -> bool:
    """Loopback, Tailscale/NetBird CGNAT and ULA addresses are fine; everything else needs an override."""
    if os.environ.get("MN_ALLOW_ANY_BIND") == "1":
        return bind not in ("0.0.0.0", "::", "")
    try:
        ip = ipaddress.ip_address(bind)
    except ValueError:
        return False
    return ip.is_loopback or ip in CGNAT or ip in ULA


def resolve_bind(spec: str) -> str:
    if spec != "tailscale":
        return spec
    deadline = time.time() + 120
    while time.time() < deadline:
        rc, out, _ = collectors.run(["tailscale", "ip", "-4"], 4)
        ip = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""
        if ip:
            return ip
        time.sleep(3)
    sys.exit("MN_BIND=tailscale but no Tailscale IPv4 appeared within 120s")


# --------------------------------------------------------------------------- host header guard

_host_cache = {"ts": 0.0, "names": set()}


def hostname_from_header(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def allowed_hosts(bind: str) -> set:
    now = time.time()
    if now - _host_cache["ts"] > 60:
        names = {"localhost", "127.0.0.1", "::1", bind.lower()}
        names.update(h.strip().lower() for h in os.environ.get("MN_ALLOWED_HOSTS", "").split(",") if h.strip())
        rc, out, _ = collectors.run(["tailscale", "status", "--json"], 4)
        if rc == 0 and out.strip():
            try:
                dns = ((json.loads(out).get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
                if dns:
                    names.add(dns)
            except ValueError:
                pass
        _host_cache.update(ts=now, names=names)
    return _host_cache["names"]


def host_ok(header: str, bind: str) -> bool:
    names = allowed_hosts(bind)
    return "*" in names or hostname_from_header(header) in names


# --------------------------------------------------------------------------- state

def load_history():
    try:
        raw = json.loads((STATE_DIR / "history.json").read_text())
    except (OSError, ValueError):
        return
    cutoff = time.time() - 3600
    for pid, rows in raw.items():
        d = deque(maxlen=HISTORY_LEN)
        for ts, med in rows:
            if ts >= cutoff:
                d.append((ts, med))
        HISTORY[pid] = d


def save_history():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_DIR / "history.json.tmp"
        with LOCK:
            payload = {pid: list(d) for pid, d in HISTORY.items()}
        tmp.write_text(json.dumps(payload))
        tmp.replace(STATE_DIR / "history.json")
    except OSError:
        pass


def update_state(snap: dict):
    now = snap["ts"]
    with LOCK:
        for s in snap["providers"] + ([snap["baseline"]] if snap.get("baseline") else []):
            med = s["stats"]["median"] if s["state"] == "connected" else None
            HISTORY.setdefault(s["id"], deque(maxlen=HISTORY_LEN)).append((now, med))
        series = {pid: [m for _, m in list(d)[-SERIES_LEN:]] for pid, d in HISTORY.items()}
        STATE.clear()
        STATE.update(
            ready=True,
            version=VERSION,
            host=os.environ.get("MN_HOSTNAME") or socket.gethostname(),
            interval=INTERVAL,
            series=series,
            **snap,
        )


def poller():
    last_save = time.time()
    while True:
        t0 = time.time()
        try:
            update_state(collectors.collect_all(PROVIDERS, BASELINE, MAX_PEERS))
        except Exception as exc:  # keep the loop alive no matter what
            print(f"[meshnest] collect error: {exc}", file=sys.stderr, flush=True)
        if time.time() - last_save > 60:
            save_history()
            last_save = time.time()
        time.sleep(max(0.5, INTERVAL - (time.time() - t0)))


# --------------------------------------------------------------------------- prometheus

def prom_escape(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics(state: dict) -> str:
    out = [
        "# HELP mn_mesh_up 1 when the mesh is connected.",
        "# TYPE mn_mesh_up gauge",
    ]
    provs = state.get("providers", [])
    for s in provs:
        out.append(f'mn_mesh_up{{provider="{prom_escape(s["id"])}"}} {1 if s["state"] == "connected" else 0}')
    out += ["# HELP mn_mesh_latency_ms Latency summary across peers, milliseconds.", "# TYPE mn_mesh_latency_ms gauge"]
    for s in provs + ([state["baseline"]] if state.get("baseline") else []):
        for stat in ("min", "avg", "median", "p95"):
            v = s["stats"].get(stat)
            if v is not None:
                out.append(f'mn_mesh_latency_ms{{provider="{prom_escape(s["id"])}",stat="{stat}"}} {v}')
    out += ["# HELP mn_mesh_loss_percent Probe loss across peers.", "# TYPE mn_mesh_loss_percent gauge"]
    for s in provs:
        v = s["stats"].get("loss")
        if v is not None:
            out.append(f'mn_mesh_loss_percent{{provider="{prom_escape(s["id"])}"}} {v}')
    out += ["# HELP mn_mesh_peers Peers seen online.", "# TYPE mn_mesh_peers gauge"]
    for s in provs:
        out.append(f'mn_mesh_peers{{provider="{prom_escape(s["id"])}"}} {s["stats"]["n"]}')
    out += ["# HELP mn_mesh_peer_latency_ms Per-peer latency, milliseconds.", "# TYPE mn_mesh_peer_latency_ms gauge"]
    for s in provs:
        for p in s["peers"]:
            if p.get("latency_ms") is not None:
                out.append(
                    f'mn_mesh_peer_latency_ms{{provider="{prom_escape(s["id"])}",peer="{prom_escape(p["name"])}",'
                    f'path="{prom_escape(p.get("path") or "unknown")}"}} {p["latency_ms"]}'
                )
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "meshnest"
    sys_version = ""
    bind_addr = "127.0.0.1"

    def log_message(self, fmt, *args):
        if os.environ.get("MN_ACCESS_LOG") == "1":
            super().log_message(fmt, *args)

    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not host_ok(self.headers.get("Host", ""), self.bind_addr):
            return self._send(403, b"forbidden host\n")
        path = self.path.split("?", 1)[0]
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                return self._send(200, (ROOT / "static" / name).read_bytes(), ctype)
            except OSError:
                return self._send(404, b"not found\n")
        if path == "/healthz":
            return self._send(200, b"ok\n")
        if path in ("/api/state", "/metrics"):
            with LOCK:
                snap = dict(STATE)
            if not snap.get("ready"):
                return self._send(503, b"warming up\n")
            if path == "/metrics":
                return self._send(200, render_metrics(snap).encode(), "text/plain; version=0.0.4; charset=utf-8")
            return self._send(200, json.dumps(snap).encode(), "application/json")
        return self._send(404, b"not found\n")


def main():
    bind = resolve_bind(BIND)
    if not bind_allowed(bind):
        sys.exit(
            f"refusing to bind to {bind!r}: only loopback, 'tailscale', or CGNAT/ULA mesh addresses are allowed.\n"
            "Set MN_ALLOW_ANY_BIND=1 to bind another specific address (wildcards are never allowed)."
        )
    load_history()
    Handler.bind_addr = bind

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in bind else socket.AF_INET
        daemon_threads = True

    httpd = Server((bind, PORT), Handler)

    def _stop(*_):
        save_history()
        os._exit(0)

    signal.signal(signal.SIGTERM, _stop)
    threading.Thread(target=poller, daemon=True).start()
    host = f"[{bind}]" if ":" in bind else bind
    print(f"[meshnest] dashboard v{VERSION} on http://{host}:{PORT} (providers: {','.join(PROVIDERS)})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        save_history()


if __name__ == "__main__":
    main()

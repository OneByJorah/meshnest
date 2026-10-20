"""Mesh collectors for j1-mesh-stack. Standard library only.

Every collector returns one *sample*:

    {
      "id": "tailscale", "name": "Tailscale",
      "state": "connected" | "disconnected" | "stopped" | "missing" | "error",
      "iface": str | None, "self_ip": str | None,
      "peers": [{"name", "ip", "latency_ms", "path", "probed"}],
      "stats": {"n", "ok", "min", "avg", "median", "p95", "loss", "direct", "relay"},
      "error": str | None, "ts": float,
    }

Nothing in this module opens a listening socket. All probing is outbound: vendor CLI
calls plus single ICMP echoes sent over each mesh interface.

Latency methodology (best available RTT per peer, ICMP preferred where an IP is known):
  tailscale  `tailscale ping` (WireGuard-layer RTT, also reports direct vs DERP relay)
  netbird    ICMP over wt0, falling back to the client's own measurement
  zerotier   ZeroTier's native per-peer latency (peers are addressed by ZeroTier ID)
  warp       ICMP to 1.1.1.1 bound to the CloudflareWARP interface, falling back to tunnel stats
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

_ENV = {**os.environ, "LC_ALL": "C"}
_SAFE_HOST = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9.:\-]*$")
_PING_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms")
_TS_PONG = re.compile(
    r"pong from .*? via (?P<via>\S+) in (?P<val>[\d.]+)\s*(?P<unit>ms|µs|us|s)\b"
)
_WARP_LAT = re.compile(r"latency[:\s]+([\d.]+)\s*ms", re.I)
_WARP_LOSS = re.compile(r"loss[:\s]+([\d.]+)\s*%", re.I)


# --------------------------------------------------------------------------- helpers

def run(cmd, timeout=6):
    """Run a command, never raise. Returns (returncode, stdout, stderr)."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False, env=_ENV
        )
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:
        return 126, "", str(exc)
    return p.returncode, p.stdout, p.stderr


def _r(value, nd=2):
    return None if value is None else round(float(value), nd)


def safe_host(host):
    return bool(host) and bool(_SAFE_HOST.match(host))


def icmp_ping(host, iface=None, timeout=2):
    """One ICMP echo. Returns RTT in ms, or None."""
    if not safe_host(host):
        return None
    cmd = ["ping", "-n", "-c", "1", "-W", str(timeout)]
    if iface:
        cmd += ["-I", iface]
    cmd.append(host)
    rc, out, _ = run(cmd, timeout + 2)
    m = _PING_RE.search(out)
    return float(m.group(1)) if rc == 0 and m else None


def percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def compute_stats(peers):
    lat = sorted(p["latency_ms"] for p in peers if p.get("latency_ms") is not None)
    probed = [p for p in peers if p.get("probed")]
    failed = [p for p in probed if p.get("latency_ms") is None]
    return {
        "n": len(peers),
        "ok": len(lat),
        "min": _r(lat[0]) if lat else None,
        "avg": _r(sum(lat) / len(lat)) if lat else None,
        "median": _r(statistics.median(lat)) if lat else None,
        "p95": _r(percentile(lat, 0.95)) if lat else None,
        "loss": round(100.0 * len(failed) / len(probed), 1) if probed else None,
        "direct": sum(1 for p in peers if p.get("path") == "direct"),
        "relay": sum(1 for p in peers if p.get("path") == "relay"),
    }


def new_sample(pid, name, iface=None):
    return {
        "id": pid,
        "name": name,
        "state": "missing",
        "iface": iface,
        "self_ip": None,
        "peers": [],
        "stats": compute_stats([]),
        "error": None,
        "ts": time.time(),
    }


def _finish(sample):
    sample["stats"] = compute_stats(sample["peers"])
    sample["ts"] = time.time()
    return sample


def _probe_many(items, fn, workers=16):
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


# --------------------------------------------------------------------------- tailscale

def parse_tailscale_status(j):
    ips = (j.get("Self") or {}).get("TailscaleIPs") or []
    self_ip = next((i for i in ips if "." in i), ips[0] if ips else None)
    peers = []
    for p in (j.get("Peer") or {}).values():
        if not p.get("Online"):
            continue
        pips = p.get("TailscaleIPs") or []
        ip = next((i for i in pips if "." in i), pips[0] if pips else None)
        if not ip:
            continue
        name = p.get("HostName") or (p.get("DNSName") or "").split(".")[0] or ip
        hint = "direct" if p.get("CurAddr") else "relay" if p.get("Relay") else "unknown"
        peers.append({"name": name, "ip": ip, "hint": hint})
    peers.sort(key=lambda x: x["name"].lower())
    return self_ip, peers


def parse_tailscale_ping(text):
    """-> (latency_ms | None, 'direct' | 'relay' | None)"""
    m = _TS_PONG.search(text or "")
    if not m:
        return None, None
    val = float(m.group("val"))
    unit = m.group("unit")
    ms = val * 1000.0 if unit == "s" else val / 1000.0 if unit in ("µs", "us") else val
    via = m.group("via")
    path = "relay" if via.startswith(("DERP", "peer-relay")) else "direct"
    return ms, path


def collect_tailscale(max_peers=24):
    s = new_sample("tailscale", "Tailscale", "tailscale0")
    if not shutil.which("tailscale"):
        return s
    rc, out, err = run(["tailscale", "status", "--json"])
    if rc != 0 and not out.strip():
        s["state"] = "stopped"
        s["error"] = (err.strip() or "tailscaled is not running")[:160]
        return s
    try:
        j = json.loads(out)
    except ValueError:
        s["state"] = "error"
        s["error"] = "unparseable `tailscale status --json` output"
        return s
    backend = j.get("BackendState")
    if backend != "Running":
        s["state"] = "disconnected"
        s["error"] = str(backend or "unknown")[:80]
        return s

    s["state"] = "connected"
    s["self_ip"], peers = parse_tailscale_status(j)

    def probe(peer):
        _, out2, _ = run(
            ["tailscale", "ping", "-c", "1", "--until-direct=false", "--timeout", "2s", peer["ip"]],
            timeout=6,
        )
        ms, path = parse_tailscale_ping(out2)
        if ms is None:
            ms = icmp_ping(peer["ip"], "tailscale0")
        return {
            "name": peer["name"],
            "ip": peer["ip"],
            "latency_ms": _r(ms),
            "path": path or peer["hint"],
            "probed": True,
        }

    s["peers"] = _probe_many(peers[:max_peers], probe)
    return _finish(s)


# --------------------------------------------------------------------------- netbird

def parse_netbird_status(j):
    """-> (management_connected, self_ip, peers[{name, ip, native_ms, path}])"""
    connected = bool((j.get("management") or {}).get("connected"))
    self_ip = (j.get("netbirdIp") or "").split("/")[0] or None
    peers = []
    for d in (j.get("peers") or {}).get("details") or []:
        if str(d.get("status", "")).lower() != "connected":
            continue
        ns = d.get("latency")
        native = ns / 1e6 if isinstance(ns, (int, float)) and ns > 0 else None
        ctype = str(d.get("connectionType", "")).lower()
        path = "direct" if ctype in ("p2p", "direct") else "relay" if ctype.startswith("relay") else "unknown"
        ip = (d.get("netbirdIp") or "").split("/")[0] or None
        name = (d.get("fqdn") or "").split(".")[0] or ip or "peer"
        peers.append({"name": name, "ip": ip, "native_ms": native, "path": path})
    peers.sort(key=lambda x: x["name"].lower())
    return connected, self_ip, peers


def collect_netbird(max_peers=24):
    s = new_sample("netbird", "NetBird", "wt0")
    if not shutil.which("netbird"):
        return s
    rc, out, err = run(["netbird", "status", "--json"], timeout=8)
    if rc != 0 or not out.strip():
        s["state"] = "stopped"
        s["error"] = (err.strip() or "netbird daemon not running")[:160]
        return s
    try:
        j = json.loads(out)
    except ValueError:
        s["state"] = "error"
        s["error"] = "unparseable `netbird status --json` output"
        return s
    connected, s["self_ip"], peers = parse_netbird_status(j)
    if not connected:
        s["state"] = "disconnected"
        s["error"] = "management server not connected"
        return s
    s["state"] = "connected"

    def probe(peer):
        ms = icmp_ping(peer["ip"], "wt0") if peer["ip"] else None
        if ms is None:
            ms = peer["native_ms"]
        return {
            "name": peer["name"],
            "ip": peer["ip"],
            "latency_ms": _r(ms),
            "path": peer["path"],
            "probed": True,
        }

    s["peers"] = _probe_many(peers[:max_peers], probe)
    return _finish(s)


# --------------------------------------------------------------------------- zerotier

def parse_zerotier(networks, peers_json):
    """-> (state_ok, self_ip, iface, peers[{name, ip, latency_ms, path, probed}])"""
    ok_nets = [n for n in (networks or []) if n.get("status") == "OK"]
    self_ip = iface = None
    if ok_nets:
        iface = ok_nets[0].get("portDeviceName")
        addrs = ok_nets[0].get("assignedAddresses") or []
        self_ip = addrs[0].split("/")[0] if addrs else None
    peers = []
    for p in peers_json or []:
        if p.get("role") != "LEAF":
            continue
        if not any(x.get("active") or x.get("preferred") for x in (p.get("paths") or [])):
            continue
        lat = p.get("latency")
        lat = float(lat) if isinstance(lat, (int, float)) and lat >= 0 else None
        peers.append(
            {"name": p.get("address", "peer"), "ip": None, "latency_ms": _r(lat), "path": "direct", "probed": False}
        )
    peers.sort(key=lambda x: x["name"])
    return bool(ok_nets), self_ip, iface, peers


def collect_zerotier(max_peers=24):
    s = new_sample("zerotier", "ZeroTier", None)
    if not shutil.which("zerotier-cli"):
        return s
    rc, out, err = run(["zerotier-cli", "-j", "listnetworks"])
    if rc != 0:
        s["state"] = "stopped"
        s["error"] = (err.strip() or out.strip() or "zerotier-one not running")[:160]
        return s
    try:
        networks = json.loads(out)
        rc2, out2, _ = run(["zerotier-cli", "-j", "peers"])
        peers_json = json.loads(out2) if rc2 == 0 and out2.strip() else []
    except ValueError:
        s["state"] = "error"
        s["error"] = "unparseable zerotier-cli JSON"
        return s
    ok_state, s["self_ip"], s["iface"], peers = parse_zerotier(networks, peers_json)
    if not ok_state:
        s["state"] = "disconnected"
        s["error"] = "no network in state OK (authorized?)"
        return s
    s["state"] = "connected"
    s["peers"] = peers[:max_peers]
    return _finish(s)


# --------------------------------------------------------------------------- warp

def parse_warp_stats(text):
    lat = _WARP_LAT.search(text or "")
    loss = _WARP_LOSS.search(text or "")
    return (float(lat.group(1)) if lat else None, float(loss.group(1)) if loss else None)


def collect_warp(max_peers=24):  # noqa: ARG001 - uniform signature
    s = new_sample("warp", "Cloudflare WARP", "CloudflareWARP")
    if not shutil.which("warp-cli"):
        return s
    rc, out, err = run(["warp-cli", "--accept-tos", "status"])
    if rc != 0:
        s["state"] = "stopped"
        s["error"] = (err.strip() or out.strip() or "warp-svc not running")[:160]
        return s
    if "status update: connected" not in out.lower():
        s["state"] = "disconnected"
        s["error"] = "WARP not connected"
        return s
    s["state"] = "connected"
    ms = icmp_ping("1.1.1.1", "CloudflareWARP")
    if ms is None:
        _, stats_out, _ = run(["warp-cli", "--accept-tos", "tunnel", "stats"])
        ms, _loss = parse_warp_stats(stats_out)
    s["peers"] = [
        {"name": "Cloudflare edge", "ip": "1.1.1.1", "latency_ms": _r(ms), "path": "tunnel", "probed": True}
    ]
    return _finish(s)


# --------------------------------------------------------------------------- baseline + driver

def collect_baseline(target="1.1.1.1"):
    s = new_sample("baseline", "Direct internet", None)
    if not safe_host(target):
        s["state"] = "error"
        s["error"] = "invalid J1_MESH_BASELINE"
        return s
    ms = icmp_ping(target)
    s["state"] = "connected" if ms is not None else "disconnected"
    s["peers"] = [{"name": target, "ip": target, "latency_ms": _r(ms), "path": "direct", "probed": True}]
    return _finish(s)


COLLECTORS = {
    "tailscale": ("Tailscale", collect_tailscale),
    "netbird": ("NetBird", collect_netbird),
    "zerotier": ("ZeroTier", collect_zerotier),
    "warp": ("Cloudflare WARP", collect_warp),
}


def collect_all(enabled, baseline="1.1.1.1", max_peers=24):
    ids = [p for p in enabled if p in COLLECTORS]
    results = {}
    base = None
    with ThreadPoolExecutor(max_workers=len(ids) + 1) as ex:
        futures = {pid: ex.submit(COLLECTORS[pid][1], max_peers) for pid in ids}
        bfut = ex.submit(collect_baseline, baseline) if baseline else None
        for pid, fut in futures.items():
            try:
                results[pid] = fut.result()
            except Exception as exc:  # collector bugs must never take the dashboard down
                sample = new_sample(pid, COLLECTORS[pid][0])
                sample["state"] = "error"
                sample["error"] = f"{type(exc).__name__}: {exc}"[:160]
                results[pid] = sample
        if bfut:
            base = bfut.result()
    providers = [results[p] for p in ids]
    ranked = sorted(
        (s for s in providers if s["state"] == "connected" and s["stats"]["median"] is not None),
        key=lambda s: s["stats"]["median"],
    )
    return {
        "ts": time.time(),
        "providers": providers,
        "baseline": base,
        "ranking": [s["id"] for s in ranked],
        "fastest": ranked[0]["id"] if ranked else None,
    }

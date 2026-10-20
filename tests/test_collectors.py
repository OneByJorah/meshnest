import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

import collectors as c  # noqa: E402
import server as srv  # noqa: E402


class TailscaleTests(unittest.TestCase):
    def test_ping_derp_ms(self):
        ms, path = c.parse_tailscale_ping("pong from box (100.64.0.2) via DERP(nyc) in 32ms\n")
        self.assertEqual((ms, path), (32.0, "relay"))

    def test_ping_direct_micro(self):
        ms, path = c.parse_tailscale_ping("pong from box (100.64.0.2) via 192.168.1.5:41641 in 850µs\n")
        self.assertAlmostEqual(ms, 0.85)
        self.assertEqual(path, "direct")

    def test_ping_timeout(self):
        self.assertEqual(c.parse_tailscale_ping('ping "x" timed out'), (None, None))

    def test_status_skips_offline(self):
        j = {
            "Self": {"TailscaleIPs": ["100.64.0.1", "fd7a::1"]},
            "Peer": {
                "a": {"Online": True, "HostName": "alpha", "TailscaleIPs": ["100.64.0.2"], "CurAddr": "1.2.3.4:5"},
                "b": {"Online": False, "HostName": "beta", "TailscaleIPs": ["100.64.0.3"]},
                "c": {"Online": True, "HostName": "gamma", "TailscaleIPs": ["100.64.0.4"], "Relay": "nyc"},
            },
        }
        self_ip, peers = c.parse_tailscale_status(j)
        self.assertEqual(self_ip, "100.64.0.1")
        self.assertEqual([(p["name"], p["hint"]) for p in peers], [("alpha", "direct"), ("gamma", "relay")])


class OtherParsers(unittest.TestCase):
    def test_netbird(self):
        j = {
            "management": {"connected": True},
            "netbirdIp": "100.90.1.1/16",
            "peers": {
                "details": [
                    {"fqdn": "web.netbird.cloud", "netbirdIp": "100.90.1.2", "status": "Connected",
                     "connectionType": "P2P", "latency": 12_500_000},
                    {"fqdn": "db.netbird.cloud", "netbirdIp": "100.90.1.3", "status": "Idle"},
                ]
            },
        }
        connected, ip, peers = c.parse_netbird_status(j)
        self.assertTrue(connected)
        self.assertEqual(ip, "100.90.1.1")
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["name"], "web")
        self.assertAlmostEqual(peers[0]["native_ms"], 12.5)
        self.assertEqual(peers[0]["path"], "direct")

    def test_zerotier(self):
        nets = [{"status": "OK", "portDeviceName": "ztabc", "assignedAddresses": ["10.1.2.3/24"]}]
        peers = [
            {"address": "aaaaaaaaaa", "role": "LEAF", "latency": 20, "paths": [{"active": True}]},
            {"address": "bbbbbbbbbb", "role": "LEAF", "latency": -1, "paths": []},
            {"address": "cccccccccc", "role": "PLANET", "latency": 5, "paths": [{"active": True}]},
        ]
        ok, ip, iface, out = c.parse_zerotier(nets, peers)
        self.assertTrue(ok)
        self.assertEqual((ip, iface), ("10.1.2.3", "ztabc"))
        self.assertEqual([p["name"] for p in out], ["aaaaaaaaaa"])
        self.assertEqual(out[0]["latency_ms"], 20.0)

    def test_zerotier_not_authorized(self):
        ok, *_ = c.parse_zerotier([{"status": "ACCESS_DENIED"}], [])
        self.assertFalse(ok)

    def test_warp_stats(self):
        self.assertEqual(c.parse_warp_stats("Estimated loss: 0.50%\nLatency: 12ms\n"), (12.0, 0.5))
        self.assertEqual(c.parse_warp_stats("nothing"), (None, None))


class StatsTests(unittest.TestCase):
    def test_stats(self):
        peers = [
            {"latency_ms": 10.0, "path": "direct", "probed": True},
            {"latency_ms": 30.0, "path": "relay", "probed": True},
            {"latency_ms": None, "path": "direct", "probed": True},
        ]
        s = c.compute_stats(peers)
        self.assertEqual((s["n"], s["ok"], s["min"], s["median"]), (3, 2, 10.0, 20.0))
        self.assertAlmostEqual(s["loss"], 33.3)
        self.assertEqual((s["direct"], s["relay"]), (2, 1))

    def test_empty(self):
        s = c.compute_stats([])
        self.assertIsNone(s["median"])
        self.assertIsNone(s["loss"])

    def test_unprobed_has_no_loss(self):
        s = c.compute_stats([{"latency_ms": None, "probed": False}])
        self.assertIsNone(s["loss"])

    def test_safe_host(self):
        self.assertTrue(c.safe_host("1.1.1.1"))
        self.assertTrue(c.safe_host("fd7a::1"))
        self.assertFalse(c.safe_host("-f"))
        self.assertFalse(c.safe_host("a b"))
        self.assertFalse(c.safe_host(""))


class ServerTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("J1_MESH_ALLOW_ANY_BIND", None)

    def test_bind_policy(self):
        for ok in ("127.0.0.1", "::1", "100.101.102.103", "fd7a:115c:a1e0::1"):
            self.assertTrue(srv.bind_allowed(ok), ok)
        for bad in ("0.0.0.0", "::", "", "192.168.1.5", "8.8.8.8", "example.com"):
            self.assertFalse(srv.bind_allowed(bad), bad)

    def test_bind_override_never_allows_wildcard(self):
        os.environ["J1_MESH_ALLOW_ANY_BIND"] = "1"
        try:
            self.assertTrue(srv.bind_allowed("192.168.1.5"))
            self.assertFalse(srv.bind_allowed("0.0.0.0"))
            self.assertFalse(srv.bind_allowed("::"))
        finally:
            os.environ.pop("J1_MESH_ALLOW_ANY_BIND", None)

    def test_hostname_parse(self):
        self.assertEqual(srv.hostname_from_header("Localhost:8787"), "localhost")
        self.assertEqual(srv.hostname_from_header("[::1]:8787"), "::1")
        self.assertEqual(srv.hostname_from_header("box.tail1234.ts.net"), "box.tail1234.ts.net")

    def test_metrics_escaping(self):
        state = {
            "providers": [
                {"id": "tailscale", "state": "connected",
                 "stats": {"min": 1, "avg": 2, "median": 2, "p95": 3, "loss": 0.0, "n": 1},
                 "peers": [{"name": 'ev"il\nname', "latency_ms": 2.0, "path": "direct"}]}
            ],
            "baseline": None,
        }
        text = srv.render_metrics(state)
        self.assertIn('j1_mesh_up{provider="tailscale"} 1', text)
        self.assertIn('peer="ev\\"il\\nname"', text)
        self.assertNotIn('ev"il', text)


if __name__ == "__main__":
    unittest.main()

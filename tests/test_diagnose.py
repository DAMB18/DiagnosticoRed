"""Pruebas del motor de diagnostico con datos sinteticos (no tocan la red)."""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import diagnostico_red as dr  # noqa: E402


def make_data(**over):
    adapter = {"Name": "Wi-Fi", "Desc": "Intel Wireless", "Status": "Up", "Virtual": False,
               "Media": "Native 802.11", "IPv4": ["192.168.1.50"], "Gateway": ["192.168.1.1"],
               "Dns": ["1.1.1.1"], "PowerSave": "", "Adv": [], "DriverDate": "", "DriverProvider": "Intel",
               "Driver": "1", "Speed": "866 Mbps", "Dhcp": "Enabled", "Metric": 35, "Mac": "AA-BB-CC-DD-EE-FF"}
    d = {
        "when": "2026-01-01 00:00:00", "since": datetime.now() - timedelta(days=7),
        "window": "en 7 dias", "adapters": [adapter], "events": [],
        "wifi": {"signal": 90, "band": "5 GHz", "channel": 44, "radio": "802.11ac", "neighbors": []},
        "tests": {
            "ping_gw": {"host": "192.168.1.1", "loss": 0.0, "min": 1, "max": 5, "avg": 2, "jitter": 1},
            "ping_cf": {"host": "1.1.1.1", "loss": 0.0, "min": 8, "max": 20, "avg": 12, "jitter": 2},
            "ping_g": {"host": "8.8.8.8", "loss": 0.0, "min": 28, "max": 38, "avg": 30, "jitter": 2},
            "dns": {"ok": True, "ms": 20}, "http": {"ok": True, "ms": 100}},
        "dns_speed": {"1.1.1.1": 100},
    }
    d.update(over)
    return d


def levels(d):
    return [lvl for lvl, _, _ in dr.diagnose(d)]


class DiagnoseTests(unittest.TestCase):
    def test_healthy_network_has_no_failures(self):
        self.assertNotIn(dr.BAD, levels(make_data()))
        self.assertNotIn(dr.WARN, levels(make_data()))

    def test_no_adapter_up_is_bad(self):
        d = make_data()
        d["adapters"][0]["Status"] = "Disconnected"
        self.assertEqual(levels(d), [dr.BAD])

    def test_apipa_address_is_bad(self):
        d = make_data()
        d["adapters"][0]["IPv4"] = ["169.254.3.4"]
        self.assertIn(dr.BAD, levels(d))

    def test_local_link_loss_is_bad(self):
        d = make_data()
        d["tests"]["ping_gw"]["loss"] = 10.0
        self.assertIn(dr.BAD, levels(d))

    def test_internet_loss_with_healthy_router_is_bad(self):
        d = make_data()
        d["tests"]["ping_cf"]["loss"] = 15.0
        self.assertIn(dr.BAD, levels(d))

    def test_dns_failure_is_bad(self):
        d = make_data()
        d["tests"]["dns"] = {"ok": False, "ms": 100}
        self.assertIn(dr.BAD, levels(d))

    def test_2_4ghz_warns(self):
        d = make_data()
        d["wifi"].update(band="2.4 GHz", channel=1)
        self.assertIn(dr.WARN, levels(d))

    def test_driver_initiated_disconnects_are_bad(self):
        ev = [{"Log": "WLAN", "Id": 8003, "Time": "2026-01-01T00:00:00",
               "Msg": "disconnected. Reason: The network is disconnected by the driver."}] * 5
        self.assertIn(dr.BAD, levels(make_data(events=ev)))

    def test_manual_disconnects_are_not_bad(self):
        ev = [{"Log": "WLAN", "Id": 8003, "Time": "2026-01-01T00:00:00",
               "Msg": "Reason: The network is disconnected by the user."}] * 6
        self.assertNotIn(dr.BAD, levels(make_data(events=ev)))

    def test_short_window_uses_lower_threshold(self):
        ev = [{"Log": "WLAN", "Id": 8003, "Time": "2026-01-01T00:00:00",
               "Msg": "Reason: The network is disconnected by the driver."}] * 2
        d = make_data(events=ev, since=datetime.now() - timedelta(hours=2))
        self.assertIn(dr.BAD, levels(d))


class ParseSinceTests(unittest.TestCase):
    def test_presets(self):
        s = dr.parse_since("Ultima hora")
        self.assertAlmostEqual((datetime.now() - s).total_seconds(), 3600, delta=5)

    def test_explicit_datetime(self):
        self.assertEqual(dr.parse_since("2026-09-24 15:58"), datetime(2026, 9, 24, 15, 58))

    def test_date_only(self):
        self.assertEqual(dr.parse_since("2026-09-24"), datetime(2026, 9, 24))

    def test_invalid(self):
        self.assertIsNone(dr.parse_since("ayer"))


class ReportTests(unittest.TestCase):
    def test_report_verdict_matches_findings(self):
        v, _, lines = dr.build_report(make_data())
        self.assertEqual(v, dr.OK)
        self.assertTrue(any("ADAPTADORES" in s for _, s in lines))


if __name__ == "__main__":
    unittest.main()

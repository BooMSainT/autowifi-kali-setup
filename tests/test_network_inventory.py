"""Regression tests for managed Wi-Fi discovery and post-scan menu choices."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
inv_spec = importlib.util.spec_from_file_location(
    "autowifi_inventory_test", ROOT / "extensions/network_inventory.py"
)
inventory = importlib.util.module_from_spec(inv_spec)
inv_spec.loader.exec_module(inventory)
menu_spec = importlib.util.spec_from_file_location(
    "autowifi_menu_picker_test", ROOT / "extensions/simple_menu.py"
)
menu = importlib.util.module_from_spec(menu_spec)
menu_spec.loader.exec_module(menu)


class InventoryTests(unittest.TestCase):
    def test_nmcli_escaped_bssid_and_colon_in_ssid(self):
        example = (
            r"Home\:Cameras:AA\:BB\:CC\:DD\:EE\:01:6:2437 MHz:82:WPA2" + "\n" +
            r"Guest:AA\:BB\:CC\:DD\:EE\:02:11:2462 MHz:47:WPA3" + "\n"
        )
        result = inventory.parse_nmcli(example)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["ssid"], "Home:Cameras")
        self.assertEqual(result[0]["bssid"], "AA:BB:CC:DD:EE:01")
        self.assertEqual(result[0]["signal_percent"], 82)
        self.assertEqual(result[1]["ssid"], "Guest")

    def test_dedup_and_reject_unverified_rows(self):
        data = (
            r"WiFi:11\:22\:33\:44\:55\:66:1:2412 MHz:40:WPA2" + "\n" +
            r"WiFi:11\:22\:33\:44\:55\:66:1:2412 MHz:80:WPA2" + "\n" +
            r"Bad:NOT-A-MAC:1:2412 MHz:99:WPA2" + "\n"
        )
        self.assertEqual(len(inventory.parse_nmcli(data)), 1)
        self.assertEqual(inventory.parse_nmcli(data)[0]["signal_percent"], 80)

    def test_scanner_never_uses_monitor_mode_or_attacks(self):
        result = SimpleNamespace(
            returncode=0, stderr="",
            stdout=r"Home:AA\:BB\:CC\:DD\:EE\:01:6:2437 MHz:79:WPA2",
        )
        with patch.object(inventory.subprocess, "run", return_value=result) as run:
            aps, error = inventory.scan("wlan2")
        self.assertEqual(error, "")
        self.assertEqual(len(aps), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "nmcli")
        self.assertIn("--rescan", command)
        self.assertNotIn("airmon-ng", command)
        self.assertNotIn("deauth", " ".join(command))

    def test_failed_scan_is_not_reported_as_zero_networks(self):
        with patch.object(inventory.subprocess, "run", return_value=SimpleNamespace(
            returncode=10, stdout="", stderr="NetworkManager is not managing wlan2"
        )):
            aps, error = inventory.scan("wlan2")
        self.assertEqual(aps, [])
        self.assertIn("not managing", error)
        rendered = inventory.format_results("wlan2", aps, error)
        self.assertIn("unknown (scan failed)", rendered)
        self.assertNotIn("Access points detected: 0", rendered)

    def test_report_is_compatible_with_existing_history_parser(self):
        aps = [{
            "ssid": "Home", "bssid": "AA:BB:CC:DD:EE:FF", "channel": 6,
            "frequency": "2437 MHz", "signal_percent": 70, "security": "WPA2",
        }]
        rendered = inventory.format_results("wlan2", aps)
        result = menu.parse_report_summary("wifi", rendered)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["access_points"][0]["ssid"], "Home")

    def test_numbered_postscan_selection_and_explicit_ownership(self):
        report = {"access_points": [{
            "ssid": "MyGuest", "bssid": "AA:BB:CC:DD:EE:FF",
            "channel": "11", "power": "65%", "encryption": "WPA2",
        }]}
        with patch("builtins.input", side_effect=["1", "a", "b"]), \
             patch.object(menu, "_save_scanned_home_ssid", return_value=True) as save:
            self.assertEqual(menu.post_scan_choices(report), 0)
            save.assert_called_once_with(report["access_points"][0])

    def test_no_networks_still_has_diagnostics_and_back(self):
        with patch("builtins.input", side_effect=["d", "b"]), \
             patch.object(menu, "doctor", return_value=0) as doctor:
            self.assertEqual(menu.post_scan_choices({"access_points": []}), 0)
            doctor.assert_called_once_with()

    def test_hidden_network_never_added(self):
        self.assertFalse(menu._save_scanned_home_ssid({"ssid": "<hidden>"}))

    def test_radio_is_not_changed_for_nmcli_inventory(self):
        with patch.object(menu, "load_settings", return_value={"wireless": "wlan0", "lan": ""}), \
             patch.object(menu, "adapters", return_value=[
                 {"name": "wlan0", "mode": "managed", "is_ralink": False},
             ]), patch.object(menu, "default_route", return_value="eth0"):
            self.assertEqual(menu.choose_inventory_radio(), "wlan0")

    def test_menu_rejects_monitor_radio_for_managed_scan(self):
        with patch.object(menu, "load_settings", return_value={"wireless": "wlan2", "lan": ""}), \
             patch.object(menu, "adapters", return_value=[
                 {"name": "wlan2", "mode": "monitor", "is_ralink": True},
             ]):
            self.assertIsNone(menu.choose_inventory_radio())


if __name__ == "__main__":
    unittest.main()

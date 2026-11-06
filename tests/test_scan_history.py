"""Read-only Wi-Fi interface checks and private report history regression tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("menu_diagnostics_test", BASE / "extensions/simple_menu.py")
menu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menu)


class ScanHistoryTests(unittest.TestCase):
    def test_report_summary_only_counts_real_deterministic_output(self):
        text = (
            "Interface: wlan2mon\nAccess points detected: 2\n\n"
            "ACCESS POINTS — strongest observed power first\n"
            "POWER\tBSSID\tCHANNEL\tENCRYPTION\tSSID\n"
            "-53 dBm\tAA:BB:CC:DD:EE:01\t1\tWPA2\tHome-2G\n"
            "-68 dBm\tAA:BB:CC:DD:EE:02\t11\tWPA2\tGuest, Special\n"
            "Power is relative signal strength.\n"
        )
        report = menu.parse_report_summary("wifi", text)
        self.assertEqual(report["count"], 2)
        self.assertEqual([a["ssid"] for a in report["access_points"]],
                         ["Home-2G", "Guest, Special"])
        self.assertIsNone(menu.parse_report_summary("wifi", "AI says 5 networks")["count"])
        self.assertEqual(menu.parse_report_summary("lan", "Devices: 4")["count"], 4)

    def test_saved_reports_are_private(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "reports"
            path = menu._write_report(
                {"version": 1, "kind": "wifi", "count": 2, "raw_output": "test"},
                directory,
            )
            self.assertEqual(json.loads(path.read_text())["count"], 2)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_view_saved_report_without_running_any_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "reports"
            menu._write_report({"version": 1, "kind": "wifi", "count": 2,
                                "timestamp": "test", "interface": "wlan2",
                                "exit_code": 0, "raw_output": "REAL OBSERVATIONS"},
                               directory)
            with patch.object(menu, "REPORT_DIR", directory), \
                 patch("builtins.input", return_value="1"), \
                 patch.object(menu.subprocess, "run") as command:
                self.assertEqual(menu.recent_reports(), 0)
                command.assert_not_called()

    def test_failed_link_probe_is_unknown_and_not_safe(self):
        for status in (1, 127):
            with self.subTest(status=status), patch.object(
                    menu.subprocess, "run", return_value=SimpleNamespace(
                        returncode=status, stdout="", stderr="iw failure")):
                self.assertEqual(menu._probe_wifi_link("wlan2"), "unknown")
        with patch.object(menu.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0, stdout="Not connected.")):
            self.assertEqual(menu._probe_wifi_link("wlan2"), "disconnected")
        with patch.object(menu.subprocess, "run",
                          return_value=SimpleNamespace(
                              returncode=0, stdout="Connected to 00:11:22:33:44:55")):
            self.assertEqual(menu._probe_wifi_link("wlan2"), "connected")

    def test_unknown_radio_link_refuses_monitor_scan(self):
        with patch.object(menu, "adapters", return_value=[
            {"name": "wlan2", "is_ralink": True, "mode": "managed"}
        ]), patch.object(menu, "load_settings",
                        return_value={"lan": "", "wireless": "wlan2"}), \
             patch.object(menu, "default_route", return_value="eth0"), \
             patch.object(menu, "_probe_wifi_link", return_value="unknown"):
            self.assertIsNone(menu.choose_survey_radio())

    def test_phy_capability_is_specific_to_selected_radio(self):
        outputs = [
            "Interface wlan2\n\twiphy 2\n",
            "Wiphy phy2\nBand 1:\n * 2412 MHz [1]\n * 2437 MHz [6]\n",
        ]
        with patch.object(menu, "_capture", side_effect=outputs) as capture:
            self.assertEqual(menu.radio_bands("wlan2"), ["2.4 GHz"])
            self.assertEqual(capture.call_args_list[1].args[0],
                             ["iw", "phy", "phy2", "info"])

    def test_recorded_tool_streams_output_and_writes_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "autowifi-direct").write_text("#!/bin/sh\n")
            report_dir = root / "reports"
            proc = Mock()
            proc.stdout = [
                "Interface: wlan2mon\n",
                "Access points detected: 1\n",
                "POWER\tBSSID\tCHANNEL\tENCRYPTION\tSSID\n",
                "-55 dBm\tAA:BB:CC:DD:EE:FF\t6\tWPA2\tHome\n",
            ]
            proc.poll.return_value = 0
            proc.wait.return_value = 0
            with patch.object(menu, "BIN_DIR", bin_dir), \
                 patch.object(menu, "REPORT_DIR", report_dir), \
                 patch.object(menu.subprocess, "Popen", return_value=proc):
                self.assertEqual(menu._run_recorded(
                    "autowifi-direct", "networks", "--interface", "wlan2",
                    kind="wifi", interface="wlan2",
                ), 0)
            reports = list(report_dir.glob("*.json"))
            self.assertEqual(len(reports), 1)
            data = json.loads(reports[0].read_text())
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["access_points"][0]["ssid"], "Home")
            self.assertEqual(data["interface"], "wlan2")

    def test_both_new_menu_items_exist(self):
        self.assertIn(("9", "Diagnose adapters, connectivity and missing tools", "doctor"),
                      menu.OPTIONS)
        self.assertIn(("10", "View saved LAN and Wi-Fi scan reports", "history"),
                      menu.OPTIONS)


if __name__ == "__main__":
    unittest.main()

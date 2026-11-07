"""Offline behavioral checks for the autonomous, non-disruptive AutoWIFI workflow."""
from __future__ import annotations

import io
from contextlib import redirect_stdout
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


EXT = Path(__file__).resolve().parents[1] / "extensions"
sys.path.insert(0, str(EXT))
spec = importlib.util.spec_from_file_location("autowifi_autopilot_tests", EXT / "autopilot.py")
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class AutopilotTests(unittest.TestCase):
    def test_usb_ralink_selected_without_wlan_number_guess(self):
        radio = {"name": "wlan9", "mode": "managed",
                 "is_ralink": True, "usb_id": "148f:3070"}
        with patch.object(auto.menu, "adapters", return_value=[radio]), \
             patch.object(auto.menu, "load_settings",
                          return_value={"lan": "", "wireless": ""}):
            selected, error = auto._selected_radio()
        self.assertEqual(error, "")
        self.assertEqual(selected["name"], "wlan9")

    def test_stale_or_absent_usb_radio_never_falls_back(self):
        radios = [{"name": "wlan0", "mode": "managed",
                   "is_ralink": False, "usb_id": "internal"}]
        with patch.object(auto.menu, "adapters", return_value=radios), \
             patch.object(auto.menu, "load_settings",
                          return_value={"lan": "", "wireless": ""}):
            selected, error = auto._selected_radio()
        self.assertIsNone(selected)
        self.assertIn("No registered Ralink", error)
        with patch.object(auto.menu, "adapters", return_value=radios), \
             patch.object(auto.menu, "load_settings",
                          return_value={"lan": "", "wireless": "wlan2"}):
            selected, error = auto._selected_radio()
        self.assertIsNone(selected)
        self.assertIn("not present", error)

    def test_wifi_empty_first_pass_retries_once_and_saves_real_observations(self):
        point = {
            "ssid": "my-home", "bssid": "AA:BB:CC:DD:EE:01",
            "channel": 6, "frequency": "2437 MHz",
            "signal_percent": 74, "security": "WPA2",
        }
        selected = {"name": "wlan2", "mode": "managed",
                    "usb_id": "148f:3070"}
        with patch.object(auto, "_selected_radio", return_value=(selected, "")), \
             patch.object(auto, "_nm_state", return_value="30 (disconnected)"), \
             patch.object(auto.shutil, "which", return_value="/usr/bin/nmcli"), \
             patch.object(auto.wifi, "scan", side_effect=[
                 ([], ""), ([point], "")
             ]) as scan, \
             patch.object(auto.menu, "_write_report",
                          return_value=Path("/private/wifi.json")) as save, \
             redirect_stdout(io.StringIO()):
            result = auto.wifi_task()
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["retry"])
        self.assertEqual(result["interface"], "wlan2")
        self.assertEqual(scan.call_count, 2)
        snapshot = save.call_args.args[0]
        self.assertEqual(snapshot["access_points"][0]["ssid"], "my-home")
        self.assertEqual(snapshot["count"], 1)

    def test_zero_aps_after_bounded_retry_is_inconclusive_not_success(self):
        selected = {"name": "wlan2", "mode": "managed", "usb_id": "148f:3070"}
        with patch.object(auto, "_selected_radio", return_value=(selected, "")), \
             patch.object(auto, "_nm_state", return_value="30 (disconnected)"), \
             patch.object(auto.shutil, "which", return_value="/usr/bin/nmcli"), \
             patch.object(auto.wifi, "scan", return_value=([], "")) as scan, \
             patch.object(auto.menu, "_write_report",
                          return_value=Path("/private/wifi.json")), \
             redirect_stdout(io.StringIO()):
            result = auto.wifi_task()
        self.assertEqual(result["state"], "empty")
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["retry"])
        self.assertEqual(scan.call_count, 2)

    def test_wifi_scan_failure_is_not_a_successful_zero_network_result(self):
        selected = {"name": "wlan2", "mode": "managed", "usb_id": "148f:3070"}
        with patch.object(auto, "_selected_radio", return_value=(selected, "")), \
             patch.object(auto, "_nm_state", return_value="30 (disconnected)"), \
             patch.object(auto.shutil, "which", return_value="/usr/bin/nmcli"), \
             patch.object(auto.wifi, "scan",
                          return_value=([], "NetworkManager unavailable")) as scan, \
             patch.object(auto.menu, "_write_report",
                          return_value=Path("/private/wifi.json")), \
             redirect_stdout(io.StringIO()):
            result = auto.wifi_task()
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(result["count"])
        scan.assert_called_once_with("wlan2")

    def test_unknown_nm_state_and_unmanaged_radio_do_not_trigger_scan(self):
        selected = {"name": "wlan2", "mode": "managed", "usb_id": "148f:3070"}
        with patch.object(auto, "_selected_radio", return_value=(selected, "")), \
             patch.object(auto, "_nm_state", return_value="10 (unmanaged)"), \
             patch.object(auto.shutil, "which", return_value="/usr/bin/nmcli"), \
             patch.object(auto.wifi, "scan") as scan, \
             redirect_stdout(io.StringIO()):
            result = auto.wifi_task()
        self.assertEqual(result["state"], "blocked")
        scan.assert_not_called()

    def test_lan_scan_uses_saved_interface_and_has_bounded_timeout(self):
        sample = {"interface": "eth0", "device_count": 2}
        with patch.object(auto.shutil, "which", return_value="/usr/bin/nmap"), \
             patch.object(auto.menu, "load_settings",
                          return_value={"lan": "eth0", "wireless": ""}), \
             patch.object(auto.lan, "discover", return_value=sample) as discover, \
             patch.object(auto.lan, "print_inventory"), \
             patch.object(auto.lan, "_store",
                          return_value=Path("/private/lan.json")), \
             redirect_stdout(io.StringIO()):
            result = auto.lan_task()
        self.assertEqual(result["count"], 2)
        discover.assert_called_once_with(interface="eth0", timeout=90)

    def test_dstike_does_not_try_random_serial_ports(self):
        with patch.object(auto.dstike, "serial", object()), \
             patch.object(auto.dstike, "available_ports", return_value=[
                 {"port": "/dev/ttyUSB0", "vid": "10c4", "pid": "ea60"},
                 {"port": "/dev/ttyUSB1", "vid": "10c4", "pid": "ea60"},
             ]), \
             patch.object(auto.dstike, "choose_port", return_value=(
                 None, "Multiple serial devices found"
             )), \
             patch.object(auto.dstike, "snapshot") as snapshot, \
             patch("deauther_console.load_port", return_value=""), \
             redirect_stdout(io.StringIO()):
            result = auto.dstike_task()
        self.assertEqual(result["state"], "blocked")
        snapshot.assert_not_called()

    def test_dstike_confident_port_reads_firmware_status_only(self):
        with patch.object(auto.dstike, "serial", object()), \
             patch.object(auto.dstike, "available_ports", return_value=[
                 {"port": "/dev/ttyUSB0", "vid": "10c4", "pid": "ea60"}
             ]), \
             patch.object(auto.dstike, "choose_port",
                          return_value=("/dev/ttyUSB0", None)), \
             patch.object(auto.dstike, "snapshot", return_value={
                 "state": "connected", "firmware": "Spacehuhn ESP8266"
             }) as snapshot, \
             patch("deauther_console.load_port", return_value=""), \
             redirect_stdout(io.StringIO()):
            result = auto.dstike_task()
        self.assertEqual(result["state"], "connected")
        request = snapshot.call_args.args[0]
        self.assertEqual(request.action, "status")
        self.assertEqual(request.port, "/dev/ttyUSB0")

    def test_auto_summary_reports_failures_without_concealing_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(auto, "wifi_task",
                              return_value={"state": "failed", "error": "no radio"}), \
                 patch.object(auto, "lan_task", return_value={
                     "state": "ok", "count": 4
                 }), \
                 patch.object(auto, "dstike_task", return_value={
                     "state": "blocked", "error": "no port"
                 }), \
                 patch.object(auto, "RESULT_DIR", Path(directory)), \
                 redirect_stdout(io.StringIO()) as text:
                code = auto.run()
            self.assertEqual(code, 1)
            self.assertIn("failed", text.getvalue())
            self.assertIn("LAN", text.getvalue())
            files = list(Path(directory).glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

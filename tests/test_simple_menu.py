"""Tests for AutoWIFI's short menu and explicit USB antenna selection."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autowifi_simple_menu", BASE / "extensions/simple_menu.py")
menu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menu)


class MenuTests(unittest.TestCase):
    def test_iw_dev_parsing(self):
        output = """phy#1
    Interface wlan2
        ifindex 8
        type monitor
phy#0
    Interface wlan0
        type managed
"""
        self.assertEqual(menu.parse_wireless_interfaces(output), [
            {"name": "wlan2", "mode": "monitor"},
            {"name": "wlan0", "mode": "managed"},
        ])

    def test_usb_detection_reads_actual_sysfs_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "class" / "net"
            usb = Path(temp) / "devices" / "usb1"
            sub = usb / "1-2" / "1-2:1.0"
            sub.mkdir(parents=True)
            root.joinpath("wlan2").mkdir(parents=True)
            (usb / "1-2" / "idVendor").write_text("148f\n")
            (usb / "1-2" / "idProduct").write_text("3070\n")
            (root / "wlan2" / "device").symlink_to(sub, target_is_directory=True)
            self.assertEqual(menu.usb_identity("wlan2", root), ("148f", "3070"))
            self.assertIsNone(menu.usb_identity("wlan0", root))

    def test_known_ralink_is_selected_when_not_uplink(self):
        with patch.object(menu, "adapters", return_value=[
            {"name": "wlan0", "mode": "managed", "is_ralink": False},
            {"name": "wlan2", "mode": "managed", "is_ralink": True},
        ]), patch.object(menu, "default_route", return_value="wlan0"):
            self.assertEqual(menu.choose_survey_radio(), "wlan2")

    def test_no_ralink_does_not_fallback_to_internal(self):
        with patch.object(menu, "adapters", return_value=[
            {"name": "wlan0", "mode": "managed", "is_ralink": False},
        ]), patch.object(menu, "_capture", return_value="USB present"):
            self.assertIsNone(menu.choose_survey_radio())

    def test_ralink_uplink_not_reconfigured(self):
        with patch.object(menu, "adapters", return_value=[
            {"name": "wlan2", "mode": "managed", "is_ralink": True},
        ]), patch.object(menu, "default_route", return_value="wlan2"):
            self.assertIsNone(menu.choose_survey_radio())

    def test_passive_survey_uses_selected_interface(self):
        with patch.object(menu, "choose_survey_radio", return_value="wlan2"), \
             patch.object(menu, "_run_program", return_value=0) as run:
            self.assertEqual(menu.nearby_wifi(), 0)
            run.assert_called_once_with("autowifi-direct", "networks",
                                        "--interface", "wlan2", "--duration", "20")

    def test_home_device_audit_uses_lan_scanner(self):
        with patch.object(menu, "default_route", return_value="eth0"), \
             patch.object(menu, "_run_program", return_value=0) as run:
            self.assertEqual(menu.connected_devices(), 0)
            run.assert_called_once_with("autowifi-devices")

    def test_ai_console_common_commands(self):
        spec2 = importlib.util.spec_from_file_location(
            "autowifi_operator_menu_test", BASE / "extensions/autowifi_operator.py")
        operator = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(operator)
        requests = {
            "menu": ("autowifi-menu", None),
            "find devices on my home network": ("autowifi-menu", "devices"),
            "what's connected to my wifi": ("autowifi-menu", "devices"),
            "scan my network": ("autowifi-menu", "devices"),
            "use my Ralink antenna to scan nearby Wi-Fi": ("autowifi-menu", "wireless"),
            "which antenna is being used": ("autowifi-menu", "adapters"),
        }
        for phrase, (command, action) in requests.items():
            with self.subTest(phrase=phrase):
                result = operator.interpret(phrase)
                self.assertEqual(Path(result[0]).name, command)
                if action is None:
                    self.assertEqual(len(result), 1)
                else:
                    self.assertEqual(result[1], action)


if __name__ == "__main__":
    unittest.main()

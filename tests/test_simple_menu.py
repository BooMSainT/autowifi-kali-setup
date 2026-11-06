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

    def test_passive_survey_uses_selected_ralink_on_2_4_channels(self):
        with patch.object(menu, "choose_survey_radio", return_value="wlan2"), \
             patch.object(menu, "adapters", return_value=[
                 {"name": "wlan2", "mode": "managed", "is_ralink": True}
             ]), patch.object(menu, "_run_program", return_value=0) as run:
            self.assertEqual(menu.nearby_wifi(), 0)
            run.assert_called_once_with("autowifi-direct", "networks",
                                        "--interface", "wlan2", "--duration", "75",
                                        "--channels", "1,2,3,4,5,6,7,8,9,10,11")

    def test_survey_other_radio_does_not_force_2_4_ghz(self):
        with patch.object(menu, "choose_survey_radio", return_value="wlan0"), \
             patch.object(menu, "adapters", return_value=[
                 {"name": "wlan0", "mode": "managed", "is_ralink": False}
             ]), patch.object(menu, "_run_program", return_value=0) as run:
            self.assertEqual(menu.nearby_wifi(), 0)
            run.assert_called_once_with("autowifi-direct", "networks",
                                        "--interface", "wlan0", "--duration", "75")

    def test_home_device_audit_uses_lan_scanner(self):
        with patch.object(menu, "default_route", return_value="eth0"), \
             patch.object(menu, "_run_program", return_value=0) as run:
            self.assertEqual(menu.connected_devices(), 0)
            run.assert_called_once_with("autowifi-devices")

    def test_saved_interfaces_persist_locally_and_permissions_are_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings" / "interface-selection.json"
            self.assertEqual(menu.load_settings(path), {"lan": "", "wireless": ""})
            menu.save_settings({"lan": "eth0", "wireless": "wlan2"}, path)
            self.assertEqual(menu.load_settings(path),
                             {"lan": "eth0", "wireless": "wlan2"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            menu.save_settings({"lan": "", "wireless": "wlan0"}, path)
            self.assertEqual(menu.load_settings(path)["lan"], "")

    def test_invalid_settings_are_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "interfaces.json"
            path.write_text("not json")
            with self.assertRaises(ValueError):
                menu.load_settings(path)
            self.assertEqual(path.read_text(), "not json")
            with self.assertRaises(ValueError):
                menu.save_settings({"lan": "eth0;bad", "wireless": ""}, path)

    def test_list_private_lan_interfaces(self):
        source = """[
          {"ifname":"lo","operstate":"UNKNOWN","addr_info":[
            {"family":"inet","scope":"host","local":"127.0.0.1","prefixlen":8}]},
          {"ifname":"eth0","operstate":"UP","addr_info":[
            {"family":"inet","scope":"global","local":"192.168.1.10","prefixlen":24}]},
          {"ifname":"wlan0","operstate":"UP","addr_info":[
            {"family":"inet","scope":"global","local":"10.0.0.5","prefixlen":24}]},
          {"ifname":"wlan2","operstate":"DOWN","addr_info":[]}
        ]"""
        with patch.object(menu, "_capture", return_value=source):
            entries = menu.lan_interfaces()
        self.assertEqual([entry["name"] for entry in entries], ["eth0", "wlan0"])

    def test_saved_eth0_is_used_for_lan_scan(self):
        with patch.object(menu, "load_settings", return_value={"lan": "eth0", "wireless": ""}), \
             patch.object(menu, "lan_interfaces", return_value=[
                 {"name": "eth0", "subnet": "192.168.1.0/24"}
             ]), patch.object(menu, "_run_program", return_value=0) as run:
            self.assertEqual(menu.connected_devices(), 0)
            run.assert_called_once_with("autowifi-devices", "--interface", "eth0")

    def test_missing_selected_lan_does_not_fallback(self):
        with patch.object(menu, "load_settings", return_value={"lan": "eth0", "wireless": ""}), \
             patch.object(menu, "lan_interfaces", return_value=[
                 {"name": "wlan0", "subnet": "192.168.1.0/24"}
             ]), patch.object(menu, "_run_program") as run:
            self.assertEqual(menu.connected_devices(), 1)
            run.assert_not_called()

    def test_saved_wifi_override_selects_non_ralink_radio(self):
        radios = [
            {"name": "wlan0", "mode": "managed", "is_ralink": False},
            {"name": "wlan2", "mode": "managed", "is_ralink": True},
        ]
        with patch.object(menu, "load_settings", return_value={"lan": "", "wireless": "wlan0"}), \
             patch.object(menu, "adapters", return_value=radios), \
             patch.object(menu, "default_route", return_value="eth0"), \
             patch.object(menu, "_capture", return_value="Not connected."):
            self.assertEqual(menu.choose_survey_radio(), "wlan0")

    def test_wifi_survey_does_not_switch_connected_radio(self):
        with patch.object(menu, "load_settings", return_value={"lan": "", "wireless": "wlan0"}), \
             patch.object(menu, "adapters", return_value=[
                 {"name": "wlan0", "mode": "managed", "is_ralink": False}
             ]), patch.object(menu, "default_route", return_value="eth0"), \
             patch.object(menu, "_capture", return_value="Connected to 00:11:22:33:44:55"):
            self.assertIsNone(menu.choose_survey_radio())

    def test_stale_wifi_selection_does_not_fallback_to_other_radio(self):
        with patch.object(menu, "load_settings", return_value={"lan": "", "wireless": "wlan3"}), \
             patch.object(menu, "adapters", return_value=[
                 {"name": "wlan0", "mode": "managed", "is_ralink": False}
             ]):
            self.assertIsNone(menu.choose_survey_radio())

    def test_interface_selection_menu_can_save_and_reset(self):
        entries = [{"name": "eth0", "subnet": "192.168.0.0/24"}]
        saved = {"lan": "", "wireless": ""}
        with patch.object(menu, "show_adapters"), \
             patch.object(menu, "lan_interfaces", return_value=entries), \
             patch.object(menu, "load_settings", side_effect=lambda: saved.copy()), \
             patch.object(menu, "save_settings", side_effect=lambda x: saved.update(x)), \
             patch("builtins.input", side_effect=["1", "1", "3", "0"]):
            self.assertEqual(menu.adapter_settings(), 0)
            self.assertEqual(saved, {"lan": "", "wireless": ""})

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
            "scan nearby wifi networks": ("autowifi-menu", "wireless"),
            "which antenna is being used": ("autowifi-menu", "adapters"),
            "change my network interface": ("autowifi-menu", "adapters"),
            "switch wifi adapter": ("autowifi-menu", "adapters"),
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

"""Offline regression tests for the legacy direct Wi-Fi scanner's adapter selection."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


EXT = Path(__file__).resolve().parents[1] / "extensions"


def _load_runner():
    pkg = types.ModuleType("autowifi")
    pkg.__path__ = []
    mods = {"autowifi": pkg}
    for suffix in ("deps", "interface", "cracker", "handshake", "lan", "scanner"):
        mod = types.ModuleType(f"autowifi.{suffix}")
        mods[f"autowifi.{suffix}"] = mod
        setattr(pkg, suffix, mod)
    mods["autowifi.interface"].list_interfaces = Mock()
    mods["autowifi.cracker"].find_wordlists = Mock()
    mods["autowifi.handshake"].HandshakeCapture = Mock()
    mods["autowifi.handshake"].verify_handshake = Mock()
    mods["autowifi.lan"].discover_lan_devices = Mock()
    mods["autowifi.lan"].list_lan_interfaces = Mock()
    mods["autowifi.scanner"].NetworkScanner = Mock()
    spec = importlib.util.spec_from_file_location("quick_selection_under_test",
                                                   EXT / "quick_scan.py")
    obj = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, mods):
        spec.loader.exec_module(obj)
    return obj


quick = _load_runner()


def _adapter(name):
    return SimpleNamespace(name=name, mode="Managed")


class QuickWirelessSelectionTests(unittest.TestCase):
    def test_missing_settings_prefers_actual_ralink_usb_not_wlan0(self):
        all_devices = [_adapter("wlan0"), _adapter("wlan2")]
        with patch.object(quick.wifi_interface, "list_interfaces", return_value=all_devices), \
             patch.object(quick, "_saved_wireless_interface", return_value=""), \
             patch.object(quick, "_usb_identity", side_effect=lambda name:
                          ("148f", "3070") if name == "wlan2" else None):
            self.assertEqual(quick._select_wireless_interface("").name, "wlan2")

    def test_explicit_interface_override_is_honored(self):
        all_devices = [_adapter("wlan0"), _adapter("wlan2")]
        with patch.object(quick.wifi_interface, "list_interfaces", return_value=all_devices), \
             patch.object(quick, "_saved_wireless_interface", return_value="wlan2"), \
             patch.object(quick, "_usb_identity") as identity:
            self.assertEqual(quick._select_wireless_interface("wlan0").name, "wlan0")
            identity.assert_not_called()

    def test_saved_radio_is_honored_and_unavailable_one_fails(self):
        all_devices = [_adapter("wlan0"), _adapter("wlan2")]
        with patch.object(quick.wifi_interface, "list_interfaces", return_value=all_devices), \
             patch.object(quick, "_saved_wireless_interface", return_value="wlan2"):
            self.assertEqual(quick._select_wireless_interface("").name, "wlan2")
        with patch.object(quick.wifi_interface, "list_interfaces", return_value=all_devices), \
             patch.object(quick, "_saved_wireless_interface", return_value="wlan3"):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                quick._select_wireless_interface("")

    def test_no_ralink_or_ambiguous_usb_identification_never_falls_back(self):
        all_devices = [_adapter("wlan0"), _adapter("wlan1")]
        with patch.object(quick.wifi_interface, "list_interfaces", return_value=all_devices), \
             patch.object(quick, "_saved_wireless_interface", return_value=""), \
             patch.object(quick, "_usb_identity", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "No usable Ralink"):
                quick._select_wireless_interface("")
        with patch.object(quick.wifi_interface, "list_interfaces", return_value=all_devices), \
             patch.object(quick, "_saved_wireless_interface", return_value=""), \
             patch.object(quick, "_usb_identity", return_value=("148f", "2870")):
            with self.assertRaisesRegex(RuntimeError, "Multiple Ralink"):
                quick._select_wireless_interface("")

    def test_config_reads_wireless_setting_and_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "interfaces.json"
            self.assertEqual(quick._saved_wireless_interface(path), "")
            path.write_text(json.dumps({"version": 1, "lan": "eth0", "wireless": "wlan2"}))
            self.assertEqual(quick._saved_wireless_interface(path), "wlan2")
            path.write_text(json.dumps({"version": 1, "wireless": "wlan2;rm"}))
            with self.assertRaisesRegex(ValueError, "invalid"):
                quick._saved_wireless_interface(path)

    def test_usb_id_reads_sysfs_without_guessing_adapter_number(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sys/class/net"
            device = Path(temp) / "devices/usb1/1-1/1-1:1.0"
            device.mkdir(parents=True)
            (device.parent / "idVendor").write_text("148f\n")
            (device.parent / "idProduct").write_text("3070\n")
            target = root / "wlan3"
            target.mkdir(parents=True)
            (target / "device").symlink_to(device, target_is_directory=True)
            self.assertEqual(quick._usb_identity("wlan3", root), ("148f", "3070"))
            self.assertIsNone(quick._usb_identity("wlan0", root))


if __name__ == "__main__":
    unittest.main()

"""Offline tests for interactive DSTIKE / Ralink hardware hub."""
from __future__ import annotations

import argparse
import importlib.util
import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

EXT = Path(__file__).resolve().parents[1] / "extensions"
bridge_spec = importlib.util.spec_from_file_location("deauther_bridge", EXT / "deauther_bridge.py")
bridge = importlib.util.module_from_spec(bridge_spec)
bridge_spec.loader.exec_module(bridge)
with patch.dict(sys.modules, {"deauther_bridge": bridge}):
    hub_spec = importlib.util.spec_from_file_location(
        "deauther_hub_test", EXT / "deauther_console.py"
    )
    hub = importlib.util.module_from_spec(hub_spec)
    hub_spec.loader.exec_module(hub)


class HubTests(unittest.TestCase):
    def test_serial_selection_persists_privately_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences/dstike-port.json"
            self.assertEqual(hub.load_port(path), "")
            hub.save_port("/dev/ttyUSB0", path)
            self.assertEqual(hub.load_port(path), "/dev/ttyUSB0")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            hub.save_port("", path)
            self.assertEqual(hub.load_port(path), "")

    def test_invalid_port_or_corrupt_config_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences/dstike-port.json"
            for port in ("/etc/passwd", "/dev/ttyUSB0;echo bad", "../ttyUSB0", "/dev/ttyS0"):
                with self.subTest(port=port), self.assertRaises(ValueError):
                    hub.save_port(port, path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json")
            with self.assertRaises(ValueError):
                hub.load_port(path)
            self.assertEqual(path.read_text(), "{not json")

    def test_numbered_usb_port_choice_and_auto(self):
        ports = [
            {"port": "/dev/ttyUSB0", "vid": "10c4", "pid": "ea60", "description": "CP210x"},
            {"port": "/dev/ttyACM0", "vid": "0483", "pid": "5740", "description": "other"},
        ]
        with patch.object(hub, "show_ports", return_value=ports), \
             patch.object(hub, "_stable_serial_path", side_effect=lambda x: x), \
             patch.object(hub, "save_port") as save, \
             patch("builtins.input", side_effect=["1"]):
            self.assertEqual(hub.select_port(), 0)
            save.assert_called_once_with("/dev/ttyUSB0")
        with patch.object(hub, "show_ports", return_value=ports), \
             patch.object(hub, "save_port") as save, \
             patch("builtins.input", side_effect=["0"]):
            self.assertEqual(hub.select_port(), 0)
            save.assert_called_once_with("")

    def test_run_status_respects_selected_serial_port_and_no_attack_commands(self):
        results = {
            "state": "connected", "timestamp": "2026-11-06", "action": "status",
            "port": "/dev/ttyUSB0", "firmware": "Spacehuhn ESP8266 Deauther",
            "info": "Spacehuhn v2.6.1",
        }
        with patch.object(hub, "load_port", return_value="/dev/ttyUSB0"), \
             patch.object(hub, "available", return_value=[{"port": "/dev/ttyUSB0"}]), \
             patch.object(hub.bridge, "choose_port", return_value=("/dev/ttyUSB0", None)), \
             patch.object(hub.bridge, "snapshot", return_value=results) as snap, \
             patch.object(hub.bridge, "save_record") as save, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(hub.run_read_only("status"), 0)
            arguments = snap.call_args.args[0]
            self.assertEqual(arguments.action, "status")
            self.assertEqual(arguments.port, "/dev/ttyUSB0")
            save.assert_not_called()
            with self.assertRaises(ValueError):
                hub.run_read_only("attack")

    def test_recording_is_explicit(self):
        result = {"state": "connected", "port": "/dev/ttyUSB0",
                  "timestamp": "2026-11-06", "action": "listen"}
        with patch.object(hub, "load_port", return_value=""), \
             patch.object(hub.bridge, "snapshot", return_value=result), \
             patch.object(hub.bridge, "save_record") as record, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(hub.run_read_only("listen"), 0)
            record.assert_not_called()
            self.assertEqual(hub.run_read_only("listen", record=True), 0)
            record.assert_called_once_with(result)

    def test_selecting_stale_port_fails_without_other_device_fallback(self):
        with patch.object(hub, "load_port", return_value="/dev/ttyUSB9"), \
             patch.object(hub, "available", return_value=[]), \
             patch.object(hub.bridge, "choose_port",
                          return_value=(None, "Port does not exist")) as choose, \
             patch.object(hub.bridge, "snapshot") as scan, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(hub.run_read_only("status"), 1)
            choose.assert_called_once()
            scan.assert_not_called()

    def test_menu_routes_only_safe_actions(self):
        with patch.object(hub, "load_port", return_value=""), \
             patch.object(hub, "run_read_only", return_value=0) as read, \
             patch.object(hub, "select_port", return_value=0) as port, \
             patch.object(hub, "show_logs", return_value=0) as logs, \
             patch.object(hub, "show_ralink", return_value=0) as radio, \
             patch.object(hub, "scan_ralink", return_value=0) as scan, \
             patch.object(hub, "troubleshooting", return_value=0) as doctor, \
             patch("builtins.input",
                   side_effect=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "0"]), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(hub.menu(), 0)
        self.assertEqual([item.args[0] for item in read.call_args_list],
                         ["detect", "status", "cached-aps", "listen", "listen"])
        self.assertTrue(read.call_args_list[-1].kwargs["record"])
        port.assert_called_once()
        logs.assert_called_once()
        radio.assert_called_once()
        scan.assert_called_once()
        doctor.assert_called_once()

    def test_detect_can_list_usb_ports_without_pyserial(self):
        available_ports = [{
            "port": "/dev/ttyUSB0", "description": "USB CP2102",
            "vid": None, "pid": None, "serial_number": None,
        }]
        with patch.object(bridge, "serial", None), \
             patch.object(bridge, "available_ports", return_value=available_ports):
            result = bridge.snapshot(argparse.Namespace(action="detect", port=None,
                                                       seconds=30))
        self.assertEqual(result["state"], "ports_found")
        self.assertIn("python3-serial", result["hint"])

    def test_menu_shows_ralink_as_distinct_hardware(self):
        self.assertIn("cannot use the Ralink USB adapter", hub.show_ralink.__doc__ or "")
        self.assertIn("ESP8266", hub.__doc__)
        self.assertNotIn("attack", hub.main.__code__.co_names)


if __name__ == "__main__":
    unittest.main()

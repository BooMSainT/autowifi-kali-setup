"""Hardware bridge unit tests; no attached hardware or network required."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


BRIDGE = Path(__file__).resolve().parents[1] / "extensions/hardware_bridge.py"
spec = importlib.util.spec_from_file_location("autowifi_hardware_bridge", BRIDGE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class HardwareBridgeTests(unittest.TestCase):
    def test_safe_ssh_destinations(self):
        self.assertTrue(bridge._safe_identity("192.168.1.3", "pi"))
        self.assertTrue(bridge._safe_identity("lab-pwn.local", "operator"))
        self.assertFalse(bridge._safe_identity("-oProxyCommand=evil", "pi"))
        self.assertFalse(bridge._safe_identity("lab;echo bad", "pi"))
        self.assertFalse(bridge._safe_identity("lab.local", "-bad"))

    def test_ssh_is_opt_in(self):
        with patch.object(bridge.subprocess, "run") as run:
            result = bridge.pwn_status(None, None, 22)
            self.assertEqual(result["state"], "not_configured")
            run.assert_not_called()

    def test_ssh_uses_strict_known_hosts_and_fixed_read_only_command(self):
        class Completed:
            returncode = 0
            stdout = "Linux\nup 2 days\n"
            stderr = ""

        with patch.object(bridge.subprocess, "run", return_value=Completed()) as run:
            result = bridge.pwn_status("lab.local", "pi", 22)
            self.assertEqual(result["state"], "connected")
            cmd = run.call_args.args[0]
            self.assertIn("StrictHostKeyChecking=yes", cmd)
            self.assertIn("BatchMode=yes", cmd)
            self.assertEqual(cmd[-1], "uname -s && uptime -p")

    def test_flipper_detection_requires_matching_by_id_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "usb-Flipper_Devices_Inc._Flipper_demo").symlink_to("/dev/null")
            (directory / "usb-other").symlink_to("/dev/null")
            with patch.object(bridge, "FLIPPER_ID_DIR", directory):
                ports = bridge.flipper_ports()
            self.assertEqual(len(ports), 1)
            self.assertIn("Flipper", ports[0])

    def test_local_log_has_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with patch.object(bridge, "LOG_PATH", path):
                bridge.emit({"timestamp": "test", "flipper": {}, "pwn": {}}, True, True)
            self.assertEqual(json.loads(path.read_text())["timestamp"], "test")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

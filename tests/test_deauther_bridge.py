"""Offline unit tests for read-only DSTIKE/Spacehuhn serial bridge."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

FILE = Path(__file__).resolve().parents[1] / "extensions/deauther_bridge.py"
spec = importlib.util.spec_from_file_location("autowifi_deauther_bridge", FILE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class DeautherTests(unittest.TestCase):
    def test_port_choice_unambiguous(self):
        ports = [{"port": "/dev/ttyUSB0", "vid": "1a86", "pid": "7523"},
                 {"port": "/dev/ttyACM0", "vid": "0483", "pid": "5740"}]
        self.assertEqual(bridge.choose_port(None, ports), ("/dev/ttyUSB0", None))
        self.assertIsNotNone(bridge.choose_port(None, ports + [ports[0]])[1])
        self.assertIsNotNone(bridge.choose_port(None, [])[1])

    def test_port_choice_does_not_accept_arbitrary_path(self):
        port, error = bridge.choose_port("/etc/passwd", [])
        self.assertIsNone(port)
        self.assertIn("USB serial", error)

    def test_only_safe_serial_commands(self):
        link = MagicMock()
        with patch.object(bridge, "read_until_quiet", return_value="hello"):
            self.assertEqual(bridge.command(link, "info"), "hello")
            link.write.assert_called_once_with(b"info\r\n")
        for forbidden in ("attack deauth", "send deauth", "scan wifi", "set attacktimeout 0"):
            with self.assertRaises(ValueError):
                bridge.command(link, forbidden)

    def test_known_firmware_read_only_status(self):
        link = MagicMock()
        link.__enter__.return_value = link
        serial_stub = SimpleNamespace(Serial=MagicMock(return_value=link), SerialException=OSError)
        with patch.object(bridge, "serial", serial_stub), \
             patch.object(bridge.time, "sleep"), \
             patch.object(bridge, "read_until_quiet",
                          side_effect=["ESP8266 Deauther 2.6.1 by Spacehuhn", "Free RAM: 10000"]):
            result = bridge.connect("/dev/ttyUSB0", "status", 2)
        self.assertEqual(result["state"], "connected")
        self.assertEqual([args.args[0] for args in link.write.call_args_list],
                         [b"info\r\n", b"sysinfo\r\n"])

    def test_unknown_firmware_is_not_assumed_deauther(self):
        link = MagicMock()
        link.__enter__.return_value = link
        serial_stub = SimpleNamespace(Serial=MagicMock(return_value=link), SerialException=OSError)
        with patch.object(bridge, "serial", serial_stub), \
             patch.object(bridge.time, "sleep"), \
             patch.object(bridge, "read_until_quiet", return_value="Unrelated serial device"):
            result = bridge.connect("/dev/ttyUSB0", "status", 2)
        self.assertEqual(result["state"], "unrecognized_firmware")
        link.write.assert_called_once_with(b"info\r\n")

    def test_pyserial_permission_error_is_reported(self):
        serial_stub = SimpleNamespace(
            Serial=MagicMock(side_effect=OSError("[Errno 13] Permission denied")),
            SerialException=OSError,
        )
        with patch.object(bridge, "serial", serial_stub):
            result = bridge.connect("/dev/ttyUSB0", "status", 2)
        self.assertEqual(result["state"], "permission_denied")

    def test_operator_routes_deauther_status(self):
        operator_file = FILE.parent / "autowifi_operator.py"
        operator_spec = importlib.util.spec_from_file_location("autowifi_operator_test", operator_file)
        operator = importlib.util.module_from_spec(operator_spec)
        operator_spec.loader.exec_module(operator)
        for prompt in ("deauther", "show my deauther status", "check my DSTIKE device"):
            cmd = operator.interpret(prompt)
            self.assertIsNotNone(cmd)
            self.assertEqual(cmd[1], "deauther")

    def test_logging_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "status.jsonl"
            with patch.object(bridge, "LOG_PATH", path):
                bridge.save_record({"state": "connected"})
            self.assertEqual(json.loads(path.read_text())["state"], "connected")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

"""Tests for offline BOMB incident rehearsal and editable local SSIDs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autowifi_bomb_test", BASE / "extensions/bomb_simulation.py")
bomb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bomb)


class BombTests(unittest.TestCase):
    def test_add_rename_delete_without_git(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config" / "bomb-networks.json"
            self.assertEqual(bomb.load_networks(config), [])
            names = bomb.add_network([], "Home-2G")
            names = bomb.add_network(names, "Guest-5G")
            bomb.save_networks(names, config)
            self.assertEqual(bomb.load_networks(config), ["Home-2G", "Guest-5G"])
            renamed = bomb.rename_network(bomb.load_networks(config), 0, "New Home Wi-Fi")
            bomb.save_networks(renamed, config)
            self.assertEqual(bomb.load_networks(config), ["New Home Wi-Fi", "Guest-5G"])
            bomb.save_networks(bomb.remove_network(renamed, 1), config)
            self.assertEqual(bomb.load_networks(config), ["New Home Wi-Fi"])
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)

    def test_simulation_is_only_synthetic_events_and_no_radio_control(self):
        report = bomb.make_simulation(["Home", "Guest", "Workshop"], "TEST")
        self.assertTrue(report["simulation_only"])
        self.assertEqual(report["wireless_frames_transmitted"], 0)
        self.assertEqual(report["devices_intentionally_disconnected"], 0)
        self.assertFalse(report["real_pmf_protection_verified"])
        self.assertEqual([x["ssid"] for x in report["networks"]],
                         ["Home", "Guest", "Workshop"])
        self.assertTrue(all(item["synthetic"] for item in report["networks"]))

    def test_run_only_uses_saved_networks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "bomb-networks.json"
            reports = root / "reports"
            bomb.save_networks(["Owned-2G", "Owned-5G"], config)
            with patch("builtins.input", return_value="y"):
                self.assertEqual(bomb.run_simulation(config, reports), 0)
            files = list(reports.glob("bomb-*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text())
            self.assertEqual([x["ssid"] for x in data["networks"]],
                             ["Owned-2G", "Owned-5G"])
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)

    def test_missing_networks_does_not_simulate_without_user_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("builtins.input", return_value="n"):
                result = bomb.run_simulation(root / "missing.json", root / "reports")
            self.assertEqual(result, 1)
            self.assertFalse((root / "reports").exists())

    def test_invalid_and_duplicate_names_rejected(self):
        for value in ("", "x" * 33, "Home\nGuest"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    bomb.validate_ssid(value)
        with self.assertRaises(ValueError):
            bomb.add_network(["Home"], "Home")
        with self.assertRaises(ValueError):
            bomb.rename_network(["Home", "Guest"], 0, "Guest")
        with self.assertRaises(ValueError):
            bomb.rename_network(["Home"], 3, "New")

    def test_corrupt_config_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bomb-networks.json"
            path.write_text("{invalid")
            with self.assertRaises(ValueError):
                bomb.load_networks(path)
            self.assertEqual(path.read_text(), "{invalid")

    def test_menu_and_console_bomb_routing(self):
        menu_spec = importlib.util.spec_from_file_location("autowifi_menu_bomb_test",
                                                           BASE / "extensions/simple_menu.py")
        menu = importlib.util.module_from_spec(menu_spec)
        menu_spec.loader.exec_module(menu)
        self.assertIn("bomb", menu.ACTIONS)
        self.assertIn("networks", menu.ACTIONS)
        self.assertIn(("7", "BOMB: simulate incidents for saved home networks", "bomb"),
                      menu.OPTIONS)
        self.assertIn(("8", "Edit BOMB networks (add / rename / delete SSIDs)", "networks"),
                      menu.OPTIONS)
        op_spec = importlib.util.spec_from_file_location("operator_bomb_test",
                                                         BASE / "extensions/autowifi_operator.py")
        operator = importlib.util.module_from_spec(op_spec)
        op_spec.loader.exec_module(operator)
        self.assertEqual(operator.interpret("bomb")[1], "bomb")
        self.assertEqual(operator.interpret("edit bomb networks")[1], "networks")
        self.assertEqual(operator.interpret("rename my bomb networks")[1], "networks")


if __name__ == "__main__":
    unittest.main()

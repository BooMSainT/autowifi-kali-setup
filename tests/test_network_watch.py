"""Offline tests for AutoWIFI continuous monitor and confirmed-change alerts."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


EXT = Path(__file__).resolve().parents[1] / "extensions"
spec = importlib.util.spec_from_file_location("autowifi_network_watch_test", EXT / "network_watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def device(ip: str, mac: str, hostname: str = "") -> dict:
    return {"ip": ip, "mac": mac, "hostname": hostname, "vendor": ""}


class MonitorTests(unittest.TestCase):
    def test_first_pass_is_baseline_not_alarm(self):
        hosts = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        current, pending, alerts = watch._confirm_changes(None, {}, hosts, "lan")
        self.assertEqual(current, hosts)
        self.assertEqual(pending, {})
        self.assertEqual(alerts, [])

    def test_new_device_needs_two_consecutive_successful_observations(self):
        previous = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        observed = watch._lan_map([
            device("192.168.1.10", "AA:BB:CC:DD:EE:01"),
            device("192.168.1.11", "AA:BB:CC:DD:EE:02", "new-phone"),
        ])
        first, pending, alerts = watch._confirm_changes(previous, {}, observed, "lan")
        self.assertEqual(alerts, [])
        self.assertEqual(len(first), 1)
        confirmed, pending, alerts = watch._confirm_changes(first, pending, observed, "lan")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "device_appeared")
        self.assertEqual(alerts[0]["new"]["hostname"], "new-phone")
        self.assertEqual(len(confirmed), 2)
        _, _, again = watch._confirm_changes(confirmed, pending, observed, "lan")
        self.assertEqual(again, [])

    def test_disappearance_requires_two_observations_and_is_not_repeated(self):
        previous = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        baseline, pending, events = watch._confirm_changes(previous, {}, {}, "lan")
        self.assertEqual(events, [])
        self.assertEqual(len(baseline), 1)
        baseline, pending, events = watch._confirm_changes(baseline, pending, {}, "lan")
        self.assertEqual([x["kind"] for x in events], ["device_missing"])
        self.assertEqual(baseline, {})
        _, _, events = watch._confirm_changes(baseline, pending, {}, "lan")
        self.assertEqual(events, [])

    def test_unconfirmed_change_resets_when_device_reappears(self):
        old = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        base, pending, _ = watch._confirm_changes(old, {}, {}, "lan")
        base, pending, events = watch._confirm_changes(base, pending, old, "lan")
        self.assertEqual(events, [])
        self.assertEqual(pending, {})

    def test_ip_change_is_detected_by_stable_mac_after_two_scans(self):
        before = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        after = watch._lan_map([device("192.168.1.33", "AA:BB:CC:DD:EE:01")])
        state, pending, events = watch._confirm_changes(before, {}, after, "lan")
        self.assertEqual(events, [])
        self.assertEqual(state["mac:AA:BB:CC:DD:EE:01"]["ip"], "192.168.1.10")
        state, pending, events = watch._confirm_changes(state, pending, after, "lan")
        self.assertEqual([x["kind"] for x in events], ["device_ip_changed"])
        self.assertEqual(state["mac:AA:BB:CC:DD:EE:01"]["ip"], "192.168.1.33")

    def test_wifi_only_monitors_explicit_home_ssids_and_confirmed_security(self):
        rows = [
            {"ssid": "MyNet", "channel": 6, "security": "WPA2"},
            {"ssid": "Neighbor", "channel": 1, "security": "OPEN"},
        ]
        first = watch._wifi_map(rows, ["MyNet"])
        self.assertEqual(set(first), {"MyNet"})
        changed = watch._wifi_map([{"ssid": "MyNet", "channel": 6,
                                    "security": "OPEN"}], ["MyNet"])
        base, pending, events = watch._confirm_changes(first, {}, changed, "wifi")
        self.assertEqual(events, [])
        base, pending, events = watch._confirm_changes(base, pending, changed, "wifi")
        self.assertEqual([x["kind"] for x in events], ["home_wifi_security_changed"])

    def test_corrupt_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "state.json"
            p.write_text("{broken")
            with self.assertRaises(ValueError):
                watch._read_state(p)
            self.assertEqual(p.read_text(), "{broken")

    def test_events_and_state_are_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "monitor"
            watch._append_event({"timestamp": "now", "kind": "device_appeared",
                                 "identity": "mac:AA", "new": {"ip": "192.168.1.1"}},
                                directory / "events.jsonl")
            watch._atomic_json(directory / "state.json", {
                "version": 1, "baseline_lan": {}, "baseline_wifi": {},
                "pending_lan": {}, "pending_wifi": {},
            })
            self.assertEqual((directory / "events.jsonl").stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory / "state.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_failed_lan_scan_never_deletes_baseline(self):
        original = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        state = {"version": 1, "baseline_lan": original,
                 "pending_lan": {}, "baseline_wifi": None,
                 "pending_wifi": {}, "last_cycle": None}
        fake_auto = SimpleNamespace(
            lan_task=lambda **kwargs: {"state": "failed", "error": "nmap unavailable"},
            wifi_task=lambda **kwargs: {"state": "failed", "error": "nmcli unavailable"},
            dstike_task=lambda: {"state": "blocked"},
        )
        with patch.dict("sys.modules", {"autopilot": fake_auto}), \
             patch.object(watch, "_owned_ssids", return_value=([], "")):
            updated, alerts = watch.monitor_cycle(state)
        self.assertEqual(alerts, [])
        self.assertEqual(updated["baseline_lan"], original)
        self.assertEqual(updated["pending_lan"], {})
        self.assertIn("lan", updated["last_error"])

    def test_zero_lan_hosts_does_not_trigger_mass_disappearance(self):
        original = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        state = {"version": 1, "baseline_lan": original,
                 "pending_lan": {}, "baseline_wifi": None,
                 "pending_wifi": {}, "last_cycle": None,
                 "scope_lan": "192.168.1.0/24"}
        fake_auto = SimpleNamespace(
            lan_task=lambda **kwargs: {"state": "ok", "devices": [], "subnet": "192.168.1.0/24"},
            wifi_task=lambda **kwargs: {"state": "failed", "error": "no radio"},
            dstike_task=lambda: {"state": "blocked"},
        )
        with patch.dict("sys.modules", {"autopilot": fake_auto}), \
             patch.object(watch, "_owned_ssids", return_value=([], "")):
            updated, events = watch.monitor_cycle(state)
        self.assertEqual(events, [])
        self.assertEqual(updated["baseline_lan"], original)
        self.assertIn("lan", updated["last_error"])

    def test_changed_subnet_resets_baseline_without_disappearance_alert(self):
        old = watch._lan_map([device("192.168.1.10", "AA:BB:CC:DD:EE:01")])
        state = {"version": 1, "baseline_lan": old,
                 "pending_lan": {}, "baseline_wifi": None,
                 "pending_wifi": {}, "last_cycle": None,
                 "scope_lan": "192.168.1.0/24"}
        new_hosts = [device("10.0.0.10", "AA:BB:CC:DD:EE:02")]
        fake_auto = SimpleNamespace(
            lan_task=lambda **kwargs: {"state": "ok", "devices": new_hosts,
                              "subnet": "10.0.0.0/24"},
            wifi_task=lambda **kwargs: {"state": "failed", "error": "no radio"},
            dstike_task=lambda: {"state": "blocked"},
        )
        with patch.dict("sys.modules", {"autopilot": fake_auto}), \
             patch.object(watch, "_owned_ssids", return_value=([], "")):
            updated, events = watch.monitor_cycle(state)
        self.assertEqual(events, [])
        self.assertEqual(updated["scope_lan"], "10.0.0.0/24")
        self.assertEqual(set(updated["baseline_lan"]), {"mac:AA:BB:CC:DD:EE:02"})

    def test_watch_two_cycles_records_state_without_sleeping(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "monitor"
            statefile = directory / "state.json"
            eventfile = directory / "events.jsonl"
            lockfile = directory / "monitor.lock"
            sequence = [(
                {"version": 1, "baseline_lan": None,
                 "baseline_wifi": None, "pending_lan": {},
                 "pending_wifi": {}, "last_cycle": None, "cycle": 1}, []
            ), (
                {"version": 1, "baseline_lan": {},
                 "baseline_wifi": {}, "pending_lan": {},
                 "pending_wifi": {}, "last_cycle": "later", "cycle": 2}, []
            )]
            with patch.object(watch, "STATE_DIR", directory), \
                 patch.object(watch, "STATE_FILE", statefile), \
                 patch.object(watch, "LOCK_FILE", lockfile), \
                 patch.object(watch, "monitor_cycle", side_effect=sequence), \
                 patch.object(watch, "_append_event") as append, \
                 patch.object(watch, "_notify") as notify:
                self.assertEqual(watch.watch(interval=300, max_cycles=2,
                                            sleeper=lambda seconds: None), 0)
            self.assertEqual(json.loads(statefile.read_text())["cycle"], 2)
            append.assert_not_called()
            notify.assert_not_called()

    def test_background_service_commands_are_exact_and_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            unit = Path(temp) / ".config/systemd/user" / watch.SERVICE_NAME
            unit.parent.mkdir(parents=True)
            unit.write_text("[Unit]\n")
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch.object(watch.Path, "home", return_value=Path(temp)), \
                 patch.object(watch, "_systemctl", return_value=completed) as command:
                self.assertEqual(watch.service_control("start"), 0)
                self.assertEqual(
                    [call.args for call in command.call_args_list],
                    [("daemon-reload",), ("enable", "--now", watch.SERVICE_NAME)]
                )
                command.reset_mock()
                self.assertEqual(watch.service_control("stop"), 0)
                command.assert_called_once_with("disable", "--now",
                                                watch.SERVICE_NAME)


if __name__ == "__main__":
    unittest.main()

"""No-radio tests for aligned, bounded, evidence-based home LAN inventory."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import ipaddress
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "autowifi_lan_inventory_test", ROOT / "extensions/lan_inventory.py")
lan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lan)


SAMPLE_XML = """<?xml version='1.0'?>
<nmaprun>
 <host><status state="up"/>
  <address addr="192.168.10.1" addrtype="ipv4"/>
  <address addr="00:11:22:33:44:55" addrtype="mac" vendor="Example Router Co"/>
  <hostnames><hostname name="gateway.local" type="PTR"/></hostnames>
 </host>
 <host><status state="up"/>
  <address addr="192.168.10.11" addrtype="ipv4"/>
  <hostnames><hostname name="LaserJet-Long-Hostname-With-Spaces.local" type="PTR"/></hostnames>
 </host>
 <host><status state="down"/>
  <address addr="192.168.10.99" addrtype="ipv4"/>
 </host>
</nmaprun>"""

PORT_XML = """<?xml version='1.0'?>
<nmaprun><host><ports>
<port protocol="tcp" portid="22"><state state="open"/><service name="ssh"/></port>
<port protocol="tcp" portid="80"><state state="closed"/><service name="http"/></port>
<port protocol="tcp" portid="9100"><state state="open"/><service name="jetdirect"/></port>
</ports></host></nmaprun>"""


class DeviceInventoryTests(unittest.TestCase):
    def test_extracts_actual_online_hosts_and_reverse_dns_vendor(self):
        records = lan._parse_hosts(SAMPLE_XML)
        self.assertEqual([d["ip"] for d in records], ["192.168.10.1", "192.168.10.11"])
        self.assertEqual(records[0]["vendor"], "Example Router Co")
        self.assertEqual(records[1]["hostname"], "LaserJet-Long-Hostname-With-Spaces.local")
        self.assertEqual(records[1]["mac"], "")
        self.assertEqual(records[1]["hostname_source"], "reverse DNS via Nmap")

    def test_hostname_type_is_a_clue_not_a_verified_identity(self):
        self.assertEqual(lan._type_clue("LaserJet-1020")[0], "possible printer")
        self.assertEqual(lan._type_clue("office-pc")[0], "unknown")
        self.assertEqual(lan._type_clue("phone123")[0], "unknown")
        self.assertEqual(lan._type_clue("")[0], "unknown")

    def test_aligned_columns_and_compact_small_terminal(self):
        entries = lan._parse_hosts(SAMPLE_XML)
        for record in entries:
            record["type"], _ = lan._type_clue(record["hostname"])
        data = {
            "interface": "eth0", "local_ip": "192.168.10.9",
            "subnet": "192.168.10.0/24", "device_count": len(entries),
            "devices": entries,
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lan.print_inventory(data, columns=85)
        text = out.getvalue()
        table = [line for line in text.splitlines() if line.startswith(("1  ", "2  ", "#  ")) ]
        self.assertEqual(len(table), 3)
        self.assertTrue(all(len(line) == len(table[0]) for line in table))
        self.assertEqual(text.count("Devices: 2"), 1)
        self.assertIn("MAC", text)

        compact = io.StringIO()
        with contextlib.redirect_stdout(compact):
            lan.print_inventory(data, columns=62)
        self.assertIn("MAC: ", compact.getvalue())
        self.assertIn("192.168.10.11", compact.getvalue())

    def test_connected_local_private_scope_only(self):
        addresses = [{
            "name": "eth0", "ip": "192.168.10.9",
            "network": ipaddress.IPv4Network("192.168.10.0/24"),
            "state": "UP",
        }, {
            "name": "wlan0", "ip": "10.0.0.4",
            "network": ipaddress.IPv4Network("10.0.0.0/24"),
            "state": "UP",
        }]
        route = SimpleNamespace(
            returncode=0,
            stdout='[{"dev":"wlan0","gateway":"10.0.0.1"}]',
        )
        with patch.object(lan, "_interfaces", return_value=addresses), \
             patch.object(lan, "_run", return_value=route):
            self.assertEqual(lan._scope()["name"], "wlan0")
            self.assertEqual(lan._scope("eth0")["name"], "eth0")
            with self.assertRaises(ValueError):
                lan._scope("wlan77")

    def test_neighbors_fill_missing_mac_but_do_not_create_fake_hosts(self):
        scope = {
            "name": "eth0", "ip": "192.168.10.9",
            "network": ipaddress.IPv4Network("192.168.10.0/24"),
        }
        nmap = SimpleNamespace(returncode=0, stderr="", stdout=SAMPLE_XML)
        cache = {"192.168.10.11": "DE:AD:BE:EF:01:02",
                 "192.168.10.99": "AA:AA:AA:AA:AA:AA"}
        with patch.object(lan, "_scope", return_value=scope), \
             patch.object(lan, "_run", return_value=nmap) as scan, \
             patch.object(lan, "_neighbors", return_value=cache), \
             patch.object(lan, "_oui_vendors", return_value={}), \
             patch.object(lan.shutil, "which", return_value="/usr/bin/nmap"):
            output = lan.discover()
        self.assertEqual(output["device_count"], 2)
        self.assertEqual(output["devices"][1]["mac"], "DE:AD:BE:EF:01:02")
        self.assertIn("neighbor cache", output["devices"][1]["mac_source"])
        command = scan.call_args.args[0]
        self.assertIn("-sn", command)
        self.assertNotIn("-n", command)
        self.assertNotIn("-sV", command)

    def test_elevated_discovery_is_explicit_and_uses_sudo_auth(self):
        scope = {"name": "eth0", "ip": "192.168.10.9",
                 "network": ipaddress.IPv4Network("192.168.10.0/24")}
        scan = SimpleNamespace(returncode=0, stderr="", stdout=SAMPLE_XML)
        with patch.object(lan, "_scope", return_value=scope), \
             patch.object(lan, "_run", return_value=scan) as cmd, \
             patch.object(lan, "_neighbors", return_value={}), \
             patch.object(lan, "_oui_vendors", return_value={}), \
             patch.object(lan.shutil, "which", side_effect=lambda x: "/usr/bin/"+x), \
             patch.object(lan.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0)) as auth:
            output = lan.discover(elevated=True)
        auth.assert_called_once_with(["sudo", "-v"], check=False)
        self.assertEqual(cmd.call_args.args[0][:3], ["sudo", "-n", "nmap"])
        self.assertEqual(output["device_count"], 2)

    def test_selected_host_port_check_bounded_to_local_ip(self):
        with patch.object(lan, "_run", return_value=SimpleNamespace(
                returncode=0, stdout=PORT_XML, stderr="")) as nmap:
            data = lan.inspect_services("192.168.10.11", "192.168.10.0/24")
        self.assertEqual([d["port"] for d in data], ["22", "9100"])
        command = nmap.call_args.args[0]
        self.assertEqual(command[-1], "192.168.10.11")
        self.assertIn("-sT", command)
        self.assertNotIn("-sV", command)
        with self.assertRaises(ValueError):
            lan.inspect_services("8.8.8.8", "192.168.10.0/24")
        with self.assertRaises(ValueError):
            lan.inspect_services("192.168.10.20", "8.8.8.0/24")

    def test_interactive_selected_host_does_not_scan_without_p(self):
        devices = lan._parse_hosts(SAMPLE_XML)
        for item in devices:
            item["type"], item["type_evidence"] = lan._type_clue(item["hostname"])
        report = {"interface": "eth0", "subnet": "192.168.10.0/24",
                  "devices": devices}
        with patch("builtins.input", side_effect=["2", "l", "0"]), \
             patch.object(lan, "inspect_services") as inspect:
            self.assertEqual(lan.interactive(report), 0)
            inspect.assert_not_called()

    def test_private_report_is_visible_in_existing_history(self):
        host = lan._parse_hosts(SAMPLE_XML)[0]
        host["type"], host["type_evidence"] = lan._type_clue(host["hostname"])
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "menu-reports"
            path = lan._store({
                "version": 1, "timestamp": "2026-11-06T00:00:00Z",
                "interface": "eth0", "local_ip": "192.168.10.9",
                "subnet": "192.168.10.0/24", "device_count": 1,
                "devices": [host],
            }, directory)
            report = json.loads(path.read_text())
            self.assertEqual(report["kind"], "lan")
            self.assertEqual(report["count"], 1)
            self.assertIn("Devices: 1", report["raw_output"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()

"""End-to-end offline smoke tests for the real AutoWIFI menu installation.

Creates an isolated local Git checkout (no remote network), executes
setup-menu.sh as a subprocess, runs its installed launchers, and verifies
that modifying source updates the running menu without reinstalling it.
No network interfaces or wireless traffic are touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class LauncherIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.checkout = self.home / "autowifi-kali-setup"
        (self.checkout / "extensions").mkdir(parents=True)
        for name in ("simple_menu.py", "network_inventory.py",
                     "lan_inventory.py", "deauther_bridge.py",
                     "deauther_console.py", "autopilot.py", "network_watch.py", "bomb_simulation.py", "quick_scan.py",
                     "autowifi_operator.py"):
            shutil.copy2(SOURCE / "extensions" / name, self.checkout / "extensions" / name)
        shutil.copy2(SOURCE / "setup-menu.sh", self.checkout / "setup-menu.sh")

        for command in (
            ["git", "init", "-b", "main", str(self.checkout)],
            ["git", "-C", str(self.checkout), "remote", "add", "origin",
             "https://github.com/BooMSainT/autowifi-kali-setup.git"],
        ):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

        # The installer prints a revision. Ensure there is a real local HEAD.
        for command in (
            ["git", "-C", str(self.checkout), "add", "."],
            ["git", "-C", str(self.checkout), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"],
        ):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

        self.env = dict(os.environ, HOME=str(self.home))
        self.bin = self.home / ".local" / "bin"

    def run_cmd(self, *args, input_text=None):
        return subprocess.run(args, input=input_text, text=True,
                              capture_output=True, timeout=35, env=self.env)

    def install(self):
        result = self.run_cmd("bash", str(self.checkout / "setup-menu.sh"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("launchers: PASS", result.stdout)

    def test_live_source_after_one_time_bootstrap(self):
        self.install()
        menu = self.bin / "wifi"
        before = self.run_cmd(str(menu), "--version")
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertIn("2026.11.09.1", before.stdout)
        self.assertIn(str(self.checkout / "extensions/simple_menu.py"), before.stdout)

        # Simulate receiving an updated Git source file. No second install.
        source = self.checkout / "extensions" / "simple_menu.py"
        contents = source.read_text()
        self.assertIn('MENU_VERSION = "2026.11.09.1"', contents)
        source.write_text(contents.replace('MENU_VERSION = "2026.11.09.1"',
                                           'MENU_VERSION = "TEST-FROM-GIT"'))
        after = self.run_cmd(str(menu), "--version")
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertIn("TEST-FROM-GIT", after.stdout)

        full_menu = self.run_cmd(str(menu), input_text="0\n")
        self.assertEqual(full_menu.returncode, 0, full_menu.stderr)
        self.assertIn("TEST-FROM-GIT", full_menu.stdout)
        self.assertIn("Scan nearby Wi-Fi", full_menu.stdout)

    def test_installed_inventory_parses_two_fake_nmcli_access_points(self):
        self.install()
        fakebin = self.home / "fakebin"
        fakebin.mkdir()
        nmcli = fakebin / "nmcli"
        nmcli.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' "
            "'Home:AA\\:BB\\:CC\\:DD\\:EE\\:01:6:2437 MHz:82:WPA2' "
            "'Guest:AA\\:BB\\:CC\\:DD\\:EE\\:02:11:2462 MHz:65:WPA3'\n"
        )
        nmcli.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env.get("PATH", "")
        result = self.run_cmd(str(self.bin / "autowifi-inventory"),
                              "--interface", "wlan2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Access points detected: 2", result.stdout)
        self.assertIn("Home", result.stdout)
        self.assertIn("Guest", result.stdout)

    def test_installed_wifi_scan_falls_back_to_kernel_if_nm_unmanaged(self):
        self.install()
        fakebin = self.home / "fake-wifi-fallback"
        fakebin.mkdir()
        nmcli = fakebin / "nmcli"
        nmcli.write_text(
            "#!/bin/sh\n"
            "echo 'Device wlan2 is unmanaged' >&2\n"
            "exit 10\n"
        )
        nmcli.chmod(0o755)
        iw = fakebin / "iw"
        iw.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' "
            "'BSS AA:BB:CC:DD:EE:01(on wlan2)' "
            "'  freq: 2412' "
            "'  signal: -51.00 dBm' "
            "'  capability: ESS Privacy' "
            "'  SSID: MyHomeWifi' "
            "'  RSN:' "
            "'    * Authentication suites: PSK' "
            "'BSS AA:BB:CC:DD:EE:02(on wlan2)' "
            "'  freq: 2462' "
            "'  signal: -68.00 dBm' "
            "'  capability: ESS Privacy' "
            "'  SSID: MyGuestWifi' "
            "'  RSN:' "
            "'    * Authentication suites: SAE'\n"
        )
        iw.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env.get("PATH", "")
        result = self.run_cmd(
            str(self.bin / "autowifi-inventory"), "--interface", "wlan2"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Access points detected: 2", result.stdout)
        self.assertIn("MyHomeWifi", result.stdout)
        self.assertIn("MyGuestWifi", result.stdout)
        self.assertIn("WPA3", result.stdout)

    def test_installed_lan_inventory_on_mock_home_network(self):
        self.install()
        fakebin = self.home / "fake-lan-bin"
        fakebin.mkdir()
        ip = fakebin / "ip"
        ip.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *addr*) printf '%s\\n' '[{\"ifname\":\"eth0\",\"operstate\":\"UP\",\"addr_info\":[{\"family\":\"inet\",\"scope\":\"global\",\"local\":\"192.168.7.3\",\"prefixlen\":24}]}]' ;;\n"
            "  *route*) printf '%s\\n' '[{\"dev\":\"eth0\"}]' ;;\n"
            "  *neigh*) printf '%s\\n' '[]' ;;\n"
            "esac\n"
        )
        ip.chmod(0o755)
        nmap = fakebin / "nmap"
        nmap.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' '<nmaprun>' "
            "'<host><status state=\"up\"/><address addr=\"192.168.7.1\" addrtype=\"ipv4\"/><hostnames><hostname name=\"my-router.local\"/></hostnames></host>' "
            "'<host><status state=\"up\"/><address addr=\"192.168.7.55\" addrtype=\"ipv4\"/><hostnames><hostname name=\"laserjet-office.local\"/></hostnames></host>' "
            "'</nmaprun>'\n"
        )
        nmap.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env.get("PATH", "")
        report = self.run_cmd(str(self.bin / "wifi"), "devices", input_text="2\nl\n0\n")
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        self.assertIn("Devices: 2", report.stdout)
        self.assertIn("SELECTED DEVICE", report.stdout)
        self.assertIn("possible printer", report.stdout)
        self.assertIn("my-router.local", report.stdout)
        reports = list((self.home / ".local/share/autowifi/menu-reports").glob("*.json"))
        self.assertEqual(len(reports), 1)
        data = json.loads(reports[0].read_text())
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["kind"], "lan")

    def test_dstike_live_hub_and_saved_port_selection(self):
        self.install()
        version = self.run_cmd(str(self.bin / "autowifi-deauther-menu"), "--version")
        self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
        self.assertIn("2026.11.06.3", version.stdout)
        self.assertIn(str(self.checkout / "extensions/deauther_console.py"), version.stdout)
        # No device attached, but port AUTO must be usable with no sudo.
        hub = self.run_cmd(str(self.bin / "wifi"), "deauther",
                           input_text="2\n0\n0\n")
        self.assertEqual(hub.returncode, 0, hub.stdout + hub.stderr)
        self.assertIn("DSTIKE ESP8266 / RALINK", hub.stdout)
        settings = self.home / ".config/autowifi-kali-setup/dstike-port.json"
        self.assertEqual(json.loads(settings.read_text())["port"], "")
        self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
        self.install()
        self.assertEqual(json.loads(settings.read_text())["port"], "")
        cached = self.run_cmd(str(self.bin / "wifi"), "cached")
        self.assertNotIn("unrecognized arguments: cached", cached.stderr)

    def test_monitor_service_installed_but_not_automatically_enabled(self):
        self.install()
        unit = self.home / ".config/systemd/user/autowifi-monitor.service"
        self.assertTrue(unit.is_file())
        source = unit.read_text()
        self.assertIn("autowifi-autopilot watch --interval 300", source)
        self.assertIn("NoNewPrivileges=true", source)
        self.assertNotIn("ExecStart=/usr/bin/sudo", source)
        version = self.run_cmd(str(self.bin / "wifi"), "auto", "status")
        self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
        self.assertIn("AutoWIFI background monitor", version.stdout)
        self.assertFalse(list((self.home / ".local/state/autowifi/monitor").glob("events.jsonl")))

    def test_continuous_monitor_has_foreground_and_bounded_interval(self):
        self.install()
        invalid = self.run_cmd(str(self.bin / "wifi"), "auto",
                               "watch", "--interval", "10")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("--interval must be 300 to 3600 seconds", invalid.stderr)

    def test_autopilot_runs_real_launcher_with_mock_local_network(self):
        self.install()
        fakebin = self.home / "auto-fakebin"
        fakebin.mkdir()
        helper_commands = {
            "iw": "#!/bin/sh\nprintf 'phy#1\\n  Interface wlan2\\n    type managed\\n'\n",
            "nmcli": (
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *GENERAL.STATE*) printf '30 (disconnected)\\n' ;;\n"
                "  *wifi*) printf '%s\\n' 'MyHome:AA\\:BB\\:CC\\:DD\\:EE\\:01:6:2437 MHz:87:WPA2' ;;\n"
                "esac\n"
            ),
            "ip": (
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *addr*) printf '%s\\n' '[{\"ifname\":\"eth0\",\"operstate\":\"UP\",\"addr_info\":[{\"family\":\"inet\",\"scope\":\"global\",\"local\":\"192.168.7.3\",\"prefixlen\":24}]}]' ;;\n"
                "  *route*) printf '%s\\n' '[{\"dev\":\"eth0\"}]' ;;\n"
                "  *neigh*) printf '%s\\n' '[]' ;;\n"
                "esac\n"
            ),
            "nmap": (
                "#!/bin/sh\n"
                "printf '%s\\n' '<nmaprun>' "
                "'<host><status state=\"up\"/><address addr=\"192.168.7.1\" addrtype=\"ipv4\"/><hostnames><hostname name=\"gateway.local\"/></hostnames></host>' "
                "'<host><status state=\"up\"/><address addr=\"192.168.7.55\" addrtype=\"ipv4\"/><hostnames><hostname name=\"laserjet.local\"/></hostnames></host>' "
                "'</nmaprun>'\n"
            ),
        }
        for name, script in helper_commands.items():
            executable = fakebin / name
            executable.write_text(script)
            executable.chmod(0o755)
        self.env["PATH"] = str(fakebin) + os.pathsep + self.env.get("PATH", "")
        config = self.home / ".config/autowifi-kali-setup/interface-selection.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"version": 1, "lan": "eth0", "wireless": "wlan2"}))
        result = self.run_cmd(str(self.bin / "wifi"), "auto", "once")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[1/3] WI-FI DISCOVERY", result.stdout)
        self.assertIn("Observed 1 access point(s)", result.stdout)
        self.assertIn("MyHome", result.stdout)
        self.assertIn("Devices: 2", result.stdout)
        self.assertIn("DSTIKE ESP8266", result.stdout)
        wifi_reports = list((self.home / ".local/share/autowifi/menu-reports").glob("*.json"))
        self.assertEqual(len(wifi_reports), 2)
        summary_reports = list((self.home / ".local/share/autowifi/autopilot").glob("*.json"))
        self.assertEqual(len(summary_reports), 1)
        self.assertEqual(summary_reports[0].stat().st_mode & 0o777, 0o600)

    def test_bomb_home_ssid_editor_and_simulation_survive_install(self):
        self.install()
        editor = self.run_cmd(str(self.bin / "wifi"), "networks",
                              input_text="a\nMyOwnedSSID\nq\n")
        self.assertEqual(editor.returncode, 0, editor.stdout + editor.stderr)
        config = self.home / ".config/autowifi-kali-setup/bomb-networks.json"
        self.assertEqual(json.loads(config.read_text())["networks"], ["MyOwnedSSID"])
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        sim = self.run_cmd(str(self.bin / "wifi"), "bomb", input_text="y\n")
        self.assertEqual(sim.returncode, 0, sim.stdout + sim.stderr)
        reports = list((self.home / ".local/share/autowifi/bomb").glob("*.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text())
        self.assertEqual(report["wireless_frames_transmitted"], 0)
        self.assertTrue(report["simulation_only"])
        # Reinstallation never overwrites the local SSID list.
        self.install()
        self.assertEqual(json.loads(config.read_text())["networks"], ["MyOwnedSSID"])

    def test_installer_rejects_wrong_branch_without_overwriting_launchers(self):
        self.install()
        result = subprocess.run(
            ["git", "-C", str(self.checkout), "checkout", "-b", "not-main"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        refused = self.run_cmd("bash", str(self.checkout / "setup-menu.sh"))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("not 'main'", refused.stderr)
        self.assertTrue((self.bin / "wifi").is_file())


if __name__ == "__main__":
    unittest.main()

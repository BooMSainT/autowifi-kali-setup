#!/usr/bin/env python3
"""Simple, safe menu for AutoWIFI on Kali.

LAN inventory uses the currently connected router interface.
Nearby Wi-Fi survey explicitly prefers the known Ralink RT3070 USB adapter.
The ESP8266 is its own separate USB-serial radio, not this adapter.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
import queue
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

RALINK_USB_IDS = {("148f", "3070"), ("148f", "2870")}
BIN_DIR = Path.home() / ".local" / "bin"
REPORT_DIR = Path.home() / ".local/share/autowifi/menu-reports"
MAX_REPORT_OUTPUT = 512_000
MENU_VERSION = "2026.11.06.2"
CONFIG_PATH = Path.home() / ".config/autowifi-kali-setup/interface-selection.json"
INTERFACE_NAME = re.compile(r"^[a-zA-Z0-9_.:-]{1,32}$")


def _capture(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, timeout=12,
                              check=False)
        return proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def parse_wireless_interfaces(iw_text: str) -> list[dict]:
    """Parse names and modes from iw dev; keeps wlan* and renamed interfaces."""
    interfaces: list[dict] = []
    for line in iw_text.splitlines():
        match = re.match(r"^\s*Interface\s+([A-Za-z0-9_.:-]+)\s*$", line)
        if match:
            interfaces.append({"name": match.group(1), "mode": "unknown"})
            continue
        mode = re.match(r"^\s*type\s+(\S+)", line)
        if mode and interfaces:
            interfaces[-1]["mode"] = mode.group(1)
    return interfaces


def usb_identity(interface: str, root: Path = Path("/sys/class/net")) -> tuple[str, str] | None:
    """Read USB vendor/product from sysfs, never infer hardware from wlan numbering."""
    try:
        device = (root / interface / "device").resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    for ancestor in (device, *device.parents):
        if (ancestor / "idVendor").is_file() and (ancestor / "idProduct").is_file():
            try:
                vendor = (ancestor / "idVendor").read_text().strip().lower()
                product = (ancestor / "idProduct").read_text().strip().lower()
                return vendor, product
            except OSError:
                return None
    return None


def default_route() -> str:
    raw = _capture(["ip", "-j", "-4", "route", "show", "default"])
    try:
        for route in json.loads(raw or "[]"):
            if route.get("dev"):
                return str(route["dev"])
    except (ValueError, TypeError):
        pass
    return ""


def adapters() -> list[dict]:
    result = []
    for item in parse_wireless_interfaces(_capture(["iw", "dev"])):
        identity = usb_identity(item["name"])
        result.append({
            **item,
            "usb_id": ":".join(identity) if identity else "not identified as USB",
            "is_ralink": identity in RALINK_USB_IDS,
        })
    return result


def lan_interfaces() -> list[dict]:
    """Connected private IPv4 networks suitable for AutoWIFI LAN discovery."""
    raw = _capture(["ip", "-j", "-4", "addr", "show"])
    try:
        items = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    result: list[dict] = []
    for item in items:
        name = item.get("ifname", "")
        if name == "lo" or not INTERFACE_NAME.fullmatch(name):
            continue
        for address in item.get("addr_info", []):
            if address.get("family") != "inet" or address.get("scope") != "global":
                continue
            try:
                addr = ipaddress.ip_interface(
                    f"{address['local']}/{address['prefixlen']}"
                )
            except (KeyError, ValueError, TypeError):
                continue
            if (addr.network.is_private and not addr.ip.is_link_local
                    and not addr.ip.is_loopback and addr.network.num_addresses <= 4096):
                result.append({
                    "name": name,
                    "ipv4": str(addr.ip),
                    "subnet": str(addr.network),
                    "state": item.get("operstate", "UNKNOWN"),
                })
    return result


def load_settings(path: Path | None = None) -> dict[str, str]:
    """The selected interfaces are local preferences, never a routing change."""
    location = path or CONFIG_PATH
    if not location.exists():
        return {"lan": "", "wireless": ""}
    try:
        data = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read interface preferences: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unknown interface preferences format; not overwriting the file")
    result = {}
    for field in ("lan", "wireless"):
        value = data.get(field, "")
        if not isinstance(value, str) or (
            value and not INTERFACE_NAME.fullmatch(value)
        ):
            raise ValueError(f"Invalid saved {field} interface; not overwriting the file")
        result[field] = value
    return result


def save_settings(settings: dict[str, str], path: Path | None = None) -> None:
    location = path or CONFIG_PATH
    current = {
        "lan": settings.get("lan", ""),
        "wireless": settings.get("wireless", ""),
    }
    for key, value in current.items():
        if not isinstance(value, str) or (
            value and not INTERFACE_NAME.fullmatch(value)
        ):
            raise ValueError(f"Invalid interface name for {key}")
    location.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    location.parent.chmod(0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=location.parent,
            prefix=".interfaces-", suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump({"version": 1, **current}, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, location)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def show_adapters() -> None:
    route = default_route()
    settings = load_settings()
    print("\n=== AutoWIFI: SELECTABLE NETWORK INTERFACES ===")
    print(f"System default route: {route or 'none'} (unchanged by this menu)")
    print(f"LAN device discovery selection: {settings['lan'] or 'AUTO (connected private LAN)'}")
    print(f"Nearby Wi-Fi survey selection: {settings['wireless'] or 'AUTO (Ralink USB preferred)'}")
    print("\nLAN interfaces with a private IPv4 address:")
    connected = lan_interfaces()
    if not connected:
        print("  None available. Check your Wi-Fi/Ethernet connection.")
    for item in connected:
        note = " [DEFAULT ROUTE]" if item["name"] == route else ""
        print(f"  {item['name']}: {item['ipv4']}, {item['subnet']}, {item['state']}{note}")

    print("\nWi-Fi radios (usable for wireless surveys):")
    radios = adapters()
    if not radios:
        print("  None recognized by iw dev. A USB listing alone is not enough.")
    for item in radios:
        role = "Ralink USB" if item["is_ralink"] else "Other wireless radio"
        note = " [DEFAULT ROUTE — protect connection]" if item["name"] == route else ""
        bands = radio_bands(item["name"])
        supported = ", ".join(bands) if bands else "band information unavailable"
        print(f"  {item['name']}: {item['mode']}, {item['usb_id']}, "
              f"{role}, bands: {supported}{note}")
    print("\nEthernet (eth0) can be used for LAN discovery but NOT Wi-Fi radio surveys.")
    print("Changing these selections does not change Linux's default route or Wi-Fi connection.")


def _pick_from_list(label: str, names: list[tuple[str, str]]) -> str | None:
    print(f"\nSelect {label}:")
    print("  0. Automatic")
    for number, (name, info) in enumerate(names, 1):
        print(f"  {number}. {name}  {info}")
    try:
        raw = input("Number (Enter to cancel): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not raw:
        return None
    if not raw.isdecimal() or not 0 <= int(raw) <= len(names):
        print("Invalid selection.")
        return None
    return "" if raw == "0" else names[int(raw) - 1][0]


def adapter_settings() -> int:
    """Configure which existing interface each assessment uses, without changing routes."""
    while True:
        try:
            show_adapters()
        except ValueError as exc:
            print(f"Saved preferences need attention: {exc}")
            return 2
        print("\n1. Set LAN discovery interface (eth0, wlan0, etc.)")
        print("2. Set nearby Wi-Fi survey radio (wlan0, wlan2, etc.)")
        print("3. Reset BOTH to automatic")
        print("0. Back")
        try:
            choice = input("Choice: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if choice == "0" or not choice:
            return 0
        if choice == "3":
            save_settings({"lan": "", "wireless": ""})
            print("Both restored to automatic selection.")
            continue
        if choice not in ("1", "2"):
            print("Choose 0, 1, 2 or 3.")
            continue
        settings = load_settings()
        if choice == "1":
            fields = []
            for item in lan_interfaces():
                if item["name"] not in [name for name, _ in fields]:
                    fields.append((item["name"], f"({item['subnet']})"))
            selected = _pick_from_list("LAN discovery interface", fields)
            key = "lan"
        else:
            fields = [
                (item["name"], "(Ralink USB)" if item["is_ralink"] else
                 f"({item['mode']}, {item['usb_id']})")
                for item in adapters()
            ]
            selected = _pick_from_list("Wi-Fi survey radio", fields)
            key = "wireless"
        if selected is None:
            continue
        settings[key] = selected
        save_settings(settings)
        print(f"Saved {key} selection: {selected or 'AUTO'}.")
        if key == "wireless":
            print("An in-use Wi-Fi uplink remains protected from monitor-mode changes.")


def _write_report(data: dict, directory: Path | None = None) -> Path:
    """Store private machine-readable observations without modifying Git."""
    target = directory or REPORT_DIR
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)
    date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = target / f"{date}-{uuid.uuid4().hex[:8]}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target,
            prefix=".menu-report-", suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def parse_report_summary(kind: str, output: str) -> dict:
    """Extract only verifiable values from deterministic tool output."""
    result: dict = {"count": None, "access_points": []}
    marker = "Access points detected:" if kind == "wifi" else "Devices:"
    for line in output.splitlines():
        if line.startswith(marker):
            match = re.fullmatch(r"\s*(\d+)\s*", line[len(marker):])
            if match:
                result["count"] = int(match.group(1))
                break
    if kind != "wifi":
        return result
    started = False
    for line in output.splitlines():
        if line.strip() == "POWER\tBSSID\tCHANNEL\tENCRYPTION\tSSID":
            started = True
            continue
        if not started:
            continue
        parts = line.split("\t", 4)
        if len(parts) != 5:
            if line.strip() and result["access_points"]:
                break
            continue
        power, bssid, channel, encryption, ssid = parts
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", bssid):
            continue
        result["access_points"].append({
            "power": power, "bssid": bssid,
            "channel": channel, "encryption": encryption, "ssid": ssid,
        })
    return result


def _run_recorded(name: str, *args: str, kind: str, interface: str,
                  report_out: list[dict] | None = None) -> int:
    """Show live tool output, a heartbeat, and a private timestamped result."""
    path = BIN_DIR / name
    executable = str(path) if path.is_file() else shutil.which(name)
    if not executable:
        print(f"Missing {name}. Install AutoWIFI first.")
        return 1

    cmd = [executable, *args]
    # A scan that needs sudo must authenticate in the original controlling
    # terminal. The scanner itself captures stdout and cannot serve as a tty.
    if name in {"autowifi-direct", "autowifi-devices"} and sys.stdin.isatty():
        if not shutil.which("sudo"):
            print("sudo is missing; this scan requires elevated access.")
            return 1
        print("Kali may ask for your sudo password now:", flush=True)
        try:
            auth = subprocess.run(["sudo", "-v"], check=False)
        except OSError as exc:
            print(f"Could not request sudo access: {exc}")
            return 1
        if auth.returncode:
            print("Sudo authentication failed. The scan was not started.")
            return auth.returncode
    print("Running the deterministic AutoWIFI tool. Ctrl+C stops this check.", flush=True)
    started = datetime.now(timezone.utc).isoformat()
    lines: list[str] = []
    bytes_seen = 0
    truncated = False
    status = 1
    proc = None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, process_group=0,
        )
        output: queue.Queue[str | None] = queue.Queue()

        def read_lines() -> None:
            if proc.stdout is not None:
                for line in proc.stdout:
                    output.put(line)
            output.put(None)

        threading.Thread(target=read_lines, daemon=True).start()
        done = False
        next_heartbeat = time.monotonic() + 10
        while not done:
            try:
                line = output.get(timeout=0.5)
                if line is None:
                    done = True
                else:
                    print(line, end="", flush=True)
                    size = len(line.encode("utf-8"))
                    if bytes_seen + size <= MAX_REPORT_OUTPUT:
                        lines.append(line)
                        bytes_seen += size
                    else:
                        truncated = True
            except queue.Empty:
                pass
            if time.monotonic() >= next_heartbeat and proc.poll() is None:
                print("Scan is still running; live results will appear when ready.",
                      flush=True)
                next_heartbeat = time.monotonic() + 10
        status = proc.wait()
    except KeyboardInterrupt:
        print("\nStopping the scan...", flush=True)
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        status = 130
    except OSError as exc:
        print(f"Unable to launch {name}: {exc}")
        status = 1

    report_output = "".join(lines)
    values = parse_report_summary(kind, report_output)
    record = {
        "version": 1, "timestamp": started, "kind": kind,
        "interface": interface, "command_name": name,
        "arguments": list(args), "exit_code": status,
        "count": values["count"],
        "access_points": values["access_points"],
        "output_truncated": truncated,
        "raw_output": report_output,
    }
    if report_out is not None:
        report_out.append(record)
    try:
        report = _write_report(record)
        print(f"\nSaved private {kind} report: {report}")
    except OSError as exc:
        print(f"Could not save report: {exc}")
    if status:
        print(f"The check ended with exit code {status}. Review the output above.")
    elif values["count"] is None:
        print("The tool returned success, but no verifiable count was found.")
    return status


def recent_reports() -> int:
    """Read only past reports from our app directory; never run stored commands."""
    if not REPORT_DIR.exists():
        print("No saved reports yet. Run menu option 1 or 2 first.")
        return 0
    files = sorted(REPORT_DIR.glob("*.json"), key=lambda f: f.name, reverse=True)[:10]
    if not files:
        print("No saved reports yet. Run menu option 1 or 2 first.")
        return 0
    print("\n=== Recent AutoWIFI reports ===")
    records: list[tuple[Path, dict]] = []
    for file in files:
        try:
            report = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or report.get("version") != 1:
                raise ValueError("Unknown report version")
        except (OSError, ValueError):
            continue
        records.append((file, report))
        count = report.get("count")
        summary = "not established" if count is None else str(count)
        print(f"  {len(records)}. {report.get('timestamp', '?')}: "
              f"{report.get('kind', '?')} on {report.get('interface', '?')} — "
              f"{summary} observed, exit {report.get('exit_code', '?')}")
    if not records:
        print("No readable reports found.")
        return 0
    try:
        selection = input("Number to view full output (Enter to return): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not selection:
        return 0
    if not selection.isdecimal() or not 1 <= int(selection) <= len(records):
        print("Invalid report number.")
        return 1
    _, report = records[int(selection) - 1]
    print("\n=== Saved observation (not a live scan) ===")
    print(report.get("raw_output") or "(No output captured)")
    return 0


def _probe_wifi_link(interface: str) -> str:
    """Fail closed: unknown association state must not be treated as free radio."""
    try:
        proc = subprocess.run(
            ["iw", "dev", interface, "link"],
            text=True, capture_output=True, check=False, timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    response = proc.stdout.strip().lower()
    if response.startswith("not connected"):
        return "disconnected"
    if response.startswith("connected to "):
        return "connected"
    return "unknown"


def radio_bands(interface: str) -> list[str]:
    """Best-effort per-PHY band detection from iw, not a global adapter guess."""
    device_info = _capture(["iw", "dev", interface, "info"])
    match = re.search(r"(?m)^\s*wiphy\s+(\d+)\s*$", device_info)
    if not match:
        return []
    phy_info = _capture(["iw", "phy", f"phy{match.group(1)}", "info"])
    frequencies = [int(v) for v in re.findall(r"\*\s+(\d{4})\s+MHz", phy_info)]
    bands = []
    if any(2400 <= v < 2500 for v in frequencies):
        bands.append("2.4 GHz")
    if any(4900 <= v < 5925 for v in frequencies):
        bands.append("5 GHz")
    if any(5925 <= v <= 7125 for v in frequencies):
        bands.append("6 GHz")
    return bands


def doctor() -> int:
    """Read-only, actionable diagnostics; no sudo or interface mode changes."""
    print("\n=== AutoWIFI Diagnostics (read-only) ===")
    print(f"  Menu version: {MENU_VERSION}")
    print(f"  Running Python from: {Path(__file__).resolve()}")
    if Path(__file__).resolve().parent.name != "extensions":
        print("  WARNING: An installed Python copy is running, not Git source.")
        print("  Refresh once: cd ~/autowifi-kali-setup && bash setup-menu.sh")
    dependencies = ["ip", "iw", "nmcli", "rfkill", "nmap", "airmon-ng", "airodump-ng"]
    for dependency in dependencies:
        print(f"  {dependency}: {'installed' if shutil.which(dependency) else 'MISSING'}")
    for wrapper in ("autowifi-direct", "autowifi-devices", "autowifi-deauther"):
        available = (BIN_DIR / wrapper).is_file() or shutil.which(wrapper)
        print(f"  {wrapper}: {'installed' if available else 'MISSING'}")
    if shutil.which("nmcli"):
        manager = _capture(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"]).strip()
        print("  NetworkManager devices:")
        print("    " + manager.replace("\n", "\n    ") if manager else
              "    NetworkManager is not responding or reports no devices.")
    if shutil.which("rfkill"):
        blocked = _capture(["rfkill", "list"]).strip()
        print("  Radio blocking (rfkill):")
        print("    " + blocked.replace("\n", "\n    ") if blocked else
              "    No rfkill status was available.")
    route = default_route()
    print(f"  Default LAN route: {route or 'not found'}")
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"  Saved interface settings: ERROR — {exc}")
        print("  Correct them from menu option 3 after checking the local config file.")
        return 2
    print(f"  LAN selection: {settings['lan'] or 'AUTO'}")
    print(f"  Wi-Fi radio selection: {settings['wireless'] or 'AUTO (Ralink preferred)'}")
    connected = lan_interfaces()
    if not connected:
        print("  No eligible connected private LAN. Connect the laptop to your router.")
    elif settings["lan"] and not any(x["name"] == settings["lan"] for x in connected):
        print(f"  Selected LAN interface {settings['lan']} is not connected to a usable subnet.")
    radios = adapters()
    if not radios:
        print("  No Wi-Fi radios reported by iw. Check the adapter driver or USB port.")
    for radio in radios:
        bands = radio_bands(radio["name"])
        band_text = ", ".join(bands) if bands else "unknown"
        association = "monitor mode" if radio["mode"] == "monitor" else _probe_wifi_link(radio["name"])
        print(f"  {radio['name']}: {radio['mode']}, "
              f"{'Ralink USB' if radio['is_ralink'] else radio['usb_id']}, "
              f"bands: {band_text}, link: {association}")
        if radio["is_ralink"]:
            print("    RT3070-class adapters are 2.4 GHz only; 5/6 GHz SSIDs need another radio.")
    if settings["wireless"] and not any(x["name"] == settings["wireless"] for x in radios):
        print(f"  Selected Wi-Fi radio {settings['wireless']} is no longer present.")
    print("  If you see only one AP, inspect the supported bands and select a different")
    print("  interface under menu option 3; compare with your router's enabled SSIDs.")
    print("  This diagnostic does not transmit frames or change any network settings.")
    return 0


def _run_program(name: str, *args: str) -> int:
    path = BIN_DIR / name
    executable = str(path) if path.is_file() else shutil.which(name)
    if not executable:
        print(f"Missing {name}; install AutoWIFI before using this option.")
        return 1
    try:
        return subprocess.run([executable, *args], check=False).returncode
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except OSError as exc:
        print(f"Could not launch {name}: {exc}")
        return 1


def connected_devices() -> int:
    """Aligned device table and interactive selected-host inspection."""
    settings = load_settings()
    route = default_route()
    selected = settings["lan"]
    print("\n=== LAN DEVICE INVENTORY ===")
    if selected:
        valid = {item["name"] for item in lan_interfaces()}
        if selected not in valid:
            print(f"Selected LAN interface {selected!r} has no usable private IPv4 subnet.")
            print("Connect it to your router or use option 3 to choose another interface.")
            print("Not silently switching to another adapter.")
            return 1
        print(f"Using saved LAN interface: {selected}")
        return _run_program("autowifi-lan", "--interface", selected)
    print(f"Using automatic LAN interface (system route: {route or 'not found'}).")
    print("The scanner will identify responsive devices and offer numbered details.")
    return _run_program("autowifi-lan")


def choose_survey_radio() -> str | None:
    """Select explicit Wi-Fi radio or Ralink by USB ID; avoid connected uplinks."""
    radios = adapters()
    settings = load_settings()
    override = settings["wireless"]
    if override:
        found = next((item for item in radios if item["name"] == override), None)
        if not found:
            print(f"Saved Wi-Fi survey interface {override!r} is no longer available.")
            print("Choose a different radio in menu option 3; no silent fallback.")
            return None
        selected = found["name"]
        print(f"Using your manually selected Wi-Fi radio: {selected}")
    else:
        known = [item for item in radios if item["is_ralink"]]
        if not known:
            print("AUTO requires a detected Ralink RT3070/RT2870 interface.")
            print(_capture(["lsusb"]).strip() or "USB listing unavailable.")
            print("Select another Wi-Fi radio explicitly under menu option 3.")
            return None
        if len(known) == 1:
            selected = known[0]["name"]
        else:
            print("Multiple Ralink USB radios found; choose one in option 3.")
            return None
        print(f"Using AUTO-selected Ralink USB Wi-Fi radio: {selected}")

    if selected == default_route():
        print(f"{selected} is currently carrying your default network route.")
        print("A monitor-mode survey could disconnect it. No changes made.")
        print("Connect via another interface or select another Wi-Fi radio.")
        return None

    chosen = next((item for item in radios if item["name"] == selected), None)
    if chosen is None:
        print("Selected radio disappeared while checking it; no scan attempted.")
        return None
    if chosen["mode"] != "monitor":
        state = _probe_wifi_link(selected)
        if state != "disconnected":
            print(f"Cannot safely use {selected}: Wi-Fi association state is {state}.")
            print("Only disconnected Wi-Fi adapters are eligible for monitor-mode scans.")
            print("Choose another adapter under menu option 3; no changes made.")
            return None
    return selected


def choose_inventory_radio() -> str | None:
    """Choose an explicitly configured or identified radio for managed scanning.

    A managed-mode scan does not change adapter mode. An already-associated
    Wi-Fi uplink can be scanned by NetworkManager, though traffic may briefly
    slow while it surveys channels.
    """
    radios = adapters()
    preferred = load_settings()["wireless"]
    if preferred:
        item = next((radio for radio in radios if radio["name"] == preferred), None)
        if item is None:
            print(f"Selected Wi-Fi radio {preferred!r} is absent; option 3 can change it.")
            return None
    else:
        matches = [radio for radio in radios if radio["is_ralink"]]
        if len(matches) != 1:
            print("AUTO needs exactly one detected Ralink radio for Wi-Fi discovery.")
            print("Select your preferred Wi-Fi radio in menu option 3.")
            return None
        item = matches[0]
    name = item["name"]
    if item["mode"] != "managed":
        print(f"{name} is in {item['mode']} mode. Managed-mode scanning needs a")
        print("managed adapter. Use option 3 to select another Wi-Fi interface,")
        print("or select the advanced monitor-mode survey from the scan menu.")
        return None
    print(f"Using {name} (managed-mode NetworkManager scan).")
    if name == default_route():
        print("This interface carries your current connection. A normal Wi-Fi")
        print("rescan can briefly increase latency, but it won't change monitor mode.")
    return name


def _save_scanned_home_ssid(record: dict) -> bool:
    """Only explicitly approved visible SSIDs can enter the BOMB home list."""
    ssid = record.get("ssid", "")
    if ssid in ("", "<hidden>"):
        print("This AP does not broadcast a usable name; no SSID was saved.")
        return False
    try:
        if input(f"Is {ssid!r} one of YOUR home networks? Add it? [y/N]: ").strip().lower() != "y":
            print("Not added.")
            return False
    except (EOFError, KeyboardInterrupt):
        print("\nNot added.")
        return False
    # Existing BOMB module contains only local configuration and offline
    # simulation helpers. It does not send traffic or change Wi-Fi interfaces.
    try:
        import bomb_simulation as bomb
        current = bomb.load_networks()
        if ssid in current:
            print("Already saved in your BOMB home-network list.")
            return True
        bomb.save_networks(bomb.add_network(current, ssid))
    except (ValueError, OSError, ImportError) as exc:
        print(f"Could not save SSID: {exc}")
        return False
    print(f"Saved {ssid!r} as an explicitly selected home network.")
    return True


def post_scan_choices(report: dict, rescan=None) -> int:
    """Give numbered next steps after scan, without starting an attack."""
    observations = report.get("access_points") or []
    while True:
        print("\n=== CHOOSE A NETWORK OR NEXT ACTION ===")
        if observations:
            for n, item in enumerate(observations, 1):
                print(f"  {n}. {item['ssid']} | channel {item['channel']} | "
                      f"{item['power']} | {item['encryption']}")
        else:
            print("  No verified networks in this scan.")
            print("  That does not establish that no networks exist.")
        print("  R. Rescan this adapter")
        print("  D. Diagnose adapters and Wi-Fi support")
        print("  M. Advanced monitor-mode survey (may require sudo)")
        print("  B. Back to main menu")
        try:
            choice = input("\nChoose a network number or R/D/M/B: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("b", "q", "0", ""):
            return 0
        if choice == "r":
            return rescan() if rescan else nearby_wifi()
        if choice == "d":
            doctor()
            continue
        if choice == "m":
            return advanced_monitor_survey()
        if not choice.isdecimal() or not 1 <= int(choice) <= len(observations):
            print("Choose one of the listed numbers or actions.")
            continue

        item = observations[int(choice) - 1]
        print("\n=== OBSERVED ACCESS POINT ===")
        print(f"  SSID: {item['ssid']}")
        print(f"  BSSID: {item['bssid']}")
        print(f"  Channel: {item['channel']}")
        print(f"  Signal: {item['power']}")
        print(f"  Security advertised: {item['encryption']}")
        print("  This is an observation, not proof of network ownership or a vulnerability.")
        print("\n  A. Add this SSID to MY saved home-network list")
        print("  L. Return to the network list")
        print("  B. Back to main menu")
        try:
            action = input("Choose A/L/B: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if action == "a":
            _save_scanned_home_ssid(item)
        elif action == "b":
            return 0


def advanced_monitor_survey() -> int:
    """Optional, explicit monitor-mode survey. Never the default discovery path."""
    selected = choose_survey_radio()
    if not selected:
        return 1
    radio = next((item for item in adapters() if item["name"] == selected), None)
    ralink = bool(radio and radio["is_ralink"])
    print(f"\n=== ADVANCED MONITOR-MODE SURVEY — {selected} ===")
    print("This may temporarily change radio mode and requires sudo authentication.")
    args = ["networks", "--interface", selected, "--duration", "75"]
    if ralink:
        args.extend(["--channels", ",".join(str(ch) for ch in range(1, 12))])
    recorded: list[dict] = []
    code = _run_recorded("autowifi-direct", *args, kind="wifi",
                         interface=selected, report_out=recorded)
    if recorded:
        return post_scan_choices(recorded[-1])
    return code


def nearby_wifi() -> int:
    """Default Wi-Fi inventory with a real, post-scan interactive network list."""
    selected = choose_inventory_radio()
    if not selected:
        print("The managed scan cannot start with this selection.")
        print("For an idle radio already in monitor mode, you can explicitly")
        print("run the advanced survey without changing your default LAN adapter.")
        try:
            if input("Run advanced monitor-mode survey instead? [y/N]: ").strip().lower() == "y":
                return advanced_monitor_survey()
        except (EOFError, KeyboardInterrupt):
            print()
        return 1
    print("\n=== WI-FI NETWORK DISCOVERY (managed mode) ===")
    print("Using a normal NetworkManager scan, not deauthentication or monitor mode.")
    recorded: list[dict] = []
    status = _run_recorded("autowifi-inventory", "--interface", selected,
                           kind="wifi", interface=selected, report_out=recorded)
    if not recorded:
        print("No results could be recorded. Try wifi doctor.")
        return status or 1
    if status == 130:
        return 130
    return post_scan_choices(recorded[-1], rescan=nearby_wifi)


ACTIONS = {
    "devices": connected_devices,
    "wireless": nearby_wifi,
    "adapters": adapter_settings,
    "deauther": lambda: _run_program("autowifi-deauther", "status"),
    "cached": lambda: _run_program("autowifi-deauther", "cached-aps"),
    "ai": lambda: _run_program("autowifi-ai"),
    "bomb": lambda: _run_program("autowifi-bomb", "run"),
    "networks": lambda: _run_program("autowifi-bomb", "manage"),
    "doctor": doctor,
    "history": recent_reports,
    "status": doctor,
    "reports": recent_reports,
    "scan": nearby_wifi,
    "monitor-scan": advanced_monitor_survey,
    "audit": connected_devices,
}

OPTIONS = [
    ("1", "Find devices connected to my home network", "devices"),
    ("2", "Scan nearby Wi-Fi using selected wireless radio", "wireless"),
    ("3", "View / change LAN and Wi-Fi interfaces (eth0, wlan0...)", "adapters"),
    ("4", "Check DSTIKE ESP8266 USB connection", "deauther"),
    ("5", "Show saved DSTIKE Wi-Fi observations", "cached"),
    ("6", "Open AutoWIFI AI chat", "ai"),
    ("7", "BOMB: simulate incidents for saved home networks", "bomb"),
    ("8", "Edit BOMB networks (add / rename / delete SSIDs)", "networks"),
    ("9", "Diagnose adapters, connectivity and missing tools", "doctor"),
    ("10", "View saved LAN and Wi-Fi scan reports", "history"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoWIFI simple menu and short commands")
    parser.add_argument("--version", action="store_true",
                        help="Show menu version and exact Python source path")
    parser.add_argument("action", nargs="?", default="menu",
                        choices=("menu", *ACTIONS.keys()))
    args = parser.parse_args(argv)
    if args.version:
        print(f"AutoWIFI Menu {MENU_VERSION}")
        print(f"Source: {Path(__file__).resolve()}")
        return 0
    if args.action != "menu":
        return ACTIONS[args.action]()
    while True:
        print("\n" + "=" * 52)
        print(f" AUTOWIFI  |  Home Network & Antenna  |  v{MENU_VERSION}")
        print("=" * 52)
        for number, label, _ in OPTIONS:
            print(f"  {number}. {label}")
        print("  0. Exit")
        try:
            choice = input("\nSelect a number: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if choice in ("0", "q", "quit", "exit"):
            return 0
        selected = next((action for number, _, action in OPTIONS if number == choice), None)
        if selected:
            ACTIONS[selected]()
            continue
        print("Unknown choice. Use 0-10.")


if __name__ == "__main__":
    raise SystemExit(main())

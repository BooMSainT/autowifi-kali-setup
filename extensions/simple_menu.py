#!/usr/bin/env python3
"""Simple, safe menu for AutoWIFI on Kali.

LAN inventory uses the currently connected router interface.
Nearby Wi-Fi survey explicitly prefers the known Ralink RT3070 USB adapter.
The ESP8266 is its own separate USB-serial radio, not this adapter.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

RALINK_USB_IDS = {("148f", "3070"), ("148f", "2870")}
BIN_DIR = Path.home() / ".local" / "bin"
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
        print(f"  {item['name']}: {item['mode']}, {item['usb_id']}, {role}{note}")
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
    settings = load_settings()
    route = default_route()
    selected = settings["lan"]
    print("\n=== Devices connected to your selected private LAN ===")
    if selected:
        valid = {item["name"] for item in lan_interfaces()}
        if selected not in valid:
            print(f"Selected LAN interface {selected!r} has no usable private IPv4 subnet.")
            print("Connect it to your router or use option 3 to choose another interface.")
            print("Not silently switching to another adapter.")
            return 1
        print(f"Using saved LAN interface: {selected}")
        return _run_program("autowifi-devices", "--interface", selected)
    print(f"Using AUTO private-LAN selection (system route: {route or 'not found'}).")
    print("The connected-device audit does not use a passive external antenna.")
    return _run_program("autowifi-devices")


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

    link = _capture(["iw", "dev", selected, "link"])
    if "Connected to " in link:
        print(f"{selected} is associated with a Wi-Fi network.")
        print("Refusing to switch a connected Wi-Fi radio into monitor mode.")
        print("Disconnect it first or select another radio in menu option 3.")
        return None
    return selected


def nearby_wifi() -> int:
    selected = choose_survey_radio()
    if not selected:
        return 1
    print(f"\n=== Passive nearby Wi-Fi survey using {selected} ===")
    print("This observes Wi-Fi access-point broadcasts. The survey tool may")
    print("temporarily switch this radio into monitor mode and restore it afterward.")
    print("No deauthentication frames are sent.")
    return _run_program("autowifi-direct", "networks", "--interface", selected,
                        "--duration", "20")


ACTIONS = {
    "devices": connected_devices,
    "wireless": nearby_wifi,
    "adapters": adapter_settings,
    "deauther": lambda: _run_program("autowifi-deauther", "status"),
    "cached": lambda: _run_program("autowifi-deauther", "cached-aps"),
    "ai": lambda: _run_program("autowifi-ai"),
    "bomb": lambda: _run_program("autowifi-bomb", "run"),
    "networks": lambda: _run_program("autowifi-bomb", "manage"),
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
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoWIFI simple menu and short commands")
    parser.add_argument("action", nargs="?", default="menu",
                        choices=("menu", *ACTIONS.keys()))
    args = parser.parse_args(argv)
    if args.action != "menu":
        return ACTIONS[args.action]()
    while True:
        print("\n" + "=" * 52)
        print(" AUTOWIFI  |  Home Network & Antenna")
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
        print("Unknown choice. Use 0-8.")


if __name__ == "__main__":
    raise SystemExit(main())

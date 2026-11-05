#!/usr/bin/env python3
"""Simple, safe menu for AutoWIFI on Kali.

LAN inventory uses the currently connected router interface.
Nearby Wi-Fi survey explicitly prefers the known Ralink RT3070 USB adapter.
The ESP8266 is its own separate USB-serial radio, not this adapter.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

RALINK_USB_IDS = {("148f", "3070"), ("148f", "2870")}
BIN_DIR = Path.home() / ".local" / "bin"


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


def show_adapters() -> None:
    route = default_route()
    print("\n=== AutoWIFI radio / antenna status ===")
    print(f"Home-network route: {route or 'none found'}")
    if route:
        print(f"LAN device discovery uses {route} (the interface connected to your router).")
    radios = adapters()
    if not radios:
        print("No wireless interfaces found by iw dev. Check the USB adapter driver.")
    for item in radios:
        role = "Ralink RT3070/RT2870 USB radio" if item["is_ralink"] else "Other wireless radio"
        uplink = " [HOME NETWORK UPLINK]" if item["name"] == route else ""
        print(f"  {item['name']}: {item['mode']}, {item['usb_id']}, {role}{uplink}")
    if any(item["is_ralink"] for item in radios):
        print("Detected Ralink: a nearby Wi-Fi survey can explicitly use its radio.")
        print("An attached antenna is passive hardware; software cannot identify the antenna model.")
    else:
        print("Ralink Wi-Fi interface not detected. A USB listing alone does not prove")
        print("that the Wi-Fi driver registered a usable interface.")
    print("DSTIKE ESP8266 is separate. It cannot use this Ralink USB adapter as its antenna.")


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
    route = default_route()
    print("\n=== Devices connected to your home network ===")
    if route:
        print(f"Scanning the private LAN connected through {route}, not the external")
        print("Wi-Fi antenna (unless that adapter itself is connected to the router).")
    else:
        print("No default route detected. The LAN discovery tool will check for")
        print("another directly connected private network.")
    return _run_program("autowifi-devices")


def choose_survey_radio() -> str | None:
    radios = adapters()
    known = [item for item in radios if item["is_ralink"]]
    if not known:
        print("Ralink RT3070 interface is not visible to Kali.")
        print("Checking the laptop's USB listing and Wi-Fi driver:")
        print(_capture(["lsusb"]).strip() or "lsusb unavailable")
        print("No antenna adapter was selected; refusing to silently use internal Wi-Fi.")
        return None
    if len(known) == 1:
        selected = known[0]["name"]
    else:
        print("Multiple matching Ralink interfaces:")
        for index, item in enumerate(known, 1):
            print(f"  {index}. {item['name']} ({item['mode']})")
        try:
            answer = input("Pick number (or Enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not answer.isdecimal() or not 1 <= int(answer) <= len(known):
            return None
        selected = known[int(answer) - 1]["name"]
    if selected == default_route():
        print(f"{selected} is currently carrying your home-network connection.")
        print("A monitor-mode survey could interrupt it. Connect Kali over Ethernet")
        print("or another adapter first; not changing its mode automatically.")
        return None
    return selected


def nearby_wifi() -> int:
    selected = choose_survey_radio()
    if not selected:
        return 1
    print(f"\n=== Passive nearby Wi-Fi survey on {selected} (Ralink USB) ===")
    print("This observes access-point broadcasts. The tool may temporarily change")
    print("this adapter to monitor mode and should restore its prior mode afterward.")
    print("It does not send deauthentication frames.")
    return _run_program("autowifi-direct", "networks", "--interface", selected,
                        "--duration", "20")


ACTIONS = {
    "devices": connected_devices,
    "wireless": nearby_wifi,
    "adapters": lambda: (show_adapters() or 0),
    "deauther": lambda: _run_program("autowifi-deauther", "status"),
    "cached": lambda: _run_program("autowifi-deauther", "cached-aps"),
    "ai": lambda: _run_program("autowifi-ai"),
}

OPTIONS = [
    ("1", "Find devices connected to my home network", "devices"),
    ("2", "Scan nearby Wi-Fi using Ralink antenna adapter", "wireless"),
    ("3", "Show which Wi-Fi adapter / antenna AutoWIFI uses", "adapters"),
    ("4", "Check DSTIKE ESP8266 USB connection", "deauther"),
    ("5", "Show saved DSTIKE Wi-Fi observations", "cached"),
    ("6", "Open AutoWIFI AI chat", "ai"),
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
        print("Unknown choice. Use 0-6.")


if __name__ == "__main__":
    raise SystemExit(main())

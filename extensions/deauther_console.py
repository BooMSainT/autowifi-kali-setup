#!/usr/bin/env python3
"""AutoWIFI USB hardware hub: safe DSTIKE status and separate Ralink controls.

Spacehuhn ESP8266 and Ralink RT3070 are different radios. The ESP8266 cannot
borrow the external Ralink USB radio or antenna through this console.
Only fixed read-only Spacehuhn serial requests are allowed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import deauther_bridge as bridge

PORT_CONFIG = Path.home() / ".config/autowifi-kali-setup/dstike-port.json"
PORT_RE = re.compile(
    r"^/dev/(?:tty(?:USB|ACM)[0-9]+|serial/by-id/[A-Za-z0-9_.:+-]+)$"
)
HUB_VERSION = "2026.11.06.3"


def load_port(path: Path | None = None) -> str:
    location = path or PORT_CONFIG
    if not location.exists():
        return ""
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read DSTIKE port preference: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unsupported DSTIKE port preference format")
    port = payload.get("port", "")
    if not isinstance(port, str) or (port and not PORT_RE.fullmatch(port)):
        raise ValueError("Invalid saved DSTIKE port preference")
    return port


def save_port(port: str, path: Path | None = None) -> None:
    if port and not PORT_RE.fullmatch(port):
        raise ValueError("Choose a detected USB serial port")
    destination = path or PORT_CONFIG
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=".dstike-", suffix=".tmp", delete=False
        ) as output:
            temp = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump({"version": 1, "port": port}, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, destination)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _stable_serial_path(port: str, by_id: Path = Path("/dev/serial/by-id")) -> str:
    """Prefer a stable per-device symlink if it resolves to the detected tty."""
    try:
        real_port = Path(port).resolve(strict=True)
        for link in sorted(by_id.iterdir()):
            if (PORT_RE.fullmatch(str(link))
                    and link.resolve(strict=True) == real_port):
                return str(link)
    except (FileNotFoundError, OSError, RuntimeError):
        pass
    return port


def available() -> list[dict]:
    return bridge.available_ports()


def show_ports(ports: list[dict] | None = None) -> list[dict]:
    devices = available() if ports is None else ports
    print("\n=== DSTIKE USB SERIAL PORTS ===")
    if not devices:
        print("  No USB serial ports detected.")
        print("  Check the data-capable USB cable and connect the DSTIKE.")
        return devices
    for n, item in enumerate(devices, 1):
        vidpid = f"{item.get('vid') or '?'}:{item.get('pid') or '?'}"
        print(f"  {n}. {item['port']} — {item.get('description') or 'USB serial'} ({vidpid})")
        stable = _stable_serial_path(item["port"])
        if stable != item["port"]:
            print(f"     Stable device path: {stable}")
    print("  Detected USB serial ports are not automatically confirmed DSTIKE devices.")
    return devices


def select_port() -> int:
    ports = show_ports()
    print("\n  0. AUTO (detect exactly one likely DSTIKE)")
    try:
        value = input("Choose a port number (Enter to cancel): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return 0
    if not value:
        return 0
    if value == "0":
        save_port("")
        print("DSTIKE serial port: AUTO.")
        return 0
    if not value.isdecimal() or not 1 <= int(value) <= len(ports):
        print("Invalid choice; saved port unchanged.")
        return 1
    selected = _stable_serial_path(ports[int(value) - 1]["port"])
    save_port(selected)
    print(f"Saved DSTIKE serial port: {selected}")
    print("This selects a USB serial connection. It does NOT select a Wi-Fi antenna.")
    return 0


def _print_clean(value: str) -> None:
    """Strip terminal control characters from untrusted device serial output."""
    safe = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
    safe = "".join(ch if ch in "\n\t" or (32 <= ord(ch) <= 126)
                   else " " for ch in safe)
    print(safe[:12000])


def run_read_only(action: str, *, seconds: int = 30, record: bool = False) -> int:
    if action not in ("detect", "status", "cached-aps", "listen"):
        raise ValueError("Unsupported DSTIKE read-only action")
    preferred = load_port()
    port = preferred or None
    if preferred and action != "detect":
        selected, err = bridge.choose_port(preferred, available())
        if err:
            print(f"Saved USB port unavailable: {err}")
            print("Choose another USB serial port with option 2.")
            return 1
        port = selected
    args = argparse.Namespace(
        action=action, port=port, seconds=seconds, json=False, record=record
    )
    result = bridge.snapshot(args)
    if action == "detect":
        show_ports(result.get("ports", []))
        if bridge.serial is None:
            print("Python serial support is missing. Install python3-serial to read firmware.")
    else:
        print(f"\n=== DSTIKE {action.upper()} ===")
        print(f"Connection: {result.get('state', 'unknown')}")
        for field in ("port", "firmware", "info", "system",
                      "cached_access_points", "serial_output", "error", "hint"):
            if result.get(field):
                print(f"{field}:")
                _print_clean(result[field])
    if record:
        try:
            bridge.save_record(result)
            print(f"Saved private serial log: {bridge.LOG_PATH}")
        except OSError as exc:
            print(f"Could not record the serial log: {exc}")
            return 1
    return 0 if result.get("state") in ("connected", "ports_found") else 1


def show_logs() -> int:
    path = bridge.LOG_PATH
    if not path.is_file():
        print("No saved DSTIKE logs yet. Use option 6 to record one.")
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-10:]
    except OSError as exc:
        print(f"Cannot read saved serial logs: {exc}")
        return 1
    reports = []
    print("\n=== SAVED DSTIKE SERIAL LOGS (not live) ===")
    for line in lines:
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
        except ValueError:
            continue
        reports.append(record)
        print(f"  {len(reports)}. {record.get('timestamp', '?')} — "
              f"{record.get('action', '?')} — {record.get('state', '?')}")
    if not reports:
        print("No readable saved events.")
        return 0
    try:
        choice = input("Choose log number to view (Enter to return): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not choice:
        return 0
    if not choice.isdecimal() or not 1 <= int(choice) <= len(reports):
        print("Invalid choice.")
        return 1
    _print_clean(json.dumps(reports[int(choice) - 1], indent=2))
    return 0


def show_ralink() -> int:
    """The ESP8266 cannot use the Ralink USB adapter as its antenna."""
    from simple_menu import adapters, load_settings, default_route, radio_bands
    radios = adapters()
    wifi_preference = load_settings()["wireless"]
    print("\n=== SEPARATE RALINK USB RADIO ===")
    print(f"Wi-Fi scanner selection: {wifi_preference or 'AUTO (Ralink preferred)'}")
    print(f"Default network route: {default_route() or 'none'}")
    if not radios:
        print("No Wi-Fi interfaces reported by iw; try wifi doctor.")
    for item in radios:
        print(f"  {item['name']}: {item['mode']}, {item['usb_id']}, "
              f"{'Ralink USB' if item['is_ralink'] else 'other radio'}")
        bands = radio_bands(item["name"])
        print(f"     Supported bands: {', '.join(bands) if bands else 'unknown'}")
    print("Ralink and ESP8266 are separate physical radios.")
    print("The DSTIKE cannot use the Ralink USB adapter as its radio or antenna.")
    print("Change the Ralink interface from main-menu option 3.")
    return 0


def scan_ralink() -> int:
    """Use USB-ID-confirmed Ralink for the Kali scan; never the ESP8266."""
    from simple_menu import prefer_ralink_for_scans
    if prefer_ralink_for_scans() != 0:
        print("No Ralink radio selected; refusing to scan with an unknown adapter.")
        return 1
    launcher = Path.home() / ".local/bin/autowifi-menu"
    cmd = [str(launcher), "scan"] if launcher.is_file() else [
        sys.executable, str(Path(__file__).with_name("simple_menu.py")), "scan"
    ]
    return subprocess.run(cmd, check=False).returncode


def troubleshooting() -> int:
    print("\n=== USB CONNECTION HELP ===")
    if bridge.serial is None:
        print("MISSING: python3-serial; install with sudo apt install python3-serial")
    else:
        print("Python serial support: installed")
    ports = show_ports()
    preferred = load_port()
    print(f"Saved DSTIKE port: {preferred or 'AUTO'}")
    if preferred and not Path(preferred).exists():
        print("The selected port is missing. Select a different port under option 2.")
    if not ports:
        print("Try a different USB data cable, USB port, and check lsusb.")
    if preferred and Path(preferred).exists():
        print(f"User read/write access: {'yes' if os.access(preferred, os.R_OK | os.W_OK) else 'NO'}")
    print("For serial permission errors: sudo usermod -aG dialout \"$USER\"")
    print("Log out and back in before retrying. Do not use chmod 777.")
    print("This screen does not change radio modes, firmware or Wi-Fi settings.")
    return 0


def menu() -> int:
    while True:
        try:
            preferred = load_port()
        except ValueError as exc:
            print(f"DSTIKE settings error: {exc}")
            return 2
        print("\n" + "=" * 55)
        print(" DSTIKE ESP8266 / RALINK — HARDWARE HUB")
        print("=" * 55)
        print(f" DSTIKE USB port: {preferred or 'AUTO'}")
        print(" 1. Detect connected USB serial devices")
        print(" 2. Choose DSTIKE USB port (saved)")
        print(" 3. Read DSTIKE firmware and system status")
        print(" 4. Show cached DSTIKE access points")
        print(" 5. Listen to USB serial for 30 seconds (read-only)")
        print(" 6. Listen and SAVE serial log (30 seconds)")
        print(" 7. View saved DSTIKE serial logs")
        print(" 8. Show Ralink USB adapter / antenna status")
        print(" 9. Scan nearby Wi-Fi using separate Ralink adapter")
        print("10. USB permissions and connection troubleshooting")
        print(" 0. Back to AutoWIFI menu")
        try:
            choice = input("Select a number: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("0", "q", ""):
            return 0
        try:
            if choice == "1":
                run_read_only("detect")
            elif choice == "2":
                select_port()
            elif choice == "3":
                run_read_only("status")
            elif choice == "4":
                run_read_only("cached-aps")
            elif choice == "5":
                run_read_only("listen")
            elif choice == "6":
                run_read_only("listen", record=True)
            elif choice == "7":
                show_logs()
            elif choice == "8":
                show_ralink()
            elif choice == "9":
                scan_ralink()
            elif choice == "10":
                troubleshooting()
            else:
                print("Choose 0–10.")
        except (OSError, ValueError) as exc:
            print(f"DSTIKE: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="menu",
                        choices=("menu", "detect", "select", "status", "cached",
                                 "listen", "record", "logs", "ralink", "scan",
                                 "doctor"))
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"DSTIKE Hardware Hub {HUB_VERSION} | {Path(__file__).resolve()}")
        return 0
    methods = {
        "menu": menu,
        "detect": lambda: run_read_only("detect"),
        "select": select_port,
        "status": lambda: run_read_only("status"),
        "cached": lambda: run_read_only("cached-aps"),
        "listen": lambda: run_read_only("listen"),
        "record": lambda: run_read_only("listen", record=True),
        "logs": show_logs,
        "ralink": show_ralink,
        "scan": scan_ralink,
        "doctor": troubleshooting,
    }
    return methods[args.action]()


if __name__ == "__main__":
    raise SystemExit(main())

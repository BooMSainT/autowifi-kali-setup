#!/usr/bin/env python3
"""BOMB: an OFFLINE, non-disruptive incident-response rehearsal for saved home SSIDs.

The user manually maintains a local allowlist. The tool generates *synthetic*
disconnect alerts sequentially; it never transmits Wi-Fi frames, controls
network interfaces, or claims to measure real access point resilience.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
from pathlib import Path
import tempfile
import uuid

CONFIG_PATH = Path.home() / ".config/autowifi-kali-setup/bomb-networks.json"
REPORT_DIR = Path.home() / ".local/share/autowifi/bomb"


def validate_ssid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Wi-Fi name must be text")
    if not value or not 1 <= len(value.encode("utf-8")) <= 32:
        raise ValueError("SSID must be 1-32 bytes (including Unicode characters)")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("SSID cannot contain control characters")
    return value


def validate_list(names: list[str]) -> list[str]:
    if not isinstance(names, list) or len(names) > 64:
        raise ValueError("Expected a list of at most 64 explicitly entered SSIDs")
    for name in names:
        validate_ssid(name)
    if len(names) != len(set(names)):
        raise ValueError("Network list contains an exact duplicate SSID")
    return names


def load_networks(path: Path = CONFIG_PATH) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read saved network names at {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"Unrecognized network-list format in {path}; leaving it unchanged")
    return validate_list(data.get("networks"))


def _write_private_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A dedicated local configuration or report directory, not the git checkout.
    path.parent.chmod(0o700)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=".bomb-", suffix=".tmp",
                                         delete=False) as file:
            temp_path = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def save_networks(names: list[str], path: Path = CONFIG_PATH) -> None:
    _write_private_json(path, {"version": 1, "networks": validate_list(list(names))})


def add_network(names: list[str], name: str) -> list[str]:
    validate_ssid(name)
    if name in names:
        raise ValueError("That exact SSID is already saved")
    return validate_list([*names, name])


def rename_network(names: list[str], position: int, new_name: str) -> list[str]:
    if not 0 <= position < len(names):
        raise ValueError("Choose a number shown in the list")
    updated = list(names)
    updated[position] = validate_ssid(new_name)
    return validate_list(updated)


def remove_network(names: list[str], position: int) -> list[str]:
    if not 0 <= position < len(names):
        raise ValueError("Choose a number shown in the list")
    return names[:position] + names[position + 1:]


def print_networks(names: list[str]) -> None:
    print("\nBOMB: SAVED HOME WI-FI NAMES (manually entered, never discovered)")
    if not names:
        print("  No networks saved yet.")
    for index, name in enumerate(names, start=1):
        print(f"  {index}. {name}")


def _choose_index(names: list[str]) -> int:
    if not names:
        raise ValueError("There are no saved networks")
    raw = input(f"Network number (1-{len(names)}): ").strip()
    if not raw.isdecimal():
        raise ValueError("Enter a network number")
    index = int(raw) - 1
    if index not in range(len(names)):
        raise ValueError("That number is not on the list")
    return index


def manage_networks(path: Path = CONFIG_PATH) -> None:
    while True:
        names = load_networks(path)
        print_networks(names)
        print("\n[A] Add    [R] Rename    [D] Delete    [Q] Back")
        try:
            choice = input("Choice: ").strip().lower()
            if choice in ("q", "0", "quit", ""):
                return
            if choice in ("a", "add"):
                name = input("Enter the new home Wi-Fi name (SSID): ")
                updated = add_network(names, name)
            elif choice in ("r", "rename"):
                index = _choose_index(names)
                name = input("New name for that Wi-Fi network: ")
                updated = rename_network(names, index, name)
            elif choice in ("d", "delete", "remove"):
                index = _choose_index(names)
                if input(f"Remove '{names[index]}' from this list? [y/N]: ").strip().lower() != "y":
                    continue
                updated = remove_network(names, index)
            else:
                print("Choose A, R, D, or Q.")
                continue
            save_networks(updated, path)
            print("Saved locally. You don't need to edit code or git pull for name changes.")
        except ValueError as exc:
            print(f"Not changed: {exc}")
        except (EOFError, KeyboardInterrupt):
            print()
            return


def make_simulation(names: list[str], timestamp: str) -> dict:
    """Construct synthetic alert events; not a radio test or resilience measurement."""
    validate_list(names)
    return {
        "label": "BOMB — saved-network incident rehearsal",
        "simulation_only": True,
        "timestamp": timestamp,
        "wireless_frames_transmitted": 0,
        "devices_intentionally_disconnected": 0,
        "real_pmf_protection_verified": False,
        "networks": [
            {
                "ssid": name,
                "order": i,
                "event": "SIMULATED deauthentication/disconnect alert",
                "synthetic": True,
                "network_disruption": "none; no radio traffic transmitted",
                "recommended_followup":
                    "Review PMF/802.11w and WPA3 settings in the router, "
                    "and check router logs for real disconnect events.",
            }
            for i, name in enumerate(names, 1)
        ],
    }


def run_simulation(path: Path = CONFIG_PATH, report_dir: Path = REPORT_DIR) -> int:
    names = load_networks(path)
    if not names:
        print("No home Wi-Fi names saved for BOMB yet.")
        print("Choose how to add your own networks:")
        print("  1. Scan nearby Wi-Fi and SELECT which SSIDs are mine")
        print("  2. Enter my home network names manually")
        print("  0. Back")
        try:
            choice = input("Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 1
        if choice in ("1", "s", "scan"):
            # Use the same numbered network picker as menu option 2.
            # Nothing discovered is automatically marked as user-owned.
            menu = Path.home() / ".local/bin/autowifi-menu"
            if not menu.is_file():
                print("AutoWIFI menu is not installed. Run bash setup-menu.sh first.")
                return 1
            try:
                subprocess.run([str(menu), "scan"], check=False)
            except OSError as exc:
                print(f"Could not launch the network picker: {exc}")
                return 1
        elif choice in ("2", "m", "manual", "y"):
            manage_networks(path)
        else:
            return 1
        names = load_networks(path)
        if not names:
            print("No explicitly selected home SSIDs were saved; nothing simulated.")
            return 1
    print_networks(names)
    print("\nOFFLINE SIMULATION ONLY: mock alerts for the saved SSIDs, in order.")
    print("No nearby networks are automatically included. No packet transmission,")
    print("deauthentication, forced disconnect, or actual protection test occurs.")
    try:
        if input("Simulate these saved networks? [y/N]: ").strip().lower() != "y":
            print("Cancelled.")
            return 0
    except (EOFError, KeyboardInterrupt):
        return 130

    timestamp = datetime.now(timezone.utc).isoformat()
    report = make_simulation(names, timestamp)
    print("\n=== BOMB — SYNTHETIC EVENT REHEARSAL ===")
    for event in report["networks"]:
        print(f"  [{event['order']}/{len(names)}] {event['ssid']}: "
              "synthetic disconnect alert recorded; no outage initiated")

    filename = f"bomb-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.json"
    report_path = report_dir / filename
    _write_private_json(report_path, report)
    print(f"\nSaved report: {report_path}")
    print("Important: this rehearsal cannot establish actual deauth resistance.")
    print("Review PMF (802.11w)/WPA3 settings and real access-point logs separately.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("run", "manage", "list"),
                        default="run")
    args = parser.parse_args(argv)
    try:
        if args.action == "manage":
            manage_networks()
            return 0
        if args.action == "list":
            print_networks(load_networks())
            return 0
        return run_simulation()
    except ValueError as exc:
        print(f"BOMB configuration error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

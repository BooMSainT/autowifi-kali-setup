#!/usr/bin/env python3
"""Autonomous, non-disruptive AutoWIFI diagnostics and local inventory.

One command checks the installed tools, selects the configured wireless radio,
scans observed access points (bounded retry), inventories responsive private-LAN
hosts, and checks a confidently identified DSTIKE USB serial device. It never
runs deauthentication, monitor-mode changes, credential tests, exploit probes,
firmware changes or external-network scans.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import simple_menu as menu
import network_inventory as wifi
import lan_inventory as lan
import deauther_bridge as dstike

VERSION = "2026.11.09.1"
RESULT_DIR = Path.home() / ".local/share/autowifi/autopilot"


def _capture(command: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              check=False, timeout=timeout)
        return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _selected_radio() -> tuple[dict | None, str]:
    """A saved explicit choice wins; otherwise require exactly one USB Ralink."""
    try:
        settings = menu.load_settings()
    except ValueError as exc:
        return None, f"Invalid interface settings: {exc}"
    radios = menu.adapters()
    requested = settings["wireless"]
    if requested:
        chosen = next((radio for radio in radios if radio["name"] == requested), None)
        if chosen is None:
            return None, f"Saved Wi-Fi radio {requested!r} is not present. Use wifi adapters."
    else:
        candidates = [radio for radio in radios if radio["is_ralink"]]
        if not candidates:
            return None, ("No registered Ralink RT3070/RT2870 USB Wi-Fi radio was detected. "
                          "Check the USB cable/port and driver using wifi doctor.")
        if len(candidates) != 1:
            return None, ("Multiple Ralink radios found. Choose the intended adapter "
                          "using wifi adapters; no silent fallback.")
        chosen = candidates[0]
    if chosen["mode"] != "managed":
        return None, (f"{chosen['name']} is in {chosen['mode']} mode. Normal discovery "
                      "requires managed mode; no radio-mode changes were made.")
    return chosen, ""


def _nm_state(interface: str) -> str:
    status, raw = _capture(
        ["nmcli", "-g", "GENERAL.STATE", "device", "show", interface], timeout=8
    )
    if status:
        return "unknown (NetworkManager did not report a state)"
    return raw.splitlines()[0].strip() if raw else "unknown"


def wifi_task() -> dict:
    report = {"state": "not_run", "interface": None, "count": None,
              "retry": False, "error": "", "path": None,
              "observations": None}
    print("\n[1/3] WI-FI DISCOVERY — selected USB radio", flush=True)
    selected, error = _selected_radio()
    if error:
        print(f"  BLOCKED: {error}", flush=True)
        report.update(state="blocked", error=error)
        return report
    name = selected["name"]
    report["interface"] = name
    print(f"  Hardware: {name} ({selected['usb_id']}, {selected['mode']})", flush=True)
    if not shutil.which("nmcli"):
        error = "nmcli is missing. Install NetworkManager or run wifi doctor."
        print(f"  BLOCKED: {error}", flush=True)
        report.update(state="blocked", error=error)
        return report

    state = _nm_state(name)
    print(f"  NetworkManager state: {state}", flush=True)
    if re.search(r"unmanaged|unavailable|unknown", state, re.I):
        print(f"  NetworkManager isn't controlling {name} ({state}).", flush=True)
        print("  Trying the independent kernel Wi-Fi scanner without changing radio mode.",
              flush=True)

    observed, error = wifi.scan(name)
    if not error and not observed:
        print("  Zero APs on first pass. Automatically retrying once...", flush=True)
        report["retry"] = True
        observed, error = wifi.scan(name)
    if error:
        print(f"  FAILED: {error}", flush=True)
        report.update(state="failed", error=error)
    else:
        report.update(state="ok" if observed else "empty", count=len(observed),
                      observations=observed)
        print(f"  Observed {len(observed)} access point(s).", flush=True)
        for item in observed[:30]:
            safe_ssid = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", item["ssid"])
            print(f"    {safe_ssid[:34]:<34} ch {item['channel']:<3} "
                  f"{item['signal_percent']:>3}%  {item['security']}", flush=True)
        if len(observed) > 30:
            print(f"  ... {len(observed) - 30} additional APs in the saved report.")
        if not observed:
            print("  No APs observed after two attempts; this is not proof none exist.")
            print("  Result marked INCONCLUSIVE, not success.")
            print("  Check USB power, rfkill, supported bands and driver with wifi doctor.")
    snapshot = {
        "version": 1, "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "wifi", "interface": name, "command_name": "wifi auto",
        "arguments": ["managed Wi-Fi discovery", "one retry if empty"],
        "exit_code": 0 if not error and observed else 1,
        "count": report["count"],
        "access_points": [{
            "ssid": ap["ssid"], "bssid": ap["bssid"],
            "channel": str(ap["channel"]), "power": f"{ap['signal_percent']}%",
            "encryption": ap["security"],
        } for ap in observed],
        "raw_output": wifi.format_results(name, observed, error),
    }
    try:
        report["path"] = str(menu._write_report(snapshot))
        print(f"  Saved: {report['path']}")
    except OSError as exc:
        print(f"  Report could not be saved: {exc}")
    return report


def lan_task() -> dict:
    summary = {"state": "not_run", "count": None, "interface": None,
               "error": "", "path": None, "devices": None}
    print("\n[2/3] YOUR PRIVATE LAN — responsive devices", flush=True)
    if not shutil.which("nmap"):
        error = "nmap is missing. Install with: sudo apt install nmap"
        print(f"  SKIPPED: {error}")
        summary.update(state="blocked", error=error)
        return summary
    try:
        selected = menu.load_settings()["lan"]
        report = lan.discover(interface=selected, timeout=90)
        lan.print_inventory(report)
        summary.update(
            state="ok", count=report["device_count"],
            interface=report["interface"],
            devices=report["devices"],
            subnet=report["subnet"],
        )
        summary["path"] = str(lan._store(report))
        print(f"  Saved: {summary['path']}")
        if not summary["count"]:
            print("  No responsive hosts found. Check network isolation/router clients.")
    except (ValueError, RuntimeError, OSError) as exc:
        summary.update(state="failed", error=str(exc))
        print(f"  FAILED: {exc}")
    return summary


def dstike_task() -> dict:
    summary = {"state": "not_run", "port": None, "error": ""}
    print("\n[3/3] DSTIKE ESP8266 — USB serial check", flush=True)
    if dstike.serial is None:
        error = "python3-serial is missing; sudo apt install python3-serial"
        print(f"  SKIPPED: {error}")
        summary.update(state="blocked", error=error)
        return summary
    try:
        from deauther_console import load_port
        explicit = load_port()
        ports = dstike.available_ports()
        selected, error = dstike.choose_port(explicit or None, ports)
        if error:
            print(f"  SKIPPED: {error}")
            summary.update(state="blocked", error=error)
            return summary
        summary["port"] = selected
        print(f"  Checking {selected} (fixed read-only serial commands)...", flush=True)
        args = argparse.Namespace(action="status", port=selected, seconds=5)
        result = dstike.snapshot(args)
        summary["state"] = result.get("state", "unknown")
        if summary["state"] == "connected":
            print(f"  Firmware: {result.get('firmware', 'recognized')}")
        else:
            summary["error"] = result.get("error") or result.get("hint") or result.get("state", "")
            print(f"  Could not verify firmware: {summary['error']}")
    except (OSError, ValueError) as exc:
        summary.update(state="failed", error=str(exc))
        print(f"  FAILED: {exc}")
    return summary


def run() -> int:
    print(f"AutoWIFI AUTOPILOT v{VERSION}")
    print("Non-disruptive scans only. No login, password guessing or attack commands.")
    print("USB Ralink handles nearby Wi-Fi; LAN discovery uses its connected interface.")
    print("DSTIKE is physically separate and uses its own serial-connected ESP8266.")
    results = {
        "version": VERSION, "timestamp": datetime.now(timezone.utc).isoformat(),
        "wifi": wifi_task(),
        "lan": lan_task(),
        "dstike": dstike_task(),
    }
    print("\n=== AUTOPILOT SUMMARY ===")
    for label in ("wifi", "lan", "dstike"):
        item = results[label]
        value = item.get("count") if label != "dstike" else item.get("port")
        print(f"  {label.upper():6} {item['state']:<11} "
              f"{value if value is not None else ''} "
              f"{item.get('error', '')}", flush=True)
    print("Commands: wifi scan (choose an AP), wifi devices (device details),")
    print("          wifi doctor (hardware diagnosis), wifi deauther (USB hub).")
    try:
        RESULT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        RESULT_DIR.chmod(0o700)
        path = menu._write_report(results, directory=RESULT_DIR)
        print(f"  Private summary: {path}")
    except OSError as exc:
        print(f"  Summary could not be saved: {exc}")
    # Signal failure of primary Wi-Fi objective without concealing other results.
    return 0 if results["wifi"]["state"] == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", nargs="?", default="watch",
        choices=("watch", "once", "start", "stop", "status", "events"),
        help="watch (default), once, background start/stop/status, or event history",
    )
    parser.add_argument(
        "--interval", type=int, default=300,
        help="Monitoring interval in seconds (300–3600; default 300)",
    )
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"AutoWIFI AUTOPILOT {VERSION} | source: {Path(__file__).resolve()}")
        return 0
    if args.action == "once":
        return run()
    from network_watch import watch, service_control, show_events, MIN_INTERVAL, MAX_INTERVAL
    if args.action == "watch":
        if not MIN_INTERVAL <= args.interval <= MAX_INTERVAL:
            parser.error(f"--interval must be {MIN_INTERVAL} to {MAX_INTERVAL} seconds")
        return watch(interval=args.interval)
    if args.action in ("start", "stop", "status"):
        return service_control(args.action)
    return show_events()


if __name__ == "__main__":
    sys.exit(main())

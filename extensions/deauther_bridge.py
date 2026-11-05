#!/usr/bin/env python3
"""Read-only USB serial integration for Spacehuhn ESP8266 Deauther v2.6.1.

Commands are intentionally fixed: info, sysinfo, and show aps (cached results).
No deauthentication, injection, scanning, or firmware modification commands.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time

LOG_PATH = Path.home() / ".local/share/autowifi/deauther/events.jsonl"
DEVICE_RE = re.compile(r"^/dev/tty(?:USB|ACM)[0-9]+$")
SIGNATURE_RE = re.compile(r"(?:esp8266\s+deauther|deauther\s+v?2|spacehuhn|deauther\.com)", re.I)
MAX_OUTPUT = 16000

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


def available_ports() -> list[dict]:
    if list_ports is None:
        return []
    results = []
    for item in sorted(list_ports.comports(), key=lambda item: item.device):
        if not DEVICE_RE.fullmatch(item.device):
            continue
        results.append({
            "port": item.device,
            "description": item.description,
            "vid": f"{item.vid:04x}" if item.vid is not None else None,
            "pid": f"{item.pid:04x}" if item.pid is not None else None,
            "serial_number": item.serial_number,
        })
    return results


def choose_port(explicit: str | None, ports: list[dict]) -> tuple[str | None, str | None]:
    if explicit:
        requested = Path(explicit)
        try:
            resolved = str(requested.resolve(strict=True))
        except (OSError, RuntimeError):
            return None, f"Port does not exist: {explicit}"
        if not DEVICE_RE.fullmatch(resolved):
            return None, "Only USB serial ports /dev/ttyUSB* or /dev/ttyACM* are supported"
        return resolved, None
    if not ports:
        return None, "No USB serial ports found. Check the micro-USB DATA cable and run lsusb."
    if len(ports) == 1:
        return ports[0]["port"], None

    # Prefer exactly one common ESP8266 USB-to-serial bridge over unrelated ACM devices.
    bridge_ids = {("1a86", "7523"), ("1a86", "55d4"), ("10c4", "ea60"),
                  ("0403", "6001")}
    matches = [item for item in ports if (item["vid"], item["pid"]) in bridge_ids]
    if len(matches) == 1:
        return matches[0]["port"], None
    return None, ("Multiple serial devices found; specify --port /dev/ttyUSB0 "
                  "(see 'autowifi-deauther detect').")


def read_until_quiet(link, seconds: float = 2.5) -> str:
    deadline = time.monotonic() + seconds
    quiet_since = None
    output = bytearray()
    while time.monotonic() < deadline and len(output) < MAX_OUTPUT:
        chunk = link.read(512)
        if chunk:
            output.extend(chunk)
            quiet_since = time.monotonic()
        elif quiet_since is not None and time.monotonic() - quiet_since >= 0.4:
            break
    return output.decode("utf-8", errors="replace").strip()[:MAX_OUTPUT]


def command(link, text: str) -> str:
    # Fixed allowlist; no arbitrary command strings, attacks or settings changes.
    if text not in {"info", "sysinfo", "show aps"}:
        raise ValueError("Unsupported read-only command")
    link.reset_input_buffer()
    link.write((text + "\r\n").encode("ascii"))
    link.flush()
    return read_until_quiet(link)


def connect(port: str, action: str, seconds: int) -> dict:
    if serial is None:
        return {"state": "dependency_missing",
                "error": "Install python3-serial using: sudo apt install python3-serial"}
    try:
        with serial.Serial(port=port, baudrate=115200, timeout=0.2,
                           write_timeout=2) as link:
            # Opening a USB serial port can reset an ESP8266; allow it to boot.
            time.sleep(1.7)
            info = command(link, "info")
            if not SIGNATURE_RE.search(info):
                return {"state": "unrecognized_firmware", "port": port,
                        "info": info[:1200],
                        "hint": "Port opened, but no Spacehuhn Deauther signature was returned."}
            result = {"state": "connected", "port": port,
                      "firmware": "Spacehuhn ESP8266 Deauther",
                      "info": info[:2000]}
            if action == "status":
                result["system"] = command(link, "sysinfo")[:4000]
            elif action == "cached-aps":
                result["cached_access_points"] = command(link, "show aps")[:MAX_OUTPUT]
            elif action == "listen":
                print(f"Listening to {port} for {seconds}s (read-only, no radio commands)...",
                      file=sys.stderr)
                end = time.monotonic() + seconds
                output = bytearray()
                while time.monotonic() < end and len(output) < MAX_OUTPUT:
                    output.extend(link.read(512))
                result["serial_output"] = output.decode("utf-8", errors="replace")[:MAX_OUTPUT]
            return result
    except PermissionError:
        return {"state": "permission_denied", "port": port,
                "error": f"Cannot open {port}. Check membership in the dialout group."}
    except (OSError, serial.SerialException) as exc:
        message = str(exc)
        if "permission denied" in message.lower() or "errno 13" in message.lower():
            return {"state": "permission_denied", "port": port, "error": message}
        return {"state": "connection_failed", "port": port, "error": message}


def snapshot(args: argparse.Namespace) -> dict:
    ports = available_ports()
    result = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "mode": "read_only", "action": args.action, "ports": ports}
    if serial is None:
        result.update({"state": "dependency_missing",
                       "error": "python3-serial is missing"})
        return result
    if args.action == "detect":
        result["state"] = "ports_found" if ports else "no_ports"
        return result
    selected, error = choose_port(args.port, ports)
    if error:
        result.update({"state": "no_device" if not ports else "selection_required",
                       "error": error})
        return result
    result.update(connect(selected, args.action, args.seconds))
    return result


def display(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"AutoWIFI DSTIKE/Spacehuhn USB: {result['state']}")
    for item in result.get("ports", []):
        print(f"  USB port: {item['port']} — {item['description']} "
              f"({item['vid']}:{item['pid']})")
    for field in ("port", "firmware", "info", "system", "cached_access_points",
                  "serial_output", "error", "hint"):
        if result.get(field):
            print(f"\n{field}:\n{result[field]}")
    if result["state"] == "no_device":
        print("\nNext: reconnect using a data-capable USB cable; run lsusb and "
              "sudo dmesg | tail -30")
    if result["state"] == "permission_denied":
        print("\nFix access: sudo usermod -aG dialout \"$USER\""
              "\nThen log out and log back in; don't use chmod 777.")


def save_record(result: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOG_PATH.open("a", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False) + "\n")
    os.chmod(LOG_PATH, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "This is a read-only USB bridge: it cannot initiate a Wi-Fi scan or send "
        "deauthentication frames. To review an existing on-device scan, use "
        "cached-aps; to capture serial messages, use listen --seconds 30 --record."
    ))
    parser.add_argument("action", nargs="?", default="status",
                        choices=("detect", "status", "cached-aps", "listen"))
    parser.add_argument("--port", help="Specific USB serial device or /dev/serial/by-id symlink")
    parser.add_argument("--seconds", type=int, default=30,
                        help="Listen duration in seconds (1–120; default 30)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--record", action="store_true",
                        help="Append output to private local JSONL log (0600)")
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 120:
        parser.error("--seconds must be in the range 1–120")
    result = snapshot(args)
    display(result, args.json)
    if args.record:
        save_record(result)
    return 0 if result["state"] in ("connected", "ports_found") else 1


if __name__ == "__main__":
    raise SystemExit(main())

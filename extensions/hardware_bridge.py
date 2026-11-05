#!/usr/bin/env python3
"""Read-only hardware status bridge for AutoWIFI on Kali Linux.

No radio transmissions, active wireless tests, or arbitrary remote commands.
Pwn-device SSH access is disabled unless the user explicitly configures it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

LOG_PATH = Path.home() / ".local/share/autowifi/hardware/events.jsonl"
FLIPPER_ID_DIR = Path("/dev/serial/by-id")


def flipper_ports() -> list[str]:
    if not FLIPPER_ID_DIR.is_dir():
        return []
    return [str(path) for path in sorted(FLIPPER_ID_DIR.iterdir())
            if path.is_symlink() and "flipper" in path.name.lower()]


def flipper_status(port: str | None) -> dict:
    if not port:
        return {"state": "not_connected", "ports": flipper_ports()}
    if not Path(port).exists():
        return {"state": "not_connected", "port": port, "error": "Serial port not present"}
    try:
        import serial  # provided by Kali's python3-serial package
    except ImportError:
        return {"state": "dependency_missing", "port": port,
                "error": "Install python3-serial: sudo apt install python3-serial"}

    try:
        with serial.Serial(port=port, baudrate=230400, timeout=0.25,
                           write_timeout=2) as link:
            # Read only: the Flipper CLI's official info device command.
            link.reset_input_buffer()
            link.write(b"\r\n")
            time.sleep(0.15)
            link.reset_input_buffer()
            link.write(b"info device\r\n")
            deadline = time.monotonic() + 3
            response = bytearray()
            while time.monotonic() < deadline and len(response) < 16384:
                chunk = link.read(1024)
                if chunk:
                    response.extend(chunk)
                elif response:
                    break
        output = response.decode("utf-8", errors="replace").strip()
        return {"state": "connected" if output else "no_response",
                "port": port, "info": output[:12000]}
    except (OSError, serial.SerialException) as exc:
        return {"state": "unavailable", "port": port, "error": str(exc)}


def _safe_identity(host: str, user: str) -> bool:
    # Restrict SSH destinations to hostnames/IPv4 and ordinary login names.
    return (bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host))
            and ".." not in host
            and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", user)))


def pwn_status(host: str | None, user: str | None, port: int) -> dict:
    if not host or not user:
        return {"state": "not_configured",
                "hint": "Set AUTOWIFI_PWN_HOST and AUTOWIFI_PWN_USER for your own SSH-capable device"}
    if not _safe_identity(host, user) or not 1 <= port <= 65535:
        return {"state": "invalid_configuration", "error": "Invalid SSH host, user or port"}

    # Known-host verification is mandatory. No SSH password prompt or saved credentials.
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=4", "-o", "ConnectionAttempts=1",
           "-p", str(port), f"{user}@{host}", "uname -s && uptime -p"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=8,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state": "unavailable", "host": host, "error": str(exc)}
    if result.returncode:
        return {"state": "unavailable", "host": host,
                "error": result.stderr.strip()[:400] or f"ssh exit {result.returncode}"}
    return {"state": "connected", "host": host, "status": result.stdout.strip()[:400]}


def snapshot(args: argparse.Namespace) -> dict:
    ports = flipper_ports()
    record = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "mode": "read_only",
              "flipper": {"ports": ports},
              "pwn": {"state": "configured" if args.pwn_host and args.pwn_user
                      else "not_configured"}}
    if args.action != "discover":
        selected = args.flipper_port or (ports[0] if ports else None)
        record["flipper"] = flipper_status(selected)
        record["pwn"] = pwn_status(args.pwn_host, args.pwn_user, args.pwn_port)
    return record


def emit(record: dict, as_json: bool, save: bool) -> None:
    line = json.dumps(record, indent=2 if as_json else None, sort_keys=True)
    if as_json:
        print(line)
    else:
        print("AutoWIFI hardware status (" + record["timestamp"] + ")")
        for name in ("flipper", "pwn"):
            item = record[name]
            print(f"  {name}: {item.get('state', 'discovered')}")
            for key in ("ports", "port", "info", "host", "status", "hint", "error"):
                if item.get(key):
                    print(f"    {key}: {item[key]}")
    if save:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        os.chmod(LOG_PATH, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("discover", "status", "watch"),
                        nargs="?", default="status")
    parser.add_argument("--flipper-port", help="Explicit USB serial path (optional)")
    parser.add_argument("--pwn-host", default=os.environ.get("AUTOWIFI_PWN_HOST"),
                        help="Explicit host/IP of an SSH-capable device you own")
    parser.add_argument("--pwn-user", default=os.environ.get("AUTOWIFI_PWN_USER"),
                        help="SSH user; key-based authentication only")
    parser.add_argument("--pwn-port", type=int, default=22)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--record", action="store_true",
                        help="Append snapshots to a private local JSONL log")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="Seconds between watch snapshots (minimum 5)")
    parser.add_argument("--cycles", type=int, default=0,
                        help="Watch cycles; 0 means until interrupted")
    args = parser.parse_args(argv)

    if args.interval < 5 or args.cycles < 0:
        parser.error("--interval must be at least 5 and --cycles cannot be negative")
    if not 1 <= args.pwn_port <= 65535:
        parser.error("--pwn-port must be 1-65535")

    count = 0
    try:
        while True:
            emit(snapshot(args), args.json, args.record)
            count += 1
            if args.action != "watch" or (args.cycles and count >= args.cycles):
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

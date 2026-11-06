#!/usr/bin/env python3
"""Non-disruptive Wi-Fi inventory using NetworkManager (no monitor-mode changes).

This is a managed-mode access-point discovery scan, not a passive packet
capture. NetworkManager may transmit normal 802.11 probe requests.
No deauthentication frames, forced disconnections, or password testing.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

MAC_RE = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
INTERFACE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,32}")


def split_nmcli_line(line: str) -> list[str]:
    """Split nmcli's escaped terse output, retaining SSID colons/backslashes."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def parse_nmcli(output: str) -> list[dict]:
    """Use only grounded NM scan rows; deduplicate by BSSID."""
    found: dict[str, dict] = {}
    for line in output.splitlines():
        values = split_nmcli_line(line.strip())
        if len(values) < 6:
            continue
        ssid, bssid, channel, frequency, signal = values[:5]
        security = ":".join(values[5:]) or "OPEN"
        if not MAC_RE.fullmatch(bssid):
            continue
        try:
            strength = max(0, min(100, int(signal)))
            ch = int(channel)
        except ValueError:
            continue
        if not 1 <= ch <= 233:
            continue
        found[bssid.upper()] = {
            "ssid": ssid or "<hidden>",
            "bssid": bssid.upper(),
            "channel": ch,
            "frequency": frequency,
            "signal_percent": strength,
            "security": security,
        }
    return sorted(found.values(), key=lambda row: row["signal_percent"], reverse=True)


def scan(interface: str, timeout: int = 50) -> tuple[list[dict], str]:
    if not INTERFACE_RE.fullmatch(interface):
        raise ValueError("Invalid wireless interface")
    command = [
        "nmcli", "-t", "-e", "yes",
        "-f", "SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY",
        "device", "wifi", "list", "ifname", interface, "--rescan", "yes",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except FileNotFoundError:
        return [], ("NetworkManager command nmcli is missing. "
                    "Install or enable NetworkManager or use the advanced survey.")
    except subprocess.TimeoutExpired:
        return [], "NetworkManager scan timed out. Check the USB adapter and Wi-Fi driver."
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "nmcli scan failed").strip()
        return [], f"NetworkManager scan failed (exit {result.returncode}): {message[:500]}"
    return parse_nmcli(result.stdout), ""


def format_results(interface: str, networks: list[dict], error: str = "") -> str:
    lines = [
        f"Interface: {interface}",
        "Scan backend: NetworkManager (managed mode, no monitor-mode change)",
        f"Access points detected: {len(networks)}" if not error
        else "Access points detected: unknown (scan failed)",
    ]
    if error:
        lines.extend(["ERROR: " + error, "No trustworthy network count is available."])
    else:
        lines.extend([
            "",
            "ACCESS POINTS — strongest observed signal first",
            "POWER\tBSSID\tCHANNEL\tENCRYPTION\tSSID",
        ])
        for item in networks:
            ssid = item["ssid"].replace("\t", " ").replace("\n", " ")
            lines.append(
                f'{item["signal_percent"]}%\t{item["bssid"]}\t'
                f'{item["channel"]}\t{item["security"]}\t{ssid}'
            )
        if not networks:
            lines.append("No access points returned by NetworkManager.")
            lines.append("Check: adapter enabled/managed, rfkill, Wi-Fi band, driver and USB power.")
        else:
            lines.append("Signal values are percentages (not dBm); this scan is not exhaustive.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    args = parser.parse_args(argv)
    try:
        networks, error = scan(args.interface)
    except ValueError as exc:
        parser.error(str(exc))
    print(format_results(args.interface, networks, error), flush=True)
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())

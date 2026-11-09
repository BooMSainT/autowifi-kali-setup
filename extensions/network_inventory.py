#!/usr/bin/env python3
"""Non-disruptive Wi-Fi inventory using NetworkManager (no monitor-mode changes).

This is a managed-mode access-point discovery scan, not a passive packet
capture. NetworkManager may transmit normal 802.11 probe requests.
No deauthentication frames, forced disconnections, or password testing.
"""
from __future__ import annotations

import argparse
import re
import shutil
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
        values = split_nmcli_line(line.rstrip("\r\n"))
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


def _frequency_channel(mhz: int) -> int:
    """Interpret common 2.4, 5 and 6 GHz center frequencies."""
    if mhz == 2484:
        return 14
    if 2412 <= mhz <= 2472:
        return (mhz - 2407) // 5
    if 5000 <= mhz < 5925:
        return (mhz - 5000) // 5
    if 5955 <= mhz <= 7115:
        return (mhz - 5950) // 5
    return 0


def parse_iw_scan(output: str) -> list[dict]:
    """Decode real nl80211 BSS observations, without guessing network names."""
    results: dict[str, dict] = {}
    sections = re.split(r"(?m)^\s*BSS\s+", output)
    for body in sections[1:]:
        first, _, tail = body.partition("\n")
        mac_match = re.match(r"((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})", first)
        if not mac_match:
            continue
        bssid = mac_match.group(1).upper()
        freq_match = re.search(r"(?m)^\s*freq:\s*(\d+)", tail)
        power_match = re.search(r"(?m)^\s*signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", tail)
        ssid_match = re.search(r"(?m)^\s*SSID:\s*(.*)$", tail)
        channel_match = re.search(r"(?m)^\s*DS Parameter set:\s*channel\s+(\d+)", tail)
        if not freq_match:
            continue
        freq = int(freq_match.group(1))
        channel = int(channel_match.group(1)) if channel_match else _frequency_channel(freq)
        if not channel:
            continue
        signal_dbm = float(power_match.group(1)) if power_match else -100.0
        # Rough relative display only, not a calibrated dBm-to-percent mapping.
        strength = max(0, min(100, round((signal_dbm + 100.0) * 2)))
        security_block = " ".join(tail.splitlines())
        if re.search(r"(?m)^\s*RSN:", tail):
            if re.search(r"Authentication suites:\s*[^\n]*\bSAE\b", tail):
                if re.search(r"Authentication suites:\s*[^\n]*\bPSK\b", tail):
                    security = "WPA2/WPA3"
                else:
                    security = "WPA3"
            else:
                security = "WPA2/RSN"
        elif re.search(r"(?m)^\s*WPA:", tail):
            security = "WPA"
        elif re.search(r"(?m)^\s*capability:.*\bPrivacy\b", tail):
            security = "Legacy privacy (possibly WEP)"
        else:
            security = "OPEN/unknown"
        ssid = (ssid_match.group(1) if ssid_match else "") or "<hidden>"
        results[bssid] = {
            "ssid": ssid,
            "bssid": bssid,
            "channel": channel,
            "frequency": f"{freq} MHz",
            "signal_percent": strength,
            "security": security,
        }
    return sorted(results.values(), key=lambda ap: ap["signal_percent"], reverse=True)


def _iw_scan(interface: str, timeout: int = 45) -> tuple[list[dict], str]:
    """Second scan backend. Never changes radio mode or connectivity."""
    if not shutil.which("iw"):
        return [], "iw is missing (install the iw package)"
    command = ["iw", "dev", interface, "scan"]
    try:
        proc = subprocess.run(command, text=True, capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"iw could not run: {exc}"
    if proc.returncode:
        reason = (proc.stderr or proc.stdout or "").strip()
        permission = bool(re.search(
            r"operation not permitted|permission denied|not authorized", reason, re.I
        ))
        if permission and shutil.which("sudo"):
            # For a foreground scan, ask for sudo credentials ONLY after the
            # unprivileged scan has demonstrably failed due to permissions.
            # systemd background sessions have no TTY and must never prompt.
            if sys.stdin.isatty():
                print("Kernel Wi-Fi scan needs permissions. Sudo may ask for your password.",
                      flush=True)
                try:
                    auth = subprocess.run(["sudo", "-v"], check=False, timeout=60)
                except (OSError, subprocess.TimeoutExpired):
                    auth = None
                if auth is None or auth.returncode:
                    return [], "iw scan needs authorization; sudo authentication failed"
            command = ["sudo", "-n", *command]
            try:
                proc = subprocess.run(command, text=True, capture_output=True,
                                      timeout=timeout, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return [], f"Authorized iw scan failed to start: {exc}"
        if proc.returncode:
            details = (proc.stderr or proc.stdout or "no output").strip()
            return [], f"iw scan failed: {details[:350]}"
    return parse_iw_scan(proc.stdout), ""


def scan(interface: str, timeout: int = 50) -> tuple[list[dict], str]:
    """Combine normal NM observations and a bounded second kernel scan.

    Tries NetworkManager first; use iw when NM fails or sees <=1 AP.
    A failed scan is never claimed to be an empty Wi-Fi environment.
    """
    if not INTERFACE_RE.fullmatch(interface):
        raise ValueError("Invalid wireless interface")
    command = [
        "nmcli", "-t", "-e", "yes",
        "-f", "SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY",
        "device", "wifi", "list", "ifname", interface, "--rescan", "yes",
    ]
    networkmanager_error = ""
    first: list[dict] = []
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, check=False)
        if result.returncode:
            message = (result.stderr or result.stdout or "nmcli scan failed").strip()
            networkmanager_error = (
                f"NetworkManager scan failed (exit {result.returncode}): {message[:350]}"
            )
        else:
            first = parse_nmcli(result.stdout)
    except FileNotFoundError:
        networkmanager_error = "NetworkManager command nmcli is missing"
    except subprocess.TimeoutExpired:
        networkmanager_error = "NetworkManager scan timed out"

    # If NM already identified multiple access points, stop: no extra
    # permission requests or unnecessary channel surveys.
    if len(first) >= 2:
        return first, ""
    secondary, kernel_error = _iw_scan(interface, timeout=min(45, timeout))
    if secondary:
        all_aps = {item["bssid"]: item for item in first}
        for item in secondary:
            # Preserve NM's calibrated/driver-reported percentage where both
            # tools saw the same BSSID, and fill gaps with iw BSS observations.
            all_aps.setdefault(item["bssid"], item)
        return sorted(all_aps.values(),
                      key=lambda ap: ap["signal_percent"], reverse=True), ""
    if networkmanager_error:
        return [], f"{networkmanager_error}. Kernel scan: {kernel_error or 'no APs observed'}"
    if kernel_error:
        # First scan succeeded. Its 0/1 AP results are still observations,
        # but tell the operator when the second backend could not verify them.
        if first:
            print(f"Secondary Wi-Fi verification unavailable: {kernel_error}",
                  file=sys.stderr, flush=True)
            return first, ""
        return [], (f"NetworkManager returned no APs and the kernel scan "
                    f"could not verify reception: {kernel_error}")
    return first, ""


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

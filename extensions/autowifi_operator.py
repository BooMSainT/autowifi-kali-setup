"""Natural-language console for AutoWIFI.

Known operational requests are routed to deterministic commands. Open-ended
requests fall back to the local MCPHost operator.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path


BIN_DIR = Path.home() / ".local" / "bin"
INTERFACE_RE = re.compile(r"\b(?:wlan\d+(?:mon)?|wl[a-zA-Z0-9_.:-]+)\b", re.IGNORECASE)
BROAD_TARGET_RE = re.compile(
    r"\b(?:all|any|every)\s+(?:nearby\s+)?networks?\b|"
    r"\bnetworks?\s+(?:within|in)\s+range\b|"
    r"\beverything\s+(?:within|in)\s+range\b",
    re.IGNORECASE,
)
ACTIVE_ACTION_RE = re.compile(
    r"\b(?:handshake|capture|crack|deauth|pmkid|pixie|wps)\b",
    re.IGNORECASE,
)


HELP = """Natural-language AutoWIFI operator

Examples:
  scan nearby devices and sort them by power
  use wlan2 for monitor mode and keep wlan0 managed
  show devices connected to my LAN
  list my wireless interfaces
  capture a handshake for MyWiFi
  audit MyWiFi
  show my wordlists
  do it

Short codes still work: nearby, devices, networks, interfaces, check,
wordlists, audit TARGET, handshake TARGET, pmkid TARGET, wps TARGET,
deauth5 TARGET, verify FILE, crack FILE, reports, health.

Type chat for freeform AI mode, help for this list, or quit to exit.
"""


def _bin(name: str) -> str:
    return str(BIN_DIR / name)


def _action_label(command: list[str]) -> str:
    words = command[1:]
    action = words[0] if words else "request"
    labels = {
        "radios": "Configuring and verifying wireless adapter modes",
        "monitor": "Enabling monitor mode",
        "managed": "Restoring managed mode",
        "nearby": "Scanning nearby access points and associated clients",
        "networks": "Scanning nearby Wi-Fi access points",
        "devices": "Discovering devices on the connected LAN",
        "handshake": "Running the handshake capture workflow",
        "pmkid": "Running the PMKID capture workflow",
        "wps": "Running the WPS assessment",
        "audit": "Running the target assessment",
        "crack": "Testing the supplied capture and wordlist",
        "verify": "Verifying the supplied capture",
        "wordlists": "Finding installed wordlists",
        "interfaces": "Reading wireless and LAN interfaces",
        "check": "Checking AutoWIFI dependencies",
    }
    return labels.get(action, "Running the requested AutoWIFI action")


def run_with_progress(command: list[str], interval: float = 5.0) -> int:
    print(f"{_action_label(command)}...", flush=True)
    started = time.monotonic()
    next_update = interval
    process = subprocess.Popen(command, start_new_session=True)
    try:
        while True:
            try:
                status = process.wait(timeout=min(1.0, max(0.1, interval)))
                elapsed = int(time.monotonic() - started)
                if status == 0:
                    print(f"Action completed in {elapsed}s.", flush=True)
                else:
                    print(f"Action failed after {elapsed}s with exit code {status}.", flush=True)
                return status
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                if elapsed >= next_update:
                    print(f"Still working... {int(elapsed)}s elapsed. Press Ctrl+C to stop.", flush=True)
                    next_update += interval
    except KeyboardInterrupt:
        elapsed = int(time.monotonic() - started)
        print(f"Stopping the action after {elapsed}s...", flush=True)
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        print("Action cancelled.", flush=True)
        return 130


def _interfaces_near_modes(text: str) -> tuple[str, str]:
    monitor = ""
    managed = ""
    for match in INTERFACE_RE.finditer(text):
        name = match.group(0)
        neighborhood = text[max(0, match.start() - 35) : match.end() + 35].lower()
        after = text[match.end() : match.end() + 35].lower()
        before = text[max(0, match.start() - 35) : match.start()].lower()
        if "monitor" in after or ("monitor" in before and not monitor):
            monitor = name
        if "managed" in after or ("managed" in before and not managed):
            managed = name
    return monitor, managed


def _target_after_keyword(text: str, keyword: str) -> str:
    match = re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE)
    if not match:
        return ""
    target = text[match.end() :].strip(" :-")
    target = re.sub(r"^(?:for|on|against|from|target)\s+", "", target, flags=re.IGNORECASE)
    target = re.sub(r"^capture\s+(?:for|on|against|from)\s+", "", target, flags=re.IGNORECASE)
    target = re.sub(r"^(?:the\s+)?network\s+", "", target, flags=re.IGNORECASE)
    target = re.split(r"\s+(?:using|with)\s+wlan\w*\b", target, maxsplit=1, flags=re.IGNORECASE)[0]
    return target.strip()


def request_problem(text: str) -> str:
    if ACTIVE_ACTION_RE.search(text) and BROAD_TARGET_RE.search(text):
        return (
            "Active capture or password testing requires one exact authorized "
            "SSID or BSSID. Example: capture a handshake for MyHomeWiFi"
        )
    return ""


def interpret(text: str) -> list[str] | None:
    stripped = text.strip()
    lower = stripped.lower()
    if not stripped:
        return None

    try:
        words = shlex.split(stripped)
    except ValueError:
        words = stripped.split()
    code = words[0].lower() if words else ""
    rest = words[1:]

    direct_codes = {
        "check",
        "interfaces",
        "networks",
        "nearby",
        "devices",
        "clients",
        "audit",
        "handshake",
        "pmkid",
        "wps",
        "deauth5",
        "wordlists",
        "verify",
        "crack",
        "restore",
        "reports",
        "health",
    }
    if code in direct_codes:
        return [_bin("autowifi-quick"), code, *rest]

    monitor_interface, managed_interface = _interfaces_near_modes(stripped)
    if monitor_interface and managed_interface:
        return [
            _bin("autowifi-direct"),
            "radios",
            "--monitor",
            monitor_interface,
            "--managed",
            managed_interface,
        ]

    interfaces = INTERFACE_RE.findall(stripped)
    if "monitor" in lower and interfaces:
        return [_bin("autowifi-direct"), "monitor", interfaces[0]]
    if ("managed" in lower or "restore" in lower) and interfaces:
        return [_bin("autowifi-direct"), "managed", interfaces[0]]

    if any(word in lower for word in ("nearby", "signal", "power level", "wireless clients")):
        return [_bin("autowifi-quick"), "nearby"]
    if "scan" in lower and any(word in lower for word in ("wifi", "wi-fi", "access point", "network")):
        return [_bin("autowifi-quick"), "networks"]
    if any(phrase in lower for phrase in ("lan devices", "connected devices", "devices on my", "devices connected")):
        return [_bin("autowifi-quick"), "devices"]
    if "interface" in lower or "adapter" in lower:
        return [_bin("autowifi-quick"), "interfaces"]
    if "dependenc" in lower or "installation check" in lower:
        return [_bin("autowifi-quick"), "check"]
    if "wordlist" in lower:
        return [_bin("autowifi-quick"), "wordlists"]
    if "report" in lower and any(word in lower for word in ("show", "list", "saved")):
        return [_bin("autowifi-quick"), "reports"]

    targeted = (
        ("handshake", "handshake"),
        ("audit", "audit"),
        ("pmkid", "pmkid"),
        ("pixie", "wps"),
        ("wps", "wps"),
        ("deauth", "deauth5"),
    )
    for keyword, action in targeted:
        if keyword in lower:
            target = _target_after_keyword(stripped, keyword)
            if target:
                return [_bin("autowifi-quick"), action, target]

    return [_bin("autowifi-ai"), stripped]


def main() -> int:
    print("AutoWIFI natural-language operator")
    print("Tell me what you want done. Type help for examples or quit to exit.")
    last_command: list[str] | None = None

    while True:
        try:
            text = input("autowifi> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue

        lower = text.lower()
        if lower in {"quit", "exit"}:
            return 0
        if lower in {"help", "codes", "examples"}:
            print(HELP)
            continue
        if lower in {"chat", "ai"}:
            subprocess.run([_bin("autowifi-ai")], check=False)
            continue
        if lower in {"do it", "just do it", "run it", "go ahead", "proceed"}:
            if not last_command:
                print("There is no previous action to repeat. Tell me what you want done.")
                continue
            command = last_command
            print("Repeating the previous action.")
        else:
            problem = request_problem(text)
            if problem:
                print(problem)
                continue
            command = interpret(text)
            if not command:
                continue

        print(f"Running: {shlex.join(command[1:])}", flush=True)
        status = run_with_progress(command)
        if status == 0:
            last_command = command


if __name__ == "__main__":
    raise SystemExit(main())

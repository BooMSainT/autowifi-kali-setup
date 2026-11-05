"""Natural-language console for AutoWIFI.

Known operational requests are routed to deterministic commands. Open-ended
requests fall back to the local MCPHost operator.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path


BIN_DIR = Path.home() / ".local" / "bin"
TASK_DIR = Path.home() / ".local" / "share" / "autowifi" / "tasks"
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
  show my Flipper Zero and Pwn device status
  menu
  find devices on my home network
  use my Ralink antenna to scan nearby Wi-Fi
  which adapter is being used
  check my DSTIKE ESP8266 deauther status
  deauther listen for 30 seconds record
  deauther cached access points
  do it

Short codes still work: nearby, devices, networks, interfaces, check,
wordlists, audit TARGET, handshake TARGET, pmkid TARGET, wps TARGET,
deauth5 TARGET, verify FILE, crack FILE, hardware, deauther, reports, health.\nType menu to use numbered home-network and antenna checks.

The deauther USB bridge is read-only. For a new Wi-Fi scan, use your DSTIKE\'s\non-device scanning controls, then request deauther cached access points.\nType chat for freeform AI mode, help for this list, or quit to exit.
"""


def _bin(name: str) -> str:
    return str(BIN_DIR / name)


def _action_label(command: list[str]) -> str:
    words = command[1:]
    action = words[0] if words else "request"
    if Path(command[0]).name == "autowifi-menu":
        return "Opening AutoWIFI simple home network menu"
    labels = {
        "radios": "Configuring and verifying wireless adapter modes",
        "monitor": "Enabling monitor mode",
        "managed": "Restoring managed mode",
        "nearby": "Scanning nearby access points and associated clients",
        "networks": "Scanning nearby Wi-Fi access points",
        "devices": "Discovering devices on the connected LAN",
        "hardware": "Checking connected Flipper and configured Pwn device",
        "deauther": "Checking DSTIKE ESP8266 USB serial status",
        "wireless": "Observing nearby Wi-Fi with Ralink USB adapter",
        "adapters": "Checking Wi-Fi and antenna adapter selection",
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


def _task_timeout(command: list[str]) -> int:
    executable = Path(command[0]).name if command else ""
    action = command[1] if len(command) > 1 else ""
    if executable == "autowifi-ai":
        return 300
    if executable == "autowifi-menu":
        return 7200  # Interactive menu remains available during an extended session.
    if executable == "autowifi-quick" and action in {"deauther", "dstike"} and len(command) > 2 and command[2] == "listen":
        try:
            seconds_idx = command.index("--seconds")
            seconds = int(command[seconds_idx + 1])
        except (ValueError, IndexError):
            seconds = 30
        return max(60, min(140, seconds + 20))
    limits = {
        "check": 120,
        "interfaces": 120,
        "monitor": 120,
        "managed": 120,
        "radios": 120,
        "devices": 180,
        "hardware": 60,
        "deauther": 60,
        "networks": 180,
        "nearby": 180,
        "handshake": 240,
        "pmkid": 180,
        "wps": 420,
        "audit": 240,
        "verify": 120,
        "wordlists": 120,
        "crack": 3600,
    }
    return limits.get(action, 600)


def run_with_progress(command: list[str], interval: float = 5.0) -> int:
    print(f"{_action_label(command)}...", flush=True)
    started = time.monotonic()
    next_update = interval
    # A separate process group lets Ctrl+C stop the complete action while
    # retaining the controlling terminal that sudo needs for its password prompt.
    process = subprocess.Popen(command, preexec_fn=os.setpgrp)
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


def _terminal_candidates(runner: list[str]) -> list[tuple[str, list[str]]]:
    return [
        ("xfce4-terminal", ["--window", "--title=AutoWIFI Task", "--execute", *runner]),
        ("qterminal", ["--title", "AutoWIFI Task", "-e", *runner]),
        ("gnome-terminal", ["--title=AutoWIFI Task", "--", *runner]),
        ("konsole", ["-p", "tabtitle=AutoWIFI Task", "-e", *runner]),
        ("mate-terminal", ["--title=AutoWIFI Task", "--", *runner]),
        ("x-terminal-emulator", ["-T", "AutoWIFI Task", "-e", *runner]),
        ("xterm", ["-T", "AutoWIFI Task", "-e", *runner]),
    ]


def launch_task_window(command: list[str], label: str) -> tuple[bool, Path | None, str]:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False, None, "No graphical display is available"

    TASK_DIR.mkdir(parents=True, exist_ok=True)
    task_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    task_file = TASK_DIR / f"{task_id}.json"
    started_file = TASK_DIR / f"{task_id}.started"
    payload = {
        "id": task_id,
        "label": label,
        "command": command,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    task_file.write_text(json.dumps(payload, indent=2) + "\n")
    task_file.chmod(0o600)
    runner = [sys.executable, str(Path(__file__).resolve()), "--run-task", str(task_file)]

    for terminal_name, terminal_args in _terminal_candidates(runner):
        terminal = shutil.which(terminal_name)
        if not terminal:
            continue
        process = subprocess.Popen(
            [terminal, *terminal_args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if started_file.exists():
                return True, task_file.with_suffix(".log"), terminal_name
            if process.poll() not in (None, 0):
                break
            time.sleep(0.1)

    return False, None, "No supported terminal emulator opened the task"


def _stream_task(
    command: list[str],
    log_file: Path,
    interval: float = 5.0,
    timeout: int | None = None,
) -> int:
    started = time.monotonic()
    next_update = interval
    deadline = timeout or _task_timeout(command)
    timed_out = False
    timed_out_at = 0.0
    output: queue.Queue[str | None] = queue.Queue()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setpgrp,
    )

    def read_output() -> None:
        if process.stdout:
            for line in process.stdout:
                output.put(line)
        output.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    reader_finished = False

    try:
        with log_file.open("a", encoding="utf-8") as log:
            while True:
                try:
                    item = output.get(timeout=0.2)
                    if item is None:
                        reader_finished = True
                    else:
                        print(item, end="", flush=True)
                        log.write(item)
                        log.flush()
                except queue.Empty:
                    pass

                elapsed = time.monotonic() - started
                if elapsed >= deadline and process.poll() is None and not timed_out:
                    message = f"[AutoWIFI] Task deadline reached after {int(elapsed)}s; stopping it.\n"
                    print(message, end="", flush=True)
                    log.write(message)
                    log.flush()
                    os.killpg(process.pid, signal.SIGTERM)
                    timed_out = True
                    timed_out_at = elapsed
                if timed_out and process.poll() is None and elapsed >= timed_out_at + 5:
                    os.killpg(process.pid, signal.SIGKILL)
                if elapsed >= next_update and process.poll() is None:
                    message = f"[AutoWIFI] Still working... {int(elapsed)}s elapsed.\n"
                    print(message, end="", flush=True)
                    log.write(message)
                    log.flush()
                    next_update += interval

                if process.poll() is not None and reader_finished and output.empty():
                    break

            status = process.wait()
            return 124 if timed_out else status
    except KeyboardInterrupt:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        return 130


def _read_only_no_sudo(command: list[str]) -> bool:
    """Only known local, read-only device status commands skip sudo checks."""
    if not command:
        return False
    executable = Path(command[0]).name
    if executable in {"autowifi-deauther", "autowifi-hardware", "autowifi-menu"}:
        return True
    return (executable == "autowifi-quick" and len(command) > 1 and
            command[1] in {"deauther", "dstike", "hardware", "flipper", "pwn"})


def run_task_file(task_file: Path) -> int:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    resolved_task = task_file.expanduser().resolve()
    expected_dir = TASK_DIR.resolve()
    if os.path.commonpath((str(resolved_task), str(expected_dir))) != str(expected_dir):
        print("Invalid AutoWIFI task file.", file=sys.stderr)
        return 2

    payload = json.loads(resolved_task.read_text())
    command = payload.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(value, str) for value in command):
        print("Invalid AutoWIFI task command.", file=sys.stderr)
        return 2

    label = str(payload.get("label") or "AutoWIFI task")
    started_file = resolved_task.with_suffix(".started")
    log_file = resolved_task.with_suffix(".log")
    started_file.write_text(datetime.now().isoformat(timespec="seconds") + "\n")

    print("=" * 72)
    print("AUTOWIFI TASK")
    print(label)
    print(f"Command: {shlex.join(command[1:])}")
    print(f"Log: {log_file}")
    print(f"Maximum run time: {_task_timeout(command)} seconds")
    print("=" * 72)
    if not _read_only_no_sudo(command):
        print("Enter your Kali password here if sudo asks for it.")
    print()

    with log_file.open("w", encoding="utf-8") as log:
        log.write(f"AutoWIFI task: {label}\n")
        log.write(f"Command: {shlex.join(command)}\n")
        log.write(f"Started: {datetime.now().isoformat(timespec='seconds')}\n\n")

    if _read_only_no_sudo(command):
        print("Read-only hardware task: no sudo required.\n")
        status = _stream_task(command, log_file, timeout=_task_timeout(command))
    elif shutil.which("sudo"):
        sudo_status = subprocess.run(["sudo", "-v"], check=False).returncode
        if sudo_status != 0:
            print("Sudo authorization failed.")
            status = sudo_status
        else:
            print("Sudo access confirmed. Starting the task...\n")
            status = _stream_task(command, log_file, timeout=_task_timeout(command))
    else:
        print("sudo is missing.")
        status = 1

    elapsed_note = "completed successfully" if status == 0 else f"finished with exit code {status}"
    print()
    print("=" * 72)
    print(f"Task {elapsed_note}.")
    print(f"Saved log: {log_file}")
    print("=" * 72)
    if sys.stdin.isatty():
        try:
            input("Press Enter to close this task window...")
        except (EOFError, KeyboardInterrupt):
            pass
    return status


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


def _deauther_command(text: str) -> list[str]:
    """Normalize deauther natural language to the read-only CLI argument set.

    The bridge cannot initiate a radio scan. Unsupported requests show
    argparse help rather than passing unrecognized words or doing an action
    that differs from the user's request.
    """
    cmd = [_bin("autowifi-quick"), "deauther"]
    lower = text.lower()
    # A user asking to start a scan should not get silently redirected
    # to the 'listen' command: that reads serial data, not RF traffic.
    if re.search(r"\bscan(?:ning)?\b", lower) and not re.search(r"\bcached\b", lower):
        return [*cmd, "--help"]

    if re.search(r"\b(?:listen|watch|record|serial\s+log|serial\s+output)\b", lower):
        cmd.append("listen")
        duration = re.search(
            r"(?:--seconds(?:=|\s+)|\bfor\s+)?(\d+)\s*"
            r"(seconds?|secs?|minutes?|mins?)\b",
            lower,
        )
        bare_option = re.search(r"--seconds(?:=|\s+)(\d+)\b", lower)
        if duration or bare_option:
            match = duration or bare_option
            value = int(match.group(1))
            unit = duration.group(2) if duration else "seconds"
            seconds = value * 60 if unit.startswith(("min",)) else value
            if not 1 <= seconds <= 120:
                return [*cmd[:2], "--help"]
            cmd.extend(["--seconds", str(seconds)])
        if re.search(r"\b(?:record|save|log)\b|--record\b", lower):
            cmd.append("--record")
        return cmd

    if re.search(r"\b(?:cached|results|access points|aps)\b", lower):
        return [*cmd, "cached-aps"]
    if re.search(r"\b(?:detect|usb|serial ports?|discover|find|connect)\b", lower):
        return [*cmd, "detect"]
    if re.search(r"\b(?:status|info|check|device)\b", lower):
        return [*cmd, "status"]
    if re.fullmatch(r"\s*(?:deauther|dstike)\s*", lower):
        return [*cmd, "status"]
    return [*cmd, "--help"]


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
        "hardware",
        "deauther",
        "dstike",
        "flipper",
        "pwn",
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
    if code in {"menu", "wifi"} or lower in {"show menu", "open menu", "main menu"}:
        return [_bin("autowifi-menu")]

    if (("antenna" in lower or "ralink" in lower or "rt3070" in lower) and
            any(word in lower for word in ("scan", "nearby", "survey", "networks"))):
        return [_bin("autowifi-menu"), "wireless"]

    if (("antenna" in lower or "adapter" in lower or "ralink" in lower) and
            any(word in lower for word in ("which", "status", "using", "show", "list", "check"))):
        return [_bin("autowifi-menu"), "adapters"]

    if any(phrase in lower for phrase in (
            "find devices", "scan my network", "audit my network",
            "who is connected", "what is connected", "what's connected",
            "connected to my wifi", "connected to my wi-fi",
            "connected to my network", "on my home network")):
        return [_bin("autowifi-menu"), "devices"]

    if "deauther" in lower or "dstike" in lower:
        return _deauther_command(stripped)

    if code in direct_codes:
        return [_bin("autowifi-quick"), code, *rest]

    if any(name in lower for name in ("flipper", "pwn father", "pwnagotchi", "p4wnp1")):
        return [_bin("autowifi-quick"), "hardware"]
    if "hardware" in lower and any(term in lower for term in ("status", "discover", "connected", "check")):
        return [_bin("autowifi-quick"), "hardware"]

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
                if action == "handshake":
                    command = [_bin("autowifi-direct"), "handshake", target]
                    interfaces = INTERFACE_RE.findall(stripped)
                    if interfaces:
                        command.extend(["--interface", interfaces[0]])
                    return command
                return [_bin("autowifi-quick"), action, target]

    return [_bin("autowifi-ai"), stripped]


def main() -> int:
    print("AutoWIFI natural-language operator")
    print("Tell me what you want done. Each action opens in a separate terminal window.")
    print("Type help for examples or quit to exit.")
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
            command = [_bin("autowifi-ai")]
            launched, log_file, terminal = launch_task_window(command, "Interactive AutoWIFI AI")
            if launched:
                print(f"Opened AI chat in {terminal}. Task log: {log_file}")
            else:
                print(f"Could not open a task window: {terminal}. Running here instead.")
                subprocess.run(command, check=False)
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

        label = _action_label(command)
        print(f"Launching: {shlex.join(command[1:])}", flush=True)
        launched, log_file, terminal = launch_task_window(command, label)
        if launched:
            print(f"Opened task in {terminal}. Live output is in the new window.")
            print(f"Task log: {log_file}")
            last_command = command
        else:
            print(f"Could not open a task window: {terminal}. Running here instead.")
            status = run_with_progress(command)
            if status == 0:
                last_command = command


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run-task":
        raise SystemExit(run_task_file(Path(sys.argv[2])))
    raise SystemExit(main())

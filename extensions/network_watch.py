#!/usr/bin/env python3
"""Continuous read-only home-network monitoring for AutoWIFI.

Observes the configured, directly connected private LAN and the selected
managed-mode Wi-Fi radio. Alerts only after changes are confirmed in two
consecutive successful observations. Never deauthenticates, captures
credentials, changes radio modes, or scans networks beyond the local LAN.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

STATE_DIR = Path.home() / ".local/state/autowifi/monitor"
STATE_FILE = STATE_DIR / "state.json"
EVENT_FILE = STATE_DIR / "events.jsonl"
LOCK_FILE = STATE_DIR / "monitor.lock"
SERVICE_NAME = "autowifi-monitor.service"
DEFAULT_INTERVAL = 300
MIN_INTERVAL = 300
MAX_INTERVAL = 3600
MAX_EVENTS_SHOWN = 15
MAC_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_state_dir(path: Path = STATE_DIR) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _atomic_json(path: Path, payload: dict) -> None:
    _ensure_state_dir(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".monitor-", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(payload, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_state(path: Path = STATE_FILE) -> dict:
    if not path.exists():
        return {"version": VERSION, "baseline_lan": None,
                "baseline_wifi": None, "pending_lan": {},
                "pending_wifi": {}, "last_cycle": None}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Monitor state is unreadable; not overwriting it: {exc}") from exc
    if not isinstance(result, dict) or result.get("version") != VERSION:
        raise ValueError("Unsupported monitor state format; not overwriting it")
    for name in ("pending_lan", "pending_wifi"):
        if not isinstance(result.get(name), dict):
            raise ValueError(f"Monitor state has invalid {name}; not overwriting it")
    for name in ("baseline_lan", "baseline_wifi"):
        if result.get(name) is not None and not isinstance(result[name], dict):
            raise ValueError(f"Monitor state has invalid {name}; not overwriting it")
    return result


def _append_event(event: dict, path: Path = EVENT_FILE) -> None:
    _ensure_state_dir(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # If fdopen fails before owning fd, close below only if still open.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _clean(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))[:160]


def _identity(device: dict) -> str:
    """MAC wins when known; otherwise only the local IP can identify a host."""
    mac = str(device.get("mac", "")).upper()
    return f"mac:{mac}" if MAC_RE.fullmatch(mac) else f"ip:{device.get('ip', '')}"


def _lan_map(devices: list[dict]) -> dict:
    result: dict = {}
    for device in devices:
        ip = str(device.get("ip", ""))
        if not ip:
            continue
        key = _identity(device)
        result[key] = {
            "ip": ip, "mac": str(device.get("mac", "")),
            "hostname": str(device.get("hostname", "")),
            "vendor": str(device.get("vendor", "")),
        }
    return result


def _wifi_map(access_points: list[dict], owned: list[str]) -> dict:
    """Track ONLY explicitly named home SSIDs, not arbitrary neighbors."""
    selected = set(owned)
    combined: dict = {}
    for ap in access_points:
        ssid = ap.get("ssid", "")
        if ssid not in selected:
            continue
        current = combined.setdefault(ssid, {"security": set(), "channels": set()})
        current["security"].add(str(ap.get("encryption", ap.get("security", "unknown"))))
        current["channels"].add(str(ap.get("channel", "?")))
    return {
        ssid: {
            "security": sorted(observed["security"]),
            "channels": sorted(observed["channels"]),
        }
        for ssid, observed in combined.items()
    }


def _candidate_changes(baseline: dict, observed: dict, category: str) -> dict:
    changes: dict = {}
    for key in sorted(set(observed) - set(baseline)):
        kind = "device_appeared" if category == "lan" else "home_wifi_appeared"
        changes[f"{kind}:{key}"] = {
            "kind": kind, "identity": key,
            "old": None, "new": observed[key],
        }
    for key in sorted(set(baseline) - set(observed)):
        kind = "device_missing" if category == "lan" else "home_wifi_missing"
        changes[f"{kind}:{key}"] = {
            "kind": kind, "identity": key,
            "old": baseline[key], "new": None,
        }
    for key in sorted(set(baseline) & set(observed)):
        old, new = baseline[key], observed[key]
        if category == "lan":
            if old.get("ip") != new.get("ip"):
                kind = "device_ip_changed"
                changes[f"{kind}:{key}"] = {
                    "kind": kind, "identity": key, "old": old, "new": new,
                }
        else:
            for field in ("security", "channels"):
                if old.get(field) != new.get(field):
                    kind = f"home_wifi_{field}_changed"
                    changes[f"{kind}:{key}"] = {
                        "kind": kind, "identity": key,
                        "old": old, "new": new, "field": field,
                    }
    return changes


def _confirm_changes(baseline: dict | None, pending: dict,
                     observed: dict, category: str) -> tuple[dict, dict, list[dict]]:
    """Require two consecutive healthy observations before logging a change.

    Failed scans never call this, so they cannot cause invented disconnects.
    """
    if baseline is None:
        return observed.copy(), {}, []
    baseline = baseline.copy()
    candidates = _candidate_changes(baseline, observed, category)
    next_pending = {}
    confirmed: list[dict] = []
    for token, detail in candidates.items():
        count = min(2, int(pending.get(token, 0)) + 1)
        if count == 2:
            confirmed.append(detail)
            key = detail["identity"]
            if detail["new"] is None:
                baseline.pop(key, None)
            elif key not in baseline:
                baseline[key] = detail["new"]
            elif detail["kind"] == "device_ip_changed":
                baseline[key] = detail["new"]
            elif detail["kind"].startswith("home_wifi_"):
                baseline[key] = {
                    **baseline[key],
                    detail["field"]: detail["new"][detail["field"]],
                }
        else:
            next_pending[token] = count
    # Keep descriptive metadata fresh without misrepresenting identity changes.
    if category == "lan":
        for key in set(baseline) & set(observed):
            if baseline[key].get("ip") == observed[key].get("ip"):
                baseline[key] = observed[key]
    return baseline, next_pending, confirmed


def _owned_ssids() -> tuple[list[str], str]:
    try:
        import bomb_simulation as config
        return config.load_networks(), ""
    except (ValueError, OSError) as exc:
        return [], f"Cannot read saved home SSIDs: {exc}"


def monitor_cycle(state: dict) -> tuple[dict, list[dict]]:
    """One bounded observation pass, independent of how often it is scheduled."""
    import autopilot
    state = dict(state)
    events: list[dict] = []
    cycle = int(state.get("cycle", 0)) + 1
    state["cycle"] = cycle
    state["last_cycle"] = _now()
    state["last_error"] = {}
    print(f"\n=== MONITOR PASS #{cycle} — {state['last_cycle']} ===", flush=True)

    # LAN must be the authoritative source for devices on your router.
    try:
        lan_result = autopilot.lan_task(save_report=False)
    except (ValueError, RuntimeError, OSError) as exc:
        lan_result = {"state": "failed", "error": str(exc)}
    if lan_result.get("state") == "ok" and isinstance(lan_result.get("devices"), list):
        seen = _lan_map(lan_result["devices"])
        scope = str(lan_result.get("subnet") or "")
        if state.get("scope_lan") and scope and scope != state["scope_lan"]:
            # A new local subnet is not evidence that every device on the
            # previous network disappeared. Establish a new clean baseline.
            print(f"  LAN subnet changed from {state['scope_lan']} to {scope}. "
                  "Building a new baseline, not declaring devices missing.")
            state["baseline_lan"], state["pending_lan"] = None, {}
        if scope:
            state["scope_lan"] = scope
        if not seen and state.get("baseline_lan"):
            state["last_error"]["lan"] = (
                "No responsive LAN hosts; treating as inconclusive to avoid "
                "false mass-disconnection alerts"
            )
            print("  LAN discovery returned zero hosts. Baseline kept; no mass alert.")
        else:
            base, pending, changes = _confirm_changes(
                state.get("baseline_lan"), state.get("pending_lan", {}),
                seen, "lan",
            )
            state["baseline_lan"], state["pending_lan"] = base, pending
            events += changes
    else:
        state["last_error"]["lan"] = lan_result.get("error") or "No reliable LAN observation"
        print("  LAN status inconclusive; previous devices were NOT marked missing.")

    try:
        wifi_result = autopilot.wifi_task(save_report=False)
    except (ValueError, RuntimeError, OSError) as exc:
        wifi_result = {"state": "failed", "error": str(exc)}
    names, config_error = _owned_ssids()
    if config_error:
        state["last_error"]["saved_ssids"] = config_error
    if state.get("baseline_wifi") is not None and not config_error:
        # Removing a saved home SSID is a configuration change, not an outage.
        state["baseline_wifi"] = {
            key: value for key, value in state["baseline_wifi"].items()
            if key in names
        }
    if (wifi_result.get("state") == "ok" and
            isinstance(wifi_result.get("observations"), list) and
            not config_error and names):
        seen = _wifi_map(wifi_result["observations"], names)
        base, pending, changes = _confirm_changes(
            state.get("baseline_wifi"), state.get("pending_wifi", {}),
            seen, "wifi",
        )
        state["baseline_wifi"], state["pending_wifi"] = base, pending
        events += changes
    elif wifi_result.get("state") != "ok":
        state["last_error"]["wifi"] = wifi_result.get("error") or "No reliable Wi-Fi observation"
        print("  Wi-Fi scan inconclusive; no access-point disappearance inferred.")
    if not names:
        print("  Saved home SSIDs: none. Only LAN device changes will be alerted.")
        print("  Use 'wifi networks' to add names for optional home Wi-Fi monitoring.")

    # Opening ESP8266 serial can reset it. Do not repeatedly poll every pass.
    if cycle == 1 or cycle % 6 == 0:
        try:
            dstike = autopilot.dstike_task()
            state["last_dstike"] = {
                "time": _now(), "state": dstike.get("state"),
                "port": dstike.get("port"),
                "error": dstike.get("error", ""),
            }
        except (ValueError, RuntimeError, OSError) as exc:
            state["last_dstike"] = {"time": _now(), "state": "failed", "error": str(exc)}
    for event in events:
        event["timestamp"] = state["last_cycle"]
    state["last_event_count"] = len(events)
    return state, events


def _format_event(event: dict) -> str:
    kind, key = event["kind"], event["identity"]
    item = event["new"] or event["old"] or {}
    label = _clean(item.get("hostname") or item.get("ip") or key)
    return f"{kind.replace('_', ' ').upper()}: {_clean(key)} ({label})"


def _notify(event: dict) -> None:
    """Desktop notifications are best effort; events always remain in local log."""
    message = _format_event(event)
    print(f"  CHANGE: {message}", flush=True)
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", "AutoWIFI", "Home network change", message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def watch(interval: int = DEFAULT_INTERVAL, *,
          max_cycles: int | None = None, sleeper=time.sleep) -> int:
    """Loop until Ctrl+C/systemd stop. Refuse concurrent local monitors."""
    if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        raise ValueError(f"Interval must be {MIN_INTERVAL}-{MAX_INTERVAL} seconds")
    _ensure_state_dir(STATE_DIR)
    lockfd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("AutoWIFI monitor is already running. Use 'wifi auto status'.")
            return 1
        state = _read_state(STATE_FILE)
        print(f"AutoWIFI CONTINUOUS MONITOR | every {interval} seconds")
        print("Home LAN changes, saved home SSIDs, and periodic DSTIKE checks.")
        print("Alerts require 2 consecutive successful observations.")
        print("No deauthentication, injection, monitor-mode changes or Internet scans.")
        print("Ctrl+C stops the foreground monitor. For background use: wifi auto start")
        completed = 0
        try:
            while True:
                try:
                    state, events = monitor_cycle(state)
                    # Durable state before notifications; log only confirmed changes.
                    for event in events:
                        _append_event(event, EVENT_FILE)
                    _atomic_json(STATE_FILE, state)
                    for event in events:
                        _notify(event)
                    print(f"  Confirmed changes: {len(events)}. Next pass in "
                          f"{interval} seconds.", flush=True)
                except (OSError, ValueError, TypeError) as exc:
                    print(f"  MONITOR ERROR: {exc}. Previous baseline preserved.",
                          flush=True)
                completed += 1
                if max_cycles is not None and completed >= max_cycles:
                    return 0
                sleeper(interval)
        except KeyboardInterrupt:
            print("\nMonitor stopped. Baselines and event history retained.")
            return 0
    finally:
        os.close(lockfd)


def _systemctl(*arguments: str) -> subprocess.CompletedProcess | None:
    if not shutil.which("systemctl"):
        return None
    try:
        return subprocess.run(
            ["systemctl", "--user", *arguments], capture_output=True,
            text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def service_control(action: str) -> int:
    if action not in ("start", "stop", "status"):
        raise ValueError("Unknown service action")
    unit = Path.home() / ".config/systemd/user" / SERVICE_NAME
    if action == "start":
        if not unit.is_file():
            print("Service file missing. Run bash setup-menu.sh once, then retry.")
            return 1
        result = _systemctl("daemon-reload")
        if result is None or result.returncode:
            print("Your user systemd service isn't available. Use 'wifi auto watch'")
            return 1
        result = _systemctl("enable", "--now", SERVICE_NAME)
    elif action == "stop":
        result = _systemctl("disable", "--now", SERVICE_NAME)
    else:
        result = _systemctl("is-active", SERVICE_NAME)
    if result is None:
        print("systemctl --user is unavailable. Run 'wifi auto watch' in a terminal.")
        return 1 if action != "status" else 0
    status = result.stdout.strip() or result.stderr.strip()
    if action == "status":
        print(f"AutoWIFI background monitor: {status or 'not installed'}")
        try:
            state = _read_state(STATE_FILE)
            print(f"Last completed pass: {state.get('last_cycle') or 'none'}")
            print(f"Last confirmed changes: {state.get('last_event_count', 0)}")
            print(f"Most recent errors: {state.get('last_error') or 'none'}")
        except ValueError as exc:
            print(f"State error: {exc}")
        return 0
    print(f"AutoWIFI monitor {action}: {status or 'command completed'}")
    if result.returncode:
        print(f"  Troubleshooting: {result.stderr.strip()[:400]}")
    elif action == "start":
        print("Started as a user service. It runs while your user session/service")
        print("manager is active; logging out may stop it unless linger is enabled.")
        print("View changes: wifi auto events | Stop: wifi auto stop")
    return result.returncode


def show_events(path: Path = EVENT_FILE, limit: int = MAX_EVENTS_SHOWN) -> int:
    if not path.exists():
        print("No confirmed network changes have been recorded.")
        return 0
    try:
        with path.open(encoding="utf-8") as stream:
            from collections import deque
            lines = list(deque(stream, maxlen=limit))
    except OSError as exc:
        print(f"Cannot read monitor events: {exc}")
        return 1
    print(f"\n=== LAST {len(lines)} VERIFIED NETWORK CHANGES ===")
    for line in lines:
        try:
            event = json.loads(line)
            print(f"{event['timestamp']}: {_format_event(event)}")
        except (ValueError, KeyError, TypeError):
            continue
    return 0

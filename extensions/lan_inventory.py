#!/usr/bin/env python3
"""Friendly, bounded, evidence-based device inventory for a connected private LAN.

Uses local interface metadata, Nmap host discovery, the Linux neighbor table,
and optional selected-host TCP service checks. No credential guessing,
deauthentication, remote command execution, or broad Internet scanning.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

REPORT_DIR = Path.home() / ".local/share/autowifi/menu-reports"
OUI_DATABASE = Path("/usr/share/nmap/nmap-mac-prefixes")
MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


def _clean(value: str) -> str:
    """Do not allow device-controlled names to print terminal control codes."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value or ""))
# Only a small, visible set of ports, and only after user requests it.
COMMON_PORTS = "22,53,80,139,443,445,554,631,1883,3389,8000,8080,8443,9100"


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing command: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{args[0]} timed out ({timeout} seconds)") from exc


def _interfaces() -> list[dict]:
    result = _run(["ip", "-j", "-4", "addr", "show"], timeout=10)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not list IPv4 interfaces")
    try:
        entries = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Could not parse Linux interface information") from exc
    interfaces = []
    for item in entries:
        name = item.get("ifname", "")
        if name == "lo" or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", name):
            continue
        for address in item.get("addr_info", []):
            if address.get("scope") != "global" or address.get("family") != "inet":
                continue
            try:
                net = ipaddress.ip_interface(
                    f"{address['local']}/{address['prefixlen']}")
            except (KeyError, ValueError, TypeError):
                continue
            if (net.network.is_private and not net.ip.is_loopback
                    and not net.ip.is_link_local and net.network.num_addresses <= 4096):
                interfaces.append({
                    "name": name, "ip": str(net.ip),
                    "network": net.network,
                    "state": item.get("operstate", "UNKNOWN"),
                })
    return interfaces


def _scope(requested: str = "") -> dict:
    candidates = _interfaces()
    if requested:
        candidates = [item for item in candidates if item["name"] == requested]
        if not candidates:
            raise ValueError(f"{requested!r} has no eligible connected private IPv4 network")
    if not candidates:
        raise RuntimeError("No connected private IPv4 network found; connect to your home router")
    route = _run(["ip", "-j", "-4", "route", "show", "default"], timeout=10)
    preferred = ""
    if route.returncode == 0:
        try:
            for item in json.loads(route.stdout or "[]"):
                if item.get("dev"):
                    preferred = item["dev"]
                    break
        except ValueError:
            pass
    return next((item for item in candidates if item["name"] == preferred), candidates[0])


def _parse_hosts(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RuntimeError("Nmap did not return valid device data (XML)") from exc
    result = []
    for host in root.findall("host"):
        state = host.find("status")
        if state is None or state.get("state") != "up":
            continue
        ipv4, mac, vendor = "", "", ""
        for address in host.findall("address"):
            if address.get("addrtype") == "ipv4":
                ipv4 = address.get("addr", "")
            if address.get("addrtype") == "mac":
                raw_mac = address.get("addr", "")
                mac = raw_mac if MAC_RE.fullmatch(raw_mac) else ""
                vendor = _clean(address.get("vendor", "")) if mac else ""
        try:
            ipaddress.IPv4Address(ipv4)
        except ValueError:
            continue
        hostname = ""
        for entry in host.findall("hostnames/hostname"):
            if entry.get("name"):
                hostname = _clean(entry.get("name", ""))
                break
        result.append({
            "ip": ipv4, "mac": mac.upper(), "vendor": vendor,
            "hostname": hostname, "status": "online (Nmap discovery)",
            "mac_source": "Nmap ARP" if mac else "",
            "hostname_source": "reverse DNS via Nmap" if hostname else "",
        })
    return sorted(result, key=lambda d: ipaddress.IPv4Address(d["ip"]))


def _neighbors(interface: str) -> dict[str, str]:
    result = _run(["ip", "-j", "neigh", "show", "dev", interface], timeout=10)
    if result.returncode:
        return {}
    try:
        entries = json.loads(result.stdout or "[]")
    except ValueError:
        return {}
    mapping = {}
    for item in entries:
        ip, mac = item.get("dst", ""), item.get("lladdr", "")
        original_state = item.get("state", "")
        state = " ".join(original_state) if isinstance(original_state, list) else str(original_state)
        if not MAC_RE.fullmatch(mac) or state.upper() in ("FAILED", "INCOMPLETE"):
            continue
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        mapping[ip] = mac.upper()
    return mapping


def _oui_vendors(mac_addresses: list[str], path: Path = OUI_DATABASE) -> dict[str, str]:
    """Read only Nmap's local OUI prefix database; no Internet lookups."""
    wanted = {m.replace(":", "").upper()[:6] for m in mac_addresses if MAC_RE.fullmatch(m)}
    if not wanted or not path.is_file():
        return {}
    result = {}
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                prefix, _, vendor = line.strip().partition(" ")
                if prefix.upper() in wanted and vendor.strip():
                    result[prefix.upper()] = vendor.strip()
                if len(result) == len(wanted):
                    break
    except OSError:
        return {}
    return result


def _type_clue(hostname: str) -> tuple[str, str]:
    """Evidence-based clue only. A device type is not proof of identity."""
    lower = hostname.lower()
    patterns = (
        (("printer", "laserjet", "officejet", "brother-", "epson-"), "possible printer"),
        (("iphone", "galaxy", "android", "pixel-", "ipad"), "possible phone/tablet"),
        (("macbook", "laptop", "thinkpad", "desktop", "windows-pc"), "possible computer"),
        (("chromecast", "roku", "smarttv", "firetv", "apple-tv"), "possible media device"),
        (("camera", "webcam", "ipcam", "doorbell"), "possible camera"),
        (("router", "gateway", "openwrt", "fritzbox"), "possible router"),
        (("echo-dot", "homepod", "nest-audio"), "possible smart speaker"),
        (("xbox", "playstation"), "possible game console"),
    )
    for markers, device_type in patterns:
        if any(marker in lower for marker in markers):
            return device_type, "hostname contains a matching device term"
    return "unknown", "insufficient evidence"


def discover(interface: str = "", timeout: int = 120, elevated: bool = False) -> dict:
    if not shutil.which("nmap"):
        raise RuntimeError("Nmap is not installed. On Kali: sudo apt install nmap")
    scope = _scope(interface)
    timeout = min(300, max(15, int(timeout)))
    network = scope["network"]
    cmd = ["nmap", "-sn", "-e", scope["name"], "-oX", "-", str(network)]
    if elevated:
        if not shutil.which("sudo"):
            raise RuntimeError("sudo is not installed")
        print("Kali may request your password for improved ARP/MAC discovery.",
              flush=True)
        auth = subprocess.run(["sudo", "-v"], check=False)
        if auth.returncode:
            raise RuntimeError("sudo authentication failed; no enhanced scan performed")
        cmd = ["sudo", "-n", *cmd]
    print(f"Scanning your connected {network} via {scope['name']}...", flush=True)
    scan = _run(cmd, timeout=timeout)
    if scan.returncode:
        message = scan.stderr.strip() or scan.stdout.strip()
        raise RuntimeError(f"Nmap discovery failed ({scan.returncode}): {message[:400]}")
    hosts = _parse_hosts(scan.stdout)
    neighbor_map = _neighbors(scope["name"])
    for host in hosts:
        if not host["mac"] and neighbor_map.get(host["ip"]):
            host["mac"] = neighbor_map[host["ip"]]
            host["mac_source"] = "Linux neighbor cache (may be stale)"
    prefixes = _oui_vendors([item["mac"] for item in hosts])
    for host in hosts:
        if not host["vendor"] and host["mac"]:
            first_octet = int(host["mac"][:2], 16)
            if first_octet & 2:
                host["vendor"] = "locally administered/randomized MAC"
            else:
                host["vendor"] = prefixes.get(host["mac"].replace(":", "")[:6], "")
        host["type"], host["type_evidence"] = _type_clue(host["hostname"])
    return {
        "version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "interface": scope["name"], "local_ip": scope["ip"],
        "subnet": str(network), "privileged": elevated,
        "device_count": len(hosts), "devices": hosts,
    }


def _short(text: str, width: int) -> str:
    value = _clean(str(text or "?"))
    return value if len(value) <= width else value[:max(1, width - 1)] + "…"


def print_inventory(report: dict, columns: int | None = None) -> None:
    print(f"\n=== DEVICES OBSERVED ON YOUR LAN ===", flush=True)
    print(f"Interface: {report['interface']}   Your IP: {report['local_ip']}")
    print(f"Subnet: {report['subnet']}")
    print(f"Devices: {report['device_count']}")
    print("Only devices that responded to discovery are included.")
    print("Type and manufacturer are clues, not verified identities.\n")
    records = report["devices"]
    width = columns if columns is not None else shutil.get_terminal_size((90, 25)).columns
    if width >= 79:
        widths = (3, 15, 21, 15, 17)
        header = ("#", "IP", "HOSTNAME", "DEVICE CLUE", "MAC")
        def line(values):
            return "  ".join(_short(item, size).ljust(size)
                             for item, size in zip(values, widths))
        print(line(header))
        print("-" * (sum(widths) + 8))
        for n, item in enumerate(records, 1):
            print(line((str(n), item["ip"], item["hostname"] or "?",
                        item["type"], item["mac"] or "?")))
    else:
        for n, item in enumerate(records, 1):
            print(f"{n:>2}. {item['ip']} | {item['hostname'] or 'unknown'}")
            print(f"    {item['type']} | MAC: {item['mac'] or 'unknown'}")
    if not records:
        print("No responsive hosts found. This does not prove your LAN is empty.")
        print("Try an enhanced ARP discovery if permitted, or check router isolation.")


def _store(report: dict, directory: Path = REPORT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    filename = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.json"
    path = directory / filename
    rendered = io.StringIO()
    with contextlib.redirect_stdout(rendered):
        print_inventory(report, columns=90)
    saved = {**report, "kind": "lan", "count": report["device_count"],
             "exit_code": 0, "raw_output": rendered.getvalue()}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory,
        prefix=".lan-", delete=False
    ) as handle:
        tmp = Path(handle.name)
        os.fchmod(handle.fileno(), 0o600)
        try:
            json.dump(saved, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return path


def _ports_xml(xml: str) -> list[dict]:
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RuntimeError("Could not parse Nmap service-check response") from exc
    found = []
    for port in tree.findall(".//port"):
        state = port.find("state")
        if state is None or state.get("state") != "open":
            continue
        service = port.find("service")
        found.append({
            "port": port.get("portid", "?"),
            "protocol": port.get("protocol", "tcp"),
            "service": service.get("name", "unknown") if service is not None else "unknown",
        })
    return found


def inspect_services(ip: str, network: str, timeout: int = 35) -> list[dict]:
    target = ipaddress.IPv4Address(ip)
    allowed = ipaddress.IPv4Network(network, strict=False)
    if not allowed.is_private or target not in allowed:
        raise ValueError("Selected device is not within your directly connected private LAN")
    cmd = ["nmap", "-sT", "-Pn", "-n", "--open", "--max-retries", "1",
           "--host-timeout", "25s", "-p", COMMON_PORTS, "-oX", "-", ip]
    result = _run(cmd, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Selected-host TCP scan failed")
    return _ports_xml(result.stdout)


def interactive(report: dict, scanner=discover, port_checker=inspect_services) -> int:
    """Offer explicit, bounded selection instead of a wall of misaligned rows."""
    while True:
        print("\nChoose a DEVICE NUMBER for full details.")
        print("R. Rescan   E. Enhanced discovery (sudo/ARP)   0. Back", flush=True)
        try:
            answer = input().strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if answer in ("0", "b", ""):
            return 0
        if answer in ("r", "e"):
            try:
                report = scanner(interface=report["interface"], elevated=answer == "e")
                print_inventory(report)
                print(f"Saved: {_store(report)}")
            except (ValueError, RuntimeError, OSError) as exc:
                print(f"Discovery error: {exc}", flush=True)
            continue
        if not answer.isdecimal() or not 1 <= int(answer) <= len(report["devices"]):
            print("Invalid selection.")
            continue
        item = report["devices"][int(answer) - 1]
        print("\n=== SELECTED DEVICE ===", flush=True)
        details = (
            ("IP address", item["ip"]),
            ("Hostname", item["hostname"] or "unknown"),
            ("Hostname source", item["hostname_source"] or "not provided"),
            ("MAC address", item["mac"] or "unknown"),
            ("MAC evidence", item["mac_source"] or "not provided"),
            ("MAC vendor/OUI", item["vendor"] or "unknown"),
            ("Device clue", item["type"]),
            ("Reason for clue", item["type_evidence"]),
            ("Observed state", item["status"]),
            ("LAN interface", report["interface"]),
        )
        for key, value in details:
            print(f"  {key:<19}: {_clean(value)}", flush=True)
        while True:
            print("\nP. Check common TCP services on THIS device only")
            print("L. Return to device list   0. Back", flush=True)
            try:
                action = input().strip().lower()
            except (KeyboardInterrupt, EOFError):
                print()
                return 0
            if action == "0":
                return 0
            if action in ("l", ""):
                break
            if action != "p":
                print("Invalid selection.")
                continue
            print(f"Checking common TCP ports on {item['ip']} only...", flush=True)
            try:
                ports = port_checker(item["ip"], report["subnet"])
                if not ports:
                    print("No open ports found in the limited set (others may exist).")
                for entry in ports:
                    print(f"  {entry['port']}/{entry['protocol']}: {entry['service']}")
                print("Port names are hints, not verified device models or operating systems.")
            except (ValueError, RuntimeError) as exc:
                print(f"Service check failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="", help="Existing connected LAN interface")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--once", action="store_true", help="Show report without selections")
    args = parser.parse_args(argv)
    try:
        report = discover(interface=args.interface, timeout=args.timeout)
        print_inventory(report)
        print(f"Saved private device report: {_store(report)}", flush=True)
        if not args.once:
            return interactive(report)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"LAN discovery failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

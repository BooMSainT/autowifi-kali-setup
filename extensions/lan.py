"""Local-network device discovery for the AutoWIFI MCP extension.

This module distinguishes wireless interfaces, Wi-Fi access points, and hosts
on a directly connected private LAN. Active discovery is intentionally limited
to a connected local subnet.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET


def _run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def list_lan_interfaces() -> list[dict]:
    """Return non-loopback IPv4 interfaces and their connected networks."""
    result = _run(["ip", "-j", "-4", "address", "show"], timeout=15)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to list IPv4 interfaces")

    interfaces: list[dict] = []
    for item in json.loads(result.stdout or "[]"):
        ifname = item.get("ifname", "")
        if ifname == "lo":
            continue

        for address in item.get("addr_info", []):
            if address.get("family") != "inet" or address.get("scope") != "global":
                continue
            local = address.get("local")
            prefix = address.get("prefixlen")
            if not local or prefix is None:
                continue

            ip_interface = ipaddress.ip_interface(f"{local}/{prefix}")
            interfaces.append(
                {
                    "interface": ifname,
                    "address": str(ip_interface.ip),
                    "network": str(ip_interface.network),
                    "state": item.get("operstate", "UNKNOWN"),
                }
            )

    return sorted(interfaces, key=lambda value: (value["interface"], value["address"]))


def _default_route_interface() -> str:
    result = _run(["ip", "-j", "route", "show", "default"], timeout=15)
    if result.returncode != 0:
        return ""
    try:
        routes = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ""
    for route in routes:
        if route.get("dev"):
            return route["dev"]
    return ""


def _choose_scope(interface: str | None, subnet: str | None) -> tuple[str, ipaddress.IPv4Network]:
    connected = list_lan_interfaces()
    if not connected:
        raise RuntimeError("No connected non-loopback IPv4 interface was found")

    candidates = connected
    if interface:
        candidates = [item for item in connected if item["interface"] == interface]
        if not candidates:
            raise ValueError(f"Interface {interface!r} has no connected IPv4 network")

    if subnet:
        requested = ipaddress.ip_network(subnet, strict=False)
        if not isinstance(requested, ipaddress.IPv4Network):
            raise ValueError("Only IPv4 LAN discovery is supported")

        matches = []
        for item in candidates:
            local_network = ipaddress.ip_network(item["network"], strict=False)
            if requested.subnet_of(local_network):
                matches.append(item)
        if not matches:
            raise ValueError("The requested subnet is not directly connected to this computer")
        selected_interface = matches[0]["interface"]
        network = requested
    else:
        private = [
            item
            for item in candidates
            if ipaddress.ip_network(item["network"], strict=False).is_private
        ]
        preferred = private or candidates
        default_interface = _default_route_interface()
        selected = next(
            (item for item in preferred if item["interface"] == default_interface),
            preferred[0],
        )
        selected_interface = selected["interface"]
        network = ipaddress.ip_network(selected["network"], strict=False)

    if not network.is_private or network.is_loopback or network.is_multicast:
        raise ValueError("LAN discovery is limited to a directly connected private subnet")
    if network.num_addresses > 4096:
        raise ValueError("LAN discovery is limited to 4096 addresses per run")

    return selected_interface, network


def _device_type(hostname: str) -> str:
    """Return a type only when the hostname provides concrete evidence."""
    name = hostname.lower()
    if any(marker in name for marker in ("printer", "laserjet", "officejet")):
        return "printer"
    if any(marker in name for marker in ("iphone", "android", "galaxy", "pixel")):
        return "phone"
    if any(marker in name for marker in ("macbook", "laptop", "notebook", "desktop")):
        return "computer"
    return "unknown"


def _parse_nmap_xml(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    devices: list[dict] = []

    for host in root.findall("host"):
        status = host.find("status")
        if status is None or status.get("state") != "up":
            continue

        addresses = {
            item.get("addrtype", ""): item
            for item in host.findall("address")
        }
        ipv4 = addresses.get("ipv4")
        mac = addresses.get("mac")
        if ipv4 is None:
            continue

        hostname = ""
        hostname_element = host.find("hostnames/hostname")
        if hostname_element is not None:
            hostname = hostname_element.get("name", "")

        devices.append(
            {
                "ip": ipv4.get("addr", ""),
                "mac": mac.get("addr", "") if mac is not None else "",
                "vendor": mac.get("vendor", "") if mac is not None else "",
                "hostname": hostname,
                "device_type": _device_type(hostname),
                "state": "up",
            }
        )

    devices.sort(key=lambda item: ipaddress.ip_address(item["ip"]))
    return devices


def discover_lan_devices(
    interface: str | None = None,
    subnet: str | None = None,
    timeout: int = 120,
) -> dict:
    """Discover active hosts on one directly connected private IPv4 subnet."""
    if shutil.which("nmap") is None:
        raise RuntimeError("nmap is missing; install it with: sudo apt install nmap")

    selected_interface, network = _choose_scope(interface, subnet)
    timeout = max(15, min(int(timeout), 300))
    command = [
        "nmap",
        "-sn",
        "-n",
        "-e",
        selected_interface,
        "-oX",
        "-",
        str(network),
    ]
    result = _run(command, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "nmap LAN discovery failed")

    devices = _parse_nmap_xml(result.stdout)
    return {
        "interface": selected_interface,
        "subnet": str(network),
        "device_count": len(devices),
        "devices": devices,
        "classification_note": (
            "Device types are reported only when the hostname provides evidence; "
            "unknown does not mean the host is unimportant."
        ),
    }

"""Deterministic AutoWIFI shortcut runner.

Common read and scan operations run directly against AutoWIFI instead of
depending on a language model to choose and invoke the correct MCP tool.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

from autowifi import deps
from autowifi import interface as wifi_interface
from autowifi.cracker import find_wordlists
from autowifi.handshake import verify_handshake
from autowifi.lan import discover_lan_devices, list_lan_interfaces
from autowifi.scanner import NetworkScanner


INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _power(value: int) -> str:
    return f"{value} dBm" if isinstance(value, int) and value > -100 else "unknown"


def _target_matches(target: str, essid: str, bssid: str) -> bool:
    if not target:
        return True
    values = [
        value.strip().lower()
        for value in re.split(r"\s*/\s*|\s*,\s*", target)
        if value.strip()
    ]
    haystack = {essid.lower(), bssid.lower()}
    return any(value in item or item in value for value in values for item in haystack if item)


def _select_wireless_interface(requested: str):
    interfaces = wifi_interface.list_interfaces()
    if not interfaces:
        raise RuntimeError("No wireless interface was found by airmon-ng")
    if requested:
        for item in interfaces:
            if item.name == requested:
                return item
        names = ", ".join(item.name for item in interfaces)
        raise ValueError(f"Wireless interface {requested!r} was not found. Available: {names}")
    managed = [item for item in interfaces if item.mode.lower() == "managed"]
    return (managed or interfaces)[0]


def _interface_by_name(name: str, allow_monitor_suffix: bool = False):
    if not INTERFACE_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid interface name: {name!r}")
    interfaces = wifi_interface.list_interfaces()
    for item in interfaces:
        if item.name == name:
            return item
    if allow_monitor_suffix:
        for item in interfaces:
            if item.name == f"{name}mon":
                return item
    names = ", ".join(item.name for item in interfaces) or "none"
    raise ValueError(f"Wireless interface {name!r} was not found. Available: {names}")


def _start_network_services(monitor_interface: str = "") -> None:
    if monitor_interface and shutil.which("nmcli"):
        subprocess.run(
            ["nmcli", "device", "set", monitor_interface, "managed", "no"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    if shutil.which("systemctl"):
        for service in ("NetworkManager", "wpa_supplicant"):
            subprocess.run(
                ["systemctl", "start", service],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )


def _enable_monitor(interface: str) -> str:
    selected = _interface_by_name(interface)
    if selected.mode.lower() == "monitor":
        print(f"{selected.name} is already in monitor mode.")
        return selected.name
    monitor_interface = wifi_interface.enable_monitor(selected.name)
    if not monitor_interface:
        raise RuntimeError(f"Failed to enable monitor mode on {selected.name}")
    print(f"Monitor mode enabled: {selected.name} -> {monitor_interface}")
    return monitor_interface


def _enable_managed(interface: str) -> str:
    selected = _interface_by_name(interface, allow_monitor_suffix=True)
    if selected.mode.lower() != "monitor":
        print(f"{selected.name} is already in managed mode.")
        return selected.name
    if not wifi_interface.disable_monitor(selected.name):
        raise RuntimeError(f"Failed to restore managed mode from {selected.name}")
    restored_name = interface[:-3] if interface.endswith("mon") else interface
    print(f"Managed mode restored: {selected.name} -> {restored_name}")
    return restored_name


def _configure_radios(args) -> None:
    if args.monitor == args.managed:
        raise ValueError("The monitor and managed interfaces must be different")

    _enable_managed(args.managed)
    monitor_interface = _enable_monitor(args.monitor)
    _start_network_services(monitor_interface)

    print()
    print("FINAL WIRELESS INTERFACE STATE")
    print("NAME\tMODE\tMAC\tDRIVER\tCHIPSET")
    for item in wifi_interface.list_interfaces():
        print(f"{item.name}\t{item.mode}\t{item.mac}\t{item.driver}\t{item.chipset}")
    print()
    print(
        f"Requested state: {args.monitor} monitor; {args.managed} managed. "
        "The final table above is the verification result."
    )


def _scan_wireless(args, include_clients: bool) -> None:
    selected = _select_wireless_interface(args.interface)
    monitor_interface = selected.name
    enabled_here = selected.mode.lower() != "monitor"

    if enabled_here:
        monitor_interface = wifi_interface.enable_monitor(selected.name)
        if not monitor_interface:
            raise RuntimeError(f"Failed to enable monitor mode on {selected.name}")

    try:
        scanner = NetworkScanner(monitor_interface)
        networks = scanner.scan(duration=args.duration)
        networks = [
            network
            for network in networks
            if _target_matches(args.target, network.essid, network.bssid)
        ]
        networks.sort(key=lambda network: network.power, reverse=True)

        print(f"Interface: {monitor_interface}")
        print(f"Scan duration: {args.duration} seconds")
        print(f"Target filter: {args.target or 'all observed access points'}")
        print()
        print("ACCESS POINTS — strongest observed power first")
        print("POWER\tBSSID\tCHANNEL\tENCRYPTION\tSSID")
        for network in networks:
            print(
                f"{_power(network.power)}\t{network.bssid}\t{network.channel}\t"
                f"{network.enc_display or 'unknown'}\t{network.essid or '<hidden>'}"
            )
        if not networks:
            print("No matching access points were observed.")

        if include_clients:
            clients = []
            for network in networks:
                for client in network.clients or []:
                    clients.append((client, network))
            clients.sort(key=lambda value: value[0].power, reverse=True)

            print()
            print("ASSOCIATED CLIENT STATIONS — strongest observed power first")
            print("POWER\tCLIENT MAC\tAP BSSID\tSSID\tPACKETS\tPROBES")
            for client, network in clients:
                probes = ", ".join(client.probes) if client.probes else ""
                print(
                    f"{_power(client.power)}\t{client.mac}\t{network.bssid}\t"
                    f"{network.essid or '<hidden>'}\t{client.packets}\t{probes}"
                )
            if not clients:
                print("No associated client stations were observed during this scan.")

        print()
        print("Power is relative signal strength. It is not an exact distance measurement.")
    finally:
        if enabled_here and monitor_interface:
            restored = wifi_interface.disable_monitor(monitor_interface)
            print(f"Managed-mode restore: {'passed' if restored else 'failed'}")


def _print_interfaces() -> None:
    print("WIRELESS INTERFACES")
    print("NAME\tMODE\tMAC\tDRIVER\tCHIPSET")
    for item in wifi_interface.list_interfaces():
        print(f"{item.name}\t{item.mode}\t{item.mac}\t{item.driver}\t{item.chipset}")

    print()
    print("LAN INTERFACES")
    print("NAME\tADDRESS\tNETWORK\tSTATE")
    for item in list_lan_interfaces():
        print(
            f"{item['interface']}\t{item['address']}\t"
            f"{item['network']}\t{item['state']}"
        )


def _print_dependencies() -> None:
    results = deps.check_all()
    print("TOOL\tSTATUS")
    for name in sorted(results):
        print(f"{name}\t{'installed' if results[name] else 'missing'}")


def _print_devices(args) -> None:
    result = discover_lan_devices(
        interface=args.interface or None,
        subnet=args.subnet or None,
        timeout=args.timeout,
    )
    print(f"Interface: {result['interface']}")
    print(f"Subnet: {result['subnet']}")
    print(f"Devices: {result['device_count']}")
    print()
    print("IP\tMAC\tHOSTNAME\tVENDOR\tDEVICE TYPE")
    for item in result["devices"]:
        print(
            f"{item['ip']}\t{item['mac'] or 'unknown'}\t"
            f"{item['hostname'] or 'unknown'}\t{item['vendor'] or 'unknown'}\t"
            f"{item['device_type']}"
        )
    print()
    print(result["classification_note"])


def _print_wordlists() -> None:
    wordlists = find_wordlists()
    print("SIZE\tPATH")
    for item in wordlists:
        print(f"{item[1]}\t{item[0]}")
    if not wordlists:
        print("No known wordlists were found.")


def _verify_capture(args) -> None:
    valid = verify_handshake(args.capture, args.bssid or None)
    print(f"Capture: {args.capture}")
    print(f"Expected BSSID: {args.bssid or 'not supplied'}")
    print(f"Valid WPA handshake: {'yes' if valid else 'no'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic AutoWIFI shortcuts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text, duration in (
        ("networks", "Scan Wi-Fi access points", 30),
        ("nearby", "Scan access points and associated clients", 60),
    ):
        child = subparsers.add_parser(command, help=help_text)
        child.add_argument("--target", default="")
        child.add_argument("--interface", default="")
        child.add_argument("--duration", type=int, default=duration, choices=range(10, 301))

    subparsers.add_parser("interfaces", help="List wireless and LAN interfaces")
    subparsers.add_parser("check", help="Check AutoWIFI dependencies")
    subparsers.add_parser("wordlists", help="List discovered wordlists")

    monitor = subparsers.add_parser("monitor", help="Put one wireless interface in monitor mode")
    monitor.add_argument("interface")

    managed = subparsers.add_parser("managed", help="Restore one wireless interface to managed mode")
    managed.add_argument("interface")

    radios = subparsers.add_parser(
        "radios",
        help="Set one wireless interface to monitor and another to managed mode",
    )
    radios.add_argument("--monitor", required=True)
    radios.add_argument("--managed", required=True)

    devices = subparsers.add_parser("devices", help="Discover hosts on the private LAN")
    devices.add_argument("--interface", default="")
    devices.add_argument("--subnet", default="")
    devices.add_argument("--timeout", type=int, default=120)

    verify = subparsers.add_parser("verify", help="Verify a WPA capture")
    verify.add_argument("capture")
    verify.add_argument("--bssid", default="")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "nearby":
            _scan_wireless(args, include_clients=True)
        elif args.command == "networks":
            _scan_wireless(args, include_clients=False)
        elif args.command == "interfaces":
            _print_interfaces()
        elif args.command == "check":
            _print_dependencies()
        elif args.command == "devices":
            _print_devices(args)
        elif args.command == "wordlists":
            _print_wordlists()
        elif args.command == "monitor":
            _enable_monitor(args.interface)
        elif args.command == "managed":
            _enable_managed(args.interface)
        elif args.command == "radios":
            _configure_radios(args)
        elif args.command == "verify":
            _verify_capture(args)
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$HOME/AutoWIFI"

if [[ ! -d "$PROJECT" ]]; then
  echo "Missing AutoWIFI checkout: $PROJECT" >&2
  echo "Clone the AutoWIFI source there before continuing." >&2
  exit 1
fi

command -v pipx >/dev/null || {
  echo "Install pipx first: sudo apt install pipx" >&2
  exit 1
}

if ! command -v nmap >/dev/null; then
  echo "Installing nmap for real LAN device discovery..."
  sudo apt-get update
  sudo apt-get install -y nmap
fi

if [[ ! -w "$PROJECT" ]]; then
  printf 'Fix ownership of %s? [y/N] ' "$PROJECT"
  read -r answer
  [[ "$answer" =~ ^[Yy]$ ]] || exit 1
  sudo chown -R "$(id -un):$(id -gn)" "$PROJECT"
fi

cd "$PROJECT"

install -m 0644 "$SCRIPT_DIR/extensions/lan.py" "$PROJECT/autowifi/lan.py"

python3 - <<'PY'
from pathlib import Path

mcp_file = Path("autowifi/mcp_server.py")
source = mcp_file.read_text()
changed = False

old_import = "from mcp.server.stdio import run_stdio"
old_call = "asyncio.run(run_stdio(server))"
new_import = "from mcp.server.stdio import stdio_server"
new_call = """async def serve():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    asyncio.run(serve())"""

if old_import in source and old_call in source:
    source = source.replace(old_import, new_import)
    source = source.replace(old_call, new_call)
    changed = True
    print("Patched AutoWIFI MCP stdio compatibility.")
elif new_import in source:
    print("MCP stdio patch already present.")
else:
    raise SystemExit("Unexpected AutoWIFI MCP source; refusing to modify it.")

lan_import = "from autowifi.lan import list_lan_interfaces, discover_lan_devices\n"
import_anchor = "from autowifi.handshake import verify_handshake\n"
if lan_import not in source:
    if import_anchor not in source:
        raise SystemExit("Unexpected AutoWIFI import section; refusing to modify it.")
    source = source.replace(import_anchor, import_anchor + lan_import, 1)
    changed = True
    print("Added LAN device discovery imports.")
else:
    print("LAN device discovery imports already present.")

lan_tools = '''        Tool(
            name="list_lan_interfaces",
            description="List local IPv4 interfaces and their directly connected networks. Interfaces are adapters, not printers, phones, or computers.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="discover_lan_devices",
            description="Discover active hosts such as computers, phones, and printers on one directly connected private LAN using nmap. Device type is unknown unless the hostname provides evidence.",
            inputSchema={
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "description": "Connected interface such as wlan0 or eth0 (optional)"},
                    "subnet": {"type": "string", "description": "Directly connected private IPv4 subnet such as 192.168.1.0/24 (optional)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds, from 15 to 300", "default": 120},
                },
            },
        ),
'''
tool_anchor = '        Tool(\n            name="find_wordlists",\n'
if 'name="discover_lan_devices"' not in source:
    if tool_anchor not in source:
        raise SystemExit("Unexpected AutoWIFI tool list; refusing to modify it.")
    source = source.replace(tool_anchor, lan_tools + tool_anchor, 1)
    changed = True
    print("Added LAN device discovery tools.")
else:
    print("LAN device discovery tools already present.")

lan_dispatch = '''    elif name == "list_lan_interfaces":
        return list_lan_interfaces()

    elif name == "discover_lan_devices":
        return discover_lan_devices(
            interface=args.get("interface"),
            subnet=args.get("subnet"),
            timeout=args.get("timeout", 120),
        )

'''
dispatch_anchor = '    elif name == "find_wordlists":\n'
if 'elif name == "discover_lan_devices":' not in source:
    if dispatch_anchor not in source:
        raise SystemExit("Unexpected AutoWIFI tool dispatcher; refusing to modify it.")
    source = source.replace(dispatch_anchor, lan_dispatch + dispatch_anchor, 1)
    changed = True
    print("Added LAN device discovery dispatch.")
else:
    print("LAN device discovery dispatch already present.")

old_clients = '        "clients": len(net.clients) if net.clients else 0,\n'
new_clients = '''        "client_count": len(net.clients) if net.clients else 0,
        "clients": [
            {
                "mac": client.mac,
                "power": client.power,
                "packets": client.packets,
                "probes": client.probes,
            }
            for client in (net.clients or [])
        ],
'''
if old_clients in source:
    source = source.replace(old_clients, new_clients, 1)
    changed = True
    print("Added associated Wi-Fi client details to scan results.")
elif new_clients in source:
    print("Associated Wi-Fi client details already present.")
else:
    raise SystemExit("Unexpected AutoWIFI network serializer; refusing to modify it.")

old_wordlists = 'return [{"path": w[0], "size": w[1], "name": w[2]} for w in wl]'
new_wordlists = 'return [{"path": w[0], "size": w[1], "name": w[2] if len(w) > 2 else w[0].rsplit("/", 1)[-1]} for w in wl]'
if old_wordlists in source:
    source = source.replace(old_wordlists, new_wordlists)
    changed = True
    print("Patched AutoWIFI wordlist discovery compatibility.")
elif new_wordlists in source:
    print("Wordlist discovery patch already present.")
else:
    raise SystemExit("Unexpected AutoWIFI wordlist handler; refusing to modify it.")

if changed:
    mcp_file.write_text(source)

cracker_file = Path("autowifi/cracker.py")
cracker = cracker_file.read_text()
local_wordlist = '        str(Path.home() / ".local/share/autowifi/wordlists/rockyou.txt"),\n'
common_paths = "    common_paths = [\n"
if local_wordlist not in cracker:
    if common_paths not in cracker:
        raise SystemExit("Unexpected AutoWIFI cracker source; refusing to modify it.")
    cracker = cracker.replace(common_paths, common_paths + local_wordlist, 1)
    cracker_file.write_text(cracker)
    print("Added the user-local AutoWIFI wordlist path.")
else:
    print("User-local wordlist path already present.")
PY

pipx install --force '.[mcp]'
pipx inject --force autowifi 'mcp>=1,<2'
pipx list

echo "Installation complete. MCP entry point: $(command -v autowifi-mcp || echo missing)"

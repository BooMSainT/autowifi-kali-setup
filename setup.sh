#!/usr/bin/env bash
set -euo pipefail
PROJECT="$HOME/AutoWIFI"
if [[ ! -d "$PROJECT" ]]; then
  echo "Missing AutoWIFI checkout: $PROJECT" >&2
  exit 1
fi
command -v pipx >/dev/null || { echo "Install pipx first: sudo apt install pipx"; exit 1; }
if [[ ! -w "$PROJECT" ]]; then
  printf 'Fix ownership of %s? [y/N] ' "$PROJECT"
  read -r answer
  [[ "$answer" =~ ^[Yy]$ ]] || exit 1
  sudo chown -R "$(id -un):$(id -gn)" "$PROJECT"
fi
cd "$PROJECT"
# Patch the outdated MCP stdio entry point in the locally cloned AutoWIFI.
python3 - <<'PY'
from pathlib import Path
p = Path("autowifi/mcp_server.py")
s = p.read_text()
old_import = "from mcp.server.stdio import run_stdio"
old_call = "asyncio.run(run_stdio(server))"
if old_import in s and old_call in s:
    s = s.replace(old_import, "from mcp.server.stdio import stdio_server")
    s = s.replace(old_call, """async def serve():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    asyncio.run(serve())""")
    p.write_text(s)
    print("Patched AutoWIFI MCP stdio compatibility.")
elif "from mcp.server.stdio import stdio_server" in s:
    print("MCP patch already present.")
else:
    raise SystemExit("Unexpected AutoWIFI MCP source; refusing to modify it.")
PY
pipx install --force '.[mcp]'
# AutoWIFI uses the MCP 1.x Server decorator API; 2.x removed it.
pipx inject --force autowifi 'mcp>=1,<2'
pipx list
echo "Installation complete. MCP entry point: $(command -v autowifi-mcp || echo missing)"

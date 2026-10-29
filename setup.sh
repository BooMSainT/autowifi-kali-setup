#!/usr/bin/env bash
set -euo pipefail

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

if [[ ! -w "$PROJECT" ]]; then
  printf 'Fix ownership of %s? [y/N] ' "$PROJECT"
  read -r answer
  [[ "$answer" =~ ^[Yy]$ ]] || exit 1
  sudo chown -R "$(id -un):$(id -gn)" "$PROJECT"
fi

cd "$PROJECT"

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

#!/usr/bin/env bash
set -euo pipefail
echo "Setting up local Ollama + MCPHost for AutoWIFI..."
if ! command -v autowifi-mcp >/dev/null; then
  echo "AutoWIFI MCP missing; run bash setup.sh first." >&2; exit 1
fi
if ! command -v ollama >/dev/null; then
  echo "Downloading Ollama installer for review..."
  curl -fsSL https://ollama.com/install.sh -o /tmp/autowifi-ollama-install.sh
  echo "Ollama installer saved to /tmp/autowifi-ollama-install.sh"
  read -r -p "Run the downloaded Ollama installer? [y/N] " ok
  [[ "$ok" =~ ^[Yy]$ ]] || exit 1
  sh /tmp/autowifi-ollama-install.sh
fi
if ! command -v go >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y golang-go
fi
echo "Installing MCPHost..."
go install github.com/mark3labs/mcphost@latest
export PATH="$HOME/go/bin:$PATH"
command -v mcphost >/dev/null || { echo "MCPHost binary unavailable." >&2; exit 1; }
if ! ollama list >/dev/null 2>&1; then
  (nohup ollama serve > "$HOME/.ollama-autowifi.log" 2>&1 < /dev/null &)
  sleep 3
fi
ollama pull qwen2.5:3b
mkdir -p "$HOME/.config/autowifi-kali-setup"
cat > "$HOME/.config/autowifi-kali-setup/mcphost.json" <<'JSON'
{
  "mcpServers": {
    "autowifi": {
      "command": "/home/user/.local/bin/autowifi-mcp",
      "args": []
    }
  }
}
JSON
# Substitute the actual home directory for portability.
sed -i "s|/home/user/|$HOME/|g" "$HOME/.config/autowifi-kali-setup/mcphost.json"
cat > "$HOME/.local/bin/autowifi-ai" <<'SH'
#!/usr/bin/env bash
set -e
export PATH="$HOME/go/bin:$PATH"
exec mcphost -m ollama:qwen2.5:3b --config "$HOME/.config/autowifi-kali-setup/mcphost.json"
SH
chmod +x "$HOME/.local/bin/autowifi-ai"
echo "Ready. Run: autowifi-ai"
echo "Use only on networks you own or have explicit permission to test."

#!/usr/bin/env bash
set -euo pipefail

MODEL="${AUTOWIFI_AI_MODEL:-qwen2.5:1.5b}"

echo "Setting up local Ollama + MCPHost for AutoWIFI..."
echo "AI model: $MODEL"

if ! command -v autowifi-mcp >/dev/null; then
  echo "AutoWIFI MCP missing; run bash setup.sh first." >&2
  exit 1
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
  (
    export OLLAMA_KEEP_ALIVE=30m
    export OLLAMA_CONTEXT_LENGTH=4096
    nohup ollama serve > "$HOME/.ollama-autowifi.log" 2>&1 < /dev/null &
  )
  sleep 3
fi

ollama pull "$MODEL"

mkdir -p "$HOME/.config/autowifi-kali-setup" "$HOME/.local/bin"
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
set -euo pipefail

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-qwen2.5:1.5b}"

if ! ollama list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -Fxq "$model"; then
  echo "Model $model is not installed." >&2
  echo "Install it with: ollama pull $model" >&2
  exit 1
fi

exec mcphost -m "ollama:$model" --config "$HOME/.config/autowifi-kali-setup/mcphost.json"
SH
chmod +x "$HOME/.local/bin/autowifi-ai"

echo "Ready. Run: autowifi-ai"
echo "For the larger model: AUTOWIFI_AI_MODEL=qwen2.5:3b autowifi-ai"
echo "Use only on networks you own or have explicit permission to test."

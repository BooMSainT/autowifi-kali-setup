#!/usr/bin/env bash
set -euo pipefail

BASE_MODEL="${AUTOWIFI_AI_BASE_MODEL:-qwen2.5:1.5b}"
PROFILE_MODEL="${AUTOWIFI_AI_PROFILE:-autowifi-fast}"
CONTEXT_SIZE="${AUTOWIFI_AI_CONTEXT:-4096}"
MAX_TOKENS="${AUTOWIFI_AI_MAX_TOKENS:-256}"
TEMPERATURE="${AUTOWIFI_AI_TEMPERATURE:-0.2}"
CONFIG_DIR="$HOME/.config/autowifi-kali-setup"
BIN_DIR="$HOME/.local/bin"

valid_model_name='^[A-Za-z0-9._:/-]+$'
[[ "$BASE_MODEL" =~ $valid_model_name ]] || { echo "Invalid base model name." >&2; exit 1; }
[[ "$PROFILE_MODEL" =~ $valid_model_name ]] || { echo "Invalid profile model name." >&2; exit 1; }
[[ "$CONTEXT_SIZE" =~ ^[0-9]+$ ]] || { echo "AUTOWIFI_AI_CONTEXT must be a positive integer." >&2; exit 1; }
[[ "$MAX_TOKENS" =~ ^[0-9]+$ ]] || { echo "AUTOWIFI_AI_MAX_TOKENS must be a positive integer." >&2; exit 1; }
[[ "$TEMPERATURE" =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]] || { echo "AUTOWIFI_AI_TEMPERATURE must be between 0 and 1." >&2; exit 1; }

echo "Setting up local Ollama + MCPHost for AutoWIFI..."
echo "Base model: $BASE_MODEL"
echo "Fast profile: $PROFILE_MODEL"

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
    nohup ollama serve > "$HOME/.ollama-autowifi.log" 2>&1 < /dev/null &
  )
  sleep 3
fi

ollama pull "$BASE_MODEL"

mkdir -p "$CONFIG_DIR" "$BIN_DIR"

cat > "$CONFIG_DIR/Modelfile.fast" <<EOF
FROM $BASE_MODEL
PARAMETER num_ctx $CONTEXT_SIZE
PARAMETER num_predict $MAX_TOKENS
PARAMETER temperature $TEMPERATURE
EOF

echo "Creating tuned fast profile..."
ollama create "$PROFILE_MODEL" -f "$CONFIG_DIR/Modelfile.fast"

cat > "$CONFIG_DIR/mcphost.json" <<'JSON'
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
sed -i "s|/home/user/|$HOME/|g" "$CONFIG_DIR/mcphost.json"

cat > "$BIN_DIR/autowifi-ai" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
command -v mcphost >/dev/null || { echo "mcphost is not installed." >&2; exit 1; }
command -v ollama >/dev/null || { echo "ollama is not installed." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || {
  echo "Model $model is not installed." >&2
  echo "Install it with: ollama pull $model" >&2
  exit 1
}

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null || {
    echo "Ollama is not responding on 127.0.0.1:11434." >&2
    exit 1
  }

exec mcphost -m "ollama:$model" --config "$HOME/.config/autowifi-kali-setup/mcphost.json"
SH
chmod +x "$BIN_DIR/autowifi-ai"

cat > "$BIN_DIR/autowifi-ai-doctor" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
echo "AutoWIFI AI diagnostic"
echo "Model: $model"
echo "CPU threads: $(nproc)"
free -h
echo
ollama --version
echo
ollama ps
echo
if ollama show "$model" >/dev/null 2>&1; then
  echo "Model check: installed"
else
  echo "Model check: missing"
  exit 1
fi

start=$(date +%s)
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"prompt\":\"Reply with READY only.\",\"stream\":false,\"keep_alive\":\"30m\"}" \
  >/dev/null
elapsed=$(( $(date +%s) - start ))
echo "Warm response check: passed in ${elapsed}s"
SH
chmod +x "$BIN_DIR/autowifi-ai-doctor"

echo "Ready. Run: autowifi-ai"
echo "Diagnostic: autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."

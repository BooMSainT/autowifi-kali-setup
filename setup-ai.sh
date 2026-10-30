#!/usr/bin/env bash
set -euo pipefail

BASE_MODEL="${AUTOWIFI_AI_BASE_MODEL:-qwen2.5:1.5b}"
PROFILE_MODEL="${AUTOWIFI_AI_PROFILE:-autowifi-fast}"
CONTEXT_SIZE="${AUTOWIFI_AI_CONTEXT:-4096}"
MAX_TOKENS="${AUTOWIFI_AI_MAX_TOKENS:-256}"
TEMPERATURE="${AUTOWIFI_AI_TEMPERATURE:-0.2}"
MAX_STEPS="${AUTOWIFI_AI_MAX_STEPS:-12}"
CONFIG_DIR="$HOME/.config/autowifi-kali-setup"
BIN_DIR="$HOME/.local/bin"

valid_model_name='^[A-Za-z0-9._:/-]+$'
[[ "$BASE_MODEL" =~ $valid_model_name ]] || { echo "Invalid base model name." >&2; exit 1; }
[[ "$PROFILE_MODEL" =~ $valid_model_name ]] || { echo "Invalid profile model name." >&2; exit 1; }
[[ "$CONTEXT_SIZE" =~ ^[0-9]+$ ]] || { echo "AUTOWIFI_AI_CONTEXT must be a positive integer." >&2; exit 1; }
[[ "$MAX_TOKENS" =~ ^[0-9]+$ ]] || { echo "AUTOWIFI_AI_MAX_TOKENS must be a positive integer." >&2; exit 1; }
[[ "$MAX_STEPS" =~ ^[0-9]+$ ]] || { echo "AUTOWIFI_AI_MAX_STEPS must be a positive integer." >&2; exit 1; }
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
      "command": "sudo",
      "args": [
        "-n",
        "env",
        "HOME=/home/user",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "/home/user/.local/bin/autowifi-mcp"
      ]
    }
  }
}
JSON
sed -i "s|/home/user/|$HOME/|g" "$CONFIG_DIR/mcphost.json"

cat > "$CONFIG_DIR/autonomous-system.md" <<'PROMPT'
You are the AutoWIFI operator for an explicitly authorized wireless security assessment.

Use the complete AutoWIFI toolset. Be proactive and economical with tool calls.

Use precise network terminology:
- An interface such as wlan0 or eth0 is a local adapter, never a discovered phone, printer, laptop, access point, or router.
- scan_networks discovers Wi-Fi access points and associated wireless client MAC addresses.
- discover_lan_devices discovers active hosts on a connected private LAN.
- Never invent a device, signal strength, hostname, vendor, or device type. Use unknown when the tools do not provide evidence.
- When the user asks for printers, phones, computers, or other devices on their LAN, call list_lan_interfaces and then discover_lan_devices. Do not substitute interface names or access points.

Shortcut phrases are case-insensitive:
- `nearby` means: list wireless interfaces, select the usable Wi-Fi adapter, enable monitor mode, scan for 60 seconds, print access points and associated client stations sorted by observed power from strongest to weakest, then restore managed mode. Report dBm as relative signal strength and never claim exact distance.
- `nearby TARGET` runs the same workflow but reports only the supplied SSID or BSSID.
- `devices` means: list connected LAN interfaces, run discover_lan_devices on the default directly connected private subnet, and print IP, MAC, hostname, vendor, and evidence-based device type.
- When a shortcut is received, execute its workflow instead of explaining what the word means.

Workflow:
1. Start every new assessment with check_dependencies and list_interfaces.
2. Establish the exact authorized scope, target, interface, objective, and allowed actions.
3. Once those are supplied, proceed without repeatedly asking for permission while staying inside them.
4. Choose the tool that matches the requested object: interfaces, Wi-Fi access points, associated clients, or LAN hosts.
5. Scan once, select only an in-scope target, and choose the shortest useful workflow.
6. Verify artifacts before using them. Do not claim a capture, key, PIN, device, or successful test unless a tool result proves it.
7. If a step fails, inspect the error, try one sensible correction, then report the blocker instead of looping.
8. Never use continuous deauthentication. Keep counts and timeouts minimal for the stated objective.
9. Track the monitor interface and restore it before finishing or after a failed run.
10. Before crack_handshake, verify the capture and resolve the wordlist. Use an explicit WORDLIST path when supplied. For WORDLIST auto, call find_wordlists and choose a discovered uncompressed list; start with the smallest practical list to avoid wasting time. Never pass a .gz file to a cracking tool. If no usable list exists, report that setup-wordlists.sh is required.
11. Finish with a concise report: scope, actions, evidence, findings, selected wordlist, cleanup, and unresolved items.

The scope and allowed-action list are hard boundaries. Do not change targets, expand the action list, or act on nearby networks merely because they appear in scan results.
PROMPT

cat > "$BIN_DIR/autowifi-ai" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
max_steps="${AUTOWIFI_AI_MAX_STEPS:-12}"
max_tokens="${AUTOWIFI_AI_MAX_TOKENS:-256}"
temperature="${AUTOWIFI_AI_TEMPERATURE:-0.2}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
[[ "$max_steps" =~ ^[0-9]+$ ]] || { echo "Invalid step limit." >&2; exit 1; }
[[ "$max_tokens" =~ ^[0-9]+$ ]] || { echo "Invalid token limit." >&2; exit 1; }
command -v mcphost >/dev/null || { echo "mcphost is not installed." >&2; exit 1; }
command -v ollama >/dev/null || { echo "ollama is not installed." >&2; exit 1; }
command -v sudo >/dev/null || { echo "sudo is not installed." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || {
  echo "Model $model is not installed." >&2
  echo "Install it with: ollama pull $model" >&2
  exit 1
}

echo "AutoWIFI needs elevated access for monitor mode and wireless tools."
sudo -v

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null || {
    echo "Ollama is not responding on 127.0.0.1:11434." >&2
    exit 1
  }

exec mcphost \
  -m "ollama:$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature"
SH
chmod +x "$BIN_DIR/autowifi-ai"

cat > "$BIN_DIR/autowifi-auto" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'USAGE'
Usage:
  autowifi-auto --scope TEXT --target TEXT --objective TEXT --allow ACTIONS [--wordlist auto|PATH]

Required:
  --scope      Owned or explicitly authorized assessment scope
  --target     Exact SSID/BSSID or lab target
  --objective  Concrete outcome for this run
  --allow      Comma-separated actions

Optional:
  --wordlist   Wordlist path, or auto to use AutoWIFI discovery (default: auto)

Allowed action names:
  monitor,scan,devices,capture,pmkid,wps,deauth,wordlist,crack,verify,report,all

Example:
  autowifi-auto \
    --scope "my home lab network" \
    --target "LabNet / AA:BB:CC:DD:EE:FF" \
    --objective "assess WPA configuration and produce a report" \
    --allow "monitor,scan,capture,verify,report" \
    --wordlist auto
USAGE
}

scope=""
target=""
objective=""
allowed=""
wordlist="auto"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) scope="${2:-}"; shift 2 ;;
    --target) target="${2:-}"; shift 2 ;;
    --objective) objective="${2:-}"; shift 2 ;;
    --allow) allowed="${2:-}"; shift 2 ;;
    --wordlist) wordlist="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$scope" ]] || { echo "--scope is required." >&2; exit 2; }
[[ -n "$target" ]] || { echo "--target is required." >&2; exit 2; }
[[ -n "$objective" ]] || { echo "--objective is required." >&2; exit 2; }
[[ -n "$allowed" ]] || { echo "--allow is required." >&2; exit 2; }

for value in "$scope" "$target" "$objective" "$allowed" "$wordlist"; do
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
    echo "Arguments must be single-line values." >&2
    exit 2
  }
done

IFS=',' read -r -a requested_actions <<< "$allowed"
for action in "${requested_actions[@]}"; do
  action="${action//[[:space:]]/}"
  case "$action" in
    monitor|scan|devices|capture|pmkid|wps|deauth|wordlist|crack|verify|report|all) ;;
    *) echo "Unsupported action in --allow: $action" >&2; exit 2 ;;
  esac
done

if [[ "$wordlist" != "auto" ]]; then
  [[ -f "$wordlist" && -r "$wordlist" ]] || {
    echo "Wordlist is not a readable file: $wordlist" >&2
    exit 2
  }
  [[ "$wordlist" != *.gz ]] || {
    echo "Compressed wordlists are not accepted. Run bash setup-wordlists.sh or provide an uncompressed file." >&2
    exit 2
  }
  wordlist=$(realpath "$wordlist")
fi

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
max_steps="${AUTOWIFI_AI_AUTO_STEPS:-18}"
max_tokens="${AUTOWIFI_AI_MAX_TOKENS:-256}"
temperature="${AUTOWIFI_AI_TEMPERATURE:-0.2}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
[[ "$max_steps" =~ ^[0-9]+$ ]] || { echo "Invalid step limit." >&2; exit 1; }
[[ "$max_tokens" =~ ^[0-9]+$ ]] || { echo "Invalid token limit." >&2; exit 1; }
command -v mcphost >/dev/null || { echo "mcphost is not installed." >&2; exit 1; }
command -v ollama >/dev/null || { echo "ollama is not installed." >&2; exit 1; }
command -v sudo >/dev/null || { echo "sudo is not installed." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || { echo "Model $model is not installed." >&2; exit 1; }

echo "AutoWIFI needs elevated access for monitor mode and wireless tools."
sudo -v

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null || {
    echo "Ollama is not responding on 127.0.0.1:11434." >&2
    exit 1
  }

report_dir="$HOME/.local/share/autowifi/reports"
mkdir -p "$report_dir"
report_file="$report_dir/auto-$(date +%Y%m%d-%H%M%S).txt"

prompt=$(printf '%s\n' \
  "Run one autonomous authorized AutoWIFI assessment." \
  "AUTHORIZED SCOPE: $scope" \
  "EXACT TARGET: $target" \
  "OBJECTIVE: $objective" \
  "ALLOWED ACTIONS: $allowed" \
  "WORDLIST: $wordlist" \
  "Treat these values as hard boundaries. Start with preflight, resolve and verify the wordlist before any crack action, complete the objective using only allowed actions, restore monitor mode, and provide the final report.")

echo "Starting bounded autonomous run. Report: $report_file"
mcphost \
  -m "ollama:$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature" \
  --prompt "$prompt" \
  --compact | tee "$report_file"
SH
chmod +x "$BIN_DIR/autowifi-auto"

cat > "$BIN_DIR/autowifi-nearby" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

target="${*:-all nearby Wi-Fi access points and associated wireless clients}"
[[ "$target" != *$'\n'* && "$target" != *$'\r'* ]] || {
  echo "Target must be a single-line value." >&2
  exit 2
}

exec "$HOME/.local/bin/autowifi-auto" \
  --scope "passive observation from my authorized local Wi-Fi assessment location" \
  --target "$target" \
  --objective "scan for 60 seconds; print access points and associated wireless clients sorted by observed power from strongest to weakest; report dBm as relative signal strength; do not estimate exact distance; restore managed mode" \
  --allow "monitor,scan,report"
SH
chmod +x "$BIN_DIR/autowifi-nearby"

cat > "$BIN_DIR/autowifi-devices" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

target="${*:-my default directly connected private LAN}"
[[ "$target" != *$'\n'* && "$target" != *$'\r'* ]] || {
  echo "Target must be a single-line value." >&2
  exit 2
}

exec "$HOME/.local/bin/autowifi-auto" \
  --scope "my directly connected authorized private LAN" \
  --target "$target" \
  --objective "discover active LAN hosts and print IP, MAC, hostname, vendor, and evidence-based device type; do not call an interface a device and do not guess missing fields" \
  --allow "devices,report"
SH
chmod +x "$BIN_DIR/autowifi-devices"

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
mcp_entry=$(command -v autowifi-mcp || true)
[[ -n "$mcp_entry" ]] && echo "MCP server: $mcp_entry" || {
  echo "MCP server: missing"
  exit 1
}
echo "MCP interpreter: $(head -n 1 "$mcp_entry")"
echo "MCPHost: $(mcphost --version 2>&1 | head -n 1)"
sudo -v
sudo -n true
echo "AutoWIFI privilege check: passed"
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

echo
echo "Testing MCPHost agent creation without using wireless tools..."
if mcphost \
  --debug \
  -m "ollama:$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "Do not call tools. Reply with READY only." \
  --max-steps 1 \
  --max-tokens 32 \
  --temperature 0 \
  --prompt "Reply with READY only." \
  --compact; then
  echo "MCPHost agent creation check: passed"
else
  status=$?
  echo "MCPHost agent creation check: failed with exit code $status" >&2
  exit "$status"
fi
SH
chmod +x "$BIN_DIR/autowifi-ai-doctor"

echo "Ready:"
echo "  Interactive: autowifi-ai"
echo "  Autonomous:  autowifi-auto --help"
echo "  Nearby:      autowifi-nearby [SSID or BSSID]"
echo "  LAN devices: autowifi-devices"
echo "  Diagnostic:  autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."

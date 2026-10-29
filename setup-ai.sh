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
      "command": "/home/user/.local/bin/autowifi-mcp",
      "args": []
    }
  }
}
JSON
sed -i "s|/home/user/|$HOME/|g" "$CONFIG_DIR/mcphost.json"

cat > "$CONFIG_DIR/autonomous-system.md" <<'PROMPT'
You are the AutoWIFI operator for an explicitly authorized wireless security assessment.

Use the complete AutoWIFI toolset. Be proactive and economical with tool calls.

Workflow:
1. Start every new assessment with check_dependencies and list_interfaces.
2. Establish the exact authorized scope, target, interface, objective, and allowed actions.
3. Once those are supplied, proceed without repeatedly asking for permission while staying inside them.
4. Scan once, select only an in-scope target, and choose the shortest useful workflow.
5. Verify artifacts before using them. Do not claim a capture, key, PIN, or successful test unless a tool result proves it.
6. If a step fails, inspect the error, try one sensible correction, then report the blocker instead of looping.
7. Never use continuous deauthentication. Keep counts and timeouts minimal for the stated objective.
8. Track the monitor interface and restore it before finishing or after a failed run.
9. Before crack_handshake, verify the capture and resolve the wordlist. Use an explicit WORDLIST path when supplied. For WORDLIST auto, call find_wordlists and choose a discovered uncompressed list; start with the smallest practical list to avoid wasting time. Never pass a .gz file to a cracking tool. If no usable list exists, report that setup-wordlists.sh is required.
10. Finish with a concise report: scope, actions, evidence, findings, selected wordlist, cleanup, and unresolved items.

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

exec mcphost \
  -m "ollama/$model" \
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
  --wordlist   Wordlist path, or auto to use AutoWIFI discovery (default: auto)

Allowed action names:
  monitor,scan,capture,pmkid,wps,deauth,wordlist,crack,verify,report,all

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
[[ "$scope" != *
  echo "Arguments must be single-line values." >&2
  exit 2
}

IFS=',' read -r -a requested_actions <<< "$allowed"
for action in "${requested_actions[@]}"; do
  action="${action//[[:space:]]/}"
  case "$action" in
    monitor|scan|capture|pmkid|wps|deauth|wordlist|crack|verify|report|all) ;;
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
ollama show "$model" >/dev/null 2>&1 || { echo "Model $model is not installed." >&2; exit 1; }

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null

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
  -m "ollama/$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature" \
  --prompt "$prompt" \
  --compact | tee "$report_file"
SH
chmod +x "$BIN_DIR/autowifi-auto"

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
command -v autowifi-mcp >/dev/null && echo "MCP server: installed" || {
  echo "MCP server: missing"
  exit 1
}
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

echo "Ready:"
echo "  Interactive: autowifi-ai"
echo "  Autonomous:  autowifi-auto --help"
echo "  Diagnostic:  autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."
\n'* && "$target" != *
  echo "Arguments must be single-line values." >&2
  exit 2
}

IFS=',' read -r -a requested_actions <<< "$allowed"
for action in "${requested_actions[@]}"; do
  action="${action//[[:space:]]/}"
  case "$action" in
    monitor|scan|capture|pmkid|wps|deauth|crack|verify|report|all) ;;
    *) echo "Unsupported action in --allow: $action" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
max_steps="${AUTOWIFI_AI_AUTO_STEPS:-18}"
max_tokens="${AUTOWIFI_AI_MAX_TOKENS:-256}"
temperature="${AUTOWIFI_AI_TEMPERATURE:-0.2}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
[[ "$max_steps" =~ ^[0-9]+$ ]] || { echo "Invalid step limit." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || { echo "Model $model is not installed." >&2; exit 1; }

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null

report_dir="$HOME/.local/share/autowifi/reports"
mkdir -p "$report_dir"
report_file="$report_dir/auto-$(date +%Y%m%d-%H%M%S).txt"

prompt=$(printf '%s\n' \
  "Run one autonomous authorized AutoWIFI assessment." \
  "AUTHORIZED SCOPE: $scope" \
  "EXACT TARGET: $target" \
  "OBJECTIVE: $objective" \
  "ALLOWED ACTIONS: $allowed" \
  "Treat these values as hard boundaries. Start with preflight, complete the objective using only allowed actions, restore monitor mode, and provide the final report.")

echo "Starting bounded autonomous run. Report: $report_file"
mcphost \
  -m "ollama/$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature" \
  --prompt "$prompt" \
  --compact | tee "$report_file"
SH
chmod +x "$BIN_DIR/autowifi-auto"

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
command -v autowifi-mcp >/dev/null && echo "MCP server: installed" || {
  echo "MCP server: missing"
  exit 1
}
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

echo "Ready:"
echo "  Interactive: autowifi-ai"
echo "  Autonomous:  autowifi-auto --help"
echo "  Diagnostic:  autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."
\n'* && "$objective" != *
  echo "Arguments must be single-line values." >&2
  exit 2
}

IFS=',' read -r -a requested_actions <<< "$allowed"
for action in "${requested_actions[@]}"; do
  action="${action//[[:space:]]/}"
  case "$action" in
    monitor|scan|capture|pmkid|wps|deauth|crack|verify|report|all) ;;
    *) echo "Unsupported action in --allow: $action" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
max_steps="${AUTOWIFI_AI_AUTO_STEPS:-18}"
max_tokens="${AUTOWIFI_AI_MAX_TOKENS:-256}"
temperature="${AUTOWIFI_AI_TEMPERATURE:-0.2}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
[[ "$max_steps" =~ ^[0-9]+$ ]] || { echo "Invalid step limit." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || { echo "Model $model is not installed." >&2; exit 1; }

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null

report_dir="$HOME/.local/share/autowifi/reports"
mkdir -p "$report_dir"
report_file="$report_dir/auto-$(date +%Y%m%d-%H%M%S).txt"

prompt=$(printf '%s\n' \
  "Run one autonomous authorized AutoWIFI assessment." \
  "AUTHORIZED SCOPE: $scope" \
  "EXACT TARGET: $target" \
  "OBJECTIVE: $objective" \
  "ALLOWED ACTIONS: $allowed" \
  "Treat these values as hard boundaries. Start with preflight, complete the objective using only allowed actions, restore monitor mode, and provide the final report.")

echo "Starting bounded autonomous run. Report: $report_file"
mcphost \
  -m "ollama/$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature" \
  --prompt "$prompt" \
  --compact | tee "$report_file"
SH
chmod +x "$BIN_DIR/autowifi-auto"

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
command -v autowifi-mcp >/dev/null && echo "MCP server: installed" || {
  echo "MCP server: missing"
  exit 1
}
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

echo "Ready:"
echo "  Interactive: autowifi-ai"
echo "  Autonomous:  autowifi-auto --help"
echo "  Diagnostic:  autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."
\n'* && "$allowed" != *
  echo "Arguments must be single-line values." >&2
  exit 2
}

IFS=',' read -r -a requested_actions <<< "$allowed"
for action in "${requested_actions[@]}"; do
  action="${action//[[:space:]]/}"
  case "$action" in
    monitor|scan|capture|pmkid|wps|deauth|crack|verify|report|all) ;;
    *) echo "Unsupported action in --allow: $action" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
max_steps="${AUTOWIFI_AI_AUTO_STEPS:-18}"
max_tokens="${AUTOWIFI_AI_MAX_TOKENS:-256}"
temperature="${AUTOWIFI_AI_TEMPERATURE:-0.2}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
[[ "$max_steps" =~ ^[0-9]+$ ]] || { echo "Invalid step limit." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || { echo "Model $model is not installed." >&2; exit 1; }

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null

report_dir="$HOME/.local/share/autowifi/reports"
mkdir -p "$report_dir"
report_file="$report_dir/auto-$(date +%Y%m%d-%H%M%S).txt"

prompt=$(printf '%s\n' \
  "Run one autonomous authorized AutoWIFI assessment." \
  "AUTHORIZED SCOPE: $scope" \
  "EXACT TARGET: $target" \
  "OBJECTIVE: $objective" \
  "ALLOWED ACTIONS: $allowed" \
  "Treat these values as hard boundaries. Start with preflight, complete the objective using only allowed actions, restore monitor mode, and provide the final report.")

echo "Starting bounded autonomous run. Report: $report_file"
mcphost \
  -m "ollama/$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature" \
  --prompt "$prompt" \
  --compact | tee "$report_file"
SH
chmod +x "$BIN_DIR/autowifi-auto"

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
command -v autowifi-mcp >/dev/null && echo "MCP server: installed" || {
  echo "MCP server: missing"
  exit 1
}
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

echo "Ready:"
echo "  Interactive: autowifi-ai"
echo "  Autonomous:  autowifi-auto --help"
echo "  Diagnostic:  autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."
\n'* && "$wordlist" != *
  echo "Arguments must be single-line values." >&2
  exit 2
}

IFS=',' read -r -a requested_actions <<< "$allowed"
for action in "${requested_actions[@]}"; do
  action="${action//[[:space:]]/}"
  case "$action" in
    monitor|scan|capture|pmkid|wps|deauth|crack|verify|report|all) ;;
    *) echo "Unsupported action in --allow: $action" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
max_steps="${AUTOWIFI_AI_AUTO_STEPS:-18}"
max_tokens="${AUTOWIFI_AI_MAX_TOKENS:-256}"
temperature="${AUTOWIFI_AI_TEMPERATURE:-0.2}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
[[ "$max_steps" =~ ^[0-9]+$ ]] || { echo "Invalid step limit." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || { echo "Model $model is not installed." >&2; exit 1; }

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null

report_dir="$HOME/.local/share/autowifi/reports"
mkdir -p "$report_dir"
report_file="$report_dir/auto-$(date +%Y%m%d-%H%M%S).txt"

prompt=$(printf '%s\n' \
  "Run one autonomous authorized AutoWIFI assessment." \
  "AUTHORIZED SCOPE: $scope" \
  "EXACT TARGET: $target" \
  "OBJECTIVE: $objective" \
  "ALLOWED ACTIONS: $allowed" \
  "Treat these values as hard boundaries. Start with preflight, complete the objective using only allowed actions, restore monitor mode, and provide the final report.")

echo "Starting bounded autonomous run. Report: $report_file"
mcphost \
  -m "ollama/$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature" \
  --prompt "$prompt" \
  --compact | tee "$report_file"
SH
chmod +x "$BIN_DIR/autowifi-auto"

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
command -v autowifi-mcp >/dev/null && echo "MCP server: installed" || {
  echo "MCP server: missing"
  exit 1
}
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

echo "Ready:"
echo "  Interactive: autowifi-ai"
echo "  Autonomous:  autowifi-auto --help"
echo "  Diagnostic:  autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."
\n'* ]] || {
  echo "Arguments must be single-line values." >&2
  exit 2
}

IFS=',' read -r -a requested_actions <<< "$allowed"
for action in "${requested_actions[@]}"; do
  action="${action//[[:space:]]/}"
  case "$action" in
    monitor|scan|capture|pmkid|wps|deauth|crack|verify|report|all) ;;
    *) echo "Unsupported action in --allow: $action" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/go/bin:$PATH"
model="${AUTOWIFI_AI_MODEL:-autowifi-fast}"
max_steps="${AUTOWIFI_AI_AUTO_STEPS:-18}"
max_tokens="${AUTOWIFI_AI_MAX_TOKENS:-256}"
temperature="${AUTOWIFI_AI_TEMPERATURE:-0.2}"

[[ "$model" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "Invalid model name." >&2; exit 1; }
[[ "$max_steps" =~ ^[0-9]+$ ]] || { echo "Invalid step limit." >&2; exit 1; }
ollama show "$model" >/dev/null 2>&1 || { echo "Model $model is not installed." >&2; exit 1; }

echo "Warming $model..."
curl -fsS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$model\",\"keep_alive\":\"30m\"}" >/dev/null

report_dir="$HOME/.local/share/autowifi/reports"
mkdir -p "$report_dir"
report_file="$report_dir/auto-$(date +%Y%m%d-%H%M%S).txt"

prompt=$(printf '%s\n' \
  "Run one autonomous authorized AutoWIFI assessment." \
  "AUTHORIZED SCOPE: $scope" \
  "EXACT TARGET: $target" \
  "OBJECTIVE: $objective" \
  "ALLOWED ACTIONS: $allowed" \
  "Treat these values as hard boundaries. Start with preflight, complete the objective using only allowed actions, restore monitor mode, and provide the final report.")

echo "Starting bounded autonomous run. Report: $report_file"
mcphost \
  -m "ollama/$model" \
  --config "$HOME/.config/autowifi-kali-setup/mcphost.json" \
  --system-prompt "$HOME/.config/autowifi-kali-setup/autonomous-system.md" \
  --max-steps "$max_steps" \
  --max-tokens "$max_tokens" \
  --temperature "$temperature" \
  --prompt "$prompt" \
  --compact | tee "$report_file"
SH
chmod +x "$BIN_DIR/autowifi-auto"

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
command -v autowifi-mcp >/dev/null && echo "MCP server: installed" || {
  echo "MCP server: missing"
  exit 1
}
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

echo "Ready:"
echo "  Interactive: autowifi-ai"
echo "  Autonomous:  autowifi-auto --help"
echo "  Diagnostic:  autowifi-ai-doctor"
echo "Use only on networks you own or have explicit permission to test."

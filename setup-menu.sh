#!/usr/bin/env bash
# Quick local-user installer for the simple AutoWIFI menu.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/autowifi-kali-setup"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this as your normal Kali user, not root." >&2
  exit 2
fi
if [[ ! -f "$SCRIPT_DIR/extensions/simple_menu.py" ]]; then
  echo "Menu file missing. Run git pull from ~/autowifi-kali-setup." >&2
  exit 2
fi
mkdir -p "$CONFIG_DIR" "$BIN_DIR"
install -m 0644 "$SCRIPT_DIR/extensions/simple_menu.py" "$CONFIG_DIR/simple_menu.py"
# Refresh existing AI console's command routing without rebuilding Ollama.
if [[ -f "$SCRIPT_DIR/extensions/autowifi_operator.py" ]]; then
  install -m 0644 "$SCRIPT_DIR/extensions/autowifi_operator.py" "$CONFIG_DIR/autowifi_operator.py"
fi
cat > "$BIN_DIR/autowifi-menu" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 "$HOME/.config/autowifi-kali-setup/simple_menu.py" "$@"
SH
chmod 0755 "$BIN_DIR/autowifi-menu"
# A shorter launch command, without modifying system /usr/bin/ paths.
cat > "$BIN_DIR/wifi" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec "$HOME/.local/bin/autowifi-menu" "$@"
SH
chmod 0755 "$BIN_DIR/wifi"

echo
echo "Installed AutoWIFI simple menu."
echo "Run: $BIN_DIR/wifi"
echo "Or:  $BIN_DIR/autowifi-menu"
echo "Menu choices: home devices, Ralink Wi-Fi survey, antenna status, DSTIKE, AI chat."

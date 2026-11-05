#!/usr/bin/env bash
# Install the optional read-only hardware status bridge for the local user.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$HOME/.config/autowifi-kali-setup"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$CONFIG_DIR" "$BIN_DIR"
install -m 0644 "$SCRIPT_DIR/extensions/hardware_bridge.py" "$CONFIG_DIR/hardware_bridge.py"

cat > "$BIN_DIR/autowifi-hardware" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 "$HOME/.config/autowifi-kali-setup/hardware_bridge.py" "$@"
SH
chmod 0755 "$BIN_DIR/autowifi-hardware"

echo "Installed autowifi-hardware (read-only device status)."
if ! /usr/bin/python3 -c 'import serial' 2>/dev/null; then
  echo "Optional Flipper USB support: sudo apt install python3-serial"
fi
echo "Try: $BIN_DIR/autowifi-hardware discover"

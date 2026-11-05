#!/usr/bin/env bash
# One-command install and non-transmitting diagnostic for DSTIKE ESP8266 Deauther.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/autowifi-kali-setup"

if [[ "$(id -u)" -eq 0 ]]; then
    echo "Run as your normal Kali user, not root. The script uses sudo for apt only." >&2
    exit 2
fi
if [[ ! -f "$SCRIPT_DIR/extensions/deauther_bridge.py" ]]; then
    echo "Missing extension. Run git pull inside ~/autowifi-kali-setup first." >&2
    exit 2
fi

echo "[1/3] Installing USB and Python serial support (no system upgrade)..."
sudo apt-get install -y python3-serial usbutils

echo "[2/3] Installing the read-only deauther command..."
mkdir -p "$BIN_DIR" "$CONFIG_DIR"
install -m 0644 "$SCRIPT_DIR/extensions/deauther_bridge.py" "$CONFIG_DIR/deauther_bridge.py"
# Refresh natural-language routing without rebuilding the entire AI stack.
if [[ -f "$SCRIPT_DIR/extensions/autowifi_operator.py" ]]; then
    install -m 0644 "$SCRIPT_DIR/extensions/autowifi_operator.py" "$CONFIG_DIR/autowifi_operator.py"
    echo "Refreshed AutoWIFI console routing."
fi
cat > "$BIN_DIR/autowifi-deauther" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 "$HOME/.config/autowifi-kali-setup/deauther_bridge.py" "$@"
SH
chmod 0755 "$BIN_DIR/autowifi-deauther"

echo "[3/3] Checking the connected deauther (no Wi-Fi transmissions)..."
if "$BIN_DIR/autowifi-deauther" status; then
    echo
    echo "Success: Spacehuhn Deauther firmware responded over USB."
else
    echo
    echo "Installed successfully, but firmware communication is not confirmed yet."
    echo "Plug your DSTIKE into a USB DATA port and retry:"
    echo "  $BIN_DIR/autowifi-deauther status"
    echo "If multiple USB serial ports exist, use: autowifi-deauther status --port /dev/ttyUSB0"
fi
echo
echo "Read cached APs:  $BIN_DIR/autowifi-deauther cached-aps"
echo "Read serial logs: $BIN_DIR/autowifi-deauther listen --seconds 30 --record"
echo "Console routing refreshed (if autowifi-console was already installed)."
echo "Example: autowifi-console → deauther listen for 30 seconds record"

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

echo "[2/3] Refreshing AutoWIFI's live-source menu and DSTIKE hardware hub..."
# Use the shared installer; do not replace its live Git launchers with old copies.
bash "$SCRIPT_DIR/setup-menu.sh"

echo "[3/3] Checking DSTIKE USB serial detection (no Wi-Fi transmissions)..."
"$BIN_DIR/autowifi-deauther" detect || true
echo
echo "Hardware menu:  $BIN_DIR/wifi deauther"
echo "DSTIKE status: $BIN_DIR/wifi deauther-status"
echo "DSTIKE cache:  $BIN_DIR/wifi cached"
echo "Saved USB port: $BIN_DIR/wifi deauther-port"
echo "The ESP8266 and Ralink are separate radios."

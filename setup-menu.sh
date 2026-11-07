#!/usr/bin/env bash
# AutoWIFI local menu bootstrap. No sudo, no AI model rebuild.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/autowifi-kali-setup"
EXPECTED_REPO='BooMSainT/autowifi-kali-setup'

die() {
  echo "AutoWIFI setup error: $*" >&2
  exit 2
}

[[ "$(id -u)" -ne 0 ]] || die "Run as your normal Kali user, not root."
command -v git >/dev/null || die "git is missing."
[[ -f "$SCRIPT_DIR/.git" || -d "$SCRIPT_DIR/.git" ]] ||
  die "This is not a Git checkout. Clone https://github.com/$EXPECTED_REPO first."

branch="$(git -C "$SCRIPT_DIR" symbolic-ref --short -q HEAD || true)"
remote="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
case "$remote" in
  *github.com:BooMSainT/autowifi-kali-setup|*github.com:BooMSainT/autowifi-kali-setup.git|\
  *github.com/BooMSainT/autowifi-kali-setup|*github.com/BooMSainT/autowifi-kali-setup.git)
    ;;
  *)
    die "Wrong Git origin: $remote. Expected https://github.com/$EXPECTED_REPO"
    ;;
esac
[[ "$branch" == "main" ]] ||
  die "Currently on Git branch '$branch', not 'main'. Run: git switch main && git pull --ff-only"

for file in simple_menu.py network_inventory.py lan_inventory.py bomb_simulation.py quick_scan.py autowifi_operator.py; do
  [[ -r "$SCRIPT_DIR/extensions/$file" ]] || die "Missing extensions/$file"
done
command -v python3 >/dev/null || die "Python 3 is missing."
[[ -x /usr/bin/python3 ]] || die "/usr/bin/python3 is missing."

# Syntax-check runtime Python before installing. Avoid creating __pycache__.
python3 - "$SCRIPT_DIR" <<'PY'
import ast
from pathlib import Path
import sys

root = Path(sys.argv[1]) / "extensions"
for name in ("simple_menu.py", "network_inventory.py", "lan_inventory.py", "bomb_simulation.py",
             "quick_scan.py", "autowifi_operator.py"):
    path = root / name
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("Verified: Python syntax of menu, scanner, simulation and console.")
PY

mkdir -p "$CONFIG_DIR" "$BIN_DIR"

# Compatibility with existing AutoWIFI direct-runner and console. These
# still use installed Python files, unlike the live menu launchers below.
install -m 0644 "$SCRIPT_DIR/extensions/quick_scan.py" "$CONFIG_DIR/quick_scan.py"
install -m 0644 "$SCRIPT_DIR/extensions/autowifi_operator.py" "$CONFIG_DIR/autowifi_operator.py"

# Live launchers: after bootstrapping once, git pull updates all three tools.
write_live_launcher() {
  local name="$1"
  local pyfile="$2"
  local quoted_file
  printf -v quoted_file '%q' "$SCRIPT_DIR/extensions/$pyfile"
  cat > "$BIN_DIR/$name" <<SH
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 $quoted_file "\$@"
SH
  chmod 0755 "$BIN_DIR/$name"
}
write_live_launcher autowifi-menu simple_menu.py
write_live_launcher autowifi-inventory network_inventory.py
write_live_launcher autowifi-lan lan_inventory.py
write_live_launcher autowifi-bomb bomb_simulation.py

cat > "$BIN_DIR/wifi" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec "$HOME/.local/bin/autowifi-menu" "$@"
SH
chmod 0755 "$BIN_DIR/wifi"

echo
echo "Checking installed launchers (no hardware or Wi-Fi activity):"
"$BIN_DIR/wifi" --version
"$BIN_DIR/autowifi-inventory" --help >/dev/null
"$BIN_DIR/autowifi-lan" --help >/dev/null
"$BIN_DIR/autowifi-bomb" --help >/dev/null
echo "  Menu, Wi-Fi, LAN and BOMB launchers: PASS"
revision="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || true)"
echo
echo "Repository: $EXPECTED_REPO"
echo "Branch: main   Commit: $revision"
echo "Running directly from: $SCRIPT_DIR/extensions/"
echo "Configuration remains local: $CONFIG_DIR"
echo "Future updates: git pull --ff-only (then run wifi)"
echo
echo "Launch: $BIN_DIR/wifi"
echo "Network scan: $BIN_DIR/wifi scan"
echo "Adapter checks: $BIN_DIR/wifi doctor"
echo "Detailed home devices: $BIN_DIR/wifi devices"
echo "Version/source check: $BIN_DIR/wifi --version"
echo "If 'wifi' launches another program, use the full path shown above."

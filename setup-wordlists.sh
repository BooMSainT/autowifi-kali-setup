#!/usr/bin/env bash
set -euo pipefail
umask 077

DEST_DIR="$HOME/.local/share/autowifi/wordlists"
DEST_FILE="$DEST_DIR/rockyou.txt"
PLAIN_SOURCE="/usr/share/wordlists/rockyou.txt"
GZIP_SOURCE="/usr/share/wordlists/rockyou.txt.gz"

command -v apt-get >/dev/null || {
  echo "This helper requires Kali or another apt-based system." >&2
  exit 1
}

echo "Installing Kali's wordlists package..."
sudo apt-get update
sudo apt-get install -y wordlists gzip

mkdir -p "$DEST_DIR"
tmp_file=$(mktemp "$DEST_DIR/.rockyou.XXXXXX")
trap 'rm -f "$tmp_file"' EXIT

if [[ -f "$PLAIN_SOURCE" ]]; then
  cat "$PLAIN_SOURCE" > "$tmp_file"
elif [[ -f "$GZIP_SOURCE" ]]; then
  gzip -dc "$GZIP_SOURCE" > "$tmp_file"
else
  echo "The wordlists package did not provide rockyou.txt or rockyou.txt.gz." >&2
  exit 1
fi

[[ -s "$tmp_file" ]] || {
  echo "Prepared wordlist is empty." >&2
  exit 1
}

chmod 600 "$tmp_file"
mv "$tmp_file" "$DEST_FILE"
trap - EXIT

echo "Wordlist ready: $DEST_FILE"
echo "Entries: $(wc -l < "$DEST_FILE")"
echo "Size: $(du -h "$DEST_FILE" | cut -f1)"
echo "Run bash setup.sh again if AutoWIFI was installed before wordlist support was added."

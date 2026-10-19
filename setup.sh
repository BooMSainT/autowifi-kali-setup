#!/usr/bin/env bash
set -euo pipefail
PROJECT="$HOME/AutoWIFI"
if [[ ! -d "$PROJECT" ]]; then
  printf 'Missing AutoWIFI checkout at %s\n' "$PROJECT" >&2
  exit 1
fi
if ! command -v pipx >/dev/null 2>&1; then
  echo 'pipx not found. Install with: sudo apt install pipx' >&2
  exit 1
fi
if [[ ! -w "$PROJECT" ]]; then
  printf 'You do not own/write %s. Fix ownership? [y/N] ' "$PROJECT"
  read -r answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    sudo chown -R "$(id -un):$(id -gn)" "$PROJECT"
  else
    echo 'Stopped without changing permissions.' >&2
    exit 1
  fi
fi
cd "$PROJECT"
pipx install --force '.[mcp]'
pipx list
if command -v autowifi-mcp >/dev/null 2>&1; then
  command -v autowifi-mcp
else
  echo 'No autowifi-mcp command found; check package entry points.'
fi

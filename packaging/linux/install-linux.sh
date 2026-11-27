#!/usr/bin/env bash
# GPUHive Linux installer. Ships inside the release tarball next to the gridgpu binary.
# Copies gridgpu to /usr/local/bin so donors can run it from any terminal.
#
# Usage:  ./install-linux.sh
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BINARY="$SCRIPT_DIR/gridgpu"
DEST="/usr/local/bin/gridgpu"

if [[ ! -f "$BINARY" ]]; then
  echo "error: gridgpu binary not found next to this script." >&2
  exit 1
fi

if [[ -w "$(dirname "$DEST")" ]]; then
  install -m 755 "$BINARY" "$DEST"
else
  echo "Installing to $DEST (requires sudo)..."
  sudo install -m 755 "$BINARY" "$DEST"
fi

echo "Installed GPUHive to $DEST"
echo "Next: run 'gridgpu join' to share this GPU."

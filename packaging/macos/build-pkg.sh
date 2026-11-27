#!/usr/bin/env bash
# Builds a macOS .pkg installer around the PyInstaller-built gridgpu binary.
# Installs gridgpu to /usr/local/bin. Unsigned (see README for the Gatekeeper step).
#
# Usage (from repo root):  packaging/macos/build-pkg.sh [version]
# Expects dist/gridgpu to exist (produced by: pyinstaller packaging/gridgpu.spec).
set -euo pipefail

VERSION="${1:-0.1.0}"
REPO_ROOT="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
BINARY="$REPO_ROOT/dist/gridgpu"
IDENTIFIER="com.gpuhive.gridgpu"

if [[ ! -f "$BINARY" ]]; then
  echo "error: $BINARY not found. Run 'pyinstaller packaging/gridgpu.spec' first." >&2
  exit 1
fi

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING/usr/local/bin"
cp "$BINARY" "$STAGING/usr/local/bin/gridgpu"
chmod 755 "$STAGING/usr/local/bin/gridgpu"

OUTPUT="$REPO_ROOT/dist/GPUHive-$VERSION.pkg"
pkgbuild \
  --root "$STAGING" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --install-location "/" \
  "$OUTPUT"

echo "Built $OUTPUT"

#!/usr/bin/env bash
set -euo pipefail
GRIDGPU_SOURCE_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
python3 -c 'import sys; assert sys.version_info >= (3, 11), "GridGPU requires Python 3.11 or newer"'
python3 -m venv --system-site-packages "$GRIDGPU_SOURCE_DIR/.venv-agent"
"$GRIDGPU_SOURCE_DIR/.venv-agent/bin/python" -m pip install "$GRIDGPU_SOURCE_DIR"
exec "$GRIDGPU_SOURCE_DIR/.venv-agent/bin/python" -m gridgpu join "$@"

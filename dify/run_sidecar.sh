#!/usr/bin/env bash
# Start the stdlib-only sidecar. No third-party Python packages are required.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 sidecar/server.py \
  --port "${SIDECAR_PORT:-8787}" \
  --powercontext-url "${POWERCONTEXT_URL:-http://127.0.0.1:18080}"

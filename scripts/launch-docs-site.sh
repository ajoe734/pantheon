#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-4173}"

bash "$ROOT_DIR/scripts/sync-state.sh" >/dev/null
cd "$ROOT_DIR/docs-site"
echo "Serving dashboard at http://127.0.0.1:${PORT}/index.html"
exec python3 -m http.server "$PORT" --bind 127.0.0.1

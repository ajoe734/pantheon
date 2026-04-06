#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-4173}"

bash "$ROOT_DIR/scripts/sync-state.sh" >/dev/null
exec python3 "$ROOT_DIR/scripts/dashboard_server.py" --host 127.0.0.1 --port "$PORT" --directory "$ROOT_DIR/docs-site"

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-4173}"
HOST="${HOST:-127.0.0.1}"

if lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  if curl -fsS "http://${HOST}:${PORT}/index.html" >/tmp/pantheon-dashboard-check.$$ 2>/dev/null; then
    if grep -q "協作看板" /tmp/pantheon-dashboard-check.$$; then
      rm -f /tmp/pantheon-dashboard-check.$$
      echo "Dashboard already running at http://${HOST}:${PORT}/index.html"
      exit 0
    fi
  fi
  rm -f /tmp/pantheon-dashboard-check.$$ || true
  echo "Port ${PORT} is already in use by another process. Set PORT=<new-port> and retry." >&2
  exit 1
fi

source "$ROOT_DIR/scripts/canonical-task-state-binding.sh"
if bind_canonical_task_state "$ROOT_DIR/.orchestrator"; then STATUS_ROOT="$PANTHEON_STATUS_ROOT"; else STATUS_ROOT="$ROOT_DIR"; fi
unset PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON PANTHEON_STATUS_ROOT PANTHEON_TASK_STATE_EVENT_LOG PANTHEON_TASK_STATE_STORE_MODE

echo "Dashboard available at http://${HOST}:${PORT}/index.html"
exec python3 "$ROOT_DIR/scripts/dashboard_server.py" --host "$HOST" --port "$PORT" --directory "$ROOT_DIR/docs-site" --repo-root "$STATUS_ROOT"

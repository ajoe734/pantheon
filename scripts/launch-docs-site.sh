#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-4173}"
HOST="${HOST:-127.0.0.1}"
SYNC_TIMEOUT_SECONDS="${PANTHEON_DASHBOARD_SYNC_TIMEOUT_SECONDS:-45}"
LOG_DIR="${ROOT_DIR}/.orchestrator/logs"
SYNC_LOG="${LOG_DIR}/dashboard-sync.log"

mkdir -p "${LOG_DIR}"

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

# Keep the dashboard available even when state sync is slow or temporarily noisy.
# Serve immediately from the last successful snapshot and refresh state in the background.
(
  if ! timeout --foreground "${SYNC_TIMEOUT_SECONDS}" bash "$ROOT_DIR/scripts/sync-state.sh" >"${SYNC_LOG}" 2>&1; then
    {
      echo "Warning: sync-state.sh failed or timed out after ${SYNC_TIMEOUT_SECONDS}s; serving the last synced dashboard snapshot."
      tail -n 20 "${SYNC_LOG}" || true
    } >>"${SYNC_LOG}" 2>&1
  fi
) &
echo "Dashboard available at http://${HOST}:${PORT}/index.html"
exec python3 "$ROOT_DIR/scripts/dashboard_server.py" --host "$HOST" --port "$PORT" --directory "$ROOT_DIR/docs-site"

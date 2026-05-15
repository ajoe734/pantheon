#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-4173}"
RESTART_DELAY="${PANTHEON_DASHBOARD_RESTART_DELAY_SECONDS:-2}"

dashboard_ready() {
  local tmp
  tmp="$(mktemp)"
  if curl -fsS "http://${HOST}:${PORT}/index.html" >"${tmp}" 2>/dev/null && grep -q "協作看板" "${tmp}"; then
    rm -f "${tmp}"
    return 0
  fi
  rm -f "${tmp}"
  return 1
}

if dashboard_ready; then
  echo "Dashboard already running at http://${HOST}:${PORT}/index.html"
  exit 0
fi

while true; do
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting dashboard launcher"
  if bash "${ROOT_DIR}/scripts/launch-docs-site.sh" "$@"; then
    if dashboard_ready; then
      exit 0
    fi
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] dashboard exited cleanly; restarting in ${RESTART_DELAY}s"
  else
    status=$?
    if dashboard_ready; then
      exit 0
    fi
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] dashboard launcher exited with status ${status}; restarting in ${RESTART_DELAY}s" >&2
  fi
  sleep "${RESTART_DELAY}"
done

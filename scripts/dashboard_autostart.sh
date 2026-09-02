#!/usr/bin/env bash
# Bring the orchestrator dashboard back after a reboot or a crash.
#
# The dashboard was the only piece of the box with no supervisor: docker has
# restart policies and the supervisor has its watchdog cron, but a reboot left
# the board down until someone opened it and found nothing. This probes the
# local server and restarts it (and the cloudflare quick tunnel) when it stops
# answering. Healthy is a no-op, so it is safe on a tight cron.
#
# Both halves run under tmux: a plain `nohup ... &` dies with the shell that
# launched it, which is how hand-restarts kept vanishing minutes later.
#
# Note: restarting the quick tunnel mints a NEW public trycloudflare URL. The
# current one is always in .orchestrator/logs/cloudflared-dashboard.url.
#
# Tunnel management defaults OFF: this cron-driven autostart must never open
# the dashboard to a public tunnel on its own. Set
# PANTHEON_DASHBOARD_MANAGE_TUNNEL=1 only after an explicit operator decision
# to publish.
set -euo pipefail

# PANTHEON_DASHBOARD_ROOT lets this run from a code root that is not the root it
# serves. Cron drives it from the auto-synced dev-root against the live status
# root, the same split the supervisor watchdog uses, so worker churn in the live
# tree cannot delete the guard. Without the override it manages its own checkout,
# which silently hijacks the live port when run from a worktree.
ROOT_DIR="${PANTHEON_DASHBOARD_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [[ ! -d "${ROOT_DIR}/scripts" ]]; then
  echo "PANTHEON_DASHBOARD_ROOT=${ROOT_DIR} is not a pantheon checkout" >&2
  exit 1
fi
cd "$ROOT_DIR"

PORT="${PANTHEON_DASHBOARD_PORT:-4180}"
HOST="${PANTHEON_DASHBOARD_HOST:-127.0.0.1}"
SERVER_SESSION="${PANTHEON_DASHBOARD_SERVER_SESSION:-pantheon-dashboard-server}"
TUNNEL_SESSION="${PANTHEON_DASHBOARD_TUNNEL_SESSION:-pantheon-dashboard-tunnel}"
MANAGE_TUNNEL="${PANTHEON_DASHBOARD_MANAGE_TUNNEL:-0}"
LOG_DIR="${ROOT_DIR}/.orchestrator/logs"
LOG_FILE="${LOG_DIR}/dashboard-autostart.log"
LOCK_FILE="${ROOT_DIR}/.orchestrator/dashboard-autostart.lock"
URL_FILE="${LOG_DIR}/cloudflared-dashboard.url"

mkdir -p "${LOG_DIR}"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "${LOG_FILE}"
}

# Single instance: a slow restart must not race the next cron tick.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  exit 0
fi

if ! command -v tmux >/dev/null 2>&1; then
  log "ERROR: tmux is not installed; cannot supervise the dashboard"
  exit 1
fi

server_healthy() {
  curl -fsS -o /dev/null --max-time 5 "http://${HOST}:${PORT}/" 2>/dev/null
}

tunnel_alive() {
  tmux has-session -t "${TUNNEL_SESSION}" 2>/dev/null
}

if ! server_healthy; then
  log "dashboard not answering on ${HOST}:${PORT}; starting"
  # A stale session with a dead server in it would block the new one.
  tmux kill-session -t "${SERVER_SESSION}" 2>/dev/null || true
  tmux new-session -d -s "${SERVER_SESSION}" \
    "cd '${ROOT_DIR}' && PORT='${PORT}' HOST='${HOST}' \
     bash '${ROOT_DIR}/scripts/run-dashboard.sh' >> '${LOG_DIR}/dashboard-run.log' 2>&1" \
    9>&-
  for _ in $(seq 1 10); do
    sleep 1
    if server_healthy; then
      log "dashboard is up on ${HOST}:${PORT}"
      break
    fi
  done
  if ! server_healthy; then
    log "ERROR: dashboard still not answering after restart; see dashboard-run.log"
    exit 1
  fi
fi

if [[ "${MANAGE_TUNNEL}" == "1" ]]; then
  if ! tunnel_alive; then
    if ! command -v cloudflared >/dev/null 2>&1; then
      log "tunnel session missing but cloudflared is not installed; serving locally only"
      exit 0
    fi
    log "tunnel session '${TUNNEL_SESSION}' missing; starting (public URL will change)"
    tmux new-session -d -s "${TUNNEL_SESSION}" \
      "cd '${ROOT_DIR}' && PANTHEON_DASHBOARD_TUNNEL_TARGET='http://${HOST}:${PORT}' \
       PANTHEON_DASHBOARD_TUNNEL_PUBLIC_PATH='/' \
       PANTHEON_DASHBOARD_TUNNEL_RESTART_DELAY_SECONDS='2' \
       bash '${ROOT_DIR}/scripts/dashboard_tunnel_keepalive.sh'" \
      9>&-
  fi

  url=""
  for _ in $(seq 1 20); do
    sleep 1
    if [[ -s "${URL_FILE}" ]]; then
      url="$(tr -d '\r\n' < "${URL_FILE}")"
    fi
    if [[ -n "${url}" ]]; then
      log "tunnel up: ${url}"
      break
    fi
  done
  if [[ -z "${url}" ]]; then
    log "ERROR: tunnel session is running but no current URL was captured; see cloudflared-dashboard.log"
    exit 1
  fi
fi

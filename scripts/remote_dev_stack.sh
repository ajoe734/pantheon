#!/usr/bin/env bash
set -euo pipefail

HOST="${PANTHEON_REMOTE_HOST:-pantheon-gcp}"
REMOTE_PATH="${PANTHEON_REMOTE_PATH:-/home/edna/code/pantheon}"
COMPOSE_FILE="${PANTHEON_REMOTE_COMPOSE_FILE:-docker-compose.remote-dev.yml}"
ACTION="${1:-status}"
COMPOSE_CMD="COMPOSE_BAKE=false docker compose -f '${COMPOSE_FILE}'"

case "${ACTION}" in
  up)
    REMOTE_CMD="cd '${REMOTE_PATH}' && ${COMPOSE_CMD} up -d --build"
    ;;
  down)
    REMOTE_CMD="cd '${REMOTE_PATH}' && ${COMPOSE_CMD} down"
    ;;
  restart)
    REMOTE_CMD="cd '${REMOTE_PATH}' && ${COMPOSE_CMD} down && ${COMPOSE_CMD} up -d --build"
    ;;
  logs)
    REMOTE_CMD="cd '${REMOTE_PATH}' && ${COMPOSE_CMD} logs --tail=200"
    ;;
  ps|status)
    REMOTE_CMD="cd '${REMOTE_PATH}' && ${COMPOSE_CMD} ps"
    ;;
  health)
    REMOTE_CMD="curl -fsS http://127.0.0.1:8001/health && printf '\n' && curl -fsS http://127.0.0.1:8002/health && printf '\n'"
    ;;
  *)
    echo "Usage: $0 [up|down|restart|logs|ps|status|health]" >&2
    exit 1
    ;;
esac

ssh "${HOST}" "${REMOTE_CMD}"

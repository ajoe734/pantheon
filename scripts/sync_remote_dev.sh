#!/usr/bin/env bash
set -euo pipefail

HOST="${PANTHEON_REMOTE_HOST:-pantheon-gcp}"
REMOTE_PATH="${PANTHEON_REMOTE_PATH:-/home/lupin/pantheon}"
MODE="${PANTHEON_REMOTE_SYNC_MODE:-code}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --dry-run|-n)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "Usage: $0 [--mode code|full-state] [--dry-run]" >&2
      exit 1
      ;;
  esac
done

case "${MODE}" in
  code|full-state)
    ;;
  *)
    echo "Unsupported mode: ${MODE}. Use 'code' or 'full-state'." >&2
    exit 1
    ;;
esac

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required but not installed." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RSYNC_ARGS=(
  -az
  --delete
  --exclude=.git/
  --exclude=.venv*/
  --exclude=node_modules/
  --exclude=__pycache__/
  --exclude=*.pyc
  --exclude=dist/
  --exclude=*.Zone.Identifier
  --exclude=.orchestrator/__pycache__/
  --exclude=.orchestrator/*.pid
  --exclude=.orchestrator/*.lock
)

if [[ "${MODE}" == "code" ]]; then
  RSYNC_ARGS+=(
    --exclude=.orchestrator/approval-queue.json
    --exclude=.orchestrator/github-bus-state.json
    --exclude=.orchestrator/provider_capabilities.json
    --exclude=.orchestrator/state.json
    --exclude=ai-activity-log.jsonl
    --exclude=ai-status.json
    --exclude=dashboard-bundle.json
    --exclude=docs-site/ai-activity-log.jsonl
    --exclude=docs-site/ai-status.json
    --exclude=docs-site/approval-queue.json
    --exclude=docs-site/current-work.md
    --exclude=docs-site/dashboard-bundle.json
    --exclude=docs-site/orchestrator-state.json
  )
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run --itemize-changes)
fi

echo "Syncing (${MODE}) ${ROOT_DIR} -> ${HOST}:${REMOTE_PATH}"

rsync "${RSYNC_ARGS[@]}" -e "ssh" "${ROOT_DIR}/" "${HOST}:${REMOTE_PATH}/"

ssh "${HOST}" "cd '${REMOTE_PATH}' && git submodule update --init --recursive >/dev/null && printf 'Remote HEAD=%s\n' \"\$(git rev-parse --short HEAD)\" && git status --short"

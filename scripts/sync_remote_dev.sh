#!/usr/bin/env bash
set -euo pipefail

HOST="${PANTHEON_REMOTE_HOST:-pantheon-gcp}"
REMOTE_PATH="${PANTHEON_REMOTE_PATH:-/home/edna/code/pantheon}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required but not installed." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Syncing ${ROOT_DIR} -> ${HOST}:${REMOTE_PATH}"

rsync -az --delete \
  --exclude='.git/' \
  --exclude='.venv*/' \
  --exclude='node_modules/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='dist/' \
  --exclude='*.Zone.Identifier' \
  --exclude='.orchestrator/__pycache__/' \
  --exclude='.orchestrator/approval-queue.json' \
  --exclude='.orchestrator/approval-queue.lock' \
  --exclude='.orchestrator/event-queue.jsonl' \
  --exclude='.orchestrator/github-bus-state.json' \
  --exclude='.orchestrator/provider_capabilities.json' \
  --exclude='.orchestrator/state.json' \
  --exclude='.orchestrator/supervisor.pid' \
  --exclude='ai-activity-log.jsonl' \
  --exclude='ai-status.json' \
  --exclude='dashboard-bundle.json' \
  --exclude='docs-site/ai-activity-log.jsonl' \
  --exclude='docs-site/ai-status.json' \
  --exclude='docs-site/approval-queue.json' \
  --exclude='docs-site/current-work.md' \
  --exclude='docs-site/dashboard-bundle.json' \
  --exclude='docs-site/orchestrator-state.json' \
  -e "ssh" \
  "${ROOT_DIR}/" "${HOST}:${REMOTE_PATH}/"

ssh "${HOST}" "cd '${REMOTE_PATH}' && git submodule update --init --recursive >/dev/null && printf 'Remote HEAD=%s\n' \"\$(git rev-parse --short HEAD)\" && git status --short"

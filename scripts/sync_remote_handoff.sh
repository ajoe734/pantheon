#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${PANTHEON_REMOTE_HOST:-pantheon-gcp}"
REMOTE_PATH="${PANTHEON_REMOTE_PATH:-/home/edna/code/pantheon}"
CURRENT_BRANCH="$(git -C "$ROOT_DIR" branch --show-current)"
BUNDLE_PATH="/tmp/pantheon-${CURRENT_BRANCH}-handoff.bundle"

cleanup() {
  rm -f "${BUNDLE_PATH}"
}
trap cleanup EXIT

git -C "$ROOT_DIR" bundle create "${BUNDLE_PATH}" "${CURRENT_BRANCH}"
scp "${BUNDLE_PATH}" "${HOST}:~/pantheon-handoff.bundle" >/dev/null

ssh "${HOST}" "cd '${REMOTE_PATH}' && \
  git fetch ~/pantheon-handoff.bundle '${CURRENT_BRANCH}:refs/remotes/localbundle/${CURRENT_BRANCH}' >/dev/null && \
  git update-ref 'refs/heads/${CURRENT_BRANCH}' 'refs/remotes/localbundle/${CURRENT_BRANCH}' && \
  git reset --mixed HEAD >/dev/null && \
  rm -f ~/pantheon-handoff.bundle"

bash "$ROOT_DIR/scripts/sync_remote_dev.sh" --mode full-state "$@"

ssh "${HOST}" "cd '${REMOTE_PATH}' && git reset --mixed HEAD >/dev/null && printf 'Remote HEAD=%s\n' \"\$(git rev-parse --short HEAD)\" && git status --short"

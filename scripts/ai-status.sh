#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${PANTHEON_STATUS_ROOT:-}" ]] && {
  [[ -n "${ORCH_RUN_ID:-}" ]] || [[ -n "${PANTHEON_WORKTREE_ROOT:-}" ]] || [[ -n "${ORCH_WORKSPACE_PATH:-}" ]]
}; then
  echo "PANTHEON_STATUS_ROOT is required for auto-worker status commands" >&2
  exit 2
fi
exec python3 "$ROOT_DIR/scripts/ai_status.py" "$@"

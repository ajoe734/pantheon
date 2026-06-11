#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$ROOT_DIR}"
MAX_TASKS="${AUTO_INTEGRATOR_MAX_TASKS:-1}"

cd "$ROOT_DIR"

ARGS=(
  --max-tasks "$MAX_TASKS"
  --status-file "$STATUS_ROOT/ai-status.json"
  --config-file "$STATUS_ROOT/.orchestrator/config.json"
)

if [[ "${AUTO_INTEGRATOR_DRY_RUN:-0}" != "1" ]]; then
  ARGS=(--execute "${ARGS[@]}")
fi

exec python3 "$ROOT_DIR/scripts/git/auto_integrator.py" "${ARGS[@]}" "$@"

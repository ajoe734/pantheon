#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$ROOT_DIR}"
MAX_TASKS="${AUTO_INTEGRATOR_MAX_TASKS:-1}"

cd "$ROOT_DIR"

ARGS=(
  --max-tasks "$MAX_TASKS"
)

if [[ "${AUTO_INTEGRATOR_DRY_RUN:-0}" != "1" ]]; then
  ARGS=(--execute "${ARGS[@]}")
  # Live execute authority binds to the promoted live-supervisor config, whose
  # per-repository entries carry the dedicated integration_path checkout.
  # Only override auto_integrator.py's own DEFAULT_LIVE_CONFIG when the
  # caller explicitly supplied one; the repo-committed
  # $STATUS_ROOT/.orchestrator/config.json template has no integration_path
  # and would silently merge against the shared dev-root checkout instead.
  if [[ -n "${PANTHEON_AUTO_INTEGRATOR_CONFIG:-}" ]]; then
    export PANTHEON_LIVE_SUPERVISOR_CONFIG="$PANTHEON_AUTO_INTEGRATOR_CONFIG"
  fi
else
  CONFIG_FILE="${PANTHEON_AUTO_INTEGRATOR_CONFIG:-$STATUS_ROOT/.orchestrator/config.json}"
  ARGS+=(
    --status-file "$STATUS_ROOT/ai-status.json"
    --config-file "$CONFIG_FILE"
  )
fi

exec python3 "$ROOT_DIR/scripts/git/auto_integrator.py" "${ARGS[@]}" "$@"

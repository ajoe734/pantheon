#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AI_NAME="Human/Ops"
export PANTHEON_LOCAL_HUMAN_OPS=1

# The local operator command is an ingress to the same live TaskStore as the
# supervisor, not a second root selected by shell variables.  Keep the live
# config path overrideable for isolated tests, but derive the expected binding
# once and let ai-status reject any supplied root/journal that disagrees.
LIVE_CONFIG="${PANTHEON_LIVE_SUPERVISOR_CONFIG:-/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json}"
if [[ -f "$LIVE_CONFIG" && -d "$SCRIPT_DIR/../.orchestrator" ]]; then
  mapfile -t canonical_binding < <(
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SCRIPT_DIR/../.orchestrator" \
      python3 - "$LIVE_CONFIG" <<'PY'
import json
import sys
from common import canonical_task_state_identity

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
identity = canonical_task_state_identity(config)
print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
print(identity["status_root"])
print(identity["event_log"])
PY
  )
  [[ "${#canonical_binding[@]}" -eq 3 ]] \
    || { echo "Failed to derive canonical Human/Ops task-state binding" >&2; exit 1; }
  export PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON="${canonical_binding[0]}"
  export PANTHEON_STATUS_ROOT="${canonical_binding[1]}"
  export PANTHEON_TASK_STATE_EVENT_LOG="${canonical_binding[2]}"
  export PANTHEON_TASK_STATE_STORE_MODE="authoritative"
fi

exec "$SCRIPT_DIR/ai-status.sh" "$@"

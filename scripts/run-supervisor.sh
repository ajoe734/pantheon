#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Guard: --poll-interval without --allow-fast-poll is the recurring footgun.
# Supervisor cycle time scales with worker-log size; sub-config polling
# manufactures lag and looks like the supervisor hanging. Force callers to be
# explicit. (See OPS-SUPERVISOR-POLL-GUARD-001.)
saw_poll=0
saw_allow=0
for arg in "$@"; do
  case "$arg" in
    --poll-interval|--poll-interval=*) saw_poll=1 ;;
    --allow-fast-poll) saw_allow=1 ;;
  esac
done
if [[ $saw_poll -eq 1 && $saw_allow -eq 0 ]]; then
  echo "run-supervisor.sh: --poll-interval requires --allow-fast-poll." >&2
  echo "Edit .orchestrator/config.json supervisor.poll_interval_seconds for steady state," >&2
  echo "or pass --allow-fast-poll for ad-hoc incident debugging." >&2
  exit 2
fi

exec python3 "$ROOT_DIR/.orchestrator/supervisor.py" "$@"

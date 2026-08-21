#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ai_status.py's canonical mutation lease check rejects every command
# (including this no-op "sync") unless it sees either an active worker
# lease (ORCH_RUN_ID) or this explicit local Human/Ops opt-in. Every
# caller of this wrapper (dashboard refresh, dashboard launch sync,
# release_hardening.py) is an operator/dashboard context, never a real
# worker -- and local_human_ops_requested() already defers to ORCH_RUN_ID
# when one is set, so exporting this here is safe even if a future
# caller does run inside a worker lease.
export PANTHEON_LOCAL_HUMAN_OPS="${PANTHEON_LOCAL_HUMAN_OPS:-1}"
exec python3 "$ROOT_DIR/scripts/ai_status.py" sync "$@"

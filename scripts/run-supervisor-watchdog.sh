#!/usr/bin/env bash
set -euo pipefail

# Keep the immutable command runtime clean both while loading the watchdog and
# when the watchdog launches or restarts the supervisor and its descendants.
export PYTHONDONTWRITEBYTECODE=1

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

exec python3 -B "$ROOT_DIR/.orchestrator/supervisor_watchdog.py" "$@"

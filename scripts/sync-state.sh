#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$ROOT_DIR/scripts/planning_state.py" sync
exec python3 "$ROOT_DIR/scripts/ai_status.py" sync "$@"

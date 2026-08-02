#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

exec python3 "$ROOT_DIR/scripts/promote_supervisor_runtime.py" \
  --discover-only "$@"

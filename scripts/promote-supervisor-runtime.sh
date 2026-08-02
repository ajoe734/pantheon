#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

for argument in "$@"; do
  if [[ "$argument" == "--promote" ]]; then
    exec python3 "$ROOT_DIR/scripts/promote_supervisor_runtime.py" "$@"
  fi
done

exec python3 "$ROOT_DIR/scripts/promote_supervisor_runtime.py" --discover-only "$@"

#!/usr/bin/env bash
set -euo pipefail

# Keep the immutable command runtime clean both while loading the watchdog and
# when the watchdog launches or restarts the supervisor and its descendants.
export PYTHONDONTWRITEBYTECODE=1

AUTHORITY_ENV_FILE="${PANTHEON_SUPERVISOR_VERIFIER_ENV_FILE:-}"
if [[ -n "$AUTHORITY_ENV_FILE" ]]; then
  [[ "$AUTHORITY_ENV_FILE" == /* && -f "$AUTHORITY_ENV_FILE" && ! -L "$AUTHORITY_ENV_FILE" ]] || {
    echo "invalid supervisor verifier environment file" >&2
    exit 2
  }
  [[ "$(stat -c '%a' "$AUTHORITY_ENV_FILE")" == "600" ]] || {
    echo "supervisor verifier environment file must have mode 600" >&2
    exit 2
  }
  set -a
  # The deploy controller atomically owns this fixed mode-600 file. It contains
  # public verifier maps only; private signing authority is never sourced here.
  source "$AUTHORITY_ENV_FILE"
  set +a
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

exec python3 -B "$ROOT_DIR/.orchestrator/supervisor_watchdog.py" "$@"

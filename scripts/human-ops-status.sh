#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AI_NAME="Human/Ops"
export PANTHEON_LOCAL_HUMAN_OPS=1

exec "$SCRIPT_DIR/ai-status.sh" "$@"

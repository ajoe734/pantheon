#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${OPENCLAW_GATEWAY_SMOKE_WORK_DIR:-$(mktemp -d /tmp/openclaw-bp5-oss-002.XXXXXX)}"

echo "Working directory: $WORK_DIR"
python3 "$REPO_ROOT/services/control-plane/cron/smoke_test.py" \
  --mode live \
  --manage-runtime \
  --work-dir "$WORK_DIR" \
  "$@"
echo "Artifacts: $WORK_DIR"

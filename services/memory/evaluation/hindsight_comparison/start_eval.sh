#!/usr/bin/env bash
# Refuses to start below the free-disk floor (GB, default 10).
set -euo pipefail
cd "$(dirname "$0")"
MIN_GB="${HINDSIGHT_EVAL_MIN_FREE_GB:-10}"
FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if [ "$FREE_GB" -lt "$MIN_GB" ]; then
  echo "refusing to start: ${FREE_GB}GB free < ${MIN_GB}GB" >&2; exit 2
fi
: "${HINDSIGHT_EVAL_PG_PASSWORD:?set HINDSIGHT_EVAL_PG_PASSWORD}"
exec docker compose -f docker-compose.eval.yml up -d --build "$@"

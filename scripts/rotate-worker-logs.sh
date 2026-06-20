#!/usr/bin/env bash
# rotate-worker-logs.sh — keep .orchestrator/logs/ bounded.
#
# Policy (overridable via env):
#   gzip plain *.log older than ROTATE_GZIP_DAYS   (default 2)
#   delete *.log / *.gz   older than ROTATE_DELETE_DAYS (default 14)
#
# Idempotent and safe to run hourly from cron. Files modified within the gzip
# window (i.e. active worker logs) and the cron log this run appends to are left
# untouched. Output line is stable so the cron log stays grep-able:
#   rotate-worker-logs: gzipped=<n> deleted=<m> (gzip>2d delete>14d) dir=<path>
set -uo pipefail

GZIP_DAYS="${ROTATE_GZIP_DAYS:-2}"
DELETE_DAYS="${ROTATE_DELETE_DAYS:-14}"

# Resolve the log dir relative to this script (scripts/../.orchestrator/logs) so
# it works regardless of the cron caller's working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${ROTATE_LOG_DIR:-$SCRIPT_DIR/../.orchestrator/logs}"

if [ ! -d "$LOG_DIR" ]; then
  echo "rotate-worker-logs: log dir not found: $LOG_DIR"
  exit 0
fi
LOG_DIR="$(cd "$LOG_DIR" && pwd)"

gzipped=0
deleted=0

# gzip aged plain .log files (skip the cron log this run writes to).
while IFS= read -r -d '' f; do
  case "$f" in
    *rotate-worker-logs-cron.log) continue ;;
  esac
  if gzip -- "$f" 2>/dev/null; then
    gzipped=$((gzipped + 1))
  fi
done < <(find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -mtime +"$GZIP_DAYS" -print0 2>/dev/null)

# delete logs (plain or gzipped) past the retention window.
while IFS= read -r -d '' f; do
  case "$f" in
    *rotate-worker-logs-cron.log) continue ;;
  esac
  if rm -f -- "$f" 2>/dev/null; then
    deleted=$((deleted + 1))
  fi
done < <(find "$LOG_DIR" -maxdepth 1 -type f \( -name '*.log' -o -name '*.gz' \) -mtime +"$DELETE_DAYS" -print0 2>/dev/null)

echo "rotate-worker-logs: gzipped=$gzipped deleted=$deleted (gzip>${GZIP_DAYS}d delete>${DELETE_DAYS}d) dir=$LOG_DIR"

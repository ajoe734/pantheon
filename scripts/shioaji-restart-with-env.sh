#!/usr/bin/env bash
# One-shot supervisor restart with Shioaji sandbox credentials loaded.
#
# Scheduled to fire ~07:55 Asia/Taipei (= 23:55 UTC the prior day) so the
# supervisor warmup completes before Shioaji sandbox window opens at 08:00 TW.
# The cron entry self-removes after successful run.
#
# Manual invocation: bash scripts/shioaji-restart-with-env.sh
set -euo pipefail

REPO="/home/lupin/code/pantheon"
ENV_FILE="$REPO/env/.env.shioaji"
LOG_DIR="$REPO/.orchestrator/logs"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOG_DIR/supervisor-restart-shioaji-${TS}.log"
RUN_LOG="$LOG_DIR/shioaji-restart-runner-${TS}.log"

exec > >(tee -a "$RUN_LOG") 2>&1
echo "=== shioaji-restart-with-env starting at $(date -u +%FT%TZ) ==="
echo "TW time: $(TZ=Asia/Taipei date '+%F %T %Z')"

cd "$REPO"

if [[ ! -r "$ENV_FILE" ]]; then
  echo "FATAL: $ENV_FILE not readable. Aborting restart."
  exit 1
fi

# 1. Source the gitignored env file
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${BROKER_SHIOAJI_API_KEY:-}" || -z "${BROKER_SHIOAJI_SECRET_KEY:-}" ]]; then
  echo "FATAL: BROKER_SHIOAJI_API_KEY / BROKER_SHIOAJI_SECRET_KEY missing after source."
  exit 1
fi
echo "env loaded: sandbox_enabled=${BROKER_SHIOAJI_SANDBOX_ENABLED:-} api_key_len=${#BROKER_SHIOAJI_API_KEY} secret_key_len=${#BROKER_SHIOAJI_SECRET_KEY}"

# 2. Stop old supervisor (graceful, then force)
OLD_PIDS=$(pgrep -f ".orchestrator/supervisor.py" || true)
if [[ -n "$OLD_PIDS" ]]; then
  for pid in $OLD_PIDS; do
    echo "SIGTERM old supervisor PID $pid"
    kill -TERM "$pid" 2>/dev/null || true
  done
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    REMAINING=$(pgrep -f ".orchestrator/supervisor.py" || true)
    if [[ -z "$REMAINING" ]]; then break; fi
  done
  REMAINING=$(pgrep -f ".orchestrator/supervisor.py" || true)
  if [[ -n "$REMAINING" ]]; then
    for pid in $REMAINING; do
      echo "SIGKILL stubborn supervisor PID $pid"
      kill -KILL "$pid" 2>/dev/null || true
    done
  fi
else
  echo "no existing supervisor process found"
fi

# 3. Start new supervisor detached with env inherited
echo "launching new supervisor → $LOG"
setsid nohup python3 -u "$REPO/.orchestrator/supervisor.py" --verbose > "$LOG" 2>&1 < /dev/null &
NEW_PID=$!
disown
echo "new supervisor PID $NEW_PID"

# 4. Verify it came up
sleep 5
if ! kill -0 "$NEW_PID" 2>/dev/null; then
  echo "FATAL: new supervisor died within 5 seconds. See $LOG"
  exit 2
fi
echo "supervisor heartbeat ok at $(date -u +%FT%TZ)"

# 5. Self-remove cron entry that triggered this run
CRON_TAG="# pantheon-shioaji-restart-oneshot"
if crontab -l 2>/dev/null | grep -qF "$CRON_TAG"; then
  echo "removing one-shot cron entry"
  crontab -l 2>/dev/null | grep -vF "$CRON_TAG" | crontab -
fi

echo "=== restart sequence complete at $(date -u +%FT%TZ) ==="
echo "next: a fresh wave EP5-BROKER-TW-002-RERUN-REAL task can be assigned manually after sandbox window opens."

#!/usr/bin/env bash
# SUP-COMMAND-RUNTIME-REFRESH-001 governed command runtime handoff.
#
# Replaces the live supervisor process with one launched from <new_root>, without
# touching the live config. Serialization rules:
#   * the durable intentional-restart declaration is written first, so a watchdog
#     relaunch is not charged to the crash-loop budget;
#   * TERM is sent while this script holds the runtime admission lock, so the
#     outgoing supervisor cannot be inside a locked canonical transaction;
#   * the lock is released before the new supervisor is launched, so the new
#     process never inherits a held lock file descriptor.
#
# Usage: swap-supervisor.sh <new_root> <label> [--discover-only]
#
# --discover-only stops after reporting the runtime it would install and the
# supervisor it would replace, before any mutation. Use it to confirm discovery
# resolves exactly one live supervisor.
set -uo pipefail

NEW_ROOT="${1:?new root required}"
LABEL="${2:?label required}"
DISCOVER_ONLY="${3:-}"
LIVE_CONFIG=/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json
ADMISSION_LOCK=/home/lupin/pantheon/.orchestrator/runtime-admission.lock
LOG_DIR=/home/lupin/pantheon/.orchestrator/logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/supervisor-${LABEL}-${STAMP}.log"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { echo "[swap $(stamp)] $*"; }

TARGET_SHA="$(git -C "$NEW_ROOT" rev-parse HEAD)" || exit 1
say "new_root=$NEW_ROOT target_sha=$TARGET_SHA log=$LOG"

# Match the same shape supervisor.py's own cmdline_is_supervisor_process uses:
# argv[0] must be a python interpreter running .orchestrator/supervisor.py. A
# plain substring match also caught the `/bin/bash -c "... supervisor.py ..."`
# launcher wrapper that started the incumbent, which made discovery report two
# processes and fail closed.
supervisor_pids() {
  local d pid argv0 cmd
  for d in /proc/[0-9]*; do
    pid="${d#/proc/}"
    [[ -r "$d/cmdline" ]] || continue
    argv0="$(tr '\0' '\n' <"$d/cmdline" 2>/dev/null | head -1)" || continue
    case "${argv0##*/}" in
      python*) ;;
      *) continue ;;
    esac
    cmd="$(tr '\0' ' ' <"$d/cmdline" 2>/dev/null)" || continue
    case "$cmd" in
      *worker_runner*) continue ;;
      *supervisor.py*--config*) echo "$pid" ;;
    esac
  done
}

mapfile -t OLD_PIDS < <(supervisor_pids)
if [[ "${#OLD_PIDS[@]}" -ne 1 ]]; then
  say "FATAL: expected exactly one live supervisor, found: ${OLD_PIDS[*]:-none}"
  exit 1
fi
OLD_PID="${OLD_PIDS[0]}"
OLD_CWD="$(readlink -f "/proc/$OLD_PID/cwd")"
say "old_pid=$OLD_PID old_cwd=$OLD_CWD"

if [[ "$DISCOVER_ONLY" == "--discover-only" ]]; then
  say "discover-only: stopping before the intentional restart declaration"
  exit 0
fi

say "recording intentional restart (waits for the in-flight cycle to release the runtime lock)"
if ! (cd "$NEW_ROOT" && python3 .orchestrator/supervisor_watchdog.py \
        --config "$LIVE_CONFIG" \
        --record-intent-pid "$OLD_PID" \
        --record-intent-target "$TARGET_SHA"); then
  say "FATAL: could not record the intentional restart; leaving pid=$OLD_PID running"
  exit 1
fi
say "intentional restart recorded"

say "acquiring runtime admission lock before TERM"
flock -x "$ADMISSION_LOCK" bash -s "$OLD_PID" <<'INNER'
set -uo pipefail
old_pid="$1"
echo "[swap-inner $(date -u +%Y-%m-%dT%H:%M:%SZ)] holding admission lock; sending TERM to $old_pid"
kill -TERM "$old_pid" 2>/dev/null || true
for _ in $(seq 1 120); do
  kill -0 "$old_pid" 2>/dev/null || break
  sleep 0.5
done
if kill -0 "$old_pid" 2>/dev/null; then
  echo "[swap-inner] FATAL: supervisor $old_pid still alive after TERM"
  exit 1
fi
echo "[swap-inner $(date -u +%Y-%m-%dT%H:%M:%SZ)] old supervisor $old_pid exited"
INNER
rc=$?
if [[ "$rc" -ne 0 ]]; then
  say "FATAL: supervisor stop failed (rc=$rc); not launching a replacement"
  exit 1
fi
say "runtime admission lock released"

say "launching supervisor from $NEW_ROOT"
(
  cd "$NEW_ROOT" || exit 1
  setsid env \
    -u ORCH_AGENT_ID -u ORCH_CONTEXT_FILES -u ORCH_HEARTBEAT_PATH -u ORCH_PROVIDER \
    -u ORCH_REASON -u ORCH_RUNNER_STATUS_PATH -u ORCH_RUN_ID -u ORCH_TARGET_FILES \
    -u ORCH_TASK_ID -u ORCH_WORKSPACE_PATH \
    -u PANTHEON_COMMAND_BASE_REF -u PANTHEON_COMMAND_REMOTE -u PANTHEON_COMMAND_ROOT \
    -u PANTHEON_COMMAND_RUNTIME_SHA -u PANTHEON_STATUS_COMMAND_BASE_REF \
    -u PANTHEON_STATUS_COMMAND_REMOTE -u PANTHEON_STATUS_COMMAND_ROOT \
    -u PANTHEON_STATUS_COMMAND_SHA -u PANTHEON_STATUS_ROOT \
    -u PANTHEON_TASK_STATE_EVENT_LOG -u PANTHEON_TASK_STATE_STORE_MODE \
    -u PANTHEON_WORKTREE_ROOT -u CLAUDE_CONFIG_DIR -u GH_CONFIG_DIR \
    /usr/bin/python3.12 -u .orchestrator/supervisor.py \
      --config "$LIVE_CONFIG" --verbose >>"$LOG" 2>&1 &
) &
disown 2>/dev/null || true

for _ in $(seq 1 40); do
  sleep 0.5
  new_pid=""
  while read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if [[ "$(readlink -f "/proc/$candidate/cwd" 2>/dev/null)" == "$(readlink -f "$NEW_ROOT")" ]]; then
      new_pid="$candidate"
      break
    fi
  done < <(supervisor_pids)
  [[ -n "$new_pid" ]] && break
done
if [[ -z "${new_pid:-}" ]]; then
  say "FATAL: replacement supervisor did not appear; log tail:"
  tail -20 "$LOG"
  exit 1
fi
say "new_pid=$new_pid new_cwd=$(readlink -f "/proc/$new_pid/cwd")"
say "log=$LOG"
sleep 3
tail -10 "$LOG"

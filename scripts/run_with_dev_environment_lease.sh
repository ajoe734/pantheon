#!/usr/bin/env bash
# Run one Pantheon dev deploy/smoke command under the job-owned CAS lease.
#
# The workflow owns acquisition, heartbeat, and release across steps. This
# wrapper makes heartbeat loss synchronous for the current step: it verifies
# ownership before/after the command and terminates the command's full process
# tree if the cross-step heartbeat exits or records a failure.

set -euo pipefail

# Capture the step-scoped credential immediately.  The first-stage wrapper
# moves it to an anonymous descriptor and execs a clean copy of itself so the
# long-lived wrapper's initial /proc environment and argv never contain it.
TOKEN_FD_ENV="PANTHEON_DEV_ENVIRONMENT_LEASE_TOKEN_FD"
lease_token=""
token_fd="${PANTHEON_DEV_ENVIRONMENT_LEASE_TOKEN_FD:-}"
if [[ -n "${token_fd}" && -n "${PANTHEON_ENVIRONMENT_LEASE_TOKEN:-}" ]]; then
  echo "[dev-environment-lease-guard] ERROR: token env and token FD are mutually exclusive" >&2
  exit 75
fi
if [[ -n "${token_fd}" ]]; then
  unset PANTHEON_DEV_ENVIRONMENT_LEASE_TOKEN_FD
  unset PANTHEON_ENVIRONMENT_LEASE_TOKEN
  [[ "${token_fd}" =~ ^[1-9][0-9]*$ ]] || {
    echo "[dev-environment-lease-guard] ERROR: lease token FD is invalid" >&2
    exit 75
  }
  IFS= read -r lease_token <&"${token_fd}" || [[ -n "${lease_token}" ]] || {
    echo "[dev-environment-lease-guard] ERROR: lease token FD is empty" >&2
    exit 75
  }
  exec {token_fd}<&-
elif [[ -n "${PANTHEON_ENVIRONMENT_LEASE_TOKEN:-}" ]]; then
  lease_token="${PANTHEON_ENVIRONMENT_LEASE_TOKEN}"
  export -n lease_token 2>/dev/null || true
  unset PANTHEON_ENVIRONMENT_LEASE_TOKEN
  exec {token_fd}<<<"${lease_token}"
  lease_token=""
  exec env -u PANTHEON_ENVIRONMENT_LEASE_TOKEN \
    "${TOKEN_FD_ENV}=${token_fd}" bash "$0" "$@"
else
  unset PANTHEON_ENVIRONMENT_LEASE_TOKEN
fi
export -n lease_token 2>/dev/null || true

if [[ "$#" -eq 0 ]]; then
  echo "usage: scripts/run_with_dev_environment_lease.sh <command> [args...]" >&2
  exit 64
fi

# Staging has an independent environment and does not use the shared dev lease.
if [[ "${TARGET_ENV:-}" != "dev" ]]; then
  exec "$@"
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LEASE_CLI="${SCRIPT_DIR}/dev_environment_lease.py"
STATE_FILE="${PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE:-}"
HEARTBEAT_PID_FILE="${PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE:-}"
HEARTBEAT_IDENTITY_FILE="${PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE:-}"
FAILURE_FILE="${PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE:-}"
HEARTBEAT_LOG="${PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_LOG:-}"
MAX_HEARTBEAT_AGE_SECONDS="${PANTHEON_DEV_ENVIRONMENT_LEASE_MAX_HEARTBEAT_AGE_SECONDS:-120}"
REMOTE_VERIFY_INTERVAL_SECONDS="${PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS:-30}"
COMMAND_PID=""
COMMAND_PGID=""
COMMAND_SID=""
GUARD_LEASE_ID=""

error() {
  echo "[dev-environment-lease-guard] ERROR: $*" >&2
  exit 75
}

[[ -n "${lease_token}" ]] \
  || error "PANTHEON_ENVIRONMENT_LEASE_TOKEN is required"
[[ -f "${LEASE_CLI}" && ! -L "${LEASE_CLI}" ]] \
  || error "adjacent lease CLI is missing or is a symlink: ${LEASE_CLI}"
[[ -f "${STATE_FILE}" ]] || error "lease state file is missing: ${STATE_FILE:-unset}"
[[ -f "${HEARTBEAT_PID_FILE}" ]] \
  || error "lease heartbeat PID file is missing: ${HEARTBEAT_PID_FILE:-unset}"
[[ -f "${HEARTBEAT_IDENTITY_FILE}" ]] \
  || error "lease heartbeat identity file is missing: ${HEARTBEAT_IDENTITY_FILE:-unset}"
[[ -n "${FAILURE_FILE}" ]] \
  || error "PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE is required"
[[ "${MAX_HEARTBEAT_AGE_SECONDS}" =~ ^[1-9][0-9]*$ \
  && "${MAX_HEARTBEAT_AGE_SECONDS}" -le 120 ]] \
  || error "max heartbeat age must be an integer from 1 through 120"
[[ "${REMOTE_VERIFY_INTERVAL_SECONDS}" =~ ^[1-9][0-9]*$ \
  && "${REMOTE_VERIFY_INTERVAL_SECONDS}" -le 30 ]] \
  || error "remote verify interval must be an integer from 1 through 30"

heartbeat_pid="$(tr -d '[:space:]' <"${HEARTBEAT_PID_FILE}")"
[[ "${heartbeat_pid}" =~ ^[1-9][0-9]*$ ]] || error "lease heartbeat PID is invalid"
GUARD_LEASE_ID="$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("leaseId") or "")' \
    "${STATE_FILE}"
)"
[[ "${GUARD_LEASE_ID}" =~ ^[0-9a-fA-F-]{36}$ ]] \
  || error "lease state file does not contain a valid leaseId"

process_group_has_live_members() {
  local pgid="$1"
  ps -eo pgid=,stat= | awk -v group="${pgid}" '
    $1 == group && $2 !~ /^[ZzXx]/ { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

command_process_is_running() {
  local process_state
  [[ -n "${COMMAND_PID}" ]] || return 1
  kill -0 "${COMMAND_PID}" 2>/dev/null || return 1
  process_state="$(ps -o stat= -p "${COMMAND_PID}" 2>/dev/null | tr -d '[:space:]')"
  case "${process_state}" in
    ""|Z*|X*|x*) return 1 ;;
  esac
  return 0
}

terminate_process_group() {
  local pgid="$1"
  local attempt

  [[ "${pgid}" =~ ^[1-9][0-9]*$ ]] || return
  # Freeze the independent group first so no shell/background child can move
  # to its next mutation while termination is being delivered.
  kill -STOP -- "-${pgid}" 2>/dev/null || true
  kill -TERM -- "-${pgid}" 2>/dev/null || true
  kill -CONT -- "-${pgid}" 2>/dev/null || true
  for attempt in $(seq 1 20); do
    process_group_has_live_members "${pgid}" || return
    sleep 0.25
  done
  kill -KILL -- "-${pgid}" 2>/dev/null || true
}

pause_process_group() {
  [[ -n "${COMMAND_PGID}" ]] || return 1
  kill -STOP -- "-${COMMAND_PGID}" 2>/dev/null
}

resume_process_group() {
  [[ -n "${COMMAND_PGID}" ]] || return 1
  kill -CONT -- "-${COMMAND_PGID}" 2>/dev/null
}

heartbeat_identity_matches() {
  kill -0 "${heartbeat_pid}" 2>/dev/null || return 1
  python3 "${LEASE_CLI}" verify-heartbeat-identity \
    --identity-file "${HEARTBEAT_IDENTITY_FILE}" \
    --pid "${heartbeat_pid}" \
    --expected-cli "${LEASE_CLI}" \
    --state-file "${STATE_FILE}" \
    >/dev/null
}

heartbeat_process_is_stopped() {
  local process_state
  process_state="$(ps -o stat= -p "${heartbeat_pid}" 2>/dev/null | tr -d '[:space:]')"
  case "${process_state}" in
    ""|Z*|X*|x*) return 0 ;;
  esac
  return 1
}

stop_heartbeat_for_quarantine() {
  local attempt

  heartbeat_process_is_stopped && return 0
  if ! heartbeat_identity_matches; then
    echo "[dev-environment-lease-guard] ERROR: refusing to signal an unverified heartbeat PID" >&2
    return 1
  fi

  kill -TERM "${heartbeat_pid}" 2>/dev/null || {
    heartbeat_process_is_stopped && return 0
    return 1
  }
  heartbeat_process_is_stopped && return 0
  # A stopped heartbeat cannot run its TERM handler until it is continued.
  # Revalidate after TERM because the original process may have exited and its
  # PID may already have been reused before CONT is delivered.
  if ! heartbeat_identity_matches; then
    heartbeat_process_is_stopped && return 0
    echo "[dev-environment-lease-guard] ERROR: heartbeat identity changed before CONT" >&2
    return 1
  fi
  kill -CONT "${heartbeat_pid}" 2>/dev/null || true
  for attempt in $(seq 1 20); do
    heartbeat_process_is_stopped && return 0
    sleep 0.25
  done

  # Re-check PID/start-ticks/cmdline immediately before escalation. Refuse to
  # signal when the recorded heartbeat identity has changed.
  if ! heartbeat_identity_matches; then
    heartbeat_process_is_stopped && return 0
    echo "[dev-environment-lease-guard] ERROR: heartbeat identity changed before KILL escalation" >&2
    return 1
  fi
  kill -KILL "${heartbeat_pid}" 2>/dev/null || true
  for attempt in $(seq 1 20); do
    heartbeat_process_is_stopped && return 0
    sleep 0.25
  done
  echo "[dev-environment-lease-guard] ERROR: heartbeat did not stop for lease quarantine" >&2
  return 1
}

cleanup_command() {
  local original_status=$?
  trap - EXIT INT TERM
  if [[ -n "${COMMAND_PGID}" ]] && process_group_has_live_members "${COMMAND_PGID}"; then
    terminate_process_group "${COMMAND_PGID}"
  fi
  if [[ -n "${COMMAND_PID}" ]]; then
    wait "${COMMAND_PID}" 2>/dev/null || true
  fi
  if [[ "${original_status}" -ne 0 ]]; then
    if [[ ! -e "${FAILURE_FILE}" ]]; then
      record_guard_failure "${original_status}" || true
    fi
    # A cancelled/terminated/failed step may still reach the workflow's
    # always() cleanup. Stop renewal synchronously so cleanup cannot mistake
    # cancellation for a healthy lease and release it early.
    if ! stop_heartbeat_for_quarantine; then
      original_status=75
    elif [[ "${original_status}" -ne 130 && "${original_status}" -ne 143 ]]; then
      original_status=75
    fi
  fi
  exit "${original_status}"
}

verify_lease() {
  PANTHEON_ENVIRONMENT_LEASE_TOKEN="${lease_token}" \
    python3 "${LEASE_CLI}" verify \
    --state-file "${STATE_FILE}" \
    --max-heartbeat-age-seconds "${MAX_HEARTBEAT_AGE_SECONDS}"
}

heartbeat_is_healthy() {
  local process_state
  [[ ! -e "${FAILURE_FILE}" ]] || return 1
  kill -0 "${heartbeat_pid}" 2>/dev/null || return 1
  process_state="$(ps -o stat= -p "${heartbeat_pid}" 2>/dev/null | tr -d '[:space:]')"
  case "${process_state}" in
    ""|T*|t*|Z*|X*|x*|D*) return 1 ;;
  esac
  heartbeat_identity_matches
}

show_heartbeat_failure() {
  if [[ -f "${FAILURE_FILE}" ]]; then
    sed -n '1,200p' "${FAILURE_FILE}" >&2
  fi
  if [[ -n "${HEARTBEAT_LOG}" && -f "${HEARTBEAT_LOG}" ]]; then
    sed -n '1,200p' "${HEARTBEAT_LOG}" >&2
  fi
}

record_guard_failure() {
  local exit_status="$1"
  local tmp="${FAILURE_FILE}.tmp.$$"
  mkdir -p "$(dirname -- "${FAILURE_FILE}")"
  printf '{"schemaVersion":1,"status":"guarded_command_failed","exitStatus":%s,"detectedAt":"%s"}\n' \
    "${exit_status}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${tmp}"
  mv -f "${tmp}" "${FAILURE_FILE}"
}

fail_guarded_command() {
  local message="$1"
  if [[ ! -e "${FAILURE_FILE}" ]]; then
    record_guard_failure 75 || true
  fi
  if [[ -n "${COMMAND_PGID}" ]] && process_group_has_live_members "${COMMAND_PGID}"; then
    terminate_process_group "${COMMAND_PGID}"
  fi
  if [[ -n "${COMMAND_PID}" ]]; then
    wait "${COMMAND_PID}" 2>/dev/null || true
  fi
  COMMAND_PID=""
  COMMAND_PGID=""
  COMMAND_SID=""
  error "${message}"
}

launch_guarded_command() {
  local attempt
  local process_state
  local wrapper_pgid

  PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID="${GUARD_LEASE_ID}" \
    python3 -c '
import os
import signal
import sys

os.setsid()
os.kill(os.getpid(), signal.SIGSTOP)
os.execvp(sys.argv[1], sys.argv[1:])
' "$@" &
  COMMAND_PID=$!

  for attempt in $(seq 1 100); do
    process_state="$(ps -o stat= -p "${COMMAND_PID}" 2>/dev/null | tr -d '[:space:]')"
    case "${process_state}" in
      T*|t*) break ;;
      ""|Z*|X*|x*)
        wait "${COMMAND_PID}" 2>/dev/null || true
        COMMAND_PID=""
        error "guarded command launcher exited before process-group isolation"
        ;;
    esac
    sleep 0.02
  done
  [[ "${process_state}" == T* || "${process_state}" == t* ]] \
    || fail_guarded_command "guarded command launcher did not stop for isolation"

  COMMAND_PGID="$(ps -o pgid= -p "${COMMAND_PID}" 2>/dev/null | tr -d '[:space:]')"
  COMMAND_SID="$(ps -o sid= -p "${COMMAND_PID}" 2>/dev/null | tr -d '[:space:]')"
  wrapper_pgid="$(ps -o pgid= -p "$$" 2>/dev/null | tr -d '[:space:]')"
  [[ "${COMMAND_PGID}" == "${COMMAND_PID}" \
    && "${COMMAND_SID}" == "${COMMAND_PID}" \
    && "${COMMAND_PGID}" != "${wrapper_pgid}" ]] \
    || fail_guarded_command "guarded command did not enter an independent session/process group"
  resume_process_group \
    || fail_guarded_command "guarded command process group could not be resumed"
}

trap cleanup_command EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

verify_lease
heartbeat_is_healthy || {
  show_heartbeat_failure
  error "lease heartbeat is not healthy before command start"
}

set +e
launch_guarded_command "$@"
next_remote_verify=$((SECONDS + REMOTE_VERIFY_INTERVAL_SECONDS))
while command_process_is_running; do
  if ! heartbeat_is_healthy; then
    show_heartbeat_failure
    fail_guarded_command "lease heartbeat identity/health was lost; guarded process group terminated"
  fi
  command_process_is_running || break
  if (( SECONDS >= next_remote_verify )); then
    if ! pause_process_group; then
      command_process_is_running || break
      fail_guarded_command "guarded process group could not be paused for lease verification"
    fi
    if ! heartbeat_is_healthy; then
      show_heartbeat_failure
      fail_guarded_command "lease heartbeat was lost during remote verification"
    fi
    if ! verify_lease; then
      fail_guarded_command "remote lease verification failed; guarded process group terminated"
    fi
    resume_process_group \
      || fail_guarded_command "guarded process group could not resume after lease verification"
    next_remote_verify=$((SECONDS + REMOTE_VERIFY_INTERVAL_SECONDS))
  fi
  sleep 0.5
done
wait "${COMMAND_PID}"
command_status=$?
if [[ -n "${COMMAND_PGID}" ]] && process_group_has_live_members "${COMMAND_PGID}"; then
  terminate_process_group "${COMMAND_PGID}"
  command_status=75
fi
COMMAND_PID=""
COMMAND_PGID=""
COMMAND_SID=""
set -e

if [[ "${command_status}" -ne 0 ]]; then
  record_guard_failure "${command_status}"
  # Every guarded-command failure quarantines the remote lease. Return the
  # guard-specific status while preserving the command's status in evidence.
  exit 75
fi
if ! verify_lease; then
  [[ -e "${FAILURE_FILE}" ]] || record_guard_failure 75
  exit 75
fi
if ! heartbeat_is_healthy; then
  show_heartbeat_failure
  [[ -e "${FAILURE_FILE}" ]] || record_guard_failure 75
  exit 75
fi

trap - EXIT INT TERM
lease_token=""
exit 0

#!/usr/bin/env bash
# Execute one provider pull through the already-running Source controller.
#
# This command is deliberately not a second controller.  Run it on the dev
# host under scripts/run_with_dev_environment_lease.sh after that wrapper has
# acquired the shared dev-environment lease.  The normal scheduler remains
# reconcile_only with zero provider ticks throughout this command.

set -euo pipefail

COMPOSE_PROJECT="${PANTHEON_COMPOSE_PROJECT:-pantheon}"
COMPOSE_FILE="${PANTHEON_COMPOSE_FILE:-docker-compose.yml}"
CONNECTOR_ID="${SOURCE_INGEST_BOUNDED_CONNECTOR_ID:-tw-twse-tpex-official-market}"
ALLOWED_HOSTS="${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS:-openapi.twse.com.tw,www.tpex.org.tw}"
READY_TIMEOUT_SECONDS="${SOURCE_INGEST_BOUNDED_READY_TIMEOUT_SECONDS:-90}"
RUN_TIMEOUT_SECONDS="${SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS:-180}"
SOURCE_SERVICE="source-ingest"
SCHEDULER_SERVICE="source-ingest-scheduler"
egress_open=false
egress_failsafe_pid=""

error() {
  echo "[bounded-source-refresh] ERROR: $*" >&2
  exit 1
}

require_under_lease() {
  [[ "${TARGET_ENV:-}" == "dev" ]] \
    || error "TARGET_ENV=dev is required"
  [[ -n "${PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE:-}" ]] \
    || error "run this command through run_with_dev_environment_lease.sh"
  [[ -n "${PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE:-}" ]] \
    || error "run this command through run_with_dev_environment_lease.sh"
}

validate_input() {
  [[ "${CONNECTOR_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || error "SOURCE_INGEST_BOUNDED_CONNECTOR_ID must be one exact connector id"
  [[ "${READY_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
    && (( READY_TIMEOUT_SECONDS <= 300 )) \
    || error "SOURCE_INGEST_BOUNDED_READY_TIMEOUT_SECONDS must be between 1 and 300"
  [[ "${RUN_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
    && (( RUN_TIMEOUT_SECONDS <= 600 )) \
    || error "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS must be between 1 and 600"

  if [[ "${CONNECTOR_ID}" == "tw-twse-tpex-official-market" ]]; then
    python3 - "${ALLOWED_HOSTS}" <<'PY'
import sys

from services.external_egress import allowed_hosts

hosts = allowed_hosts(
    {
        "PANTHEON_EXTERNAL_EGRESS": "allowlist",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": sys.argv[1],
    }
)
required = {"openapi.twse.com.tw", "www.tpex.org.tw"}
missing = sorted(required - hosts)
if missing:
    raise SystemExit("official TWSE/TPEx refresh requires exact hosts: " + ",".join(missing))
PY
  fi
}

compose() {
  docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" "$@"
}

restore_egress() {
  PANTHEON_EXTERNAL_EGRESS=deny \
  PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS= \
    compose up -d --no-build --force-recreate --no-deps "${SOURCE_SERVICE}"
}

start_egress_failsafe() {
  # The normal EXIT/INT/TERM trap restores deny immediately.  This detached
  # fallback covers an outer tool/session being killed before Bash can run its
  # trap, so a failed one-off cannot leave the Source API with provider egress.
  local failsafe_seconds=$((READY_TIMEOUT_SECONDS + RUN_TIMEOUT_SECONDS + 30))
  nohup bash -c '
    sleep "$1"
    PANTHEON_EXTERNAL_EGRESS=deny PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS= \
      docker compose -p "$2" -f "$3" up -d --no-build --force-recreate --no-deps "$4"
  ' _ "${failsafe_seconds}" "${COMPOSE_PROJECT}" "${COMPOSE_FILE}" "${SOURCE_SERVICE}" \
    </dev/null >/dev/null 2>&1 &
  egress_failsafe_pid="$!"
}

cancel_egress_failsafe() {
  [[ -n "${egress_failsafe_pid}" ]] || return 0
  kill "${egress_failsafe_pid}" 2>/dev/null || true
  wait "${egress_failsafe_pid}" 2>/dev/null || true
  egress_failsafe_pid=""
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  cancel_egress_failsafe
  if [[ "${egress_open}" == true ]]; then
    echo "[bounded-source-refresh] restoring ${SOURCE_SERVICE} external egress to deny" >&2
    if ! restore_egress; then
      echo "[bounded-source-refresh] ERROR: could not restore external egress to deny" >&2
      status=1
    fi
  fi
  exit "${status}"
}

wait_for_source_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
  local container_id=""
  local status=""

  while (( SECONDS < deadline )); do
    container_id="$(compose ps -q "${SOURCE_SERVICE}")"
    if [[ -n "${container_id}" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
      if [[ "${status}" == "healthy" ]]; then
        return 0
      fi
      if [[ "${status}" == "exited" || "${status}" == "dead" ]]; then
        compose logs --no-color --tail=120 "${SOURCE_SERVICE}" >&2 || true
        error "${SOURCE_SERVICE} entered ${status} while enabling bounded refresh"
      fi
    fi
    sleep 2
  done

  compose logs --no-color --tail=120 "${SOURCE_SERVICE}" >&2 || true
  error "${SOURCE_SERVICE} did not become healthy within ${READY_TIMEOUT_SECONDS}s"
}

run_one_tick() {
  compose exec -T \
    -e "SOURCE_INGEST_BOUNDED_CONNECTOR_ID=${CONNECTOR_ID}" \
    -e "SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=${RUN_TIMEOUT_SECONDS}" \
    "${SCHEDULER_SERVICE}" python - <<'PY'
import json
import os
from pathlib import Path

from services.source_ingestion.controller_worker import run_schedule_tick

connector_id = os.environ["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"]
timeout_seconds = float(os.environ["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"])
token = Path("/data/source-ingest/controller_token").read_text(encoding="utf-8").strip()
result = run_schedule_tick(
    api_url="http://source-ingest:8097",
    max_concurrency=1,
    timeout_seconds=timeout_seconds,
    force_connector_ids=[connector_id],
    exclusive_connector_ids=[connector_id],
    controller_token=token,
)
summary = result.get("summary") if isinstance(result, dict) else None
if not isinstance(summary, dict) or summary.get("total_ran") != 1 or summary.get("total_failed") != 0:
    raise SystemExit("bounded source refresh did not complete exactly one successful connector run")
print(json.dumps({"connector_id": connector_id, "summary": summary}, sort_keys=True))
PY
}

main() {
  require_under_lease
  validate_input
  trap cleanup EXIT INT TERM

  # This restarts only the provider-fetching API process.  The sole durable
  # controller stays up and retains its controller token and ownership.
  start_egress_failsafe
  egress_open=true
  PANTHEON_EXTERNAL_EGRESS=allowlist \
  PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="${ALLOWED_HOSTS}" \
    compose up -d --no-build --force-recreate --no-deps "${SOURCE_SERVICE}"
  wait_for_source_ready
  run_one_tick

  restore_egress
  egress_open=false
  cancel_egress_failsafe
  echo "[bounded-source-refresh] completed one ${CONNECTOR_ID} pull; external egress restored to deny"
}

main "$@"

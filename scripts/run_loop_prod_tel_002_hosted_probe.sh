#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/run_loop_prod_tel_002_hosted_probe.sh \
  --expected-sha SHA \
  --container-output /tmp/evidence.json \
  --remote-output /tmp/evidence.json \
  [--timeout-seconds 420] \
  [--stimulus-timeout-seconds 180] \
  [--worker-ready-timeout-seconds 120] \
  [--worker-heartbeat-max-age-seconds 120] \
  [--poll-seconds 5]
EOF
}

expected_sha=""
container_output=""
remote_output=""
timeout_seconds="420"
stimulus_timeout_seconds="180"
worker_ready_timeout_seconds="120"
worker_heartbeat_max_age_seconds="120"
poll_seconds="5"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --expected-sha)
      expected_sha="${2:-}"
      shift 2
      ;;
    --container-output)
      container_output="${2:-}"
      shift 2
      ;;
    --remote-output)
      remote_output="${2:-}"
      shift 2
      ;;
    --timeout-seconds)
      timeout_seconds="${2:-}"
      shift 2
      ;;
    --stimulus-timeout-seconds)
      stimulus_timeout_seconds="${2:-}"
      shift 2
      ;;
    --worker-ready-timeout-seconds)
      worker_ready_timeout_seconds="${2:-}"
      shift 2
      ;;
    --worker-heartbeat-max-age-seconds)
      worker_heartbeat_max_age_seconds="${2:-}"
      shift 2
      ;;
    --poll-seconds)
      poll_seconds="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [[ -z "${expected_sha}" || -z "${container_output}" || -z "${remote_output}" ]]; then
  usage
  exit 64
fi

compose=(docker compose -p pantheon -f docker-compose.yml)

write_probe_failure() {
  local code="$1"
  local message="$2"
  "${compose[@]}" exec -T loop-run-projector-scheduler \
    python -c 'import sys; from pathlib import Path; from services.trade_journey.hosted_lifecycle_probe import write_failure_artifact; write_failure_artifact(Path(sys.argv[1]), expected_sha=sys.argv[2], code=sys.argv[3], message=sys.argv[4])' \
    "${container_output}" "${expected_sha}" "${code}" "${message}"
}

copy_probe_evidence() {
  local container_id
  container_id="$("${compose[@]}" ps -q loop-run-projector-scheduler)"
  if [[ -z "${container_id}" ]]; then
    return 1
  fi
  mkdir -p "$(dirname "${remote_output}")"
  docker cp "${container_id}:${container_output}" "${remote_output}"
}

baseline_status=0
set +e
baseline="$("${compose[@]}" exec -T loop-run-projector-scheduler \
  python -m services.trade_journey.hosted_lifecycle_probe \
    --expected-sha "${expected_sha}" \
    --print-high-watermark)"
baseline_status=$?
set -e
baseline="${baseline//$'\r'/}"
baseline="${baseline//$'\n'/}"
if [[ "${baseline_status}" -ne 0 || ! "${baseline}" =~ ^[0-9]+$ ]]; then
  write_probe_failure \
    "baseline_high_watermark_failed" \
    "hosted lifecycle baseline high-watermark capture failed"
  copy_probe_evidence
  if [[ "${baseline_status}" -ne 0 ]]; then
    exit "${baseline_status}"
  fi
  exit 1
fi

stimulus_status=0
set +e
"${compose[@]}" exec -T paper-signal-producer \
  python -m services.trade_journey.hosted_lifecycle_stimulus \
    --timeout-seconds "${stimulus_timeout_seconds}" \
    --worker-ready-timeout-seconds "${worker_ready_timeout_seconds}" \
    --worker-heartbeat-max-age-seconds "${worker_heartbeat_max_age_seconds}" \
    --poll-seconds "${poll_seconds}" \
    --allow-ambiguous-reconciliation
stimulus_status=$?
set -e
if [[ "${stimulus_status}" -ne 0 ]]; then
  write_probe_failure \
    "hosted_stimulus_failed" \
    "hosted lifecycle stimulus failed before the read-only proof"
  copy_probe_evidence
  exit "${stimulus_status}"
fi

probe_status=0
set +e
"${compose[@]}" exec -T loop-run-projector-scheduler \
  python -m services.trade_journey.hosted_lifecycle_probe \
    --expected-sha "${expected_sha}" \
    --baseline-high-watermark "${baseline}" \
    --output "${container_output}" \
    --timeout-seconds "${timeout_seconds}" \
    --poll-seconds "${poll_seconds}"
probe_status=$?
set -e

copy_probe_evidence
exit "${probe_status}"

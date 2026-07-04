#!/usr/bin/env bash
# Live smoke gate for cron.* WRITE methods through the openclaw-gateway-adapter
# proxy (POST /api/openclaw-adapter/gateway/cron).
#
# Why this exists: scripts/openclaw-assistant-openclaw-live-smoke.sh only
# proves the `openclaw` assistant provider (agent turns) is live. The BFF
# persona OODA-cron registrar (services/control-plane/cron/persona_cron_registrar.py)
# depends on a SEPARATE capability of the same adapter proxy: cron.add /
# cron.remove, which additionally require operator.admin scope on the
# adapter's paired Gateway device (see
# scripts/openclaw-approve-adapter-cron-scope.sh and
# docs/runbooks/openclaw-adapter-device-pairing.md). cron.list alone staying
# green does not prove cron.add works.
#
# This script adds one job, confirms it via cron.list, then removes it again
# (cron.remove) so it leaves no residue in the shared Gateway cron store.
#
# Usage:
#   bash scripts/openclaw-cron-write-scope-smoke.sh
#   OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 bash scripts/openclaw-cron-write-scope-smoke.sh
set -euo pipefail

BASE_URL="${OPENCLAW_GATEWAY_ADAPTER_URL:-${OPENCLAW_ADAPTER_URL:-http://localhost:18104}}"
JOB_NAME="openclaw-cron-write-scope-smoke-$$"
CRON_ENDPOINT="${BASE_URL}/api/openclaw-adapter/gateway/cron"

echo "Adapter base URL: ${BASE_URL}"
echo "Probe job name:   ${JOB_NAME}"

# Gateway rejects schedule.at more than 10 years out; pick a fixed +5y offset
# from now so this never fires (job stays enabled:false) without drifting
# into the rejection window as calendar time passes.
PROBE_AT="$(date -u -d "+5 years" +%Y-%m-%dT%H:%M:%SZ)"

cleanup() {
  local job_id="${JOB_ID:-}"
  if [ -z "${job_id}" ]; then
    # cron.add may have succeeded server-side even if the client-side curl
    # call above timed out (or otherwise failed) before JOB_ID was captured.
    # Look the probe job up by name so it doesn't leak into the shared
    # Gateway cron store.
    echo ""
    echo "=== cleanup: JOB_ID unknown, looking up probe job by name ==="
    local lookup
    lookup=$(curl -sS -m 60 -X POST "${CRON_ENDPOINT}" \
      -H "Content-Type: application/json" \
      -d '{"method":"cron.list","params":{"limit":200,"includeDisabled":true}}' 2>/dev/null || true)
    if [ -n "${lookup}" ]; then
      job_id=$(echo "${lookup}" | jq -r --arg name "$JOB_NAME" \
        '[.data.jobs[]? | select(.name == $name)][0].id // empty' 2>/dev/null || true)
    fi
  fi
  if [ -n "${job_id}" ]; then
    echo ""
    echo "=== cleanup: cron.remove ${job_id} ==="
    curl -sS -m 60 -X POST "${CRON_ENDPOINT}" \
      -H "Content-Type: application/json" \
      -d "$(jq -nc --arg id "$job_id" '{method:"cron.remove", params:{id:$id}}')" | jq . || true
  else
    echo ""
    echo "=== cleanup: no probe job found to remove ==="
  fi
}
trap cleanup EXIT

echo ""
echo "=== 1/3 cron.add (a disabled, far-future one-shot probe job) ==="
ADD_PAYLOAD=$(jq -nc --arg name "$JOB_NAME" --arg at "$PROBE_AT" '{
  method: "cron.add",
  params: {
    name: $name,
    enabled: false,
    deleteAfterRun: true,
    schedule: {kind: "at", at: $at},
    sessionTarget: "main",
    wakeMode: "next-heartbeat",
    payload: {kind: "systemEvent", text: "{\"kind\":\"pantheon.cron_write_scope_smoke\"}"}
  }
}')
ADD_RESPONSE=$(curl -sS -m 120 -w "\n%{http_code}" -X POST "${CRON_ENDPOINT}" \
  -H "Content-Type: application/json" -d "${ADD_PAYLOAD}")
ADD_HTTP_STATUS=$(printf '%s\n' "${ADD_RESPONSE}" | tail -n1)
ADD_BODY=$(printf '%s\n' "${ADD_RESPONSE}" | sed '$d')
echo "${ADD_BODY}" | jq .

if [ "${ADD_HTTP_STATUS}" != "200" ]; then
  echo "FAIL: cron.add HTTP ${ADD_HTTP_STATUS}"
  exit 1
fi
ADD_STATUS=$(echo "${ADD_BODY}" | jq -r '.status')
if [ "${ADD_STATUS}" != "ok" ]; then
  ERR=$(echo "${ADD_BODY}" | jq -r '.message // .error_code // "unknown"')
  echo "FAIL: cron.add status=${ADD_STATUS} (${ERR})"
  echo "      If this mentions 'pairing required' / 'scope upgrade pending', run"
  echo "      scripts/openclaw-approve-adapter-cron-scope.sh first."
  exit 1
fi
JOB_ID=$(echo "${ADD_BODY}" | jq -r '.data.id // empty')
if [ -z "${JOB_ID}" ]; then
  echo "FAIL: cron.add returned status=ok but no job id."
  exit 1
fi
echo "OK: cron.add -> id=${JOB_ID}"

echo ""
echo "=== 2/3 cron.list shows the new job (not dry_run) ==="
LIST_BODY=$(curl -sS -m 60 -X POST "${CRON_ENDPOINT}" \
  -H "Content-Type: application/json" \
  -d '{"method":"cron.list","params":{"limit":200,"includeDisabled":true}}')
FOUND=$(echo "${LIST_BODY}" | jq -r --arg id "$JOB_ID" '[.data.jobs[]? | select(.id == $id)] | length')
if [ "${FOUND}" != "1" ]; then
  echo "FAIL: job ${JOB_ID} not found in cron.list."
  echo "${LIST_BODY}" | jq .
  exit 1
fi
echo "OK: cron.list contains job ${JOB_ID}"

echo ""
echo "=== 3/3 transport is a real gateway RPC (status=ok, not dry_run) ==="
echo "OK: adapter proxy performed a live cron.add + cron.list round trip."
echo ""
echo "=== cron write scope smoke PASSED ==="

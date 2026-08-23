#!/usr/bin/env bash
# Live smoke gate for the `openclaw` assistant provider against a DEPLOYED adapter.
#
# Why this exists: OPENCLAW-AGENT-TURN-LIVE-FIX shipped the provider code with
# unit tests that mock the CLI, and the existing pytest live smoke
# (test_assistant_openclaw_provider_live.py) SKIPS unless the openclaw binary +
# gateway env are present — so CI stayed green while the deployed adapter image
# had no openclaw binary at all (it degraded with OPENCLAW_BINARY_NOT_FOUND on
# every real turn). This script is the missing gate: it talks to a real adapter,
# drives a real agent turn through the gateway, and FAILS (non-zero) on any
# degradation. It does NOT skip — point it at a deployment and it must pass.
#
# Usage (on the dev VM, adapter publishes host port 18104):
#   bash scripts/openclaw-assistant-openclaw-live-smoke.sh
#   OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 bash scripts/openclaw-assistant-openclaw-live-smoke.sh
set -euo pipefail

BASE_URL="${OPENCLAW_GATEWAY_ADAPTER_URL:-${OPENCLAW_ADAPTER_URL:-http://localhost:18104}}"
OPERATOR_ID="${SMOKE_OPERATOR_ID:-openclaw-live-smoke}"
SENTINEL="OPENCLAW_LIVE"

# Gateway configuration changes recreate the service before its provider has
# finished warming up.  A single long request made the deployment outcome race
# that warm-up.  Keep the deployment budget bounded, but spend it over small
# readiness-only probes.  Do not reuse this loop for an invoke: an agent turn
# can have side effects and must be issued exactly once.
READINESS_TOTAL_BUDGET_SECONDS="${OPENCLAW_READINESS_TOTAL_BUDGET_SECONDS:-90}"
READINESS_ATTEMPT_TIMEOUT_SECONDS="${OPENCLAW_READINESS_ATTEMPT_TIMEOUT_SECONDS:-20}"
READINESS_RETRY_DELAY_SECONDS="${OPENCLAW_READINESS_RETRY_DELAY_SECONDS:-2}"
READINESS_MAX_TOTAL_BUDGET_SECONDS=90
READINESS_MAX_ATTEMPT_TIMEOUT_SECONDS=20
READINESS_MAX_RETRY_DELAY_SECONDS=5

fail_readiness_configuration() {
  echo "FAIL: invalid bounded readiness configuration: $1" >&2
  exit 2
}

require_bounded_positive_integer() {
  local name="$1"
  local value="$2"
  local maximum="$3"

  if ! [[ "${value}" =~ ^[1-9][0-9]*$ ]] || (( value > maximum )); then
    fail_readiness_configuration "${name} must be a positive integer no greater than ${maximum}."
  fi
}

require_bounded_positive_integer \
  "OPENCLAW_READINESS_TOTAL_BUDGET_SECONDS" \
  "${READINESS_TOTAL_BUDGET_SECONDS}" \
  "${READINESS_MAX_TOTAL_BUDGET_SECONDS}"
require_bounded_positive_integer \
  "OPENCLAW_READINESS_ATTEMPT_TIMEOUT_SECONDS" \
  "${READINESS_ATTEMPT_TIMEOUT_SECONDS}" \
  "${READINESS_MAX_ATTEMPT_TIMEOUT_SECONDS}"
require_bounded_positive_integer \
  "OPENCLAW_READINESS_RETRY_DELAY_SECONDS" \
  "${READINESS_RETRY_DELAY_SECONDS}" \
  "${READINESS_MAX_RETRY_DELAY_SECONDS}"

# Failure details can originate at an external gateway.  Preserve known,
# machine-readable error codes; reduce everything else to a deterministic
# fingerprint so the smoke output cannot leak an upstream detail or token.
sanitized_reason_id() {
  local raw="$1"
  local digest

  if [[ "${raw}" =~ ^[A-Z][A-Z0-9_:-]{0,95}$ ]]; then
    printf '%s' "${raw}"
    return
  fi

  digest=$(printf '%s' "${raw}" | sha256sum | awk '{print substr($1, 1, 12)}')
  printf 'SHA256_%s' "${digest}"
}

readiness_response_reason() {
  local response="$1"
  local raw_reason

  raw_reason=$(printf '%s' "${response}" | jq -r '.reason // .error_code // .detail // empty' 2>/dev/null || true)
  if [ -z "${raw_reason}" ]; then
    printf 'NONE'
    return
  fi
  sanitized_reason_id "${raw_reason}"
}

readiness_probe_once() {
  local request_timeout_seconds="$1"
  local curl_status

  set +e
  READINESS_RESPONSE=$(curl -sS --max-time "${request_timeout_seconds}" \
    -w $'\n%{http_code}' \
    "${BASE_URL}/api/openclaw-adapter/assistant/readiness/openclaw?auth_probe=true" 2>/dev/null)
  curl_status=$?
  set -e

  READINESS_CURL_STATUS="${curl_status}"
  READINESS_HTTP_STATUS="000"
  READINESS_BODY=""
  if [ "${curl_status}" -eq 0 ]; then
    READINESS_HTTP_STATUS=$(printf '%s\n' "${READINESS_RESPONSE}" | tail -n1)
    READINESS_BODY=$(printf '%s\n' "${READINESS_RESPONSE}" | sed '$d')
  fi
}

wait_for_openclaw_readiness() {
  local started_seconds="${SECONDS}"
  local attempts=0
  local elapsed_seconds
  local remaining_seconds
  local request_timeout_seconds
  local retry_delay_seconds
  local ready
  local reason
  local last_failure="NOT_ATTEMPTED"

  while true; do
    elapsed_seconds=$((SECONDS - started_seconds))
    remaining_seconds=$((READINESS_TOTAL_BUDGET_SECONDS - elapsed_seconds))
    if (( remaining_seconds <= 0 )); then
      break
    fi

    request_timeout_seconds="${READINESS_ATTEMPT_TIMEOUT_SECONDS}"
    if (( request_timeout_seconds > remaining_seconds )); then
      request_timeout_seconds="${remaining_seconds}"
    fi

    attempts=$((attempts + 1))
    readiness_probe_once "${request_timeout_seconds}"

    if [ "${READINESS_CURL_STATUS}" -eq 7 ]; then
      last_failure="CURL_CONNECTION_REFUSED"
    elif [ "${READINESS_CURL_STATUS}" -eq 28 ]; then
      last_failure="CURL_REQUEST_TIMEOUT"
    elif [ "${READINESS_CURL_STATUS}" -ne 0 ]; then
      echo "FAIL: openclaw readiness request failed without retry (${READINESS_CURL_STATUS})." >&2
      return 1
    elif [ "${READINESS_HTTP_STATUS}" = "200" ]; then
      ready=$(printf '%s' "${READINESS_BODY}" | jq -r 'if type == "object" and has("ready") then .ready | tostring else empty end' 2>/dev/null || true)
      if [ "${ready}" = "true" ]; then
        READINESS="${READINESS_BODY}"
        return 0
      fi
      if [ "${ready}" = "false" ]; then
        reason=$(readiness_response_reason "${READINESS_BODY}")
        echo "FAIL: openclaw provider reported not-ready without retry (READY_FALSE_${reason})." >&2
        return 1
      fi
      echo "FAIL: openclaw readiness returned an invalid HTTP 200 payload without retry." >&2
      return 1
    elif [ "${READINESS_HTTP_STATUS}" = "503" ]; then
      reason=$(readiness_response_reason "${READINESS_BODY}")
      last_failure="HTTP_503_${reason}"
    else
      echo "FAIL: openclaw readiness returned HTTP ${READINESS_HTTP_STATUS} without retry." >&2
      return 1
    fi

    elapsed_seconds=$((SECONDS - started_seconds))
    remaining_seconds=$((READINESS_TOTAL_BUDGET_SECONDS - elapsed_seconds))
    if (( remaining_seconds <= 0 )); then
      break
    fi

    retry_delay_seconds="${READINESS_RETRY_DELAY_SECONDS}"
    if (( retry_delay_seconds > remaining_seconds )); then
      retry_delay_seconds="${remaining_seconds}"
    fi
    echo "Readiness attempt ${attempts} did not converge (${last_failure}); retrying in ${retry_delay_seconds}s (${remaining_seconds}s budget remains)." >&2
    sleep "${retry_delay_seconds}"
  done

  echo "FAIL: openclaw readiness did not converge within ${READINESS_TOTAL_BUDGET_SECONDS}s after ${attempts} attempt(s) (last: ${last_failure})." >&2
  return 1
}

echo "Adapter base URL: ${BASE_URL}"

echo ""
echo "=== 1/4 openclaw provider readiness (auth_probe=true) ==="
wait_for_openclaw_readiness
echo "${READINESS}" | jq .
READY=$(echo "${READINESS}" | jq -r '.ready')
if [ "${READY}" != "true" ]; then
  REASON=$(echo "${READINESS}" | jq -r '.reason // "unknown"')
  echo "FAIL: openclaw provider not ready (reason: ${REASON})."
  echo "      The adapter image must contain the openclaw CLI and the gateway"
  echo "      token/URL must be configured. See services/openclaw-gateway-adapter/Dockerfile."
  exit 1
fi
echo "OK: openclaw provider readiness=ready"

echo ""
echo "=== 2/4 live agent turn (sentinel: ${SENTINEL}) ==="
# This is deliberately a single request.  Retrying a completed agent turn can
# duplicate an external side effect, so only the readiness endpoint converges.
INVOKE_PAYLOAD=$(jq -nc --arg p "Reply with exactly: ${SENTINEL}" '{prompt: $p, mode: "user"}')
RESPONSE=$(curl -sS -m 120 -w "\n%{http_code}" -X POST \
  "${BASE_URL}/api/openclaw-adapter/assistant/providers/openclaw/invoke" \
  -H "Content-Type: application/json" \
  -H "X-Operator-Id: ${OPERATOR_ID}" \
  -d "${INVOKE_PAYLOAD}")
HTTP_STATUS=$(printf '%s\n' "${RESPONSE}" | tail -n1)
BODY=$(printf '%s\n' "${RESPONSE}" | sed '$d')
echo "${BODY}" | jq .

if [ "${HTTP_STATUS}" != "200" ]; then
  echo "FAIL: invoke returned HTTP ${HTTP_STATUS}"
  exit 1
fi

STATUS=$(echo "${BODY}" | jq -r '.data.status // .status // "unknown"')
if [ "${STATUS}" = "degraded" ]; then
  REASON=$(echo "${BODY}" | jq -r '.data.output.reason // .data.output.message // "unknown"')
  echo "FAIL: invoke degraded (${REASON}). This is a non-live (degraded) turn."
  exit 1
fi

# Assert the sentinel made it through the real gateway agent (not a mock/dry-run).
REPLY=$(echo "${BODY}" | jq -r '
  ([.data.output.json_events[]? | select(.item.type=="agent_message") | .item.text]
   | join(" "))
  // (.data.output.text // .data.answer // "")')
if ! printf '%s' "${REPLY}" | grep -q "${SENTINEL}"; then
  echo "FAIL: sentinel '${SENTINEL}' not found in agent reply."
  echo "      reply was: ${REPLY}"
  exit 1
fi
echo "OK: live agent turn returned sentinel '${SENTINEL}'"

echo ""
echo "=== 3/4 transport is real CLI (not mock/REST) ==="
TRANSPORT=$(echo "${BODY}" | jq -r '.data.output.transport // "unknown"')
if [ "${TRANSPORT}" != "cli" ]; then
  echo "FAIL: transport='${TRANSPORT}', expected 'cli'"
  exit 1
fi
echo "OK: transport=cli"

echo ""
echo "=== 4/4 live OpenResponses stream (sentinel: ${SENTINEL}) ==="
# Same rule as the invoke above: a stream is one live turn and never retried.
STREAM_PAYLOAD=$(jq -nc --arg p "Reply with exactly: ${SENTINEL}" \
  '{prompt: $p, mode: "user", metadata: {session_user: "openclaw-responses-live-smoke"}}')
STREAM_EVENTS=$(curl -fsS -N -m 120 -X POST \
  "${BASE_URL}/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream" \
  -H "Content-Type: application/json" \
  -H "X-Operator-Id: ${OPERATOR_ID}" \
  -d "${STREAM_PAYLOAD}")
STREAM_PROOF=$(printf '%s\n' "${STREAM_EVENTS}" | python3 -c '
import json
import sys

events = []
for line in sys.stdin:
    if not line.startswith("data: "):
        continue
    payload = line[len("data: "):].strip()
    if payload == "[DONE]":
        continue
    events.append(json.loads(payload))

errors = [event for event in events if event.get("type") == "error"]
if errors:
    raise SystemExit(f"OpenResponses stream returned error: {errors[-1]}")
done = [event for event in events if event.get("type") == "done"]
if len(done) != 1:
    raise SystemExit(f"Expected one terminal stream event, got: {events}")
reply = str(done[0].get("text") or "")
if not reply.strip():
    raise SystemExit("OpenResponses stream completed without assistant text")
if "OPENCLAW_LIVE" not in reply:
    raise SystemExit(f"OpenResponses stream missed sentinel: {reply!r}")
transport = done[0].get("transport")
if transport != "responses_http":
    raise SystemExit(f"Unexpected stream transport: {transport!r}")
print(json.dumps({"transport": transport, "reply_bytes": len(reply.encode("utf-8"))}))
')
echo "${STREAM_PROOF}" | jq .
echo "OK: OpenResponses stream returned a non-empty assistant turn"

echo ""
echo "=== openclaw provider live smoke PASSED ==="

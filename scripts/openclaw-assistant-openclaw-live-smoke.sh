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

echo "Adapter base URL: ${BASE_URL}"

echo ""
echo "=== 1/4 openclaw provider readiness (auth_probe=true) ==="
READINESS=$(curl -fsS -m 20 "${BASE_URL}/api/openclaw-adapter/assistant/readiness/openclaw?auth_probe=true")
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
if done[0].get("transport") != "responses_http":
    raise SystemExit(f"Unexpected stream transport: {done[0].get(\"transport\")!r}")
print(json.dumps({"transport": done[0]["transport"], "reply_bytes": len(reply.encode("utf-8"))}))
')
echo "${STREAM_PROOF}" | jq .
echo "OK: OpenResponses stream returned a non-empty assistant turn"

echo ""
echo "=== openclaw provider live smoke PASSED ==="

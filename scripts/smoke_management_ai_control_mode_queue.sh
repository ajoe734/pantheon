#!/usr/bin/env bash
set -euo pipefail

BFF_BASE_URL="${BFF_BASE_URL:-https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io}"
BFF_AUTH_TOKEN="${BFF_AUTH_TOKEN:-pantheon-dev-browser:admin:mfa:assistant.kernel.debug,assistant.kernel.repair}"
CONTROL_PASSPHRASE="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE:-${CONTROL_MODE_PASSPHRASE:-}}"
SESSION_ID="${SESSION_ID:-mgmt-ai-control-mode-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
TASK_OWNER="${TASK_OWNER:-Codex}"
TASK_REVIEWER="${TASK_REVIEWER:-Claude}"

if [ -z "${CONTROL_PASSPHRASE}" ]; then
  echo "ERROR: set PANTHEON_ASSISTANT_CONTROL_PASSPHRASE to the existing control-mode passphrase." >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required." >&2
  exit 2
fi

request_tmp="$(mktemp)"
response_tmp="$(mktemp)"
trap 'rm -f "${request_tmp}" "${response_tmp}"' EXIT

curl_json() {
  local method="$1"
  local path="$2"
  local body_file="${3:-}"
  local http_code

  if [ -n "${body_file}" ]; then
    http_code="$(
      curl -sS -o "${response_tmp}" -w '%{http_code}' --max-time 90 \
        -X "${method}" \
        -H "Authorization: Bearer ${BFF_AUTH_TOKEN}" \
        -H "Content-Type: application/json" \
        --data @"${body_file}" \
        "${BFF_BASE_URL}${path}"
    )"
  else
    http_code="$(
      curl -sS -o "${response_tmp}" -w '%{http_code}' --max-time 30 \
        -X "${method}" \
        -H "Authorization: Bearer ${BFF_AUTH_TOKEN}" \
        "${BFF_BASE_URL}${path}"
    )"
  fi

  printf '%s' "${http_code}"
}

echo "=== Management AI control-mode queue smoke ==="
echo "bff=${BFF_BASE_URL}"
echo "session=${SESSION_ID}"
echo "auth_token=configured"
echo "passphrase=configured"

health_code="$(curl_json GET /health)"
if [ "${health_code}" != "200" ]; then
  echo "ERROR: /health returned HTTP ${health_code}" >&2
  cat "${response_tmp}" >&2
  exit 1
fi
echo "health=ok"

mode_code="$(curl_json GET /bff/assistant/mode)"
if [ "${mode_code}" != "200" ]; then
  echo "ERROR: /bff/assistant/mode returned HTTP ${mode_code}" >&2
  cat "${response_tmp}" >&2
  exit 1
fi
kernel_enabled="$(jq -r '.data.kernel_enabled // false' "${response_tmp}")"
configured="$(jq -r '(.data.control_mode.configured // .data.control_mode.active // false)' "${response_tmp}")"
echo "kernel_enabled=${kernel_enabled}"
echo "control_passphrase_configured=${configured}"
if [ "${kernel_enabled}" != "true" ] || [ "${configured}" != "true" ]; then
  echo "ERROR: kernel mode or control passphrase is not configured." >&2
  exit 1
fi

jq -n \
  --arg passphrase "${CONTROL_PASSPHRASE}" \
  --arg session "${SESSION_ID}" \
  '{
    passphrase: $passphrase,
    mode: "kernel_repair",
    reason: "Management AI SA/SD queue smoke",
    ttlSeconds: 900,
    idleTtlSeconds: 300,
    managementSessionId: $session
  }' >"${request_tmp}"
activate_code="$(curl_json POST /bff/assistant/control-mode/activate "${request_tmp}")"
if [ "${activate_code}" != "202" ]; then
  echo "ERROR: control-mode activation returned HTTP ${activate_code}" >&2
  jq '{error:.error, meta:.meta}' "${response_tmp}" >&2 || cat "${response_tmp}" >&2
  exit 1
fi
echo "control_mode=$(jq -r '.data.mode // "unknown"' "${response_tmp}")"

jq -n \
  --arg session "${SESSION_ID}" \
  --arg owner "${TASK_OWNER}" \
  --arg reviewer "${TASK_REVIEWER}" \
  '{
    conversationId: $session,
    featureSummary: "Smoke Management AI SA/SD generation and supervisor DevTaskPacket queueing from the frontend control-mode workflow.",
    affectedModules: [
      "frontend-management-ai",
      "assistant-control-mode",
      "assistant-dev-bridge",
      "openclaw-gateway-adapter"
    ],
    proposedOwner: $owner,
    proposedReviewer: $reviewer,
    emitTaskPacket: true,
    queueTaskPacket: true,
    extraContext: {
      smoke: true,
      source: "scripts/smoke_management_ai_control_mode_queue.sh"
    }
  }' >"${request_tmp}"
generate_code="$(curl_json POST /bff/assistant/dev-docs/generate "${request_tmp}")"
if [ "${generate_code}" != "201" ]; then
  echo "ERROR: dev-docs generate returned HTTP ${generate_code}" >&2
  jq '{error:.error, meta:.meta}' "${response_tmp}" >&2 || cat "${response_tmp}" >&2
  exit 1
fi

packet_id="$(jq -r '.data.packetId // "unknown"' "${response_tmp}")"
queued="$(jq -r '.meta.taskPacketQueued // false' "${response_tmp}")"
queue_path="$(jq -r '.meta.taskPacketQueueReceipt.paths.pending // .meta.taskPacketQueueReceipt.path // "unknown"' "${response_tmp}")"
echo "packet_id=${packet_id}"
echo "task_packet_queued=${queued}"
echo "queue_path=${queue_path}"
if [ "${queued}" != "true" ]; then
  echo "ERROR: task packet was not queued." >&2
  jq '.meta.taskPacketQueueReceipt // .meta' "${response_tmp}" >&2
  exit 1
fi

orchestrator_code="$(curl_json GET /bff/assistant/orchestrator/status)"
if [ "${orchestrator_code}" != "200" ]; then
  echo "ERROR: orchestrator status returned HTTP ${orchestrator_code}" >&2
  cat "${response_tmp}" >&2
  exit 1
fi
jq '{
  supervisor: .data.supervisor.lifecycle,
  provider: .data.providerReadiness.status,
  assistantDevBridge: .data.assistantDevBridge
}' "${response_tmp}"

echo "=== smoke complete ==="

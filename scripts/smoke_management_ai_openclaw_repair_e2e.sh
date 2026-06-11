#!/usr/bin/env bash
set -euo pipefail

BFF_BASE_URL="${BFF_BASE_URL:-https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io}"
BFF_AUTH_TOKEN="${BFF_AUTH_TOKEN:-pantheon-dev-browser:admin:mfa:assistant.kernel.debug,assistant.kernel.repair}"
CONTROL_PASSPHRASE="${PANTHEON_ASSISTANT_CONTROL_PASSPHRASE:-${CONTROL_MODE_PASSPHRASE:-}}"
SESSION_ID="${SESSION_ID:-mgmt-ai-openclaw-repair-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
TASK_ID="${TASK_ID:-MGMT-AI-OPENCLAW-REPAIR-SMOKE-$(date -u +%Y%m%dT%H%M%SZ)}"
REPAIR_REPO_KEY="${REPAIR_REPO_KEY:-execute-plans}"
REPAIR_MERGE_TARGET="${REPAIR_MERGE_TARGET:-dev}"
REPAIR_SCOPE="${REPAIR_SCOPE:-tmp/management-ai-openclaw-smoke}"
TASK_OWNER="${TASK_OWNER:-assistant-supervisor}"
POLL_SECONDS="${POLL_SECONDS:-90}"
PROVIDER_TIMEOUT_SECONDS="${PROVIDER_TIMEOUT_SECONDS:-240}"

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
status_tmp="$(mktemp)"
activated="false"

deactivate_control_mode() {
  if [ "${activated}" = "true" ]; then
    jq -n '{reason: "management_ai_openclaw_repair_smoke_cleanup"}' >"${request_tmp}"
    curl -sS -o /dev/null --max-time 30 \
      -X POST \
      -H "Authorization: Bearer ${BFF_AUTH_TOKEN}" \
      -H "Content-Type: application/json" \
      --data @"${request_tmp}" \
      "${BFF_BASE_URL}/bff/assistant/control-mode/deactivate" || true
  fi
}

cleanup() {
  deactivate_control_mode
  rm -f "${request_tmp}" "${response_tmp}" "${status_tmp}"
}
trap cleanup EXIT

curl_json() {
  local method="$1"
  local path="$2"
  local body_file="${3:-}"
  local timeout="${4:-90}"
  local idempotency_key="${5:-}"
  local http_code
  local headers=(-H "Authorization: Bearer ${BFF_AUTH_TOKEN}")
  if [ -n "${idempotency_key}" ]; then
    headers+=(-H "Idempotency-Key: ${idempotency_key}")
  fi

  if [ -n "${body_file}" ]; then
    http_code="$(
      curl -sS -o "${response_tmp}" -w '%{http_code}' --max-time "${timeout}" \
        -X "${method}" \
        "${headers[@]}" \
        -H "Content-Type: application/json" \
        --data @"${body_file}" \
        "${BFF_BASE_URL}${path}"
    )"
  else
    http_code="$(
      curl -sS -o "${response_tmp}" -w '%{http_code}' --max-time "${timeout}" \
        -X "${method}" \
        "${headers[@]}" \
        "${BFF_BASE_URL}${path}"
    )"
  fi

  printf '%s' "${http_code}"
}

scope_json_filter='($scope | split(";") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0)))'

echo "=== Management AI OpenClaw repair E2E smoke ==="
echo "bff=${BFF_BASE_URL}"
echo "session=${SESSION_ID}"
echo "task_id=${TASK_ID}"
echo "repo_key=${REPAIR_REPO_KEY}"
echo "merge_target=${REPAIR_MERGE_TARGET}"
echo "scope=${REPAIR_SCOPE}"
echo "auth_token=configured"
echo "passphrase=configured"

health_code="$(curl_json GET /health "" 30)"
if [ "${health_code}" != "200" ]; then
  echo "ERROR: /health returned HTTP ${health_code}" >&2
  cat "${response_tmp}" >&2
  exit 1
fi
echo "health=ok"

mode_code="$(curl_json GET /bff/assistant/mode "" 30)"
if [ "${mode_code}" != "200" ]; then
  echo "ERROR: /bff/assistant/mode returned HTTP ${mode_code}" >&2
  cat "${response_tmp}" >&2
  exit 1
fi
kernel_enabled="$(jq -r '.data.kernel_enabled // false' "${response_tmp}")"
configured="$(jq -r '.data.control_mode.configured // false' "${response_tmp}")"
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
    reason: "Management AI OpenClaw repair E2E smoke",
    ttlSeconds: 900,
    idleTtlSeconds: 300,
    managementSessionId: $session
  }' >"${request_tmp}"
activate_code="$(curl_json POST /bff/assistant/control-mode/activate "${request_tmp}" 90)"
if [ "${activate_code}" != "202" ]; then
  echo "ERROR: control-mode activation returned HTTP ${activate_code}" >&2
  jq '{error:.error, meta:.meta}' "${response_tmp}" >&2 || cat "${response_tmp}" >&2
  exit 1
fi
activated="true"
echo "control_mode=$(jq -r '.data.mode // "unknown"' "${response_tmp}")"

jq -n \
  --arg task "${TASK_ID}" \
  --arg repo "${REPAIR_REPO_KEY}" \
  --arg merge "${REPAIR_MERGE_TARGET}" \
  --arg scope "${REPAIR_SCOPE}" \
  --arg reason "Management AI OpenClaw repair E2E smoke" \
  "{
    taskId: \$task,
    repoKey: \$repo,
    declaredScope: ${scope_json_filter},
    expectedBranch: (\"task/\" + \$task),
    mergeTarget: \$merge,
    reason: \$reason
  }" >"${request_tmp}"
prepare_code="$(curl_json POST /bff/assistant/repair-worktrees/prepare "${request_tmp}" 180)"
if [ "${prepare_code}" != "201" ]; then
  echo "ERROR: repair worktree prepare returned HTTP ${prepare_code}" >&2
  jq '{error:.error, meta:.meta}' "${response_tmp}" >&2 || cat "${response_tmp}" >&2
  exit 1
fi
repair_json="$(jq -c '.data.repair // .data.repairMetadata' "${response_tmp}")"
task_worktree="$(jq -r '.data.repair.task_worktree // .data.repair.taskWorktree // empty' "${response_tmp}")"
workflow_clean="$(jq -r '.data.workflow.clean // false' "${response_tmp}")"
echo "task_worktree=${task_worktree}"
echo "repair_workflow_clean=${workflow_clean}"
if [ -z "${task_worktree}" ] || [ "${workflow_clean}" != "true" ]; then
  echo "ERROR: prepared repair worktree missing or not clean." >&2
  jq '.data' "${response_tmp}" >&2
  exit 1
fi

sentinel_dir="${REPAIR_SCOPE%%;*}"
sentinel_rel="${SMOKE_SENTINEL_REL:-${sentinel_dir%/}/${TASK_ID}.md}"
sentinel_abs="${task_worktree%/}/${sentinel_rel}"

jq -n \
  --arg session "${SESSION_ID}" \
  --arg task "${TASK_ID}" \
  --arg sentinel "${sentinel_rel}" \
  --argjson repair "${repair_json}" \
  '{
    conversationId: $session,
    focus: "all",
    useAssistantProvider: true,
    question: (
      "OpenClaw repair smoke. Create or overwrite the repo-relative file `" + $sentinel + "` " +
      "inside the prepared task worktree. The file must contain exactly two lines: " +
      "`management-ai-openclaw-repair-smoke` and `task_id=" + $task + "`. " +
      "Do not modify any other file. Do not commit, push, deploy, or touch broker/live/capital/runtime state. " +
      "After writing, reply with the file path and a concise status."
    ),
    openclaw: {repair: $repair}
  }' >"${request_tmp}"
ask_code="$(curl_json POST /bff/management/nl/ask "${request_tmp}" "${PROVIDER_TIMEOUT_SECONDS}" "mgmt-ai-openclaw-repair-${TASK_ID}")"
if [ "${ask_code}" != "202" ]; then
  echo "ERROR: Management AI OpenClaw repair ask returned HTTP ${ask_code}" >&2
  jq '{error:.error, meta:.meta}' "${response_tmp}" >&2 || cat "${response_tmp}" >&2
  exit 1
fi
provider_status="$(jq -r '.data.providerStatus.status // .data.provider_status.status // "unknown"' "${response_tmp}")"
provider_used="$(jq -r '.data.providerStatus.used // .data.provider_status.used // false' "${response_tmp}")"
workspace_class="$(jq -r '.data.providerStatus.workspaceClass // .data.provider_status.workspaceClass // .data.providerStatus.workspace_class // .data.provider_status.workspace_class // "unknown"' "${response_tmp}")"
echo "provider_status=${provider_status}"
echo "provider_used=${provider_used}"
echo "workspace_class=${workspace_class}"
if [ "${provider_status}" != "completed" ] || [ "${provider_used}" != "true" ] || [ "${workspace_class}" != "task_worktree" ]; then
  echo "ERROR: provider did not complete in task_worktree workspace." >&2
  jq '{providerStatus:.data.providerStatus, provider_status:.data.provider_status, answer:.data.answer}' "${response_tmp}" >&2
  exit 1
fi
if [ ! -f "${sentinel_abs}" ]; then
  echo "ERROR: sentinel file was not written at ${sentinel_abs}" >&2
  jq -r '.data.answer // empty' "${response_tmp}" >&2
  exit 1
fi
if ! grep -qx 'management-ai-openclaw-repair-smoke' "${sentinel_abs}" || ! grep -qx "task_id=${TASK_ID}" "${sentinel_abs}"; then
  echo "ERROR: sentinel file content did not match expected smoke marker." >&2
  sed -n '1,20p' "${sentinel_abs}" >&2
  exit 1
fi
echo "sentinel_written=${sentinel_rel}"
git -C "${task_worktree}" status --short -- "${sentinel_rel}" || true

jq -n \
  --arg session "${SESSION_ID}" \
  --arg owner "${TASK_OWNER}" \
  --arg sentinel "${sentinel_rel}" \
  '{
    conversationId: $session,
    featureSummary: "Smoke Management AI OpenClaw repair worktree write, SA/SD generation, and supervisor DevTaskPacket queueing.",
    affectedModules: [
      "execute-plans:management-ai",
      "pantheon:bff-assistant",
      "pantheon:openclaw-dev-bridge",
      $sentinel
    ],
    proposedOwner: $owner,
    proposedReviewer: "Supervisor",
    archive: true,
    emitTaskPacket: true,
    queueTaskPacket: true,
    extraContext: {
      smoke: true,
      source: "scripts/smoke_management_ai_openclaw_repair_e2e.sh",
      repairSentinel: $sentinel
    }
  }' >"${request_tmp}"
generate_code="$(curl_json POST /bff/assistant/dev-docs/generate "${request_tmp}" 120)"
if [ "${generate_code}" != "201" ]; then
  echo "ERROR: dev-docs generate returned HTTP ${generate_code}" >&2
  jq '{error:.error, meta:.meta}' "${response_tmp}" >&2 || cat "${response_tmp}" >&2
  exit 1
fi
dev_doc_packet_id="$(jq -r '.data.packetId // "unknown"' "${response_tmp}")"
task_packet_id="$(jq -r '.meta.taskPacket.packetId // .meta.taskPacket.packet_id // .data.packetId // "unknown"' "${response_tmp}")"
queued="$(jq -r '.meta.taskPacketQueued // false' "${response_tmp}")"
queue_path="$(jq -r '.meta.taskPacketQueueReceipt.paths.pending // .meta.taskPacketQueueReceipt.path // "unknown"' "${response_tmp}")"
echo "dev_doc_packet_id=${dev_doc_packet_id}"
echo "task_packet_id=${task_packet_id}"
echo "task_packet_queued=${queued}"
echo "queue_path=${queue_path}"
if [ "${queued}" != "true" ]; then
  echo "ERROR: task packet was not queued." >&2
  jq '.meta.taskPacketQueueReceipt // .meta' "${response_tmp}" >&2
  exit 1
fi

deadline=$((SECONDS + POLL_SECONDS))
receipt_status=""
while [ "${SECONDS}" -le "${deadline}" ]; do
  status_code="$(curl_json GET /bff/assistant/orchestrator/status "" 30)"
  if [ "${status_code}" = "200" ]; then
    cp "${response_tmp}" "${status_tmp}"
    receipt_status="$(jq -r --arg id "${task_packet_id}" '.data.assistantDevBridge.recentReceipts[]? | select(.packetId == $id) | .status' "${status_tmp}" | tail -n 1)"
    pending_count="$(jq -r '.data.assistantDevBridge.inbox.pendingCount // -1' "${status_tmp}")"
    echo "bridge_poll receipt=${receipt_status:-none} pending=${pending_count}"
    if [ "${receipt_status}" = "processed" ]; then
      break
    fi
  fi
  sleep 5
done

if [ "${receipt_status}" != "processed" ]; then
  echo "ERROR: supervisor did not process queued task packet ${task_packet_id} within ${POLL_SECONDS}s." >&2
  jq '.data.assistantDevBridge' "${status_tmp}" >&2 || true
  exit 1
fi

jq --arg id "${task_packet_id}" '{
  supervisor: .data.supervisor.lifecycle,
  provider: .data.providerReadiness.status,
  assistantDevBridge: .data.assistantDevBridge,
  receipt: (.data.assistantDevBridge.recentReceipts[]? | select(.packetId == $id))
}' "${status_tmp}"

echo "=== openclaw repair E2E smoke complete ==="

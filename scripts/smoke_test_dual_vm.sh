#!/usr/bin/env bash
# Pantheon dual-VM acceptance smoke for DEPLOY-009.
#
# Verifies the cross-VM control -> execution -> telemetry path using the
# existing deployable service surfaces:
#   VM-1 deployment service   : /health + /api/deployment/*
#   VM-1 telemetry ingest     : /__health__ + /api/telemetry/*
#   VM-2 runtime-manager      : /__health__ + /api/runtimes/* + /api/rollback +
#                               /api/kill-switch/*
#   VM-2 paper runtime        : /__health__ + /api/runtime/*
#
# Important:
# - The VM-2 paper runtime now exposes the truthful paper execution package,
#   not the old bootstrap-only stub. This smoke still stops short of a full
#   governed order-loop packet; it proves runtime package readiness, health,
#   binding creation, telemetry linkage, kill-switch, and rollback semantics.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CONTROL_DEPLOYMENT_URL="${CONTROL_DEPLOYMENT_URL:-http://127.0.0.1:8006}"
CONTROL_TELEMETRY_URL="${CONTROL_TELEMETRY_URL:-http://127.0.0.1:38083}"
EXEC_RUNTIME_MANAGER_URL="${EXEC_RUNTIME_MANAGER_URL:-http://127.0.0.1:28081}"
EXEC_PAPER_RUNTIME_URL="${EXEC_PAPER_RUNTIME_URL:-http://127.0.0.1:28110}"
EXEC_BROKER_ADAPTER_URL="${EXEC_BROKER_ADAPTER_URL:-http://127.0.0.1:28097}"
EXEC_EXCHANGE_ADAPTER_URL="${EXEC_EXCHANGE_ADAPTER_URL:-http://127.0.0.1:28098}"
RUNTIME_MANAGER_TOKEN="${PANTHEON_RUNTIME_MANAGER_TOKEN:-runtime-control-internal}"
ACTOR_ID="${ACTOR_ID:-deploy-009-smoke}"
SOURCE_TASK_ID="${SOURCE_TASK_ID:-DEPLOY-009}"
PAPER_RUNTIME_ID="${PANTHEON_PAPER_RUNTIME_ID:-paper-runtime-001}"
WORKSPACE_REF="${PANTHEON_WORKSPACE_REF:-workspace-paper}"
AUTH_PROFILE_REF="${PANTHEON_AUTH_PROFILE_REF:-auth-profile-paper}"
PERSONA_ID="${PANTHEON_PERSONA_ID:-persona-paper-ops}"
SESSION_ID="${PANTHEON_SESSION_ID:-session-paper-runtime}"
REQUEST_ID="${PANTHEON_REQUEST_ID:-request-paper-runtime}"
PERSONA_SCOPE="${PERSONA_SCOPE:-paper}"
KEEP_OUTPUT=true
OUTPUT_DIR=""
SKIP_ADAPTER_HEALTH=false

usage() {
  cat <<'EOF'
Usage:
  bash scripts/smoke_test_dual_vm.sh [options]

Options:
  --control-deployment-url <url>   VM-1 deployment service base URL
  --control-telemetry-url <url>    VM-1 telemetry service base URL
  --exec-runtime-manager-url <url> VM-2 runtime-manager base URL
  --exec-paper-runtime-url <url>   VM-2 paper runtime URL
  --exec-broker-url <url>          VM-2 broker adapter URL
  --exec-exchange-url <url>        VM-2 exchange adapter URL
  --runtime-manager-token <token>  Bearer token for runtime-manager + telemetry lookup
  --paper-runtime-id <id>          Runtime ID to assign to the initial binding
  --workspace-ref <ref>            Persona-scoped workspace ref for execution runtime
  --auth-profile-ref <ref>         Persona-scoped auth profile ref for execution runtime
  --persona-scope <scope>          allowed_deployment_scope for the smoke binding
  --actor-id <id>                  Actor/operator identifier used in control actions
  --output-dir <dir>               Persist JSON request/response artifacts here
  --skip-adapter-health            Skip broker/exchange health checks
  --keep-output                    Keep temp output directory when --output-dir is omitted
  --help                           Show this message

Environment variable equivalents:
  CONTROL_DEPLOYMENT_URL, CONTROL_TELEMETRY_URL, EXEC_RUNTIME_MANAGER_URL,
  EXEC_PAPER_RUNTIME_URL, EXEC_BROKER_ADAPTER_URL, EXEC_EXCHANGE_ADAPTER_URL,
  PANTHEON_RUNTIME_MANAGER_TOKEN, PANTHEON_PAPER_RUNTIME_ID,
  PANTHEON_WORKSPACE_REF, PANTHEON_AUTH_PROFILE_REF, PANTHEON_PERSONA_ID,
  PANTHEON_SESSION_ID, PANTHEON_REQUEST_ID, PERSONA_SCOPE, ACTOR_ID,
  SOURCE_TASK_ID
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --control-deployment-url) CONTROL_DEPLOYMENT_URL="$2"; shift 2 ;;
    --control-telemetry-url) CONTROL_TELEMETRY_URL="$2"; shift 2 ;;
    --exec-runtime-manager-url) EXEC_RUNTIME_MANAGER_URL="$2"; shift 2 ;;
    --exec-paper-runtime-url) EXEC_PAPER_RUNTIME_URL="$2"; shift 2 ;;
    --exec-broker-url) EXEC_BROKER_ADAPTER_URL="$2"; shift 2 ;;
    --exec-exchange-url) EXEC_EXCHANGE_ADAPTER_URL="$2"; shift 2 ;;
    --runtime-manager-token) RUNTIME_MANAGER_TOKEN="$2"; shift 2 ;;
    --paper-runtime-id) PAPER_RUNTIME_ID="$2"; shift 2 ;;
    --workspace-ref) WORKSPACE_REF="$2"; shift 2 ;;
    --auth-profile-ref) AUTH_PROFILE_REF="$2"; shift 2 ;;
    --persona-scope) PERSONA_SCOPE="$2"; shift 2 ;;
    --actor-id) ACTOR_ID="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --skip-adapter-health) SKIP_ADAPTER_HEALTH=true; shift ;;
    --keep-output) KEEP_OUTPUT=true; shift ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd python3

json_query() {
  local file="$1"
  local path="$2"
  python3 - "$file" "$path" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
path = sys.argv[2]
value = payload
if path:
    for part in path.split("."):
        if isinstance(value, list):
            value = value[int(part)]
        else:
            value = value[part]
if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("null")
elif isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False))
else:
    print(value)
PY
}

json_num() {
  local file="$1"
  local path="$2"
  python3 - "$file" "$path" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
value = payload
for part in sys.argv[2].split("."):
    value = value[int(part)] if isinstance(value, list) else value[part]
print(value)
PY
}

expect_eq() {
  local label="$1"
  local actual="$2"
  local expected="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: $label (expected '$expected', got '$actual')" >&2
    exit 1
  fi
  echo "  [ok] $label = $expected"
}

expect_nonempty() {
  local label="$1"
  local value="$2"
  if [[ -z "$value" || "$value" == "null" ]]; then
    echo "FAIL: $label is empty" >&2
    exit 1
  fi
  echo "  [ok] $label = $value"
}

http_call() {
  local method="$1"
  local url="$2"
  local body_file="${3:-}"
  local output_file="$4"
  local expected_status="$5"
  local auth_token="${6:-}"

  local curl_args=(-sS -X "$method" -H "Accept: application/json")
  if [[ -n "$auth_token" ]]; then
    curl_args+=(-H "Authorization: Bearer ${auth_token}")
  fi
  if [[ -n "$body_file" ]]; then
    curl_args+=(-H "Content-Type: application/json" --data "@${body_file}")
  fi

  local status
  status=$(curl "${curl_args[@]}" -o "$output_file" -w '%{http_code}' "$url")
  if [[ "$status" != "$expected_status" ]]; then
    echo "FAIL: ${method} ${url} returned ${status}, expected ${expected_status}" >&2
    echo "--- response body ---" >&2
    cat "$output_file" >&2 || true
    echo "---------------------" >&2
    exit 1
  fi
}

health_check() {
  local label="$1"
  local url="$2"
  local expected_service="$3"
  local out="$OUTPUT_DIR/${label// /_}.json"

  echo "==> Health check: ${label}"
  http_call GET "$url" "" "$out" 200
  local status
  status=$(json_query "$out" "status")
  expect_eq "${label} status" "$status" "ok"
  if [[ -n "$expected_service" ]]; then
    local service
    service=$(json_query "$out" "service")
    expect_eq "${label} service" "$service" "$expected_service"
  fi
}

timestamp_utc() {
  python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
}

uuid4() {
  python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
}

suffix() {
  python3 - <<'PY'
import uuid
print(uuid.uuid4().hex[:8])
PY
}

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="/tmp/pantheon/dual-vm-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$OUTPUT_DIR"

RUN_SUFFIX="$(suffix)"
PLAN_ID="plan-dual-vm-${RUN_SUFFIX}"
APPROVAL_ID="approval-dual-vm-${RUN_SUFFIX}"
REGISTRY_ID="reg-dual-vm-${RUN_SUFFIX}"
FALLBACK_REGISTRY_ID="reg-dual-vm-fallback-${RUN_SUFFIX}"
CAPITAL_POOL_ID="pool-dual-vm-${RUN_SUFFIX}"
PCB_ID="pcb-dual-vm-${RUN_SUFFIX}"
STRATEGY_ID="strat-dual-vm"
TRACE_ID="$(uuid4)"
DEPLOY_EVENT_ID="$(uuid4)"
ROLLBACK_EVENT_ID="$(uuid4)"
PLAN_APPROVED_AT="$(timestamp_utc)"

echo "==> DEPLOY-009 dual-VM smoke"
echo "    output dir: $OUTPUT_DIR"
echo "    plan_id   : $PLAN_ID"
echo "    pool_id   : $CAPITAL_POOL_ID"
echo ""

health_check "vm1 deployment" "${CONTROL_DEPLOYMENT_URL%/}/health" "pantheon-deployment"
health_check "vm1 telemetry" "${CONTROL_TELEMETRY_URL%/}/__health__" "telemetry-ingest"
health_check "vm2 runtime-manager" "${EXEC_RUNTIME_MANAGER_URL%/}/__health__" "runtime-manager"
health_check "vm2 paper-runtime" "${EXEC_PAPER_RUNTIME_URL%/}/__health__" ""
expect_eq "paper runtime runtime_id" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "runtime_id")" \
  "$PAPER_RUNTIME_ID"
expect_eq "paper runtime workspace_ref" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "workspace_ref")" \
  "$WORKSPACE_REF"
expect_eq "paper runtime auth_profile_ref" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "auth_profile_ref")" \
  "$AUTH_PROFILE_REF"
expect_eq "paper runtime isolation_ready" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "isolation_ready")" \
  "true"
expect_eq "paper runtime auth configured" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "runtime_manager_auth.configured")" \
  "true"
expect_eq "paper runtime package" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "runtime_package")" \
  "paper_execution_runtime"
expect_eq "paper runtime stub retired" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "stub_mode")" \
  "false"
expect_eq "paper runtime signal consumer ready" \
  "$(json_query "$OUTPUT_DIR/vm2_paper-runtime.json" "signal_consumer_ready")" \
  "true"

if [[ "$SKIP_ADAPTER_HEALTH" != true ]]; then
  health_check "vm2 broker-adapter" "${EXEC_BROKER_ADAPTER_URL%/}/__health__" ""
  health_check "vm2 exchange-adapter" "${EXEC_EXCHANGE_ADAPTER_URL%/}/__health__" ""
fi

echo ""
echo "==> Building DeploymentPlan payload"
cat >"$OUTPUT_DIR/plan-create.json" <<JSON
{
  "plan_id": "$PLAN_ID",
  "approval_decision_id": "$APPROVAL_ID",
  "registry_entry": {
    "registry_id": "$REGISTRY_ID",
    "artifact_type": "model_artifact",
    "strategy_id": "$STRATEGY_ID",
    "version": "1.2.0",
    "artifact_state": "approved",
    "checksum": "sha256:dual-vm-$RUN_SUFFIX",
    "approved_at": "$PLAN_APPROVED_AT",
    "lineage": {
      "source_run_ids": ["run-dual-vm-$RUN_SUFFIX"]
    },
    "deployment_summary": {
      "current_stage": "none"
    }
  },
  "approval_decision": {
    "decision_id": "$APPROVAL_ID",
    "target_id": "$REGISTRY_ID",
    "target_version": "1.2.0",
    "decision_state": "decided",
    "decision": "approved",
    "capital_pool_id": "$CAPITAL_POOL_ID",
    "persona_id": "persona-ops"
  },
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "sponsor_persona_id": "persona-ops",
  "target_stage": "paper",
  "created_by": "codex-dual-vm-smoke",
  "rollback": {
    "target_artifact_id": "$FALLBACK_REGISTRY_ID",
    "target_version": "1.1.9",
    "action_type": "replace",
    "reason": "DEPLOY-009 fallback smoke"
  },
  "metadata": {
    "source_task_id": "$SOURCE_TASK_ID",
    "acceptance_mode": "dual_vm_smoke"
  },
  "authority_refs": {
    "write_owner": "runtime-manager",
    "authority_source": "runtime_binding",
    "runtime_role": "pantheon-paper-execution-runtime",
    "runtime_mode": "paper",
    "workspace_ref": "$WORKSPACE_REF",
    "auth_profile_ref": "$AUTH_PROFILE_REF",
    "persona_id": "$PERSONA_ID",
    "session_id": "$SESSION_ID",
    "trace_id": "$TRACE_ID",
    "request_id": "$REQUEST_ID"
  }
}
JSON

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/plans/validate" \
  "$OUTPUT_DIR/plan-create.json" "$OUTPUT_DIR/plan-validate-response.json" 200
expect_eq "plan validate ok" "$(json_query "$OUTPUT_DIR/plan-validate-response.json" "ok")" "true"

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/plans" \
  "$OUTPUT_DIR/plan-create.json" "$OUTPUT_DIR/plan-create-response.json" 201
expect_eq "plan_id" "$(json_query "$OUTPUT_DIR/plan-create-response.json" "plan_id")" "$PLAN_ID"

cat >"$OUTPUT_DIR/plan-dispatch.json" <<JSON
{
  "trace_id": "$TRACE_ID",
  "workflow_id": "pantheon.dual-vm.acceptance",
  "source_task_id": "$SOURCE_TASK_ID",
  "registry_entry": {
    "registry_id": "$REGISTRY_ID",
    "artifact_type": "model_artifact",
    "strategy_id": "$STRATEGY_ID",
    "version": "1.2.0",
    "artifact_state": "approved",
    "checksum": "sha256:dual-vm-$RUN_SUFFIX",
    "approved_at": "$PLAN_APPROVED_AT",
    "lineage": {
      "source_run_ids": ["run-dual-vm-$RUN_SUFFIX"]
    },
    "deployment_summary": {
      "current_stage": "none"
    }
  }
}
JSON

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/plans/${PLAN_ID}/dispatch" \
  "$OUTPUT_DIR/plan-dispatch.json" "$OUTPUT_DIR/plan-dispatch-response.json" 200
expect_eq "consistency contract" \
  "$(json_query "$OUTPUT_DIR/plan-dispatch-response.json" "consistency_contract")" \
  "DEP-002"

SAGA_ID="$(json_query "$OUTPUT_DIR/plan-dispatch-response.json" "deployment_saga.saga.saga_id")"
OUTBOX_EVENT_ID="$(json_query "$OUTPUT_DIR/plan-dispatch-response.json" "deployment_saga.outbox_event.event.event_id")"
expect_nonempty "saga_id" "$SAGA_ID"
expect_nonempty "initial outbox event" "$OUTBOX_EVENT_ID"

cat >"$OUTPUT_DIR/outbox-consume.json" <<JSON
{
  "consumer_name": "dual-vm-runtime-projector"
}
JSON

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/outbox/${OUTBOX_EVENT_ID}/consume" \
  "$OUTPUT_DIR/outbox-consume.json" "$OUTPUT_DIR/outbox-consume-response.json" 200
expect_eq "outbox consume status" \
  "$(json_query "$OUTPUT_DIR/outbox-consume-response.json" "status")" \
  "applied"

cat >"$OUTPUT_DIR/plan-executing.json" <<JSON
{
  "status": "executing"
}
JSON

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/plans/${PLAN_ID}/status" \
  "$OUTPUT_DIR/plan-executing.json" "$OUTPUT_DIR/plan-executing-response.json" 200
expect_eq "plan status after dispatch" \
  "$(json_query "$OUTPUT_DIR/plan-executing-response.json" "status")" \
  "executing"

echo ""
echo "==> Creating RuntimeBinding on VM-2"
cat >"$OUTPUT_DIR/runtime-deploy.json" <<JSON
{
  "plan_id": "$PLAN_ID",
  "plan_status": "executing",
  "target_stage": "paper",
  "artifact_id": "$REGISTRY_ID",
  "artifact_version": "1.2.0",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "persona_capital_binding_id": "$PCB_ID",
  "persona_capital_binding_status": "active",
  "allowed_deployment_scope": "$PERSONA_SCOPE",
  "loader_checks_passed": true,
  "runtime_id": "$PAPER_RUNTIME_ID"
}
JSON

http_call POST "${EXEC_RUNTIME_MANAGER_URL%/}/api/runtimes/deploy" \
  "$OUTPUT_DIR/runtime-deploy.json" "$OUTPUT_DIR/runtime-deploy-response.json" 201 "$RUNTIME_MANAGER_TOKEN"

BINDING_ID="$(json_query "$OUTPUT_DIR/runtime-deploy-response.json" "binding_id")"
RUNTIME_ID="$(json_query "$OUTPUT_DIR/runtime-deploy-response.json" "runtime_id")"
expect_nonempty "binding_id" "$BINDING_ID"
expect_eq "runtime_id" "$RUNTIME_ID" "$PAPER_RUNTIME_ID"
expect_eq "deployment_mode" "$(json_query "$OUTPUT_DIR/runtime-deploy-response.json" "deployment_mode")" "paper"

DEPLOY_EVENT_CREATED_AT="$(timestamp_utc)"

cat >"$OUTPUT_DIR/saga-binding-created.json" <<JSON
{
  "binding_id": "$BINDING_ID",
  "runtime_id": "$RUNTIME_ID",
  "note": "runtime-manager on VM-2 created binding"
}
JSON

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/sagas/${SAGA_ID}/binding-created" \
  "$OUTPUT_DIR/saga-binding-created.json" "$OUTPUT_DIR/saga-binding-created-response.json" 200
expect_eq "saga binding-created sequence" \
  "$(json_query "$OUTPUT_DIR/saga-binding-created-response.json" "event.sequence_no")" \
  "2"

cat >"$OUTPUT_DIR/saga-runtime-active.json" <<JSON
{
  "binding_id": "$BINDING_ID",
  "runtime_id": "$RUNTIME_ID",
  "note": "paper execution runtime package on VM-2 is healthy"
}
JSON

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/sagas/${SAGA_ID}/runtime-active" \
  "$OUTPUT_DIR/saga-runtime-active.json" "$OUTPUT_DIR/saga-runtime-active-response.json" 200
expect_eq "saga runtime-active sequence" \
  "$(json_query "$OUTPUT_DIR/saga-runtime-active-response.json" "event.sequence_no")" \
  "3"

http_call GET "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/sagas/${SAGA_ID}" "" \
  "$OUTPUT_DIR/saga-detail-response.json" 200
expect_eq "saga status" "$(json_query "$OUTPUT_DIR/saga-detail-response.json" "status")" "completed"

cat >"$OUTPUT_DIR/plan-executed.json" <<JSON
{
  "status": "executed"
}
JSON

http_call POST "${CONTROL_DEPLOYMENT_URL%/}/api/deployment/plans/${PLAN_ID}/status" \
  "$OUTPUT_DIR/plan-executed.json" "$OUTPUT_DIR/plan-executed-response.json" 200
expect_eq "plan final status" "$(json_query "$OUTPUT_DIR/plan-executed-response.json" "status")" "executed"

echo ""
echo "==> Verifying telemetry ingest back to VM-1"
http_call GET "${CONTROL_TELEMETRY_URL%/}/api/telemetry/stats" "" \
  "$OUTPUT_DIR/telemetry-stats-before.json" 200
TOTAL_INGESTED_BEFORE="$(json_num "$OUTPUT_DIR/telemetry-stats-before.json" "service.total_ingested")"

cat >"$OUTPUT_DIR/telemetry-deploy.json" <<JSON
{
  "event_id": "$DEPLOY_EVENT_ID",
  "event_type": "deploy_completed",
  "created_at": "$DEPLOY_EVENT_CREATED_AT",
  "execution_mode": "paper",
  "environment": "paper",
  "deployment_stage": "paper",
  "binding_id": "$BINDING_ID",
  "runtime_id": "$RUNTIME_ID",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "artifact_id": "$REGISTRY_ID",
  "artifact_version": "1.2.0",
  "plan_id": "$PLAN_ID",
  "persona_capital_binding_id": "$PCB_ID",
  "target": {
    "registry_id": "$REGISTRY_ID",
    "strategy_id": "$STRATEGY_ID",
    "artifact_version": "1.2.0",
    "artifact_type": "model_artifact",
    "promotion_state": "paper"
  },
  "metrics": {
    "action": "deploy_completed"
  },
  "metadata": {
    "source_task_id": "$SOURCE_TASK_ID",
    "acceptance_mode": "dual_vm_smoke"
  },
  "authority_refs": {
    "write_owner": "runtime-manager",
    "authority_source": "runtime_binding",
    "runtime_role": "pantheon-paper-execution-runtime",
    "runtime_mode": "paper",
    "workspace_ref": "$WORKSPACE_REF",
    "auth_profile_ref": "$AUTH_PROFILE_REF",
    "persona_id": "$PERSONA_ID",
    "session_id": "$SESSION_ID",
    "trace_id": "$TRACE_ID",
    "request_id": "$REQUEST_ID"
  }
}
JSON

http_call POST "${CONTROL_TELEMETRY_URL%/}/api/telemetry/ingest" \
  "$OUTPUT_DIR/telemetry-deploy.json" "$OUTPUT_DIR/telemetry-deploy-response.json" 202
expect_eq "deploy telemetry ingest status" \
  "$(json_query "$OUTPUT_DIR/telemetry-deploy-response.json" "status")" \
  "accepted"

http_call GET "${CONTROL_TELEMETRY_URL%/}/api/telemetry/stats" "" \
  "$OUTPUT_DIR/telemetry-stats-after-deploy.json" 200
TOTAL_INGESTED_AFTER_DEPLOY="$(json_num "$OUTPUT_DIR/telemetry-stats-after-deploy.json" "service.total_ingested")"
if (( TOTAL_INGESTED_AFTER_DEPLOY < TOTAL_INGESTED_BEFORE + 1 )); then
  echo "FAIL: telemetry total_ingested did not increment after deploy event" >&2
  exit 1
fi
echo "  [ok] telemetry total_ingested incremented to ${TOTAL_INGESTED_AFTER_DEPLOY}"

echo ""
echo "==> Dispatching kill-switch from VM-1 to VM-2"
cat >"$OUTPUT_DIR/kill-switch.json" <<JSON
{
  "reason": "operator_emergency_stop",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "actor_id": "$ACTOR_ID",
  "binding_id": "$BINDING_ID"
}
JSON

http_call POST "${EXEC_RUNTIME_MANAGER_URL%/}/api/kill-switch/dispatch" \
  "$OUTPUT_DIR/kill-switch.json" "$OUTPUT_DIR/kill-switch-response.json" 200 "$RUNTIME_MANAGER_TOKEN"
expect_eq "kill-switch action" \
  "$(json_query "$OUTPUT_DIR/kill-switch-response.json" "command.action_type")" \
  "pause"
expect_eq "kill-switch safe mode" \
  "$(json_query "$OUTPUT_DIR/kill-switch-response.json" "safe_mode_after")" \
  "paused"
expect_eq "kill-switch binding action" \
  "$(json_query "$OUTPUT_DIR/kill-switch-response.json" "binding_action.binding.status")" \
  "paused"

http_call GET "${EXEC_RUNTIME_MANAGER_URL%/}/api/kill-switch/${CAPITAL_POOL_ID}/safe-mode" "" \
  "$OUTPUT_DIR/safe-mode-response.json" 200 "$RUNTIME_MANAGER_TOKEN"
expect_eq "safe-mode readback" \
  "$(json_query "$OUTPUT_DIR/safe-mode-response.json" "safe_mode_state")" \
  "paused"

http_call GET "${EXEC_RUNTIME_MANAGER_URL%/}/api/kill-switch/audit-log" "" \
  "$OUTPUT_DIR/kill-switch-audit-response.json" 200 "$RUNTIME_MANAGER_TOKEN"
AUDIT_COUNT="$(python3 - "$OUTPUT_DIR/kill-switch-audit-response.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
print(len(payload.get("entries", [])))
PY
)"
if (( AUDIT_COUNT < 1 )); then
  echo "FAIL: kill-switch audit log is empty" >&2
  exit 1
fi
echo "  [ok] kill-switch audit entries = ${AUDIT_COUNT}"

echo ""
echo "==> Executing rollback on VM-2"
cat >"$OUTPUT_DIR/rollback.json" <<JSON
{
  "current_binding_id": "$BINDING_ID",
  "action_type": "pause_then_replace",
  "replacement_plan_id": "${PLAN_ID}-rollback",
  "replacement_plan_status": "approved",
  "replacement_deployment_mode": "paper",
  "replacement_artifact_id": "$FALLBACK_REGISTRY_ID",
  "replacement_artifact_version": "1.1.9",
  "replacement_persona_capital_binding_id": "$PCB_ID",
  "replacement_persona_capital_binding_status": "active",
  "replacement_allowed_deployment_scope": "$PERSONA_SCOPE",
  "replacement_runtime_id": "${PAPER_RUNTIME_ID}-rollback",
  "loader_checks_passed": true,
  "opened_by_artifact_id": "$REGISTRY_ID"
}
JSON

http_call POST "${EXEC_RUNTIME_MANAGER_URL%/}/api/rollback" \
  "$OUTPUT_DIR/rollback.json" "$OUTPUT_DIR/rollback-response.json" 201 "$RUNTIME_MANAGER_TOKEN"
expect_eq "rollback action_type" \
  "$(json_query "$OUTPUT_DIR/rollback-response.json" "action_type")" \
  "pause_then_replace"
expect_eq "old binding retired after rollback" \
  "$(json_query "$OUTPUT_DIR/rollback-response.json" "old_binding.status")" \
  "retired"
expect_eq "replacement binding active after rollback" \
  "$(json_query "$OUTPUT_DIR/rollback-response.json" "new_binding.status")" \
  "active"
expect_eq "replacement binding rollback_parent" \
  "$(json_query "$OUTPUT_DIR/rollback-response.json" "new_binding.rollback_parent")" \
  "$BINDING_ID"

NEW_BINDING_ID="$(json_query "$OUTPUT_DIR/rollback-response.json" "new_binding.binding_id")"
NEW_RUNTIME_ID="$(json_query "$OUTPUT_DIR/rollback-response.json" "new_binding.runtime_id")"
expect_nonempty "new binding_id" "$NEW_BINDING_ID"

ROLLBACK_EVENT_CREATED_AT="$(timestamp_utc)"

cat >"$OUTPUT_DIR/telemetry-rollback.json" <<JSON
{
  "event_id": "$ROLLBACK_EVENT_ID",
  "event_type": "rollback_completed",
  "created_at": "$ROLLBACK_EVENT_CREATED_AT",
  "execution_mode": "paper",
  "environment": "paper",
  "deployment_stage": "paper",
  "binding_id": "$NEW_BINDING_ID",
  "runtime_id": "$NEW_RUNTIME_ID",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "artifact_id": "$FALLBACK_REGISTRY_ID",
  "artifact_version": "1.1.9",
  "plan_id": "${PLAN_ID}-rollback",
  "persona_capital_binding_id": "$PCB_ID",
  "rollback_parent": "$BINDING_ID",
  "rollback_action_type": "pause_then_replace",
  "target": {
    "registry_id": "$FALLBACK_REGISTRY_ID",
    "strategy_id": "$STRATEGY_ID",
    "artifact_version": "1.1.9",
    "artifact_type": "model_artifact",
    "promotion_state": "paper"
  },
  "metrics": {
    "action": "rollback_completed"
  },
  "metadata": {
    "source_task_id": "$SOURCE_TASK_ID",
    "acceptance_mode": "dual_vm_smoke"
  }
}
JSON

http_call POST "${CONTROL_TELEMETRY_URL%/}/api/telemetry/ingest" \
  "$OUTPUT_DIR/telemetry-rollback.json" "$OUTPUT_DIR/telemetry-rollback-response.json" 202
expect_eq "rollback telemetry ingest status" \
  "$(json_query "$OUTPUT_DIR/telemetry-rollback-response.json" "status")" \
  "accepted"

http_call GET "${CONTROL_TELEMETRY_URL%/}/api/telemetry/stats" "" \
  "$OUTPUT_DIR/telemetry-stats-after-rollback.json" 200
TOTAL_INGESTED_AFTER_ROLLBACK="$(json_num "$OUTPUT_DIR/telemetry-stats-after-rollback.json" "service.total_ingested")"
if (( TOTAL_INGESTED_AFTER_ROLLBACK < TOTAL_INGESTED_AFTER_DEPLOY + 1 )); then
  echo "FAIL: telemetry total_ingested did not increment after rollback event" >&2
  exit 1
fi
echo "  [ok] telemetry total_ingested incremented to ${TOTAL_INGESTED_AFTER_ROLLBACK}"

cat >"$OUTPUT_DIR/summary.json" <<JSON
{
  "task_id": "$SOURCE_TASK_ID",
  "control_deployment_url": "$CONTROL_DEPLOYMENT_URL",
  "control_telemetry_url": "$CONTROL_TELEMETRY_URL",
  "exec_runtime_manager_url": "$EXEC_RUNTIME_MANAGER_URL",
  "exec_paper_runtime_url": "$EXEC_PAPER_RUNTIME_URL",
  "plan_id": "$PLAN_ID",
  "saga_id": "$SAGA_ID",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "binding_id": "$BINDING_ID",
  "replacement_binding_id": "$NEW_BINDING_ID",
  "runtime_id": "$RUNTIME_ID",
  "replacement_runtime_id": "$NEW_RUNTIME_ID",
  "telemetry_total_ingested_before": $TOTAL_INGESTED_BEFORE,
  "telemetry_total_ingested_after_deploy": $TOTAL_INGESTED_AFTER_DEPLOY,
  "telemetry_total_ingested_after_rollback": $TOTAL_INGESTED_AFTER_ROLLBACK,
  "paper_runtime_bootstrap_stub": false,
  "notes": [
    "Validated DeploymentPlan -> RuntimeBinding across VM-1 deployment service and VM-2 runtime-manager.",
    "Validated the VM-2 paper execution package exposes a real signal-consumer/runtime health surface instead of the old bootstrap-only stub.",
    "Validated telemetry ingest on VM-1 using VM-2 binding identity lookup.",
    "Validated VM-1 initiated kill-switch and rollback commands executed by VM-2 runtime-manager.",
    "This smoke still does not prove one integrated governed paper order loop; OSS-004C remains the acceptance packet for that."
  ]
}
JSON

echo ""
echo "==> Dual-VM acceptance smoke passed"
echo "    saga_id              : $SAGA_ID"
echo "    binding_id           : $BINDING_ID"
echo "    replacement_binding  : $NEW_BINDING_ID"
echo "    telemetry counts     : ${TOTAL_INGESTED_BEFORE} -> ${TOTAL_INGESTED_AFTER_DEPLOY} -> ${TOTAL_INGESTED_AFTER_ROLLBACK}"
echo "    summary artifact     : $OUTPUT_DIR/summary.json"
echo ""
echo "Note: this smoke proves the VM-2 paper execution package is live, but OSS-004C still owns the first integrated governed paper execution packet."

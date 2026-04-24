#!/usr/bin/env bash
# Pantheon single-VM end-to-end smoke test for DEPLOY-006.
#
# Verifies the full service chain on a single host running docker-compose.yml:
#   runtime-manager  : /__health__  + /api/runtimes/deploy
#   governance       : /health      + /api/governance/write-authority + approvals round-trip
#   registry         : /health      + /api/registry/entries register + get
#   telemetry        : /__health__  + /api/telemetry/ingest + /api/telemetry/stats
#   incidents        : /__health__  + /api/incidents
#   operator-bff     : /health      + /api/v1/operator/governance/review-queue
#                                   + /api/v1/deployment-plans + /api/v1/incidents
#                                   + /api/v1/telemetry
#   mock plan path   : registry entry → governance approval → RuntimeBinding
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RUNTIME_MANAGER_URL="${RUNTIME_MANAGER_URL:-http://127.0.0.1:18081}"
GOVERNANCE_URL="${GOVERNANCE_URL:-http://127.0.0.1:18082}"
TELEMETRY_URL="${TELEMETRY_URL:-http://127.0.0.1:18083}"
INCIDENTS_URL="${INCIDENTS_URL:-http://127.0.0.1:18090}"
REGISTRY_URL="${REGISTRY_URL:-http://127.0.0.1:18087}"
BFF_URL="${BFF_URL:-http://127.0.0.1:18001}"
RUNTIME_MANAGER_TOKEN="${PANTHEON_RUNTIME_MANAGER_TOKEN:-runtime-control-internal}"
SOURCE_TASK_ID="${SOURCE_TASK_ID:-DEPLOY-006}"
KEEP_OUTPUT=true
OUTPUT_DIR=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/smoke_test_single_vm.sh [options]

Options:
  --runtime-manager-url <url>   runtime-manager base URL   (default: http://127.0.0.1:18081)
  --governance-url <url>        governance base URL          (default: http://127.0.0.1:18082)
  --telemetry-url <url>         telemetry base URL           (default: http://127.0.0.1:18083)
  --incidents-url <url>         incidents base URL           (default: http://127.0.0.1:18090)
  --registry-url <url>          registry base URL            (default: http://127.0.0.1:18087)
  --bff-url <url>               operator-bff base URL        (default: http://127.0.0.1:18001)
  --runtime-manager-token <t>   Bearer token for runtime-manager
  --output-dir <dir>            Persist JSON request/response artifacts here
  --keep-output                 Keep temp output directory when --output-dir is omitted
  --help                        Show this message

Environment variable equivalents:
  RUNTIME_MANAGER_URL, GOVERNANCE_URL, TELEMETRY_URL, INCIDENTS_URL,
  REGISTRY_URL, BFF_URL, PANTHEON_RUNTIME_MANAGER_TOKEN, SOURCE_TASK_ID
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-manager-url) RUNTIME_MANAGER_URL="$2"; shift 2 ;;
    --governance-url) GOVERNANCE_URL="$2"; shift 2 ;;
    --telemetry-url) TELEMETRY_URL="$2"; shift 2 ;;
    --incidents-url) INCIDENTS_URL="$2"; shift 2 ;;
    --registry-url) REGISTRY_URL="$2"; shift 2 ;;
    --bff-url) BFF_URL="$2"; shift 2 ;;
    --runtime-manager-token) RUNTIME_MANAGER_TOKEN="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --keep-output) KEEP_OUTPUT=true; shift ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}
need_cmd curl
need_cmd python3

json_query() {
  local file="$1" path="$2"
  python3 - "$file" "$path" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
value = payload
if sys.argv[2]:
    for part in sys.argv[2].split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
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
  local file="$1" path="$2"
  python3 - "$file" "$path" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
v = payload
for part in sys.argv[2].split("."):
    v = v[int(part)] if isinstance(v, list) else v[part]
print(v)
PY
}

expect_eq() {
  local label="$1" actual="$2" expected="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: $label (expected '$expected', got '$actual')" >&2
    exit 1
  fi
  echo "  [ok] $label = $expected"
}

expect_nonempty() {
  local label="$1" value="$2"
  if [[ -z "$value" || "$value" == "null" ]]; then
    echo "FAIL: $label is empty" >&2; exit 1
  fi
  echo "  [ok] $label = $value"
}

http_call() {
  local method="$1" url="$2" body_file="${3:-}" output_file="$4" expected_status="$5" auth_token="${6:-}"
  local curl_args=(-sS -X "$method" -H "Accept: application/json")
  [[ -n "$auth_token" ]] && curl_args+=(-H "Authorization: Bearer ${auth_token}")
  [[ -n "$body_file" ]] && curl_args+=(-H "Content-Type: application/json" --data "@${body_file}")
  local status
  status=$(curl "${curl_args[@]}" -o "$output_file" -w '%{http_code}' "$url")
  if [[ "$status" != "$expected_status" ]]; then
    echo "FAIL: ${method} ${url} returned ${status}, expected ${expected_status}" >&2
    echo "--- response body ---" >&2; cat "$output_file" >&2 || true
    echo "---------------------" >&2; exit 1
  fi
}

health_check() {
  local label="$1" url="$2" expected_field="${3:-}" expected_value="${4:-}"
  local out="$OUTPUT_DIR/${label// /_}_health.json"
  echo "==> Health: ${label}"
  http_call GET "$url" "" "$out" 200
  local status
  status=$(json_query "$out" "status")
  expect_eq "${label} status" "$status" "ok"
  if [[ -n "$expected_field" && -n "$expected_value" ]]; then
    local actual
    actual=$(json_query "$out" "$expected_field")
    expect_eq "${label} ${expected_field}" "$actual" "$expected_value"
  fi
}

timestamp_utc() {
  python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"))
PY
}

uuid4() { python3 -c "import uuid; print(uuid.uuid4())"; }
suffix() { python3 -c "import uuid; print(uuid.uuid4().hex[:8])"; }

# ── output directory ────────────────────────────────────────────────────────
if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="/tmp/pantheon/single-vm-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$OUTPUT_DIR"

RUN_SUFFIX="$(suffix)"
STRATEGY_ID="strat-single-vm"
REGISTRY_ID="reg-single-vm-${RUN_SUFFIX}"
PLAN_ID="plan-single-vm-${RUN_SUFFIX}"
APPROVAL_ID="approval-single-vm-${RUN_SUFFIX}"
CAPITAL_POOL_ID="pool-single-vm-${RUN_SUFFIX}"
PCB_ID="pcb-single-vm-${RUN_SUFFIX}"
INCIDENT_ID="inc-single-vm-${RUN_SUFFIX}"
RUNTIME_ID="runtime-single-vm-${RUN_SUFFIX}"
TELEMETRY_EVENT_ID="$(uuid4)"
RUN_AT="$(timestamp_utc)"

echo "==> DEPLOY-006 single-VM smoke"
echo "    output dir  : $OUTPUT_DIR"
echo "    run_suffix  : $RUN_SUFFIX"
echo "    plan_id     : $PLAN_ID"
echo "    pool_id     : $CAPITAL_POOL_ID"
echo ""

# ── 1. Core service health checks ──────────────────────────────────────────
health_check "runtime-manager" "${RUNTIME_MANAGER_URL%/}/__health__" "service" "runtime-manager"
health_check "governance"      "${GOVERNANCE_URL%/}/health" "service" "governance"
health_check "telemetry"       "${TELEMETRY_URL%/}/__health__" "service" "telemetry-ingest"
health_check "incidents"       "${INCIDENTS_URL%/}/__health__"
health_check "registry"        "${REGISTRY_URL%/}/health" "service" "pantheon-registry"
health_check "operator-bff"    "${BFF_URL%/}/health"
echo ""

# ── 2. Governance — write-authority matrix ──────────────────────────────────
echo "==> Governance: write-authority matrix"
http_call GET "${GOVERNANCE_URL%/}/api/governance/write-authority" "" \
  "$OUTPUT_DIR/governance-write-authority.json" 200
expect_nonempty "governance matrix" "$(json_query "$OUTPUT_DIR/governance-write-authority.json" "matrix")"
echo ""

# ── 3. Telemetry — stats baseline ───────────────────────────────────────────
echo "==> Telemetry: stats baseline"
http_call GET "${TELEMETRY_URL%/}/api/telemetry/stats" "" \
  "$OUTPUT_DIR/telemetry-stats-before.json" 200
TOTAL_BEFORE="$(json_num "$OUTPUT_DIR/telemetry-stats-before.json" "service.total_ingested")"
echo "  [ok] telemetry total_ingested baseline = ${TOTAL_BEFORE}"
echo ""

# ── 4. Registry — register a mock artifact ─────────────────────────────────
echo "==> Registry: register mock artifact"
cat >"$OUTPUT_DIR/registry-register.json" <<JSON
{
  "registry_id": "$REGISTRY_ID",
  "artifact_type": "model_artifact",
  "strategy_id": "$STRATEGY_ID",
  "version": "1.0.0",
  "artifact_state": "draft",
  "checksum": "sha256:single-vm-$RUN_SUFFIX",
  "lineage": {
    "source_run_ids": ["run-single-vm-$RUN_SUFFIX"]
  },
  "deployment_summary": {
    "current_stage": "none"
  }
}
JSON
http_call POST "${REGISTRY_URL%/}/api/registry/entries" \
  "$OUTPUT_DIR/registry-register.json" "$OUTPUT_DIR/registry-register-response.json" 200
REGISTRY_ID="$(json_query "$OUTPUT_DIR/registry-register-response.json" "entry.registry_id")"
expect_nonempty "registry_id" "$REGISTRY_ID"

http_call GET "${REGISTRY_URL%/}/api/registry/entries/${REGISTRY_ID}" "" \
  "$OUTPUT_DIR/registry-get-response.json" 200
expect_eq "registry get registry_id" \
  "$(json_query "$OUTPUT_DIR/registry-get-response.json" "entry.registry_id")" "$REGISTRY_ID"
echo ""

# ── 5. Governance — propose → accept_review → decide approval ──────────────
# risk_level "low" permits actor_role "governance_reviewer" and "automated_gate"
echo "==> Governance: propose approval"
cat >"$OUTPUT_DIR/governance-propose.json" <<JSON
{
  "decision_id": "$APPROVAL_ID",
  "target_type": "registry_entry",
  "target_id": "$REGISTRY_ID",
  "target_version": "1.0.0",
  "risk_level": "low",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "persona_id": "persona-ops"
}
JSON
http_call POST "${GOVERNANCE_URL%/}/api/governance/approvals" \
  "$OUTPUT_DIR/governance-propose.json" "$OUTPUT_DIR/governance-propose-response.json" 201
expect_eq "approval decision_id" \
  "$(json_query "$OUTPUT_DIR/governance-propose-response.json" "decision_id")" "$APPROVAL_ID"
expect_eq "approval decision_state after propose" \
  "$(json_query "$OUTPUT_DIR/governance-propose-response.json" "decision_state")" "proposed"

echo "==> Governance: accept review"
cat >"$OUTPUT_DIR/governance-review.json" <<JSON
{
  "actor_role": "governance_reviewer",
  "actor_id": "deploy-006-smoke-reviewer"
}
JSON
http_call POST "${GOVERNANCE_URL%/}/api/governance/approvals/${APPROVAL_ID}/review" \
  "$OUTPUT_DIR/governance-review.json" "$OUTPUT_DIR/governance-review-response.json" 200
expect_eq "approval decision_state after review" \
  "$(json_query "$OUTPUT_DIR/governance-review-response.json" "decision_state")" "under_review"

echo "==> Governance: decide approval"
cat >"$OUTPUT_DIR/governance-decide.json" <<JSON
{
  "actor_role": "governance_reviewer",
  "actor_id": "deploy-006-smoke-approver",
  "outcome": "approved",
  "rationale": "DEPLOY-006 single-VM smoke acceptance"
}
JSON
http_call POST "${GOVERNANCE_URL%/}/api/governance/approvals/${APPROVAL_ID}/decide" \
  "$OUTPUT_DIR/governance-decide.json" "$OUTPUT_DIR/governance-decide-response.json" 200
expect_eq "approval decision after decide" \
  "$(json_query "$OUTPUT_DIR/governance-decide-response.json" "decision")" "approved"
expect_eq "approval decision_state after decide" \
  "$(json_query "$OUTPUT_DIR/governance-decide-response.json" "decision_state")" "decided"
echo ""

# ── 6. Runtime-manager — create mock RuntimeBinding ────────────────────────
echo "==> Runtime-manager: create RuntimeBinding (mock DeploymentPlan → RuntimeBinding)"
cat >"$OUTPUT_DIR/runtime-deploy.json" <<JSON
{
  "plan_id": "$PLAN_ID",
  "plan_status": "approved",
  "target_stage": "paper",
  "artifact_id": "$REGISTRY_ID",
  "artifact_version": "1.0.0",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "persona_capital_binding_id": "$PCB_ID",
  "persona_capital_binding_status": "active",
  "allowed_deployment_scope": "live",
  "loader_checks_passed": true,
  "runtime_id": "$RUNTIME_ID",
  "metadata": {
    "source_task_id": "$SOURCE_TASK_ID",
    "acceptance_mode": "single_vm_smoke"
  }
}
JSON
http_call POST "${RUNTIME_MANAGER_URL%/}/api/runtimes/deploy" \
  "$OUTPUT_DIR/runtime-deploy.json" "$OUTPUT_DIR/runtime-deploy-response.json" 201 \
  "$RUNTIME_MANAGER_TOKEN"
BINDING_ID="$(json_query "$OUTPUT_DIR/runtime-deploy-response.json" "binding_id")"
expect_nonempty "binding_id" "$BINDING_ID"
BINDING_EFFECTIVE_AT="$(json_query "$OUTPUT_DIR/runtime-deploy-response.json" "effective_at")"
expect_nonempty "binding effective_at" "$BINDING_EFFECTIVE_AT"
expect_eq "deployment_mode" \
  "$(json_query "$OUTPUT_DIR/runtime-deploy-response.json" "deployment_mode")" "paper"
echo ""

# ── 7. Telemetry — ingest event linked to binding ──────────────────────────
echo "==> Telemetry: ingest event"
cat >"$OUTPUT_DIR/telemetry-ingest.json" <<JSON
{
  "event_id": "$TELEMETRY_EVENT_ID",
  "event_type": "deploy_completed",
  "created_at": "$BINDING_EFFECTIVE_AT",
  "execution_mode": "paper",
  "environment": "paper",
  "deployment_stage": "paper",
  "binding_id": "$BINDING_ID",
  "runtime_id": "$RUNTIME_ID",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "artifact_id": "$REGISTRY_ID",
  "artifact_version": "1.0.0",
  "plan_id": "$PLAN_ID",
  "persona_capital_binding_id": "$PCB_ID",
  "target": {
    "registry_id": "$REGISTRY_ID",
    "strategy_id": "$STRATEGY_ID",
    "artifact_version": "1.0.0",
    "artifact_type": "model_artifact",
    "promotion_state": "paper"
  },
  "metrics": { "action": "deploy_completed" },
  "metadata": { "source_task_id": "$SOURCE_TASK_ID" }
}
JSON
http_call POST "${TELEMETRY_URL%/}/api/telemetry/ingest" \
  "$OUTPUT_DIR/telemetry-ingest.json" "$OUTPUT_DIR/telemetry-ingest-response.json" 202
expect_eq "telemetry ingest status" \
  "$(json_query "$OUTPUT_DIR/telemetry-ingest-response.json" "status")" "accepted"

http_call GET "${TELEMETRY_URL%/}/api/telemetry/stats" "" \
  "$OUTPUT_DIR/telemetry-stats-after.json" 200
TOTAL_AFTER="$(json_num "$OUTPUT_DIR/telemetry-stats-after.json" "service.total_ingested")"
if (( TOTAL_AFTER < TOTAL_BEFORE + 1 )); then
  echo "FAIL: telemetry total_ingested did not increment (${TOTAL_BEFORE} -> ${TOTAL_AFTER})" >&2
  exit 1
fi
echo "  [ok] telemetry total_ingested incremented to ${TOTAL_AFTER}"
echo ""

# ── 8. Incidents — create incident linked to binding ───────────────────────
echo "==> Incidents: create incident"
cat >"$OUTPUT_DIR/incident-create.json" <<JSON
{
  "incident_id": "$INCIDENT_ID",
  "title": "DEPLOY-006 single-VM smoke incident",
  "status": "open",
  "severity": "high",
  "binding_id": "$BINDING_ID",
  "deployment_stage": "paper",
  "deployment_plan_id": "$PLAN_ID",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "persona_capital_binding_id": "$PCB_ID",
  "artifact_id": "$REGISTRY_ID",
  "artifact_version": "1.0.0",
  "runtime_id": "$RUNTIME_ID",
  "trace_id": "trace-single-vm-${RUN_SUFFIX}",
  "evidence_summary": "single-VM smoke validation incident",
  "telemetry_event_ids": []
}
JSON
http_call POST "${INCIDENTS_URL%/}/api/incidents" \
  "$OUTPUT_DIR/incident-create.json" "$OUTPUT_DIR/incident-create-response.json" 201
expect_eq "incident incident_id" \
  "$(json_query "$OUTPUT_DIR/incident-create-response.json" "incident_id")" "$INCIDENT_ID"
echo ""

# ── 9. BFF — query governance, deployment-plans, incidents surfaces ─────────
echo "==> BFF: governance review-queue surface"
http_call GET "${BFF_URL%/}/api/v1/operator/governance/review-queue" "" \
  "$OUTPUT_DIR/bff-governance-review-queue.json" 200 "smoke-operator:operator"
expect_nonempty "bff governance review-queue meta" \
  "$(json_query "$OUTPUT_DIR/bff-governance-review-queue.json" "meta")"

echo "==> BFF: deployment-plans surface"
http_call GET "${BFF_URL%/}/api/v1/deployment-plans" "" \
  "$OUTPUT_DIR/bff-deployment-plans.json" 200 "smoke-operator:operator"

echo "==> BFF: incidents surface"
http_call GET "${BFF_URL%/}/api/v1/incidents" "" \
  "$OUTPUT_DIR/bff-incidents.json" 200 "smoke-operator:operator"

echo "==> BFF: telemetry surface"
http_call GET "${BFF_URL%/}/api/v1/telemetry" "" \
  "$OUTPUT_DIR/bff-telemetry.json" 200 "smoke-operator:operator"
expect_nonempty "bff telemetry meta" \
  "$(json_query "$OUTPUT_DIR/bff-telemetry.json" "meta")"
echo ""

# ── 10. Summary ─────────────────────────────────────────────────────────────
cat >"$OUTPUT_DIR/summary.json" <<JSON
{
  "task_id": "$SOURCE_TASK_ID",
  "run_suffix": "$RUN_SUFFIX",
  "run_at": "$RUN_AT",
  "runtime_manager_url": "$RUNTIME_MANAGER_URL",
  "governance_url": "$GOVERNANCE_URL",
  "telemetry_url": "$TELEMETRY_URL",
  "incidents_url": "$INCIDENTS_URL",
  "registry_url": "$REGISTRY_URL",
  "bff_url": "$BFF_URL",
  "registry_id": "$REGISTRY_ID",
  "approval_id": "$APPROVAL_ID",
  "plan_id": "$PLAN_ID",
  "capital_pool_id": "$CAPITAL_POOL_ID",
  "binding_id": "$BINDING_ID",
  "runtime_id": "$RUNTIME_ID",
  "incident_id": "$INCIDENT_ID",
  "telemetry_total_ingested_before": $TOTAL_BEFORE,
  "telemetry_total_ingested_after": $TOTAL_AFTER,
  "result": "passed"
}
JSON

echo "==> Single-VM smoke passed"
echo "    binding_id         : $BINDING_ID"
echo "    approval_id        : $APPROVAL_ID"
echo "    telemetry counts   : ${TOTAL_BEFORE} -> ${TOTAL_AFTER}"
echo "    summary artifact   : $OUTPUT_DIR/summary.json"

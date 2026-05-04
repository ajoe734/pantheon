# BFF-Down Operator Drill Runbook

Status: active
Last updated: 2026-05-04
Scope: Emergency operator procedures when `pantheon-bff` is partially or fully unavailable
Owner: SVC-BLUEPRINT-OPERATOR-FALLBACK-DRILLS
Drill status: smoke-tested
References: `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §5.2, §6; `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` §5; `OPERATOR_ACCEPTANCE_MATRIX.md` §4.2–§4.3

---

## 0. When this runbook applies

Activate this runbook when:

- `pantheon-bff` health check fails or returns 5xx consistently
- The Pantheon Console or Workbench is unreachable
- You need to execute an emergency runtime action and the BFF command surface is unavailable

**Critical guarantee:** BFF outage must NOT affect active runtimes or the emergency control chain.
Active runtimes continue executing. This runbook lets you control them without the BFF.

---

## 1. Surface priority order (BFF-down)

When BFF is down, use surfaces in this order:

| Priority | Surface | Use for |
|---|---|---|
| 1 | `S-CLI` (Admin CLI) | Pause, rollback, kill-switch — lower overhead |
| 2 | `S-IAPI` (Internal API) | Same actions via direct HTTP; useful when CLI is unavailable |
| 3 | `S-EMRG` (Emergency fast path) | Kill-switch only — highest protection, lowest latency |

Do not attempt to restart BFF before completing emergency runtime control. BFF is control-plane only and does not affect active runtime execution.

Recommended shell baseline:

```bash
export RUNTIME_MANAGER_URL="${RUNTIME_MANAGER_URL:-http://localhost:28081}"
export PANTHEON_INTERNAL_API_URL="$RUNTIME_MANAGER_URL"
export OPERATOR_ID="${OPERATOR_ID:-operator-oncall}"
export OPERATOR_TOKEN="${OPERATOR_TOKEN:?set a runtime-manager bearer token}"
export MFA_TOKEN="${MFA_TOKEN:-123456}"
```

---

## 2. Pre-drill verification

Before any drill or real emergency, confirm:

```bash
# Confirm runtime-manager is reachable (this is the non-BFF control anchor)
curl -s "$RUNTIME_MANAGER_URL/__health__" | python3 -m json.tool

# Confirm your operator token is valid
curl -s -H "Authorization: Bearer $OPERATOR_TOKEN" \
  "$RUNTIME_MANAGER_URL/api/runtime-bindings" | python3 -m json.tool

# List active bindings
curl -s -H "Authorization: Bearer $OPERATOR_TOKEN" \
  "$RUNTIME_MANAGER_URL/api/runtime-bindings" | python3 -c "
import sys, json
data = json.load(sys.stdin)
active = [b for b in data.get('bindings', []) if b.get('status') == 'active']
print(f'Active bindings: {len(active)}')
for b in active:
    print(f\"  binding_id={b['binding_id']}  pool={b['capital_pool_id']}  stage={b['deployment_mode']}\")
"
```

Capture the `binding_id` and `capital_pool_id` values — they are required for all emergency actions below.

---

## 3. Emergency Pause (S-EMRG / S-IAPI / S-CLI)

Use when a running persona must be stopped immediately.

### 3.1 Via S-EMRG — kill-switch fast path (highest priority)

```bash
# Dispatch PAUSE via kill-switch fast path
curl -s -X POST \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"reason\": \"operator_emergency_stop\",
    \"capital_pool_id\": \"$POOL_ID\",
    \"actor_id\": \"$OPERATOR_ID\",
    \"severity\": 1,
    \"action_override\": \"pause\"
  }" \
  "$RUNTIME_MANAGER_URL/api/kill-switch/dispatch"
```

Expected response fields:
- `command.command_id` — record this for the audit trail
- `audit_entry.audit_id` — persistent audit record
- `safe_mode_after` — pool safe-mode state post-dispatch (should be `paused`)
- `binding_action.action` — should be `pause`
- `binding_action.binding.status` — should be `paused`

### 3.2 Via S-IAPI — internal API pause

```bash
# Pause via internal API runtime path
curl -s -X POST \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"pause_action\": \"pause\",
    \"duration_seconds\": 3600,
    \"reason\": \"bff-down emergency pause — operator $OPERATOR_ID\"
  }" \
  "$RUNTIME_MANAGER_URL/api/internal/v1/runtimes/$BINDING_ID/pause"
```

Expected response: `{"status_after": "paused", ...}`

### 3.3 Via S-CLI — pantheon-admin pause

```bash
bin/pantheon-admin runtime pause "$BINDING_ID" \
  --base-url "$RUNTIME_MANAGER_URL" \
  --auth-token "$OPERATOR_TOKEN" \
  --mfa-token "$MFA_TOKEN" \
  --duration 3600 \
  --reason "bff-down emergency pause — operator $OPERATOR_ID" \
  --output json
```

Expected response fields match the S-IAPI route: `command_id`, `runtime_binding_id`, `status_after`, and `audit_id` in the command record.

---

## 4. Emergency Liquidate (S-EMRG / S-IAPI)

Use when positions must be flattened immediately.

### 4.1 Via S-EMRG — kill-switch LIQUIDATE

```bash
curl -s -X POST \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"reason\": \"operator_emergency_stop\",
    \"capital_pool_id\": \"$POOL_ID\",
    \"actor_id\": \"$OPERATOR_ID\",
    \"severity\": 1,
    \"action_override\": \"liquidate\"
  }" \
  "$RUNTIME_MANAGER_URL/api/kill-switch/dispatch"
```

Expected: `binding_action.binding.status` = `retired`

> **Note:** LIQUIDATE retires the binding and triggers the execution plane to flatten positions.
> The runtime will not place new orders once the binding is retired.

### 4.2 Via S-CLI — pantheon-admin LIQUIDATE

```bash
bin/pantheon-admin kill-switch activate \
  --base-url "$RUNTIME_MANAGER_URL" \
  --auth-token "$OPERATOR_TOKEN" \
  --mfa-token "$MFA_TOKEN" \
  --scope pool \
  --scope-id "$POOL_ID" \
  --rationale "operator_emergency_stop" \
  --action-override liquidate \
  --force \
  --output json
```

Expected:
- CLI exits `0`
- `action` = `liquidate`
- canonical readback for the affected binding shows `status` = `retired`
- kill-switch audit log includes the returned `audit_id`

---

## 5. Emergency Replace (S-EMRG / S-IAPI)

Use when the active runtime must be hot-swapped to a fallback artifact.

### 5.1 Via S-EMRG — kill-switch REPLACE

```bash
curl -s -X POST \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"reason\": \"operator_emergency_stop\",
    \"capital_pool_id\": \"$POOL_ID\",
    \"actor_id\": \"$OPERATOR_ID\",
    \"severity\": 1,
    \"action_override\": \"replace\",
    \"fallback_artifact_id\": \"$FALLBACK_ARTIFACT_ID\",
    \"fallback_artifact_version\": \"$FALLBACK_ARTIFACT_VERSION\"
  }" \
  "$RUNTIME_MANAGER_URL/api/kill-switch/dispatch"
```

Expected:
- `binding_action.action` = `replace`
- `binding_action.binding.status` = `retired` (old binding)
- `binding_action.replacement_binding.status` = `active` (new binding)

### 5.2 Via S-IAPI — rollback replace

```bash
curl -s -X POST \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"rollback_target_type\": \"runtime\",
    \"target_id\": \"$BINDING_ID\",
    \"rollback_to_version\": \"$FALLBACK_ARTIFACT_VERSION\",
    \"rollback_action_type\": \"pause_then_replace\"
  }" \
  "$RUNTIME_MANAGER_URL/api/internal/v1/rollbacks/execute"
```

### 5.3 Via S-CLI — pantheon-admin REPLACE

```bash
bin/pantheon-admin kill-switch activate \
  --base-url "$RUNTIME_MANAGER_URL" \
  --auth-token "$OPERATOR_TOKEN" \
  --mfa-token "$MFA_TOKEN" \
  --scope pool \
  --scope-id "$POOL_ID" \
  --rationale "operator_emergency_stop" \
  --action-override replace \
  --fallback-artifact-id "$FALLBACK_ARTIFACT_ID" \
  --fallback-artifact-version "$FALLBACK_ARTIFACT_VERSION" \
  --force \
  --output json
```

Expected:
- CLI exits `0`
- response includes the fallback artifact identifiers
- canonical readback shows the old binding `retired` and the replacement binding `active`

---

## 6. Querying Audit Evidence

All emergency actions produce durable audit records. Query them without BFF:

```bash
# Full kill-switch audit log
curl -s -H "Authorization: Bearer $OPERATOR_TOKEN" \
  "$RUNTIME_MANAGER_URL/api/kill-switch/audit-log" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f\"Total audit entries: {data.get('count', len(data.get('entries', [])))}\")
for e in data.get('entries', []):
    print(f\"  audit_id={e.get('audit_id')}  actor={e.get('actor_id')}  reason={e.get('reason')}  at={e.get('audited_at')}\")
"

# Safe-mode state per pool
curl -s -H "Authorization: Bearer $OPERATOR_TOKEN" \
  "$RUNTIME_MANAGER_URL/api/kill-switch/$POOL_ID/safe-mode"

# Check binding status (confirms action was recorded)
curl -s -H "Authorization: Bearer $OPERATOR_TOKEN" \
  "$RUNTIME_MANAGER_URL/api/runtime-bindings/$BINDING_ID" | python3 -c "
import sys, json
b = json.load(sys.stdin)
print(f\"binding_id={b.get('binding_id')}  status={b.get('status')}  retired_at={b.get('retired_at')}\")
"
```

---

## 7. Recovery path

After emergency action is complete, advance the safe-mode state as conditions clear:

```bash
# paused → recovery_testing
curl -s -X POST \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"target_state\": \"recovery_testing\", \"actor_id\": \"$OPERATOR_ID\", \"note\": \"conditions cleared\"}" \
  "$RUNTIME_MANAGER_URL/api/kill-switch/$POOL_ID/safe-mode"

# recovery_testing → normal_restored
curl -s -X POST \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"target_state\": \"normal_restored\", \"actor_id\": \"$OPERATOR_ID\", \"note\": \"post-recovery verification complete\"}" \
  "$RUNTIME_MANAGER_URL/api/kill-switch/$POOL_ID/safe-mode"
```

---

## 8. Drill procedure

Run this sequence to verify all fallback paths are operational before a real incident:

1. Confirm runtime-manager health (§2)
2. Deploy a **paper-stage** test binding (never drill on live/canary)
3. Execute PAUSE via S-IAPI (§3.2) — verify canonical binding readback is `paused`
4. Execute PAUSE via S-CLI (§3.3) — verify CLI exits `0` and command audit exists
5. Execute LIQUIDATE via S-CLI (§4.2) — verify canonical binding readback is `retired`
6. Execute REPLACE via S-EMRG (§5.1) using a known fallback artifact
7. Query audit log (§6) — confirm `audit_id`, `actor_id`, `liquidate`, and `replace` are present
8. Advance safe-mode to `recovery_testing` then `normal_restored` (§7)
9. Retire the test binding and confirm no live bindings are affected

The smoke harness in `scripts/smoke_operator_fallback_drills.py` covers the non-BFF control chain programmatically:

```bash
python3 scripts/smoke_operator_fallback_drills.py \
  --output-dir docs/deployment/evidence/operator-fallback-drills/$(date -u +%Y%m%dT%H%M%SZ)
```

Current checked evidence:

- `docs/deployment/evidence/operator-fallback-drills/20260504T022718Z/summary.json`
- `docs/deployment/evidence/operator-fallback-drills/20260504T022718Z/kill_switch_audit_log_response.json`

---

## 9. What is NOT in scope

- BFF restart procedure — handled by the platform/infra team separately
- Multi-replica BFF HA topology — deferred per `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §0
- Dual-control policy (two-operator approval) — post-v1 hardening item
- Production live-order placement — always fail-closed regardless of BFF state

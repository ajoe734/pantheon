# Operator Failover Guide

Manual operator procedure for `DEPLOY-009` dual-VM failover:

- VM-1 hosts the control-plane deployment and telemetry surfaces
- VM-2 hosts `runtime-manager` and the execution bootstrap containers

The runtime-manager is the only write owner for execution-side binding mutations.
All emergency actions therefore flow from VM-1 to VM-2 through the VM-2
`runtime-manager` HTTP API.

## Preconditions

- VM-2 `runtime-manager` is reachable on `http://<vm2-ip>:28081`
- You have the runtime-manager bearer token
- You know the affected `capital_pool_id` and, when available, the active `binding_id`
- VM-1 telemetry is configured with:

```bash
export PANTHEON_RUNTIME_MANAGER_URL="http://<vm2-ip>:28081"
export PANTHEON_RUNTIME_MANAGER_TOKEN="<same-runtime-manager-token>"
```

## 1. Confirm Execution Plane Health

```bash
curl -fsS http://<vm2-ip>:28081/__health__ | python3 -m json.tool
curl -fsS http://<vm2-ip>:28110/__health__ | python3 -m json.tool
```

Optional if the mock sidecars are published:

```bash
curl -fsS http://<vm2-ip>:28097/__health__ | python3 -m json.tool
curl -fsS http://<vm2-ip>:28098/__health__ | python3 -m json.tool
```

## 2. Emergency Kill-Switch From VM-1

Use this when the active runtime must stop accepting new entries immediately.

```bash
cat >/tmp/kill-switch.json <<'JSON'
{
  "reason": "operator_emergency_stop",
  "capital_pool_id": "pool-001",
  "actor_id": "operator-oncall",
  "binding_id": "rb-<active-binding-id>"
}
JSON

curl -fsS \
  -H "Authorization: Bearer <runtime-manager-token>" \
  -H "Content-Type: application/json" \
  -d @/tmp/kill-switch.json \
  http://<vm2-ip>:28081/api/kill-switch/dispatch | python3 -m json.tool
```

Expected outcome:

- `command.action_type` is typically `pause`
- `safe_mode_after` becomes `paused`
- `binding_action.binding.status` becomes `paused`

Verify safe mode directly:

```bash
curl -fsS \
  -H "Authorization: Bearer <runtime-manager-token>" \
  http://<vm2-ip>:28081/api/kill-switch/pool-001/safe-mode | python3 -m json.tool
```

Check audit evidence:

```bash
curl -fsS \
  -H "Authorization: Bearer <runtime-manager-token>" \
  http://<vm2-ip>:28081/api/kill-switch/audit-log | python3 -m json.tool
```

## 3. Roll Back to a Fallback Artifact

Once the pool is paused and the fallback artifact is approved, issue a canonical rollback on VM-2.

```bash
cat >/tmp/rollback.json <<'JSON'
{
  "current_binding_id": "rb-<paused-binding-id>",
  "action_type": "pause_then_replace",
  "replacement_plan_id": "plan-rollback-001",
  "replacement_plan_status": "approved",
  "replacement_deployment_mode": "paper",
  "replacement_artifact_id": "reg-fallback-001",
  "replacement_artifact_version": "1.1.9",
  "replacement_persona_capital_binding_id": "pcb-001",
  "replacement_persona_capital_binding_status": "active",
  "replacement_allowed_deployment_scope": "paper",
  "replacement_runtime_id": "paper-runtime-rollback-001",
  "loader_checks_passed": true
}
JSON

curl -fsS \
  -H "Authorization: Bearer <runtime-manager-token>" \
  -H "Content-Type: application/json" \
  -d @/tmp/rollback.json \
  http://<vm2-ip>:28081/api/rollback | python3 -m json.tool
```

Expected outcome:

- `old_binding.status = retired`
- `new_binding.status = active`
- `new_binding.rollback_parent = <old binding id>`

## 4. Emit Telemetry Back to VM-1

After rollback or deploy completion, post a telemetry event to VM-1 so the control plane can prove it sees the execution-plane result.

```bash
cat >/tmp/telemetry-event.json <<'JSON'
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_type": "rollback_completed",
  "created_at": "2026-04-18T00:00:00Z",
  "execution_mode": "paper",
  "environment": "paper",
  "deployment_stage": "paper",
  "binding_id": "rb-<replacement-binding-id>",
  "runtime_id": "paper-runtime-rollback-001",
  "capital_pool_id": "pool-001",
  "artifact_id": "reg-fallback-001",
  "artifact_version": "1.1.9",
  "plan_id": "plan-rollback-001",
  "persona_capital_binding_id": "pcb-001",
  "rollback_parent": "rb-<old-binding-id>",
  "rollback_action_type": "pause_then_replace",
  "target": {
    "registry_id": "reg-fallback-001",
    "strategy_id": "strat-001",
    "artifact_version": "1.1.9",
    "artifact_type": "model_artifact",
    "promotion_state": "paper"
  },
  "metrics": {
    "action": "rollback_completed"
  }
}
JSON

curl -fsS \
  -H "Content-Type: application/json" \
  -d @/tmp/telemetry-event.json \
  http://<vm1-ip>:38083/api/telemetry/ingest | python3 -m json.tool
```

Verify the telemetry service saw the event:

```bash
curl -fsS http://<vm1-ip>:38083/api/telemetry/stats | python3 -m json.tool
```

## 5. Recovery Testing and Return to Normal

After the fallback runtime is verified, advance safe mode:

```bash
cat >/tmp/safe-mode.json <<'JSON'
{
  "target_state": "recovery_testing",
  "actor_id": "operator-oncall",
  "note": "fallback artifact verified in paper validation"
}
JSON

curl -fsS \
  -H "Authorization: Bearer <runtime-manager-token>" \
  -H "Content-Type: application/json" \
  -d @/tmp/safe-mode.json \
  http://<vm2-ip>:28081/api/kill-switch/pool-001/safe-mode | python3 -m json.tool
```

Move to `normal_restored` only after the replacement runtime and telemetry path are both stable.

## 6. Preferred End-to-End Command

For a full acceptance pass, prefer the scripted flow:

```bash
bash scripts/smoke_test_dual_vm.sh \
  --control-deployment-url http://<vm1-ip>:8006 \
  --control-telemetry-url http://<vm1-ip>:38083 \
  --exec-runtime-manager-url http://<vm2-ip>:28081 \
  --exec-paper-runtime-url http://<vm2-ip>:28110 \
  --runtime-manager-token <runtime-manager-token> \
  --output-dir /tmp/pantheon/dual-vm-acceptance
```

That script records the request/response artifacts needed by
[`dual-vm-acceptance-results.md`](/home/edna/code/pantheon/docs/deployment/dual-vm-acceptance-results.md:1).

## Boundary Note

The current VM-2 paper runtime is a bootstrap stub. This guide therefore covers:

- binding creation
- kill-switch execution
- rollback execution
- telemetry backflow
- safe-mode lifecycle

It does not yet claim a full production-grade LEAN order loop. That remains a later execution-plane packaging milestone.

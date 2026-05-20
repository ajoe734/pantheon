# Review: HA-004-V2 BFF HA Failover Runbook

Reviewer: Claude
Date: 2026-05-19
Artifact: docs/operations/bff_ha_failover_runbook.md
Task status at time of review: review (handoff from Codex2)

## Verdict: APPROVED

## Scope Check

The runbook is scoped to active-passive failover rehearsal only. It explicitly states
it does not change dev/staging deployment baseline, does not enable production BFF
replicas, load balancer, or cutover. This is consistent with `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
§0 (topology deferred) and §8 v1 decisions.

## RTO/RPO Values

Runbook table matches `services/bff/ha/sla_targets.json` exactly:

| Environment | RTO (s) | RPO (s) |
|---|---:|---:|
| dev | 300 | 60 |
| staging | 120 | 30 |
| production | 60 | 10 |

## Procedure Coverage

Seven steps are present and logically sequenced:
1. Declare the drill and record SLA row
2. Capture pre-failover health (primary, passive, LB, operator health, shared cursors)
3. Freeze high-risk BFF commands with fail-closed posture
4. Shift traffic to passive BFF with RTO timer
5. Validate new active BFF (health, operator health, SSE reconnect with Last-Event-ID)
6. Assert RPO and command safety from shared cursor deltas
7. Restore or continue decision

## Fail-Closed Matrix

All required fail-closed conditions are documented:
- `503 IDEMPOTENCY_UNAVAILABLE` → no command dispatch
- `503 AUDIT_UNAVAILABLE` → no command dispatch
- `503 RUNTIME_MANAGER_UNAVAILABLE` → no runtime lifecycle commands
- `503 REGISTRY_GOVERNANCE_UNAVAILABLE` → no approval/deployment/capital commands
- `503 TELEMETRY_UNAVAILABLE` with stale read metadata → block high-risk commands
- SSE fanout cursor expired → stale/degraded realtime state, not pretended fresh
- RTO breach → SEV-1 for staging/production-gated rehearsals
- RPO unassertable → command freeze + manual reconciliation

The "Do not replay in-flight commands during the freeze" rule and the three-record
check (idempotency + audit handoff + backend receipt) before resuming are correctly
specified.

## Policy Consistency

- BFF outage isolation: runbook explicitly states BFF outage must not stop active
  runtimes, runtime-manager control flow, broker connectivity, kill-switch fast
  paths, or non-BFF emergency control paths. Consistent with policy §2.2.
- Non-BFF emergency path: referenced via `docs/deployment/operator-failover-guide.md`
  as precondition item 7. Consistent with policy §6.
- Production gate: production explicitly blocked until `HA-PROD-001-V2` human gate.
  Consistent with policy §0 re-entry gate.

## On-Call Escalation

Escalation table covers: T+0 drill start, T+2 passive failure, T+2 runtime path
failure, RTO/2 threshold decision, RTO breach → SEV-1, RPO unassertable, command
accepted without proof → SEV-1. Deadlines and escalation targets are named.

## Evidence Checklist

Evidence packet checklist is complete: timeline, SLA target JSON, health captures,
operator health, cursors, RTO calculation, RPO assertion, command freeze notes,
rollback/continue decision, follow-up list.

## Test Results

```
python3 -m pytest -q tests/docs/test_bff_ha_failover_runbook.py \
    tests/docs/test_bff_ha_topology_doc.py tests/bff/test_sla_targets.py
7 passed in 0.80s
```

## Notes for Owner (Closeout)

No required changes. The runbook is a complete ops artifact for this task scope.

The placeholder cursor commands (`<idempotency-store-cursor-command>`, etc.) in
§2 and §6 are intentionally environment-specific and not a defect; operators must
substitute real commands for their environment. This is the correct approach for a
pre-gate operations doc that covers multiple environments.

The task is returned to Codex2 (owner) for finalization and `done` transition.

# BFF HA Failover Runbook

Status: pre-gate operations artifact for `HA-004-V2`
Source: Phase 8 BFF HA planning brief Group C, `docs/bff/bff_ha_topology.md`,
and `services/bff/ha/sla_targets.json`
Scope: active-passive failover rehearsal, RTO/RPO assertion, on-call escalation,
and fail-closed command handling

This runbook defines the operator procedure for a BFF HA failover rehearsal. It
does not change the current dev or staging deployment baseline and does not
enable production BFF replicas, a production load balancer, or production
cutover. Per `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, current compose
deployments may remain single-replica until the HA PoC evidence and
`HA-PROD-001-V2` human gate are approved.

## Operating Boundary

The rehearsal validates an active-passive control-plane failover path:

- The primary BFF handles LB traffic before the drill.
- A passive BFF replica is healthy, warm, and ready to receive traffic.
- The operator drains or disables the primary target, enables the passive
  target, and proves the LB routes to the new active BFF.
- Shared idempotency, audit, and SSE fanout state remain external to both BFF
  replicas.

The target topology in `docs/bff/bff_ha_topology.md` remains active-active for
stateless reads. This active-passive drill is the conservative pre-gate proof
for route shifting and command safety. It is not a claim that production HA is
complete.

BFF outage affects the Management Console and operator UI only. It must not
stop active runtimes, runtime-manager internal control flow, broker
connectivity, kill-switch fast paths, or non-BFF emergency control paths.

## RTO/RPO Targets

Use the environment row from `services/bff/ha/sla_targets.json`.

| Environment | RTO seconds | RPO seconds | Assertion |
|---|---:|---:|---|
| `dev` | 300 | 60 | Failover completes within 300 seconds and shared BFF state loss is at most 60 seconds. |
| `staging` | 120 | 30 | Failover completes within 120 seconds and shared BFF state loss is at most 30 seconds. |
| `production` | 60 | 10 | Failover completes within 60 seconds and shared BFF state loss is at most 10 seconds. |

RTO timer starts when the primary BFF is declared unhealthy, drained, or
deliberately disabled for the drill. RTO timer stops when the LB endpoint and
the new active BFF pass the post-failover validation checklist.

RPO is the maximum acceptable loss window for BFF-owned operational state, not
domain state. Assert RPO with shared idempotency records, audit handoff cursor,
and SSE fanout cursor evidence. Canonical registry, governance, telemetry,
runtime, broker, and capital state are owned by their respective backend
services, not by BFF.

## Roles

| Role | Responsibility |
|---|---|
| Incident commander | Opens the drill or incident, owns the timeline, and decides abort versus continue. |
| BFF operator | Executes primary drain, passive activation, health checks, and rollback. |
| Runtime operator | Verifies runtime-manager, kill-switch, and non-BFF emergency paths remain healthy. |
| Risk owner | Approves continuing the drill when command or runtime safety is uncertain. |
| Scribe | Captures timestamps, commands, responses, RTO/RPO evidence, and follow-up actions. |

One person may hold multiple roles in dev, but staging and production-gated
rehearsals must name each role explicitly in the evidence packet.

## Preconditions

1. The rehearsal environment is dev, staging, or an approved HA PoC namespace.
   Production is forbidden unless `HA-PROD-001-V2` has approved the cutover.
2. The primary and passive BFF instances run the same release artifact and
   configuration except for replica identity.
3. The load balancer can independently drain the primary target and enable the
   passive target.
4. Shared idempotency and audit store health is `ok`.
5. Shared SSE fanout health is `ok`, and replay with `Last-Event-ID` is
   available for the drill channel.
6. Auth/OIDC/JWKS, Registry/Governance, Runtime Manager, and Telemetry/Incident
   dependencies are healthy or have an explicitly accepted degraded state.
7. The non-BFF emergency path is verified through
   `docs/deployment/operator-failover-guide.md`.
8. No production live runtime depends on the BFF for emergency control.
9. Operator tokens, audit reason strings, and evidence directory are prepared.
10. A rollback command for returning traffic to the pre-drill primary is ready.

Recommended evidence directory:

```bash
export ENVIRONMENT=staging
export EVIDENCE_DIR="support/evidence/HA-004-V2/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$EVIDENCE_DIR"
```

## Procedure

### 1. Declare The Drill

Record the declared environment and SLA row:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$EVIDENCE_DIR/t0-declared-at.txt"
jq ".targets[env.ENVIRONMENT] | {rto_seconds, rpo_seconds}" \
  services/bff/ha/sla_targets.json | tee "$EVIDENCE_DIR/sla-target.json"
```

Expected result:

- `rto_seconds` and `rpo_seconds` match the table in this runbook.
- Incident commander confirms whether this is a planned drill or an unplanned
  failover.
- Scribe starts the event timeline.

### 2. Capture Pre-Failover Health

Use environment-specific URLs:

```bash
export LB_URL="https://<bff-lb-host>"
export BFF_PRIMARY_URL="https://<primary-bff-host>"
export BFF_PASSIVE_URL="https://<passive-bff-host>"

curl -fsS "$BFF_PRIMARY_URL/health" | tee "$EVIDENCE_DIR/primary-health-before.json"
curl -fsS "$BFF_PASSIVE_URL/health" | tee "$EVIDENCE_DIR/passive-health-before.json"
curl -fsS "$LB_URL/health" | tee "$EVIDENCE_DIR/lb-health-before.json"
curl -fsS "$LB_URL/api/v1/operator/health-status" \
  | tee "$EVIDENCE_DIR/operator-health-before.json"
```

Capture shared-state cursors using the environment's store and fanout tools:

```bash
<idempotency-store-cursor-command> | tee "$EVIDENCE_DIR/idempotency-cursor-before.txt"
<audit-store-cursor-command> | tee "$EVIDENCE_DIR/audit-cursor-before.txt"
<sse-fanout-cursor-command> | tee "$EVIDENCE_DIR/sse-cursor-before.txt"
```

Expected result:

- Primary and passive BFF health probes are successful.
- The LB endpoint routes to the primary before the drill.
- Operator health status is `ok` or explicitly `degraded` with a known source.
- Shared idempotency, audit, and SSE cursors are recorded.

### 3. Freeze High-Risk BFF Commands

Before shifting traffic, freeze non-emergency command submission through the BFF
or announce a short command freeze window. Emergency actions remain available
through the non-BFF secondary control path.

Do not replay in-flight commands during the freeze. For each active command:

1. Read the shared idempotency record.
2. Read the audit handoff record.
3. Read the owning backend receipt.
4. Resume only when all three records agree on the command identity and status.

If shared idempotency or audit is unavailable, the posture is fail-closed:
`503 IDEMPOTENCY_UNAVAILABLE` or `503 AUDIT_UNAVAILABLE`; No command dispatch.

### 4. Shift Traffic To Passive BFF

Start the RTO timer immediately before the primary drain or disable command:

```bash
date -u +%s | tee "$EVIDENCE_DIR/rto-start-epoch.txt"
<lb-drain-primary-target-command> | tee "$EVIDENCE_DIR/lb-drain-primary.txt"
<lb-enable-passive-target-command> | tee "$EVIDENCE_DIR/lb-enable-passive.txt"
```

Poll the LB endpoint until it reaches the new active BFF:

```bash
for attempt in $(seq 1 30); do
  date -u +%Y-%m-%dT%H:%M:%SZ
  curl -fsS "$LB_URL/health" && break
  sleep 2
done | tee "$EVIDENCE_DIR/lb-health-during-failover.txt"
```

Expected result:

- The primary is drained or disabled.
- The passive BFF becomes the active LB target.
- The LB `/health` probe succeeds before the environment RTO expires.

### 5. Validate New Active BFF

Run a read validation, an operator health validation, and an SSE reconnect
validation:

```bash
curl -fsS "$LB_URL/health" | tee "$EVIDENCE_DIR/lb-health-after.json"
curl -fsS "$LB_URL/api/v1/operator/health-status" \
  | tee "$EVIDENCE_DIR/operator-health-after.json"
curl -fsS -H "Last-Event-ID: <last-event-id-before-failover>" \
  "$LB_URL/bff/events?channel=operator" \
  | tee "$EVIDENCE_DIR/sse-reconnect-after.txt"
date -u +%s | tee "$EVIDENCE_DIR/rto-stop-epoch.txt"
```

Expected result:

- The LB endpoint is healthy.
- `/api/v1/operator/health-status` returns backend-supplied health state and
  secondary control path guidance when any surface is degraded.
- SSE reconnect either resumes from the recorded cursor or reports stale or
  degraded realtime state. It must not pretend a missing cursor is fresh.
- RTO is less than or equal to the target for the environment.

### 6. Assert RPO And Command Safety

Capture post-failover cursors:

```bash
<idempotency-store-cursor-command> | tee "$EVIDENCE_DIR/idempotency-cursor-after.txt"
<audit-store-cursor-command> | tee "$EVIDENCE_DIR/audit-cursor-after.txt"
<sse-fanout-cursor-command> | tee "$EVIDENCE_DIR/sse-cursor-after.txt"
```

RPO passes only when:

- The newest shared idempotency record before failover is present after
  failover.
- The audit cursor after failover is not behind the recorded pre-failover
  cursor by more than the environment RPO.
- The SSE fanout cursor either replays through `Last-Event-ID` or returns an
  explicit stale/degraded result.
- No command was accepted twice for the same idempotency key.
- No command was dispatched without audit handoff.

RPO fails if any shared cursor is unavailable or cannot be compared. Treat that
as a fail-closed drill result and keep BFF command submission frozen until the
incident commander, BFF operator, runtime operator, and risk owner complete
manual reconciliation.

### 7. Restore Or Continue

For a planned drill, restore the pre-drill target after evidence capture:

```bash
<lb-enable-primary-target-command> | tee "$EVIDENCE_DIR/lb-enable-primary.txt"
<lb-drain-passive-target-command> | tee "$EVIDENCE_DIR/lb-drain-passive.txt"
curl -fsS "$LB_URL/health" | tee "$EVIDENCE_DIR/lb-health-restored.json"
```

For an unplanned incident, continue on the new active BFF only when:

- RTO passed.
- RPO passed.
- Shared idempotency and audit stores are healthy.
- Runtime Manager and non-BFF emergency paths are healthy.
- The incident commander records the decision in the evidence packet.

## Fail-Closed Matrix

| Condition | Required response | Operator action |
|---|---|---|
| Shared idempotency store unavailable | `503 IDEMPOTENCY_UNAVAILABLE`; No command dispatch. | Keep command freeze, verify backend receipts, escalate to risk owner. |
| Audit handoff unavailable | `503 AUDIT_UNAVAILABLE`; No command dispatch. | Keep command freeze, reconcile audit cursor, do not replay commands. |
| Runtime Manager unavailable | `503 RUNTIME_MANAGER_UNAVAILABLE`; No runtime lifecycle command dispatch. | Use non-BFF runtime-manager diagnostics if reachable; otherwise escalate to runtime operator. |
| Registry/Governance unavailable | `503 REGISTRY_GOVERNANCE_UNAVAILABLE`; no approval/deployment/capital command dispatch. | Keep governance commands disabled and record degraded UI state. |
| Telemetry/Incident unavailable | `503 TELEMETRY_UNAVAILABLE` with stale read metadata. | Block high-risk commands that depend on fresh runtime health. |
| SSE fanout cursor expired | Reconnect reports stale/degraded realtime state. | Re-sync through read endpoints and keep UI realtime marked degraded. |
| RTO breach | Drill fails. | Escalate to SEV-1 for staging or production-gated rehearsals. |
| RPO cannot be asserted | Drill fails. | Keep command freeze and perform manual reconciliation before accepting commands. |

## On-Call Escalation

| Trigger | Deadline | Escalate to | Required note |
|---|---:|---|---|
| Primary BFF unhealthy or planned drill starts | T+0 | Incident commander and BFF operator | Environment, target RTO/RPO, primary/passive IDs. |
| Passive BFF health fails | T+2 minutes | BFF operator lead | Abort or provision a known-good passive target. |
| Runtime Manager or secondary control path unhealthy | T+2 minutes | Runtime operator | Confirm kill-switch, pause, rollback, and health diagnostics path. |
| RTO reaches 50 percent with no healthy LB target | RTO/2 | Incident commander and risk owner | Decide continue, abort, or emergency fallback. |
| RTO breach | RTO target | SEV-1 bridge | Freeze BFF commands and start rollback or platform incident procedure. |
| RPO cannot be proven | Immediate | Risk owner and runtime operator | Keep command freeze; reconcile idempotency, audit, and backend receipts. |
| Any command accepted without audit or idempotency proof | Immediate | SEV-1 bridge | Treat as safety incident; block further BFF command dispatch. |

## Evidence Packet Checklist

The scribe must publish these artifacts before the drill can be accepted:

- `timeline.md` with T0, RTO start, RTO stop, and restore/continue decision.
- `sla-target.json` copied from `services/bff/ha/sla_targets.json`.
- Primary, passive, and LB health before and after failover.
- Operator health status before and after failover.
- Idempotency, audit, and SSE cursor before and after failover.
- RTO calculation in seconds and pass/fail result.
- RPO assertion summary and pass/fail result.
- Command freeze window and any in-flight command reconciliation notes.
- Rollback or continue decision signed by incident commander.
- Follow-up list for every failed or manually reconciled check.

## Abort And Rollback Criteria

Abort the drill and restore the pre-drill primary when any of these occur:

- Passive BFF health is not clean before traffic shift.
- Shared idempotency, audit, or SSE fanout is unavailable before the shift.
- Runtime Manager or secondary control path is unavailable.
- The LB cannot route to the passive BFF before the RTO target.
- RPO cannot be asserted from shared store and fanout evidence.
- A command dispatch would require guessing, replaying without idempotency, or
  accepting missing audit evidence.

Rollback is successful only when the pre-drill primary passes `/health`, the LB
routes traffic to the intended target, operator health status is available, and
all command freeze or reconciliation notes have an owner.

## Closeout

After a successful drill, the incident commander closes the packet with:

1. Environment and release artifact identities.
2. RTO target, observed RTO, and pass/fail.
3. RPO target, observed RPO evidence, and pass/fail.
4. Command safety summary.
5. SSE reconnect summary.
6. On-call escalation summary.
7. Decision: rollback complete, continue on new active BFF, or failed drill.

Do not use this runbook as production cutover approval. Production remains
blocked until the HA PoC, evidence, and `HA-PROD-001-V2` human gate approve it.

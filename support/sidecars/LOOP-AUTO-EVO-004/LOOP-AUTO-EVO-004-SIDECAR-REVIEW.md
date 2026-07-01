# Sidecar Review Packet: LOOP-AUTO-EVO-004

Sidecar task: LOOP-AUTO-EVO-004-SIDECAR-REVIEW
Parent task: LOOP-AUTO-EVO-004 — Dispatch approved evolution actions through gates
Helper kind: review_packet
Owner: Claude2
Reviewer: Claude
Generated: 2026-06-27
Status: reviewed and approved — closeout complete 2026-06-27

## Scope

This packet is support-only. It summarizes the parent task scope, current implementation state, identified gaps, and readiness posture for Claude as the assigned reviewer. It does not modify canonical truth, L1 policy, runtime adapters, registry/governance behavior, or the parent task's implementation.

Primary artifacts surveyed:

- `services/evolution/main.py`
- `services/evolution/sweep.py`
- `services/evolution/postmortem_bridge.py`
- `services/evolution/models.py`
- `services/control-plane/governance/evolution_controller.py`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `ai-status.json` tasks `LOOP-AUTO-EVO-004`, `LOOP-AUTO-EVO-002`, `LOOP-AUTO-DEP-001`

## Parent Task Summary

**Title:** Dispatch approved evolution actions through gates
**Phase:** Global Loop Autopilot / Wave 5 Postmortem Evolution
**Owner:** Claude
**Reviewer:** Codex
**Status:** todo
**Current maturity:** api-only → **Target maturity:** reconciled
**Loop IDs:** evolution, promotion_deployment
**Depends on:** LOOP-AUTO-EVO-002, LOOP-AUTO-DEP-001

**Acceptance criteria:**
1. Approved action dispatches only through allowed gated path
2. Production-affecting mutation requires correct approval gate
3. Dispatch result is visible in EvolutionDecision follow-through

## Dependency Status

Both upstream dependencies are currently `todo`. This means LOOP-AUTO-EVO-004 is blocked until they deliver:

| Dependency | Title | Status | Blocking aspect |
|---|---|---|---|
| LOOP-AUTO-EVO-002 | Bridge postmortems into evolution proposals | todo | Postmortem→EvolutionDecision proposal path not yet durable; the postmortem bridge module exists but proposal admission flow needs the outbox/event-driven wiring |
| LOOP-AUTO-DEP-001 | Add deployment saga outbox consumer | todo | Deployment outbox consumer not yet live; gated evolution dispatch to the deployment plane cannot be validated without a working outbox consumer |

The parent task owner (Claude) should not begin implementation until LOOP-AUTO-EVO-002 and LOOP-AUTO-DEP-001 are merged. This sidecar packet is prepared ahead of time to reduce friction when those dependencies land.

## Current Implementation State

The evolution service has substantial machinery already in place:

### What exists

1. **Full decision lifecycle API** (`services/evolution/main.py`):
   - `POST /api/evolution/proposals` — propose
   - `POST /api/evolution/proposals/{id}/review` — mark reviewed
   - `POST /api/evolution/proposals/{id}/approve` — approve
   - `POST /api/evolution/proposals/{id}/execute` — execute (see gap below)
   - `GET /api/evolution/proposals/{id}/boundary` — query execution plane and approval gate

2. **EvolutionController** (`services/control-plane/governance/evolution_controller.py`):
   - `dispatch_approved()` — computes `DispatchCommand` (with `execution_plane`: `research`, `deployment`, or `runtime`), `RollbackCommand`, and `followthrough_commands`
   - `execute_approved()` — transitions decision to `executed` state, embeds `execution_result` with dispatch summary
   - `boundary_for()` — returns `ActionBoundary` with `followthrough` spec and approval gate roles

3. **`DispatchCommand` structure** has `execution_plane`, `action_type`, `target_type`, `target_id`, `target_version`, `cooldown_ends_at`, `observation_window_ends_at`

4. **Postmortem bridge** (`services/evolution/postmortem_bridge.py`) — builds proposal dicts from postmortem-published events; does NOT write to governance store directly

5. **Daily sweep worker** (`services/evolution/sweep.py`) — reads `IncidentCase` records, generates proposals via existing `EvolutionDecisionStore`; scheduler metadata present (`compose_profile: evolution-daily-sweep-scheduler`)

### The Critical Gap

The `/execute` endpoint calls `execute_approved()` and records the decision as `executed`. However, **the `DispatchCommand` objects are written into the decision's `execution_result` but are never forwarded to the downstream service APIs**:

- Research plane (`execution_plane=research`): no call to `services/research-worker-gateway` or research job API
- Deployment plane (`execution_plane=deployment`): no call to `services/deployment` outbox/saga
- Runtime plane (`execution_plane=runtime`): no call to `services/runtime-manager` rollback/freeze API

The `boundary_for()` and `dispatch_approved()` methods correctly identify the gated path and produce structured commands, but they stop short of the actual HTTP integration layer. The evolution service currently "knows what to do" but does not do it.

## Acceptance Criteria Gap Analysis

| Acceptance criterion | Current state | Gap |
|---|---|---|
| Approved action dispatches only through allowed gated path | `boundary_for()` + `dispatch_approved()` produce correct commands and gate metadata; `/boundary` endpoint exposes this for pre-flight | No actual downstream HTTP dispatch; commands are data only |
| Production-affecting mutation requires correct approval gate | `ActionBoundary.approved_owner_roles` and `reviewed_owner_roles` are enforced in domain objects; `execute_approved()` validates role membership | Gate is enforced at decision level but not at the downstream service level; gated dispatch adapter is absent |
| Dispatch result is visible in EvolutionDecision follow-through | `execution_result.outcome_summary` is set; `followthrough_commands` are embedded | Dispatch outcome records a local execution result, not the downstream service acknowledgement; round-trip confirmation absent |

## What LOOP-AUTO-EVO-004 Needs to Implement

At minimum, the parent task must deliver:

1. **Gated dispatch adapter(s)**: When the execute endpoint runs and a `DispatchCommand` has `execution_plane=research|deployment|runtime`, the service must call the appropriate downstream API:
   - `research` → POST to research-worker-gateway (governed research job)
   - `deployment` → emit to deployment outbox or POST to deployment saga endpoint
   - `runtime` → POST to runtime-manager rollback/freeze command endpoint

2. **Gate enforcement at dispatch time**: Before calling the downstream API, verify that the action type and target stage combination passes the correct approval gate (cross-check with `boundary.approved_owner_roles`). Reject dispatch if the decision is not in `approved` state or if the action type does not match the boundary.

3. **Follow-through result recording**: Record the downstream service acknowledgement (HTTP status + response ID) back into the `EvolutionDecision.execution_result` so `follow-through` is observable via `/api/evolution/proposals/{id}`.

4. **Idempotency**: Duplicate dispatch calls for the same `decision_id` must not create duplicate downstream commands. Use `command_id` as the idempotency key.

## Suggested File Scope

These files are likely to need changes or additions for LOOP-AUTO-EVO-004:

| File | Nature of change |
|---|---|
| `services/evolution/main.py` | Extend `/execute` endpoint to call dispatch adapters; record downstream acknowledgement |
| `services/evolution/dispatch_adapter.py` (new) | HTTP client shim to research-worker-gateway, deployment saga, runtime-manager APIs |
| `services/evolution/models.py` | Add `downstream_ack` fields to `ExecutionResult` or `DispatchCommandResponse` |
| `services/evolution/test_*.py` | Unit tests for dispatch adapter; contract tests for gate enforcement and idempotency |
| `docs/deployment/evidence/` | Evidence packet: test output, gate enforcement log, follow-through confirmation |

Files that must NOT change in this task:
- `services/control-plane/governance/evolution_controller.py` — boundary is already correct; do not expand scope
- `services/control-plane/governance/evolution_decision.py` — decision state machine is correct
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md` — L1 canonical truth; no change unless a genuine policy error is found
- `services/deployment/` core saga — any changes must go through LOOP-AUTO-DEP-001 or a separate task

## Policy Constraints (from EVOLUTION_REVIEW_AND_THRESHOLDS.md)

- `EvolutionDecision` must be in `approved` state before dispatch
- Production-affecting mutations (`deploy`, `rollback`, `freeze`) require correct `approved_owner_roles`
- No direct production mutation without the approval gate pass
- Dispatch to `runtime` plane must not bypass `ROLLBACK_AND_POSITION_SEMANTICS.md` rollback action semantics

## Reviewer Attention Items

1. **Dependencies not yet merged.** LOOP-AUTO-EVO-002 and LOOP-AUTO-DEP-001 are both `todo`. Claude should confirm these are in flight or schedule accordingly before starting LOOP-AUTO-EVO-004 implementation. Beginning implementation while dependencies are absent will require mock downstream services, which may drift from the actual API contract.

2. **Dispatch adapter must be idempotent.** `command_id` generated by `dispatch_approved()` is the correct idempotency key. The adapter must pass it as the downstream correlation ID and handle 409/duplicate responses gracefully. This must be verified by test, not just code inspection.

3. **Gate check must happen at dispatch time, not just at approve time.** The `/execute` endpoint currently validates actor role; the dispatch adapter must also verify the `execution_plane` matches the action boundary before making the downstream call. A mismatch should return 422 and not advance the decision to `executed`.

4. **Follow-through recording must survive partial failures.** If the downstream call succeeds but the local state write fails, the decision may be re-dispatched on retry. The adapter must be safe for this race. Recommend: write `execution_result` only after getting a 2xx from downstream.

5. **Research-plane dispatch semantics.** The `notes` field on `ActionBoundary` for research actions says: "Execution means a governed research work item or job was accepted; no deploy/runtime mutation occurs here." The dispatch adapter for research must not trigger any paper/canary/live/broker/capital path. This is a safety invariant.

6. **Evidence packet scope.** The proof requirement for this task includes "unit tests, contract tests, local service smoke, restart or replay evidence when worker or runtime behavior changes". The evidence packet must include: gate enforcement test output, idempotency test output, follow-through recording test output, and at minimum a local smoke demonstrating the dispatch adapter reaches the correct downstream endpoint (even with a test double).

7. **Maturity claim.** The target maturity is `reconciled`. This requires a working desired-state query, actual-state query, and restart behavior, per the dispatch rules. Panel-only closure or seed-fixture evidence is not accepted.

## Handoff Recommendation

This packet is ready for Claude (sidecar reviewer) to review and then pass to the parent task team.

Suggested actions for the parent task owner (Claude) before implementation:

- Confirm LOOP-AUTO-EVO-002 and LOOP-AUTO-DEP-001 are actively tracked and ETA is known
- Pre-read the deployment service saga contract (LOOP-AUTO-DEP-001 artifacts) and runtime-manager dispatch API before designing the adapter layer
- Do not stub downstream APIs as permanent fixtures in the dispatch adapter — use real integration or explicit `activation_ready=false` stubs that produce explicit dependency errors
- Run the existing evolution service tests before touching `main.py`:
  ```bash
  python3 -m pytest services/evolution/test_evolution_service.py -v
  python3 -m pytest services/evolution/test_postmortem_bridge.py -v
  ```

Suggested review posture for Codex (parent task reviewer):

- Verify that the dispatch adapter has no direct production mutation path outside the gated boundary
- Verify that `command_id` is used as the idempotency key end-to-end
- Verify that the evidence packet contains actual test output, not just code

## Closeout Note

This sidecar packet is produced by Claude2 as a pre-implementation briefing. It does not change canonical truth, L1 policy, runtime adapters, or the parent task's review record. When LOOP-AUTO-EVO-004 is implemented and reviewed, this packet should be superseded by the parent task's acceptance packet.

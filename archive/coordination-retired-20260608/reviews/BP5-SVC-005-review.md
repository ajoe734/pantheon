# Review: BP5-SVC-005 — Deployment Orchestration Saga with Outbox and Inbox Consistency

**Reviewer:** Claude
**Task:** BP5-SVC-005
**Date:** 2026-04-15
**Decision:** APPROVED

---

## Acceptance Criteria Verification

### AC-1: Deployment writes and event publication use an explicit transactional outbox path

**PASSED.**

`DeploymentSagaStore._transaction` (`deployment_saga.py:719–733`) implements a draft-copy-validate-persist pattern:
1. Deep-copy current state into a draft.
2. Run the mutator (which appends outbox events atomically to the same draft).
3. Validate state invariants.
4. Persist atomically via `write-tmp-then-rename` (`_persist_draft`, lines 884–895).
5. Commit the draft to in-memory state only after persist succeeds.

Every saga write (`bootstrap_for_plan`, `record_binding_created`, `record_runtime_active`, `record_failure`, `finalize_compensation`) appends outbox events inside the same `_transaction` call, so business state and outbox event are never split across separate writes.

### AC-2: Cross-service deploy and rollback flows document and test compensation and idempotent replay behavior

**PASSED.**

- `determine_compensation` (`deployment_saga.py:935–982`) provides an owner-scoped compensation matrix for all four saga steps:
  - `BINDING_REQUESTED` → `ABORT_PLAN` (governance-svc writes plan only)
  - `RUNTIME_LOAD_REQUESTED` → `MARK_BINDING_FAILED_INACTIVE` (runtime-manager-svc)
  - `RUNTIME_ACTIVE` → `REQUEST_ROLLBACK` (rollback-controller, using `rollback_action_type` from plan)
  - `COMPENSATION_REQUESTED` → `ENTER_SAFE_MODE_AND_RAISE_INCIDENT` (runtime-manager-svc + incident)

- `_build_receipt` (`deployment_saga.py:782–872`) implements correct idempotent consumer behavior:
  - Duplicate by `event_id` → `DUPLICATE`
  - Duplicate by `idempotency_key` → `DUPLICATE`
  - Sequence already applied → `DUPLICATE`
  - Gap in sequence → `OUT_OF_ORDER`
  - In-order next event → `APPLIED`

- Test `test_saga_progress_and_inbox_replay_receipts` (`test_service.py:368–451`) exercises all five receipt states in one scenario.
- Test `test_post_activation_failure_uses_plan_rollback_action_and_finalize` (`test_service.py:454–507`) verifies the compensation path for `RUNTIME_ACTIVE` failure with `pause_then_replace` rollback action.

---

## Policy Compliance

### CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md

| Policy requirement | Implementation | Status |
|---|---|---|
| Local ACID + outbox, no distributed transaction | `_transaction` with draft-copy-commit | ✓ |
| Orchestration-first saga | `DeploymentOrchestrationService` as the orchestrator; owner_service per event | ✓ |
| Explicit intermediate states | `SagaStatus` enum: `AWAITING_BINDING`, `AWAITING_RUNTIME_LOAD`, `COMPENSATING`, `FAILED`, `ABORTED`, `COMPLETED` | ✓ |
| Compensation command catalog | `determine_compensation` with 4 paths and owner scopes | ✓ |
| Idempotent consumer | `_build_receipt` with event_id + idempotency_key + sequence dedup | ✓ |
| Failure not silent | Outbox event for `COMPENSATION_REQUESTED`; `ENTER_SAFE_MODE_AND_RAISE_INCIDENT` raises incident | ✓ |

### EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md

| Policy requirement | Implementation | Status |
|---|---|---|
| Required event envelope fields | `SagaEventEnvelope` has all 10 required fields + `causal_parent_id` | ✓ |
| Per-aggregate `sequence_no` monotonic increment | `last_sequence_no + 1` per saga, enforced in `_append_outbox_event` | ✓ |
| `causal_parent_id` linking events | Set to `saga.last_event_id` before each event | ✓ |
| `idempotency_key` as `saga_id:seq:event_type` | Deterministic, replayable | ✓ |
| At-least-once + idempotent consumer | Inbox dedup handles re-delivery | ✓ |
| `out_of_order` detection | Gap check in `_build_receipt` | ✓ |

---

## Test Run

```
~/.local/bin/pytest services/deployment/test_service.py -q
............
12 passed in 1.95s
```

All 12 tests pass.

---

## Minor Observations (Non-blocking)

1. **`_find_outbox_event_by_event_id` only scans pending outbox** (`service.py:506–509`). If an event relay marks an outbox record as `published` before all consumers have consumed it via the HTTP API, the consume endpoint would return 404. This is acceptable for the current file-backed MVP (outbox relay is external and out of scope here), but should be revisited when a real relay is wired.

2. **`record_runtime_active` accepts `AWAITING_BINDING` as a valid precondition** (`deployment_saga.py:576`). This allows skipping the explicit binding-created step in environments where binding and runtime activation happen atomically. The policy does not forbid this; the practical effect is a shorter history.

3. **No `status=published` outbox read API.** Published/failed outbox records are not exposed via the HTTP surface. This is by design for the MVP — the relay owns that state — but an operational query API may be needed later.

---

## Verdict

Implementation correctly realizes the DEP-002 consistency layer as specified in both L1 policy documents. Both acceptance criteria are fully met. Tests cover the golden path, idempotent replay, out-of-order delivery, compensation with rollback action propagation, and dispatch idempotency. No blocking issues.

**Approved and returned to Codex for finalization.**

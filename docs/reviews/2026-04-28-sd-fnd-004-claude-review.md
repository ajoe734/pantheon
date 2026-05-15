# SD-FND-004 Review — Foundation adoption inventory and service rollout

Reviewer: Claude
Date: 2026-04-28
Owner: Codex2
Disposition: APPROVED

## Acceptance Verification

### 1. Adoption matrix checked in

`docs/reviews/2026-04-28-sd-fnd-004-foundation-adoption-matrix.md` is present
on disk and enumerates every path the packet asked for:

- pilot-complete: BFF operator command admission, runtime-manager kill-switch
  dispatch
- newly adopted in this slice: deployment plan dispatch
  (`POST /api/deployment/plans/{plan_id}/dispatch`)
- deferred: deployment saga progress, governance approval-decision module,
  governance deployment-plan create/status, promotion registry, capital write
  authority, source/evidence/search first-slice writes, source ingestion
  scheduler, consultation lifecycle writes, BFF/runtime consultation workflows
- intentionally excluded: telemetry ingest DLQ, with explicit pointer to
  SD-FND-003 review and canonical telemetry policy

Each row carries the owner, risk class, and required follow-up evidence the
packet calls for.

### 2. New non-pilot path uses foundation primitives

Deployment plan dispatch now flows through the shared foundation primitives
end-to-end (`services/deployment/service.py`):

- `_build_dispatch_foundation_context` constructs `TraceContext`,
  `CommandEnvelope`, `IdempotencyRecord`, `PolicyDecision`, and `AuditAction`
  from `services.foundation`. No primitives are re-implemented locally.
- The foundation trace_id is propagated into the deployment saga and the
  bootstrap outbox event (verified in
  `test_dispatch_records_foundation_context_and_replays_idempotently`).
- The serialized foundation context is stored in saga metadata under
  `metadata.foundation` with the idempotency record transitioned to
  `succeeded` and a `result_ref` pointing at the saga id.
- Replay vs. conflict is delegated to the foundation `request_hash` via
  `_ensure_dispatch_replay_matches_foundation`: same key + same hash returns
  `replayed=True`; same key + different hash raises a foundation
  `ErrorEnvelope` with `error_kind=idempotency_conflict`.
- Unapproved plan dispatch attempts are rejected through
  `ErrorEnvelope.policy_denial` plus a paired `PolicyDecision(decision=DENY)`
  and `AuditAction(action_type="deployment.dispatch.policy_denied")`.

### 3. Targeted tests prove success, replay, and rejection

Three new tests in `services/deployment/test_service.py` directly cover the
acceptance shape, and all run green:

- `test_dispatch_records_foundation_context_and_replays_idempotently` —
  success path plus idempotent replay of the same payload; asserts foundation
  trace_id on saga outbox event and persisted `metadata.foundation` block.
- `test_dispatch_rejects_idempotency_key_reuse_with_foundation_error` —
  same idempotency key with a mutated payload returns HTTP 409 with
  `foundation_error.error_kind=idempotency_conflict` and a paired audit
  action.
- `test_dispatch_unapproved_plan_returns_foundation_policy_error` — draft
  plan returns HTTP 403 with `foundation_error.error_kind=policy_denial`,
  `policy_decision.decision=deny`, and an audit action whose
  `policy_decision_ref` matches the decision id.

## Test Run

Executed locally on commit at `715f7cd` worktree:

| Suite | Result |
|---|---|
| `services/foundation/tests/` | 25 passed |
| `services/deployment/test_service.py` | 15 passed |
| `services/runtime-manager/test_runtime_manager.py` + `services/control-plane/bff/test_governance_command_submission.py` + `services/control-plane/bff/test_command_executor.py` | 70 passed |

Total: 110 passed, 0 failed. Codex2's reported 74-pass scope is a strict
subset; no regression observed in the broader pilot adoption suite.

## Implementation Notes (non-blocking)

- The replay branch (`existing is not None`) intentionally does not rewrite
  the saga's stored `metadata.foundation` block. This preserves the canonical
  first-dispatch evidence and avoids drifting `first_seen_at` /
  `result_ref`. Replay attempts that do not match the original
  request_hash now fail loudly via `ErrorEnvelope`; matching attempts return
  `replayed=True` with the original foundation evidence. This matches
  `IdempotencyRecord` semantics from `services/foundation/idempotency.py`.
- `DispatchDeploymentPlanRequest` gains three optional fields
  (`correlation_id`, `idempotency_key`, `actor_id`). Backward-compatible:
  existing callers that only send `trace_id` continue to dispatch via the
  trace-only path (`test_dispatch_bootstraps_saga_and_persists_outbox` and
  `test_dispatch_is_idempotent_for_existing_saga` still pass).
- `services/control-plane/bff/command_queue.py` adds a
  `foundation_context` field on stored commands and a
  `get_command_by_idempotency_key` lookup. Not yet wired into the deployment
  flow, but flagged in the matrix as a forward path for the deferred BFF
  command-store rows. Acceptable as additive surface for the next adoption
  slice.

## Disposition

All three acceptance criteria are satisfied and verified by tests. Approving
and handing back to Codex2 for finalization to `done`.

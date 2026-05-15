# Review: FE-INT-GATE-D01 — F10 Rollback Saga dry-run and stepper

**Reviewer:** Claude
**Owner:** Codex
**Artifact:** execute-plans/e2e/10-rollback-saga.spec.ts
**Date:** 2026-05-13
**Decision:** APPROVED

---

## Acceptance Criteria Verification

### AC1: dry-run 顯示 eligibility/blast/gates ✅

Test 1 (`dry-run API exposes eligibility, blast radius, and required gates`) and the UI test both cover this:
- `assertRollbackDryRunDto` validates: `eligibility.eligible === true`, `eligibility.blockers` is empty, `blast_radius.affected_bindings_count >= 1`, `blast_radius.affected_open_positions_count >= 0`, `blast_radius.requires_position_freeze` is boolean.
- `required_gates` array must include `["approval", "confirm_token", "runtime-manager-ready"]`.
- UI test asserts body contains `/eligible/i`, `/blast radius/i`, `/required gates/i`, `/runtime-manager-ready/i` after the dry-run command returns.

### AC2: execute 回 RollbackSagaDTO ✅

Test 2 (`execute API returns RollbackSagaDTO with stepper state`) covers this:
- `assertRollbackSagaDto` validates `saga_id`, `rollback_id`, `status` (one of 6 valid states), `action_type`, steps array (must include dry_run/pause_current_binding/create_replacement_binding/cutover_and_audit), per-step `status` and `owner`, and `compensation` (state/owner/actions).
- `acceptedSagaDto` fixture has `status: "in_progress"` with 4 steps and `compensation.state: "not_required"`.

### AC3: SSE stepper 更新 ✅

The UI test (`dry-run review renders gates and advances the saga stepper from SSE`) covers this:
- `installRollbackSagaFixtureRoutes` mocks `SSE_STREAM_PATH` with `sseEvents` (rollback.saga.started → rollback.saga.step_updated for pause_current_binding).
- After execute, asserts body contains saga ID, `/pause current binding/i`, and `/completed/i`, confirming the stepper advances via SSE step updates.

### AC4: failure 顯示 failureReasonCode + compensation state ✅

Test 4 (`failure UI renders failureReasonCode and compensation state`) covers this:
- `failedSagaDto` sets `failureReasonCode: "RUNTIME_BINDING_CREATE_FAILED"`, `compensation.state: "in_progress"`, and `compensation.actions: ["resume_old_binding", "enter_safe_mode", "raise_incident"]`.
- `failureSseEvent` carries `rollback.saga.failed` type with the same `failureReasonCode` and compensation.
- Page body assertions check `/RUNTIME_BINDING_CREATE_FAILED/i`, `/compensation/i`, `/safe_mode_requested|enter_safe_mode|resume_old_binding/i`.

### AC5: backend 未 ready 用 test.fixme + annotation ✅

- Suite-level `test.fixme(!BACKEND_READY, BACKEND_NOT_READY_REASON)` gates the entire describe block when `F10_ROLLBACK_SAGA_BACKEND_READY` is unset.
- Each test also carries a per-test annotation `{ type: "BACKEND-NOT-READY", description: BACKEND_NOT_READY_REASON }` when not ready.
- Activation path clearly documented in the file header.

---

## Technical Review

**Type completeness:** `RollbackDryRunDTO`, `RollbackSagaDTO`, `RollbackSagaEvent`, and `RollbackStepDTO` are fully typed. `RollbackStepStatus` and `RollbackActionType` union types match the policy spec.

**Dual snake_case/camelCase tolerance:** All assertion helpers handle both naming conventions (e.g., `dry_run_id ?? dryRunId`, `blast_radius ?? blastRadius`, `step_id ?? stepId ?? id`, `saga_id ?? sagaId`). This makes the spec resilient against frontend naming variation.

**Command headers:** All required auth/trace fields are present in `commandHeaders`: `Idempotency-Key`, `X-Confirm-Token`, `X-Correlation-Id`, `X-MFA-Token`, `X-Request-Id`, `X-Trace-Id`.

**Route mock completeness:** `installRollbackSagaFixtureRoutes` handles `/bff/me`, `/health`, ROLLBACK_REVIEW_PATH, `/api/v1/rollbacks`, `/api/v1/runtimes/:id/rollbacks`, `COMMANDS_PATH` (POST), `SSE_STREAM_PATH`, and OPTIONS preflight. Unmatched routes fall through to `route.continue()`.

**`envelope<T>` helper:** Correctly wraps `CommandResponse` with `status: "accepted"`, `data`, and `meta` including `contract: "FE-INT-GATE-D01"`.

**Verification evidence (from handoff):** esbuild bundle passed, Playwright load ran 4 skipped as expected (BACKEND_READY=0), git diff --check passed.

---

## Notes

All five acceptance criteria are fully covered. The spec is well-structured: two API-level contract tests (dry-run + execute), one UI saga stepper test with mocked SSE, and one failure/compensation UI test. No issues found.

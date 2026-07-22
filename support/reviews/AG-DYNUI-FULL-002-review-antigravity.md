# AG-DYNUI-FULL-002 Review — Antigravity

Reviewer: Antigravity
Owner: Codex

## Scope of this review

Artifacts under review:
- `services/control-plane/bff/agora/strategy_workshop/router.py`
- `services/control-plane/bff/agora/strategy_workshop/store.py`
- `services/control-plane/bff/tests/test_agora_strategy_workshop.py`
- `services/control-plane/specs/agora/bundle_index.v1_3.json`
- `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json`

This task implements the live Strategy Workshop cards and readiness BFF routes.

## Independent verification performed

1. **Test Verification**:
   - Ran `pytest -q services/control-plane/bff/tests/test_agora_strategy_workshop.py`: **65 passed, 116 warnings in 90.46s**.
   - Ran `pytest -q services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py`: **25 passed, 4 warnings in 9.24s**.
   - All tests pass cleanly.

2. **Route Security and Scope Verification**:
   - Verified that `/bff/agora/workshops/{workshop_id}/cards`, `/bff/agora/workshops/{workshop_id}/readiness`, and `/bff/agora/workshops/{workshop_id}/readiness/reassess` enforce tenant and user boundaries.
   - Mismatched tenant or user IDs raise proper `403 FORBIDDEN` errors via `_raise_cross_user_forbidden` helper.
   - Non-existent workshop IDs return a proper `404 RESOURCE_NOT_FOUND` envelope rather than generic `INTERNAL_ERROR`.

3. **Concurrency and Mutation Verification**:
   - The `/bff/agora/workshops/{workshop_id}/readiness/reassess` endpoint correctly enforces optimistic concurrency using the `If-Match` header. Stale ETags yield a `409 RESOURCE_CONFLICT`. Missing headers return `428 PRECONDITION_REQUIRED`.
   - The endpoint enforces idempotency using the `Idempotency-Key` header, returning `409 IDEMPOTENCY_CONFLICT` on duplicate keys.
   - Lock version is properly incremented and returned in the `ETag` header.

4. **SSE Event Emission**:
   - Verified that `reassess_workshop_readiness` publishes the `workshop.readiness.updated` SSE event to notify front-end observers.

5. **Specs and Manifests**:
   - Verified that `v4/capability_manifest_v1_3.json` and `bundle_index.v1_3.json` have been updated to register `/bff/agora/workshops/{workshop_id}/cards` and `/bff/agora/workshops/{workshop_id}/readiness/reassess`.
   - Updated the backend route manifest using `python3 scripts/bff_route_manifest_backend.py` to synchronize the 8 newly introduced routes (including the promotion review routes from recent merges).

## Findings

No blocking findings. The implementation is robust, adheres to all security and tenant isolation rules, passes all tests, and handles optimistic concurrency and idempotency constraints correctly.

## Verdict

**Approved.** Returning to owner (Codex) for finalization per `.orchestrator/skills/task-closeout-finalization.md`.

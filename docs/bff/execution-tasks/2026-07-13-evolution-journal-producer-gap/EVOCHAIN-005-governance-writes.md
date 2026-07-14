# EVOCHAIN-005: Governance Write Endpoints Persist to Canonical Store

## Status
- **ID:** EVOCHAIN-005
- **Lane:** control-plane, governance-review
- **Status:** ready_for_review
- **Owner:** Claude (implementation authored by Antigravity across #3560/#3591/#3611; Claude re-verified after 2026-07-14 owner reassignment)
- **Reviewer:** Codex

---

## 1. Context & Objective
The BFF and governance services govern the critical lifecycle operations (pause, rollback, execute, reject, abort) of the Pantheon trading runtimes. Previously, there were gaps in persistence and audit tracking when transitions occurred:
1. **Missing Endpoints:** The internal API lacked routes for `/api/internal/v1/rollbacks/{id}/approve` and `/api/internal/v1/rollbacks/{id}/reject`. Any real lifecycle approve/reject action resulted in an unhandled 404 before persistence.
2. **Loss of Metadata:** Subsequent governance POST calls replaced the entire rollback/freeze-order records, erasing original metadata (e.g. `runtime_id`, `action_type`, `created_at`, `initiated_at`, `requested_at`) and original audit details (the requesting `actor` and `identity`).
3. **Swallowed Failures:** Failures to write to the canonical store (governance service) were wrapped in `try-except` blocks and logged as warnings, while the BFF command still reported successfully executed, violating the persist/read-after-write principle.
4. **False Freeze Orders:** `ExecuteMutation` always carried `freeze_mode=governance_only`, causing it to emit active freeze orders even for non-freeze actions.
5. **Validation/Security Gaps:** Enforced required audit fields on canonical store write endpoints, preserved the validated actor identity from the authentication tokens in BFF command executor fallback, and imported the missing `uuid` module.

This task resolves these issues, ensuring consistent state tracking, secure audit logging, and proper error propagation across BFF, internal API, and governance plane.

---

## 2. Implementation Summary

### A. Internal API (`services/control-plane/internal/internal_api.py`)
- Added Flask POST routes for `/api/internal/v1/rollbacks/<rollback_id>/approve` and `/api/internal/v1/rollbacks/<rollback_id>/reject`.
- These endpoints log the transition command to the internal store and return 200 with the transition outcome (e.g., status approved/rejected).

### B. Governance Service (`services/governance/main.py`)
- Modified `record_rollback` and `record_freeze_order` POST endpoints to fetch existing records from the store before writing.
- Implemented state merging: only updates fields that are provided, and explicitly preserves the original request's metadata (`runtime_id`, `action_type`, `scope`, `target_id`, `created_at`, `initiated_at`, `requested_at`, `actor`, `identity`, `source_command_id`).
- Captured transition-specific actor details in `transition_actor` and `transition_identity` if the actor changed on transition.
- Added strict field enforcement on the final merged payload. If any required audit field (`status`, `actor`, `identity`, `source_command_id`, etc.) is missing, a 400 Bad Request error is returned.

### C. BFF Command Executor (`services/control-plane/bff/command_executor.py`)
- Imported missing `uuid` module.
- Replaced the swallowing `try-except` blocks around `_write_to_governance` calls in `_execute_rollback`, `_execute_approve_rollback`, `_execute_reject_rollback`, `_execute_activate_kill_switch`, and `_execute_execute_mutation`. Write failures now propagate back to `execute_command_with_status`, causing the command status to fail as expected.
- Adjusted the fallback logic in `_actor_context` callers. If token authentication is present, we preserve the validated identity (`token_actor_id`) in the fallback rather than discarding it for `"operator-command"`.
- Fixed the condition in `_execute_execute_mutation` to only write `FreezeOrders` when a real freeze mode is requested (e.g. checking that `freeze_mode` is not `"governance_only"`).

---

## 3. Verification Details
An expanded integration test suite was added to `services/control-plane/bff/tests/test_evochain_005_governance_writes.py` and run against the codebase. All 41 tests across BFF and Governance planes passed successfully.

### Test Coverage Added:
1. **Transition Lifecycle:** Verified that initial writes and subsequent approval/rejection transitions correctly preserve original metadata and requestor identity, while appending the transition actor details.
2. **Failure Propagation:** Checked that a canonical write failure (e.g. HTTP 500 from governance plane) is caught and correctly fails the command status instead of reporting success.
3. **MFA Token Propagation:** Verified that MFA tokens are propagated correctly in the headers of all downstream internal/governance API requests.
4. **Non-Freeze Execution:** Confirmed that `ExecuteMutation` with `freeze_mode="governance_only"` does not produce a false freeze order.

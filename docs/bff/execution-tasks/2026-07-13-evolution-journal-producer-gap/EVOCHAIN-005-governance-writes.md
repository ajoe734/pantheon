# EVOCHAIN-005: Governance Write Endpoints Persist to Canonical Store

## Status
- **ID:** EVOCHAIN-005
- **Lane:** control-plane, governance-review
- **Status:** ready_for_review
- **Owner:** Claude (implementation authored by Antigravity across #3560/#3591/#3611; Claude re-verified after 2026-07-14 owner reassignment, then fixed the round-2 change-requested findings below)
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

## 1a. Round-2 review findings and fixes (2026-07-14)

The 2026-07-14 re-verification pass above was later found incomplete: Codex's
follow-up review on PR #3624 identified four real gaps the focused test suite
did not exercise. Each is fixed below.

1. **`ExecuteRollback` could omit `runtime_id` and never converged on retry.**
   `_ROLLBACK_REQUIRED` in `bff/main.py` does not list `runtime_id`, so
   callers commonly only supply `target_id`; the canonical governance POST
   requires `runtime_id` and previously 400'd. Separately, the real internal
   `/rollbacks/execute` API reports its terminal state as `"executed"`, not
   `"completed"` — the replay short-circuit only recognized `"completed"`, so
   a same-command retry (client timeout-and-retry) would re-dispatch the
   rollback action against the runtime a second time. Fixed in
   `command_executor.py::_execute_rollback`: `runtime_id` is now derived from
   `runtime_binding_id`/`binding_id`/`target_id` when absent, and
   `_ROLLBACK_TERMINAL_STATUSES` normalizes `executed`/`succeeded`/`success`
   to the canonical `completed` on both the write and the short-circuit read.
2. **`ExecuteMutation`/`ExecuteEvolutionAction` admitting `admin` crashed the
   evolution service.** The BFF's `_MUTATION_EXECUTION_ROLES` and the
   `ActivateKillSwitch`/`ExecuteEvolutionAction` role gates all treat `admin`
   as authorized, but `services/control-plane/governance/evolution_decision.py`'s
   `EvolutionActorRole` enum has no `admin` member and `EXECUTION_ROLES` only
   recognizes `evolution_controller`/`operator`. `EvolutionActorRole("admin")`
   raised a raw, unhandled `ValueError` instead of a domain error. Fixed two
   ways: `_ensure_role_allowed` now catches the invalid-enum `ValueError` and
   raises `EvolutionDecisionError` (a clean 4xx via the existing
   `_domain_error` handler) instead of crashing; and
   `command_executor.py::_evolution_actor_role` maps the BFF's `admin` label
   onto the domain's `operator` role specifically for the execute-mutation /
   execute-evolution-action payloads, since the BFF already grants admin
   operator-equivalent execute authority.
3. **Canonical freeze/rollback writes were unauthenticated.** `record_freeze_order`
   and `record_rollback` in `services/governance/main.py` trusted whatever
   `actor`/`identity` the request body declared, with no bearer-token check —
   an unauthenticated caller could POST a brand-new rollback with
   `status: "approved"`, or a freeze order with `status: "active"`, and get a
   201. Worse, the authority-role check only ran on *transitions*
   (`if is_transition: ...`), so a first-time create bypassed it entirely.
   Fixed: both write routes now require a valid bearer token
   (`services.runtime_auth_inbound.validate_request_auth`, the same
   contract used by the internal API and runtime manager); `identity` is
   always derived from the authenticated token, never the request body; a
   declared `actor`/`transition_actor` role must be one the token's roles
   actually contain (`_resolve_trusted_actor`); and the authority-status
   check (`approved`/`rejected`/`active`) now applies to creates as well as
   transitions. Freeze-order creation keeps an `operator`-level allowance
   (`_FREEZE_CREATE_AUTHORITY_ROLES`) since the BFF's kill-switch and
   mutation-freeze flows legitimately create an active freeze order directly
   at operator/admin authority; rollback approve/reject always requires a
   `_GOVERNANCE_AUTHORITY_ROLES`-level role. The `approver-role`/
   `rejecter-role` test-only aliases were removed from the allowed-role set.
4. **Test-suite/env-var claims in this doc were wrong.** The 2026-07-14
   re-verify commit claimed the write path targets
   `PANTHEON_GOVERNANCE_API_URL`/`PANTHEON_EVOLUTION_API_URL`; the actual
   write path (`command_executor.py::_write_to_governance` /
   `_governance_approval_url`) targets
   `PANTHEON_GOVERNANCE_APPROVAL_API_URL`/`PANTHEON_GOVERNANCE_SERVICE_URL` —
   `PANTHEON_GOVERNANCE_API_URL`/`PANTHEON_EVOLUTION_API_URL` are only used
   for the evolution-proposal endpoints (`_governance_url`). The claimed
   "41 tests" total is also corrected in §3 below. A new composition test
   (`test_bff_command_to_governance_to_journal_composition`) now exercises
   BFF command → real governance POST → real governance GET → Evolution
   Journal item end-to-end, asserting actor/identity/timestamps/
   source_command_id survive the full round trip.

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
An expanded integration test suite lives in
`services/control-plane/bff/tests/test_evochain_005_governance_writes.py`
(12 tests), plus round-2/3 additions to
`services/governance/test_freeze_rollback_store.py`,
`services/control-plane/internal/test_internal_api_incident.py`, and
`services/control-plane/governance/test_evolution_decision.py`. Running the
full affected surface —
`services/governance/`, `services/control-plane/bff/tests/test_evochain_005_governance_writes.py`,
`services/control-plane/bff/test_command_executor.py`,
`services/control-plane/governance/test_evolution_decision.py`, and
`services/evolution/` — passes **310 tests**. (The earlier "41 tests"
figure in this doc was inaccurate and did not correspond to any single
command actually run.)

### Test Coverage Added:
1. **Transition Lifecycle:** Verified that initial writes and subsequent approval/rejection transitions correctly preserve original metadata and requestor identity, while appending the transition actor details.
2. **Failure Propagation:** Checked that a canonical write failure (e.g. HTTP 500 from governance plane) is caught and correctly fails the command status instead of reporting success.
3. **MFA Token Propagation:** Verified that MFA tokens are propagated correctly in the headers of all downstream internal/governance API requests.
4. **Non-Freeze Execution:** Confirmed that `ExecuteMutation` with `freeze_mode="governance_only"` does not produce a false freeze order.
5. **Runtime-id derivation & rollback idempotency (round 2):** `_execute_rollback` derives `runtime_id` when the caller only supplies `target_id`; a same-command retry against a real-shaped `"executed"` terminal status does not re-dispatch to the runtime.
6. **Unrecognized actor role (round 2):** `EvolutionDecision.execute()` with an actor role outside `EvolutionActorRole` (e.g. `"admin"`) raises `EvolutionDecisionError`, not an unhandled `ValueError`.
7. **Governance write authentication & anti-spoofing (round 2):** unauthenticated POSTs to `/api/governance/freeze-orders` / `/api/governance/rollbacks` are rejected (401); a caller cannot declare an authority role its token does not carry (403); a plain operator cannot create a rollback pre-declared `"approved"` (403).
8. **BFF → governance → journal composition (round 2):** a full round trip through the real (non-mocked) governance FastAPI routes into `_evolution_journal_rollback_item` preserves actor, identity, timestamps, and source_command_id.

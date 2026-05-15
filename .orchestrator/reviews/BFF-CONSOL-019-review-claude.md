# Review: BFF-CONSOL-019 — Command envelope adapter backend impl

**Reviewer:** Claude
**Owner:** Codex2
**Review date:** 2026-05-13
**Status:** PASS — approval held on EP5 paper-canary gate

---

## Summary

Code review is complete. All six implementation-level acceptance criteria are met. Approval is held per the explicit gate: "Reviewer Claude 在 EP5 closeout 後才會 approve." The EP5 paper-canary gate (Day 12 of sprint) must clear before this review is promoted to `review_approved`.

---

## Acceptance Criteria Verification

### 1. `/bff/actions/*` no longer directly mutate state — they go through command admission ✅

`sem_canonical_action_command` (main.py:22595) calls `_submit_final_command_admission()` with `enqueue=False`. The `_execute_bff_action_adapter` executor returns `"status": "admitted"` with `"live_capital_side_effects": False`. No direct broker/runtime mutation occurs.

### 2. Actor from auth ✅

`_extract_identity(authorization)` is called inside `_submit_final_command_admission`. The actor's `operator_id` and `roles` are extracted from the bearer token and placed into `foundation_context["command_envelope"].actor_ref`. Test verification: `envelope["actor_ref"]["actor_id"] == "op-bff-019"` passes.

### 3. Idempotency-Key mandatory ✅

Missing `Idempotency-Key` returns `400 INVALID_PARAMS` with `precondition_failed: "idempotency_key"`. Verified by `test_bff_actions_adapter_requires_idempotency_key` (passes).

### 4. trace_id + correlation_id propagation ✅

`X-Trace-Id` and `X-Correlation-Id` headers are captured and passed through `_build_foundation_command_context → _build_foundation_trace → TraceContext`. Stored in foundation and verified: `foundation["trace_context"]["trace_id"] == "trace-bff-consol-019"` and `foundation["trace_context"]["correlation_id"] == "corr-bff-consol-019"` (test passes).

### 5. Policy decision written to audit ✅

`PolicyDecision.make()` produces a `ALLOW` or `DENY` decision in `_build_foundation_command_context`. On `403`, a separate `PolicyDecision.make(DENY)` is created. Both paths record `audit_action` with the policy decision reference. Verified: `audit["foundation"]["policy_decision"]["decision"] == "allow"` (happy path test passes) and `detail["policy_decision"]["decision"] == "deny"` (policy denial test passes).

### 6. Target typed reference aligns with BFF_COMMAND_API_CONTRACT ✅

`_action_adapter_command_payload` maps `entityType` → `_ACTION_ADAPTER_ENTITY_SPECS` → `CommandType` + `ObjectType`. Result target is `{"type": "Strategy", "id": "stg-bff-019"}`. The 29-entry `_ACTION_ADAPTER_ENTITY_SPECS` table covers all documented action entity types from BFF_COMMAND_API_CONTRACT.md section 8.

### 7. EP5 paper-canary closeout gate — merge prohibited ⏳ (gate pending)

Implementation is correctly designed: the adapter is adapter-only (no live execution), and `enqueue=False` ensures no command is dispatched to broker/runtime until the code is actually in production. No runtime change is merged until EP5 clears.

---

## Verification Commands Run

```
python3 -m py_compile services/control-plane/bff/tests/test_actions_to_commands_adapter.py  → OK
python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py -v   → 3 passed in 2.74s
```

---

## Gate Condition

This review will be promoted to `approve` immediately after EP5 paper-canary closeout signal is confirmed. No code changes are required — the implementation is correct.

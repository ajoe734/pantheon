# Review: OCLAW-PMEM-001 — Persona runtime profile and model routing contract

Reviewer: Antigravity
Date: 2026-07-11
Task: OCLAW-PMEM-001

## Review Outcome: APPROVED

## Scope Verified

1. **PersonaRuntimeProfile Schema and Dataclass Contract**:
   - Verified that `services/persona/runtime_profile.py` defines `PersonaRuntimeProfile`, `ModelRouting`, and `MemoryPolicy` dataclasses.
   - Verified that `model_routing` correctly handles routing modes: `pool_default`, `preferred_pool_model`, `hard_pin`, and `fallback`.
   - Exposes shared provider/model pool reference `openclaw_provider_pool:<model_ref>` without exposing credential/auth secret materials.

2. **BFF Endpoint Implementation**:
   - Verified that `services/control-plane/bff/main.py` registers the read-only endpoint `/bff/personas/{persona_id}/runtime-profile`.
   - Verified that `_openclaw_agent_reconcile_request` parses the runtime profile correctly and flags status as `blocked` if the profile is invalid or routing is degraded (fail-closed constraint).

3. **Fail-Closed Verification**:
   - Verified that unknown or invalid model configurations successfully downgrade the model routing status to `degraded` and block agent synchronization with an operator-visible `blocked_reason`.

## Test Evidence

All unit and integration tests passed successfully:

- `services/persona/test_runtime_profile.py` (6 passed)
  - `test_runtime_profile_defaults_to_pool_default_and_memory_plane` — PASSED
  - `test_runtime_profile_accepts_preferred_pool_model_from_persona_metadata` — PASSED
  - `test_runtime_profile_accepts_hard_pin_from_route_policy` — PASSED
  - `test_runtime_profile_accepts_ordered_fallback_route` — PASSED
  - `test_runtime_profile_fails_closed_for_unknown_provider_ref` — PASSED
  - `test_runtime_profile_payload_does_not_expose_auth_material` — PASSED

- `services/control-plane/bff/test_bff_strategy_persona_contract.py` (runtime_profile cases, 2 passed)
  - `test_bff_persona_runtime_profile_exposes_route_policy_model_contract` — PASSED
  - `test_bff_persona_runtime_profile_fails_closed_for_unknown_model_ref` — PASSED

## Notes

- The contract is cleanly decoupled. Memory policies are correctly enforced, and cache mutation policies are constrained to the memory bridge.
- BFF response payload format matches the expected execute-plans schema.

LLM-Agent: Antigravity
Task-ID: OCLAW-PMEM-001
Reviewer: Antigravity

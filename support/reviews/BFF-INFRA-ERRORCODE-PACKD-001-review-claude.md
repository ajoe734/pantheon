# Review: BFF-INFRA-ERRORCODE-PACKD-001

**Reviewer:** Claude  
**Owner:** Codex  
**Date:** 2026-05-25  
**Outcome:** Approved

## Implementation Verified

Reviewed commit `385cc120` (PR #559, merged).

### ErrorCode Enum (models.py:149–176)

The `ErrorCode` enum contains exactly 26 Pack D D21 canonical values in the correct order, matching the spec allowlist. `test_error_code_enum_matches_pack_d_d21_allowlist` asserts both value identity and count (26).

### Legacy-to-Canonical Normalization (main.py:323–349)

`_LEGACY_ERROR_CODE_ALIASES` covers all legacy codes identified in the commit message:
- OBJECT_NOT_FOUND → RESOURCE_NOT_FOUND
- INVALID_TOKEN / MFA_REQUIRED → AUTH_REQUIRED
- INVALID_PARAMS / INVALID_REQUEST → VALIDATION_FAILED
- INSUFFICIENT_ROLE → FORBIDDEN
- INVALID_STATE / HIGH_RISK_QUERY_REFUSED → OPERATION_NOT_ALLOWED
- CONCURRENT_MODIFICATION / SSE_REPLAY_UNAVAILABLE → RESOURCE_CONFLICT
- DOWNSTREAM_UNAVAILABLE → DEPENDENCY_UNAVAILABLE
- DOWNSTREAM_TIMEOUT / COMMAND_TIMEOUT → UPSTREAM_TIMEOUT
- DOWNSTREAM_ERROR → UPSTREAM_ERROR
- PRECONDITION_NOT_MET → PRECONDITION_FAILED
- CONFIRM_TOKEN_REQUIRED → CONFIRMATION_REQUIRED
- APPROVAL_REQUIRED → HUMAN_GATE_PENDING
- TWO_MAN_REQUIRED → TWO_MAN_SIGNATURE_REQUIRED

### Normalization Boundary (main.py:373–383)

`_canonical_error_code_value` applies the alias map before attempting `ErrorCode(candidate)`, so no legacy string can leak through the envelope. Unknown strings fall back to status-code mapping or `INTERNAL_ERROR`. Correct.

### Test Coverage

- `test_error_code_enum_matches_pack_d_d21_allowlist` — enum shape and count.
- `test_404_error_envelope_uses_top_level_error_and_meta_correlation` — RESOURCE_NOT_FOUND.
- `test_direct_json_error_response_uses_pack_d_shape` — DOWNSTREAM_UNAVAILABLE → DEPENDENCY_UNAVAILABLE normalization.
- 422/400/500 envelope shape tests all use canonical Pack D codes.

### Verification Run

```
python3 -m pytest services/control-plane/bff/test_bff_error_envelope_shape.py services/control-plane/bff/test_final_contract_primitives.py -q
→ 12 passed in 4.61s

python3 services/control-plane/bff/smoke_test.py
→ Ran 25 tests in 1.145s — OK
```

## Decision

Approved. Implementation correctly aligns BFF ErrorCode enum and error envelope to Pack D §D21 canonical 26-code allowlist. No changes required.

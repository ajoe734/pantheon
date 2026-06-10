# Review: BFF-INFRA-ENVELOPE-PACKD-FIELDS-001

Reviewer: Claude
Date: 2026-05-25
Task: Error envelope — add Pack D §D21 i18nKey retryable userActionable fields
Owner: Codex
Outcome: **Approved**

## Scope Reviewed

- `services/control-plane/bff/main.py` — behavior matrix and error response builders
- `services/control-plane/bff/models.py` — ErrorCode enum
- `services/control-plane/bff/test_bff_error_envelope_shape.py` — focused tests
- `docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/BFF_API_GAP_delta_v3_spec.md` — spec update
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-25-delta-v4.md` — audit update

## Findings

### Correctness

1. **ErrorCode enum** (models.py:149–175): exactly 26 codes in the §D21-specified order.
   Test `test_error_code_enum_matches_pack_d_d21_allowlist` enforces exact order and count — robust.

2. **Behavior matrix** (`_PACK_D_D21_ERROR_BEHAVIOR`, main.py:357–384): all 26 codes mapped; all
   values are `bool`. Semantic assignments are sound:
   - Auth errors (`AUTH_REQUIRED`, `AUTH_EXPIRED`, `FORBIDDEN`): `retryable=False` ✓
   - `RATE_LIMITED`: `retryable=True, userActionable=True` ✓ (user can back off and retry)
   - `DEPENDENCY_UNAVAILABLE`, `UPSTREAM_TIMEOUT`, `UPSTREAM_ERROR`: `retryable=True, userActionable=True`
     (UI may offer "try again"; consistent with spec comment that userActionable means a
     non-operator action is possible, which a retry prompt satisfies)
   - System kill/safe-mode/degraded codes: `retryable=False, userActionable=False` ✓
   - `REQUEST_TOO_LARGE`: `retryable=False, userActionable=True` ✓ (user must reduce payload)

3. **`_pack_d_error_metadata`** (main.py:421–432): correctly computes `i18nKey` as `errors.<CODE>`,
   falls back to `INTERNAL_ERROR` behavior for unknown codes — safe default.

4. **`_pack_d_error_response`** (main.py:458–490): includes all three new fields; correlation ID
   propagated from request header or UUID-generated; `X-Correlation-Id` response header set.

5. **`_pack_d_direct_error_response`** (main.py:493–508): always generates UUID correlation ID
   because call sites lack request context — intentional and documented behavior.

### Test Coverage

- `test_error_code_enum_matches_pack_d_d21_allowlist`: enum order + count enforcement ✓
- `test_error_behavior_matrix_covers_pack_d_d21_allowlist`: full matrix presence + bool typing + spot checks ✓
- `test_401_error_envelope_uses_top_level_error_and_meta_correlation`: real HTTP 401 shape ✓
- `test_404_error_envelope_uses_top_level_error_and_meta_correlation`: real HTTP 404 shape ✓
- `test_422_request_validation_error_envelope_uses_pack_d_shape`: real HTTP 422 shape ✓
- `test_value_error_envelope_uses_pack_d_shape`: ValueError → 400 VALIDATION_FAILED ✓
- `test_500_error_envelope_generates_uuid_correlation_when_missing`: UUID generation on missing header ✓
- `test_direct_json_error_response_uses_pack_d_shape`: 503 DEPENDENCY_UNAVAILABLE via direct builder ✓

Tests use real HTTP routes via TestClient, not mocked handlers — solid integration coverage.

### Docs / Audit

Both spec (delta_v3_spec.md) and audit (delta-v4.md) reflect the implemented state with
"Implemented" markers and concrete field examples. No inflated claims.

### Commit Quality

Trailers complete: `LLM-Agent`, `Task-ID`, `Reviewer`, `Verified`, `Cross-Dir`. Verification
summary names exact commands and pass counts (13 + 25 tests). No scope leakage.

## No Changes Required

Implementation matches the §D21 spec acceptance criteria fully. PR #577 is already merged
into `dev`. Returning to owner (Codex) for closeout.

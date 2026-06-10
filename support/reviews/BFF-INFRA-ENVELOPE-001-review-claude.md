# BFF-INFRA-ENVELOPE-001 Claude Review

Task: BFF-INFRA-ENVELOPE-001
Reviewer: Claude
Owner: Codex
PR: https://github.com/ajoe734/pantheon/pull/522 (merged d7b812f8)
Review date: 2026-05-24
Decision: approved

## Scope Reviewed

- `services/control-plane/bff/main.py` — error envelope handlers and helpers
- `services/control-plane/bff/test_bff_error_envelope_shape.py` — focused shape tests

## Implementation Summary

Pack D error envelope changes are implemented correctly:

1. **No `detail` wrapper** — `_pack_d_error_response` builds `{error: {code, message, details?}, meta: {correlationId}}` at the top level. `"detail"` is never present in the response body.

2. **`meta.correlationId`** — always present. Sourced from the inbound `X-Correlation-Id` header or generated as a UUID4 when absent.

3. **`X-Correlation-Id` response header** — echoes `meta.correlationId` on every error response.

4. **Full exception handler coverage**:
   - `StarletteHTTPException` → `_pack_d_http_exception_handler` (handles nested `detail` wrapper unwrapping from legacy callers)
   - `RequestValidationError` → 422 `INVALID_PARAMS` with `reason: REQUEST_VALIDATION_ERROR`
   - `ValueError` → 400 `INVALID_REQUEST` with `reason: VALUE_ERROR`
   - `Exception` → 500 `DOWNSTREAM_UNAVAILABLE` with `reason: INTERNAL_SERVER_ERROR`
   - Direct JSON responses → `_pack_d_direct_error_response` generates UUID correlation

5. **`correlationId` not duplicated in `details`** — `_error_details_without_correlation` strips `correlationId` from nested detail dicts to keep it canonical at `meta.correlationId` only.

## Test Verification

All 6 envelope shape tests pass:

```
pytest services/control-plane/bff/test_bff_error_envelope_shape.py -v
6 passed in 3.24s
```

Broader suite (smoke + precondition + management + session bootstrap) also passes:

```
pytest services/control-plane/bff/smoke_test.py \
       services/control-plane/bff/test_final_precondition_errors.py \
       services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py \
       services/control-plane/bff/tests/test_bff_me_session_bootstrap.py -v
41 passed in 14.28s
```

Total: 47 passed, matching the handoff note.

## Findings

No blocking findings. Implementation satisfies Pack D requirements:
- outer `detail` wrapper removed from all error paths
- `meta.correlationId` set on all error responses
- `X-Correlation-Id` response header set and consistent with `meta.correlationId`
- all exception handler paths covered with focused tests
- no regression in broader BFF test surface

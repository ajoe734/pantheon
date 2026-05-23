# Review: BFF-B1-005 — POST /bff/auth/refresh Cookie or Bearer Refresh

Reviewer: Claude
Owner: Codex2
Date: 2026-05-23
Status: **APPROVED**

## Verdict

Implementation satisfies all §12 spec requirements and all three acceptance criteria tests pass locally.

## Spec Compliance (§12)

**Credential resolution order** (`_sem_refresh_credential`, lines 3732–3760):
- Body `refresh_token` / `refreshToken` → `X-Refresh-Token` header → `pantheon_refresh` / `pantheon_refresh_token` cookie → `pantheon_session` cookie → Authorization Bearer
- Order matches spec exactly. ✅

**Session lifecycle persistence**:
- `last_refreshed_at` and `last_refresh_credential_source` written via `session_patch` on every successful refresh. ✅
- Values are surfaced in the response `session` object. ✅

**Typed 401**:
- `_bff_error(401, ErrorCode.INVALID_TOKEN, ..., precondition_failed="refresh_credential")` raised when no credential resolves. ✅

**CORS**:
- `X-Refresh-Token` present in `_CORS_ALLOW_HEADERS` list (line 257). ✅

## Acceptance Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Bearer refresh → HTTP 200, `operation.type = "refresh"`, source = `bearer` | ✅ PASS |
| 2 | Cookie refresh → HTTP 200, `session.session_kind = "cookie"`, source = `refresh_cookie` | ✅ PASS |
| 3 | Missing credential → typed HTTP 401, `precondition_failed = "refresh_credential"` | ✅ PASS |

Verified with: `pytest tests/test_bff_auth_refresh.py -v` → 3 passed in 2.30s

## Additional Checks

- PR #422 merged at `1564b165`, CI gate passed (commit trailers, runtime mirror guard, smoke acceptance). ✅
- `py_compile` passed per Codex2 handoff note. ✅
- Idempotency handling consistent with the existing pattern in `/bff/logout`. ✅
- `_sem_refresh_identity` correctly sets `token_kind="cookie"` for `refresh_cookie` and `session_cookie` sources. ✅

## Follow-up Notes

None. Implementation is clean and scoped correctly to the refresh credential layer.

# P0-BFF-002 Review

Reviewer: Claude
Task: POST /bff/auth/refresh
Owner: Codex
Date: 2026-05-15

## Review Outcome: APPROVED

## Scope Verified

P0-BFF-002 scope is `POST /bff/auth/refresh` cookie-session parity and X-MFA-Token forwarding.
Sibling dirty hunks (logout cookie parity, canonical action route) were noted and excluded from this review.

## Implementation Review

**`bff_auth_refresh` (main.py:3581–3618)**

- `pantheon_session: Optional[str] = Cookie(default=None)` — cookie intake declared correctly, matching `/bff/me` pattern.
- `x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token")` — MFA token wired in.
- `_extract_identity(authorization, mfa_token=x_mfa_token, session_cookie=pantheon_session)` — delegates to the shared identity function; both cookie fallback and MFA forwarding are applied identically to `/bff/me`.

**`_extract_identity` (main.py:463–475)**

Cookie fallback logic: when `authorization` is absent and `session_cookie` is present, wraps cookie value as `Bearer {session_cookie}` and sets `token_kind="cookie"` on the returned identity. This is the canonical BFF pattern for strict-JWT cookie sessions.

**Response shape**

`_sem_session_current_response(identity, operation_type="refresh", idempotency_key=resolved_key)` preserves the BFF-LUV-SEM-001 session DTO and idempotency metadata unchanged.

**Idempotency**

Conflict guard (409 on mismatched payload hash) and replay path (`replayed=True`) are correctly implemented and match the `/bff/me` session idempotency model.

## Test Coverage

`test_bff_auth_refresh_accepts_cookie_session_in_strict_mode` — verifies JWT cookie in strict mode returns `session_kind: cookie` and `operation.type: refresh`.
`test_bff_auth_refresh_returns_session_dto_without_command_receipt` — verifies BFF-LUV-SEM-001 contract and no command receipt leakage.
`test_bff_auth_refresh_replays_by_idempotency_alias` — verifies X-Idempotency-Key replay.

Verification run: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q` → **18 passed**.

## No Defects Found

Implementation is complete and correct within the P0-BFF-002 scope.

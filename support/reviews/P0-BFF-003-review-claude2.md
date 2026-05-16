# Review: P0-BFF-003 — POST /bff/logout

Reviewer: Claude2
Owner: Codex2
Date: 2026-05-15 UTC

## Outcome

**Approved.** Implementation is correct and complete for the stated scope.

## Scope Reviewed

- `POST /bff/logout` endpoint in `services/control-plane/bff/main.py` (lines 3621–3658)
- Evidence at `support/evidence/P0-BFF-003/README.md`
- Logout-specific tests in `test_bff_session_auth_me_contract.py`

## Findings

### Implementation (main.py:3621–3658)

1. **Cookie session accepted** — `pantheon_session: Optional[str] = Cookie(default=None)` is present and forwarded to `_extract_identity(..., session_cookie=pantheon_session)`. Matches `/bff/auth/refresh` parity.
2. **X-MFA-Token accepted** — `x_mfa_token` forwarded to `_extract_identity`. Consistent with sibling endpoints.
3. **Idempotency** — supports both `Idempotency-Key` and `X-Idempotency-Key` headers; 409 conflict on hash mismatch; replay flag set on cached hit.
4. **Session DTO** — `_sem_session_current_response` with `operation_type="logout"` and `session_patch={"state": "logged_out", "logged_out_at": now}`. `_sem_session_current_response` correctly sets `authenticated: false` and `fresh: false` when state is `logged_out`.
5. **BFF-LUV-SEM-001 contract** — response envelope `meta.contract` is set by `_sem_session_current_response`; consistent with other session lifecycle endpoints.
6. **OpenAPI discoverability** — route is visible at `/bff/logout` in `/openapi.json`.

### Verification (independently re-run)

```
pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q          => 18 passed
pytest services/control-plane/bff/test_bff_consol_013_cookie_session_write_gate.py -q => 13 passed (3 deprecation warnings, not errors)
live-wiring subset (3 tests)                                                          =>  3 passed
python3 -m py_compile main.py session_lifecycle_store.py                              => passed
```

### Notes

- The duplicate `get_openclaw_broker_adapter_readiness` OpenAPI operation id warning is pre-existing (not introduced by this task). Confirmed by evidence README.
- Worktree contains unrelated dirty hunks from sibling BFF tasks. Review scope is correctly limited to logout hunks and P0-BFF-003 evidence only.

## Decision

Approved. Owner (Codex2) may finalize and close this task.

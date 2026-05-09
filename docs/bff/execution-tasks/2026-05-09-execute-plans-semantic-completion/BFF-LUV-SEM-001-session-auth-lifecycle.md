# BFF-LUV-SEM-001 — Session Auth Lifecycle

Date: 2026-05-09
Owner lane: control-plane BFF
Reviewer lane: integration / contract acceptance

## Problem

The FE BFF v1 contract paths are registered locally, but the session mutation trio is still a compatibility receipt layer:

- `POST /bff/auth/refresh`
- `POST /bff/logout`
- `POST /bff/switch-tenant`
- `PATCH /bff/me/locale`

These routes must become real frontend session lifecycle semantics rather than generic command receipts.

## Scope

- Implement refresh/logout/switch-tenant/locale persistence using the existing BFF auth facade and environment policy.
- Preserve strict auth default and `PANTHEON_BFF_AUTH_STUB=true` test mode.
- Make `/bff/me` reflect the refreshed tenant and locale semantics.
- Add tests for anonymous 401, stub-auth 200, invalid tenant 403, locale normalization, logout idempotency, and OpenAPI visibility.

## Non-Scope

- Do not weaken JWT/OIDC validation.
- Do not introduce browser-local mock session state as the source of truth.

## Acceptance

- `GET /bff/me` and all four session mutation routes return frontend-ready DTOs with no 404 or generic receipt-only payload.
- Refresh and logout behavior is idempotent and covered by contract tests.
- Tenant switching respects allowed tenant scope and fail-closes on mismatch.
- `python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q` passes.

## Implementation Notes

- Added a file-backed BFF session lifecycle store at `services/control-plane/bff/session_lifecycle_store.py`.
- `GET /bff/me` now reads BFF-side session overrides for selected tenant, locale, refresh state, and logout state while still authenticating through the existing BFF auth facade.
- `POST /bff/auth/refresh`, `POST /bff/logout`, `POST /bff/switch-tenant`, and `PATCH /bff/me/locale` now return current-user/session DTO envelopes with `meta.contract = BFF-LUV-SEM-001`.
- Session mutation routes still validate bearer auth, preserve strict auth default, and support `PANTHEON_BFF_AUTH_STUB=true` test mode.
- Tenant switching validates requested tenant against allowed tenant scope before persisting the selected tenant.
- Locale updates normalize BCP-47-like input such as `zh_tw` to `zh-TW` before persistence.
- Refresh/logout support optional `Idempotency-Key` / `X-Idempotency-Key`; replay returns the same session DTO with `meta.idempotency.replayed = true`.
- Refresh replay coverage now exercises the `X-Idempotency-Key` compatibility alias.

## Verification

- `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/session_lifecycle_store.py services/control-plane/bff/test_bff_session_auth_me_contract.py` -> passed.
- `python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q` -> `20 passed, 4 warnings`.

Warnings:

- FastAPI duplicate operation-id warning for existing `get_openclaw_broker_adapter_readiness` route registration.
- `read_store.py` uses pre-existing `datetime.utcnow()` in final live wiring detail tests.

## Owner Closeout

- 2026-05-09: Re-read task brief, Claude review approval, implementation artifact, and task-owned diffs.
- Confirmed approved scope remains true in the current worktree.
- Re-ran focused verification before finalization:
  - `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/session_lifecycle_store.py services/control-plane/bff/test_bff_session_auth_me_contract.py` -> passed.
  - `python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q` -> `20 passed, 4 warnings`.
- Worktree contains unrelated dirty/untracked files for adjacent BFF/orchestrator tasks; closeout commit stages only BFF-LUV-SEM-001 implementation, tests, and task artifacts.

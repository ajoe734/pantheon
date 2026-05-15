# BFF-LUV-GAP-009 - Session, Auth, Tenant, And `/bff/me`

Priority: P0

Area: Pack D session/auth/tenant contract

## Goal

Implement the frontend-ready current-user/session route expected by `execute-plans` and close the Pack D D59/D51 session-context gap.

## Missing Routes

- `GET /bff/me`

Current source references also mention `/bff/me` as a mock placeholder in i18n and session code.

## Implementation Notes

- Include tenant, roles, capabilities, locale, environment, feature flags, and session freshness in the DTO.
- Respect Pack D session/auth/tenant contract and existing JWT/operator identity behavior.
- Do not weaken strict-auth behavior for existing `/api/v1/*` routes.
- Prefer a degraded but explicit anonymous/dev DTO only where the existing BFF dev mode already permits it.

## Acceptance Criteria

- `GET /bff/me` returns a stable DTO under dev/test auth.
- Strict auth mode returns final `AUTH_REQUIRED` or `PERMISSION_DENIED` style envelopes when appropriate.
- `execute-plans` can replace its mock session context with this route.
- Tests cover locale propagation and tenant scope mismatch.

## Implementation Result

- Added `GET /bff/me` in the operator BFF.
- DTO includes current user aliases (`user`, `current_user`, `currentUser`), tenant scope, locale, environment, feature flags, roles, capabilities, and session freshness.
- Tenant resolution accepts `X-Tenant-Id`, `X-Pantheon-Tenant`, or `tenant_id` query parameter and rejects out-of-scope tenants with the final BFF error envelope.
- Locale resolution propagates `X-Locale` first, then `Accept-Language`, then verified identity claims or environment default.
- JWT extraction preserves verified claims for `/bff/me` without weakening existing strict auth behavior.
- Updated execute-plans route registry row for `GET /bff/me` to `implemented`.

## Verification

- `pytest services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py`
- `pytest services/control-plane/bff/test_bff_auth_facade.py`

Closeout verification rerun on 2026-05-08 by Codex2 after Claude review approval:

- `pytest services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py` - 10 passed.
- `pytest services/control-plane/bff/test_bff_auth_facade.py` - 66 passed.

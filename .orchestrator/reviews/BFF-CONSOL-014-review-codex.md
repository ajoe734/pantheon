# BFF-CONSOL-014 Review - Codex

Reviewed at: 2026-05-13T04:39:18Z
Reviewer: Codex
Owner: Codex2

Disposition: approved

## Findings

No blocking findings.

## Scope Reviewed

- Commit `9f741f55938db4c8eaa377f10c14e3e512a6a3c0`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`
- Supporting BFF test harness updates in the same commit

## Acceptance Check

- Lovable preview, dev, staging-live, and prod origins are present in the default BFF CORS allowlist.
- Strict production mode filters dev Lovable origins and wildcard overrides.
- Unlisted CORS origins are rejected by the rebuilt BFF app middleware.
- `PANTHEON_BFF_AUTH_STUB=true` no longer enables stub tokens when `PANTHEON_BFF_AUTH_MODE=strict`.
- JWKS strict tests cover issuer validation, audience validation, and kid rotation refresh.
- Stage0 matrix includes the dedicated JWKS strict pytest target.

## Verification

- `python3 -m pytest services/control-plane/bff/tests/test_auth_jwks_strict.py -q` -> 8 passed.
- `python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q` -> 74 passed.
- `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/tests/test_auth_jwks_strict.py services/control-plane/bff/smoke_test.py services/control-plane/bff/smoke_test_incident.py` -> passed.
- `git diff --check 9f741f55^ 9f741f55 -- .github/pantheon-stage0-matrix.json services/control-plane/bff/conftest.py services/control-plane/bff/main.py services/control-plane/bff/smoke_test.py services/control-plane/bff/smoke_test_incident.py services/control-plane/bff/test_bff_auth_facade.py services/control-plane/bff/tests/test_auth_jwks_strict.py` -> passed.
- `python3 services/control-plane/bff/smoke_test.py` -> 24 OK.

## Notes

The current worktree still contains unrelated uncommitted `services/control-plane/bff/main.py` command-envelope hunks. They were not considered part of this review approval.

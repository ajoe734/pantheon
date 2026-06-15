# E2E-R17 — BFF auth boundary (no auth-bypass)

**Round:** E2E-R17 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r17-auth-boundary
**Business flow:** every protected BFF endpoint must require credentials — even
in stub/permissive auth, a missing/empty Authorization header must be rejected.

## Verification program

`scripts/verify_e2e_auth_boundary.py` (+ unit test). For 11 protected endpoints,
asserts no-auth and empty-bearer requests are rejected (401/403) while a valid
bearer is accepted. FAILs on any endpoint that serves without credentials
(an auth-bypass regression).

## Live result (dev, 2026-06-15)

```
auth boundary over 11 protected endpoints:
  every endpoint: no-auth=401  empty-bearer=401  bearer=200
OK: every protected endpoint rejects missing/empty credentials
```

## Finding

Good-news / security round: the dev BFF runs in stub/permissive auth (any
*non-empty* bearer is accepted), but it is **not an open door** — every protected
endpoint still rejects an unauthenticated request (401). The stub relaxes *who*
is accepted, not *whether* credentials are required.

## Disposition

- **Shipped (code/CI):** the auth-boundary verifier + logic test — a regression
  gate that fails if any protected endpoint silently becomes unauthenticated.
- CI wiring consolidated in E2E-R20.

## Next round

E2E-R18: runtime-state field coherence, then consolidation (R20).

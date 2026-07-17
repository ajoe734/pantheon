# PINT-016 — Strict browser operator auth and readiness

Canonical packet: `docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`
and `docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.

## Repository and dependency

- Repository: `ajoe734/pantheon`
- Base/merge target: latest `origin/dev`
- Hard dependency: merged `PINT-011`; may run parallel with `PINT-012`

## Owned scope

- Production-shaped browser-to-BFF strict session using verifiable existing
  OIDC/Supabase JWT or server-side HttpOnly/short-lived exchange.
- Issuer/audience/tenant/role mapping, refresh/logout, CORS/CSRF, strict CI
  credentials, and Persona/OpenClaw readiness readback.

## Acceptance

- `/bff/me` yields authenticated cookie/bearer session with BFF-owned roles and
  capabilities; no browser secret or privileged default token.
- Operator mutation succeeds; viewer 403, unauthenticated 401, stub rejected;
  refresh/logout cannot retain stale privilege.
- Exact deployed strict BFF reports provider/admission readiness,
  `auth_mode=strict`, `auth_stub=false`.
- Clean worktree, focused/adjacent auth tests, scoped commit, PR, checks,
  distinct review, and merge.

## Excluded

No frontend UX, permissive proof mode, production credentials, deployment, or
trading/capital authority.

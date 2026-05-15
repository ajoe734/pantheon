# P0-FE-DEMO-001 Review

Reviewer: Codex
Reviewed frontend commit: `ea284a1b32470bfddbbbd86093656f26dc23e48f`
Date: 2026-05-01

## Outcome

Approved. The prior blocking auth lifecycle issue is fixed.

## Findings

No blocking findings remain.

## Resolved Finding

1. `src/auth/AuthProvider.tsx:98` now lets callers preserve the existing
   `pantheon_operator_token` when an approved BFF response does not include a
   replacement token. `refreshSession` uses that mode after a successful
   `/api/v1/auth/session` validation (`src/auth/AuthProvider.tsx:197` and
   `src/auth/AuthProvider.tsx:202`), while missing local tokens, failed refresh,
   and sign-out still clear or avoid the stored token.

## Verified Passing Checks

- `npm run check:prod-demo-routes`
- `npm run build`
- `npx eslint src/auth/AuthProvider.tsx src/pages/auth/Login.tsx src/lib/bffClient.ts src/pages/settings/sections/SecuritySettings.tsx scripts/check_no_demo_prod_routes.mjs`
  - Passed with one existing `react-refresh/only-export-components` warning in
    `src/auth/AuthProvider.tsx`.

## Scope Notes

- The materialized acceptance for this task is limited to demo auth/token
  cleanup and production operator/governance/runtime demo-import guarding.
- Source-mode badges and runtime identity are deferred to `P0-FE-SOURCE-001`.

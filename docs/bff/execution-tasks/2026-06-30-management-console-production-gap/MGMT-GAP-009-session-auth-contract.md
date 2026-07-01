# MGMT-GAP-009 - Management Session Auth And RBAC Contract Consistency

Owner: Claude2
Reviewer: Codex
Batch: 2.5
Fleet lane: BFF session/RBAC contract
Depends on: `MGMT-GAP-003`

## Problem

The 2026-07-01 hosted re-audit used a dev operator token with roles
`operator,reviewer,approver` and tenant `tenant-dev`. Management list endpoints
returned 200 live data, but page loads still observed `403 /bff/me`.

That split is unsafe. A management session cannot be simultaneously rejected by
the session surface and allowed to render privileged live data without an
explicit, tested contract.

## Scope

Make management session bootstrap and management data reads coherent for the dev
operator/integration-gate path:

- `GET /bff/me`
- management list/detail BFF reads used by the console
- frontend session bootstrap and auth degraded state
- role-specific fail-closed pages such as human inbox actions that require
  `research-owner`

Required behavior:

1. decide whether the integration token must include an additional viewer/session
   role or `/bff/me` must accept the existing management operator role set;
2. make `/bff/me`, tenant selection, and management reads use one documented
   RBAC/session rule;
3. if `/bff/me` returns 403, privileged live management data must not render as
   an authenticated operator session;
4. if management data reads return 200, the session banner and user menu must
   expose the same tenant/role truth;
5. keep fine-grained action gates fail-closed when a role such as
   `research-owner` is missing.

## Non-Scope

- Do not weaken production RBAC to satisfy a dev token.
- Do not bypass `/bff/me` in the frontend to hide session errors.
- Do not enable real writes by default.

## Acceptance

- BFF contract tests cover `/bff/me` success, `/bff/me` 403, tenant mismatch,
  missing action role, and matching management read behavior.
- Hosted browser probe with the documented dev gate token observes coherent
  session state and no privileged live-data render under session 403.
- The harness records the exact token shape without leaking secret values:
  roles, tenant, MFA marker, and token hash only.
- FE tests cover authenticated session, degraded unauthenticated state, and
  role-specific action denial.
- Evidence is archived with BFF curl/probe output, hosted browser output, commit
  SHA, and PR link.

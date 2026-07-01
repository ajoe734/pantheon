# MGMT-LOAD-002 - BFF Shell Summary And Jobs Route Canonicalization

Owner: Claude2
Reviewer: Codex
Parent: `MGMT-GAP-010`
Depends on: `MGMT-GAP-003`

## Problem

The management shell currently fetches full approvals, alerts, jobs, current
user, and health state during first mount. Badge counts should not require full
list payloads or expensive alert aggregation. The BFF source also has duplicate
`/bff/jobs` route definitions, which makes startup behavior harder to reason
about.

## Scope

- Add `GET /bff/management/shell-summary` with session identity, transport
  health, and cheap counts for pending approvals, open alerts, and running jobs.
- Make count freshness and degraded count sources explicit in `meta.surfaces`.
- Avoid calling the full alert/list builders solely to compute badge counts.
- Consolidate duplicate `/bff/jobs` route definitions into one canonical route.
- Add OpenAPI/schema and contract tests for summary success, degraded counts,
  redaction, and jobs route behavior.

## Acceptance

- `/bff/management/shell-summary` returns no full approvals, alerts, or jobs
  list payloads.
- Summary count tests cover success and degraded source states.
- `/bff/jobs` has one canonical implementation and one contract test source of
  truth.
- Dev BFF evidence shows shell summary p95 <= 200 ms under 10 concurrent
  requests, or archives a reviewer-approved blocker with exact bottleneck.

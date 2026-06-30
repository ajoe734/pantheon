# MGMT-GAP-002 - Frontend Canonical Management Read Wiring

Owner: Claude
Reviewer: Codex
Batch: 2
Fleet lane: Frontend BFF integration
Depends on: `MGMT-GAP-003`

## Problem

Canonical BFF endpoints now exist, but several FE management pages still use old
helpers or synthetic data paths.

## Scope

Rewire these FE pages to canonical endpoints:

- Data Sources -> `GET /bff/management/data-sources`
- Permission Matrix -> `GET /bff/management/permissions`
- Memory Governance -> `GET /bff/management/memory-governance`
- Consult Rules -> `GET /bff/management/consult-rules`
- Lineage Explorer -> `GET /bff/lineage`
- Workflow Templates -> `GET /bff/workflows`, with no seed fallback presented as
  live truth
- Hook/Cron Manager -> `GET /bff/hooks`, with no seed fallback presented as live
  truth
- Ranking Dashboard -> canonical ranking read model, or an explicit
  analytical-only label if the BFF ranking model is intentionally deferred

## Non-Scope

- Do not invent client-side DTO contracts that conflict with BFF OpenAPI.
- Do not silently keep `withLiveOrMock` seed fallback in strict live mode.

## Acceptance

- Hosted browser probe captures intended endpoint calls for each page.
- Empty/degraded pages display exact source and reason.
- No page in scope presents seed/mock data as live production state.
- Unit or Playwright tests cover success and degraded envelopes.

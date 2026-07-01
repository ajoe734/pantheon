# MGMT-LOAD-004 - Management Route Code Splitting

Owner: Codex2
Reviewer: Claude
Parent: `MGMT-GAP-010`
Depends on: `MGMT-GAP-001`, `MGMT-LOAD-001`

## Problem

The Evidence route pays the parse and execution cost of the broader management
console route graph. Evidence should not import registry detail pages, studios,
phase2 surfaces, visualization/editor libraries, or unrelated management modules
on first navigation.

## Scope

- Replace eager management route imports in `App.tsx` with route-level lazy
  modules.
- Split oversight, operations, registry/detail, phase2, v5, studios, and Agora
  route clusters.
- Lazy-load command palette internals and heavyweight drawers after shell first
  paint or user interaction when possible.
- Record bundle analyzer or build-size output before and after the split.

## Acceptance

- Initial management route JS gzip <= 800 KB, or the task archives a precise
  reviewer-approved exception with the blocking shared vendor module.
- Evidence route-specific async chunk gzip <= 150 KB excluding shared vendor
  cache, or a documented equivalent budget is approved by reviewer.
- Route smoke tests cover direct navigation, redirect aliases, and lazy chunk
  error/degraded state.
- Hosted timing probe shows Evidence first row or empty state p75 <= 1.5 s and
  p95 <= 2.5 s on dev FE after deployment.

# MGMT-LOAD-004 - Management Route Code Splitting

Owner: Codex
Reviewer: Codex2
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

## Closeout Evidence

- execute-plans PR: `https://github.com/ajoe734/execute-plans/pull/134`
- PR head: `f28b7272f61bb778927981f787a440a5a9e5e5fc`
- Merge commit on execute-plans `dev`:
  `255e60414e0ca36e29c1b2e39f0543d23d2eea80`
- PR integration gate:
  `https://github.com/ajoe734/execute-plans/actions/runs/28513916762` - success.
- Pantheon dev FE deploy:
  `https://github.com/ajoe734/execute-plans/actions/runs/28514407926` - success.
- Hosted deployment manifest reported commit
  `255e60414e0ca36e29c1b2e39f0543d23d2eea80`, `VITE_BFF_MODE=live`,
  `VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false`.
- Hosted Evidence route-load artifact:
  `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/mgmt-load-004-route-load-hosted-2026-07-01.md`.

Hosted route-load result from five samples on `/management/evidence`:

| Metric | Result | Budget |
|---|---:|---:|
| first row/empty state p75 | 931 ms | <= 1500 ms |
| first row/empty state p95 | 1203 ms | <= 2500 ms |
| primary Evidence API p75 | 837 ms | n/a |
| primary Evidence API p95 | 1131 ms | n/a |

The route-load probe used `domcontentloaded` plus content milestones and did not
use `networkidle`; the long-lived `/bff/events/stream` request was excluded from
readiness.

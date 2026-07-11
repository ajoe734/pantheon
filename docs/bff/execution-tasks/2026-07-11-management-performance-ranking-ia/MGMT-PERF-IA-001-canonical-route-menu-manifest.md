# MGMT-PERF-IA-001 - Canonical Route And Menu Manifest

Owner: Claude2

Reviewer: Codex2

Wave: 0

Repository: `ajoe734/execute-plans`

Dependencies: none

## Goal

Create one typed source of truth for management sidebar groups, canonical
center routes, tab ids, command-palette entries, breadcrumbs, and compatibility
redirects.

## Required Work

- Inventory every existing management sidebar item and assign it exactly once
  to the target groups in `TARGET_INFORMATION_ARCHITECTURE.md`.
- Define canonical routes for Performance Center, Rankings Center, and
  Governance Decisions.
- Define legacy redirects from the route migration matrix and preserve relevant
  query context.
- Make sidebar, page titles, command palette, cockpit destinations, and route
  acceptance inventory consume the shared manifest.
- Add redirect-loop, query-preservation, duplicate-id, duplicate-label, and
  desktop/mobile navigation tests.
- Keep compatibility exports if needed by downstream tasks; do not prematurely
  remove legacy page implementations in this task.

## Acceptance

- Every visible management item has one group and one canonical destination.
- No canonical center appears twice in sidebar or command palette.
- Legacy performance/ranking/allocation URLs resolve without loops.
- Persona, runtime, strategy, capital pool, period, and snapshot context is
  preserved where applicable.
- Frontend PR is merged to `execute-plans/dev` with tests and merge SHA.

## Artifacts

- `execute-plans:src/management`
- `execute-plans:src/App.tsx`
- `execute-plans:scripts/lib/management-routes.mjs`
- `execute-plans:e2e`

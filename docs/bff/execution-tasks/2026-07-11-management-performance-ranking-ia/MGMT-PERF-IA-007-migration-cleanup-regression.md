# MGMT-PERF-IA-007 - Migration Cleanup And Regression

Owner: Claude

Reviewer: Antigravity

Wave: 2

Repository: `ajoe734/execute-plans`

Dependencies:

- `MGMT-PERF-IA-003`
- `MGMT-PERF-IA-004`
- `MGMT-PERF-IA-005`
- `MGMT-PERF-IA-006`

## Goal

Remove obsolete navigation and dead implementations only after canonical
centers and contextual integrations are proven.

## Required Work

- Remove `ManagementOperationsNav` and use sidebar, tabs, breadcrumbs, and
  contextual actions consistently.
- Resolve `RankingDashboardPage`, Capital Pool Detail, Rebalance Detail, and
  Ranking Formula Detail as routed canonical components or deliberate removal.
- Consolidate duplicate top-level and nested aliases into the documented
  compatibility redirect map.
- Regenerate the management route acceptance baseline from the canonical
  manifest.
- Add route crawl, redirect-loop, broken-link, query-preservation, accessibility,
  mobile layout, and no-overlap regression coverage.
- Record migration telemetry hooks and redirect-expiry ownership.

## Acceptance

- No dead exported page remains without an explicit decision.
- No page renders a second full management navigation.
- Canonical and compatibility routes pass automated crawl and hosted smoke.
- Mobile and desktop menus expose the same hierarchy without overlap.
- Frontend PR is merged and hosted dev evidence is recorded.

## Artifacts

- `execute-plans:src/App.tsx`
- `execute-plans:src/management`
- `execute-plans:scripts/lib/management-routes.mjs`
- `execute-plans:e2e`

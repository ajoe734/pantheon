# MGMT-PERF-IA-007 - Migration Cleanup And Regression

Owner: Codex

Reviewer: Claude

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

## Migration Decisions And Evidence

- Removed the duplicate `ManagementOperationsNav` component and every render
  site. The responsive `ManagementLayout` sidebar/drawer, center tabs,
  breadcrumbs, and contextual actions are now the only navigation hierarchy.
- Mounted `CapitalPoolDetail`, `RankingFormulaDetail`, and `RebalanceDetail` on
  their canonical singular routes. Plural compatibility detail aliases now
  preserve the query string and terminate on those canonical routes.
- Removed the unrouted `RankingDashboardPage` implementation and its barrel
  export. The canonical Rankings Center owns rolling and quarterly ranking.
- Reclassified retired top-level baseline entries as compatibility aliases,
  added redirect telemetry via the
  `pantheon:management-legacy-redirect` browser event, and assigned alias
  expiry review to `management-frontend` on `2026-10-01`.

Execute-plans anchor commit: `2fb71a1`.

Focused verification:

```text
npm test -- --run src/management/navigation/managementRouteManifest.test.ts src/management/pages/CapitalPoolDetail.test.tsx
# 19 passed

npm run build
# passed (existing chunk-size, circular-chunk, and CSS minifier warnings)

PLAYWRIGHT_BASE_URL=http://127.0.0.1:8081 npx playwright test e2e/26-mgmt-perf-ia-canonical-manifest.spec.ts --project=chromium
# 5 passed

git diff --check
# passed
```

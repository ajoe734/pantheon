# Live Page And Menu Audit - 2026-07-11

Status: archived observation

Audit targets:

- hosted management console on dev;
- `ajoe734/execute-plans` frontend `dev` source;
- Pantheon BFF dev surfaces.

## Inventory Summary

- 50 visible management sidebar items.
- 7 current sidebar groups.
- 25 inspected live URLs relevant to performance, risk, rankings, capital, and
  governance.
- 1 duplicated page-level operations navigation used inside multiple pages.
- 2 ranking experiences duplicated inside Promotion Allocation.
- 4 exported page components with missing or redirected canonical routes.

## Current Navigation Problems

### Pathreon Management

This group currently mixes unrelated operator jobs: cockpit, Persona Fleet,
Human Inbox, Trading Pulse, Evolution Journal, Evidence Library, research, and
Persona trading-intent review. It is too broad to communicate where monitoring
ends and governance begins.

### Performance And Ranking

The group includes Portfolio/Capital, Persona League, Quarterly Ranking,
Performance Attribution, and Promotion Allocation. Monitoring, comparison, and
mutation governance are therefore presented as peers even though they form a
sequence.

### Operations

The large operations group mixes runtime execution, intervention, approval,
reconciliation, and governance surfaces. Human decisions are discoverable from
several names and locations.

## Duplicate Surface Findings

| Responsibility | Standalone surface | Duplicate surface | Finding |
|---|---|---|---|
| Rolling persona ranking | Persona League | Promotion Allocation `real-ranking` | Two full ranking entry points |
| Quarterly evaluation | Quarterly Ranking | Promotion Allocation `paper-candidates` | Two full quarterly entry points |
| Operational navigation | Management sidebar | `ManagementOperationsNav` in pages | Competing navigation hierarchy |
| Ranking dashboard | none | exported `RankingDashboardPage` | Implemented but unrouted |
| Capital pool detail | expected detail URL | redirect to Promotion Allocation | Detail component exported but bypassed |
| Rebalance detail | expected detail URL | redirect to Promotion Allocation | Detail component exported but bypassed |
| Ranking formula detail | expected detail URL | redirect to Promotion Allocation | Detail component exported but bypassed |

## Page Responsibility Findings

### Cockpit

Correct role: cross-domain summary, alert counts, and links.

Gap: links can lead to standalone ranking pages or embedded copies, so Cockpit
does not establish authority. It must link only to canonical centers.

### Persona Fleet

Correct role: persona/runtime state and operator entry point.

Gap: performance and review navigation currently lands on fragmented pages.
Fleet should preserve persona, runtime, period, and source context when opening
Performance Center or Governance Decisions.

### Portfolio Book / Capital

Correct role: exposure, holdings, ownership, telemetry coverage, and risk
exceptions.

Gap: isolated from attribution even though operators use both during one
investigation. It becomes Performance Center's Exposure & Holdings tab.

### Performance Attribution

Correct role: formal contribution analysis plus explicit unmatched-source
diagnostics.

Gap: fallback Persona Fleet summary can visually resemble formal attribution,
and missing holdings can render as `nan`. It becomes the Attribution tab with a
mandatory confidence banner.

### Persona League

Correct role: rolling operational comparison.

Gap: duplicated in Promotion Allocation and can be confused with status or
readiness. It becomes Rankings Center's Rolling tab.

### Quarterly Ranking

Correct role: formal evaluation snapshot for a governance cycle.

Gap: duplicated in Promotion Allocation and placed beside direct allocation
controls. It becomes Rankings Center's Quarterly tab.

### Promotion Allocation

Correct role after refactor: recommendations, capital proposals, policy, review
state, and apply receipts.

Gap: currently embeds ranking sources and governance actions in one broad page.
It becomes Governance Decisions and consumes immutable ranking snapshots.

### Persona Detail Performance

Observed content: win rate, routed strategies, and recent activity.

Finding: this is an entity summary, not formal performance analysis. Keep a
compact summary and link into Performance Center with the persona filter.

### Strategy Detail Performance

Observed content: strategy-context time series.

Finding: legitimate contextual analysis. Keep it, label its scope, and provide
a formal attribution deep link.

### Agora Strategy Performance

Observed route: `/agora/strategy-performance`.

Finding: this is Trading Room execution performance rather than management
portfolio attribution. Keep it in Agora and add boundary links; do not merge it
with Performance Center.

## Source Files Inspected

- `execute-plans:src/management/ManagementLayout.tsx`
- `execute-plans:src/management/pages/oversight/PromotionAllocation.tsx`
- `execute-plans:src/App.tsx`
- `execute-plans:src/management/components/operations/ManagementOperationsNav.tsx`
- `execute-plans:src/management/pages/phase2/RankingDashboard.tsx`
- `execute-plans:src/management/pages/PersonaDetail.tsx`
- `execute-plans:src/management/pages/CapitalPoolDetail.tsx`
- `execute-plans:src/agora/pages/strategy-performance/StrategyPerformancePage.tsx`
- `execute-plans:scripts/lib/management-routes.mjs`

The frontend source belongs exclusively to `ajoe734/execute-plans`; Pantheon
workers must not recreate an embedded frontend mirror.

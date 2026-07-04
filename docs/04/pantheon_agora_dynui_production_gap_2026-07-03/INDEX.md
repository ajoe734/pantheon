# Agora Dynamic UI Production Gap Audit - 2026-07-03

Status: production gap packet; not a completion certificate

## Summary

The live cache/header incident was repaired separately by pantheon PR #2845
(`a37600e29ab2ec7da74a10ee545d1f77dbb7139e`). That repair stops stale SPA
shells from keeping old Agora bundles alive, but it does not make the Agora
dynamic UI design production-complete.

Current evidence shows the original design-pack dynamic UI work did exist, but
the delivery chain is incomplete:

- The source task `AG-DYNUI-SRC-001` is archived done and explicitly says this
  is not a static-page conversion.
- Several DYNUI implementation PRs were merged in pantheon and execute-plans.
- Current `ai-status.json` has no active `AG-*DYNUI` task to continue.
- Key archive snapshots for later DYNUI tasks are missing from clean
  `origin/dev`.
- The live `/agora/trading-room` default path still renders the aggregate empty
  state instead of the full V10/V11 dynamic workbench entry.
- The route is still hosted inside the global PlatformShell and a three-tab
  TradingDeskLayout, so it behaves as an embedded tab surface rather than a
  standalone Agora workbench.
- There are multiple local frontend checkouts. `/home/lupin/code/execute-plans`
  `origin/dev` contains the shared-auth-header fix, while
  `/home/lupin/code/pantheon/.fe-ep` is a dirty nested checkout that still shows
  the older hand-rolled `credentials: "include"` Trading Room client. This must
  be reconciled so workers and deploy scripts operate on one canonical frontend
  source.

## Evidence

- pantheon PR #2845 merged cache/header repair:
  `a37600e29ab2ec7da74a10ee545d1f77dbb7139e`.
- Post-repair live probe at `2026-07-03T23:53:51Z`:
  `/agora/trading-room` navigation `200`; `/bff/me`,
  `/bff/management/shell-summary`, `/bff/events/stream`,
  `/bff/agora/trading-room`, and
  `/bff/agora/trading-room/decision-events` all `200`; no console errors; no
  `Failed to load Trading Room`.
- Post-repair header evidence:
  `/agora/trading-room` and `/deployment.json` return
  `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`; hashed assets
  return `public, max-age=31536000, immutable`.
- execute-plans clean `origin/dev` route evidence:
  `src/App.tsx` wraps `/agora` inside `PlatformShell`; `/agora/trading-room`
  only routes to `TradingRoomPage` with no strategy id.
- execute-plans clean `origin/dev` page evidence:
  `TradingRoomPage` only enters workspace proposal flow when `strategyId` and
  a strategy version are present; otherwise it renders `AggregateView`.
- The current root error state only displays `Failed to load Trading Room` and
  loses the underlying status/code/correlation context.

## What Was Missed

1. Browser cache and stale bundle behavior was not gated before calling the
   route production-ready.
2. The UI shell architecture remains embedded in the management PlatformShell.
3. The default `/agora/trading-room` path can still fall into a thin empty
   aggregate state instead of a design-pack dynamic entry/workbench.
4. The error state is not production-grade; it hides diagnostics and has no
   retry/correlation path.
5. The original DYNUI task truth is split: old task briefs and PRs exist, but
   active task state and archive snapshots do not provide a clean continuation
   point.
6. Frontend source/deploy truth is split between the canonical execute-plans
   repo and a stale nested `.fe-ep` checkout.
7. Full V10 Strategy Workshop to V11 Trading Room E2E acceptance is not proven
   against the hosted route.
8. `AI Trading Desk Design.zip` was referenced by the original dispatch script,
   but the canonical file is not present at the expected repo-root path in the
   current local workspace. Existing closure packs must be reconciled with the
   original zip or this must stay blocked.

## Production Definition

The Agora dynamic UI is production-level only when all of these are true:

- The canonical design/source packet is available and task truth is restored.
- Agora has an intentional standalone workbench shell or a documented approved
  exception for the global PlatformShell.
- The hosted default trading-room URL renders a real dynamic entry/workbench,
  not an inert empty table shell.
- Workspace proposal, accept, grid edit, widget revision, version history, and
  rollback flows are wired through strict BFF calls.
- Error states expose actionable diagnostics without leaking secrets.
- A hosted E2E proves the full Winner Branch V10-to-V11 flow and captures
  desktop/mobile screenshots.
- Dev FE deploy, BFF checks, Branch CI, Orchestrator Sync, and live browser
  probe all pass after merge.

## Execution Packet

Fleet tasks are materialized in:

- `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/INDEX.md`
- `scripts/dispatch_agora_dynui_production_gap_2026-07-03.py`

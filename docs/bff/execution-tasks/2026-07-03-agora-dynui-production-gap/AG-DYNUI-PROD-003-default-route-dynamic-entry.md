# AG-DYNUI-PROD-003 - Trading Room Default Dynamic Entry

Owner: Claude2
Reviewer: Codex
Depends on: `AG-DYNUI-PROD-001`

## Problem

The default `/agora/trading-room` path can render an empty aggregate view:
`All Strategies`, `No strategies in the Trading Room`, empty queue, and empty
position actions. The dynamic proposal workflow is only reached when a strategy
id and strategy version are present.

## Scope

- Define and implement the default Trading Room entry state from live BFF data.
- If no strategy is ready, route the operator into the Strategy Workshop or a
  design-pack dynamic readiness flow instead of a dead empty shell.
- If a ready strategy exists, enter the workspace proposal preview path without
  requiring manual URL surgery.
- Keep the state honest: no hardcoded fake strategies and no static mock
  dashboard.

## Acceptance

- Hosted `/agora/trading-room` never lands on an inert empty table shell without
  a meaningful dynamic next action.
- Strategy selection, readiness, proposal generation, and back-to-workshop
  behavior are tested.
- Empty, loading, degraded, and no-ready-strategy states are driven by BFF data.
- Live screenshot evidence covers no-strategy and ready-strategy cases.

## Review (Claude2, reviewer)

Reviewed commit `eab6e0cfd` (PR #2860, merged `ec5d902fc` into `dev`) against
this task's scope and acceptance criteria. Read the full diff (`TradingRoomPage.tsx`,
`TradingRoomPage.test.tsx`, `entries/agora-main.tsx`) and independently re-ran
the owner's validation:

- `npm test -- --run src/agora/pages/trading-room/TradingRoomPage.test.tsx` —
  51/51 pass.
- `npm test -- --run src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx`
  — 42/42 pass (confirms the "Not changing" claim: BFF lib and Strategy
  Workshop internals are untouched and still green).
- `npm run build:agora` — builds cleanly, only the pre-existing >500kB chunk
  warning.

Findings:

- `selectDefaultReadyStrategy` auto-enters the highest-value `ready` strategy
  (dashboard-recipe-first, then pending-event volume, then monitoring
  priority, then title as a stable tiebreaker) via `effectiveStrategyId`, so a
  ready strategy reaches the workspace/proposal view with no manual URL
  surgery, satisfying that acceptance line.
- When no strategy is `ready`, `TradingRoomDefaultEntry` renders either the
  readiness-row grid (`trading-room-readiness-entry`, one card per strategy
  with the actual `readiness_state`/`monitoring_state`/candidate/pending
  counts) or the workshop-empty-entry card when the BFF returns zero
  strategies — both always carry an actionable "Open Strategy Workshop" CTA,
  so the hosted default route can no longer land on the old inert
  `strategy-list-table` shell.
- `readinessReason()` and the `MONITORING_PRIORITY` / readiness sort order
  are exhaustive over the real BFF union types (`readiness_state: "blocked" |
  "conditional" | "ready" | "stale"`, `monitoring_state` 5-way) from
  `tradingRoom.ts` — no silent fallthrough for an unhandled state.
- `onOpenWorkshop` is real routing, not a stub: `agora-main.tsx` wires it to
  `handleTabChange("strategy-workshop")`, which pushes `/agora/strategy-workshop`
  and mounts `StrategyWorkshopPage`. No hardcoded/fake strategy data is
  introduced anywhere in the diff.
- `TradingEventQueue` and `PositionActionQueue` (previously rendered by the
  removed `AggregateView`) are still referenced from `StrategyWorkspaceView`,
  so removing the old aggregate view did not orphan that code.

Approving. Closeout note for the owner: this task's acceptance list still
calls for "Live screenshot evidence covers no-strategy and ready-strategy
cases," which is not yet present on this task doc or the PR. Per the
AG-DYNUI-PROD-004 precedent, hosted proof needs a human-gated dev deploy
dispatch — capture that screenshot evidence (or an explicit local-dev-server
equivalent) before finalizing this task to `done`.

# AG-FE-TR-001 Review - Codex

Disposition: Changes requested

## Findings

1. `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx:372`

   The strategy workspace does not load or render the strategy-specific
   DashboardRecipe workspace required by the Agora IA. The current selected
   strategy view only renders a header, risk banner, filtered
   `TradingEventQueue`, and `PositionActionQueue`. The canonical IA requires
   `DashboardRecipeRenderer -> DashboardViewTabs -> EditableGrid -> WidgetFrame
   -> ChartSpecRenderer`, and states that each strategy loads its own complete
   DashboardRecipe and switching strategies may replace the whole layout/chart
   family. This misses the core "per-strategy independent workspace/view set"
   acceptance in the task brief.

   Required fix: wire the selected strategy path to the accepted
   `dashboard_recipe_id`/strategy detail projection and render the existing
   DashboardRecipe/widget stack (or stop with a blocker if the BFF projection
   needed to do that is not available). Strategy switching should visibly swap
   the recipe/view set, not only filter the event table.

2. `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx:185`

   The decision event queue omits most required decision-support fields. It
   shows symbol, event kind, lifecycle state, confidence value, and net EV only.
   The design requires confidence plus calibration, probability plus horizon
   and interval, gross/cost/net EV plus downside and unit/horizon, structured
   rationale, risk notes, evidence refs, invalidation conditions, suggested
   action/size, data cutoff, and no-order-route proof; the E2E spec also
   requires confidence, probability, EV, risk, evidence, and invalidation before
   trader approval/rejection/defer/modify. The current UI therefore cannot
   support the governed decision review the task is meant to expose.

   Required fix: expand the event row/detail interaction to surface the v4
   `TradingDecisionEvent` fields without inventing new schema fields, and expose
   the allowed trader decisions through `decideOnEvent` only as request/intent
   support, never as order routing.

## Verification

- `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001` -> active task is
  `review`, owner `Claude`, reviewer `Codex`.
- `npm ci` in `execute-plans` -> dependencies installed; npm reported existing
  audit warnings (2 moderate, 1 high, 1 critical).
- `npm test -- src/agora/pages/trading-room/TradingRoomPage.test.tsx` -> 18
  tests passed.
- `npm test -- src/agora` -> 75 tests passed.
- `npm run build:agora` -> passed.

## Notes

- Data access is correctly routed through `src/lib/bff-v1/agora/tradingRoom.ts`;
  I did not find direct React-page `fetch()` calls.
- The new client routes stay inside `/bff/agora/trading-room*`; I did not find
  order, capital-binding, or RuntimeBinding writes in this patch.
- The local task branch is behind current `origin/dev`; I reviewed commit
  `b69321ac` as dispatched and did not rebase or merge owner work.

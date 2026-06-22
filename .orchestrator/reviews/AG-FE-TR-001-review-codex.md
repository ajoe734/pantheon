# AG-FE-TR-001 Review - Codex

Disposition: Approved after final review

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

## Second Review - 2026-06-22

Disposition: Changes requested

### Findings

1. `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx:18`

   Commit `18441482` adds recipe rendering, but the live-strict data path still
   cannot supply the recipe it renders. `StrategyWorkspaceView` calls
   `getTradingRoomStrategy(strategyId)` and `extractRecipe()` only accepts
   either a recipe object directly or `detail.recipe`. The actual BFF route
   `services/control-plane/bff/agora/trading_room/router.py:545` returns
   `DetailEnvelope.data` with `strategy_id`, `pending_event_counts`,
   `readiness_state`, and `monitoring_state`; it does not include a
   `DashboardRecipeV2`, `recipe`, or `dashboard_recipe`. The test mock at
   `TradingRoomPage.test.tsx:97` bypasses that by returning a bare recipe-shaped
   object, so `strategy-recipe-workspace` is only proven against a shape the real
   route does not return.

   Required fix: make the frontend use a contract-backed recipe source. Either
   fetch the accepted `dashboard_recipe_id` through the canonical dashboard
   recipe route/client and render that `DashboardRecipeV2`, or first change the
   strategy detail projection/spec to include a named recipe field and update
   the frontend extractor/tests to that exact envelope shape. If neither route
   is available in the accepted backend contract, stop with a blocker instead of
   mocking a non-contract shape. The previous finding #1 is not resolved until a
   selected strategy visibly renders a recipe available from the real BFF
   projection.

### Resolved Items

- The expanded `TradingDecisionEvent` detail now covers the required v4 fields:
  confidence and calibration, probability horizon and interval, EV gross/cost/net
  and downside, suggested action and non-binding size, rationale, risk notes,
  evidence refs, invalidation state, data cutoff, and no-order-route proof.
- The trader decision buttons call `decideOnEvent` and remain inside the
  request/intent-support route family.

### Verification

- `git fetch origin` -> latest `origin/dev` confirmed; the task branch remains
  behind by 26 commits.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001` -> active task is
  `review`, owner `Claude`, reviewer `Codex`.
- `npm ci` in `execute-plans` -> dependencies installed; npm reported existing
  audit warnings (2 moderate, 1 high, 1 critical).
- `npm test -- src/agora/pages/trading-room/TradingRoomPage.test.tsx` -> 40
  tests passed; this confirms the current mocks, but does not cover the live BFF
  strategy-detail envelope shape described above.
- Static contract check against
  `services/control-plane/bff/agora/trading_room/router.py` and
  `services/control-plane/openapi/agora_v1_3.openapi.yaml`.

## Final Review - 2026-06-22

Disposition: Approved

### Resolution

- Reviewed head `d16ceb85`. The second review finding is resolved:
  `StrategyWorkspaceView` now reads `strategy.dashboard_recipe_id` from the
  Trading Room aggregate and fetches the recipe through
  `getDashboardRecipeById` (`GET /bff/agora/dashboard-recipes/{recipe_id}`).
- The page no longer imports `getTradingRoomStrategy` or uses the non-contract
  `extractRecipe` helper for recipe loading.
- Static contract checks line up: `TradingRoomStrategy` includes
  `dashboard_recipe_id`; `dashboard/router.py` returns the active
  `DashboardRecipeV2` in the `data` envelope for the canonical recipe route.
- No new order, capital-binding, RuntimeBinding, or expanded capability route
  use was found in the reviewed patch.

### Verification

- `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001` -> active task is
  `review`, owner `Claude`, reviewer `Codex`.
- `npm ci` in `execute-plans` -> dependencies installed; npm reported existing
  audit warnings (2 moderate, 1 high, 1 critical).
- `npm test -- src/agora/pages/trading-room/TradingRoomPage.test.tsx` -> 40
  tests passed.
- `npm test -- src/agora` -> 97 tests passed.
- `npm run build:agora` -> passed; Vite reported the existing large chunk size
  warning for the Agora bundle.

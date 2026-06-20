# `execute-plans@dev` Agora UI IA and Dependency Decision

## Current truth

The current dev branch still exposes the legacy Agora navigation. The new UI must not be implemented as another set of unrelated side-menu pages.

## Canonical primary IA

```text
/agora/trading-room          交易操盤室
/agora/strategy-workshop     策略工坊
/agora/strategy-performance  策略執行與績效
```

The application opens `/agora/trading-room` by default.

Legacy routes are retained as redirects or context drawers during migration:

```text
/agora/daily       -> /agora/trading-room
/agora/watchlist   -> /agora/trading-room?drawer=watchlist
/agora/signals     -> /agora/trading-room?view=decision-queue
/agora/notebook    -> /agora/strategy-workshop
/agora/ask         -> servant drawer over the current workspace
/agora/journal     -> journal/replay drawer
/agora/trainer     -> servant correction drawer
```

## Page composition

### Trading Room

```text
TradingDeskShell
  StrategyLensSwitcher
  DashboardRecipeRenderer
    DashboardViewTabs
    EditableGrid
      WidgetFrame
      ChartSpecRenderer
  TradingEventQueue
  PositionActionQueue
  ServantDrawer
  DashboardProposalPreview
  DashboardChangeLog
```

Each strategy loads its own complete DashboardRecipe. Switching strategy may replace the entire layout and chart family, not merely the data.

### Strategy Workshop

```text
StrategyWorkshopPage
  WorkshopConversation (70%)
    StrategyUnderstandingCard
    MissingDefinitionCard
    ResearchPlanCard
    ResearchRunCard
    BacktestResultCard
    VersionCompareCard
  StrategyCompletenessRail (30%)
  ServantComposer
```

### Strategy Execution & Performance

```text
StrategyPerformancePage
  MultiStrategyRankTable
  StrategyExecutionTimeline
  RuleVsHumanInterventionComparison
  ShadowComparison
  AttributionPanel
  EvolutionSuggestionPanel
```

## Dashboard editing

The servant first generates a complete proposal. The trader may then:

- drag a widget
- resize a widget
- remove it
- add a registered widget
- change its chart
- select one widget and instruct the servant to redesign it
- preview before/after
- accept, reject or keep both
- review versions and rollback

## Libraries

Current `execute-plans@dev` already has Recharts. Use it for:

```text
metric, line, area, simple bar
```

Add:

```json
{
  "echarts": "^5.6.0",
  "echarts-for-react": "^3.0.2",
  "react-grid-layout": "^1.5.0"
}
```

and dev dependency:

```json
{
  "@types/react-grid-layout": "^1.3.5"
}
```

Use ECharts for heatmap, network, sankey, candlestick, complex scatter and gauge. Use react-grid-layout for drag/resize. Existing react-resizable-panels remains for shell-level split panels.

## Renderer dispatch

```text
metric/line/area/simple bar -> RechartsRenderer
heatmap/network/sankey/candlestick/gauge/complex scatter -> EChartsRenderer
table/builtin decision cards -> BuiltinWidgetRenderer
```

No component may execute arbitrary HTML/JS from a WidgetSpec.

## BFF boundary

All reads and writes use `src/lib/bff-v1/agora/*`; pages must not call `fetch()` directly.

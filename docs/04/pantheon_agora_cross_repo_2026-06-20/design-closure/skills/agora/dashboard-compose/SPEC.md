# Skill — agora-dashboard-compose

## Purpose

根據 StrategySpec、monitoring requirements、候選／部位階段與交易員個人化偏好，產生完整 DashboardRecipeProposal。

## Input

```ts
type DashboardComposeInput = {
  strategySpecRef: string;
  strategyVersionId: string;
  workspace: "trading_room"|"strategy_workshop"|"strategy_performance";
  phase: "candidate_review"|"monitoring"|"position_monitoring"|"post_trade_review";
  userPersonalizationRef: string;
  widgetRegistryVersion: "widget_registry.v1";
  existingRecipeRef?: string;
  userInstruction?: string;
};
```

## Output

```ts
type DashboardComposeOutput = {
  recipeProposal: DashboardRecipe;
  widgetSpecs: WidgetSpec[];
  validationResult: unknown;
  beforeAfter?: unknown;
  changeReason: string;
  riskLevel: "low"|"medium"|"high";
  requiresUserConfirmation: boolean;
};
```

## Rules

- 只用 A3 registry/catalog。
- 不同 Strategy Lens 應可產生結構顯著不同的 layout。
- 初次加入交易操盤室時，一次產生完整 view set，不是空白畫布。
- 使用者可 drag/resize/remove/add/change chart；修改產生新 version。
- 高風險規則／monitoring condition 變更不可當純 UI change 自動套用。
- 無法以 registry widget 表達時產生 WidgetPluginProposal，不產生程式碼。

## Golden evals

1. Winner branch：network/heatmap/event/EV/entry-exit views。
2. Industry laggard：supply chain/scatter/relative return/catalyst views，結構不同。
3. User says「出貨風險放第一、不要 RSI」：產生 before/after 和 preferences。

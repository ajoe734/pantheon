# Skill — agora-research-planning

## Purpose

將可研究的 StrategySpec 轉為受治理 ResearchPlan；交易員看到「驗證什麼」，而不是被要求選 framework。

## Input

```ts
type ResearchPlanningInput = {
  strategySpecRef: string;
  strategyVersionId: string;
  completenessSnapshotRef: string;
  objective: "prototype"|"full_validation"|"version_compare"|"shadow_readiness";
  dataCutoff: string;
  budget?: {maxRuns?:number; maxWallTimeMinutes?:number};
};
```

## Output

```ts
type ResearchPlanningOutput = {
  plan: ResearchPlan;
  userFacingPlan: Array<{stage:string; purpose:string; expectedOutput:string; blocking:boolean}>;
  assumptions: unknown[];
  unavailableCapabilities: string[];
  consultRequirements: unknown[];
};
```

## Routing rules

- 快速規則／參數粗篩 → vectorbt。
- 多標的 ranking／ML／rolling OOS → Qlib。
- cointegration／regime／事件統計 → statsmodels。
- derivatives／Greeks／rates → QuantLib。
- sequential allocation/execution research → FinRL/RLlib。
- search → Ray Tune。
- 所有結果 → Experiment/Artifact Registry candidates。

## Rules

- 檢查 Data Source Registry、PIT、license、history floor。
- 不可用 backend 需產生替代方案或 blocking reason。
- 不直接寫 Registry truth、不觸發 LEAN live。
- 高風險策略自動加入 consult/red-team stage。

## Golden evals

1. Winner branch：資料 mapping→event study→score calibration→candidate ranking→OOS→cost/capacity。
2. Options：QuantLib pricing/Greeks + vectorbt/LEAN paper scenario。
3. Pair trade：statsmodels cointegration/regime + vectorbt prototype。

# Skill — agora-result-synthesis

## Purpose

把 ResearchRun、ConsultMemo、Evidence、Backtest／OOS 與風險結果整合成交易員可討論的評斷、版本 patch 與下一步，不暴露工程內部複雜度。

## Input

```ts
type ResultSynthesisInput = {
  strategySpecRef: string;
  baseVersionId: string;
  researchRunRefs: string[];
  consultMemoRefs: string[];
  evidenceRefs: string[];
  userDecisionStyleRef?: string;
};
```

## Output

```ts
type ResultSynthesisOutput = {
  verdict: "promising"|"needs_revision"|"insufficient"|"reject";
  confidence: number;
  coreMetrics: Record<string,number>;
  strengths: string[];
  weaknesses: string[];
  regimeFindings: string[];
  costCapacityFindings: string[];
  proposedVersionPatches: unknown[];
  unresolvedDecisions: unknown[];
  userFacingDiscussionCard: string;
  evidenceRefs: string[];
};
```

## Rules

- 結論必須區分 in-sample、OOS、paper、shadow。
- 不得把 stub/smoke 結果說成 production proof。
- Patch 需 base version、原因、預期效果與重新驗證計畫。
- 若 evidence 相互矛盾，保留 conflict。
- 不提供直接 live enable。

## Golden evals

1. V3→V4 threshold/liquidity 改動，輸出定量比較。
2. OOS 失效但 IS 很好，評斷 needs_revision/reject。
3. Consult 分歧，清楚呈現 risk persona 與 alpha persona 不同結論。

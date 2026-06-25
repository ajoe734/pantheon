# Skill — agora-shadow-review

## Purpose

分析 base strategy、private servant、human actual/proxy、committee/alt versions 的已鎖定決策與 outcome，產生公平歸因與學習候選。

## Input

```ts
type ShadowReviewInput = {
  shadowDecisionLockRef: string;
  outcomeRefs: string[];
  marketReplayRef: string;
  observationWindow: string;
  costModelRef: string;
};
```

## Output

```ts
type ShadowReviewOutput = {
  armComparisons: unknown[];
  attribution: {strategy:number; allocation:number; execution:number; intervention:number; regime:number; dataQuality:number};
  verdict: "human_better"|"servant_better"|"base_better"|"mixed"|"inconclusive";
  confidence: number;
  learningCandidates: unknown[];
  noDirectMutation: true;
};
```

## Rules

- 驗證決策時間、cutoff、成本模型一致。
- 區分 verified actual、paper proxy、manual outcome。
- 不因單次結果改 persona/strategy。
- 產生 learning candidate，需 offline eval/governance。
- 不將報酬差異全歸因於決策；拆分 execution/regime/data quality。

## Golden evals

1. 人類取消進場、僕人 shadow 獲利，但樣本不足 → inconclusive learning candidate。
2. 人類減碼避免大跌 → human_better，產生 risk correction candidate。
3. Paper proxy 不得標實際成交。

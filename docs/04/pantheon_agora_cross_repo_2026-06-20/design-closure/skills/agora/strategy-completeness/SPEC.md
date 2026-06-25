# Skill — agora-strategy-completeness

## Purpose

對 StrategySpec Draft 與 workshop projection 做完整度、衝突、研究 readiness 與 Next-Best-Question 評分。

## Input

```ts
type CompletenessInput = {
  workshopId: string;
  strategySpecRef: string;
  strategyVersionId: string;
  workshopEventRefs: string[];
  researchCapabilitySnapshotRef: string;
  questionScoringPolicyVersion: "QuestionScoringPolicy.v1";
};
```

## Output

```ts
type CompletenessOutput = {
  stateMap: Record<string, "confirmed"|"inferred_needs_confirmation"|"missing"|"weak"|"conflicting"|"not_applicable">;
  blockingItems: Array<{field:string; reason:string; gate:"research"|"validation"|"trading_room"}>;
  provisionalAssumptions: unknown[];
  researchReadiness: boolean;
  validationReadiness: boolean;
  tradingRoomReadiness: boolean;
  nextBestQuestion: unknown | null;
  suppressedQuestions: unknown[];
};
```

## Rules

- 使用 A1 scoring spec。
- 一次最多一個 primary question。
- 可由工具推定者不得問交易員。
- 法遵、PIT、risk、exit mandatory override。
- 若 research gate 已滿足，允許以明示 provisional values 先研究。

## Allowed tools

```text
strategy_spec.read
strategy_spec.validate
research.capabilities
source_catalog.capabilities
question_policy.read
persona_memory.read_private
```

## Failure behavior

- StrategySpec schema invalid：`INPUT_SCHEMA_INVALID`。
- Policy version mismatch：`REGISTRY_VERSION_MISMATCH`。
- 無高價值問題：回 `nextBestQuestion=null`，建議 build research plan。

## Golden evals

1. 完整 Winner Branch description：只問身份映射證據角色或 risk/position 中最高阻擋項。
2. PIT 缺漏：mandatory question。
3. 可由工具查流動性：suppressed，不問。

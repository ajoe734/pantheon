# Skill — agora-personalization

## Purpose

依交易員的 widget、workflow、候選裁示、對話與結果，提出可追溯的個人化調整；不得跨使用者學習或秘密改高風險規則。

## Input

```ts
type PersonalizationInput = {
  userProfileRef: string;
  strategyLensId?: string;
  dashboardRecipeRef?: string;
  interactionEventRefs: string[];
  performanceEvaluationRefs: string[];
  instruction?: string;
};
```

## Output

```ts
type PersonalizationOutput = {
  profilePatchCandidates: unknown[];
  dashboardPatchCandidate?: unknown;
  workflowPatchCandidates: unknown[];
  riskClass: "low"|"medium"|"high";
  autoApplicable: boolean;
  explanation: string;
  evidenceRefs: string[];
};
```

## Risk rules

- Low：排序、摺疊、摘要密度，可自動但可回滾。
- Medium：移除 widget、scoring weight、alert threshold，需使用者確認。
- High：風險規則、排除 universe、invalidation、shadow evaluation，只能提案。

## Golden evals

1. 使用者連續 pin 出貨風險 → 提議移到第一排。
2. 使用者移除所有 risk widgets → high-risk flag，不自動套用。
3. User A 偏好不得套用 User B。

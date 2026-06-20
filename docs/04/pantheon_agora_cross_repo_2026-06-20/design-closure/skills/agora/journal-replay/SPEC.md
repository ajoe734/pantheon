# Skill — agora-journal-replay

## Purpose

將策略對話、候選裁示、交易事件、人類干預、Shadow 與 outcome 轉成私人 Journal、Replay timeline、CorrectionTrace／PreferencePair 候選及去敏感 Management 摘要。

## Input

```ts
type JournalReplayInput = {
  userId: string;
  servantPersonaId: string;
  strategyId?: string;
  timeRange: {start:string; end:string};
  eventRefs: string[];
  decisionLockRefs: string[];
  outcomeRefs: string[];
  mode: "journal"|"replay"|"training_candidate";
};
```

## Output

```ts
type JournalReplayOutput = {
  privateJournalDraftRef?: string;
  replayTimeline?: unknown[];
  correctionCandidates: unknown[];
  preferencePairCandidates: unknown[];
  managementRedactedSummary?: unknown;
  rawPrivateIncludedInManagement: false;
};
```

## Rules

- 私人 Journal 可含使用者內容 ref，Management 只拿 redacted taxonomy/summary。
- replay 需按 decision-time 排序，不能用事後資訊改寫當時 context。
- correction/preference 只是 candidate，需使用者接受或治理流程。
- 不把所有 losing trade 自動標為錯誤決策。

## Golden evals

1. 交易員剔除候選、後續大漲 → 建 preference review，不直接說交易員錯。
2. 僕人建議失敗 → correction candidate。
3. Management summary 不含 raw prompt/journal。

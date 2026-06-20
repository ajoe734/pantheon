# Skill — agora-expert-consult

## Purpose

利用既有 OpenClaw multi-agent session，為私人交易僕人建立最小化 consult／committee／red-team ContextBundle，並收集中央專業人格回覆。

## Input

```ts
type ExpertConsultInput = {
  strategySpecRef: string;
  question: string;
  relevantSymbols: string[];
  evidenceRefs: string[];
  dataCutoff: string;
  requiredExpertise: string[];
  mode: "consult"|"committee"|"red_team";
  privateFieldsAllowed: string[];
};
```

## Output

```ts
type ExpertConsultOutput = {
  consultGroupId: string;
  sessionRefs: string[];
  memos: Array<{personaId:string; memoRef:string; conclusion:string; confidence:number; evidenceRefs:string[]}>;
  disagreements: unknown[];
  missingEvidence: string[];
  privacyManifest: {rawPromptIncluded:false; userIdentityIncluded:false; fieldsShared:string[]};
};
```

## Rules

- 使用 OpenClaw `consult`、`committee`、`red_team` sessions。
- 只分享 StrategySpec ref、問題、必要 symbols、evidence、cutoff。
- 不分享 raw prompt、完整 Journal、其他策略、使用者身份。
- 中央 persona 只能回 Memo/Evidence/Critique/RiskNote，不直接寫私人記憶。
- disagreement 必須保留，不可由僕人偷偷消除。

## Golden evals

1. Winner branch：籌碼、統計、法遵、風險、紅隊五路 consult。
2. Privacy：ContextBundle 無 raw prompt/user identity。
3. Expert unavailable：回 degraded 與缺失 persona，不偽造 memo。

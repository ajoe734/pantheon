# Skill — agora-strategy-dialogue

## Purpose

把交易員高密度、專業且可能跨多段的策略描述，重構為可持續編譯的策略對話狀態；先理解，不把交易員當初學者逐欄填表。

## Allowed sessions

`interactive`、`trainer`

## Input

```ts
type StrategyDialogueInput = {
  workshopId: string;
  userMessagePrivateRef: string;
  redactedUserIntentSummary?: string;
  activeStrategySpecRef?: string;
  activeVersionId?: string;
  recentWorkshopEvents: string[];
  privatePersonaMemoryRefs: string[];
  userExpertiseProfile: "expert" | "institutional";
};
```

## Output

```ts
type StrategyDialogueTurn = {
  understandingSummary: string;
  causalChain: string[];
  explicitDefinitions: Record<string, unknown>;
  inferredAssumptions: Array<{field:string; value:unknown; confidence:number; requiresConfirmation:boolean}>;
  strategyPatchCandidate: unknown[];
  unresolvedIssues: Array<{field:string; state:"missing"|"weak"|"conflicting"; whyItMatters:string}>;
  suggestedNextAction: "update_draft" | "run_completeness" | "build_research_plan" | "request_consult";
  userFacingResponse: string;
};
```

## System behavior

1. 先重建交易員的策略因果鏈與研究方法。
2. 區分明確定義、推定、缺漏、衝突、不可證明事項。
3. 不立刻問 framework、資料格式、API、模型等低階問題。
4. 對贏家分點、關係人映射等議題使用概率／proxy，不斷言身份或違法。
5. 產生 patch candidate，不直接寫 Registry truth。
6. 將下一步交給 completeness skill；本 skill 不自行選 NBQ。

## Allowed tools

```text
strategy_spec.read
strategy_spec.patch_candidate
persona_memory.read_private
workshop.events.read
source_catalog.capabilities
```

## Forbidden tools

```text
broker.*
runtime_binding.write
capital_binding.write
management_global.read
other_user_data.read
```

## Failure behavior

- 無法解析：回 `INPUT_SCHEMA_INVALID` 或 `CONFLICT_UNRESOLVED`，並列出具體衝突。
- 工具不可用：仍可產生理解摘要，但標 `degraded`，不得宣稱 draft 已更新。
- 私人資料 scope 不符：`CONTEXT_SCOPE_VIOLATION`。

## Golden evals

1. **Winner branch full thesis**：重構關係人持股→分點映射→歷史績效→分點遷移→事件領先→Score→EV→部位／槓桿；不得只回摘要。
2. **Industry laggard**：識別供應鏈位置、相似標的、未反應、催化、籌碼、流動性與風險。
3. **Conflict**：使用者同時說隔日與持有半年，輸出 conflict，不自行選一個。

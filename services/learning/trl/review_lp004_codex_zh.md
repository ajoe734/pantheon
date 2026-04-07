# LP-004 TRL Preference Learning 審查意見（Codex）

**任務**: `LP-004`  
**作者**: Grok / Copilot  
**審查者**: Codex  
**狀態**: APPROVED after contract alignment corrections

## 結論

這一輪可以通過，但前提是已吸收三個會直接讓下游接錯契約的修正：

1. `WORKFLOW_DEFINITION.md` 的 FB-002 讀取流程改回 canonical event shape，不再假設不存在的 `action` / nested artifact 欄位
2. registry handoff 改回 `REG-001` 已存在的 `model_artifact` 類型，並用 `metadata.model_family=preference_model` 區分 family
3. preference model 僅能在 `paper` 狀態被 evaluator 消費，`candidate` 只能做離線驗證，避免未審核模型影響 promotion decision

在這三點對齊後，LP-004 的治理邊界就和 `TARGET_ARCHITECTURE.md`、`FB-002`、`REG-001` 一致了。

## Absorbed Corrections

### 1. FB-002 event shape 已和 canonical schema 對齊

原本 workflow pseudocode 用的是：

- `event['action']`
- `event['target']['artifact']`
- `event['target']['prior_artifact']`
- `event['edited_artifact']`

但 `services/feedback/schema/trader_feedback_event.schema.json` 的正式欄位是：

- `event_type`
- `target` linkage object
- `edits`

這不是小命名問題，而是會讓實作者直接照錯 event shape。現在文件已改成：

- 從 canonical `target` linkage 解析 base artifact
- `edit` 事件透過 `edits` 套用在 base artifact 上，產生 preferred variant
- provenance 與 dedup key 也改成以 `registry_id` / `artifact_version` 為主

### 2. Registry handoff 已對齊 REG-001，而不是私自擴充 artifact type

原本 LP-004 直接宣告：

- `artifact_type=preference_model`
- `SELECT * FROM preference_models`

但 `services/registry/contract.md` 和 `services/registry/registry_entry_schema.json` 目前並沒有這個 artifact type；canonical 類型只有：

- `strategy_spec`
- `model_artifact`
- `feature_set`
- `prompt_bundle`
- `signal_snapshot`
- `execution_bundle`

這一輪已改成：

- registry entry 使用 `artifact_type=model_artifact`
- family 區分放在 `metadata.model_family=preference_model`

這樣 LP-004 不會再要求下游額外發明一套 registry enum。

### 3. evaluator consumption 現在只允許 `paper` preference models

原本 contract 一邊說 preference model 不能 bypass approval，一邊又允許 `candidate` model 給 evaluator 內部使用。這會讓尚未經 operator review 的 learning artifact 介入 promotion recommendation。

現在已收斂成：

- `draft`: development only
- `candidate`: offline validation / registry checks only
- `paper`: evaluator / paper-only reward shaping 可用

這和 `EV-001_INTEGRATION.md` 內本來就採用 `lifecycle_state='paper'` 的查詢模式一致。

## Reviewer Decision

`LP-004` 通過審查。

目前的 TRL 文件已經把 preference-learning scope、governed input boundary、registry handoff、以及 evaluator integration 都收斂到 canonical architecture。剩餘風險不是這一輪的阻斷項，而是後續真正做 upstream TRL package pin / adapter / smoke test 時要補上實作面。

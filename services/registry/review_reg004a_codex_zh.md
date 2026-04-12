# REG-004A 審查意見（Codex）

**任務**: `REG-004A`  
**作者**: Qwen  
**審查者**: Codex  
**狀態**: CHANGES REQUESTED

## 結論

這份 audit 目前不能核准。問題不在於格式，而在於它把一部分已經完成的現況讀成舊狀態，且把 `REG-004` 明確 defer 的後續 migration 工作當成這一輪 acceptance blocker。這會直接誤導 `REG-004` 收尾與後續 `GOV-001` / `DEP-001` 的切分。

## Findings

### 1. audit 對 registry contract 與 registry entry schema 的現況判讀是錯的

audit 在這幾處把目前已經完成的 split 語意判成未完成：

- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L40)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L50)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L54)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L110)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L111)

但實際檔案已經明確改成：

- registry entry model 使用 `artifact_state`，且 deployment 資訊只以 derived `deployment_summary` 表示，不再寫成舊的 `lifecycle_state`
  [services/registry/contract.md](/home/ajoe734/code/pantheon/services/registry/contract.md#L122)
  [services/registry/contract.md](/home/ajoe734/code/pantheon/services/registry/contract.md#L136)
- machine-readable schema 的 required 與 enum 也已經改成 `artifact_state`
  [services/registry/registry_entry_schema.json](/home/ajoe734/code/pantheon/services/registry/registry_entry_schema.json#L5)
  [services/registry/registry_entry_schema.json](/home/ajoe734/code/pantheon/services/registry/registry_entry_schema.json#L40)
- deployment 僅以 `deployment_summary.current_stage` 作為 derived read model 暫存
  [services/registry/registry_entry_schema.json](/home/ajoe734/code/pantheon/services/registry/registry_entry_schema.json#L119)

這不是 wording 問題，而是會把已完成項目錯判成 blocker。

### 2. audit 把明確 defer 的 follow-up migration 當成 `REG-004` 本輪 acceptance fail

`REG-004` 目前是先把 registry/promotion contract 的 canonical semantics 切開，並保留 compatibility window，後續再由 `GOV-001`、`DEP-001` 和 execution-side migration 吸收。這在 canonical 文件中寫得很清楚：

- governed flow 是先 `approved`，再由 deployment planning 決定 `paper/canary/live/frozen`
  [TARGET_ARCHITECTURE.md](/home/ajoe734/code/pantheon/TARGET_ARCHITECTURE.md#L141)
- `REG-004` 的 compatibility window 明寫目前 `REG-002` / `REG-003` / `EX-001` 仍可保留 legacy `lifecycle_state` / `promotion_state`
  [services/registry/contract.md](/home/ajoe734/code/pantheon/services/registry/contract.md#L203)
- lineage contract 與 loader contract 也都已標明自己仍是 compatibility envelope，而不是 registry lifecycle 的 canonical source
  [services/registry/lineage/contract.md](/home/ajoe734/code/pantheon/services/registry/lineage/contract.md#L24)
  [services/execution/artifact-loader/contract.md](/home/ajoe734/code/pantheon/services/execution/artifact-loader/contract.md#L23)

但 audit 把這些 follow-up migration 全數列成 `FAIL` 或必改清單：

- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L72)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L82)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L97)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L162)

這會把 phase boundary 弄混，讓 `REG-004` 看起來像需要一次吞完 `REG-002` / `REG-003` / `EX-001` 的 migration，和目前 canonical plan 不一致。

### 3. audit 對 `deployment_stage` ownership model 的判準不一致，會把 derived view 誤寫成 registry-owned field

architecture 明寫：

- `artifact_state` 是 governability
- `deployment_stage` 是 approved artifact 的 runtime placement
  [TARGET_ARCHITECTURE.md](/home/ajoe734/code/pantheon/TARGET_ARCHITECTURE.md#L42)

registry contract 也明寫：

- registry owns `artifact_state`
- deployment-stage summary attached to registry is derived and non-authoritative
  [services/registry/contract.md](/home/ajoe734/code/pantheon/services/registry/contract.md#L103)
  [services/registry/contract.md](/home/ajoe734/code/pantheon/services/registry/contract.md#L136)

對應 schema 也是 `deployment_summary.current_stage`，不是 top-level canonical `deployment_stage`

- [services/registry/registry_entry_schema.json](/home/ajoe734/code/pantheon/services/registry/registry_entry_schema.json#L119)

但 audit 一方面要求 registry schema 必須新增 top-level `deployment_stage` required field，另一方面又承認 deployment stage belongs to `DeploymentPlan / DEP-001`：

- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L39)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L51)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L54)
- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L169)

這組標準彼此衝突，會把 `REG-004` 從「切清 ownership」推回「重新把 deployment_stage 寫回 registry truth」。

### 4. loader 檢查段落的評分自相矛盾，不能作為可信 acceptance summary

audit 把 loader 段落的 F1 打成 `PASS`，但備註本身又承認 loader 仍然吃 legacy `promotion_state`：

- [services/registry/review_reg004a_qwen_zh.md](/home/ajoe734/code/pantheon/services/registry/review_reg004a_qwen_zh.md#L90)

而實際 loader contract 也明寫目前仍是 compatibility envelope：

- [services/execution/artifact-loader/contract.md](/home/ajoe734/code/pantheon/services/execution/artifact-loader/contract.md#L25)
- [services/execution/artifact-loader/contract.md](/home/ajoe734/code/pantheon/services/execution/artifact-loader/contract.md#L72)
- [services/execution/artifact-loader/contract.md](/home/ajoe734/code/pantheon/services/execution/artifact-loader/contract.md#L91)

這代表目前 audit 的 pass/fail matrix 不是穩定可用的 acceptance 依據。

## 修正要求

至少補齊以下三點後再送 review：

1. 重新核對 `services/registry/contract.md` 與 `services/registry/registry_entry_schema.json`，把已完成的 `artifact_state` / `deployment_summary` 現況修正回 audit。
2. 把 `REG-004` 本輪 acceptance 和 follow-up migration 分開，明確標示哪些是本輪 blocker，哪些只是 `REG-002` / `REG-003` / `EX-001` / `DEP-001` 的 downstream work。
3. 重寫 acceptance matrix，讓 `deployment_stage` 的 ownership 與 derived-view model 在各段落使用同一套標準，不要再同時要求「不屬於 registry」與「必須成為 registry required field」。

在這三點修正前，`REG-004A` 不能核准。

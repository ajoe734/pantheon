# main.py 殘餘 seam 盤點與抽取排程

量測基準：`origin/dev` 65cdb9fa5，`services/control-plane/bff/main.py` 20,279 行。

## 名詞

**Seam**：可下刀的接縫 —— 一段邏輯本該住在自己的模組，但仍留在 main.py，
測試只能 `import main` 整包拉進來才驗得到。把它移到該住的地方、讓測試能單獨呼叫，就是抽出 seam。

**殘餘 seam**：main.py 已從 8 月的 69,248 行降到 20,279 行，大部分已切出。
殘餘的是尚未切、但未完成批次的測試仍然依賴的那些 —— 本文件盤點出 **60 個**。

## 為什麼需要這份文件

每個測試遷移批次的授權只含測試檔，不含 main.py。當批次要驗的邏輯還留在 main.py，
worker 無法自行搬動，只能停下並要求開一個抽取前置任務。目前節奏是
「派工 → 撞到 → blocked → 開 corrective → 等 → 重派」，而撞到什麼事先算得出來。

## 三個關鍵事實

1. **主要耦合是共享可變狀態，不是函式。** 60 個之中模組級可變狀態明顯多於函式。
   狀態要改成注入，不是搬家。
2. **批次要「全部」符號到齊才解鎖**，差一個都不行。所以「某 seam 解鎖 N 個批次」是誤導說法 ——
   它只讓那 N 個批次各自少差一個。實測：把 5 個最高頻共享 seam 全抽掉，也只放得出 1 個批次。
3. **抽取永遠序列**（全在改 main.py，並行必衝突）。抽取的價值不在自身平行，
   而在把批次放出來 —— 批次各改各的測試檔，彼此可平行，那才是產能所在。

## 抽取排程（貪婪：每次選剩餘最少的批次，抽完它需要的全部）

共享符號由最先抵達的批次抽出，後續批次自動受益，因此後面每步的新增成本低於其總需求。
「函式行數」僅計函式型符號，狀態型符號不計行數但需改注入。

| 步 | 批次 | 狀態 | 本步新抽 | 累計符號 | 函式行數 | 累計解鎖 |
|---|---|---|---|---|---|---|
| 1 | B12 管理主控台讀模型 | todo | 2 | 2 | 136 | 1 / 9 |
| 2 | B10 演化計畫 | in_progress | 3 | 5 | 0 | 2 / 9 |
| 3 | B02 ASK／助理工作坊 | in_progress | 4 | 9 | 0 | 3 / 9 |
| 4 | B05 治理審批 | in_progress | 6 | 15 | 175 | 4 / 9 |
| 5 | B16 指令寫入流程 | in_progress | 4 | 19 | 194 | 5 / 9 |
| 6 | B08 策略／資金／排名 | todo | 6 | 25 | 63 | 6 / 9 |
| 7 | B04 安全／錯誤／冪等 | in_progress | 12 | 37 | 192 | 7 / 9 |
| 8 | B18 跨切面整併 | todo | 10 | 47 | 472 | 8 / 9 |
| 9 | B11 管理助理維運 | todo | 13 | 60 | 446 | 9 / 9 |

**前 6 步（25 個符號）解鎖 6 個批次；最後 3 個批次要再花 35 個。**
建議把投入集中在前 6 步，之後重新評估 B04 / B18 / B11 是否值得同等力度。

## 每步的抽取內容

### 第 1 步 — B12 管理主控台讀模型（todo）

建議任務 ID：`BFF-SEAM-B12-CORRECTIVE-001`

需新抽 2 個符號（累計 2）：

- `_build_operator_alerts_payload`　函式 50 行
- `_pm12_performance_attribution_sources`　函式 86 行

### 第 2 步 — B10 演化計畫（in_progress）

建議任務 ID：`BFF-SEAM-B10-CORRECTIVE-001`

需新抽 3 個符號（累計 5）：

- `_COMMAND_AUTH_CONTEXT`　模組級狀態，需改注入
- `_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY`　模組級狀態，需改注入
- `_GOV_BFF_IDEMPOTENCY`　模組級狀態，需改注入

### 第 3 步 — B02 ASK／助理工作坊（in_progress）

建議任務 ID：`BFF-SEAM-B02-CORRECTIVE-001`

需新抽 4 個符號（累計 9）：

- `_AGORA_CORE_BFF_IDEMPOTENCY`　模組級狀態，需改注入
- `_ASSISTANT_SESSION_STORE`　模組級狀態，需改注入
- `_ASSISTANT_TRANSCRIPT_STORE`　模組級狀態，需改注入
- `_sse_buffers`　模組級狀態，需改注入

### 第 4 步 — B05 治理審批（in_progress）

建議任務 ID：`BFF-SEAM-B05-CORRECTIVE-001`

需新抽 6 個符號（累計 15）：

- `_FINAL_CONTRACT_IDEMPOTENCY`　模組級狀態，需改注入
- `_human_inbox_decision_projection_from_record`　函式 46 行
- `_human_inbox_decision_recommendation_id`　函式 58 行
- `_human_inbox_trusted_promotion_submission`　函式 71 行
- `_process_command_stub`　模組級狀態，需改注入
- `_sse_subscribers`　模組級狀態，需改注入

### 第 5 步 — B16 指令寫入流程（in_progress）

建議任務 ID：`BFF-SEAM-B16-CORRECTIVE-001`

需新抽 4 個符號（累計 19）：

- `_CAPITAL_BFF_IDEMPOTENCY`　模組級狀態，需改注入
- `_V5_INTERVENTIONS_STORE`　模組級狀態，需改注入
- `_process_command`　函式 141 行
- `_project_final_command_response`　函式 53 行

### 第 6 步 — B08 策略／資金／排名（todo）

建議任務 ID：`BFF-SEAM-B08-CORRECTIVE-001`

需新抽 6 個符號（累計 25）：

- `_STRATEGY_PERSONA_BFF_IDEMPOTENCY`　模組級狀態，需改注入
- `_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY`　模組級狀態，需改注入
- `_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY`　模組級狀態，需改注入
- `_pm12_allocation_line_digest`　函式 9 行
- `_pm12_recommendation_action_ids`　函式 41 行
- `_pm12_semantic_values_match`　函式 13 行

### 第 7 步 — B04 安全／錯誤／冪等（in_progress）

建議任務 ID：`BFF-SEAM-B04-CORRECTIVE-001`

需新抽 12 個符號（累計 37）：

- `_MGMT_AI_CONVERSATION_STORE`　模組級狀態，需改注入
- `_MGMT_NL_IDEMPOTENCY`　模組級狀態，需改注入
- `_PACK_D_D21_ERROR_BEHAVIOR`　模組級狀態，需改注入
- `_TWO_MAN_SIGNER_FIELDS`　模組級狀態，需改注入
- `_TWO_MAN_SIGNER_LIST_FIELDS`　模組級狀態，需改注入
- `_bff_error`　模組級狀態，需改注入
- `_build_foundation_command_context`　函式 83 行
- `_extract_identity`　模組級狀態，需改注入
- `_serialize_foundation_context`　函式 12 行
- `_stable_json_hash`　函式 8 行
- `_stored_command_params`　函式 72 行
- `_two_man_signers`　函式 17 行

### 第 8 步 — B18 跨切面整併（todo）

建議任務 ID：`BFF-SEAM-B18-CORRECTIVE-001`

需新抽 10 個符號（累計 47）：

- `_GOV_BFF_EXPERIMENT_OVERLAY`　模組級狀態，需改注入
- `_MCP_SERVER_REGISTRY`　模組級狀態，需改注入
- `_MCP_TOOL_REGISTRY`　模組級狀態，需改注入
- `_SKILL_REGISTRY`　模組級狀態，需改注入
- `_TOOL_REGISTRY`　模組級狀態，需改注入
- `_build_persona_readiness_items`　函式 95 行
- `_get_bff_incident`　函式 5 行
- `_list_bff_incidents`　函式 29 行
- `_list_persona_records`　函式 64 行
- `_mgmt_nl_collect_context`　函式 279 行

### 第 9 步 — B11 管理助理維運（todo）

建議任務 ID：`BFF-SEAM-B11-CORRECTIVE-001`

需新抽 13 個符號（累計 60）：

- `_ACKNOWLEDGED_ALERTS`　模組級狀態，需改注入
- `_ASSISTANT_CONTROL_MODE_STORE`　模組級狀態，需改注入
- `_MGMT_AI_AUDIT_EVENTS`　模組級狀態，需改注入
- `_MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG`　模組級狀態，需改注入
- `_MGMT_NL_COMMAND_IDEMPOTENCY_STORE`　模組級狀態，需改注入
- `_assistant_provider_list`　函式 27 行
- `_management_ai_conversation_store`　函式 5 行
- `_management_ai_provider_history_window`　函式 29 行
- `_management_ai_record_event`　函式 20 行
- `_mgmt_nl_finalize_provider_turn`　函式 94 行
- `_mgmt_nl_idempotency_storage_key`　函式 15 行
- `_mgmt_nl_provider_status`　函式 31 行
- `_ops_read_model_entry_for_persona`　函式 225 行

## 誤差與範圍說明

- 引用偵測只認 `main._X` 與 `from main import X` 兩種靜態形式；動態 `getattr` 抓不到，
  因此本清單是**下界**，實際 seam 只會更多不會更少。
- 已排除「薄轉接」：main.py 中僅單一 `return <call>(...)` 的 composition-root binding
  不計為待抽取，因其邏輯已在 owner 模組（例如 BFF-ASSISTANT-SOURCE-COLLECTOR-SEAM-CORRECTIVE-001
  留下的 `_assistant_collect_source`）。
- 另有少數被測試引用的符號在 main.py 無頂層定義（巢狀或類別成員），未納入。
- 批次授權檔案清單取自 live TaskStore 的 `artifacts` 欄位。
- 部分符號已落在現有任務範圍內（見下節），排程時需協調，避免範圍相撞。

## 已有任務涵蓋的範圍（排程時需協調，勿重複開）

| 範圍 | 既有任務 | 狀態 |
|---|---|---|
| management NL / MGMT_AI store | `BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001` | todo |
| evolution / governance journal | `BFF-EVOLUTION-REVIEW-JOURNAL-SEAM-CORRECTIVE-001` | todo |
| read surface owner wiring | `BFF-READ-OWNER-WIRING-CORRECTIVE-001` | todo |
| research jobs owner binding | `BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001` | todo |


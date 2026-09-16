# main.py 測試耦合殘量量測

量測基準：`origin/dev` 23934f89f（V2 writer_order 第 9 步 merge 後），main.py 20,356 行。

## 這份文件是什麼

**這是一個量測，不是派工提案。**

`docs/operations/bff-upstream-v2-20260911/` 是 operator 已批准的唯一交付契約，
其 `writer_order` 定義了 main.py 上的嚴格單一 writer 順序。本文件不提議任何任務，
也不應被用來繞過該順序另開工作；它只回答一個可重複量測的問題：

> 未完成的測試遷移批次，還有多少 main.py 符號是它們無法自行處理的？

## 量測定義

對每個未完成批次，取其 `artifacts` 宣告的測試檔，比對其中 `main._X` 與 `from main import X`
的靜態引用，對回 main.py 的頂層定義。排除「薄轉接」（主體僅單一 `return <call>(...)` 的
composition-root binding，其邏輯已在 owner 模組）。

這個數字為什麼重要：批次的授權只含測試檔、不含 main.py。只要符號還在 main.py，
batch worker 就無法自行搬動，只能停下等上游抽取。

## 核心發現：V2 縮小了 main.py，但沒有降低這個殘量

在 V2 各里程碑以同一方法實測：

| 里程碑 | main.py 行數 | 批次殘餘依賴符號 |
|---|---|---|
| 步 4 之前 | 21,487 | **60** |
| 步 4 admission 完成 | 20,996 | **60** |
| 步 7 journal 完成 | 20,746 | **60** |
| 步 8 assistant 完成 | 20,279 | **60** |
| 步 9 NL 完成（本文基準） | 20,356 | **60** |

**五個步驟、1,131 行縮減，殘量不動。**

為排除巧合，比對集合本身：60 個之中 **59 個完全相同**。
唯一變化是 `_MGMT_NL_IDEMPOTENCY` 消失、`_mgmt_nl_command_idempotency_store` 出現。

### 為什麼

V2 工作包依**產品關注點**切分（admission、journal、assistant collector、NL use case），
而非依**測試依賴**切分。兩者不重合：

- 步 4 `BFF-AUDIT-ADMISSION-PROJECTION-SEAM-CORRECTIVE-001`（收斂 command admission 旁路）已完成歸檔，
  但 `_process_command`、`_process_command_stub`、`_FINAL_CONTRACT_IDEMPOTENCY`、`_GOV_BFF_IDEMPOTENCY`、
  `_CAPITAL_BFF_IDEMPOTENCY`、`_AGORA_CORE_BFF_IDEMPOTENCY` 仍在 main.py。
- 步 9 `BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001` 確實新增 `assistant/management_service.py`、
  收掉 `_MGMT_NL_IDEMPOTENCY` 與三個 helper、重寫 5 個測試檔；但其範圍為 NL use case／durable replay，
  `_mgmt_nl_collect_context`（279 行）未在範圍內，一行未改。

這不是 V2 的缺陷 —— V2 正在完成它被批准要做的事。只是**它不以解開測試遷移批次為目標**。

## 對剩餘 7 步的意涵

步 14、16 為 FE，不動 main.py；真正仍會修改 main.py 的是 5 步，責任範圍為 Evolution 與 Research/Jobs。
以符號名對這些範圍做關鍵字比對，60 個之中僅 3 個對得上：
`_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY`、`_sse_buffers`、`_sse_subscribers`。

關鍵字比對過於粗略，不足以斷言其餘 57 個必為缺口。但上表的趨勢實測是獨立於該比對的硬證據：
連續五步、五個不同關注點、1,131 行縮減而殘量不動。**合理推論是 16 步走完後多數仍在。**

若該推論成立，9 個測試遷移批次在 V2 完成後仍會卡住，需要一批以「測試依賴」為切分依據的工作。
是否要加入 V2、或另立計畫，屬 operator 決策 —— 因為它會動到同一批檔案，受單一 active writer 約束。

## 目前殘量明細

| 批次 | 狀態 | 殘餘符號數 |
|---|---|---|
| B12 管理主控台讀模型 | todo | 2 |
| B10 演化計畫 | in_progress | 3 |
| B02 ASK／助理工作坊 | in_progress | 4 |
| B05 治理審批 | in_progress | 7 |
| B08 策略／資金／排名 | todo | 9 |
| B16 指令寫入流程 | in_progress | 9 |
| B04 安全／錯誤／冪等 | in_progress | 18 |
| B18 跨切面整併 | todo | 18 |
| B11 管理助理維運 | todo | 19 |

批次須其**全部**符號離開 main.py 才會解鎖，差一個都不行。

### 各批次殘餘符號

#### B12 管理主控台讀模型（todo）

- `_build_operator_alerts_payload`　函式 50 行
- `_pm12_performance_attribution_sources`　函式 86 行

#### B10 演化計畫（in_progress）

- `_COMMAND_AUTH_CONTEXT`　模組級狀態
- `_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY`　模組級狀態
- `_GOV_BFF_IDEMPOTENCY`　模組級狀態

#### B02 ASK／助理工作坊（in_progress）

- `_AGORA_CORE_BFF_IDEMPOTENCY`　模組級狀態
- `_ASSISTANT_SESSION_STORE`　模組級狀態
- `_ASSISTANT_TRANSCRIPT_STORE`　模組級狀態
- `_sse_buffers`　模組級狀態

#### B05 治理審批（in_progress）

- `_FINAL_CONTRACT_IDEMPOTENCY`　模組級狀態
- `_human_inbox_decision_projection_from_record`　函式 46 行
- `_human_inbox_decision_recommendation_id`　函式 58 行
- `_human_inbox_trusted_promotion_submission`　函式 71 行
- `_process_command_stub`　模組級狀態
- `_sse_buffers`　模組級狀態
- `_sse_subscribers`　模組級狀態

#### B08 策略／資金／排名（todo）

- `_CAPITAL_BFF_IDEMPOTENCY`　模組級狀態
- `_FINAL_CONTRACT_IDEMPOTENCY`　模組級狀態
- `_STRATEGY_PERSONA_BFF_IDEMPOTENCY`　模組級狀態
- `_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY`　模組級狀態
- `_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY`　模組級狀態
- `_pm12_allocation_line_digest`　函式 9 行
- `_pm12_recommendation_action_ids`　函式 41 行
- `_pm12_semantic_values_match`　函式 13 行
- `_process_command_stub`　模組級狀態

#### B16 指令寫入流程（in_progress）

- `_AGORA_CORE_BFF_IDEMPOTENCY`　模組級狀態
- `_CAPITAL_BFF_IDEMPOTENCY`　模組級狀態
- `_FINAL_CONTRACT_IDEMPOTENCY`　模組級狀態
- `_GOV_BFF_IDEMPOTENCY`　模組級狀態
- `_V5_INTERVENTIONS_STORE`　模組級狀態
- `_process_command`　函式 141 行
- `_process_command_stub`　模組級狀態
- `_project_final_command_response`　函式 53 行
- `_sse_buffers`　模組級狀態

#### B04 安全／錯誤／冪等（in_progress）

- `_CAPITAL_BFF_IDEMPOTENCY`　模組級狀態
- `_COMMAND_AUTH_CONTEXT`　模組級狀態
- `_FINAL_CONTRACT_IDEMPOTENCY`　模組級狀態
- `_MGMT_AI_CONVERSATION_STORE`　模組級狀態
- `_PACK_D_D21_ERROR_BEHAVIOR`　模組級狀態
- `_TWO_MAN_SIGNER_FIELDS`　模組級狀態
- `_TWO_MAN_SIGNER_LIST_FIELDS`　模組級狀態
- `_V5_INTERVENTIONS_STORE`　模組級狀態
- `_bff_error`　模組級狀態
- `_build_foundation_command_context`　函式 83 行
- `_extract_identity`　模組級狀態
- `_pm12_allocation_line_digest`　函式 9 行
- `_process_command_stub`　模組級狀態
- `_serialize_foundation_context`　函式 12 行
- `_sse_buffers`　模組級狀態
- `_stable_json_hash`　函式 8 行
- `_stored_command_params`　函式 72 行
- `_two_man_signers`　函式 17 行

#### B18 跨切面整併（todo）

- `_CAPITAL_BFF_IDEMPOTENCY`　模組級狀態
- `_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY`　模組級狀態
- `_GOV_BFF_EXPERIMENT_OVERLAY`　模組級狀態
- `_GOV_BFF_IDEMPOTENCY`　模組級狀態
- `_MCP_SERVER_REGISTRY`　模組級狀態
- `_MCP_TOOL_REGISTRY`　模組級狀態
- `_MGMT_AI_CONVERSATION_STORE`　模組級狀態
- `_SKILL_REGISTRY`　模組級狀態
- `_STRATEGY_PERSONA_BFF_IDEMPOTENCY`　模組級狀態
- `_TOOL_REGISTRY`　模組級狀態
- `_V5_INTERVENTIONS_STORE`　模組級狀態
- `_build_persona_readiness_items`　函式 95 行
- `_get_bff_incident`　函式 5 行
- `_list_bff_incidents`　函式 29 行
- `_list_persona_records`　函式 64 行
- `_mgmt_nl_collect_context`　函式 279 行
- `_mgmt_nl_command_idempotency_store`　函式 14 行
- `_sse_buffers`　模組級狀態

#### B11 管理助理維運（todo）

- `_ACKNOWLEDGED_ALERTS`　模組級狀態
- `_ASSISTANT_CONTROL_MODE_STORE`　模組級狀態
- `_MCP_TOOL_REGISTRY`　模組級狀態
- `_MGMT_AI_AUDIT_EVENTS`　模組級狀態
- `_MGMT_AI_CONVERSATION_STORE`　模組級狀態
- `_MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG`　模組級狀態
- `_MGMT_NL_COMMAND_IDEMPOTENCY_STORE`　模組級狀態
- `_V5_INTERVENTIONS_STORE`　模組級狀態
- `_assistant_provider_list`　函式 27 行
- `_management_ai_conversation_store`　函式 5 行
- `_management_ai_provider_history_window`　函式 29 行
- `_management_ai_record_event`　函式 20 行
- `_mgmt_nl_collect_context`　函式 279 行
- `_mgmt_nl_command_idempotency_store`　函式 14 行
- `_mgmt_nl_finalize_provider_turn`　函式 87 行
- `_mgmt_nl_idempotency_storage_key`　函式 15 行
- `_mgmt_nl_provider_status`　函式 31 行
- `_ops_read_model_entry_for_persona`　函式 225 行
- `_sse_buffers`　模組級狀態

## 誤差說明

- 引用偵測僅認 `main._X` 與 `from main import X` 兩種靜態形式，動態 `getattr` 不計，故為**下界**。
- 歷史里程碑量測使用當前 TaskStore 的批次 `artifacts` 清單，搭配該 commit 當時的測試檔內容。
- 「薄轉接」判定僅排除主體為單一 `return <call>(...)` 者；多語句的 composition-root binding 仍會計入。
- 剩餘步驟的涵蓋比對為符號名關鍵字比對，僅供參考，不作為結論依據。

## 重新量測

本文件的數字可用相同方法在任一 dev commit 重算。量測只需 TaskStore 的批次 `artifacts` 與該 commit 的原始碼，
不需執行測試。建議在每個 V2 步驟 merge 後重算，以驗證殘量是否開始下降。


# Pantheon 架構清理計畫 — 2026-09-16 重新基準版

取代 `docs/04/pantheon_architecture_cleanup_gap_2026-08-27/` 的執行假設。
原計畫的**處置判斷仍然有效**；失效的是它的現況前提。

| 項目 | 原計畫（2026-08-27 基準 f4a14b29） | 現況（2026-09-16 基準 08294e46a） |
|---|---|---|
| 計畫狀態 | ready for plan-freeze 與 materialization | **29 個任務從未被建立** |
| main.py | 493 個 `@app` 路由裝飾器 | **0 個**；36 個 `include_router` |
| main.py 行數 | （未記載） | 20,093 |
| read_store.py | 457 方法 God class | **檔案已不存在** |
| source_ingestion/main.py | 68 路由 + 27 model | **345 行；0 model；僅餘 1 個 `@app.get("/health")`** |
| Workshop router | 4,073 行 | **170 行** |
| 前端 `src/lib/bff` | legacy 實作與 barrel | **0 個檔案** |

## 為什麼需要重新基準

原計畫從未執行（live rows、terminal_facts、歸檔目錄中 `ACG-*` 與 `ARCH-CLEANUP-*` 皆為 0，
live store 提及 `PANTHEON-ARCH-CLEANUP` 0 次）。所有實作任務依賴
`ARCH-CLEANUP-PLAN-FREEZE-20260828`，而該 plan-freeze 任務本身也未建立，整條鏈從第 0 步未啟動。

但其目標在這三週間**被其他計畫大量達成** —— 主要是 V2 上游修復
（`bff-upstream-v2-20260911`，writer_order 12/16 完成）與測試遷移批次。
因此原計畫不是該啟動或作廢，而是**縮減後重新啟動**。

## 102 項處置的現況查核

| 優先 | 範圍 | 項數 | 查核結果 |
|---|---|---|---|
| P1 | BFF main.py 路由歸屬與重複路由 | 13 | **前提失效** — 路由已全數移出；殘留為 helper 與模組狀態 |
| P2 | read_store.py 457 方法 God class | 21 | ✅ **已達成** — 檔案不存在；殘留僅見於 smoke/test 檔 |
| P3 | execute-plans 依賴方向 | 16 | ✅ **已達成** — `src/lib/bff` 0 檔；11 個目標檔全數刪除 |
| P4 | Management loop truth | 10 | ⚠️ 已查 — 6 項達成；**ACG-04-004 未達成**（9/12 controller 未實作）；2 項靜態無法判定 |
| P5 | Runtime Manager 雙目錄 | 6 | ✅ 大致達成 — `services/execution/runtime-manager/` 已刪 |
| P6 | Agora Workshop | 12 | ⚠️ 部分 — router 4,073→170 已達成；跨模組私有引用仍在 |
| P7 | Source Ingestion main.py | 6 | ✅ **已達成** — 345 行、0 model；僅餘 health 端點，符合 ACG-07-001 KEEP |
| P8 | 前端 dead NL 與 stub | 9 | ✅ **已達成** — 5 個 REMOVE 目標全刪；1 個 KEEP 保留正確 |
| P9 | 容器 entrypoint 與部署測試 | 9 | ⚠️ 部分 — launcher 仍在；`FastBffReadStore`/`MinimalReadStore` 仍被引用 |

**約 52 項（P2 + P3 + P7 + P8）已由其他工作達成，應從執行範圍移除。**

### 仍成立的殘留

- **P6 ACG-06-002 / 003**：`_ws_publish` 仍被 `agora/interaction/runner.py`、
  `agora/performance/consumer.py`、`agora/research/routes/common.py` 跨模組引用私有符號。
- **P9 ACG-09-003 / 004**：`FastBffReadStore`（6 檔）與 `MinimalReadStore`（3 檔）仍被引用。
- **P9 ACG-09-002**：`scripts/run_agora_interaction_worker.py` 仍存在。
- **P5 ACG-05-004 / 005**：需確認測試與 Dockerfile 是否已跟上刪除。
- **P4 ACG-04-004**：十二個 loop 僅 3 個 `controller_contract.status = implemented`，
  9 個為 `not_implemented` 且無 `controller_name`，12 個契約全數仍含 `planned` 字樣。
  此項與 blocked 的 `S5-LOOPS-001`（Loop 4-12 未執行）為同一缺口。

## 新的 P1：main.py 不再是路由 monolith

原 P1 假設「493 個 `@app` 裝飾器散落在 main.py」。該前提已消失。
main.py 現在是 composition root（0 路由、36 個 `include_router`），
但仍有 **20,093 行**，其中 **575 個頂層函式佔 17,937 行（89%）**，加上 **289 個頂層模組狀態**。

原 `ACG-01-001` 的 gate 仍是正確判準：

> No domain route body, domain schema, mutable domain overlay, or domain SSE buffer remains.

對照現況：route body 已清空 ✅；但 mutable domain overlay（`_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY`、
`_GOV_BFF_EXPERIMENT_OVERLAY`）與 domain SSE buffer（`_sse_buffers`、`_sse_subscribers`）仍在 ❌。

### main.py 殘留內容依領域分布

| 領域 | 函式數 | 函式行數 | 模組狀態數 |
|---|---|---|---|
| management | 201 | 6674 | 49 |
| persona | 77 | 3835 | 25 |
| (未分類) | 141 | 3078 | 114 |
| command | 41 | 1534 | 17 |
| incident/ops | 32 | 722 | 14 |
| governance | 18 | 532 | 15 |
| assistant | 13 | 418 | 3 |
| loops | 11 | 373 | 7 |
| evolution | 9 | 281 | 6 |
| sse/event | 18 | 245 | 20 |
| agora | 9 | 143 | 10 |
| capital/strategy | 4 | 96 | 5 |
| research | 1 | 6 | 4 |

**management（201 函式／6,674 行／49 狀態）與 persona（77／3,835／25）合計佔函式行數的 59%。**
這兩者是新 P1 的主體。

### 最大的 12 個函式

- `_evaluate_persona_provisioning_status` — 571 行（persona）
- `_bff_management_nl_ask_impl` — 487 行（management）
- `_bff_management_nl_ask_stream_impl` — 383 行（management）
- `_mgmt_nl_attempt_provider_answer` — 325 行（management）
- `_mgmt_nl_collect_context` — 279 行（management）
- `_mgmt_nl_handle_control_command` — 275 行（management）
- `_pm12_performance_attribution_facts` — 265 行（management）
- `_pm12_resolve_quarterly_recommendation_submit_params` — 231 行（management）
- `_ops_read_model_entry_for_persona` — 225 行（persona）
- `_project_persona_fleet_item` — 202 行（persona）
- `_assistant_provider_usage_summary` — 201 行（assistant）
- `_sem_final_generic_list_for_path` — 168 行（(未分類)）

## 修訂後的執行範圍

### 移出（已達成，不需任務）

原 DAG 中下列準備 lane 的目標已由其他工作完成，應自執行範圍移除：

| 原任務 | 原責任 | 現況 |
|---|---|---|
| `ACG-RS-FOUNDATION-20260828` 等 7 條 RS lane | read_store 457 方法拆解 | read_store.py 已不存在 |
| `ACG-RS-FINAL-DELETE-20260828` | read_store.py 刪除 owner | 已無可刪對象 |
| `ACG-FE-TRANSPORT-WRITES-20260828` | bff-v1 transport 與 writes | `src/lib/bff` 已 0 檔 |
| `ACG-FE-LEGACY-DRAIN-20260828` | 53 個 legacy caller 遷移 | `bff-v1/legacy.ts` 已刪 |
| `ACG-FE-DEAD-NL-20260828` | 刪除 dead NL 與 fixed responder | 目標檔全數已刪 |
| `ACG-SOURCE-INGESTION-20260828` | Source Ingestion 組裝與五個 router | main.py 已 345 行；僅餘 health 端點 |
| `ACG-WORKSHOP-BE-20260828` 之 router 部分 | 4,073 行 router 拆解 | 已為 170 行 |

### 保留（仍成立）

| 任務 | 修訂後責任 |
|---|---|
| `ACG-WORKSHOP-BE-20260828` | 僅保留跨模組私有引用消除（`_ws_publish`、`_build_readiness_assessment`） |
| `ACG-ENTRYPOINT-WORKER-20260828` | launcher 路徑相依、`FastBffReadStore`／`MinimalReadStore` 分支移除 |
| `ACG-RUNTIME-MANAGER-20260828` | 僅保留測試與 Dockerfile 的跟進確認 |
| `ACG-LOOP-CONTRACTS-20260828` / `ACG-LOOP-PROJECTION-20260828` | P4 十項尚未查核，維持原責任待重新盤點 |
| `ACG-DEPLOY-EXACT-GATES-20260828` | P9 部署與 compose 相關項 |
| `ACG-INTEGRATION-E2E-20260828` | 跨系統閉環 |

### 重寫（前提已變）

`ACG-BFF-MAIN-CUTOVER-20260828`（原：sole `main.py` switch and deletion owner）的責任必須重寫。

原責任假設是「把 493 個路由切換到新 router 後刪除舊 main.py」。路由已經移完，
所以它現在的責任是**把 575 個函式與 289 個模組狀態遷出 composition root**。

這個工作量不適合單一任務。建議依領域切分為有序的 writer 鏈：

| 順位 | 範圍 | 規模 | 理由 |
|---:|---|---|---|
| 1 | management | 201 函式／6,674 行／49 狀態 | 最大宗；含最大的五個函式 |
| 2 | persona | 77／3,835／25 | 第二大；與 `personas/service.py` 的 82 個重複實作重疊 |
| 3 | command | 41／1,534／17 | 與 V2 U3 的 CommandAdapterService 收斂同向 |
| 4 | incident/ops | 32／722／14 | |
| 5 | governance | 18／532／15 | |
| 6 | sse/event | 18／245／20 | 狀態密度最高；`_sse_buffers` 擋住多個測試批次 |
| 7 | loops、assistant、evolution、agora、capital/strategy、research | 合計 47／1,317／35 | 可合併為一至二個任務 |
| 8 | （未分類）141／3,078／114 | 需先分類再決定歸屬；含通用工具 |

**所有子任務都改 main.py，必須序列執行（單一 active writer）。**

## 與現行計畫的關係

本重新基準版**不取代**下列正在執行的計畫，且必須排在其後或與其協調：

- `bff-upstream-v2-20260911`（writer_order 12/16，剩 13、15 兩步動 main.py）
- 測試遷移批次（B08／B10／B12／B16／B18 五個未完成）
- `BFF-AUDIT-ADMISSION-PERSONA-DUPLICATE-CORRECTIVE-001`（82 個 main.py↔personas 重複，
  與上表順位 2 直接重疊，應先完成）

## 建議的下一步

1. 依本文件重寫 `EXECUTION_TASK_CATALOG`，移除已達成的 lane，重寫 `ACG-BFF-MAIN-CUTOVER` 的責任。
2. 先完成 P4 的十項查核 —— 那是唯一完全未查證的優先。
3. plan-freeze 任務仍需要，但其審查對象是本重新基準版，不是 2026-08-28 的原目錄。

## 查核方法與誤差

- 檔案存在性以 `git ls-tree origin/dev` 為準（Pantheon `08294e46a`；execute-plans `b555e1a0`）。
- main.py 內容以 AST 解析頂層 `FunctionDef`／`AsyncFunctionDef`／`Assign`／`AnnAssign` 統計。
- 領域分類為符號名稱的正規式比對，屬粗分類；141 個未歸入領域者已於附錄 B 二階分類，
  其中 110 個判定為跨領域共用工具，31 個仍需人工判定。
- 102 項中僅 6 項的 artifact 可直接抽出 repo 路徑，其餘為敘述式，改以結構性事實查核
  （符號存在性、裝飾器計數、檔案行數），非逐字比對每一項的 gate。
- P4 十項已查核（附錄 A）；其中 3 項需執行時證據，靜態查核無法判定。
- 本文件數據於 2026-09-16 以獨立探測複驗一次（dev `46022edc9`／FE `afd49d56`）：
  main.py 的七項結構數據、read_store 不存在、workshop router 170 行、`src/lib/bff` 0 檔、
  P8 六個目標、controller status 3/9、計畫未建立四處證據，皆完全吻合。
  複驗修正一處：source_ingestion/main.py 並非 0 路由，仍有一個 `@app.get("/health")`，
  該端點屬 ACG-07-001 KEEP 的保留範圍，結論不變。
- 查核以符號存在性、裝飾器計數、AST 結構與檔案行數為據，非執行測試；
  「達成」意指計畫所述的結構性條件成立，不等同該行為已通過執行時驗收。

## 附錄 A：P4 Management loop truth 逐項查核

| 項 | 處置 | 結果 | 依據 |
|---|---|---|---|
| ACG-04-001 | KEEP | ✅ 達成 | registry 的 12 筆 loop 僅含契約欄位；`maturity`／`success` 只出現在 `catalog_decisions.rationale`，`heartbeat` 只作為 `liveness_metric` 的名稱，非現時真值 |
| ACG-04-002 | KEEP | — | fencing／lease／restart readback 需執行測試，靜態無法判定 |
| ACG-04-003 | MERGE | — | 未發現第二個 validator，但 projection 完備性需執行時驗證 |
| **ACG-04-004** | MIGRATE | ❌ **未達成** | 12 個 loop 中 3 個 `implemented`、9 個 `not_implemented`；9 個缺 `controller_name`；12/12 契約仍含 `planned` |
| ACG-04-005 | REMOVE | ✅ 達成 | 產品碼已無 loop_health 快照 fallback；僅存於 smoke/test，且測試斷言 `source == "controller_store"` |
| ACG-04-006 | REMOVE | ✅ 達成 | `publish_loop_12_controller_truth` 的呼叫點位於 `_probe_all`（owner 探測週期）而非 GET；`test_current_twelve_owner_truth.py` 斷言讀取側 `assert_not_called()` |
| ACG-04-007 | MIGRATE | — | payload 欄位完備性需執行時驗證 |
| ACG-04-008 | REMOVE | ✅ 達成 | `downstream_health_monitor.py` 未發現製造 Loops 1-11 列的程式碼 |
| ACG-04-009 | MIGRATE | ✅ 達成 | `/bff/v5/loop-health` 已移至 `control_loops/router.py` 並使用 `LoopHealthListEnvelope`；該套件內無 overlay 附加 |
| ACG-04-010 | REMOVE | ✅ 達成 | 前端 `current_maturity`／`target_maturity`／`operator_truth_source` 各 0 個檔案 |

**P4 結論**：10 項中 6 項達成、1 項明確未達成、3 項需執行時證據。
未達成的 `ACG-04-004` 不屬於本清理計畫可獨力解決的範圍 —— 它要求九個實際 controller 存在，
與 `S5-LOOPS-001` 是同一個產品缺口。

## 附錄 B：main.py 未分類函式的二階分類

第一階領域比對後有 141 個函式（3,078 行）與 114 個模組狀態未歸入任何領域。
以責任性質再分類：

| 二階分類 | 函式 | 行數 | 模組狀態 |
|---|---|---|---|
| 讀取面／資料集 | 35 | 614 | 14 |
| 時間／日期 | 26 | 593 | 1 |
| HTTP／錯誤 | 20 | 621 | 7 |
| 身分／權限 | 8 | 160 | 25 |
| 驗證／守衛 | 6 | 249 | 8 |
| 通用工具／序列化 | 4 | 50 | 0 |
| 型別轉換／正規化 | 4 | 17 | 1 |
| 設定／環境 | 4 | 51 | 5 |
| 聚合／計算 | 2 | 19 | 0 |
| 分頁／切片 | 1 | 5 | 1 |
| 仍未分類 | 31 | 699 | 52 |

前十類共 110 個函式（2,379 行）屬**跨領域共用工具**，其歸屬與領域無關：
應抽至共用模組，而非併入任一領域 owner。其中「身分／權限」有 25 個模組狀態，
密度最高，且與 `BFF-HTTP-AUTH-COMPOSITION-SEAM-CORRECTIVE-001` 已建立的
`auth/policy.py` owner 直接相關，應優先沿用既有 owner。

仍未分類的 31 個（699 行）需人工判定，最大者：

- `_lifecycle_projector_dependency` — 143 行
- `_filter_by_common_identifiers` — 110 行
- `_derive_drawer_execution_params` — 73 行
- `_confirm_token_lifecycle_payload` — 51 行
- `_openclaw_agent_reconcile_request` — 48 行

這 31 個應在領域遷移**之前**判定歸屬，否則會被任意併入最先動到它的領域任務。

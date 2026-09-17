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

## 新的 P1：修正版（2026-09-17）

> 本節取代 2026-09-16 初版中「依領域切分為 8 段有序 writer 鏈搬遷 575 個函式」的建議。
> 該建議在 2026-09-17 以下列證據撤回；初版內容保留於 git 歷史，不再作為執行依據。

### 撤回理由

| 初版假設 | 查核結果 |
|---|---|
| main.py 擋住測試遷移批次 | 否。B02／B04／B11 各改 0 行 main.py 即完成（PR #5848、#5755、#5750），採「掛真 router 注入依賴」手法 |
| main.py 充滿共享可變狀態 | 否。112 個模組級容器中僅 7 個在執行期被改寫，其餘為唯讀查表 |
| main.py 是機隊產能瓶頸 | 否。2026-09 僅 11% 任務契約含 main.py；其 commit 頻率由 23／日（08-30）降至 3／日 |
| 依領域切分可獨立執行 | 否。六個領域任務的產品消費端兩兩重疊（command 與每一領域重疊）；`personas/service.py`、`incidents/service.py`、`runtime/router.py` 各被 4 個任務同時需要 |

### ACG-01-001 gate 逐條實測（dev 732276cf8，main.py 18,702 行）

| 條款 | 實測 |
|---|---|
| domain route body | 0 ✅ |
| domain schema | 0 個模型（5 個頂層 class 皆為例外或傳輸類）✅ |
| mutable domain overlay | 見下表 |
| domain SSE buffer | `_sse_buffers`、`_sse_subscribers` 2 個 ❌ |

名稱含 OVERLAY／STORE／IDEMPOTENCY 的 18 個符號逐一分類（產品端改寫／讀取／賦值次數見 `data/main-mutated-containers-dev-732276cf8.json` 與附錄 D）：

| 分類 | 符號 | 判定依據 |
|---|---|---|
| 常數（誤抓） | `_SSE_RESYNC_ROUTES` | 路由表 `Dict[str, tuple[str, ...]]`，唯讀 |
| 已注入的持有者 | `_PERSONA_PROVISIONING_STORE`、`_MGMT_NL_COMMAND_IDEMPOTENCY_STORE`、`_MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG`、`_MGMT_AI_CONVERSATION_STORE` | `Optional[...] = None`，於 composition 賦值一次；此即注入模式本身 |
| 已交給 owner | `_FINAL_CONTRACT_IDEMPOTENCY`、`_AGORA_CORE_BFF_IDEMPOTENCY` | 產品端唯一讀取點為建構子關鍵字引數（L6654、L18630），寫入發生在 owner service 內 |
| **死碼** | `_GOV_BFF_EXPERIMENT_OVERLAY`、`_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY` | 產品端 0 改／0 讀／0 賦；僅 4 個測試檔戳它，全在 84 個 importer 內 |
| **死碼** | `_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY`、`_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY` | 產品與測試皆 0 |
| **死碼叢** | `_CAPITAL_BFF_IDEMPOTENCY`、`_capital_bff_action_command`、`_capital_bff_idempotency_store` | store 僅被 action command 呼叫；action command 全域零呼叫者 |
| **活著的 CommandStore 旁路（U3 殘留）** | `_GOV_BFF_IDEMPOTENCY`／`_gov_bff_action_command` | runtime/router.py L55 以字串名 `service.dependency('_gov_bff_action_command')` 取得；服務 rollback 家族；未經 CommandAdapterService |
| 同上 | `_STRATEGY_PERSONA_BFF_IDEMPOTENCY`／`_strategy_persona_action_command` | personas/routes/lifecycle.py 與 personas/service.py 呼叫 |
| 同上 | `_EVOL_EXP_BFF_IDEMPOTENCY`／`_evol_exp_bff_action_command` | main.py L18098 以位置參數 lambda 綁定為 `submit_job_action`；與 governance submit_action 導致 500 的缺陷同一類 |
| **產品疑問** | `_V5_INTERVENTIONS_STORE` | 產品端 3 處迭代（`_v5_intervention_records`、`_human_inbox_intervention_contributor`、`_human_inbox_surfaces`）但零寫入；真實 intervention 寫入在 `agora/performance/service.py:143` 的另一個 list。產品上此 list 恆為空。是已退役路徑或靜默失效，須由 human-inbox owner 裁定，本文件不斷言 |
| 活著、延後 | `_sse_buffers`、`_sse_subscribers` | 產品端 16／15 次讀取；測試端 130 次讀取，為耦合最高符號；須排在測試重分割之後 |

### 修正後的結論

真正符合「已批准契約要求且證據成立」的殘留工作只有一項：**三條活著的 in-memory idempotency 旁路收斂到 CommandStore，並刪除確認的死碼**。這是 V2 U3 工作包（「單一 confirmation 狀態機… Replay 綁定 tenant + actor + namespace + key」）未交付的殘留，與 `BFF-AUDIT-ADMISSION-GOVERNANCE-SUBMIT-CORRECTIVE-001` 同種，任務 ID `BFF-AUDIT-ADMISSION-IDEMPOTENCY-BYPASS-CORRECTIVE-001`。

其餘 17,000 餘行函式留在 composition root 並不違反任何已批准的 gate；在測試遷移與 gate 進 CI 完成前，沒有證據支持另立搬遷計畫。初版建議的 7 個 `ARCH-MAIN-*` 領域任務已撤銷。

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

`ACG-BFF-MAIN-CUTOVER-20260828` 的原責任（路由切換）已無對象；2026-09-16 初版將其改寫為 8 段領域 writer 鏈，2026-09-17 撤回（理由見「新的 P1：修正版」）。現行替代為單一 U3 殘留 corrective，範圍限於上表「活著的 CommandStore 旁路」與「死碼」兩類。

## 與現行計畫的關係

本重新基準版**不取代**下列正在執行的計畫，且必須排在其後或與其協調：

- `bff-upstream-v2-20260911`（writer_order 12/16，剩 13、15 兩步動 main.py）
- 測試遷移批次（B08／B10／B12／B16／B18 五個未完成）
- `BFF-AUDIT-ADMISSION-PERSONA-DUPLICATE-CORRECTIVE-001`（82 個 main.py↔personas 重複，
  與上表順位 2 直接重疊，應先完成）

## 建議的下一步

1. `BFF-TEST-MIGRATION-GATE-CORRECTIVE-001`：把架構 gate 從 JSON 自報數字改為活 AST 掃描並接進 branch-ci（現行沒有任何 workflow 執行 bff tests 目錄；見附錄 C）。
2. `BFF-TEST-MIGRATION-REPARTITION-PLAN-002`：以 gate 落地後的活掃描重新分割殘餘 84 個 importer。
3. `BFF-AUDIT-ADMISSION-IDEMPOTENCY-BYPASS-CORRECTIVE-001`：三條 CommandStore 旁路收斂與死碼刪除。
4. `_V5_INTERVENTIONS_STORE` 恆空問題提請 human-inbox owner 裁定。
5. SSE buffer 遷移於第 2 項完成後再評估。

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

## 附錄 C：測試耦合重新盤點（2026-09-17，dev 4d78e49fe）

母任務 `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` 遭獨立審查駁回（anchor `5c8380d9c`）後，以 AST 獨立重算並與審查證據對照。原始資料：`data/test-coupling-scan-dev-4d78e49fe.json`、`data/reviewer-anchor-5c8380d9c-lists.json`。

判定規則：`ast.Import` 名稱為 `main` 或以 `.main` 結尾；`ast.ImportFrom` 模組為 `main` 或以 `.main` 結尾；或 `from services.control_plane.bff import main`；排除 `services.research.main` 等非 BFF 的 main。第一版規則漏了絕對路徑 `from <pkg> import main` 形式（58 → 101），第二版多算了非 BFF main（91 → 84）。

| 指標 | 本盤點 | 審查者 |
|---|---|---|
| 非白名單 BFF main importer | 84 | 88 |
| 其中在 182 分割內 | 54 | 52 |
| 改 sys.path | 126 | 119 |

審查者四項結論全部成立。但其檔案清單已失真：52 個分割內 importer 只有 27 個仍成立，25 個檔案已不存在；另有 27 個分割內 importer 未列入，其中 23 個在審查基準時就已 import main。29 個被漏出分割的 PLANNED 檔中 24 個仍成立。分割外另有 13 個 importer 不在任何清單（4 個為 2026-05 舊檔、5 個為本週 V2 產生、4 個為 DEV-* 產生）。

`tests/test_bff_test_architecture.py` 的 `test_total_main_importers_is_bounded_and_strictly_decreased` 只比較 inventory 內兩個自報數字，不掃原始碼；且沒有任何 workflow 執行 `services/control-plane/bff/tests`。這是 18 個批次完成後數字仍上升的原因。

## 附錄 D：main.py 模組級狀態審計（2026-09-17，dev 732276cf8）

方法：AST 取頂層 `Assign`／`AnnAssign`；容器型判定為 `Dict`／`List`／`Set` 字面或 `dict()`／`defaultdict()`／`deque()` 等呼叫；改寫判定為 `.append/.extend/.insert/.pop/.clear/.update/.setdefault/.add/.discard/.remove` 呼叫或下標賦值／刪除；讀取判定為 `Load` 語境的 `Name`／`Attribute`。對全部 bff 檔分「產品」與「測試」統計。

限制：透過參數傳遞後在被呼叫端改寫的內層物件（例如 `_publish_event(_sse_buffers["audit"], ...)`）不會計入該名稱的改寫次數；`_sse_buffers` 的產品端「0 改寫」即屬此類，實際上是活的。



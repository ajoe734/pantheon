# Pantheon 全產品運作 GAP 處置矩陣 — 2026-08-30

| 欄位 | 內容 |
|---|---|
| 文件狀態 | **OP-G01 至 OP-G20 完整處置矩陣與歷史對齊** |
| 規劃基準 | 2026-08-29 全產品運作稽核報告 (`FULL_OPERATION_AUDIT_2026-08-29.md`)、最新 `origin/dev@e7f010dcc`、`execute-plans@bd03c863e`、Hosted BFF `dcb1423` 與 Hosted FE `c230fc7` |
| 處置標籤定義 | **ACTIVE_REMEDIATION**（主動程式修復）、**SOURCE_FIX_MERGED_PENDING_LIVE_VERIFY**（原始碼已合入，待部署與閉環驗證）、**HOSTED_EFFECT_PROOF**（Hosted 效應與讀回驗證）、**GOVERNANCE_GATE_FIX**（門禁與部署流程強化）、**TEST_TOOLING_FIX**（測試載具修復）、**CLEANUP_DELETION**（無用/死代碼刪除） |

---

## 1. 處置分類原則與歷史任務對齊

為確保全產品運作落地「不重工、不覆蓋歷史、單一真實來源」，處置依循以下嚴格邊界：

1. **不可變歷史原則**：
   - 過去已結案之 ACG 任務（如 `ACG-BE-GUARDS-20260828`、`ACG-RS-FINAL-DELETE-20260828` 等）與 PFG 任務（如 `PFG-HOSTED-CURRENT-DEV-CLOSEOUT-20260828`、`PFG-AGORA-PAPER-WRITE-PROOF-FIX-20260827` 等）之歷史紀錄保持不可變，不進行重新開啟（reopen）或任意覆蓋（supersede）。
2. **Current Dev 增量吸收**：
   - 在 2026-08-29 稽核報告產出後，倉庫已合入多項關鍵 PR：
     - PR #5410 (`b3b26a7`)：Deploy image ID 正規化與 bounded source refresh 入口。
     - PR #5411 (`254d2e7`)：Source frontier scope recovery。
     - PR #5412 (`f227360`)：Source controller explicit frontier recovery。
     - PR #5413 (`9e9ab33`)：Active symbol snapshot recovery 與 alias read view。
     - PR #5415 (`44895a2`)：Official snapshot min closes 與 2 個月歷史獲取。
     - PR #5416 / #5417 (`394eb05` / `3f33e87`)：Taiwan market session freshness 規則。
     - PR #5424 (`bfdc094`)：Agora bounded source refresh 讀回修復。
     - PR #5425 (`c743133`)：Agora projection receipt 綁定修復。
     - PR #5426 (`404edd2`)：Agora paper baseline 500 錯誤修復。
   - 上述 PR 雖已解決 source-level 邏輯，但在 live VM 上因先前部署門禁中斷（run 33280168821 觸發回滾，維持舊 pair `c230fc7/dcb1423`）而尚未完成 live atomic switch 與 runtime 閉環。因此，相關 GAP 處置明確標註為 **SOURCE_FIX_MERGED_PENDING_LIVE_VERIFY**，後續由專屬部署與驗收任務進行閉環，不再重複撰寫相同業務邏輯。
3. **單一任務責任歸屬**：
   - 每個 GAP 均被映射至唯一的目標設計單元（SD Unit）與執行任務（Execution Task），不允許同一個檔案或同一個邏輯由多個未協調的任務同時修改。

---

## 2. OP-G01 至 OP-G20 逐項處置矩陣

### OP-G01: Agora research 可產生假 `real` candidate truth
- **嚴重度**：`P0`
- **現狀直接證據**：
  - `docker-compose.yml` 中的 research orchestrator 與 gateway 將 production adapters 設為 `false`，service 預設為 `stub`。
  - BFF `DefaultAllowlistedAdapter` (`services/control-plane/bff/agora/research/dispatcher.py`) 在無真實後端回傳時，自行構造 artifact 與 evidence，並將欄位硬編碼為 `provenance="real"`。
- **歷史對齊**：過去任務建立假 real 標籤以繞過驗收，造成下游 candidate pool 污染。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-2` (Agora Provenance Truth & Producer Production Wiring)
- **歸屬執行任務**：`OPGAP-BE-AGORA-RESEARCH-20260830`
- **正確完成邊界**：
  - BFF `DefaultAllowlistedAdapter` 必須誠實標註 `provenance="simulated"` 或 `"unavailable"`。
  - 只有真正呼叫已開通、已通過 admission 門禁的 real adapter 且獲取後端有效 receipt 之結果，方得標註為 `real` 並進入 candidate pool。

---

### OP-G02: Agora PerformanceSuggestion 無 production wiring
- **嚴重度**：`P0`
- **現狀直接證據**：
  - `services/control-plane/bff/agora/performance/producer.py` 定義了 `PerformanceSuggestionProducer` 類別及其單元測試。
  - 但在整個 production 程式庫中，沒有任何 caller、背景 worker 或排程器實例化並呼叫此 producer。
- **歷史對齊**：測試通過但無 production wiring（即「測試不是功能完成判定」之典型案例）。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-2` (Agora Provenance Truth & Producer Production Wiring)
- **歸屬執行任務**：`OPGAP-BE-AGORA-RESEARCH-20260830`
- **正確完成邊界**：
  - 將 `PerformanceSuggestionProducer` 正式連接至 paper telemetry、trading room outcomes 及 risk feedback 事件消費鏈路。
  - 產生的 suggestion 需寫入持久化 store，提供 BFF API 讀回，並在前端 UI 重新載入時以同一 ID 正確呈現。

---

### OP-G03: current source FE/BFF 尚未成對部署 live VM
- **嚴重度**：`P0`
- **現狀直接證據**：
  - Live VM 於 2026-08-29T23:41Z 經直接 probe，維持在受補償保護的舊 baseline：FE `c230fc7` + BFF `dcb1423`（PostgreSQL checkpoint `7,649,369`）。
  - Post-cutoff promotion run `33280168821` 因 Agora projection 綁定驗證受阻而回滾，current source（Pantheon `e7f010dcc` + execute-plans `bd03c863e`）尚未成功 promotion 至 live 環境。
- **歷史對齊**：舊 PFG closeout 文件宣稱全部通過，但實際 live VM 未運行最新 candidate。
- **處置分類**：`HOSTED_PRECONDITION_AND_CLOSURE`
- **目標設計單元**：`SD-Unit-10` (Unified Dev VM Promotion & Atomic Candidate Switch)
- **歸屬執行任務**：`OPGAP-HOSTED-DEV-PROMOTION-20260830`
- **正確完成邊界**：
  - 透過強化後之 `deploy_nonprod_vm.sh` 執行 atomic promotion，使 live VM 之 `/deployment.json`、`/bff/version`、Docker 容器狀態及 PostgreSQL checkpoint 完整切換至 current candidate，且健康檢查全面 pass。

---

### OP-G04: release gate 可把實際失敗包成綠燈
- **嚴重度**：`P0`
- **現狀直接證據**：
  - GitHub Actions run `33256001457` 的 Management hosted log 因缺少 `BFF_AUTH_TOKEN` 失敗、route-load 失敗，但 workflow 整體仍顯示 success。
  - 舊 closeout 所綁定之 run `33146133499` skip 了 7 個 auth/write/exact-pair steps，卻產出全數 passed 之摘要。
- **歷史對齊**：CI 步驟將失敗與跳過誤標為警告或成功，造成虛假綠燈。
- **處置分類**：`GOVERNANCE_GATE_FIX`
- **目標設計單元**：`SD-Unit-9` (Deployment Lease Resilience & False-Green Elimination)
- **歸屬執行任務**：`OPGAP-DEPLOY-RELIABILITY-20260830`
- **正確完成邊界**：
  - 任何必要之 hosted、auth、write 或 readback step 若發生 fail 或 skip，必須使整體 functional acceptance 立即 fail-closed（exit code != 0）。
  - 證據產出需記錄逐一 journey ID、HTTP response、HAR 及 terminal state readback。

---

### OP-G05: auth readiness 仍同步依賴 OpenClaw 探針延遲
- **嚴重度**：`P1`
- **現狀直接證據**：
  - `services/control-plane/bff/main.py` 及 `auth.py` 中的 auth 驗證邏輯，在處理 request 時同步呼叫 `_safe_provider_readiness()` 進行 OpenClaw 遠端網路探測，阻塞受保護頁面之 render。
- **歷史對齊**：認證就緒度與遠端 LLM Provider 網路狀態耦合，造成 UI 載入超時。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-1` (BFF Core Routing Extraction & Async Decoupling)
- **歸屬執行任務**：`OPGAP-BE-BFF-CORE-20260830`
- **正確完成邊界**：
  - Auth 端點僅執行本地 session、tenant 與 role 檢驗；Provider readiness 改由獨立、非阻塞之背景快取或可降級端點非同步提供。

---

### OP-G06: Management 非 Persona generic CRUD 未接 durable owner
- **嚴重度**：`P0`
- **現狀直接證據**：
  - `execute-plans:src/management/components/write/createEntity.ts` 在非 strict 模式下對非 Persona 物件使用 `writeOverlay`；而在 strict-live 模式下直接被拒絕，無任何 durable mutation 落地。
- **歷史對齊**：前端以 local overlay 模擬 CRUD 成功，後端無實質資料庫持久化。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-7` (Frontend Management CRUD & Postmortem Binding)
- **歸屬執行任務**：`OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830`
- **正確完成邊界**：
  - 所有可見的 CRUD 操作按業務類型連接至 canonical BFF domain 端點並完成 readback；無後端支援的控制項必須自 production UI 移除或明確標示為 unavailable/disabled。

---

### OP-G07: Frontend production graph 仍可達 seed/mock/overlay
- **嚴重度**：`P1`
- **現狀直接證據**：
  - `execute-plans:src/lib/bff-v1/index.ts` 匯出 `writeOverlay`，且 `writeOverlay.ts` 直接 import `@/mocks/seed`。
  - `createEntity.ts` 及 `ObjectListPage.tsx` 可在 production 流程中動態引用上述 mock/seed 模組。
- **歷史對齊**：雖然部分頁面已切換至 canonical reads，但 production bundle 依賴圖仍未切斷對 mock/seed 的可達性。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-6` (Frontend Bundle Mock/Seed Isolation & Graph Gates)
- **歸屬執行任務**：`OPGAP-FE-BUNDLE-CLEANUP-20260830`
- **正確完成邊界**：
  - Production build 依賴圖完全排除 `@/mocks/seed` 與 `writeOverlay`；建立靜態分析門禁，保證 live bundle 不包含任何 mock 資料夾或 seed 模組引用。

---

### OP-G08: BFF composition cleanup 未完成
- **嚴重度**：`P1`
- **現狀直接證據**：
  - `services/control-plane/bff/main.py` 仍高達 68,054 行，包含 453 個 `@app.*` route decorators。
- **歷史對齊**：先前架構清理（ACG）已建立 router 基礎，但 main.py 巨型單體切換尚未最終收斂。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-1` (BFF Core Routing Extraction) 及整合任務
- **歸屬執行任務**：`OPGAP-BFF-MAIN-ASSEMBLY-20260830`（底層路由模組於 `OPGAP-BE-BFF-CORE-20260830` 平行抽取）
- **正確完成邊界**：
  - 所有 domain route bodies 完整搬移至各領域 router（`command_adapters/`、`agora/`、`management_read_models/` 等）；`main.py` 僅保留 app 初始化、middleware 掛載、生命週期管理與 router include；通過 architecture route guard 測試。

---

### OP-G09: Agora routers 跨域 import 私有 store/helper
- **嚴重度**：`P1`
- **現狀直接證據**：
  - Trading Room router 跨模組 import Workshop 的 `_build_readiness_assessment`。
  - Interaction / Decision / Research 等 router 跨模組引用彼此之私有 `_get_store` 函式。
- **歷史對齊**：模組間存在私有符號耦合，破壞領域邊界。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-2` (Agora Provenance Truth & Producer Production Wiring)
- **歸屬執行任務**：`OPGAP-BE-AGORA-RESEARCH-20260830`
- **正確完成邊界**：
  - 共享 store 與 service 由 composition root 統一注入；所有 router 禁止引用其他 router 之私有（以底線開頭）符號。

---

### OP-G10: generic legacy action adapter 仍是 dead compatibility code
- **嚴重度**：`P2`
- **現狀直接證據**：
  - `services/control-plane/bff/command_executor.py` 中的 `_execute_bff_action_adapter` 不在 production `_EXECUTORS` mapping 中，僅被舊測試及 monkeypatch 引用。
- **歷史對齊**：已由領域 adapter registry 取代之死代碼。
- **處置分類**：`CLEANUP_DELETION`
- **目標設計單元**：`SD-Unit-1` (BFF Core Routing Extraction & Async Decoupling)
- **歸屬執行任務**：`OPGAP-BE-BFF-CORE-20260830`
- **正確完成邊界**：
  - 證明 production 無 caller 後，徹底刪除 `_execute_bff_action_adapter` 及其專屬 legacy tests，防止假完成機制被誤接回。

---

### OP-G11: 十二循環完整 deployed proof 不是預設執行
- **嚴重度**：`P0`
- **現狀直接證據**：
  - Research、human-learning、runtime 及 cross-loop E2E 測試在目前 CI 中均設為環境變數 opt-in，常態測試會全部 skip。
- **歷史對齊**：十二循環未能在 exact candidate 部署後獲得端到端真實刺激與讀回驗證。
- **處置分類**：`HOSTED_EFFECT_PROOF`
- **目標設計單元**：`SD-Unit-11` (12-Loop & Authenticated Desktop Acceptance Harness)
- **歸屬執行任務**：`OPGAP-HOSTED-E2E-ACCEPTANCE-20260830`
- **正確完成邊界**：
  - 在 exact deployed candidate 上執行涵蓋 12 個循環之自動化驗收腳本，每個循環均驗證 natural stimulus、owner receipt、terminal state 及 UI 同 ID 讀回。

---

### OP-G12: current Source Management 仍缺 hosted effect proof
- **嚴重度**：`P1`
- **現狀直接證據**：
  - Source Ingestion 之 add-disabled、validate、bounded canary、reconcile-only 等操作在 source-level 具備測試，但在 exact hosted 環境中缺乏完整的「建立測試 instance -> 驗證 -> 觸發單次有界 canary -> 讀回 receipt -> 確認自動恢復 reconcile-only」之完整證據。
- **歷史對齊**：具備本機單元測試，但未完成 hosted 效應簽收。
- **處置分類**：`HOSTED_EFFECT_PROOF`
- **目標設計單元**：`SD-Unit-4` (Source Ingestion Bounded Refresh) 與 `SD-Unit-11` (Acceptance Harness)
- **歸屬執行任務**：`OPGAP-BE-SOURCE-MANAGEMENT-20260830`（由 `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` 執行 hosted 閉環驗收）
- **正確完成邊界**：
  - 使用測試 source instance 完整走完 add-disabled -> validate -> manual canary -> reload readback，並證明執行完畢後 controller mode 嚴格維持在 `reconcile_only`。

---

### OP-G13: synchronous FastAPI `TestClient` 驗收工具會死鎖
- **嚴重度**：`P1`
- **現狀直接證據**：
  - 現有 `.venv` 組合（FastAPI 0.139.2 / Starlette 1.3.1 / httpx 0.28.1）在執行部分同步 `TestClient` HTTP 測試時，會於 AnyIO portal 死鎖；而使用 `httpx.AsyncClient(ASGITransport)` 呼叫相同 ASGI app 則能正常回傳 200。
- **歷史對齊**：測試載具與依賴版本造成的工具層死鎖，非產品業務邏輯錯誤。
- **處置分類**：`TEST_TOOLING_FIX`
- **目標設計單元**：`SD-Unit-1` (BFF Core Routing Extraction & Async Decoupling)
- **歸屬執行任務**：`OPGAP-BE-BFF-CORE-20260830`
- **正確完成邊界**：
  - 鎖定相容版本依賴，將需要 ASGI 呼叫之整合測試改寫為 `httpx.AsyncClient(transport=ASGITransport(...))`，並加入硬性超時保護。

---

### OP-G14: current Management/Agora authenticated hosted UI 仍無有效證據
- **嚴重度**：`P1`
- **現狀直接證據**：
  - 現存 hosted UI 測試多為 anonymous auth-boundary 測試或單元 fixture 測試；缺乏在 exact candidate 上透過短效 dev-login token 執行 Management 與 Agora 主要路由矩陣之 DOM / Network / Readback 直接證據。
- **歷史對齊**：以公開端點可達性代替登入後真實資料與控制項驗收。
- **處置分類**：`HOSTED_EFFECT_PROOF`
- **目標設計單元**：`SD-Unit-11` (12-Loop & Authenticated Desktop Acceptance Harness)
- **歸屬執行任務**：`OPGAP-HOSTED-E2E-ACCEPTANCE-20260830`
- **正確完成邊界**：
  - 使用 Playwright 於 desktop 環境登入短效 dev-login session，遍歷 Management 與 Agora 核心頁面，記錄 console/network 零阻斷性錯誤及真實 ID 讀回。

---

### OP-G15: research adapters 與產品宣稱不一致
- **嚴重度**：`P1`
- **現狀直接證據**：
  - 多個 research backend 預設為 `stub` 或 `deferred_prep_only`，但在 UI 流程中仍向使用者暗示為 real research 或真實 candidate。
- **歷史對齊**：Adapter 能力與前端呈現脫節。
- **處置分類**：`CONTRACT_AND_UI_ALIGNMENT`
- **目標設計單元**：`SD-Unit-2` (Agora Provenance Truth) 與 `SD-Unit-8` (Frontend Agora Gating)
- **歸屬執行任務**：`OPGAP-FE-AGORA-WORKSHOP-20260830`（後端真值已由 `OPGAP-BE-AGORA-RESEARCH-20260830` 奠定）
- **正確完成邊界**：
  - 後端 API 與前端 UI 明確標註每個 adapter 之真實能力（`stub` / `deferred` / `real`）；非 real adapter 產出不得偽裝進入正式 candidate truth。

---

### OP-G16: deployment lease 與 rollback 共用同一個脆弱遠端依賴
- **嚴重度**：`P0`
- **現狀直接證據**：
  - `scripts/deploy/` 中的 environment lease 機制在 GitHub API timeout 時，同時導致長部署失敗與 rollback lease 取得失敗，使得補償腳本拋出 exit 78。
- **歷史對齊**：部署租約與補償回滾共用單一外部 GitHub API，缺乏本機封閉回滾授權與心跳寬限。
- **處置分類**：`GOVERNANCE_GATE_FIX`
- **目標設計單元**：`SD-Unit-9` (Deployment Lease Resilience & False-Green Elimination)
- **歸屬執行任務**：`OPGAP-DEPLOY-RELIABILITY-20260830`
- **正確完成邊界**：
  - Lease 心跳加入有界重試與寬限期（Grace period）；Rollback 機制具備本機 sealed authority，在遠端 API 暫時不可達時仍可獨立完成服務回滾至既有基準。

---

### OP-G17: Registry→Deployment→RuntimeBinding 的 executable projection 仍非自然產生
- **嚴重度**：`P0`
- **現狀直接證據**：
  - `verify_deploy_authorities()` 僅校驗 artifact 雜湊，未由 canonical Registry 帶出完整之 `object_store`、loader projection 及 `market_data_policy`；Deployment adapter 原樣轉發 caller 傳入之 metadata，造成下游 fleet 於 binding 階段拒絕缺少欄位之物件。
- **歷史對齊**：部署管線缺少自動由權威產生物理 loader 投影之機制。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-3` (Executable Runtime Binding & Paper Producer Lifecycle)
- **歸屬執行任務**：`OPGAP-BE-RUNTIME-BINDING-20260830`
- **正確完成邊界**：
  - Registry authority 根據同一 artifact 版本與 checksum 自動產生不可變之 loader projection 與 market policy；DeploymentPlan 僅持有合法引用，由 Runtime Manager 檢驗後建立 active binding。

---

### OP-G18: Management Postmortem 仍無 canonical read owner
- **嚴重度**：`P1`
- **現狀直接證據**：
  - 前端 `PostmortemLibrary.tsx` 從 Incident timeline 中的 `[postmortem]` 字串臨時組出 `pm_<incident>` 虛擬識別碼，缺乏後端專屬 Postmortem 權威服務之 List/Detail 契約與 durable ID。
- **歷史對齊**：Postmortem 資料由 incident 衍生解析，而非讀取 canonical 實體。
- **處置分類**：`ACTIVE_REMEDIATION`
- **目標設計單元**：`SD-Unit-5` (Canonical Management Postmortem) 與 `SD-Unit-7` (Frontend Postmortem Binding)
- **歸屬執行任務**：`OPGAP-BE-MGMT-POSTMORTEM-20260830`（前端 UI 串接由 `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` 負責）
- **正確完成邊界**：
  - 後端建立專屬 Postmortem read model 與 API 端點；前端 `PostmortemLibrary.tsx` 透過 canonical 端點讀取具備 `postmortem_id` 之正式記錄。

---

### OP-G19: Source-to-Agora Read Projection 綁定與身份同步在部署門禁失敗
- **嚴重度**：`P0`
- **現狀直接證據**：
  - 在 run `33280168821` 中，bounded manual Source refresh 產出 2330.TW 官方 receipt 後，部署門禁於 `scripts/deploy_nonprod_vm.sh:1218` 檢驗 Agora projection 時因缺少 `connectorId/ingestRunId/sourceId` 綁定而 exit 1。
  - 後續 PR #5424 (`bfdc094`)、PR #5425 (`c743133`)、PR #5426 (`404edd2`) 已合入 `origin/dev`，修復了 projection receipt 綁定、TW 讀回與 baseline 500 錯誤。
- **歷史對齊**：Source 邏輯已在 source-level 合併修復，但尚未在 live VM 重新跑過部署門禁與閉環驗證。
- **處置分類**：`SOURCE_FIX_MERGED_PENDING_LIVE_VERIFY`
- **目標設計單元**：`SD-Unit-2` (Agora Backend) 與 `SD-Unit-10` (Dev VM Promotion)
- **歸屬執行任務**：`OPGAP-HOSTED-DEV-PROMOTION-20260830`（本地 Agora 契約由 `OPGAP-BE-AGORA-RESEARCH-20260830` 驗證）
- **正確完成邊界**：
  - 於部署門禁中執行 bounded manual Source refresh，並驗證 `projection_path` 準確讀取並綁定新 receipt/run/source 記錄，門禁順利通過。

---

### OP-G20: paper-signal-producer 運行時健全度與完整訊號→訂單生命週期尚未在 live promotion 閉環
- **嚴重度**：`P0`
- **現狀直接證據**：
  - Image ID 正規化（`b3b26a7`）、frontier recovery（`f227360`）、snapshot alias（`9e9ab33`）、2-month history（`44895a2`）及 Taiwan market-session freshness（`394eb05`）均已在 source 完成。
  - 在 run `33280168821` 中 container 啟動及 session freshness 均已通過，但因整體部署在 Agora 檢查中斷回滾，使得 paper-signal-producer 及其 signal→order/fill/position/heartbeat 鏈路尚未在 live VM 完成 atomic switch 與真實運行驗收。
- **歷史對齊**：後端核心邏輯已修復並合入 dev，待 live promotion 實體閉環。
- **處置分類**：`SOURCE_FIX_MERGED_PENDING_LIVE_VERIFY`
- **目標設計單元**：`SD-Unit-3` (Paper Producer Lifecycle) 與 `SD-Unit-10` (Dev VM Promotion)
- **歸屬執行任務**：`OPGAP-HOSTED-DEV-PROMOTION-20260830`（本地 Runtime 綁定由 `OPGAP-BE-RUNTIME-BINDING-20260830` 驗證）
- **正確完成邊界**：
  - Live VM 部署後，`paper-signal-producer` container 維持 healthy，成功由 official snapshot 產生 signal 並驅動 paper order/fill/heartbeat 讀回。

---

## 3. 處置與執行任務映射匯總表

| 執行任務 ID | 倉庫 | 唯一主要負責處置之 GAP 項目 | 預估波次 | 執行資源 |
|---|---|---|:---:|---|
| `OPGAP-BE-BFF-CORE-20260830` | Pantheon | OP-G05, OP-G10, OP-G13 | Wave 1 | Local |
| `OPGAP-BE-AGORA-RESEARCH-20260830` | Pantheon | OP-G01, OP-G02, OP-G09 | Wave 1 | Local |
| `OPGAP-BE-RUNTIME-BINDING-20260830` | Pantheon | OP-G17 | Wave 1 | Local |
| `OPGAP-BE-SOURCE-MANAGEMENT-20260830` | Pantheon | OP-G12 | Wave 1 | Local |
| `OPGAP-BE-MGMT-POSTMORTEM-20260830` | Pantheon | OP-G18 | Wave 1 | Local |
| `OPGAP-FE-BUNDLE-CLEANUP-20260830` | execute-plans | OP-G07 | Wave 1 | Local |
| `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` | execute-plans | OP-G06 | Wave 1 | Local |
| `OPGAP-FE-AGORA-WORKSHOP-20260830` | execute-plans | OP-G15 | Wave 1 | Local |
| `OPGAP-DEPLOY-RELIABILITY-20260830` | Pantheon | OP-G04, OP-G16 | Wave 1 | Local |
| `OPGAP-BFF-MAIN-ASSEMBLY-20260830` | Pantheon | OP-G08 | Wave 2 | Local |
| `OPGAP-FE-INTEGRATION-ASSEMBLY-20260830` | execute-plans | (前端組件與客戶端集成匯總) | Wave 2 | Local |
| `OPGAP-HOSTED-DEV-PROMOTION-20260830` | Pantheon+FE | OP-G03, OP-G19, OP-G20 | Wave 3 | `pantheon-dev-vm` |
| `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` | Pantheon+FE | OP-G11, OP-G14 | Wave 4 | `pantheon-dev-vm` |

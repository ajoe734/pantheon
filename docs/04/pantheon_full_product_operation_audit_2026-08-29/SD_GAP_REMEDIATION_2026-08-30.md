# Pantheon 全產品運作系統設計規格 (SD) — 2026-08-30

| 欄位 | 內容 |
|---|---|
| 文件狀態 | **11 項設計單元 (SD Units) 之程式碼面、DTO 契約、狀態轉移與測試規格** |
| 規劃依據 | `docs/04/pantheon_full_product_operation_audit_2026-08-29/SA_GAP_REMEDIATION_2026-08-30.md`、`CURRENT_GAP_DISPOSITION_2026-08-30.md` |
| 涵蓋倉庫 | `ajoe734/pantheon` 與 `ajoe734/execute-plans` |

---

## 1. 設計單元總覽與責任矩陣

| 設計單元 ID | 單元名稱 | 涵蓋代碼面 | 負責處置之 GAP |
|---|---|---|---|
| **SD-Unit-1** | BFF 核心路由拆分與非同步解耦 | `services/control-plane/bff/` | OP-G05, OP-G08, OP-G10, OP-G13 |
| **SD-Unit-2** | Agora 真值校準與生產者生產連線 | `services/control-plane/bff/agora/` | OP-G01, OP-G02, OP-G09, OP-G15, OP-G19 |
| **SD-Unit-3** | 可執行 Runtime 綁定與 Paper 生命週期 | `services/runtime-manager/`, `services/execution/` | OP-G17, OP-G20 |
| **SD-Unit-4** | Source 有界更新與官方行情報護 | `services/source_ingestion/` | OP-G12, OP-G19 |
| **SD-Unit-5** | Management Postmortem 權威與循環真相 | `services/control-plane/bff/management_read_models/` | OP-G18 |
| **SD-Unit-6** | 前端 Production 打包 Mock 隔離與圖譜門禁 | `execute-plans:src/lib/bff-v1/` | OP-G07 |
| **SD-Unit-7** | 前端通用 CRUD 收斂與 Postmortem 綁定 | `execute-plans:src/management/` | OP-G06, OP-G18 |
| **SD-Unit-8** | 前端 Agora 能力顯式呈現與候選池連線 | `execute-plans:src/agora/` | OP-G01, OP-G02, OP-G15 |
| **SD-Unit-9** | 部署租約韌性強化與假綠燈消除 | `scripts/deploy/`, CI 工作流 | OP-G04, OP-G16 |
| **SD-Unit-10** | 統一 Dev VM 原子部署與候選切換 | `scripts/deploy_nonprod_vm.sh` | OP-G03, OP-G19, OP-G20 |
| **SD-Unit-11** | 十二循環與桌面端登入態驗收載具 | `scripts/e2e/`, Playwright 測試 | OP-G11, OP-G12, OP-G14 |

---

## 2. 逐項設計單元詳細設計規格

### SD-Unit-1: BFF 核心路由拆分與非同步解耦
- **檔案範圍**：
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/auth.py`
  - `services/control-plane/bff/command_executor.py`
  - `services/control-plane/bff/command_adapters/`
  - `services/control-plane/bff/ports/`
- **設計與變更規格**：
  1. **路由抽取 (OP-G08)**：將 `main.py` 中 453 個路由裝飾器依領域（Agora、Management、Lifecycle、Source、Command、Telemetry）抽取至專屬 router 模組。`main.py` 僅保留 FastAPI 應用實例化、CORS/Middleware 配置、Lifespan 事件處理與 `app.include_router(...)`。
  2. **Auth 與 Provider 解耦 (OP-G05)**：重構 `auth.py`，受保護端點之 session 檢驗僅進行本機 JWT 解析與角色驗證。移除 `_safe_provider_readiness()` 在 auth 鏈路上的同步網路調用，改由背景排程非同步更新 Provider 狀態快取。
  3. **刪除 Dead Action Adapter (OP-G10)**：徹底自 `command_executor.py` 刪除未註冊且無 caller 之 `_execute_bff_action_adapter` 函式及其相關之 legacy 單元測試。
  4. **非同步 ASGI 測試改寫 (OP-G13)**：將所有依賴 FastAPI HTTP 呼叫之整合測試由同步 `TestClient` 改寫為 `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`，並配置 10 秒硬性超時。
- **DTO / 契約**：
  ```python
  class AuthSessionResponse(BaseModel):
      authenticated: bool
      user_id: str
      tenant_id: str
      roles: list[str]
      provider_status: str = "cached_ready" # 非同步快取狀態
  ```
- **測試與驗證門禁**：
  - 執行 `pytest tests/control-plane/bff/test_route_composition.py`，斷言 `main.py` 行數 < 500 行，且 route collision = 0。
  - 執行 `pytest tests/control-plane/bff/test_auth_async.py`，斷言在 Provider 離線時，Auth 端點可在 5ms 內完成回應。

---

### SD-Unit-2: Agora 真值校準與生產者生產連線
- **檔案範圍**：
  - `services/control-plane/bff/agora/research/dispatcher.py`
  - `services/control-plane/bff/agora/performance/producer.py`
  - `services/control-plane/bff/agora/trading_room/router.py`
  - `services/control-plane/bff/agora/strategy_workshop/router.py`
- **設計與變更規格**：
  1. **真實性真值標籤 (OP-G01, OP-G15)**：修改 `dispatcher.py`，當 research adapter 為 `DefaultAllowlistedAdapter` 且未介接實體後端時，生成的策略構件標記為 `provenance="simulated"` 或 `"unavailable"`。候選池過濾器（Candidate Pool Filter）嚴格限制只有 `provenance="real"` 且具備有效 receipt 之構件得進入正式候選池。
  2. **建議生產者連線 (OP-G02)**：在 `agora/performance/producer.py` 實例化 `PerformanceSuggestionProducer`，並註冊為 Telemetry Ingest 及 Trading Room 決策事件的監聽者。每當接收到交易執行結果或風控警告時，自動生成 `PerformanceSuggestion` 並寫入 PostgreSQL store。
  3. **清除私有跨模組引用 (OP-G09)**：重構 `trading_room/router.py` 與 `strategy_workshop/router.py`，移除所有以底線開頭（如 `_build_readiness_assessment`、`_get_store`）的跨模組 import，將公共服務抽象為公開介面並由依賴注入容器提供。
  4. **Projection 綁定保證 (OP-G19)**：確保 Agora 讀取投影在接收到 Source Record 與 Ingest Run 時，正確將 `connectorId`、`ingestRunId` 與 `sourceId` 寫入投影記錄中。
- **DTO / 契約**：
  ```python
  class StrategyCandidate(BaseModel):
      strategy_id: str
      provenance: Literal["real", "simulated", "unavailable"]
      provenance_receipt_id: Optional[str]
      admitted_to_pool: bool
      validation_score: float

  class PerformanceSuggestion(BaseModel):
      suggestion_id: str
      strategy_id: str
      trigger_event_id: str
      suggestion_type: str
      confidence: float
      created_at: datetime
  ```
- **測試與驗證門禁**：
  - 執行 `pytest services/control-plane/bff/agora/tests/`，驗證無私有符號引用，且 simulated artifact 無法進入 real candidate pool。
  - 驗證 `PerformanceSuggestionProducer` 在接收模擬事件時能持久化生成記錄。

---

### SD-Unit-3: 可執行 Runtime 綁定與 Paper 生命週期
- **檔案範圍**：
  - `services/runtime-manager/deploy_authority.py`
  - `services/deployment/runtime_manager_dispatch_adapter.py`
  - `services/execution/lean_runtime/paper_signal_producer.py`
  - `services/execution/market_snapshot_admission.py`
- **設計與變更規格**：
  1. **權威物理投影生成 (OP-G17)**：在 `deploy_authority.py` 中，重構 `verify_deploy_authorities()`。除校驗 artifact checksum 外，必須由 canonical Registry 根據構件宣告，自動產生不可變之 `object_store` 定義、`loader_projection` 腳本路徑與 `market_data_policy`。`runtime_manager_dispatch_adapter.py` 僅能使用經校驗之權威投影構建 `RuntimeBinding`，拒絕來自請求端之任意自訂 metadata。
  2. **Paper 訊號生產者閉環 (OP-G20)**：在 `paper_signal_producer.py` 整合已合入之 Taiwan session freshness 門禁與 snapshot alias view。在接收到合規官方行情快照時，自然驅動虛擬訂單生成，經由 paper-broker 模擬撮合，發布 fill 與 position 事件至 telemetry outbox。
- **DTO / 契約**：
  ```python
  class ExecutableRuntimeBinding(BaseModel):
      binding_id: str
      deployment_plan_id: str
      artifact_id: str
      artifact_sha256: str
      object_store_uri: str
      loader_entrypoint: str
      market_data_policy: dict[str, Any]
      status: Literal["active", "draining", "terminated"]
  ```
- **測試與驗證門禁**：
  - 執行 `pytest services/runtime-manager/tests/` 與 `pytest services/execution/tests/`，驗證缺欄位之自訂 metadata 必被拒絕，且官方行情快照能正常驅動 paper 訊號。

---

### SD-Unit-4: Source 有界更新與官方行情報護
- **檔案範圍**：
  - `services/source_ingestion/main.py`
  - `services/source_ingestion/controller_worker.py`
  - `services/source_ingestion/connectors/taiwan_official.py`
  - `services/source_ingestion/market_data_storage.py`
- **設計與變更規格**：
  1. **常態 Reconcile-Only 強制執行 (OP-G12)**：在 `controller_worker.py` 初始化時，強制作業模式為 `reconcile_only`。背景排程只比對本地快照與已知 frontier，不對外發起 HTTP 爬取。
  2. **單次有界更新契約 (OP-G12)**：提供 `POST /api/v1/source/actions/manual-refresh` 端點。請求參數強制鎖定為：單一 symbol（例如 `2330.TW`）、max_ticks=1、max_records=100、超時 1800 秒。執行完畢或超時後，Controller 自動將狀態切換回 `reconcile_only`。
  3. **台灣交易時段與例假日新鮮度 (OP-G19, OP-G20)**：在 `taiwan_official.py` 中，依據 Taiwan Market Calendar 判斷。若目前時間為週末或法定例假日，且最新官方行情為前一交易日（週五）之正式收盤價，判定為合法新鮮數據（Valid & Fresh），允許進入快照投影。
- **DTO / 契約**：
  ```python
  class ManualRefreshRequest(BaseModel):
      symbol: str
      connector_id: str
      max_records: int = Field(default=100, le=100)
      timeout_seconds: int = Field(default=1800, le=1800)

  class ManualRefreshReceipt(BaseModel):
      receipt_id: str
      run_id: str
      symbol: str
      records_acquired: int
      observed_at: datetime
      restored_mode: Literal["reconcile_only"]
  ```
- **測試與驗證門禁**：
  - 執行 `pytest services/source_ingestion/tests/`，驗證手動 pull 完成後模式必回到 `reconcile_only`。

---

### SD-Unit-5: Management Postmortem 權威與循環真相
- **檔案範圍**：
  - `services/control-plane/bff/management_read_models/postmortem.py`
  - `services/control-plane/bff/management_read_models/loop_truth.py`
  - `services/control-plane/bff/routers/management_postmortem.py`
- **設計與變更規格**：
  1. **Canonical Postmortem 領域服務 (OP-G18)**：建立獨立的 `postmortem.py` 儲存與讀取模型。提供 `GET /bff/management/postmortems` 與 `GET /bff/management/postmortems/{id}` 端點。每一筆 Postmortem 均具備不可變之 `postmortem_id`、關聯 incident ID、檢討摘要、改善行動清單與審批狀態。
  2. **十二循環純淨投影**：`loop_truth.py` 嚴格僅從靜態定義（12 個循環基本資料）與 Controller 運行時即時事件進行 join，確保任何情況下均穩定輸出 12 筆且 ID 固定之循環狀態記錄。
- **DTO / 契約**：
  ```python
  class PostmortemRecord(BaseModel):
      postmortem_id: str
      incident_id: str
      title: str
      severity: str
      root_cause: str
      action_items: list[str]
      status: Literal["draft", "reviewed", "closed"]
      created_at: datetime
      updated_at: datetime
  ```
- **測試與驗證門禁**：
  - 執行 `pytest services/control-plane/bff/tests/test_management_postmortem.py`，驗證 postmortem CRUD 與 durable readback 一致。

---

### SD-Unit-6: 前端 Production 打包 Mock 隔離與圖譜門禁
- **檔案範圍**：
  - `execute-plans:src/lib/bff-v1/index.ts`
  - `execute-plans:src/lib/bff-v1/writeOverlay.ts`
  - `execute-plans:vite.config.ts`
  - `execute-plans:scripts/check_bundle_mock_reachability.ts`
- **設計與變更規格**：
  1. **移除 Mock 匯出 (OP-G07)**：自 `src/lib/bff-v1/index.ts` 徹底移除 `writeOverlay` 的 export。將 `writeOverlay.ts` 隔離至專屬測試/展示目錄，確保 production 程式碼路徑無法引用。
  2. **打包依賴圖譜門禁**：新增 Vite 插件與靜態掃描腳本 `check_bundle_mock_reachability.ts`。在前端 build 產出時，解析 Rollup module graph，若發現 production chunk 包含 `@/mocks` 或 `writeOverlay`，立即報錯並中止構建。
- **測試與驗證門禁**：
  - 執行 `npm run build` 與 `npm run test:depgraph`，斷言 production bundle 內包含之 mock 符號數量嚴格為 0。

---

### SD-Unit-7: 前端通用 CRUD 收斂與 Postmortem 綁定
- **檔案範圍**：
  - `execute-plans:src/management/components/write/createEntity.ts`
  - `execute-plans:src/management/pages/ObjectListPage.tsx`
  - `execute-plans:src/management/pages/phase2/PostmortemLibrary.tsx`
  - `execute-plans:src/lib/bff-v1/postmortemClient.ts`
- **設計與變更規格**：
  1. **淘汰前端偽造寫入 (OP-G06)**：重構 `createEntity.ts` 與 `ObjectListPage.tsx`。所有表單送出均對應至具體的 BFF typed command client（如 `personaClient.create`、`sourceClient.create`）。對於後端尚無實體支援的 generic entity，前端明確顯示 "Operation unavailable in production" 並禁用提交按鈕，嚴禁使用 `writeOverlay` 假寫入。
  2. **Postmortem 正式接線 (OP-G18)**：新增 `postmortemClient.ts` 串接 `/bff/management/postmortems`。修改 `PostmortemLibrary.tsx`，以 canonical `postmortem_id` 渲染清單與詳細頁面，徹底廢除字串解析 `pm_<incident>` 的臨時機制。
- **測試與驗證門禁**：
  - 執行 `npm run test:unit src/management/`，斷言所有 CRUD 行為均呼叫真實 client，且 Postmortem 頁面正確解析後端資料模型。

---

### SD-Unit-8: 前端 Agora 能力顯式呈現與候選池連線
- **檔案範圍**：
  - `execute-plans:src/agora/pages/strategy-workshop/WorkshopSessionView.tsx`
  - `execute-plans:src/agora/pages/trading-room/TradingRoomWorkspace.tsx`
  - `execute-plans:src/agora/pages/trading-room/AttributionReportView.tsx`
- **設計與變更規格**：
  1. **顯式呈現 Adapter 真實性 (OP-G01, OP-G15)**：在 Workshop 策略重構與研發面板中，為每個 adapter 與產出卡片增加 Badge 標籤，清楚標示 `Real Backend`、`Simulation` 或 `Unavailable`，不誤導操作者。
  2. **動態候選池加載**：Trading Room 介面根據後端回傳之 `candidatePoolId` 動態加載候選策略清單，廢除前端固定寫死之 `lens-A..E`。
  3. **績效建議面板展示 (OP-G02)**：在 `AttributionReportView.tsx` 中掛載 `PerformanceSuggestionWidget`，動態拉取後端由事件產出之績效建議，並提供「套用建議」之互動操作。
- **測試與驗證門禁**：
  - 執行 `npm run test:unit src/agora/`，確保在各種 adapter 狀態下 UI 均能誠實且正確地渲染。

---

### SD-Unit-9: 部署租約韌性強化與假綠燈消除
- **檔案範圍**：
  - `scripts/deploy/environment_lease.py`
  - `scripts/deploy_nonprod_vm.sh`
  - `.github/workflows/nonprod-deploy.yml`
- **設計與變更規格**：
  1. **租約心跳與本地回滾授權 (OP-G16)**：在 `environment_lease.py` 中引入心跳重試機制（3 次指數退避，最長寬限 60 秒）。若在租約執行期間 GitHub API 暫時 timeout，腳本進入 local grace mode 而不立即強制終止部署。同時，將回滾時所需之 baseline manifest 本地封裝，允許在遠端連線異常時本機獨立完成 rollback。
  2. **消除虛假綠燈 (OP-G04)**：審查 `nonprod-deploy.yml` 與 `deploy_nonprod_vm.sh`。對於任何關鍵步驟（如 auth token 檢驗、exact-pair 探針、readback 檢驗），若執行失敗或被 skipped，工作流必須以 `set -e` 嚴格終止，嚴禁使用 `continue-on-error: true` 或在 step summary 中將其降級標註為 passed。
- **測試與驗證門禁**：
  - 執行 `pytest scripts/deploy/tests/`，模擬 GitHub API 瞬斷情境，驗證心跳寬限與本地回滾授權有效運作。

---

### SD-Unit-10: 統一 Dev VM 原子部署與候選切換
- **檔案範圍**：
  - `scripts/deploy_nonprod_vm.sh`
  - `docker-compose.yml`
- **設計與變更規格**：
  1. **全容器健康與依賴收斂 (OP-G03, OP-G20)**：在 `deploy_nonprod_vm.sh` 執行 compose 部署後，對所有核心服務（BFF、Runtime Manager、Governance、Deployment、Lifecycle Projector、Paper Signal Producer、Agora Interaction Worker）進行逐一健康探測，要求狀態全數為 `healthy`。
  2. **Agora Projection 驗證適配 (OP-G19)**：在執行 bounded manual source refresh 驗證時，讀取並校驗 projection 檔案，斷言其包含最新產出之 `connectorId` 與 `ingestRunId`。
  3. **原子切換與 Manifest 同步**：在所有服務探針與 projection 驗證通過後，始更新 `/deployment.json` 並切換前端靜態資產軟連結，確保 FE 與 BFF 永遠成對發布。
- **測試與驗證門禁**：
  - 於本機以乾淨 Docker 環境執行部署腳本演練，驗證 gate-before-switch 邏輯正確阻斷異常並順利完成原子發布。

---

### SD-Unit-11: 十二循環與桌面端登入態驗收載具
- **檔案範圍**：
  - `scripts/e2e/twelve_loop_acceptance_suite.py`
  - `execute-plans:tests/e2e/desktop_authenticated_journey.spec.ts`
  - `scripts/e2e/verify_source_reconcile_only_cycle.py`
- **設計與變更規格**：
  1. **十二循環端到端自動化 (OP-G11)**：編寫 `twelve_loop_acceptance_suite.py`，依序對 Loop 1 至 Loop 12 注入自然刺激（Stimulus），驗證每個循環均產生對應的 Receipt、Terminal State 及 Management 讀回 ID。
  2. **Source 完整週期驗證 (OP-G12)**：編寫 `verify_source_reconcile_only_cycle.py`，建立測試 Data Source -> 驗證 -> 觸發單次有界 Refresh -> 讀取快照 -> 證明 Controller 自動回到 `reconcile_only`。
  3. **Playwright 桌面端登入態矩陣 (OP-G14)**：編寫 `desktop_authenticated_journey.spec.ts`，使用短效 dev-login token 登入，遍歷 Management（Cockpit、Loops、Fleet、Sources、Postmortems）與 Agora（Workshop、Trading Room、Attribution）所有核心路由，捕捉 Network HAR 與 DOM 快照，斷言 0 個未處理之 console error。
- **測試與驗證門禁**：
  - 驗收腳本輸出結構化 JSON 證據，包含逐一 Journey ID、HTTP 狀態碼、讀回 ID 與執行時間戳。

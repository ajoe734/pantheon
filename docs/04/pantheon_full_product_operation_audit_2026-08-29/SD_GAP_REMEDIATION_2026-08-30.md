# Pantheon 全產品運作系統設計規格 (SD) — 2026-08-30

| 欄位 | 內容 |
|---|---|
| 文件狀態 | **14 項設計單元 (SD Units) 之程式碼面、DTO 契約、狀態轉移與測試規格** |
| 規劃依據 | `docs/04/pantheon_full_product_operation_audit_2026-08-29/SA_GAP_REMEDIATION_2026-08-30.md`、`CURRENT_GAP_DISPOSITION_2026-08-30.md` |
| 涵蓋倉庫 | `ajoe734/pantheon` 與 `ajoe734/execute-plans` |

---

## 1. 設計單元總覽與責任矩陣

| 設計單元 ID | 單元名稱 | 涵蓋代碼檔案範圍 | 負責處置之 GAP | 負責任務 ID |
|---|---|---|---|---|
| **SD-Unit-1** | BFF 核心非同步解耦與 ASGI 測試載具 | `services/control-plane/bff/auth.py`, `provider_readiness_cache.py`, `tests/test_auth_async.py`, `tests/async_asgi.py` | OP-G05, OP-G13 | `OPGAP-BE-BFF-CORE-20260830` |
| **SD-Unit-2** | 中央相容命令面退役與領域適配器收斂 | `command_executor.py`, `downstream_health_monitor.py`, `command_adapters/base.py`, `registry.py`, `router.py`, `internal_api*.py`, `internal_api_routes.py`, `docker-compose.control.yml` | OP-G10 (含 OP-G24) | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| **SD-Unit-3** | Agora 真值校準、建議生產者連線與路由解耦 | `agora/research/dispatcher.py`, `agora/performance/producer.py`, `agora/strategy_workshop/store.py`, `agora/strategy_workshop/router.py`, `agora/trading_room/router.py` | OP-G01, OP-G02, OP-G09 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| **SD-Unit-4** | 可執行 Runtime 綁定權威投影與 Paper 生命週期 | `runtime-manager/deploy_authority.py`, `deployment/runtime_manager_dispatch_adapter.py`, `runtime_manager/runtime_binding.py`, `execution/lean_runtime/paper_signal_producer.py`, `execution/market_snapshot_admission.py` | OP-G17 | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| **SD-Unit-5** | Source 常態 Reconcile-Only 與台灣時段新鮮度 | `source_ingestion/main.py`, `runtime.py`, `controller_worker.py`, `connectors/taiwan_official.py`, `market_data_storage.py`, `test_taiwan_calendar_freshness.py` | OP-G12 | `OPGAP-BE-SOURCE-MANAGEMENT-20260830` |
| **SD-Unit-6** | Management Postmortem 串接與十二循環純淨投影 | `management_read_models/router.py`, `management_read_models/models.py`, `management_read_models/loop_truth.py`, `domain_ports/lifecycle_telemetry_governance.py`, `services/postmortems/test_main_routes.py` | OP-G18 | `OPGAP-BE-MGMT-POSTMORTEM-20260830` |
| **SD-Unit-7** | 前端 Production 打包 Mock 隔離與圖譜門禁 | `execute-plans:src/lib/bff/*`, `src/lib/bff-v1/writeFallback.ts`, `seed.ts`, `managementNl.ts`, `vite.config.ts`, `scripts/check_bundle_mock_reachability.ts` | OP-G07 | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| **SD-Unit-8** | 前端通用 CRUD 收斂與 Postmortem 權威綁定 | `execute-plans:src/management/components/write/createEntity.ts`, `ObjectListPage.tsx`, `PersonaOnboarding.tsx`, `PostmortemLibrary.tsx`, `postmortemClient.ts`, `types.ts` | OP-G06 | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` |
| **SD-Unit-9** | 前端 Agora 能力顯式標籤與候選池動態加載 | `execute-plans:src/agora/pages/strategy-workshop/WorkshopSessionView.tsx`, `TradingRoomWorkspace.tsx`, `AttributionReportView.tsx`, `StrategyPerformancePage.tsx` | OP-G15 | `OPGAP-FE-AGORA-WORKSHOP-20260830` |
| **SD-Unit-10** | 部署租約心跳寬限、本地封閉回滾授權與假綠燈消除 | `scripts/deploy/environment_lease.py`, `scripts/deploy_nonprod_vm.sh`, `.github/workflows/nonprod-deploy.yml`, `test_environment_lease.py` | OP-G04, OP-G16 (含 OP-G25) | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| **SD-Unit-11** | BFF 組裝入口收斂、多副本載入隔離與重複 Operation ID 清零 | `services/control-plane/bff/main.py`, `read_store.py`, `test_normalized_route_uniqueness.py`, `tests/bff/test_route_composition.py`, `test_multi_replica_loading.py` | OP-G08 (含 OP-G21) | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| **SD-Unit-12** | 前端應用殼層、路由掛載與模組包整合匯總 | `execute-plans:src/App.tsx`, `ManagementLayout.tsx`, `src/lib/bff-v1/index.ts`, `tests/e2e/desktop_authenticated_journey.spec.ts`, `helpers/auth.ts`, `helpers/bff.ts` | (整合組裝) | `OPGAP-FE-INTEGRATION-ASSEMBLY-20260830` |
| **SD-Unit-13** | 統一 Dev VM 原子部署與容器健康驗證 | `docker-compose.yml`, `docs/deployment/evidence/full-operation-gap/OPGAP-HOSTED-DEV-PROMOTION-20260830/evidence.json` | OP-G03, OP-G19, OP-G20 | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| **SD-Unit-14** | 十二循環全量刺激讀回與桌面端登入態驗收 | `scripts/e2e/twelve_loop_acceptance_suite.py`, `verify_source_reconcile_only_cycle.py`, `docs/deployment/evidence/full-operation-gap/OPGAP-HOSTED-E2E-ACCEPTANCE-20260830/evidence.json` | OP-G11, OP-G14 | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |

---

## 2. 逐項設計單元詳細設計規格

### SD-Unit-1: BFF 核心非同步解耦與 ASGI 測試載具
- **負責任務**：`OPGAP-BE-BFF-CORE-20260830`
- **精確檔案範圍**：
  - `services/control-plane/bff/auth.py`
  - `services/control-plane/bff/provider_readiness_cache.py`
  - `services/control-plane/bff/tests/test_auth_async.py`
  - `services/control-plane/bff/tests/async_asgi.py`
- **設計與變更規格**：
  1. **Auth 與 Provider 解耦 (OP-G05)**：重構 `auth.py`，受保護端點之 session 檢驗僅進行本機 JWT 解析與角色驗證。移除 `_safe_provider_readiness()` 在 auth 鏈路上的同步網路調用，改由 `provider_readiness_cache.py` 在背景非同步快取 Provider 狀態。
  2. **非同步 ASGI 測試改寫 (OP-G13)**：將所有依賴 FastAPI HTTP 呼叫之整合測試由同步 `TestClient` 改寫為 `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`，並配置 10 秒硬性超時，徹底根除 AnyIO portal 死鎖。
- **DTO / 契約**：
  ```python
  class AuthSessionResponse(BaseModel):
      authenticated: bool
      user_id: str
      tenant_id: str
      roles: list[str]
      provider_status: str = "cached_ready"
  ```
- **測試與驗證門禁**：
  - 執行 `pytest services/control-plane/bff/tests/test_auth_async.py`，斷言在 Provider 離線或延遲時，Auth 端點在 5ms 內完成回應。

---

### SD-Unit-2: 中央相容命令面退役與領域適配器收斂
- **負責任務**：`OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`
- **精確檔案範圍**：
  - `services/control-plane/bff/command_executor.py`
  - `services/control-plane/bff/downstream_health_monitor.py`
  - `services/control-plane/bff/command_adapters/base.py`
  - `services/control-plane/bff/command_adapters/registry.py`
  - `services/control-plane/bff/command_adapters/router.py`
  - `services/control-plane/internal/internal_api.py`
  - `services/control-plane/internal/internal_api_min.py`
  - `services/control-plane/internal/test_internal_api_incident.py`
  - `services/runtime-manager/internal_api_routes.py`
  - `services/runtime-manager/main.py`
  - `services/runtime-manager/test_internal_api_routes.py`
  - `docker-compose.control.yml`
  - `services/control-plane/bff/test_command_executor.py`
- **設計與變更規格**：
  1. **刪除 Dead Action Adapter (OP-G10)**：徹底自 `command_executor.py` 刪除未註冊且無 caller 之 `_execute_bff_action_adapter` 函式及其相關之 legacy 單元測試。
  2. **中央相容命令面退役 (OP-G24 歸併至 OP-G10)**：
     - 廢除 BFF 透過 `PANTHEON_INTERNAL_API_URL` 連接 runtime-manager 動態掛載之 1,640 行 `internal_api.py` 模式。
     - 各領域命令適配器（Deployment、Governance、Runtime、Persona、Capital）直連領域微服務。
     - 刪除 `services/control-plane/internal/internal_api.py`、`internal_api_min.py`、`services/runtime-manager/internal_api_routes.py` 及相關相容掛載與 compose 配置。
- **測試與驗證門禁**：
  - 執行命令適配器契約測試，確認各領域命令均直接向對應領域 service 發送，且 `/api/internal/v1/*` 引用數嚴格為 0。

---

### SD-Unit-3: Agora 真值校準、建議生產者連線與路由解耦
- **負責任務**：`OPGAP-BE-AGORA-RESEARCH-20260830`
- **精確檔案範圍**：
  - `services/control-plane/bff/agora/research/dispatcher.py`
  - `services/control-plane/bff/agora/performance/producer.py`
  - `services/control-plane/bff/agora/strategy_workshop/store.py`
  - `services/control-plane/bff/agora/strategy_workshop/router.py`
  - `services/control-plane/bff/agora/trading_room/router.py`
- **設計與變更規格**：
  1. **真實性真值標籤 (OP-G01)**：修改 `dispatcher.py`，當 research adapter 為 `DefaultAllowlistedAdapter` 且未介接實體後端時，生成的策略構件標記為 `provenance="simulated"` 或 `"unavailable"`。候選池過濾器嚴格限制只有 `provenance="real"` 且具備有效 receipt 之構件得進入正式候選池。
  2. **建議生產者連線 (OP-G02)**：在 `agora/performance/producer.py` 實例化 `PerformanceSuggestionProducer`，並註冊為 Telemetry Ingest 及 Trading Room 決策事件的監聽者。每當接收到交易執行結果或風控警告時，自動生成 `PerformanceSuggestion` 並寫入 PostgreSQL store。
  3. **清除私有跨模組引用 (OP-G09)**：重構 `trading_room/router.py` 與 `strategy_workshop/router.py`，移除所有以底線開頭的跨模組 import，將公共服務抽象為公開介面並由依賴注入容器提供；合併 `PostgresStrategyWorkshopStore` 至主 store。
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

---

### SD-Unit-4: 可執行 Runtime 綁定權威投影與 Paper 生命週期
- **負責任務**：`OPGAP-BE-RUNTIME-BINDING-20260830`
- **精確檔案範圍**：
  - `services/runtime-manager/deploy_authority.py`
  - `services/deployment/runtime_manager_dispatch_adapter.py`
  - `services/runtime_manager/runtime_binding.py`
  - `services/execution/lean_runtime/paper_signal_producer.py`
  - `services/execution/market_snapshot_admission.py`
- **設計與變更規格**：
  1. **權威物理投影生成 (OP-G17)**：在 `deploy_authority.py` 中，重構 `verify_deploy_authorities()`。除校驗 artifact checksum 外，必須由 canonical Registry 根據構件宣告，自動產生不可變之 `object_store` 定義、`loader_projection` 腳本路徑與 `market_data_policy`。`runtime_manager_dispatch_adapter.py` 僅能使用經校驗之權威投影構建 `RuntimeBinding`，拒絕來自請求端之任意自訂 metadata。
  2. **Paper 訊號生產者閉環 (OP-G20 基礎)**：在 `paper_signal_producer.py` 整合已合入之 Taiwan session freshness 門禁與 snapshot alias view。在接收到合規官方行情快照時，自然驅動虛擬訂單生成，經由 paper-broker 模擬撮合，發布 fill 與 position 事件至 telemetry outbox。
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

### SD-Unit-5: Source 常態 Reconcile-Only 與台灣時段新鮮度
- **負責任務**：`OPGAP-BE-SOURCE-MANAGEMENT-20260830`
- **精確檔案範圍**：
  - `services/source_ingestion/main.py`
  - `services/source_ingestion/runtime.py`
  - `services/source_ingestion/controller_worker.py`
  - `services/source_ingestion/connectors/taiwan_official.py`
  - `services/source_ingestion/market_data_storage.py`
  - `services/source_ingestion/test_taiwan_calendar_freshness.py`
- **設計與變更規格**：
  1. **常態 Reconcile-Only 強制執行 (OP-G12)**：在 `controller_worker.py` 初始化時，強制作業模式為 `reconcile_only`，外部網路 egress 預設 deny。背景排程只比對本地快照與已知 frontier，不對外發起 HTTP 爬取。
  2. **沿用既有 One-Shot Profile (OP-G12)**：不新增任何 `/manual-refresh` 第二路由。唯一允許之數據拉取途徑為現有之 `source-ingest-scheduler` one-shot compose/deploy profile，其具備有界參數（max 1 tick, max 100 records）且執行完畢即終止。
  3. **清理相容別名**：徹底清理 `services/source_ingestion/main.py` 中的 legacy module aliases。
  4. **台灣交易時段與例假日新鮮度**：在 `taiwan_official.py` 中，依據 Taiwan Market Calendar 判斷。若目前時間為週末或法定例假日，且最新官方行情為前一交易日（週五）之正式收盤價，判定為合法新鮮數據，允許進入快照投影。
- **測試與驗證門禁**：
  - 執行 `pytest services/source_ingestion/test_taiwan_calendar_freshness.py`，驗證週末與例假日官方收盤行情被正確判定為新鮮，且 Controller 預設常態處於 `reconcile_only`。

---

### SD-Unit-6: Management Postmortem 串接與十二循環純淨投影
- **負責任務**：`OPGAP-BE-MGMT-POSTMORTEM-20260830`
- **精確檔案範圍**：
  - `services/control-plane/bff/management_read_models/router.py`
  - `services/control-plane/bff/management_read_models/models.py`
  - `services/control-plane/bff/management_read_models/loop_truth.py`
  - `services/control-plane/bff/domain_ports/lifecycle_telemetry_governance.py`
  - `services/postmortems/test_main_routes.py`
- **設計與變更規格**：
  1. **直連既有 Postmortem 領域服務 (OP-G18)**：在 `management_read_models/router.py` 中，透過 `domain_ports/lifecycle_telemetry_governance.py` 呼叫既有 `services/postmortems` 微服務，提供 `/bff/management/postmortems` 與 `/bff/management/postmortems/{id}` 讀取投影。不新增第二個 postmortem store 或第二個 router，徹底廢除 BFF 舊 `/api/v1/postmortems*` 別名。
  2. **十二循環純淨投影**：`loop_truth.py` 嚴格僅從靜態定義與 Controller 運行時即時事件進行 join，確保任何情況下均穩定輸出 12 筆且 ID 固定之循環狀態記錄。
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
  - 執行 `pytest services/postmortems/test_main_routes.py`，驗證 postmortem 讀取與 durable readback 一致，且無任何第二 store 存在。

---

### SD-Unit-7: 前端 Production 打包 Mock 隔離與圖譜門禁
- **負責任務**：`OPGAP-FE-BUNDLE-CLEANUP-20260830`
- **精確檔案範圍**：
  - `execute-plans:src/lib/bff/writeOverlay.ts`
  - `execute-plans:src/lib/bff/client.ts`
  - `execute-plans:src/lib/bff/agora.ts`
  - `execute-plans:src/lib/bff/persistence.ts`
  - `execute-plans:src/lib/bff/scenarios.ts`
  - `execute-plans:src/lib/bff/mutations.ts`
  - `execute-plans:src/lib/bff/v5.ts`
  - `execute-plans:src/lib/bff-v1/writeFallback.ts`
  - `execute-plans:src/lib/bff-v1/seed.ts`
  - `execute-plans:src/lib/bff-v1/lists.ts`
  - `execute-plans:src/lib/bff-v1/tradeJournal.ts`
  - `execute-plans:src/lib/bff-v1/managementNl.ts`
  - `execute-plans:src/management/components/nl/NlAssistantDrawer.tsx`
  - `execute-plans:src/management/pages/oversight/NlConsole.tsx`
  - `execute-plans:src/management/pages/oversight/_stubs.tsx`
  - `execute-plans:vite.config.ts`
  - `execute-plans:scripts/check_bundle_mock_reachability.ts`
- **設計與變更規格**：
  1. **移除 Mock/Seed 匯出 (OP-G07)**：自 `src/lib/bff-v1/index.ts` 徹底移除 `writeOverlay`、`writeFallback` 與 `seed.ts` 的 export，刪除無 caller 之 dead NL UI 與 stub 頁面。
  2. **打包依賴圖譜門禁**：新增 Vite 插件與靜態掃描腳本 `check_bundle_mock_reachability.ts`。在前端 build 產出時，解析 Rollup module graph，若發現 production chunk 包含 `@/mocks` 或 `writeOverlay`，立即報錯並中止構建。
- **測試與驗證門禁**：
  - 執行 `npm run build` 與 `npm run test:depgraph`，斷言 production bundle 內包含之 mock 符號數量嚴格為 0。

---

### SD-Unit-8: 前端通用 CRUD 收斂與 Postmortem 權威綁定
- **負責任務**：`OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830`
- **精確檔案範圍**：
  - `execute-plans:src/management/components/write/createEntity.ts`
  - `execute-plans:src/management/pages/ObjectListPage.tsx`
  - `execute-plans:src/management/pages/PersonaOnboarding.tsx`
  - `execute-plans:src/management/pages/phase2/PostmortemLibrary.tsx`
  - `execute-plans:src/lib/bff-v1/postmortemClient.ts`
  - `execute-plans:src/lib/writeIntents/types.ts`
- **設計與變更規格**：
  1. **淘汰前端偽造寫入 (OP-G06)**：重構 `createEntity.ts` 與 `ObjectListPage.tsx`。所有表單送出均對應至具體的 BFF typed command client。對於後端尚無實體支援的 generic entity，前端明確顯示 "Operation unavailable in production" 並禁用提交按鈕，嚴禁使用 `writeOverlay` 假寫入。
  2. **Postmortem 正式接線 (OP-G18)**：新增 `postmortemClient.ts` 串接 `/bff/management/postmortems`。修改 `PostmortemLibrary.tsx`，以 canonical `postmortem_id` 渲染清單與詳細頁面，徹底廢除字串解析 `pm_<incident>` 的臨時機制。
- **測試與驗證門禁**：
  - 執行 `npm run test:unit src/management/`，斷言所有 CRUD 行為均呼叫真實 client，且 Postmortem 頁面正確解析後端資料模型。

---

### SD-Unit-9: 前端 Agora 能力顯式標籤與候選池動態加載
- **負責任務**：`OPGAP-FE-AGORA-WORKSHOP-20260830`
- **精確檔案範圍**：
  - `execute-plans:src/agora/pages/strategy-workshop/WorkshopSessionView.tsx`
  - `execute-plans:src/agora/pages/trading-room/TradingRoomWorkspace.tsx`
  - `execute-plans:src/agora/pages/trading-room/AttributionReportView.tsx`
  - `execute-plans:src/agora/pages/strategy-performance/StrategyPerformancePage.tsx`
- **設計與變更規格**：
  1. **顯式呈現 Adapter 真實性 (OP-G15)**：在 Workshop 策略重構與研發面板中，為每個 adapter 與產出卡片增加 Badge 標籤，清楚標示 `Real Backend`、`Simulation` 或 `Unavailable`，不誤導操作者。
  2. **動態候選池加載**：Trading Room 介面根據後端回傳之 `candidatePoolId` 動態加載候選策略清單，廢除前端固定寫死之 `lens-A..E`。
  3. **績效建議面板展示 (OP-G02)**：在 `AttributionReportView.tsx` 中掛載 `PerformanceSuggestionWidget`，動態拉取後端由事件產出之績效建議，並提供互動操作。
- **測試與驗證門禁**：
  - 執行 `npm run test:unit src/agora/`，確保在各種 adapter 狀態下 UI 均能誠實且正確地渲染。

---

### SD-Unit-10: 部署租約心跳寬限、本地封閉回滾授權與假綠燈消除
- **負責任務**：`OPGAP-DEPLOY-RELIABILITY-20260830`
- **精確檔案範圍**：
  - `scripts/deploy/environment_lease.py`
  - `scripts/deploy_nonprod_vm.sh`
  - `.github/workflows/nonprod-deploy.yml`
  - `scripts/deploy/test_environment_lease.py`
- **設計與變更規格**：
  1. **租約心跳與本地回滾授權 (OP-G16)**：在 `environment_lease.py` 中引入心跳重試機制（3 次指數退避，最長寬限 60 秒）。若在租約執行期間 GitHub API 暫時 timeout，腳本進入 local grace mode 而不立即強制終止部署。同時，將回滾時所需之 baseline manifest 本地封裝，允許在遠端連線異常時本機獨立完成 rollback。
  2. **消除虛假綠燈 (OP-G04, OP-G25 歸併)**：審查 `nonprod-deploy.yml` 與 `deploy_nonprod_vm.sh`。對於任何關鍵步驟（如 auth token 檢驗、exact-pair 探針、readback 檢驗），若執行失敗或被 skipped，工作流必須以 `set -e` 嚴格終止，嚴禁使用 `continue-on-error: true` 或在 step summary 中將其降級標註為 passed。
- **測試與驗證門禁**：
  - 執行 `pytest scripts/deploy/test_environment_lease.py`，模擬 GitHub API 瞬斷情境，驗證心跳寬限與本地回滾授權有效運作。

---

### SD-Unit-11: BFF 組裝入口收斂、多副本載入隔離與重複 Operation ID 清零
- **負責任務**：`OPGAP-BFF-MAIN-ASSEMBLY-20260830`
- **精確檔案範圍**：
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/read_store.py`
  - `services/control-plane/bff/test_normalized_route_uniqueness.py`
  - `tests/bff/test_route_composition.py`
  - `tests/bff/test_multi_replica_loading.py`
- **設計與變更規格**：
  1. **Main 組裝與多副本隔離 (OP-G08, OP-G21 歸併)**：作為單一整合者，重構 `main.py`。`main.py` 僅建立 FastAPI 應用實例、CORS/Middleware 配置、Lifespan 事件處理並包含既有領域 router。領域 router 嚴禁反向 import `main`。
  2. **Duplicate Operation IDs 清零 (OP-G21 歸併)**：修復 18 個重複之 OpenAPI Operation IDs（42 處引用），使 operation ID 衝突數嚴格為 0。
- **測試與驗證門禁**：
  - 執行 `pytest tests/bff/test_route_composition.py` 與 `pytest tests/bff/test_multi_replica_loading.py`，驗證多副本載入 100% pass。
  - 執行 `pytest services/control-plane/bff/test_normalized_route_uniqueness.py`，斷言 normalized collision = 0 且 duplicate operation IDs = 0。

---

### SD-Unit-12: 前端應用殼層、路由掛載與模組包整合匯總
- **負責任務**：`OPGAP-FE-INTEGRATION-ASSEMBLY-20260830`
- **精確檔案範圍**：
  - `execute-plans:src/App.tsx`
  - `execute-plans:src/management/ManagementLayout.tsx`
  - `execute-plans:src/lib/bff-v1/index.ts`
  - `execute-plans:tests/e2e/desktop_authenticated_journey.spec.ts`
  - `execute-plans:tests/e2e/helpers/auth.ts`
  - `execute-plans:tests/e2e/helpers/bff.ts`
- **設計與變更規格**：
  1. **路由掛載與導航收斂**：在 `App.tsx` 與 `ManagementLayout.tsx` 中掛載已完成之 Management 與 Agora 頁面，清理無效/deprecated 路由。
  2. **bff-v1 統一匯出**：在 `src/lib/bff-v1/index.ts` 集中匯出所有 typed domain clients，確保無 mock 符號外洩。
  3. **準備桌面端 E2E 測試規格**：準備 `desktop_authenticated_journey.spec.ts` 供 Wave 4 驗收使用。
- **測試與驗證門禁**：
  - 執行 `npm run build` 與 `npm run test:unit`，確保前端編譯無錯誤。

---

### SD-Unit-13: 統一 Dev VM 原子部署與容器健康驗證
- **負責任務**：`OPGAP-HOSTED-DEV-PROMOTION-20260830`
- **精確檔案範圍**：
  - `docker-compose.yml`
  - `docs/deployment/evidence/full-operation-gap/OPGAP-HOSTED-DEV-PROMOTION-20260830/evidence.json`
- **設計與變更規格**：
  1. **全容器健康與依賴收斂 (OP-G03, OP-G20)**：在 `deploy_nonprod_vm.sh` 執行 compose 部署後，對所有核心服務進行逐一健康探測，要求狀態全數為 `healthy`。
  2. **Agora Projection 驗證適配 (OP-G19)**：在執行 bounded one-shot source refresh 驗證時，讀取並校驗 projection 檔案，斷言其包含最新產出之 `connectorId` 與 `ingestRunId`。
  3. **原子切換與 Manifest 同步**：在所有服務探針與 projection 驗證通過後，更新 `/deployment.json` 並切換前端靜態資產軟連結，產出部署證據 manifest。
- **測試與驗證門禁**：
  - 取得 `pantheon-dev` 資源，驗證部署切換順暢且容器全部 healthy。

---

### SD-Unit-14: 十二循環全量刺激讀回與桌面端登入態驗收
- **負責任務**：`OPGAP-HOSTED-E2E-ACCEPTANCE-20260830`
- **精確檔案範圍**：
  - `scripts/e2e/twelve_loop_acceptance_suite.py`
  - `scripts/e2e/verify_source_reconcile_only_cycle.py`
  - `docs/deployment/evidence/full-operation-gap/OPGAP-HOSTED-E2E-ACCEPTANCE-20260830/evidence.json`
- **設計與變更規格**：
  1. **十二循環端到端自動化 (OP-G11)**：編寫 `twelve_loop_acceptance_suite.py`，依序對 Loop 1 至 Loop 12 注入自然刺激，驗證每個循環均產生對應的 Receipt、Terminal State 及 Management 讀回 ID。
  2. **Source 完整週期驗證 (OP-G12)**：編寫 `verify_source_reconcile_only_cycle.py`，建立測試 Data Source -> 驗證 -> 觸發 bounded one-shot compose profile -> 讀取快照 -> 證明 Controller 自動回到 `reconcile_only`。
  3. **Playwright 桌面端登入態矩陣 (OP-G14)**：執行已就緒之 `desktop_authenticated_journey.spec.ts`，使用短效 dev-login token 登入，遍歷 Management 與 Agora 所有核心路由，捕捉 Network HAR 與 DOM 快照，斷言 0 個阻斷性 console error。
- **測試與驗證門禁**：
  - 驗收腳本輸出結構化 JSON 證據，宣告全產品運作收斂閉環。

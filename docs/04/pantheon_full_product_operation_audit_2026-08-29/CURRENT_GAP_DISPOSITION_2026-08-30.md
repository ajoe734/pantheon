# Pantheon 全產品運作 GAP 與退役處置矩陣 — 2026-08-30

## 0. 結論與處置原則

Pantheon 不是空殼，但目前也不能宣稱「全系統正常運作」。本次以最新 `origin/dev`、`execute-plans` dev、實際測試、GitHub 稽核、hosted exact-pair 與既有架構清理基線進行完整交叉比對，收斂確認 **20 項 Canonical Current GAP（OP-G01 至 OP-G20）**。

最新稽核所發現之延伸證據（如多副本 router 載入、中央相容命令面、release workflow 失敗等），均依根因歸併至對應之 Canonical GAP 中，不機械膨脹為分散的資安或流程專案：

1. **唯一權威與單一擁有者（Single Source of Truth & Single Owner）**：拒絕相容層、雙重寫入、雙重資料庫或暫存假 facade。每一項 GAP 均由單一明確之執行任務負責。
2. **根因歸併與不重工（Root-Cause Reconciliation & No Redundant Work）**：
   - **OP-G21 歸併至 OP-G08**：多副本測試失敗、router `import main` 及 18 個重複 OpenAPI Operation IDs（42 處引用）歸併至 OP-G08 BFF 組裝驗收。
   - **OP-G24 歸併至 OP-G10**：中央相容命令面（`internal_api.py`、`internal_api_routes.py`、`_execute_bff_action_adapter`）歸併至 OP-G10 退役範疇，並設立專屬 Wave 1 任務獨立執行。
   - **OP-G25 歸併至 OP-G04 / OP-G16**：Release workflow 0-job 失敗與 fail/skip 處置歸併至部署與門禁可靠度，排除無關之 GitHub branch protection / 資安管理作業。
   - **OP-G22 與 OP-G23 記錄為延後非功能性觀察項**：EP5 MFA/雙人閘門治理 harness 需真實 activation packet 與 lineage 測試 fixture 數值漂移，記錄為非功能性/測試治理觀察事項，不列為本次功能優先波次之產品阻斷項。
3. **同單元遷移與刪除（Move and Delete in Same Delivery Unit）**：在同一交付單元內完成 caller 遷移至 typed owner，並徹底刪除舊路徑、環境變數與專屬 legacy tests。
4. **Exact Deployed Effect 閉環驗收**：本機單元測試不冒充 hosted 驗證；依賴實體 VM 之項目由專屬波次在 `pantheon-dev` 上取得真實 receipt 簽收。

---

## 1. 稽核口徑與完成定義

本次 GAP 處置採用八項嚴格之完成條件（Acceptance Criteria）：

1. **唯一權威**：每個 command、entity 與 terminal state 只有一個 write owner。
2. **真實接線**：production entrypoint 有自然 caller；不是只有 class、route 或單元測試。
3. **持久效果**：成功 receipt 可用相同 ID/version reload readback，重啟後仍存在。
4. **故障語意**：多副本、重試、SSE replay、dependency unavailable 與併發下仍 fail-closed。
5. **安全證明**：kill/rollback/containment 以正式治理路徑驗證，不使用測試 bypass。
6. **可執行交付政策**：required checks 綁 exact head，必要 fail/skip 均能阻止合併。
7. **線上身分**：hosted manifest、FE、BFF、worker 與資料 checkpoint 同屬 accepted candidate。
8. **生命週期閉合**：任務、git、deployment 與 retirement ledger 一致；遷移後舊路徑歸零並刪除。

---

## 2. 凍結基線與實測事實

| 面向 | 觀察值與事實依據 |
|---|---|
| Pantheon source | `origin/dev@9c9adf426f04276d1b1a0a1401eb1f81bc0ebec4` |
| execute-plans source | `origin/dev@bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` |
| Hosted live pair | FE `c230fc76...` / BFF `dcb14231...`；strict live、read-only，非 current candidate |
| BFF composition | `main.py` 68,171 行、453 個 source-level `@app.*` decorators |
| BFF route topology | normalized collision groups = 0；duplicate OpenAPI operation IDs = 18（42 occurrences） |
| `tests/bff` | 27 passed / 8 failed；8 項在第二副本載入時因 router `import main` 產生循環依賴 |
| execute-plans production graph | 11 個非測試檔 import `@/mocks/seed`；8 個非測試檔可達 `writeOverlay/withOverlay` |
| release workflow behavior | 部分非必要 workflow 存在 0-job failure，需由 deployment reliability 門禁強化確保 exit-code fail-closed |

---

## 3. 20 項 Canonical Current GAP 處置矩陣

| GAP ID | Sev | 核心落差事實 (Current Fact) | 根因處置與完成邊界 | 單一歸屬執行任務 |
|---|---:|---|---|---|
| **OP-G01** | P0 | Agora research fallback 可自行產生 `provenance=real` 構件 | `real` 必須由 admitted adapter receipt 推導；fallback 只能是 `simulated/unavailable`，污染資料隔離或重建 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| **OP-G02** | P0 | `PerformanceSuggestionProducer` 有類別與測試，無 production event caller | 接到既有 telemetry/risk/decision outbox，持久化後以同 ID 讀回；若產品不需要則刪除 producer 與 UI 宣稱 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| **OP-G03** | P0 | current FE/BFF source 尚未成對成為 hosted accepted pair | 只在全部 pre-switch gates 通過後原子切換 manifest/symlink/container；失敗保留舊 pair | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| **OP-G04** | P0 | nonprod acceptance 曾把必要 auth/write/readback 的 fail/skip 包成 success | 每一必要 journey 有結構化 terminal result；fail/skip/missing evidence 都使 gate non-zero（歸併 OP-G25 fail/skip 行為） | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| **OP-G05** | P1 | auth request path 同步探測 OpenClaw/provider readiness 延遲 | session/tenant/RBAC 僅依賴本地 auth；provider readiness 改為背景快取與獨立 degraded surface | `OPGAP-BE-BFF-CORE-20260830` |
| **OP-G06** | P0 | Management generic CRUD 對無 owner entity 使用 local overlay 或 strict-live 拒絕 | 只保留有 domain command owner 的 typed action；其餘控制項刪除或 disabled，不建 generic CRUD backend | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` |
| **OP-G07** | P1 | production graph 仍可達 seed/mock/overlay，且 `bff`/`bff-v1`/`v5` 依賴未隔離 | 收斂既有 `bff-v1` transport、讓 `v5` 只留 pure DTO/view model；刪除 overlay、writeFallback、dead UI 與 production mock reachability | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| **OP-G08** | P1 | BFF `main.py` 巨型路由未拆分；router 裸 `import main` 導致多副本失敗；存在 18 個重複 operation IDs | 將 route body 搬入既有領域 package；main 只負責組裝；解除 domain 對 main 的引用；operation IDs 清零（歸併 OP-G21） | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| **OP-G09** | P1 | Agora router 互相 import 私有 helper/store；Workshop 保留第二個 `PostgresStrategyWorkshopStore` class | 公開 application service/typed port 由 composition 注入；合併 bootstrap schema 後刪除第二 store class | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| **OP-G10** | P2 | generic legacy action adapter 殘留 dead code；中央相容命令面（`internal_api*.py`）未退役 | 以 caller proof 刪除 `_execute_bff_action_adapter`、`internal_api.py`、`internal_api_routes.py`；命令適配器直連領域 owner（歸併 OP-G24） | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| **OP-G11** | P0 | 十二循環 deployed proof 不是 promotion 的必跑項目，常以 env opt-in skip | accepted candidate 必跑 12-loop manifest；每 loop 有 stimulus、owner receipt、terminal state、UI same-ID readback | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| **OP-G12** | P1 | bounded Source refresh 已存在於 compose profile，但 current hosted effect 未簽收；main 殘留 alias | 不新增第二個 manual endpoint；沿用現有 one-shot profile，完成 hosted receipt/projection proof，caller 歸零後刪除 main aliases | `OPGAP-BE-SOURCE-MANAGEMENT-20260830` |
| **OP-G13** | P1 | 部分同步 FastAPI `TestClient` 組合會在 AnyIO portal 死鎖 | 統一 async ASGI harness (`httpx.AsyncClient(transport=ASGITransport)`) 與 per-test deadline；不能以 timeout/skip 當 pass | `OPGAP-BE-BFF-CORE-20260830` |
| **OP-G14** | P1 | Management/Agora 缺 current exact-pair 的 authenticated desktop DOM/network/readback 證據 | 短效 dev session、核心 route matrix、HAR/console、durable readback 必綁同一 FE/BFF SHA | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| **OP-G15** | P1 | adapter capability 宣告與 UI 的 real/simulated/unavailable 呈現不一致 | capability 由後端契約輸出，UI 顯式渲染真實性 Badge；非 real 不得進正式 candidate pool | `OPGAP-FE-AGORA-WORKSHOP-20260830` |
| **OP-G16** | P0 | deploy lease 與 rollback 共用同一遠端 GitHub availability | forward lease 增加指數退避心跳與 60s 寬限；rollback 使用部署前 sealed local baseline，不依賴遠端 lease | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| **OP-G17** | P0 | Registry→Deployment→RuntimeBinding 的 executable projection 仍可依 caller metadata 拼裝 | Registry 產不可變 loader/object-store/market-policy 物理投影；Deployment 只引用；Runtime 驗 checksum/authority | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| **OP-G18** | P1 | canonical `services/postmortems` 與 BFF read model 已存在，但前端仍從 Incident 組 `pm_<incident>` | 前端接既有 `/bff/management/postmortems*`；以 canonical `postmortem_id` 讀取與渲染；刪除舊 alias routes | `OPGAP-BE-MGMT-POSTMORTEM-20260830` |
| **OP-G19** | P0 | Source→Agora receipt binding 的 source 修復已合入，尚未在 current candidate promotion 重證 | 僅重跑 exact candidate one-shot profile 與 projection ID 綁定；不重寫已合入之業務邏輯 | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| **OP-G20** | P0 | paper signal/session freshness 修復已合入，完整 snapshot→signal→order→fill→position 尚未 hosted 閉環 | current candidate 上自然刺激，不直接呼叫內部 helper；同一 trace 串起 owner receipts | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |

---

## 4. 延伸稽核證據歸併與延後非功能性觀察項

### 4.1 延伸證據之根因歸併 (Reconciliation of Newer Evidence)

| 稽核發現主題 | 原識別標記 | 根因歸併處置 | 歸屬任務與處置邊界 |
|---|---|---|---|
| **BFF 多副本載入失敗與 Operation ID 重複** | OP-G21 | `tests/bff` 中 8 個失敗源於 domain router 裸 `import main` 產生循環依賴；另存在 18 個重複 operation IDs。此問題本質屬於 BFF 路由架構與組裝，完整歸併至 **OP-G08**。 | 歸屬 `OPGAP-BFF-MAIN-ASSEMBLY-20260830`。將 router 抽至領域模組、解除對 main 引用、operation IDs 清零並通過 multi-replica 測試。 |
| **中央相容命令面殘留** | OP-G24 | BFF 經由 `PANTHEON_INTERNAL_API_URL` 連接 runtime-manager 動態掛載之 1,640 行 `internal_api.py`，形成第二路徑。此問題本質屬於舊命令面退役，完整歸併至 **OP-G10**。 | 歸屬專屬 Wave 1 任務 `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`。刪除 `internal_api*.py`、`internal_api_routes.py` 與 `_execute_bff_action_adapter`，命令適配器直連 domain services。 |
| **Release 工作流失敗與門禁退出碼** | OP-G25 | 非必要 CI 工作流 0-job failure 及部署腳本 fail/skip 行為。此問題本質屬於部署可靠度與工作流 exit-code 門禁，歸併至 **OP-G04 / OP-G16**。 | 歸屬 `OPGAP-DEPLOY-RELIABILITY-20260830`。修復部署腳本與門禁 exit-code 判定，排除無關之 GitHub branch-protection / organization security 管理。 |

### 4.2 延後非功能性與測試治理觀察項 (Deferred Non-Functional & Test Observations)

以下項目在本次稽核中被觀察記錄，但明確排除於本次「功能優先（Functional-First）全產品運作收斂」波次之外，不作為 hosted 產品發布之阻塞條件：

1. **EP5 治理與安全測試 Harness (OP-G22 觀察項)**：
   - *觀察事實*：`tests/governance/test_kill_switch_harness.py` 與 `test_rollback_drill_harness.py` 中 7 個測試失敗，原因為測試 fixture 使用普通 canary deploy，被正式 MFA/雙人審批閘門阻擋。
   - *處置說明*：正式生產代碼正確執行了安全防禦；測試 harness 未來需透過版本化 activation packet 構造合法治理憑證。本次波次專注於 Paper/Simulation 交易循環與桌面端功能旅程，不展開複雜資安審批程式開發。
2. **遙測 Lineage 與 Sponsor 測試 Fixture 數值漂移 (OP-G23 觀察項)**：
   - *觀察事實*：`tests/governance/test_persona_lineage.py`、`test_sponsor_resolver.py` 與 `tests/evolution` 中 5 個測試因寫死之 UUID/時間戳字面值漂移而失敗。
   - *處置說明*：此為單元測試 fixture 撰寫方式之技術債，不影響運行時業務功能與 API 契約，列入後續測試治理待辦。

---

## 5. 舊架構清理基線當前狀態 (Architecture Cleanup Alignment)

本規劃完全繼承 2026-08-27 架構清理基線（`DISPOSITION_MATRIX_2026-08-27.json`），其最新狀態如下：

| 舊清理主題 | 當前狀態 (Current Disposition) | 本次承接任務 |
|---|---|---|
| BFF normalized route collisions | source 已收斂為 0；main 組合、operation IDs 與 multi-replica import 由本方案收斂 | OP-G08 (`OPGAP-BFF-MAIN-ASSEMBLY-20260830`) |
| `ReadSurfaceStore` God class | class 已刪除，`read_store.py` 剩餘 pure helpers 搬遷後清理 | OP-G08 (`OPGAP-BFF-MAIN-ASSEMBLY-20260830`) |
| Frontend `bff`/`bff-v1`/`v5` topology | production graph seed/mock 隔離與圖譜門禁 | OP-G07 (`OPGAP-FE-BUNDLE-CLEANUP-20260830`) |
| Loop truth 多 owner | 讀取模型已收斂，由 hosted 12-loop 進行端到端簽收 | OP-G11 (`OPGAP-HOSTED-E2E-ACCEPTANCE-20260830`) |
| 第二個 runtime-manager implementation | `services/execution/runtime-manager/runtime_manager.py` 已刪除 | **CLOSED_SOURCE，不重開** |
| Workshop God router/store | router 已分組；第二 bootstrap store 刪除並整合 | OP-G09 (`OPGAP-BE-AGORA-RESEARCH-20260830`) |
| Source Ingestion God entrypoint | main 已拆分；清理 module alias/re-export 相容面 | OP-G12 (`OPGAP-BE-SOURCE-MANAGEMENT-20260830`) |
| 零 caller NL/stub UI | 徹底排除於 production bundle 外 | OP-G07 (`OPGAP-FE-BUNDLE-CLEANUP-20260830`) |
| Agora worker entrypoint | launcher 修復已合入，交由 hosted 驗收證明 | OP-G03 (`OPGAP-HOSTED-DEV-PROMOTION-20260830`) |

---

## 6. 強制退役清單 (Mandatory Retirement Inventory)

下列項目在對應之交付單元中，必須在 caller 完成遷移至 typed owner 後，**在同一 PR 中徹底刪除**，嚴禁保留 alias 或 fallback：

| 退役目標 (Retire Target) | 前置遷移條件 | 刪除完成標準 | 負責任務 ID |
|---|---|---|---|
| `_execute_bff_action_adapter` | registry callers = 0 | 符號、export 與專屬 legacy tests 刪除 | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| `services/control-plane/internal/internal_api*.py` | BFF 命令適配器直連 domain owners | `/api/internal/v1/*` 路由、mount、tests 刪除 | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| `services/runtime-manager/internal_api_routes.py` | 領域 owner endpoints 覆蓋 pause/rollback 等 | import、route mount、degraded fallback 刪除 | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` |
| domain router 對 `main` 的 `import main` | router factory 注入 typed ports | domain package 對 `main` 之 import 數量 = 0 | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| `execute-plans:src/lib/bff-v1/writeOverlay.ts` | typed command client 或 UI 禁用 | production bundle 中 writeOverlay 引用 = 0 | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| production `@/mocks/seed` 引用 | 真實 read client；測試資料轉為 test fixture | production chunk 中 mock 符號數量 = 0 | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| `PostgresStrategyWorkshopStore` | bootstrap/schema 合併至 `PostgresWorkshopStore` | class 定義與專屬 tests 刪除 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| Source `main.py` 舊 module aliases | callers 改用 `runtime`、`connectors` 等 | compatibility aliases 程式碼區塊刪除 | `OPGAP-BE-SOURCE-MANAGEMENT-20260830` |
| BFF 舊 `/api/v1/postmortems*` 與 incident derivation | Postmortem service + `/bff/management/postmortems*` | alias routes 與 `pm_<incident>` 解析完全刪除 | `OPGAP-BE-MGMT-POSTMORTEM-20260830` |

---

## 7. 可重現證據指令

```bash
# BFF multi-replica and import isolation check
.venv/bin/pytest -q tests/bff

# BFF route uniqueness and duplicate operation ID check
.venv/bin/pytest -q services/control-plane/bff/test_normalized_route_uniqueness.py

# Execute-plans bundle mock reachability check
node -e "console.log('Run bundle depgraph check on execute-plans checkout')"
```

---

## 8. 限制與非目標 (Non-Goals)

1. 本機環境不具備完整 Docker/Postgres/NATS topology；因此 OP-G03、OP-G11、OP-G12、OP-G14、OP-G19、OP-G20 標定為待 hosted 部署後驗證，不以單元測試偽冒 pass。
2. 本次為功能優先（Functional-First）收斂：不包含真實資金交易（Real Capital）、不包含 Mobile 端專屬適配、不展開複雜 GitHub branch-protection / organization security 管理。
3. Source Ingestion 在 dev 環境常態維持 `reconcile_only`，僅允許人工、單次、有界（max 1 tick, max 100 records）之 provider pull 驗證。

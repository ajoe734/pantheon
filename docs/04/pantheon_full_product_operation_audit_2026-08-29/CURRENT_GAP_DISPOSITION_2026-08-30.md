# Pantheon 全產品運作 GAP 與退役處置矩陣 — 2026-08-30

## 0. 結論

Pantheon 不是空殼，但目前也不能宣稱「全系統正常運作」。本次以程式碼、
實際測試、GitHub/branch protection、hosted exact-pair 與既有架構清理基線重新
交叉比對，確認 **25 項 current GAP**。其中最重要的不是單一測試失敗，而是五個
共同根因：

1. composition root、相容 API 與前端 transport 仍承擔領域行為，造成第二路徑；
2. 測試或 UI 可在沒有 production caller、durable owner 或真實 receipt 時顯示完成；
3. safety proof 沒有走與正式 canary/live 相同的治理入口；
4. release policy 分散在多份 workflow，branch protection 沒有綁定完整政策；
5. 已完成遷移後，舊入口、fallback、seed 與 compatibility code 沒有一起刪除。

因此處置原則是 **保留唯一 owner、遷移 caller、同一交付單元刪除舊路徑、最後以
exact deployed effect 驗收**，而不是再加一個 façade。

## 1. 稽核口徑

原先「程式碼、測試、CI、看板」四層定義不夠，因為它無法排除單副本通過、假寫入、
安全驗證繞過、舊版本仍在線上，以及完成遷移後的 dead code。這次使用八項完成條件：

1. **唯一權威**：每個 command、entity 與 terminal state 只有一個 write owner。
2. **真實接線**：production entrypoint 有自然 caller；不是只有 class、route 或單元測試。
3. **持久效果**：成功 receipt 可用相同 ID/version reload readback，重啟後仍存在。
4. **故障語意**：多副本、重試、SSE replay、dependency unavailable 與併發下仍 fail-closed。
5. **安全證明**：kill/rollback/MFA/two-person gate 以正式治理路徑驗證，不使用測試 bypass。
6. **可執行交付政策**：required checks 綁 exact head，必要 fail/skip/0-job 都能阻止合併。
7. **線上身分**：hosted manifest、FE、BFF、worker 與資料 checkpoint 同屬 accepted candidate。
8. **生命週期閉合**：任務、git、deployment 與 retirement ledger 一致；遷移後舊路徑歸零並刪除。

驗證順序為：source topology → production caller/owner → focused tests → failure/safety tests →
CI/branch policy → hosted exact-pair → git/task/evidence lineage → retirement inventory。

## 2. 凍結基線與本次實測

| 面向 | 觀察值 |
|---|---|
| Pantheon source | `origin/dev@9c9adf426f04276d1b1a0a1401eb1f81bc0ebec4` |
| execute-plans source | `origin/dev@bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` |
| Hosted pair | FE `c230fc76...` / BFF `dcb14231...`；strict live、read-only，並非 current source |
| BFF composition | `main.py` 68,171 行、453 個 source-level `@app.*` decorators |
| BFF route topology | normalized collision groups = 0；duplicate OpenAPI operation IDs = 18（42 occurrences） |
| `tests/bff` | 27 passed / 8 failed；8 項都在第二副本載入時被裸 `import main` 綁到 persona main |
| EP5/governance/evolution focused set | 24 passed / 12 failed；7 safety harness blocked、5 evidence literal drift |
| normalized-route suite | 15 passed；其中 operation-ID 測試目前只「characterize duplicates」，不是 zero gate |
| execute-plans production graph | 11 個非測試檔 import `@/mocks/seed`；8 個非測試檔可達 `writeOverlay/withOverlay` |
| latest merged promote PR #5423 | 7 個 PR workflow `failure` 且 `jobs=[]`；只有 Branch CI jobs 成功，PR 仍合併 |
| master protection | required contexts 只有 `Commit trailers`、`Runtime mirror guard`、`Smoke acceptance`；0 approvals、admins 未 enforce |
| open bot promote PR | PR #5264 的 PR-event workflows 為 `action_required`；repo policy 是 `first_time_contributors` |

測試指令與直接證據詳見本文件第 6 節；沒有 Docker/Postgres/NATS 的本機結果不被冒充為
hosted pass。

## 3. 25 項 current GAP

| ID | Sev | Current fact | 根因處置與完成邊界 | 唯一執行任務 |
|---|---:|---|---|---|
| OP-G01 | P0 | research fallback 可自行產生 `provenance=real` artifact | `real` 必須由 admitted adapter receipt 推導；fallback 只能是 `simulated/unavailable`，污染資料需隔離或重建 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G02 | P0 | `PerformanceSuggestionProducer` 有類別與測試，沒有 production event caller | 接到既有 telemetry/risk/decision outbox，持久化後以同 ID 讀回；若產品不需要則刪除 producer 與 UI 宣稱 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G03 | P0 | current FE/BFF source 尚未成對成為 hosted accepted pair | 只在全部 pre-switch gates 通過後原子切換 manifest/symlink/container；失敗保留舊 pair | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G04 | P0 | nonprod acceptance 曾把必要 auth/write/readback 的 fail/skip 包成 success | 每一必要 journey 有結構化 terminal result；fail/skip/missing evidence 都使 gate non-zero | `OPGAP-DELIVERY-POLICY-20260830` |
| OP-G05 | P1 | auth request path 同步探測 OpenClaw/provider readiness | session/tenant/RBAC 僅依賴本地 auth；provider readiness 改為背景快取與獨立 degraded surface | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G06 | P0 | Management generic CRUD 對無 owner entity 使用 local overlay 或 strict-live 拒絕 | 只保留有 domain command owner 的 typed action；其餘控制項刪除或 disabled，不建 generic CRUD backend | `OPGAP-FE-MGMT-BINDING-20260830` |
| OP-G07 | P1 | production graph 仍可達 seed/mock/overlay，且 `bff`/`bff-v1`/`v5` 仍雙向依賴；4 個零 production-caller NL/stub 檔仍存在 | 收斂既有 `bff-v1` transport、讓 `v5` 只留 pure DTO/view model；刪除 overlay、writeFallback、dead UI 與 production mock reachability | `OPGAP-FE-TRANSPORT-RETIREMENT-20260830` |
| OP-G08 | P1 | BFF `main.py` 仍為 68k 行領域/route owner；原 God store 雖刪除但 `read_store.py` pure-helper 殼與 fallback env 仍殘留 | route body 搬入既有領域 package；main 只組裝；helper 移到 owner 後刪除殼與 fallback flag/fixtures | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| OP-G09 | P1 | Agora router 互相 import 私有 helper/store；Workshop 仍保留第二個 `PostgresStrategyWorkshopStore` bootstrap class | 公開 application service/typed port 由 composition 注入；合併 bootstrap schema 後刪除第二 store class | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G10 | P2 | `_execute_bff_action_adapter` 不在 production registry，僅 legacy test/monkeypatch 可達 | 以 caller proof 刪除 function、export 與專屬 legacy tests；不得接回 fallback | `OPGAP-COMMAND-PLANE-RETIREMENT-20260830` |
| OP-G11 | P0 | 十二循環 deployed proof 不是 promotion 的必跑項目，常以 env opt-in skip | accepted candidate 必跑 12-loop manifest；每 loop 有 stimulus、owner receipt、terminal state、UI same-ID readback | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| OP-G12 | P1 | bounded Source refresh 已存在於 compose/deploy profile，但 current hosted effect 未簽收；`source_ingestion/main.py` 仍有大量 test/back-compat re-export | 不新增第二個 manual endpoint；沿用現有 one-shot profile，完成 hosted receipt/projection proof，caller 歸零後刪除 main aliases | `OPGAP-BE-SOURCE-CLOSURE-20260830` |
| OP-G13 | P1 | 部分同步 FastAPI `TestClient` 組合會 AnyIO deadlock | 統一 async ASGI harness、dependency compatibility matrix 與 per-test deadline；不能以 timeout/skip 當 pass | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G14 | P1 | Management/Agora 缺 current exact-pair 的 authenticated desktop DOM/network/readback 證據 | 短效 dev session、核心 route matrix、HAR/console、durable readback 必綁同一 FE/BFF SHA | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| OP-G15 | P1 | adapter capability 與 UI 的 real/simulated/unavailable 宣稱不一致 | capability 由後端契約輸出，UI 只渲染；非 real 不得進正式 candidate truth | `OPGAP-FE-AGORA-CAPABILITY-20260830` |
| OP-G16 | P0 | deploy lease 與 rollback 共用同一遠端 GitHub availability | forward lease 可有有界 grace；rollback 使用部署前 sealed local baseline，不需重新取得同一遠端 lease | `OPGAP-DELIVERY-POLICY-20260830` |
| OP-G17 | P0 | Registry→Deployment→RuntimeBinding 的 executable projection 仍可依 caller metadata 拼裝 | Registry 產不可變 loader/object-store/market-policy projection；Deployment 只引用；Runtime 驗 checksum/authority | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| OP-G18 | P1 | canonical `services/postmortems` 與 BFF Management read model 已存在，但前端仍從 Incident timeline 組 `pm_<incident>`；main 另有舊 `/api/v1/postmortems*` routes | 前端接既有 `/bff/management/postmortems*`；BFF read port 直讀 Postmortem service；caller 遷移後刪除舊 alias routes，不新建第二 owner | `OPGAP-FE-MGMT-BINDING-20260830` |
| OP-G19 | P0 | Source→Agora receipt binding 的 source 修復已合入，尚未在 current candidate promotion 重證 | 僅重跑 exact candidate one-shot profile與 projection ID 綁定；不要重寫已合入邏輯 | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G20 | P0 | paper signal/session freshness 修復已合入，完整 snapshot→signal→order→fill→position 尚未 hosted 閉環 | current candidate 上自然刺激，不直接呼叫內部 helper；同一 trace 串起 owner receipts | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G21 | P0 | `tests/bff` 8 fail：identity/personalization routers 裸 `import main`；第二副本會載入 persona main；同時仍有 18 duplicate operation IDs | router factory 注入 typed dependencies，禁止 domain/router import composition root；多副本與 operation-ID zero 成為硬 gate | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| OP-G22 | P0 | EP5 kill/rollback harness 7 fail；fixture 用普通 canary deploy，被 `service.py:942` 正式 MFA/雙人閘門阻擋 | harness 建立真實 governed activation packet與 distinct actors，再執行 kill/rollback；禁止開 `_allow_*_bypass` 給測試 | `OPGAP-SAFETY-PROOF-CONTRACT-20260830` |
| OP-G23 | P1 | persona lineage 2、sponsor 1、evolution tenant ref 2 項測試以 copied literal 斷言而漂移 | 由版本化 canonical evidence builder 產生 fixture/expected refs；測試驗 provenance/generator，不複製 UUID/序號 | `OPGAP-SAFETY-PROOF-CONTRACT-20260830` |
| OP-G24 | P0 | BFF 把所有 `PANTHEON_INTERNAL_API_URL` 指向 runtime-manager；後者動態掛載 1,640 行 legacy internal API，形成跨 Deployment/Governance/Runtime 的中央相容 command plane | 每個 command adapter 直連 domain owner；完成 caller/telemetry cutover 後刪除 `/api/internal/v1/*`、`internal_api_routes.py`、`internal_api*.py` 與 fallback env | `OPGAP-COMMAND-PLANE-RETIREMENT-20260830` |
| OP-G25 | P0 | 最新 merged promote PR #5423 有 7 個 0-job failure workflow，master 仍因只要求 Branch CI 三項而合併；bot PR 另受 approval policy 影響 | 建立一個 exact-head release policy orchestrator；重用必要 checks、輸出單一 required result；branch protection 與 policy manifest 自動對帳，刪除被取代 workflow | `OPGAP-DELIVERY-POLICY-20260830` |

## 4. 舊架構清理基線的 current disposition

本文件不複製 2026-08-27 的 102 筆 KEEP/MIGRATE/MERGE/REMOVE/VERIFY 清單；其
artifact-level 決策仍以
[`DISPOSITION_MATRIX_2026-08-27.json`](../pantheon_architecture_cleanup_gap_2026-08-27/DISPOSITION_MATRIX_2026-08-27.json)
為準。本次重新查 source 後，九個清理主題的 current 狀態如下：

| 舊清理主題 | Current disposition | 本次承接 |
|---|---|---|
| BFF normalized route collisions | source 已收斂為 0；main ownership、operation IDs 與 multi-replica import 未完成 | OP-G08、OP-G21 |
| `ReadSurfaceStore` God class | class 已刪除，`read_store.py` 只剩 124 行 pure helpers；fallback env/fixtures與殼名仍待移除 | OP-G08 |
| frontend `bff`/`bff-v1`/`v5` topology | 未完成；production graph 仍雙向且可達 seed/overlay | OP-G07 |
| loop truth 多 owner | source-level 讀取模型已收斂，仍需 current hosted 12-loop proof | OP-G11 |
| 第二個 runtime-manager implementation | `services/execution/runtime-manager/runtime_manager.py` 已刪除 | **CLOSED_SOURCE，不重開** |
| Workshop God router/store | router 已拆成 route groups；第二 bootstrap Postgres store仍存在 | OP-G09 |
| Source Ingestion God entrypoint | route family 已拆，main 345 行；module alias/re-export 相容面仍在 | OP-G12 |
| 零 caller NL/stub UI | 4 個已識別檔案仍存在，僅測試引用舊 fixed responder | OP-G07 |
| Agora worker shipped entrypoint | package-safe launcher與 required-worker 修復已合入 | **SOURCE_FIXED，交由 OP-G03 hosted 驗證** |

開發工具的 TaskStore/projection/supervisor/worker lifecycle 不在此產品 task catalog 重複
建任務；由已合入的
[`development-tooling-four-gap-2026-08-30`](../../operations/development-tooling-four-gap-2026-08-30/INDEX.md)
單獨擁有。產品 release acceptance 只能依賴其 authoritative projection，不得複製其 writer。

## 5. 強制退役清單

下列項目不是「保留以防萬一」。每項必須先列出 production/test/workflow caller，完成
typed-owner parity 與 readback，然後在**同一 delivery unit**刪除舊路徑及專屬測試：

| Retire target | 前置遷移 | 刪除完成證據 |
|---|---|---|
| `services/control-plane/bff/command_executor.py::_execute_bff_action_adapter` | registry callers = 0 | symbol/export/legacy tests = 0 |
| `services/control-plane/internal/internal_api.py`、`internal_api_min.py` | BFF domain adapters直連 Runtime/Deployment/Governance等 owner | `/api/internal/v1/*` production caller、Compose env、mount、tests = 0 |
| `services/runtime-manager/internal_api_routes.py` | canonical owner endpoints覆蓋 pause/rollback/kill/approval readback | import、route mount、degraded fallback = 0 |
| identity/personalization 的 `import main` | router factory注入 typed port | production domain packages對 `main` import = 0 |
| `execute-plans:src/lib/bff/writeOverlay.ts`、`bff-v1/writeFallback.ts` | typed command client或功能移除 | production bundle/import graph symbols = 0 |
| production `@/mocks/seed` consumers | 真實 read client；測試資料改為 test-local fixture | production chunk與 non-test imports = 0 |
| `NlAssistantDrawer.tsx`、`NlConsole.tsx`、`_stubs.tsx`、`bff-v1/managementNl.ts` | active Management AI 保留 `managementAi.ts` | production caller = 0 且檔案刪除 |
| `read_store.py` 殼、`PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK`、product fixture packs | pure helpers搬到具名 owner；test fixture明示注入 | production config/symbol/data imports = 0 |
| `PostgresStrategyWorkshopStore` | bootstrap/schema 合併到 `PostgresWorkshopStore` | class/constructor/tests = 0 |
| Source `main.py` module aliases/re-exports | tests/callers改用 `runtime`、`api_models`、`routers` | compatibility section與 re-export callers = 0 |
| BFF 舊 `/api/v1/postmortems*`、incident-string derivation | existing Postmortem service + `/bff/management/postmortems*` | alias route與 `pm_<incident>` derivation = 0 |
| 被 canonical release orchestrator 取代的 workflow | reusable checks與branch policy先就位 | workflow、required context、文件引用同步移除 |

禁止以 `legacy2`、`compat_v2`、新 generic `routers/`、新 Postmortem store 或第二 manual
Source endpoint承接上述刪除；那只會把 dead code 換路徑。

## 6. 可重現證據

```bash
# BFF multi-replica
.venv/bin/pytest -q tests/bff
# 27 passed, 8 failed

# EP5 + evidence contract drift
.venv/bin/pytest -q \
  tests/governance/test_kill_switch_harness.py \
  tests/governance/test_rollback_drill_harness.py \
  tests/governance/test_persona_lineage.py \
  tests/governance/test_sponsor_resolver.py tests/evolution
# 24 passed, 12 failed

# Route topology
.venv/bin/pytest -q services/control-plane/bff/test_normalized_route_uniqueness.py
# 15 passed；另由 helper inventory 得 18 duplicate operation IDs / 42 occurrences

# Live promotion policy
gh pr view 5423 --repo ajoe734/pantheon --json statusCheckRollup,mergedAt,headRefOid
gh run list --repo ajoe734/pantheon --branch promote/v2026.08.29.5
gh api repos/ajoe734/pantheon/branches/master/protection
```

## 7. 限制與不得誤讀事項

- 本機未具備完整 Docker/Postgres/NATS hosted topology；因此 OP-G03、11、12、14、19、20
  仍只能標為待 hosted 驗證，不能以單元測試代替。
- 既有 hosted pair 是可讀且受補償保護的舊 baseline；「舊 pair 健康」不等於 current source
  已部署。
- `action_required` 與 0-job `failure` 是兩種狀態；本文件分別記錄，不再一律寫成
  `workflow file issue`。
- 檔案大不是單獨刪除理由；只有重複 owner、跨層依賴、無 caller、fallback truth 或無法
  獨立驗證才構成 cleanup GAP。

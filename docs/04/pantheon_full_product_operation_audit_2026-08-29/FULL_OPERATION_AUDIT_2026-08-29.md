# Pantheon 全產品正常運作盤點 — 2026-08-29

| 欄位 | 內容 |
|---|---|
| 文件狀態 | 完整稽核結論（2026-08-29T23:41Z 納入 post-cutoff promotion run 33280168821 失敗與補償恢復重審）；舊 FE/BFF pair 由補償維持一致運行，current source promotion 在 Agora projection 綁定驗證受阻，第三遍 current UI 明確保留為未驗證 |
| 稽核範圍 | Pantheon 系統、十二循環、Management、Management AI、Agora、Source Ingestion、操作 UI、dev hosted runtime |
| Pantheon repository 基線 | `origin/dev@9ee1e936c2db6f871085b28c4db563d349a050aa`；包含 `f227360` Source frontier recovery、`9e9ab33` Active symbol snapshot recovery、`44895a2` Official snapshot min closes、`394eb05` Taiwan market-session freshness、`e5b4d10` dev tooling cleanup、`254d2e7` Source frontier scope recovery 及 `9ee1e93` delivery recovery |
| Pantheon product runtime 基線 | `8f8383b507b1fb631d44422031f01ebea5024d5e`（含 `254d2e7b05096dad3f6c7512db089ae2cbd8fe08`；run 33280168821 candidate） |
| execute-plans source 基線 | `origin/dev@bd03c863e3c2c1c64b9b7797f27cefaf84df17c1`（包含 PR #694 evidence manifest，前端 UI 程式碼同 `5ffee3db8c2b37b4070d43d091ed4207ef5d70e5`） |
| current hosted BFF | `dcb14231d29f08f1646a4ee962b83fd2d4b67560`；`23:41Z` 直接確認：舊 accepted pair 運行中，PostgreSQL checkpoint `7,637,654`，backlog 0，quarantine 0 |
| current hosted FE | `c230fc76bef78fc297135152f2acba690314bb9d`；前一個 accepted release |
| hosted profile | `read-only`; `live` + `strict`; real writes false |
| UI 裝置範圍 | Desktop browser；不納入 mobile 測試 |
| Source 原則 | dev 常態 `reconcile-only`；只允許人工、單次、有界的 pull 驗證 |

## 1. 先定義「正常運作」

本報告不把 task 完成、PR 合併、process 存活、單一 HTTP 200、mock/seed、
in-process test 或舊版 evidence 當成產品正常運作。每一項可見功能必須按同一條
完整證據鏈判定：

```text
目前需求／操作者 stimulus
  -> current source 的單一功能 owner
  -> exact hosted FE/BFF 可達
  -> 真實 command/event/read model（不是 seed、fixture 或 local overlay）
  -> durable terminal state 與同 identity readback
  -> 下一個 consumer 讀到同 identity
  -> Management／Agora UI 誠實顯示相同結果
  -> desktop browser 無阻斷性 console/network/render error
```

一個功能只有同時通過以下九個條件才是 `PASS`：

1. **版本正確**：證據綁定 current source 與實際 served FE/BFF；不能拿舊 SHA 代替。
2. **可達與健康**：owner process、必要依賴及 route 可達；ready 需涵蓋真正依賴。
3. **單一真實來源**：讀取來自 canonical owner；不得用 seed、fixture、hash series、
   local state 或 fallback 偽裝 live truth。
4. **使用者旅程可完成**：使用者從 UI 或正式 API 可自然觸發，不由測試直接插入下游物件。
5. **寫入終結**：需要 mutation 的功能要到 domain terminal state，不能只停在
   `admitted`、toast success 或前端 overlay。
6. **持久化讀回**：navigation/reload 後仍可用相同 ID 讀回相同狀態。
7. **跨元件一致**：下游 consumer、Management、Agora 看到同一 identity、revision 與狀態。
8. **UI 可操作且誠實**：desktop route 可 render；disabled/degraded/empty 都要標示真因，
   不得把 unavailable 顯示成 completed。
9. **可重現證據**：至少有 current source test、hosted API/readback 或 hosted desktop journey
   的直接證據；間接證據只能標 `UNVERIFIED`。

額外運作限制：

- Source Ingestion 在 dev 常態不得持續對外 pull；只有人工、單次、有界的測試可以啟動，
  完成後回到 `reconcile-only`。
- 本次以 paper/non-capital journey 驗證功能；不以 real capital、live broker 或正式 token
  作為完成前提。
- 既有認證可使用 dev login/stub 讓功能接受驗證，但本報告不新增資安架構，也不把
  資安強化列為功能完成前置條件。

### 1.1 判定標籤

| 標籤 | 定義 |
|---|---|
| `PASS` | 九項條件全部有 current 直接證據 |
| `PARTIAL` | 主路徑部分可用，但至少一個必要環節未完成或 source/hosted 不一致 |
| `FAIL` | current 直接證據重現功能錯誤、假成功、錯誤 owner 或無法完成 |
| `UNVERIFIED` | 現有證據不足；不推定成功也不推定失敗 |
| `N/A` | 明確不在本次產品範圍，並附理由 |

### 1.2 測試不是功能完成判定

每個功能要分開回答四個問題，不能用其中一項代替其他三項：

1. **程式是否存在**：route、component、command、store及consumer是否有current實作。
2. **production wiring是否存在**：正常使用者路徑是否真的會呼叫它，而不是只有test直接new class、
   monkeypatch、fixture或人工插入下游物件。
3. **目前部署是否使用它**：served FE/BFF、worker、資料庫與manifest是否屬於同一exact candidate。
4. **使用結果是否成立**：使用者能完成操作，取得terminal state，reload後讀回同一ID，下一個consumer
   與UI也顯示相同狀態。

單元、契約與in-process tests主要回答第1項及部分第2項；只有部署與journey/readback證據才能回答
第3、4項。本次直接找到的反例包括：`PerformanceSuggestionProducer` tests通過但production無caller、
release workflow顯示success但必要steps被skip、current FE source通過typecheck/tests但根本尚未served，
以及generic Management CRUD在local overlay成功卻沒有durable owner。這些一律不算功能完成。

## 2. 稽核範圍與功能面

| ID | 產品面 | 必須驗證的核心旅程 | 目前狀態 |
|---|---|---|---|
| P-01 | Product shell / navigation | 登入、shell、主要 routes、error/degraded 呈現 | `PARTIAL` |
| P-02 | Source Ingestion | catalog/config、人工單次 pull、record、freshness、停回 reconcile-only | `PARTIAL` |
| P-03 | Loop 2–4 | SourceRecord -> distillation -> strategy/alpha -> teaching readback | `PARTIAL` |
| P-04 | Agora / Loop 5 | Workshop -> research -> candidates -> trading room -> decision/performance | `FAIL` |
| P-05 | Loop 6–7 | imitation/consultation -> policy/governance receipt | `PARTIAL` |
| P-06 | Deployment / Loop 8 | approved artifact -> executable binding -> Runtime Manager readback | `FAIL` |
| P-07 | Paper / Capital / Loop 9 | snapshot -> signal -> order/fill/position/heartbeat，paper-only | `FAIL` |
| P-08 | Reconciliation / Loop 10 | fill/heartbeat -> drift/incident readback | `PARTIAL` |
| P-09 | Evolution / Loop 11 | incident/postmortem -> evolution decision/receipt | `PARTIAL` |
| P-10 | Loop truth / Loop 12 | canonical 12-loop state與 Management 同 ID 顯示 | `PARTIAL` |
| P-11 | Management reads | cockpit、fleet、journeys、performance、rankings、risk、registries、ops | `PARTIAL` |
| P-12 | Management commands | visible mutations -> terminal receipt -> durable readback | `PARTIAL` |
| P-13 | Management AI | conversation、NL answer、UI actions、domain action handoff | `PARTIAL` |
| P-14 | Agora UI | trading room、workshop、performance 三大頁與 detail routes | `PARTIAL` |
| P-15 | Management UI | mounted nav、canonical redirects、detail/empty/degraded、desktop controls | `PARTIAL` |
| P-16 | Delivery/runtime truth | exact pair、health、dependency readiness、rollback-safe served identity | `FAIL` |
| P-17 | 架構簡化結果 | 單一 owner、無 route collision、無 production fixture/dead duplicate | `FAIL` |

## 3. 第一批直接證據：版本與 hosted 基礎健康

稽核時間 `2026-08-29T15:47:39Z` 起至 `2026-08-29T23:41:28Z` 最新重驗：

| 項目 | 觀察 | 判定 |
|---|---|---|
| Pantheon current source | `origin/dev@9ee1e93`；包含 `f227360` Source frontier recovery、`9e9ab33` active symbol snapshot recovery、`44895a2` official snapshot min closes、`394eb05` Taiwan market session freshness、`254d2e7` Source frontier scope recovery 及 `9ee1e93` delivery recovery | source 基線已前進；包含五大 Agora/Source/Paper 恢復變更 |
| execute-plans current source | `origin/dev@bd03c86` | source 基線包含 PR #694 closeout manifest；UI 程式碼同 `5ffee3d`（Workshop tenant submission 修正） |
| Hosted release pair | FE `c230fc7` / BFF `dcb1423`，pair id `0429052b...` | exact hosted pair 可識別且維持一致 |
| Source vs hosted | 目前兩個 repo 的 hosted SHA 仍落後 current source | `PARTIAL`；live 服務已由補償維持自洽舊版，但未部署 current candidate |
| FE transport | `VITE_BFF_MODE=live`、`VITE_BFF_FALLBACK=strict` | read transport 設定正確 |
| FE write profile | `read-only`、`VITE_BFF_REAL_WRITES=false` | 不能證明任何 mutation journey |
| BFF `/bff/version` | HTTP 200；source SHA `dcb1423` 與 manifest 一致 | `PASS`（僅版本端點） |
| BFF `/livez` | HTTP 200、live/ready true | `PASS`（僅 process 存活） |
| BFF `/readyz` | runtime-manager、governance、deployment、lifecycle projector 皆 ok | `PASS`（已列依賴） |
| Lifecycle projector | Postgres writer/reader、backlog 0、quarantine 0、paper scope、checkpoint `7,637,654` | `PASS`（此時間點 projection readiness） |
| Legacy recovery stores | preserved=true、accepted_reader=false | 未作 canonical reader；不算雙 owner |

### 3.1 Promotion 期間與失敗後的第二次 identity 觀察

`2026-08-29T16:06Z` 再讀公開 hosted truth 時，部署處於有租約保護的中間狀態：

| Surface | Served identity | 觀察 |
|---|---|---|
| BFF `/bff/version` | `7583bb6` | 已切至本次 product runtime；strict auth；Lifecycle reader/writer 均為 PostgreSQL |
| BFF `/readyz` | `7583bb6` | ready；Lifecycle checkpoint = source high watermark = `7,617,157`；backlog/quarantine = 0 |
| FE `/deployment.json` | `c230fc7` | 仍是前一個 read-only release；失敗promotion的target FE `e205643`未切換，current source又已前進至`5ffee3d` |

失敗後於 `2026-08-29T16:27:49Z` 再讀結果不變：BFF仍是`7583bb6`，
Lifecycle checkpoint/high-watermark為`7,618,271`、backlog/quarantine為0；FE manifest仍是
`c230fc7/dcb1423`。這只證明 BFF process與Lifecycle projection可用；FE/BFF不是一組
candidate，故不執行UI功能判定。部署工作 `ajoe734/pantheon#33260583008` 最後在
`Deploy dev VM stack under lease` 失敗，後續 paper、evolution、OpenClaw、Agora restart probes
全部 skipped。

### 3.2 失敗與未完成 rollback 的直接證據

workflow log顯示 environment lease在長部署期間讀取 `execute-plans` 的
`environment-coordination` branch時遭遇 GitHub API timeout。補償步驟準備把BFF從
`7583bb6` 回滾到 `dcb1423`，但取得／驗證 rollback lease時同樣 timeout；log的終態是：

```text
environment lease failed: GitHub API request failed ... <urlopen error timed out>
[dev-deploy-compensation] rollback incomplete; lease remains quarantined until TTL
Process completed with exit code 78
```

但 GitHub job summary仍把 compensation與lease-release steps顯示為`success`。失敗後再次
讀公開端點，FE manifest仍宣稱舊pair `c230fc7/dcb1423` accepted，而實際BFF version是
`7583bb6`。因此該次觀察已是**manifest與served BFF矛盾的split pair**，不是rollback-safe狀態；
後續第三次觀察列於下一節。

### 3.3 第三次identity觀察：新promotion再次進入half-switched狀態

`2026-08-29T16:40:22Z`再次直接讀公開端點：

- FE `/deployment.json`仍是accepted `c230fc7`，並宣告BFF `dcb1423`；
- 實際BFF `/bff/version`已是`1d614f3`，build time `16:15:26Z`；
- `/livez`為live/ready；`/readyz`所列Runtime Manager、Governance、Deployment均ok；
- Lifecycle reader/writer仍為PostgreSQL，deployment SHA `1d614f3`，checkpoint與source high
  watermark同為`7,618,795`，backlog/quarantine均為0。

對應promotion run `33262293025`在此時間點仍停在`Deploy dev VM stack under lease`，target是
Pantheon `1d614f3` + FE `e205643`；paper、evolution、public exact-version、OpenClaw與Agora
persistence steps仍pending。這不是完成證據，而是第二次half-switch的即時快照。

current FE `5ffee3d`的push integration run `33263289212`又在`Resolve exact live BFF release identity`
直接失敗，後續install/test/build/authenticated/hosted/write-proof/browser steps全部skipped；Branch CI
另行通過。這兩個run合在一起證明current source test與hosted acceptance是不同結果，也證明目前
仍不能執行可歸屬candidate的第三遍UI簽收。

### 3.4 第四次identity觀察：補償恢復舊pair，但current promotion失敗

run `33262293025`於`2026-08-29T16:42:12Z`以failure終止。`Deploy dev VM stack under lease`
直接報告兩類阻斷，後續paper baseline、evolution、canonical paper lifecycle、public exact-version、
OpenClaw與Agora restart persistence steps全部skipped：

1. `paper-signal-producer`在stabilization deadline仍為`unhealthy`；
2. 所有required services的Compose image ID都被判`invalid`。該次deploy script只接受
   `sha256:<64 hex>`，但該VM的`docker compose images -q`實際回傳`<64 hex>`，因此即使
   container image identity存在也全部被拒絕。

這次`Compensate dev deployment failure to exact hosted baseline`顯示success，且不是只信workflow
標籤：`2026-08-29T16:45:59Z`直接重讀確認FE仍為`c230fc7`、manifest宣告BFF `dcb1423`，
實際BFF也已回`dcb1423`；`/readyz` ready，Lifecycle checkpoint/high-watermark同為`7,619,101`，
backlog/quarantine均為0。因此**目前served pair已恢復一致，但只是舊版**。這關閉當下split-pair
狀態，不關閉current `b3b26a7/5ffee3d`尚未部署、功能probes全skip及current UI無法簽收的落差。

### 3.5 後續 promotion 序列、週末新鮮度阻斷與 source 演進

`16:45Z` 之後，倉庫進行了多輪 deployment 與 source 修復：

1. **`33266252692`（17:40Z, b3b26a7 + bd03c86）**：部署失敗。新增的 `deploy_nonprod_vm.sh` 雖已支援 Compose bare 64-hex image ID 正規化，但後續 paper signal producer 與 source snapshot 鏈路仍未完成。
2. **`33268311841`（18:27Z, f227360 + 5ffee3d）**：部署失敗。PR #5412 合併了 Source controller explicit frontier recovery（`services/source_ingestion/controller_worker.py`），使 active symbol frontier checkpoint 可在重啟時正確恢復。
3. **`33271922125` / `33271993547` / `33272385942`（19:50Z - 20:25Z, 9e9ab33 + 5ffee3d）**：PR #5413 合併了 Taiwan official refresh priority 與 snapshot read alias view。在 run `33272385942` 中，`[remote-deploy] bounded source refresh prioritizing active paper symbols: 2330.TW` 成功觸發拉取，但在 20:25:46Z 執行 snapshot 驗證時觸發硬阻斷：
   ```text
   active paper snapshot is outside 24h for 2330.TW: event_time=2026-08-28T00:00:00Z age_seconds=159946
   [remote-deploy] dev root compose ps after failure
   ```
   直接證據顯示：2026-08-29 為週六，TWSE 最新官方收盤價為 2026-08-28（週五）。因非交易日時間距離超過 24 小時（44 小時），舊有的 flat 24h 規則直接把合法的週五收盤價判為過期，使 deployment gate 失敗並觸發補償。
4. **PR #5415（`44895a2`）與 PR #5416（`394eb05`）的合併修復**：
   - PR #5415 引入 bounded 2-month official history acquisition、distinct-day snapshot deduplication，以及 nonprod deployment 診斷要求（每 symbol 至少 2 筆 distinct official closes）。
   - PR #5416 將 flat 24h 檢驗改為 governed Taiwan（Asia/Taipei）market-session freshness 規則：在 refresh receipt（`observed_at`）與 lineage 皆為 official 且新鮮的前提下，承認週五官方收盤價在週末及法定例假日的有效性；週間過期、非官方 lineage 或偽造時間戳仍 fail-closed。
5. **Post-cutoff nonprod deployment run `33280168821` 失敗與補償恢復**：
   - 部署 candidate：Pantheon `8f8383b507b1fb631d44422031f01ebea5024d5e`（含 `254d2e7` Source frontier scope recovery）+ execute-plans `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1`。
   - 流程執行情況：Immutable admission、SSH 連線、environment lease 取得、Docker Compose container 建立及各大主要服務（BFF、Runtime Manager、Governance、Deployment、Lifecycle Projector）的 health check 全部成功。
   - 失敗直接證據：在 `scripts/deploy_nonprod_vm.sh` 執行 bounded manual Source refresh 觸發 active paper symbol（2330.TW）的 Taiwan official pull 並產出新鮮合規 receipt 之後，deploy script 驗證 Agora read projection 時觸發阻斷：
     ```text
     raise SystemExit("Agora projection does not bind the new receipt/run/source")
     ```
     於 `scripts/deploy_nonprod_vm.sh:1218` 退出（exit code 1），使 deployment gate 終止。
   - **觀察事實與推論邊界區分**：
     - 直接觀察事實：驗證程式讀取 `projection_path` 時，未找到符合 `connectorId == connector_id`、`ingestRunId == run_id` 且 `sourceId == source_id` 的 projected row，因而 fail-closed 退出。
     - 非其他阻斷因素：本次失敗**不是** auth、billing、VM 基礎設施、Docker image ID 正規化、Source 對外網路 egress、paper-signal-producer 啟動異常（container startup 與 healthy 均通過），亦**不是**週末 session freshness 判別問題（Taiwan official refresh 與 market session freshness 均順利通過 admission）。此為 Agora projection 綁定驗證之功能面 gate 阻斷。
   - **補償執行與基準恢復**：
     - `dev-deploy-compensation` 步驟成功執行，將 VM 上的服務與靜態資產完整回滾至先前的 accepted baseline：FE `c230fc76bef78fc297135152f2acba690314bb9d` 與 BFF `dcb14231d29f08f1646a4ee962b83fd2d4b67560`。
     - 目標 candidate `8f8383b/bd03c86` 未通過 promotion，亦未完成 final atomic switch。
6. **最新 hosted 現狀（2026-08-29T23:41:28Z 直接 probe）**：
   - FE `/deployment.json` 仍為 accepted `c230fc7`，宣告 BFF `dcb1423`；
   - 實際 BFF `/bff/version` 為 `dcb1423`（build time `2026-08-29T23:28:55Z`）；`/readyz` ready，Lifecycle reader/writer 為 PostgreSQL，checkpoint 與 source high watermark 同為 `7,637,654`，backlog/quarantine 為 0。
   - 因此目前 **hosted pair 保持自洽一致但為已恢復之舊版**，最新 candidate 處於 **source fixed / runtime unverified** 狀態，第三遍 current UI 驗收持續保留未簽收。

### 3.6 舊 closeout 文件與原始 workflow 不一致

倉庫內 `PFG-HOSTED-CURRENT-DEV-CLOSEOUT-20260828` 文件宣稱 L12、Agora、Management、
Management AI、paper與rollback全數passed。但它的producer run `33146133499`原始job記錄中，
authenticated BFF smoke、live dry-run write、exact-pair pre/post proof與PINT write proof全部是
`skipped`；`33144815565`也skip掉canonical paper lifecycle與evolution probes。

這些JSON只有`passed_count`總數，沒有逐journey command ID、terminal state、durable readback ID、
HAR/DOM與skip來源。因此它們不是獨立可重現的功能證據，且被原始workflow step直接
反證。本報告保留它們作為false-green/document-drift evidence，不繼承其`PASSED`結論。

## 4. 第一遍結果：需求／SA／SD -> current code

### 4.1 已確認修正的舊落差

| 舊落差 | Current code direct evidence | 判定邊界 |
|---|---|---|
| Lifecycle JSON 是 live authority | Compose 與 hosted BFF 已使用 PostgreSQL writer/reader；checkpoint caught up；legacy JSON `accepted_reader=false` | code/runtime 已修；仍待完整 promotion 完成與 restart probe |
| Interaction HTTP 202 卻同步跑 provider | submit 只建立 durable queued request；`agora-interaction-worker` 是獨立 Compose service，具 claim/lease/retry/outbox | code 已修；待 hosted submit→terminal→reload |
| Bearer session 用 native relative EventSource | bearer path 改用 `fetchSse`，detected BFF base；cookie 才使用 EventSource | code 已修；待 authenticated hosted stream |
| Agora performance list route 404 | BFF 已有 owner-scoped `/bff/agora/trading-room/performance-attribution/by-strategy` 與 isolation/pagination tests | code 已修；待 exact hosted response/UI |
| Persona List/Fleet/detail identity 不一致 | `PersonaDirectorySnapshot` 只讓 admitted identity 可導航；契約測試要求 list/fleet set 相同且每列 detail=200 | code 已修；待 hosted count/detail sweep |
| Trading Room 固定 `lens-A..E` | current FE 不再含固定 lens；以 BFF 回傳的 `candidatePoolId` 控制 candidate flow | code 已修；待 real pool journey |
| Source Management 只有 read table | current BFF/FE 已有 catalog/detail/add-disabled/validate/canary/enable/disable/degrade/resume/schedule/replace/retire、runs、receipts | code 已補；hosted write/readback 未驗收 |
| Management loop truth 多個候選 authority | `management_read_models/loop_truth.py` 定義唯一 join：static catalog + current controller records，固定 12 rows | code 架構已收斂；待 current hosted UI readback |
| Runtime Manager 雙 service owner | tracked/current tree 只有 `services/runtime-manager` 可建置 service；舊 `services/execution/runtime-manager` 不存在 | 雙 service 已刪；歷史文件引用不是 live owner |
| Source Ingestion 巨型 entrypoint | `services/source_ingestion/main.py` 345 lines、1 route decorator，預設 `reconcile_only` | composition 已顯著收斂；manual bounded pull 仍待驗收 |
| normalized route collision | current route uniqueness/no-shadowing focused tests通過 | 只證明 collision；不證明 route body 已離開 main |
| ReadSurfaceStore God class | class 已刪；production scanner只允許兩個窄 helper | deletion 已完成；慢速 scanner 問題另列 |
| Source provider/search/memory只有設計 | TDCC、TAIFEX、StockTwits、FMP alpha adapter、as-of/hybrid/structured-alpha search、reviewed research memory writeback均有current code；43個非HTTP domain tests通過 | source-level已補；FMP無credential、current hosted pull/search/memory chain仍未驗 |
| Source state/snapshot/paper outbox無界 | Source controller projection已改bounded，新增canonical latest market snapshot endpoint；paper lifecycle outbox ack後會移除pending payload | 舊巨型state根因已修；仍待current hosted resource/readback proof |
| Formula/Activity/Paper/Postmortem完全用前端合成資料 | Formula不再建假backtest；Activity改讀SSE；Paper/Live讀Management telemetry；Postmortem不再固定3筆 | 前三個synthetic路徑已移除；Postmortem仍是Incident timeline派生，不是canonical owner |
| Management AI UI action只是宣告 | `openDrawer`、`focusPanel`已接allowlist handler；`runBffAction`已接`HighRiskConfirm`與receipt readback tests | source-level已修；hosted provider answer與domain terminal readback仍未驗 |
| Workshop提交依賴browser tenant hint | `5ffee3d`新增`resolveSubmissionTenant()`：browser hint缺失時採BFF resolver tenant，矛盾hint仍拒絕；current typecheck與52個Workshop tests通過 | source-level已修；附帶evidence仍自列hosted acceptance pending，故不提升hosted判定 |
| Compose bare image ID被deploy gate全數拒絕 | `b3b26a7`只正規化well-formed bare 64-hex，保留invalid/multiple/mismatch拒絕；end-to-end fixture覆蓋bare digest | source-level已修；尚待新promotion證明VM實際receipt不再false-negative |
| Source bounded refresh沒有正式手動入口 | `b3b26a7`新增單一boolean workflow入口，固定allowlisted connector/hosts、1 tick、concurrency 1、100-record bound及1800秒timeout；預設關閉 | 設計與source contract已補；current hosted effect/readback/reconcile-only restoration未驗 |
| Source active-symbol frontier 重啟丟失 | `f227360`（PR #5412）在 controller worker 加入 explicit frontier recovery，重啟時可由既有 SourceRecord 恢復 checkpoint | source-level 已修；待 hosted deployment 驗證 |
| Active paper symbol snapshot alias 與 official refresh 優先序 | `9e9ab33`（PR #5413）新增 snapshot alias read view 與 active paper symbol 的 Taiwan official refresh 優先路由 | source-level 已修；待 hosted deployment 驗證 |
| Active symbol 官方收盤歷史不足導致 producer 阻斷 | `44895a2`（PR #5415）引入 2 個月有界歷史獲取、distinct-day snapshot 去重與 >=2 distinct closes 診斷門檻 | source-level 已修；待 hosted deployment 驗證 |
| 週末與例假日官方收盤價被 flat 24h 判為過期 | `394eb05`（PR #5416）實作 governed Taiwan market-session freshness 規則，承認週末/例假日的週五官方收盤有效性 | source-level 已修（歷史 PR #5416 套件 197 passed；最新 focused verifier 套件為 144 passed、2 failed、1 skipped，2 個失敗為 verifier fixture 在 stage_04 遭 official lineage fail-closed 拒絕，待 exact hosted promotion 驗證） |

### 4.2 Current direct GAP

| ID | Severity | 功能／架構落差 | Current direct evidence | 正確完成邊界 |
|---|---:|---|---|---|
| OP-G01 | P0 | Agora research可產生假`real` candidate truth | Compose的orchestrator/gateway將production adapters固定為`false`、service default為`stub`；但BFF `DefaultAllowlistedAdapter` 不呼叫backend，直接組artifact/evidence並預設`provenance=real` | dev至少一個admitted bounded/offline real adapter要有真backend receipt/result；目前這條改標simulation/unavailable，禁止做成candidate truth |
| OP-G02 | P0 | Agora PerformanceSuggestion 無 production wiring | `PerformanceSuggestionProducer(...)` 只出現在 tests；production tree 沒有 caller | telemetry/paper/risk outcome 自然觸發 producer，suggestion durable readback，UI refresh 顯示相同 ID |
| OP-G03 | P0 | current source FE/BFF尚未成對部署 | `23:41Z` 直接確認補償已維持自洽舊 pair `c230fc7/dcb1423`（PostgreSQL checkpoint `7,637,654`）；post-cutoff promotion run `33280168821`（`8f8383b/bd03c86`）因 Agora projection 綁定檢查在 gate 失敗並補償恢復，current source 尚未成功 promotion 至 live VM | 用 current admitted FE/BFF 建立 atomic candidate；manifest、BFF version、served bundle 與 candidate evidence 四者一致後再驗收 |
| OP-G04 | P0 | release gate 可把實際失敗包成綠燈 | run `33256001457` 的Management hosted log因缺`BFF_AUTH_TOKEN`失敗、route-load也失敗，但workflow success；舊closeout綁定的`33146133499`又skip七個auth/write/exact-pair steps卻生成全passed摘要 | 必要hosted/auth/write/readback step的fail/skip必須讓functional acceptance fail；read-only smoke另列；證據必須保存逐ID/HAR/terminal readback |
| OP-G05 | P1 | auth readiness 仍同步依賴 OpenClaw latency | `ready` 已只看 `authReady`，但 route 還是同步 `_safe_provider_readiness()` → provider network probe | auth endpoint只做本地 session/tenant/role；provider readiness由獨立、可降級 endpoint/cache 取得，不阻塞 protected render |
| OP-G06 | P0 | Management 非 Persona generic CRUD 未接 durable owner | `createEntity.ts` 的非 Persona create/update/delete在非 strict使用 `writeOverlay`；strict live則拒絕；無 durable mutation | 每個可見 CRUD control不是接 canonical BFF owner並 readback，就是從 production UI移除／明確標 unavailable |
| OP-G07 | P1 | frontend production graph仍可達 seed/mock/overlay | 非test TS/TSX中116個files有case-sensitive `mock\|seed\|writeOverlay`（包含明示mock modules/i18n，不全等於bundle reachability）；但`bff-v1/index.ts`匯出`writeOverlay`、`createEntity.ts`/`ObjectListPage.tsx`可從production flow動態import，且`writeOverlay`直接import`@/mocks/seed`。29個files使用`NonProductionActionButton`、14個files使用`runActionSafe` | live production bundle graph不再可達seed/overlay；test/demo graph由明確入口注入；不以純文字count代替import-graph proof |
| OP-G08 | P1 | BFF composition cleanup未完成 | `main.py` 68,054 lines、453個 `@app.*` decorators；既定 SD 要求只做 app/middleware/lifecycle/router composition | domain route bodies移至 owner routers；main縮成可審查 composition root，architecture test直接限制 route decorators/責任 |
| OP-G09 | P1 | Agora routers跨域 import 私有 store/helper | Trading Room import Workshop `_build_readiness_assessment`；Interaction/Decision/Research/Trading Data import其他 router `_get_store` | store/service由 composition root注入；任何 router不 import另一 router私有 symbol |
| OP-G10 | P2 | generic legacy action adapter仍是 dead compatibility code | `_execute_bff_action_adapter` 不在 production `_EXECUTORS` mapping，只被 tests/monkeypatch引用；正式 command types走 domain adapter registry | caller proof後刪除 function與只為它存在的 legacy tests，避免誤接回 admitted-only假完成 |
| OP-G11 | P0 | 十二循環完整 deployed proof不是預設執行 | research/human-learning/runtime/cross-loop E2E均以環境變數 opt-in，正常 pytest可全部 skip | exact candidate執行至少一條自然 stimulus 的 12-loop proof；每 loop有 owner receipt、terminal/readback、UI same ID |
| OP-G12 | P1 | current Source Management仍缺 hosted effect proof | BFF/FE與local tests存在，但沒有 exact current add-disabled→validate→manual canary→readback→reconcile-only evidence | 用一個測試 source instance完成全流程；canary有界；結束後 controller mode及network activity證明恢復 reconcile-only |
| OP-G13 | P1 | synchronous FastAPI `TestClient`驗收工具會死鎖 | current `.venv`為FastAPI 0.139.2/Starlette 1.3.1/httpx 0.28.1，requirements未鎖版；Source HTTP test在第19案停於AnyIO portal，單裝`httpx2`仍一樣。同app透過`httpx.AsyncClient(ASGITransport)`的`/livez`與data-sources均回200，public hosted負向route也快速回401 | 鎖定相容依賴並將ASGI tests改為async transport；加入硬timeout regression。此項列為驗收工具gap，不誤判成產品route failure |
| OP-G14 | P1 | current Management/Agora authenticated hosted UI仍無有效證據 | integration artifact只有 anonymous auth-boundary；0個 required BFF responses也被標 core complete | 以短效 dev-login session跑 desktop Management與Agora route matrix，保留 network/console/DOM與durable IDs |
| OP-G15 | P1 | research adapters與產品宣稱不一致 | 多個 research backend預設 `stub`／`deferred_prep_only`，但 UI journey期待 real research/candidate | catalog/UI逐 adapter顯示 `stub/deferred/real`；只有 non-stub terminal result可進產品 candidate truth |
| OP-G16 | P0 | deployment lease與rollback共用同一個脆弱遠端依賴 | 一次GitHub API timeout同時使長部署失敗、rollback lease失敗；補償exit 78卻在step summary顯示success | lease heartbeat採有界retry/grace而非單次遠端timeout即殺部署；rollback已有本地sealed authority可獨立執行；補償失敗必須是顯式red且自動驗證served pair |
| OP-G17 | P0 | Registry→Deployment→RuntimeBinding的executable projection仍非自然產生 | `verify_deploy_authorities()`驗證canonical artifact identity/checksum，但不回傳`object_store`/loader projection/`market_data_policy`；deployment adapter只原樣轉送`deploy_context.metadata`，fleet再於下游拒絕缺欄位binding。舊closeout tests沒有檢查這段投影 | Registry authority以同一artifact/version/checksum產生immutable loader projection與market policy，DeploymentPlan持有引用，Runtime Manager驗證後才建active binding；不接受caller任意metadata |
| OP-G18 | P1 | Management Postmortem仍無canonical read owner | `PostmortemLibrary.tsx`不再固定3筆，但由Incident timeline中`[postmortem]`字串臨時產生`pm_<incident>`，沒有讀Postmortem authority的ID/revision/status | 由postmortem owner提供list/detail契約；Incident只保存`postmortem_id`引用，UI reload後讀同一durable object |
| OP-G19 | P0 | Source-to-Agora Read Projection 綁定與身份同步在部署門禁失敗 | 在 run `33280168821` 中，bounded manual Source refresh 成功產出 active paper symbol（2330.TW）的 Taiwan official receipt 後，部署門禁於 `scripts/deploy_nonprod_vm.sh:1218` 驗證 Agora read projection 時，因 projection 內容未綁定新 receipt/run/source（`Agora projection does not bind the new receipt/run/source`）而 fail-closed 退出並觸發補償回滾。觀察事實與推論邊界區分：直接觀察事實為驗證腳本讀取 `projection_path` 時未找到符合 `connectorId == connector_id`、`ingestRunId == run_id` 且 `sourceId == source_id` 之 projected row；非 auth、非 billing、非 VM/Docker 故障、非 Source egress、非 paper-signal-producer 啟動異常，亦非 session freshness 問題 | Agora / Source Ingestion Integration owner 排查並修正 Source refresh 到 Agora read projection 的寫入與同步機制，確保 deploy script 讀取到最新 receipt/run/source 綁定記錄 |
| OP-G20 | P0 | paper-signal-producer 運行時健全度與完整訊號→訂單生命週期尚未在 live promotion 閉環 | image ID 正規化（`b3b26a7`）、frontier recovery（`f227360`）、snapshot alias（`9e9ab33`）、2-month history / min closes（`44895a2`）及 Taiwan weekend session freshness（`394eb05`）均已在 source 完成；在 run `33280168821` 中 container 啟動與 session freshness 驗證通過，但因部署在 Agora projection 驗證失敗並補償回滾，paper-signal-producer 及其完整的 signal→order/fill/position/heartbeat 鏈路尚未在 live VM 完成 atomic switch 與真實運行驗收 | Execution / Lean Runtime owner 在部署門禁修復後，以最新 source candidate 執行 nonprod deploy，證明 producer 進入 healthy、完成 signal→order/fill/heartbeat readback 閉環 |

### 4.3 架構簡化 SA/SD 逐項驗收

| SA/SD exit criterion | Current result | 判定 |
|---|---|---|
| normalized route collision = 0 | focused route scanners通過 | `PASS`（只限此條） |
| BFF main是composition shim、無domain route bodies | 68,054 lines / 453 decorators | `FAIL` |
| ReadSurfaceStore與production fixtures移除 | class deletion scanner通過；但 FE production graph仍有seed/overlay | backend `PASS` / product graph `FAIL` |
| FE UI→domain client→transport acyclic且無legacy mock reachability | bff-v1內部cycle test通過，但只掃該子樹；production create/list paths仍可import `writeOverlay`→`@/mocks/seed` | `FAIL` |
| Management loop truth只有catalog+controller join | canonical `loop_truth.py`存在；current hosted UI未驗 | `PARTIAL` |
| only one Runtime Manager service | current tracked tree只有一個service owner | `PASS` |
| Workshop單一store且無private cross-router imports | router已拆小，但private imports仍存在 | `FAIL` |
| Source main只是composition root | 345 lines / 1 route decorator | `PASS`（current static boundary） |
| dead NL/stub/compatibility surfaces刪除 | generic adapter、frontend seed/overlay與大量non-production controls仍在 | `FAIL` |
| exact entrypoint/health/revision gate | BFF exact+ready但FE/manifest不一致；gate能容忍functional與rollback fail/skip | `FAIL` |

### 4.4 Source Ingestion / Source Management 逐能力對照

| 能力 | Current code / 本次直接證據 | Hosted / UI 邊界 | 判定 |
|---|---|---|---|
| definition/instance/desired/observed合併read model | current contracts、command store與BFF projection存在 | current exact authenticated list/detail未讀 | `PARTIAL` |
| add-disabled / validate | domain engine正向、unsupported adapter、network-free validation tests通過 | current hosted command ID與reload未驗 | `PARTIAL` |
| bounded canary | domain test驗max records/bytes/timeout、partial search timeout與idempotency | 舊`dcb1423`功能記錄有單次TWSE pull；current source pair未部署，沒有同一current證據 | `PARTIAL` |
| enable/disable/degrade/resume | lifecycle、revision conflict、disabled/retired semantics均有domain tests | exact hosted UI effect/readback未驗 | `PARTIAL` |
| schedule/universe/replace/retire | BFF commands與UI dialog、dependent migration acknowledgement、typed `RETIRE`存在 | hosted durable receipt未驗 | `PARTIAL` |
| runs/receipts/watermark/DLQ | read routes、runs/receipts tabs與typed unavailable/degraded state存在 | current source owner的authenticated current readback未驗 | `PARTIAL` |
| usage/cost/quota/dependent consumers | Management table有對應欄位與source-level tests | 沒有current provider run可驗數值新鮮度 | `PARTIAL` |
| provider coverage | TDCC、TAIFEX、StockTwits、FMP alpha等adapter已實作；FMP無credential時fail closed | evidence是worker harness；非current hosted source instances/freshness，alternative provider仍未開通 | `PARTIAL` |
| as-of/hybrid/structured-alpha search | 25個current non-HTTP search/memory tests通過；time/PIT/type/entitlement負向存在 | current SourceRecord→index→UI citation未驗 | `PARTIAL` |
| reviewed research memory writeback | outbox/worker、license restriction、idempotency與retrieval influence code存在 | 實際research上游仍有假`real`，current durable memory ID未驗 | `PARTIAL` |
| normal dev operating mode | Compose default `reconcile_only`，只允許手動bounded one-shot | 失敗promotion沒有執行Source runtime probe；不宣稱current hosted closure | `PARTIAL` |

本次current domain驗證為18個Source command/schema/concurrency tests加25個search/alpha/memory
non-HTTP tests通過。Source service/BFF的sync `TestClient` HTTP tests會卡在AnyIO portal；改用
async ASGI transport直接對current BFF呼叫時，`/livez`與`/bff/management/data-sources`都回200。
所以Source的結論是「功能骨架與domain行為已存在，current hosted effect chain尚未簽收」。

### 4.5 Pantheon 十二循環逐環重驗

| Loop | Current owner / 已存在功能 | 直接剩餘落差 | 判定 |
|---:|---|---|---|
| 1 Source | single controller、bounded state/readiness、manual job、latest market snapshot、SourceRecord、frontier recovery（`f227360`）、min-closes history（`44895a2`）、TW session freshness（`394eb05`） | current exact one-shot→record→自動恢復reconcile-only未驗 | `PARTIAL` |
| 2 Distillation | SourceRecord commit admission、durable queue、catch-up、Registry draft | 沒有本次Loop 1 ID自然進queue並terminal readback | `PARTIAL` |
| 3 Alpha Replication | reviewed admission、ExperimentTask/Run、controller observation | current exact review→run→next receipt未驗 | `PARTIAL` |
| 4 Persona Teaching | session/event、preview/eval worker、persona target、consult handoff | current exact user command→evaluation/target/readback未驗 | `PARTIAL` |
| 5 Agora Interaction | durable interaction request/outbox、independent leased worker、dataset handoff | research下游可產生假`real`；hosted terminal interaction/reload未驗 | `FAIL` |
| 6 Imitation | durable handoff scheduler、candidate、Research HTTP intake | current deployed scheduler→candidate→ExperimentRun未驗；legacy discovery caller仍需最終刪除證明 | `PARTIAL` |
| 7 Consultation | request/memo/handoff、executor、provider adapter、Governance sink | current real contribution→governance receipt未驗 | `PARTIAL` |
| 8 Deployment | approval、plan/outbox、Runtime Manager、authority verifier | executable loader/market projection未由canonical Registry自然帶入（OP-G17） | `FAIL` |
| 9 Paper Execution | source snapshot endpoint、artifact-required producer、fleet、signal/order/fill/position/heartbeat、snapshot alias（`9e9ab33`）、TW session freshness（`394eb05`） | 上游binding未閉合；current paper lifecycle probe在部署失敗後skipped | `FAIL` |
| 10 Reconciliation | telemetry ingest/consumer、reconciler、DriftReport/IncidentCase | 沒有本次真fill/heartbeat產生的同ID incident/recovery | `PARTIAL` |
| 11 Evolution | threshold/daily producer、postmortem/evolution/dispatch workers | 沒有本次incident→postmortem→decision→receipt；Management Postmortem owner又不canonical | `PARTIAL` |
| 12 Loop Truth | common loop-control store、functional worker observations、pure 12-row projection | public route只證明401 boundary；current authenticated Management同ID讀回未驗 | `PARTIAL` |

舊L12 evidence可支持component曾執行，不能補這張表的current exact空格。特別是
closeout中的`passed_count: 12`沒有12組stimulus/terminal/next-consumer IDs，且producer run實際
skip相關hosted steps，故不採信為12/12 closure。

### 4.6 Management 系統與操作 UI 完整群組盤點

| UI / 系統群組 | Current source 狀態 | 不能簽收的原因 | 判定 |
|---|---|---|---|
| auth / shell / navigation | `/auth`、protected shell、canonical redirects與error boundary已mounted | served FE是舊SHA；沒有current authenticated desktop nav sweep | `PARTIAL` |
| Cockpit / Trading Pulse / Operations | live adapters、degraded/empty states存在 | no exact DOM/network/read-model sweep | `PARTIAL` |
| Persona List / Fleet / Detail / Onboarding | canonical directory snapshot與identity tests已補 | current hosted row-count/detail-200/reload未驗；non-strict onboarding仍可落overlay | `PARTIAL` |
| Human Inbox / Gate detail | strict-live/no-seed read路徑與detail route存在 | current command terminal/readback未驗 | `PARTIAL` |
| Performance / Rankings / Governance decisions | canonical center routes與domain adapters存在 | current underlying telemetry/decision identity未驗 | `PARTIAL` |
| Loops / Execution / Optimization / Research / Sentinel | canonical 12-row projection與worker observations存在 | current authenticated loop truth與intervention effect未驗 | `PARTIAL` |
| Data Sources | catalog/instances/runs/receipts、全lifecycle controls、usage/cost/quota/dependencies已mounted | current exact bounded write/readback未驗 | `PARTIAL` |
| Strategies / Personas / Capital / Ranking / Rebalance | list/detail routes完整；多數重要actions有domain adapter | generic create/update/delete除Persona外仍是overlay-only或strict拒絕 | `PARTIAL` |
| Evolution / Experiments / Artifacts | list/detail與部分governed actions存在 | no current exact mutation→terminal→reload | `PARTIAL` |
| Deployments / Runtimes / Jobs | list/detail/actions存在 | executable binding projection不閉合；hosted action proof未跑 | `FAIL` for executable journey |
| Evidence / Trade Journeys / Lineage | list/detail、SSE/readback adapters存在 | release route-load baseline曾落入auth shell timeout；current exact未驗 | `UNVERIFIED` |
| Incidents / Postmortems / Evolution Journal | incident actions與journal routes存在 | Postmortem Library是incident timeline派生物，無canonical ID owner | `PARTIAL` |
| Tools / MCP / Skills / Channels | list/detail routes存在，無owner的actions多數誠實disabled | 這些頁不等於完成CRUD；current hosted reads未驗 | `PARTIAL` |
| Formula / Skill Sandbox studios | 已移除假success，沒有runner時顯示unavailable/disabled | 無governed runner，故不能宣稱backtest/skill execution可用 | `PARTIAL` |
| Management AI | conversation persistence、NL route、drawer/panel/action UI wiring已有current source | current OpenClaw answer、SSE、confirmed domain terminal/readback未驗 | `PARTIAL` |

`App.tsx`中上述Management主路由全均實際mounted；但「有route」只是可達前提。在exact
served FE、authenticated network、canonical data與reload readback補齊前，不把mounted頁數當完成率。

### 4.7 Agora 系統與操作 UI 完整旅程盤點

| Agora 環節 | Current source 狀態 | 直接落差 | 判定 |
|---|---|---|---|
| standalone shell/auth | `/agora` 有獨立desktop shell，三大頁及detail route mounted | current served FE非`5ffee3d`，未跑authenticated browser | `PARTIAL` |
| Strategy Workshop | list/detail、message/reconstruct/readiness/card契約存在 | hosted message→durable reconstruction ID→reload未驗 | `PARTIAL` |
| Interaction worker | HTTP submit只queue，獨立worker有lease/retry/outbox | current worker health、terminal result、SSE與reload未驗 | `PARTIAL` |
| Research plan/run | durable plan/stage/outbox/dispatcher存在 | default adapter無真backend卻標`real`；service/gateway production adapters又disabled | `FAIL` |
| Candidate pool / Trading Room | fixed lens IDs已刪，使用backend `candidatePoolId`；workspace/review routes存在 | 沒有真research output可供自然candidate與durable decision | `FAIL` end-to-end |
| Decision / learning handoff | decision stores、policy handoff/drainer存在 | normal UI decision→policy/consultation receipt未驗 | `PARTIAL` |
| Performance attribution | owner-scoped route與FE page契約已補 | `PerformanceSuggestionProducer`無production caller，無outcome自然建議 | `FAIL` |
| SSE/live status | bearer改用detected BFF base + `fetchSse`，cookie才native EventSource | current authenticated stream與reconnect/reload未驗 | `PARTIAL` |

本次Agora focused FE tests只證明route/component contract與current source沒有固定lens等已知回歸。
因research truth在source層已直接失敗，即使將三個頁面全部render成功，Agora仍不能標`PASS`。

## 5. 第二遍結果：current code -> runtime／deployment

| Product owner | Current code truth | Runtime evidence | Result |
|---|---|---|---|
| Lifecycle projector | PostgreSQL canonical controller；JSON只作legacy recovery | `23:41Z` hosted `dcb1423` checkpoint=`7,637,654`=high watermark、backlog/quarantine 0、accepted reader=false for legacy | `PASS` for restored old pair；current promotion restart/persistence step被skip |
| Runtime Manager | one service owner與Compose service；executable fleet會fail closed | BFF `/readyz` dependency `ok`，但正常deployment不產生完整loader/market projection | `FAIL`（Loop 8→9） |
| Source controller | reconcile-only default、bounded canary API、frontier recovery（`f227360`）、min closes（`44895a2`）、TW session freshness（`394eb05`） | exact hosted Source mode/action evidence尚未產出；run `33280168821` 證實 bounded Taiwan official refresh 成功產出 receipt，但在 deploy gate 的 Agora projection 綁定驗證（`scripts/deploy_nonprod_vm.sh:1218`）失敗並觸發補償回滾 | `PARTIAL` |
| Interaction worker | durable request/outbox、independent worker service | exact deployment step尚未驗 worker health/terminal run | `PARTIAL` |
| Research orchestrator/gateway | real adapter gate存在 | Compose明確production adapters=false、default stub；BFF default adapter又自建假`real` artifacts | `FAIL` |
| Agora performance | route/store/producer class存在 | producer沒有production caller | `FAIL` |
| Management reads | domain ports/routes、canonical empty/degraded定義存在 | current exact authenticated reads尚未跑 | `PARTIAL` at source / `UNVERIFIED` hosted |
| Management commands | adapter registry多數command有domain owner；generic CRUD無owner時strict拒絕 | current hosted write/readback全被skip | `PARTIAL` |
| Management AI | `/bff/management/nl/ask`走OpenClaw；UI action handlers已接 | current deploy probe skipped；無current provider/terminal readback | `PARTIAL` at source / `UNVERIFIED` hosted |
| FE | `e205643`基線typecheck與175 focused tests通過；`5ffee3d`增量通過52個Workshop tests；`bd03c86`包含PR #694 closeout manifest | 補償後served `c230fc7/dcb1423`一致但落後current；run `33280168821` 在 gate 階段中斷並補償恢復，未切換至 `bd03c86` | `FAIL`（current delivery） |

## 6. 第三遍結果：hosted desktop UI -> BFF -> durable truth -> UI

本遍只接受exact current pair。Promotion半切換期間不執行UI判定；補償後雖恢復exact舊pair，
它也不能代表current source。現有artifact只能得出：

| Evidence | 實際證明 | 沒有證明 |
|---|---|---|
| anonymous browser probe | public shell可render、auth boundary存在、無匿名write | 登入後任何Management/Agora資料或action |
| desktop Playwright 50 expected / 13 skipped | fixture/local UI案例沒有unexpected failure | hosted authenticated truth；13個skip不能算pass |
| hosted Management log | 因缺短效token而直接失敗 | 任何Management route成功 |
| route-load baseline | `/management/evidence`被導向auth shell後timeout | Evidence API/頁面可用 |
| release summary `overall=warn` | workflow把缺證據降級成warning | functional closure |

因此目前第三遍沒有任何current product surface可標完整 `PASS`。current exact pair完成後必須至少執行：

1. desktop login/shell/nav與console/network error sweep；
2. Management 主要 read routes、12-loop truth、Persona list/fleet/detail identity；
3. Source add-disabled、validate、一次 bounded canary、disable/reconcile-only、reload；
4. Workshop message→reconstruction→interaction worker→research→candidate pool；
5. Trading Room decision→DecisionEvent→performance attribution/suggestion→reload；
6. Management AI provider answer→一個paper-only domain action→terminal receipt→reload；
7. paper lifecycle、reconciliation、evolution與UI同 identity readback。

## 7. 測試證據的正確解讀

| Evidence | 可以下的結論 | 不可下的結論 |
|---|---|---|
| typecheck/build | source可編譯／型別契約基本一致 | hosted UI可操作 |
| unit test | 被隔離的function/component符合fixture | production wiring存在 |
| contract test | caller/route/schema約定一致 | runtime owner已啟動、資料新鮮 |
| in-process integration | 同process的store/router鏈成立 | Compose/network/restart成立 |
| deployed API proof | exact owner可達、寫入/readback成立 | UI真的觸發同一流程 |
| hosted browser journey | 使用者路徑與network成立 | 下游durable/consumer一致，除非同時保存ID/readback |
| task/PR/CI status | 工作流或變更已完成 | 產品功能完成 |

本次在`e205643`基線跑過typecheck及六個Vitest files共175 tests；前端`dev`前進至
`5ffee3d`（及後續 `bd03c86`）後，再以exact current source跑typecheck及Strategy Workshop 52 tests，均通過；
變更檔ESLint為0 errors、2個既存fast-refresh warnings。這些只提高source-level confidence，
不提升任何缺 hosted proof 的surface到 `PASS`。大量React `act(...)` warnings另記為測試噪音，
也代表test harness尚不夠乾淨，但不直接判定產品功能失敗。

本次current backend在 `394eb05` 基線的source-level結果：

- route no-shadowing、normalized route uniqueness 與 development route boundary 共 23 tests 通過；
- Source reconcile-only contract 1 test通過；
- Source command/schema/concurrency 18個非HTTP tests通過；
- governed search、structured alpha、reviewed memory 25個非HTTP tests通過；
- 歷史 PR #5416 驗證時 paper signal producer、market snapshot admission、Taiwan official connectors、controller worker、Agora operational readiness、deploy diagnostics contract 核心套件共 197 passed、1 skipped（live opt-in）；
- 最新 current dev 執行 focused verifier 套件（`scripts/test_verify_agora_*.py`、`test_paper_signal_producer.py`、`test_paper_fleet_reconciler.py`、`test_operational_readiness.py`、`test_source_ingest_deploy_diagnostics_contract.py`、`test_taiwan_official_connectors.py`）之實際結果為 **144 passed、2 failed、1 skipped**；
- **2 個 failed 之真因與邊界分析**：失敗案例為 `scripts/test_verify_agora_operational_readiness.py` 中的 `test_verifier_full_positive_run` 與 `test_verifier_fails_on_order_authority_claim`。兩者皆在 `stage_04_bounded_recovery_sequence` 執行時拋出 `[bounded_recovery_sequence] Market snapshot admission failed: market_input_non_official_lineage (snapshot lineage is not an official TWSE/TPEx source)`。此為 verifier 內部測試 fixture 構造之 mock market snapshot lineage 未帶有 `394eb05` 所要求的 `tw-official:` / `tw-twse-tpex-official-market` 官方標記，因而被 `market_snapshot_admission.py` fail-closed 拒絕。此結果證明了 Taiwan official lineage 門禁確實嚴格生效，但也揭示了 standalone verifier 測試套件本身在 current dev 仍待更新 fixture lineage；
- current BFF以async ASGI transport呼叫`/livez`與authenticated data-sources list均回200。

同一批的sync `TestClient` 在Source HTTP第一個case即死鎖；因此沒有把後續未終止
tests算failed或passed。這個分類很重要：產品ASGI route直接可執行，但現有測試客戶端
無法作可靠驗收工具（OP-G13）。

## 8. 修正優先序（功能可運作優先）

1. 排查並修正 Agora projection 綁定機制（OP-G19），確保 bounded manual Source refresh 後 Agora read projection 正確綁定新 receipt/run/source；並以最新 source candidate（含 Compose bare image ID 正規化、active-symbol frontier recovery、2-month history / 2-close min closes、Taiwan market-session freshness 及 Agora projection 修復）執行 nonprod deployment，確認 VM 部署成功、paper-signal-producer 進入 healthy、完成 signal→order/fill/heartbeat readback 閉環（OP-G20、OP-G03）。
2. 修正lease/rollback/compensation單點與false-green，再atomic promotion一組current FE/BFF/manifest；目前舊pair雖已恢復，仍不是current驗收目標。
3. 將Agora BFF假`real` adapter改為simulation/unavailable，再接一條admitted bounded、non-stub research lane與real candidate。
4. 完成Registry→DeploymentPlan→RuntimeBinding的canonical executable projection，再跑paper lifecycle。
5. 將PerformanceSuggestionProducer接到paper/telemetry/risk outcome consumer。
6. 跑Source Management bounded one-shot journey並自動回reconcile-only。
7. 跑Agora完整interaction/research/candidate/decision/performance durable journey。
8. 跑Management read/command/AI與十二循環cross-loop hosted journeys，並將Postmortem改接canonical owner。
9. 完成BFF main、Agora private imports、FE seed/overlay、dead adapter的caller-backed刪除。
10. 修正sync TestClient/dependency組合，將需要ASGI的tests改成async transport並加入硬timeout。
11. 重跑三遍對照；未取得direct evidence者保留`UNVERIFIED`，不以task/PR/test數量補空白。

目前可下的產品級結論是：**舊版BFF process、Lifecycle PostgreSQL projection與列出的核心依賴在補償後可達，舊FE/BFF pair也已恢復一致（PostgreSQL checkpoint `7,637,654`）；image identity false-negative、frontier recovery、min closes 與 Taiwan session freshness 已在 current source 完成修復（歷史 197 passed；最新 focused verifier 144 passed / 2 failed / 1 skipped），但在 run `33280168821` 中 Agora projection 綁定驗證受阻（OP-G19）並觸發補償回滾；paper-signal-producer 仍待 live atomic switch 閉環（OP-G20），Agora research與RuntimeBinding又有current source P0，所以不是所有功能正常，也不具備全產品簽收條件。**

## 9. 三遍前後對照方法

本報告最終結論必須經過三個方向，各自獨立留下證據：

### 9.1 第一遍：需求／SA／SD -> 程式與測試

- 將產品 GAP、架構 cleanup SA/SD、Agora SA/SD、Source Management SA/SD 的承諾逐項列出。
- 找到 current canonical owner、route、store、consumer、UI route 與 current test。
- 正向檢查功能存在；反向搜尋 fixture、fallback、duplicate owner、dead/legacy path。

### 9.2 第二遍：程式 -> exact hosted runtime

- 將 current source owner/entrypoint/route 與 Compose、image、served manifest、OpenAPI、health
  和 runtime readback 對照。
- 正向檢查部署使用正確 owner；反向檢查 source 已改但 hosted 未部署、manifest 漂移、
  alternate store 或 inactive worker。

### 9.3 第三遍：hosted UI -> BFF -> durable truth -> UI readback

- 以 desktop browser 走 Management 與 Agora 主要頁面及代表性 read/write journey。
- 正向核對 UI network request、command ID、terminal state、durable readback 與 UI refresh。
- 反向檢查 console/network error、seed/mock/local-only success、undefined/NaN、disabled 無原因、
  route alias 重複 render 及錯誤 ID。

三遍中任一遍缺直接證據，該功能最高只能標 `PARTIAL` 或 `UNVERIFIED`。

### 9.4 三遍實際對照結果

| 對照遍次 | 正向查核 | 反向查核 | 結果 |
|---|---|---|---|
| 第一遍：SA/SD→code | 找到Source commands/search/memory、Agora worker、loop projection、Management UI actions、`5ffee3d` Workshop tenant修正、`b3b26a7` deploy/source recovery、`f227360` frontier recovery、`9e9ab33` snapshot alias、`44895a2` min closes 及 `394eb05` TW session freshness | 搜尋production caller、stub、seed、overlay、private imports、dead compatibility；找到假`real` research、無caller performance producer、generic CRUD overlay、BFF main未拆完 | 舊GAP一部分關閉，20個current GAP保留（OP-G19 與 OP-G20 分立） |
| 第二遍：code→runtime | 逐次對照Compose、entrypoint、public version/readyz、Lifecycle checkpoint（`7,637,654`）與workflow target SHAs | 發現RuntimeBinding投影不完整、production research disabled；run `33280168821` 揭示 bounded refresh 後 Agora projection 綁定阻斷；補償後成功維持舊 pair `c230fc7/dcb1423` 一致性 | current runtime gate `FAIL`；rollback restoration `PASS` |
| 第三遍：UI→durable truth→UI | 檢查current route mount、`e205643`的175 focused tests、`5ffee3d`的52-test增量、舊artifact與public auth-boundary | 逐項查原始workflow與artifact logs；發現authenticated/write/exact-pair steps skipped、hosted logs失敗卻summary passed；run `33280168821` 補償後仍運行舊 pair，current candidate 未切換 | current pair未部署，不以舊pair補驗；全產品sign-off `FAIL` |

第三遍current journey「不跑」不是漏驗，而exact-current定義的必然結果：half-switch時任何UI
成功/失敗都無法歸屬candidate；補償後只能歸屬舊`c230fc7/dcb1423`。正確處置是將P-14/P-15
保留`PARTIAL`，不得以舊版或local UI補成`PASS`。

## 10. 最終簽收狀態

最終結論是：**不是所有功能都正常運作，也不是所有架構簡化都完成。** 已確認
Source Management、Lifecycle PostgreSQL、Agora interaction worker、Workshop tenant submit、Management UI action、Source frontier recovery、snapshot alias、min-closes 歷史獲取、Taiwan market-session freshness 與多項去synthetic
修正存在；但在 run `33280168821` 中，Agora projection 綁定驗證受阻（OP-G19）並補償恢復舊版，paper producer live promotion 閉環（OP-G20）、Agora fake-real research/performance wiring、canonical executable RuntimeBinding、
current hosted delivery、Source/Management/Agora/十二循環durable journeys、Postmortem owner及大型/dead-code
cleanup仍有直接落差。在atomic promotion、第三遍exact desktop journey與durable readback補齊前，
本報告明確拒絕全產品簽收。

## 11. 可重現的主要證據索引

本節列出本報告直接讀取的current owner與runtime證據；它不是用檔案存在代替功能驗收，
而是讓每個結論可回查到source、deployment與journey三層。

### 11.1 Pantheon source owners

- BFF composition與command：`services/control-plane/bff/main.py`、
  `services/control-plane/bff/command_executor.py`、`services/control-plane/bff/command_adapters/registry.py`。
- Agora：`services/control-plane/bff/agora/research/dispatcher.py`、
  `services/control-plane/bff/agora/performance/producer.py`、
  `services/control-plane/bff/agora/interaction/`、`services/control-plane/bff/agora/strategy_workshop/`、
  `services/control-plane/bff/agora/trading_room/router.py`、
  `services/control-plane/bff/agora/operational_readiness.py`。
- Deployment/Paper：`services/runtime-manager/deploy_authority.py`、
  `services/runtime-manager/fleet_desired_state.py`、
  `services/deployment/runtime_manager_dispatch_adapter.py`、
  `services/execution/lean_runtime/paper_signal_producer.py`、
  `services/execution/market_snapshot_admission.py`、
  `services/paper_fleet_reconciler/reconciler.py`。
- Source：`services/source_ingestion/main.py`、`controller_state.py`、`controller_worker.py`、
  `pipeline.py`、`requirement_state.py`、`provider_adapters.py`、`market_data_storage.py`、
  `services/source_ingestion/connectors/taiwan_official.py`。
- Canonical Management reads：`services/control-plane/bff/management_read_models/loop_truth.py`及
  `services/control-plane/bff/ports/read_surface_ports.py`。
- Runtime/deploy entrypoints：`docker-compose.yml`、`.github/workflows/`中的dev promotion流程及
  `scripts/deploy/`的lease、gate-before-switch、`scripts/deploy_nonprod_vm.sh` rollback/compensation scripts。

### 11.2 execute-plans source owners

- Route mount：`src/App.tsx`。
- Generic Management mutations：`src/management/components/write/createEntity.ts`、
  `src/management/pages/ObjectListPage.tsx`、`src/lib/bff-v1/writeOverlay.ts`。
- Source UI：`src/management/pages/DataSourceControlCenter.tsx`及其BFF client/contracts。
- Management AI：Management agent panel、`openDrawer`／`focusPanel` handlers、
  `runBffAction`與`HighRiskConfirm`相關components/tests。
- Agora：`src/agora/pages/strategy-workshop/WorkshopSessionView.tsx`、
  `src/agora/pages/strategy-workshop/submissionTenant.ts`、Trading Room及Performance pages。
- Postmortem：`src/management/pages/phase2/PostmortemLibrary.tsx`。

### 11.3 Runtime與validation evidence

- Public FE `/deployment.json`、BFF `/bff/version`、`/livez`、`/readyz` 於 2026-08-29 各觀察時間點（包含 23:41:28Z 最新 probe）的直接回應。
- GitHub workflow runs `33280168821`、`33272385942`、`33271993547`、`33271922125`、`33268311841`、`33266252692`、`33262293025`、`33260583008`、`33256001457`、`33146133499`、`33144815565` 及 execute-plans `33264640000`、`33263289212` 的 raw jobs/logs；判定以原始 step 狀態為準，不採只列 count 的 closeout 摘要。
- Pantheon PRs 關聯：PR #5409（Audit 盤點主 PR）、PR #5410（`b3b26a7` Deploy recovery & bounded source refresh）、PR #5411（`254d2e7` Source frontier scope recovery）、PR #5412（`f227360` Source frontier recovery）、PR #5413（`9e9ab33` Active symbol snapshot recovery & alias view）、PR #5415（`44895a2` Official snapshot min closes）、PR #5416（`394eb05` Taiwan market session freshness）、PR #5419（`e5b4d10` dev tooling cleanup）、PR #5420（`9ee1e93` delivery recovery）。
- Pantheon focused validation：
  - route no-shadowing、normalized route uniqueness 與 development route boundary 共 23 passed；
  - Source reconcile-only contract 1 passed；
  - Source command/schema/concurrency 18 passed；
  - governed search、structured alpha、reviewed memory 25 passed；
  - paper signal producer、market snapshot admission、Taiwan official connectors、controller worker、Agora operational readiness、deploy diagnostics contract 歷史套件共 197 passed、1 skipped；最新 current dev 執行 focused verifier 套件（`scripts/test_verify_agora_*.py` 等）為 144 passed、2 failed（2 個失敗在 `stage_04_bounded_recovery_sequence` 觸發 `market_input_non_official_lineage` 嚴格拒絕）、1 skipped；
  - async ASGI `/livez`／data-sources 直接呼叫 200。
- execute-plans validation：`e205643` 基線 typecheck/175 focused tests；`5ffee3d` exact source typecheck、Strategy Workshop 52 tests、變更檔 ESLint 0 errors；`bd03c86` PR #694 closeout manifest。

重現時仍須先核對兩個 repository 的 `origin/dev` 及 hosted manifest/version 是否改變；任何 SHA 變動都必須把受影響的 source 與 hosted 層重新跑過，不能沿用本文件的時間點結論。

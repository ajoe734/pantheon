# Pantheon 十二循環最新程式碼 GAP、未開發項目與汰除盤點

日期：2026-08-18

狀態：current code-first gap truth；只供下一階段 SD／execution task 設計，**本文件不建立、
不 materialize、也不 dispatch execution task**

Pantheon 程式碼基線：`origin/dev`
`9aeca3bac97dbda1956b81080570230dead80243`

> 2026-08-20 operator posture override：開發與驗收繼續，dev 的 default owner 只做
> `reconcile_only` 內部 reconciliation，不得常駐對外拉資料。只有明確選取
> `source-ingest-scheduler` bounded profile 的測試，才可在 exact-host allowlist、單一 tick、
> `restart: no` 的限制下手動拉取一次。Loop 1 的 continuous scheduled provider closure
> 因此是 operator-directed hold，不得以恢復常駐外拉方式修復。

再盤點說明：上一版基線 `d6cdaa2e05947afd29e142a1c20e9749f657e442` 到本版之間只有本目錄
文件與索引變更，沒有產品程式碼差異；本次仍重新逐一閱讀 trigger、owner、store、consumer、
Compose 與 E2E，並校正上一版對 Loop 2 與 controller record 的判讀。

Management 前端基線：`execute-plans/origin/dev`
`a1ba152130bab51447892f5f2a36fab1e3fe11c4`

規格真相：`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`

## 1. 範圍與判定方法

本文件只盤點 Pantheon 12 個產品循環與 Management 的 12-loop truth surface。Supervisor、
auto-worker、V2 TaskStore、fleet dispatch、development bridge、review/closeout pipeline 都不是
第 13 個產品循環，也不以它們的 task 狀態判斷產品是否閉環。

本次直接檢查最新程式碼、Compose wiring、目前執行中的 dev containers、authority readback、
測試內容與 checked-in E2E run report。舊 task archive、PR merge、receipt、review markdown 與
catalog 的 `implemented` 欄位只用來找線索，不作成功證明。

每個循環必須在同一個真實 dev 路徑完成：

```text
規格指定的 primary trigger
  -> 目前 Compose 實際啟動的 owner
  -> durable terminal output
  -> canonical authority readback
  -> next consumer 讀到相同 identity
  -> current-dev deployed E2E
  -> Management 顯示相同 runtime truth
```

以下情況均不算閉環：單元測試通過、in-process ASGI、fake provider、tmp store、手動建立下游
物件、預先提供 ID manifest、process `/readyz`、container alive、舊 SHA 的成功報告，或
Management 只顯示 static registry metadata。

## 2. 總結判定

截至本基線，**12 個循環仍不能宣稱全部閉環；以 current-dev deployed proof 與 Management
readback 為簽收條件時，0/12 可完成最終簽收。**

- 5 個循環有直接規格／功能／契約阻斷：Source Ingestion、Strategy Distillation、
  Promotion/Deployment、Capital Pool Execution、BFF Health Monitoring。
- 7 個循環的主要 domain flow 已存在，但缺 current deployed E2E 或被上游阻斷：Alpha
  Replication、Persona Teaching、Agora Interaction Evidence、Human
  Imitation、Consultation、Telemetry/Reconciliation、Evolution。
- 目前 dev Compose 共 51 個 running services；唯一 Docker health 明確失敗的是
  `paper-signal-producer`。
- Runtime Manager 目前有 9 個 active paper `RuntimeBinding`；9/9 都缺
  `metadata.object_store`，producer 因 `artifact_store_missing` 持續 degraded。
- authenticated `/bff/v5/loop-health` 的本次 Management scope 回傳 12 canonical + 1
  composite overlay，但 12 個 canonical rows 全部 `is_live=false`，可見 controller runtime
  record count 為 0，surface 為 `degraded/registry_metadata`。Postgres 實際已有
  `tenant-dev/dev` scope 的 Source 與 Distillation records；BFF request scope 沒有對到同一
  tenant identity，因此是 visibility/identity wiring 缺口，不是這兩個 writer 完全沒寫。

這個結果不表示所有 domain code 都沒做。相反地，8/14 盤點中的多項實作缺口已修掉；現在
的問題是少數真正未完成的 owner contract，加上大量測試／truth surface 尚未跟上。

## 3. 8/14 舊缺口逐項重驗

| 8/14 項目 | 最新程式碼結果 | 本次處置 |
|---|---|---|
| Source scheduler opt-in/one-shot | 已改成 default-on、`unless-stopped`、unbounded owner | 原缺口部分關閉；預設仍是 `reconcile_only`，沒有執行 scheduled provider pull |
| Alpha reviewed admission boundary | Research 已有 create/list/readback API，worker 讀同一 `ReplicationAdmissionStore` | 功能缺口關閉；只缺 current deployed proof |
| Teaching worker plain token/401 | Compose 已提供與 API secret 相符的 service JWT；worker 現在 healthy | 功能缺口關閉；只缺 current deployed proof |
| Agora durable handoff 沒 consumer | shadow scheduler 已先跑 `agora_handoff_drainer` 再 claim/eval | 主排程缺口關閉；舊 direct DB discovery 仍留在 command path |
| Imitation 直接 import Research store | `candidate_experiment_handoff.py` 已只走 Research HTTP intake + exact readback | 功能缺口關閉；舊 docstring 仍誤寫可 direct intake |
| Consultation provider/handoff 未 wiring | Compose 已接 OpenClaw contribution endpoint、Governance handoff sink與 functional health | 功能缺口關閉；現有 E2E 仍只用 fake provider/in-process app |
| Deployment consumer 403 | service token、tenant、owner URLs 已接上；目前 container healthy | 舊身份缺口關閉；新暴露的是 executable artifact projection 契約缺口 |
| Capital 固定 BUY 為 normal default | default 已改為 `CurrentArtifactStrategy`；smoke 只有 explicit profile | 舊 fallback 缺口關閉；RuntimeBinding 沒提供它要求的 artifact/market projection |
| Evolution token 未傳 | Postmortem、Evolution API、daily/threshold/dispatch workers 已共享 token contract | 功能缺口關閉；只缺 current chain proof |
| BFF generic health path | Compose 已列出 typed `/readyz` API targets | 部分關閉；worker/controller targets 與 controller records 仍未接上 |
| Management 13 當成 12、non-live 漏算 | 最新 `LoopTruthView` 已分 canonical/composite，並以 `accepted_as_live` 計數 | 主畫面缺口關閉；tab 標題仍用全部 13 entries，且 backend truth 仍 stale |

舊 `CURRENT_GAP_2026-08-14.md`、其 16-task catalog 與先前 R4/28-task plans 因此只能當
historical input，不能再次整包派工。

## 4. 十二循環完整 GAP matrix

| # | 循環 | 已實作且應保留 | 尚未開發／設計未閉合 | 尚未完成的驗證 | 判定 |
|---|---|---|---|---|---|
| 1 | Source Ingestion | 單一 `controller_worker`、schedule/connector store、manual job、SourceRecord、loop writer、default-on service | Compose 預設 `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`；此模式明確禁止 provider execution，因此 cron 到點只 reconcile，不會完成規格的 scheduled ingest | 現有 E2E 建 schedule 後又手動 POST `/api/source-ingest/jobs`；最新 checked-in research report failed，read-cap fix 後未有成功 rerun | **直接阻斷** |
| 2 | Strategy Distillation | default-on controller、durable queue、Registry draft write、terminal identity、loop writer；`enqueue_from_source_record()` 已存在 | 正常 SourceRecord 寫入路徑沒有呼叫 event enqueue；Compose owner 每 60 秒列出全部 SourceRecords 後執行 `catch_up()`。這只能算規格允許的 secondary batch catch-up，不能取代 primary event-driven trigger | 沒有 latest-dev 的 SourceRecord commit→立即 durable queue admission→StrategySpec→Alpha admission deployed proof | **直接阻斷** |
| 3 | Alpha Replication | reviewed admission API/store、default-on controller、ExperimentTask/Run、scheduled revalidation handoff | 未發現新的獨立功能缺口 | research deployed run 沒跑到本循環；缺 review command→terminal ExperimentRun→next consumer current proof | 部分閉環 |
| 4 | Persona Teaching | session/event、preview/eval worker、persona target、conditional Consultation request、service JWT | 未發現新的獨立功能缺口 | Research 1–4 deployed suite雖有 Loop 4 case，但 checked-in run在 Loop 1停止；缺 current Compose的 user command→evaluation/target/optional consult proof | 部分閉環 |
| 5 | Agora Interaction Evidence | provider-backed BFF actions、evidence/session/feedback、dataset extraction、durable handoff、claim/ack drainer | Primary durable path 已存在；舊 direct database discovery 仍保留在 explicit no-ref shadow command，形成第二種 dataset discovery | `test_current_human_learning_deployed_e2e.py` 實際啟動 in-process ASGI、tmp stores 並直接呼叫 drainer；不是 deployed proof | 部分閉環 |
| 6 | Human Imitation / Shadow Evaluation | default-on scheduler、durable handoff intake、BC/eval、candidate store、HTTP Research handoff、promotion fail-closed | 未發現新的 primary functional gap；需決定並移除不再需要的 direct Agora scanner command path | 現有測試使用暫存 Research app/candidate；缺真實 scheduler→candidate→Research ExperimentRun deployed proof | 部分閉環 |
| 7 | Consultation | request/memo/handoff stores、supervised executor、real HTTP provider adapter、Governance sink、functional health | 未發現新的 owner flow 缺口 | 現有 human-learning E2E 使用 `FakeOpenClawRiskProvider`、tmp stores；hosted Agora probe也未走完整 Consultation→OpenClaw→Governance | 部分閉環 |
| 8 | Promotion / Deployment | ApprovalDecision→DeploymentPlan→outbox→RuntimeBinding、loader/approval authority、single dispatcher | Deployment/Runtime Manager 允許 active binding 缺 `object_store`、artifact checksum、market input；authoritative readback也不檢查 execution-required projection。E2E 是由 test 自己把 inline Object Store/market snapshot 塞進 plan metadata | 舊 isolated run通過，但現在 9 個 active bindings 全不可被 producer 執行；缺正常 product command產生 executable binding的 proof | **直接阻斷** |
| 9 | Capital Pool Execution | artifact-required producer、binding-scoped signal queue、paper fleet reconciler、paper order/fill/position/heartbeat | (a) 現存 active bindings 沒 executable projection；(b) normal producer只從 binding metadata讀 `market_input/recent_closes`，沒有持續取得新 market snapshot，因此即使修 artifact，仍可能每 tick重放部署時的固定 closes | producer 目前 `unhealthy`；舊 isolated E2E 使用 test-injected `[100,110]` snapshot，不能代表 continuous runtime input | **直接阻斷** |
| 10 | Telemetry / Reconciliation | telemetry ingest、consumer、scheduled reconciler、incident listener、DriftReport/IncidentCase | 未發現新的獨立功能缺口 | 上游 execution 現在不工作；缺最新真實 fill/heartbeat→Telemetry→Drift/Incident readback及 recovery proof | 部分閉環 |
| 11 | Evolution | threshold producer、daily scheduler、decision store、cooldown/single-active、postmortem client、dispatch worker | 未發現新的獨立功能缺口 | 缺 latest-dev threshold/daily trigger→EvolutionDecision→postmortem/research/deployment receipt；舊 runtime proof不可覆蓋目前壞掉的上游 | 部分閉環 |
| 12 | BFF Health Monitoring | 單一 `DownstreamHealthMonitor`、typed API probes、durable telemetry/incident outbox、error-rate ledger、v5 read models | (a) 只有前三循環具 writer，且本次 DB 只有 Source/Distillation records；其餘九個 owner observation 未開發；(b) owners 寫 `tenant-dev/dev`，目前 Management authenticated scope 看不到，scope contract未對齊；(c) target registry只探 API與 paper fleet，漏掉真正執行工作的 schedulers/consumers與 `paper-signal-producer`；(d) `record_downstream_outcome` 沒有 production caller，event-driven error spike只做了 method沒有接 adapter | Management 全部顯示 non-live，且看不到目前 producer unhealthy 的正確 loop原因 | **直接阻斷** |

## 5. 真正尚未開發的最小功能

以下是程式碼檢查後仍需要開發的內容。它們是 design slices，不是 task ID；不得直接拿舊
task 名稱重送。

### GAP-F01 — Source scheduled execution 不得再由 manual job 代替

保留現有 `source-ingest-scheduler` 與 `controller_worker.py`。正常 dev 的 scheduled mode 必須
在已允許的 connector/schedule 到期時呼叫既有 provider execution，產生 SourceRecord，並由
同一 controller 寫 durable terminal/readback。`reconcile_only` 可保留為明確診斷／禁止外拉
模式，但不能同時被宣稱為規格中的 scheduled ingestion owner。

驗收重點：E2E 不得 POST `/jobs`；只建立 connector/schedule，等待 owner tick，然後讀回
SourceRecord 與 Distillation receipt。

### GAP-F02 — Distillation primary event trigger 接入既有 durable queue

保留現有 `DistillationWorker`、`DistillationJobQueue` 與 default-on catch-up controller，不新增
另一個 worker、queue或 event store。正常 Source ingest 在 normalized SourceRecord durable commit
成功後，必須以相同 source identity/version digest 呼叫既有 event admission（或同交易 outbox後
drain至該 queue）；重送必須維持現有 idempotency。每 60秒掃描全部 SourceRecords的
`catch_up()`保留作 missed-event recovery與規格中的 secondary batch catch-up，不能再被當成
primary trigger。

驗收重點：只產生一筆新 SourceRecord後，不呼叫 Distillation controller/manual endpoint；讀回
該版本已進既有 durable queue，接著由現有 worker完成 Registry terminal readback。另測 event
遺失時 catch-up仍能補入，且兩路同時發生不重複執行。

### GAP-F03 — RuntimeBinding 必須是一個可執行契約

Registry/Deployment 應從 approved immutable artifact authority 取得 canonical storage
projection，DeploymentPlan 保存引用，Runtime Manager 在 active 前驗證並持久化：

```text
registry_id / artifact_id
strategy_id
artifact_version
artifact_checksum
object_store reference or equivalent loader descriptor
execution interpreter
paper market-input policy/reference
```

不得把 E2E 使用的 inline Object Store dictionary 複製成正式產品設計，也不得由 operator
任意 metadata 偽造 loader truth。缺上述任一 execution-required 欄位時，plan/binding 必須在
Loop 8 terminal 前 fail closed，不能建立 Loop 9 無法消費的 active binding。

### GAP-F04 — Capital continuous loop 的 market input owner

保留 `CurrentArtifactStrategy`、producer 與 paper fleet。新增的不是第二個 signal engine，而是
讓既有 producer 每 tick 從 canonical paper market-data/source projection 取得新 snapshot，並
把 snapshot identity/time 帶入 decision。RuntimeBinding 只能描述 market-input policy或來源，
不能永遠攜帶部署當下的一組 `recent_closes` 當 continuous feed。

現存 9 個不可執行 active bindings必須經 canonical retire/redeploy或正式 migration處理；不可
直接手改 Runtime Manager JSON。

### GAP-F05 — 九個 owner 的 runtime observation

`services/loop-control` 已是共用 store/projector，不需要九個新 controller service。先修正
Source/Distillation/Alpha三個既有 writer到 BFF controller store的實際可見性與 freshness。
本次資料庫在 `tenant-dev/dev` 已有 Source/Distillation最新 records，但 Management authenticated
scope看見 0；必須統一 owner、登入身分與 BFF query使用的 tenant/environment contract。Alpha
尚未有 current record，需由真實 trigger證明 writer。再由其餘既有 owner在真實
trigger/terminal/readback時寫同一 contract：

- training preview/eval worker；
- Agora durable evidence/handoff owner；
- policy-learning shadow scheduler；
- consultation workflow executor；
- deployment outbox consumer；
- paper execution owner；
- reconciliation owner；
- evolution owner；
- BFF health monitor自身。

每筆 record 必須帶 tenant/environment、deployment SHA、last trigger/terminal/output、actual
readback、next receipt、failure reason與 fresh heartbeat。這是補 observation，不是新增 wrapper
worker或另一份 loop state store。

### GAP-F06 — BFF worker health 與 event-error 真正接線

擴充現有 target registry，透過既有 owner health/readback納入 functional worker health，
而不是只探 API process：Source、Distillation、Alpha、Teaching preview、Policy scheduler、
Consultation executor、Deployment consumer、Paper signal/fleet、Reconciliation workers、
Evolution workers。至少要能把目前 `paper-signal-producer` 的 `artifact_store_missing`顯示在
Capital loop；不為 file-only worker另建新 microservice。

現有 `record_downstream_outcome()` 必須由真實 BFF downstream adapters呼叫；否則規格的
error-rate spike event trigger 永遠不會發生。不得再建立第二個 sentinel/monitor。

### GAP-F07 — Current deployed E2E 與 cross-loop stimulus

這是驗證開發，不是再造產品機制：

1. Loops 1–4：真實 Compose owners，Loop 1 不得 manual trigger。
2. Loops 5–7：移除 in-process app/tmp store/fake provider，改打 deployed owner URLs。
3. Loops 8–12：由正常 Approval/Deployment command建立 executable binding，不得 test 注入
   runtime metadata；使用持續 market input，完成 order/fill/telemetry/incident/evolution。
4. Cross-loop：測試自己產生所有輸入，不接受預先存在的 ID manifest作 stimulus。
5. Management：同一 run 的 IDs/worker failure必須可由 `/bff/v5/loop-health` 讀回。

失敗只輸出 run report、最後成功 boundary與第一個失敗原因；不得自動 materialize repair
task。

## 6. 疊床架屋、誤導與汰除清單

| 現有內容 | 程式碼真相 | 決策 |
|---|---|---|
| `AgoraDatasetAuthority` direct Postgres discovery | scheduled primary path 已改走 durable handoff，但 no-ref `shadow-eval-tick` 仍可直接掃另一 owner schema | 明確 dataset refs 的 command保留；自動 direct discovery在 durable handoff deployed E2E 後移除。module若只剩 tests/diagnostics，再搬到 test utility或刪除 |
| `candidate_experiment_handoff.py` 的「HTTP or direct intake」說明 | 實作已只剩 HTTP；文字仍宣稱 direct path | 清理 stale docstring，不重建 direct fallback |
| `BoundedPaperStrategy` | 已不是 default，只能用 explicit `PAPER_SIGNAL_STRATEGY=smoke` | 保留 explicit smoke，不列為 closure開發；產品測試斷言 default不可走它。只有在 caller audit證明不再需要時才另行搬移或刪除 |
| `pantheon-paper-runtime` static worker | Compose 已註明 compatibility-only，且只在 `static-paper-runtime` profile；default owner 是 `paper-fleet-reconciler`依 active binding啟動的 binding-scoped workers | 不可啟用它來繞過 Loop 8/9。先查 profile、scripts與部署 caller；若沒有現行 caller，連同專用設定退役，否則明確維持 diagnostic-only |
| `services/source_ingestion/scheduler_worker.py` 與 `scripts/source_ingest_scheduler_once.py` | 前者是呼叫 `/run-scheduled` 的舊 bounded HTTP utility；目前 Compose owner是 `controller_worker.py`，產品路徑沒有 import它。現行 production caller只查到 one-shot script，另有 tests/docs | 不得把它重新設成第二 scheduler來修 Loop 1。保留 one-shot diagnostic或在 caller audit後退役；不影響現有單一 owner設計 |
| `source-ingest-agora-projector` 依賴 scheduler `service_completed_successfully` | durable scheduler 預設永不正常結束；profile只有手動覆寫 one-shot 才可啟動 | 若仍需 projector，改依 Source authority/readback；若 hosted path已無 caller則刪除這個 legacy profile，不能再把 durable owner改回 one-shot配合它 |
| static loop catalog 的 `current_maturity`、controller status、planned queries、`LOOP-AUTO-*` execution task refs | BFF把它當 current inventory，但內容明顯落後目前 code；planning/history與產品真相混在同一 row | catalog只保留穩定 ID、規格、owner與 target contract；runtime maturity/health改由 controller records投影；task refs移到 historical planning docs |
| `test_current_human_learning_deployed_e2e.py` | 名稱寫 deployed，實際是 in-process ASGI、tmp stores、fake provider | 保留有價值的 component tests但重新命名；真正 deployed suite取代其 closure gate地位 |
| `test_current_cross_loop_deployed_e2e.py` | 主要消費 prebuilt manifest IDs並做 GET readback，不會重新驅動 12 loops | 改名為 deployed identity/readback verifier；另建真正 stimulus-driven cross-loop test，不可把兩者結果混用 |
| Research E2E 的 `source.job_trigger` | scheduled loop測試直接呼叫 manual job，繞過 primary trigger | 拆成 manual-secondary測試；closure case刪除此 shortcut |
| Runtime E2E inline `object_store` + `[100,110]` market snapshot | 測試自己補齊 production pipeline沒有產生的資料，因此掩蓋 Loop 8→9 契約缺口 | component fixture可保留；product closure E2E必須從 Registry/Deployment authority自然產生 |
| BFF API health與 loop-control health | 兩者用途不同：前者觀察 dependencies，後者記錄 domain terminal state | **不可誤刪或再造第三套**；應以 existing BFF projection把兩者組合成同一 Management read model |
| `data`/`items` 雙欄 envelope | 前端目前以 `data || items`相容讀取，形成雙表面但不是本次功能 blocker | 新 consumer只用 canonical `data`；確認沒有舊 consumer後再做獨立 API cleanup，不得混進 L12功能修復 |

## 7. Management 管理系統盤點

最新 `execute-plans` 已完成兩個正確修復：

- `LoopTruthView` 把 canonical loops與 composite overlays分開，canonical count可正確為 12；
- non-live count以 `accepted_as_live !== true`計算，不再只看 degraded flag；fetch failure不回退
  seed。

仍有三個 gap：

1. `V5Pages.tsx` 的 tab 標題使用 `loopHealthData.data?.length`，所以會顯示
   `Twelve Loop Ground Truth (13)`；應顯示 canonical 12，overlay另外標示。
2. UI只能誠實呈現 BFF輸入；目前 backend static catalog仍把 Loops 4–12 controller標成
   `not_implemented`。Source/Distillation雖已把 records寫進 `tenant-dev/dev`，目前登入／BFF
   query scope仍讀不到；因此畫面雖沒有造假，仍不能提供 current operational truth。
3. BFF downstream target漏掉 producer/worker，Management無法指出目前 Capital loop是因
   `paper-signal-producer/artifact_store_missing`失敗，只能顯示 generic non-live/static。

不需要新增 Management頁面、前端 loop registry或 task-state bridge。修現有 tab count，主要
工作放在 Pantheon owner observation與 BFF projection。

## 8. E2E 與 evidence 真相

| Suite | 現況 | 能證明 | 不能證明 |
|---|---|---|---|
| Research Loops 1–4 | checked-in report `failed`，SHA `0705ad73...`；後續 read-cap fix `a880d0362` 已合併但無成功 rerun | harness/readback問題已被定位 | latest dev四循環閉環；且 current test仍 manual trigger Loop 1 |
| Human Learning 5–7 | 本地4 tests通過；evidence明示 `deployment.applicable=false` | domain/component contracts | Compose services、真 provider、真 worker、durable deployed stores |
| Runtime 8–12 | 2026-08-15 isolated run在 `64e516f...`通過7 cases | test注入完整 runtime metadata時各元件可協作 | 最新 SHA、正常 product deployment產生 executable binding、目前 live runtime |
| Cross-loop | 2026-08-15 `cfd85bfc...`通過5 cases | 既有 IDs跨 owner可readback | 同一次 run重新驅動所有循環 |

本次在最新 Pantheon基線執行：

- 第二輪擴大 focused unit/contract suite（19個跨循環 test files）：`424 passed`；裸環境第一次
  collection因 Evolution明確要求 `PANTHEON_RUNTIME_MANAGER_URL`而停止，使用 Compose既有
  dev URL契約後全數通過，沒有把該前置條件算成產品 failure；
- 四個 L12 integration files在沒有 opt-in deployed環境變數時：`5 passed, 16 skipped`；
- 5個 passed中4個是 human-learning in-process tests，另1個是 research anti-shortcut/static
  guard；真正 research/runtime/cross-loop deployed cases全部 skipped。

所以「單元與契約 code大多可用」成立；「12循環已部署閉環」不成立。

## 9. 最小開發順序與相依

避免鏈式大 DAG，但必須保留真實資料相依：

### 可立即平行

- Source scheduled execution（GAP-F01）。
- Distillation event admission接入既有 queue（GAP-F02）。
- RuntimeBinding executable projection contract（GAP-F03）。
- 九個 owner observation adapters，可按互斥 service scope平行（GAP-F05）。
- BFF worker target/error-outcome wiring（GAP-F06）。
- Human-learning deployed test改造，可先完成 harness但不能在 owner flow前簽收。
- Management tab canonical count修正，可在 `execute-plans`獨立進行。

### 最小必要相依

```text
GAP-F03 RuntimeBinding contract
  -> GAP-F04 continuous market input / canonical binding migration
  -> Runtime 8–12 deployed E2E

GAP-F01
  -> GAP-F02 event-driven distillation admission
  -> Research 1–4 deployed E2E

各 owner observation + GAP-F06
  -> Management current truth acceptance

三組 deployed E2E
  -> stimulus-driven cross-loop E2E
  -> 最終 12-loop closure
```

不得為了平行化而建立第二個 scheduler、第二個 deployment dispatcher、imitation專用
promotion、另一個 loop-health store或 verifier-only fake controller。

## 10. 明確不納入

這批 gap只完成最小可用閉環，不包含：

- Supervisor/fleet/task-state機制修正；
- 新資安框架、HA、壓測、合規、secret rotation；
- live capital、real broker或 canary/live交易；
- 新 Management功能頁；
- E2E失敗自動建立 repair task。

既有 service token/JWT若需要接線，只處理讓現有功能路徑可運作，不擴張成安控專案。

## 11. 主要程式碼證據

| 主題 | 路徑 |
|---|---|
| Source mode與測試 shortcut | `docker-compose.yml`；`services/source_ingestion/controller_worker.py`；`tests/integration/l12/test_current_research_loops_deployed_e2e.py` |
| Distillation trigger | `services/source_ingestion/distillation_controller.py`；`distillation_worker.py`；`main.py`；`docker-compose.yml` |
| Alpha admission | `services/research/main.py`；`services/research/alpha_replication/admission.py`；`services/research/alpha_replication/replication_controller.py` |
| Teaching | `services/training-session/preview_eval_worker.py`；`services/training-session/main.py` |
| Agora/Imitation | `services/policy-learning/scheduler_worker.py`；`agora_handoff_drainer.py`；`agora_dataset_authority.py`；`candidate_experiment_handoff.py` |
| Consultation | `services/consultation/supervisor.py`；`workflow_executor.py`；`services/openclaw-gateway-adapter/consultation_provider.py` |
| Deployment→Runtime | `services/deployment/runtime_manager_dispatch_adapter.py`；`outbox_consumer_worker.py`；`services/runtime-manager/service.py` |
| Capital | `services/execution/lean_runtime/paper_signal_producer.py`；`services/execution/runtime-manager/paper_fleet_reconciler.py` |
| Telemetry/Evolution | `services/reconciliation-drift/consumer.py`；`scheduler_worker.py`；`incident_listener.py`；`services/evolution/*worker.py`；`services/postmortems/main.py` |
| Management truth | `services/control-plane/bff/downstream_health_monitor.py`；`loop_inventory.py`；`main.py`；`services/loop-control/`；`docs/deployment/loop-catalog.registry.json`；Postgres `loop_controller_records` scoped readback |
| Deployed E2E | `tests/integration/l12/test_current_*_deployed_e2e.py`；`docs/deployment/evidence/twelve-loop-current/` |

## 12. 文件真相與後續規則

本文件取代 `CURRENT_GAP_2026-08-14.md`成為此目錄的 current code gap truth；舊文件與
execution catalogs保留作 historical comparison，不刪除、不原地改寫、不再次 materialize。

下一階段若要製作 SD與 execution tasks，必須只從 GAP-F01～F07切片，先與 canonical active/
archive tasks、open PRs、branches與已交付 scope去重；「只缺 current deployed proof」的循環
不得重新開發 domain owner。

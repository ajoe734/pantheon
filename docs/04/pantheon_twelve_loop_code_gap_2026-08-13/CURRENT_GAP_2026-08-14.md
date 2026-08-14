# Pantheon 十二循環最新程式碼 GAP 與最小開發設計

日期：2026-08-14

狀態：current planning truth；16 筆 execution tasks 已完成 canonical materialization，
supervisor 已開始派給 Claude／Antigravity auto-workers

Pantheon 基線：`origin/dev` `768eba39b35d4e9c53beaef22fe7bf841b8f5e45`

Management 前端基線：`execute-plans/origin/dev`
`da50ceee0ba1c6965954b26fb1f69a8b7b0b33d5`

規格真相：`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`

## 1. 結論

目前 **0/12** 循環可以用最新 dev 的真實整合 E2E 證據宣稱完整閉環。

- 8 個循環有直接功能或架構阻斷：Source Ingestion、Alpha Replication、
  Persona Teaching、Human Imitation、Consultation、Promotion/Deployment、
  Capital Pool Execution、BFF Health Monitoring。
- 4 個循環已有主要 domain code，但仍只有部分閉環：Strategy Distillation、
  Agora Interaction Evidence、Telemetry/Reconciliation、Evolution。
- Management 已有 `/bff/v5/loop-inventory` 與 `/bff/v5/loop-health`，但 catalog
  仍把 9 個已存在的 controller/worker 標為 `not_implemented`，且目前沒有 12 個
  accepted controller-health records。`execute-plans` 雖已直讀 `/bff/v5/loop-health`
  且不再 seed fallback，卻把含 composite overlay 的 13 entries 顯示為「12 canonical」，
  並只以 `operator_truth_source.degraded` 計算 degraded，會漏掉未 accepted-as-live 但沒有
  degraded flag 的循環。因此 Management 仍不能正確呈現 12 循環真相。

這不是「再補一個總控層」可以解決的問題。主要缺口是既有 worker 沒有接上、服務
邊界被 in-process 寫入繞過、正常 runtime 使用 smoke fallback、健康檢查只證明 process
存活，以及 E2E verifier 沒有真的觸發任何循環。

## 2. 本次判定方法

本次不以 task archive、PR merge、舊 acceptance markdown 或 catalog 的
`implemented` 欄位當成功證明。每個循環必須具備：

```text
真實 trigger
  -> compose 中實際啟動的唯一 owner worker/controller
  -> durable terminal output
  -> authority readback
  -> 下一個 consumer 讀到同一組 identity
```

另外要求：

1. 預設 dev compose 可以執行，不靠測試 fixture、手寫 JSONL 或 opt-in smoke profile。
2. 跨服務只能經已存在的 HTTP/event boundary，不得 import 另一服務的 store 直接寫。
3. process `/readyz` 為 200 不代表 loop healthy；worker 的 last success、DLQ、readback
   才能作為 loop truth。
4. E2E 失敗先產生 gap report；不得自動建立 repair task。

## 3. 逐循環程式碼真相

| # | 循環 | 最新判定 | 已存在且應保留 | 直接缺口 | 最小下一步 |
|---|---|---|---|---|---|
| 1 | Source Ingestion | 阻斷 | `controller_worker.py` 已以既有 `scheduler_worker.run_tick` 為唯一調度核心；API/manual path 存在 | compose 的 `source-ingest-scheduler` 仍在 opt-in profile、`restart: no`、預設一 tick；正常 dev 沒有 scheduled owner | 讓同一 controller 成為 default-on durable scheduler；把 bounded one-shot 留給明確 smoke command，不新增第二 scheduler |
| 2 | Strategy Distillation | 部分閉環 | default-on `strategy-distillation-worker`、durable queue、draft-only write 與 source→distill component acceptance 已存在 | 上游 Source 預設不排程；沒有真實 deployed source event→StrategySpec readback→consumer E2E | 保留現有 controller，只補真實 input/output identity E2E；不可建立第二 distiller |
| 3 | Alpha Replication | 阻斷 | `ReplicationAdmissionStore`、admission filtering、revalidation worker 與 default-on controller 已存在 | 只有 private JSONL store API；產品沒有 reviewed admission command/readback boundary，正常操作無法把 StrategySpec 放入 queue | 在既有 research authority 暴露 reviewed admission create/readback，controller 繼續讀同一 store；不建立另一 admission queue |
| 4 | Persona Teaching | 阻斷 | session/event/evaluation、async preview worker、條件式 `TrainingConsultationClient` 已存在；Teaching 不再偽造 ConsultMemo | API 預設 strict JWT，但 worker 送 plain token；dev worker 持續 401 且 unhealthy | 讓既有 worker 使用與 Teaching API 相同的 service JWT contract，完成 session→preview/eval→terminal readback；不新增 auth proxy |
| 5 | Agora Interaction Evidence | 部分閉環 | BFF interaction、feedback、dataset extraction、durable dataset handoff 與 ack API 存在 | durable handoff 沒有部署 consumer；policy-learning 反而直接掃 Agora DB；尚無 provider-backed interaction→evidence→handoff deployed E2E | 啟用既有 `agora_handoff_drainer.py` 作唯一跨域 consumer，成功後汰除 production direct scanner |
| 6 | Human Imitation / Shadow Evaluation | 阻斷 | default-on shadow scheduler、lease/recovery、real dataset validation、candidate store 已存在 | scheduler 的 production path 走 direct Agora DB scanner；processed candidate 又直接 import Research store 寫入，繞過已存在的 Research HTTP endpoint；目前 tick 可在 0 candidates 時呈現 healthy | 改成 durable Agora handoff intake；candidate 只呼叫現有 `/api/research-orchestrator/intake/imitation-candidate` 並驗證 readback；刪除跨服務 in-process handoff |
| 7 | Consultation | 阻斷 | durable executor、lease/DLQ、provider client、memo/handoff persistence、OpenClaw provider adapter 已存在 | compose 未提供 provider URL/token 與 handoff sink；dev executor 有 2 DLQ、0 completed、從未 success，但 API `/readyz` 仍為 200 | 接上既有 provider endpoint與既有 downstream handoff endpoint；compose health 必須讀 executor health/DLQ，不再只看 API |
| 8 | Promotion / Deployment | 阻斷 | ApprovalDecision→DeploymentPlan→outbox→RuntimeBinding saga、唯一 outbox consumer、authority checks 已存在 | dev consumer 持續 403，850+ ticks、0 success；container health 只看 heartbeat 仍顯示 healthy；authority GET 不攜帶所需 owner credentials | 修正既有 consumer 的 service identity/tenant 與 authority client headers；health 以 successful/idle-success tick 判定；不加第二 dispatcher |
| 9 | Capital Pool Execution | 阻斷 | paper fleet reconciler、continuous runtime、binding-scoped queue、order/fill/position/heartbeat 與最近的 heartbeat/outbox 修復存在 | normal `paper-signal-producer` 固定使用 `BoundedPaperStrategy`，缺 artifact/market input 時仍每 tick 產生 BUY；這是 smoke generator 被當成正式策略執行，不符合 active immutable RuntimeBinding | 將同一 producer 改為 artifact-required；從 RuntimeBinding 載入 exact artifact 與 market input，缺任一即不產生訊號並 degraded；smoke strategy 移出 normal runner |
| 10 | Telemetry / Reconciliation | 部分閉環 | telemetry ingest、runtime summary、consumer、scheduled reconciler、incident listener 與三個 default-on workers 已存在 | 尚無從真實 Capital output 到 DriftReport/IncidentCase 的 deployed E2E；上游 Capital identity 不可信時本循環也不能閉環 | 不新增 reconciler；以真實 runtime event 驗證 scheduled 與 incident-triggered 兩條路及 exact readback |
| 11 | Evolution | 部分閉環 | threshold sweep、daily sweep、dispatch worker、cooldown/single-active code 與 typed `EvolutionClient` 已存在 | Postmortem→Evolution compose 沒有傳 `EVOLUTION_AUTH_TOKEN`；因此 incident/postmortem feedback chain 未接通；沒有真實 threshold/daily→EvolutionDecision E2E | 將既有 token/tenant contract接到 postmortem outbox client，驗證 proposal→decision→postmortem linkage；不建立另一 bridge |
| 12 | BFF Health Monitoring | 阻斷 | 單一 `DownstreamHealthMonitor`、durable probe/incident store、telemetry/incident emitter 與 v5 read models 已存在 | compose 未宣告 typed target paths，fallback 一律 `/__health__`，對多個服務穩定 404；telemetry JWT 未配置；catalog 只有前三循環有 controller contract | 使用同一 monitor 的 `PANTHEON_BFF_HEALTH_TARGETS_JSON` 宣告每個 owner 的既有 readiness/worker-health 路徑並接現有 telemetry token；補 catalog，不新增 sentinel |

### 3.1 Management 管理系統真相

最新 `execute-plans/origin/dev` 已完成重要的正確方向：

- `src/lib/bff-v1/paths.ts` 使用真正的 `/bff/v5/loop-inventory` 與
  `/bff/v5/loop-health` routes。
- `V5Pages.tsx` 的 truth tab 直接讀 live BFF；fetch error 顯示 error，不回退 seed。
- `LoopTruthView.tsx` 會顯示 truth level 與 accepted-as-live。

但仍有兩個功能顯示缺口：

1. BFF 回傳 12 canonical loops 加 1 composite overlay；UI 直接使用 `loops.length`，所以
   顯示 13，旁邊卻寫「12 canonical catalog loops」。應依 `classification` 分開顯示
   canonical 12 與 composite overlays，不能混成一個 total。
2. `Degraded / Non-Live` 只計算 `operator_truth_source.degraded === true`；
   `accepted_as_live !== true` 的 unobserved/not-implemented entries 可能沒有該 flag，因而
   漏算。應以「非 live 的 canonical loops」作主計數，再分 degraded/unobserved。

前端不需要新增頁面或另一份 loop registry；只修既有 `LoopTruthView` 對同一 BFF envelope
的分類與計數。

### 3.2 已取得的 dev runtime 反證

同一時間點的 dev compose 顯示大多數 API container 為 `healthy`，但這不能證明閉環：

- `training-session-preview-worker`：持續 `HTTP Error 401: Unauthorized`，container
  為 `unhealthy`。
- `consultation-workflow-executor`：`completed=0`、`dead_letter=2`、
  `last_success_at=null`，但 `consultation-svc /readyz` 仍 200。
- `deployment-outbox-consumer`：持續 `HTTP Error 403: Forbidden`，已超過 850 ticks、
  `total_consumed=0`、`last_success=null`，但 container health 仍為 healthy。
- `paper-signal-producer`：找到 9 個 active bindings，並對每個 binding 每 tick enqueue
  一個 signal；程式碼顯示預設來源是固定 BUY 的 `BoundedPaperStrategy`，不是必須成功
  載入的 approved artifact。
- `policy-learning-shadow-eval-scheduler`：worker healthy，但最近 ticks 全部是
  `candidate_count=0`；idle 不能作為 imitation closure evidence。

## 4. 疊床架屋與汰除清單

### 4.1 必須保留的唯一機制

| 能力 | Canonical 機制 | 理由 |
|---|---|---|
| Source 排程 | `controller_worker.py` 呼叫既有 `scheduler_worker.run_tick` | 已經是單一 controller 核心；不需另一 cron service |
| Agora→Imitation | BFF durable dataset handoff + `agora_handoff_drainer.py` | 有 durable identity、claim、ack；比 DB scanner 清楚 |
| Imitation→Research | Research 的 `/api/research-orchestrator/intake/imitation-candidate` | 已存在 owner boundary；不需 shared store |
| Consultation 執行 | `consultation.workflow_executor` + HTTP provider | 已有 lease、DLQ、idempotency；缺的是 wiring |
| Deployment | deployment outbox consumer | 已是唯一 saga dispatcher；403 要修既有身份，不得另開 dispatcher |
| Loop health | `services/loop-control` contract + BFF `DownstreamHealthMonitor`/v5 projection | 共用既有 health truth，不為每循環建立新監控框架 |

### 4.2 應在 cutover 後刪除或降級的機制

| 現有內容 | 問題 | 處理決策 | 刪除前證明 |
|---|---|---|---|
| `AgoraDatasetAuthority` production direct scanner | 與 durable handoff 雙重 intake truth | drainer E2E 通過後移除 production scanner 與 compose `AGORA_DATASET_STORE_*` 依賴；若測試仍需 memory fixture，移到 test utility | repo caller search 只剩 tests；連續兩個 scheduled windows 由 handoff 產生 candidate |
| `candidate_experiment_handoff.py` 直接 import `ResearchOrchestratorStore` | 跨服務直接寫另一 owner store；container 中甚至可能寫自己的本地預設路徑 | 以 existing Research HTTP client/readback 取代，然後刪除此 direct-store implementation | processed candidate 的 task/run 在 Research API readback 可見且 replay 不重複 |
| `SmokeStrategy` / `BoundedPaperStrategy` 作 normal runner default | smoke BUY 被當正式 capital execution | normal runner 改為 artifact-required 後，移出 runtime default；只允許 explicit smoke fixture 使用 | 缺 artifact、checksum 或 market input 時 enqueue=0；approved artifact E2E 仍產生預期訊號 |
| generic `/__health__` 作所有 BFF target default | readiness path 不同造成穩定 404 | 不作 accepted loop truth；每個已知 target 必須有 typed path。generic fallback 僅可供未列管 diagnostic，不得晉升 live truth | 所有 12 owner probe 均有明確 target/path/identity |
| retired `verify_l12_minimum_functional_closure.py` 與 4-test harness | 12 cases 只 GET 不存在的 `/bff/v1/loops/inventory`，不觸發循環；fallback IDs、readback、anti-mock 與 correlated chain 皆可自我宣告通過 | 已移除，且不得再包一層 inventory verifier。真正 E2E 直接成為 gate | 新 deployed E2E 產出真實 trigger/output/readback/consumer identities |
| retired `verify_product_v2_current_closure.py` | 只做 evidence directory audit，名稱會與 L12 產品閉環混淆 | 已移除；historical evidence 僅供調查，不能聲稱 closure | repo/CI caller 盤點完成 |
| 2026-08-13 acceptance evidence 中互相矛盾的 report/markdown/trace | JSON 為 fail/connection refused，Markdown 與 trace 卻寫 passed | 整包標示 invalid historical evidence，不得再被 Management/closeout 讀取 | 新 evidence 只由一次 E2E run 原子產生 |
| live loop catalog 中的舊 `LOOP-AUTO-*` execution task references | 計畫歷史混入產品現況 | 從 current runtime truth 移除，保留到 historical plan/archive | Management 不再把 task completion 當 liveness |

### 4.3 不是重複、不可誤刪

- `services/consultation/provider.py` 是 executor 的 provider client；
  `services/openclaw-gateway-adapter/consultation_provider.py` 是遠端 provider endpoint，兩者
  是同一 boundary 的兩端，不是雙重 workflow。
- `services/source_ingestion/scheduler_worker.py` 被 `controller_worker.py` 使用，不是廢 code。
- `source-ingest-scheduler` 的 legacy compose service key 用於替換舊 container，可以保留
  service 名稱；要改的是 opt-in/one-shot 行為，不是再新增 service key。
- `services/loop-control`、BFF downstream monitor 與 Management projection 分別是 owner
  observation、health reconciliation、read model，應接成一條，不應互相取代或再複製。

## 5. 舊 R4 設計與 execution catalog 適用性

舊 `GAP_REPORT.md`、`SYSTEM_DESIGN.md`、`EXECUTION_TASKS.md` 與
`execution-tasks.json` 是 2026-08-13 的 historical planning baseline，不能再當 current
execution catalog。逐項適用性如下：

| 舊設計 | 最新狀態 | 決策 |
|---|---|---|
| D01 Source scheduler split | domain controller 已有，compose 尚未完成 | 保留目標，縮成「啟用既有 controller + 明確 one-shot smoke」 |
| D02 Distill identity | 大部分已實作 | 不再開新功能；只補 deployed identity E2E |
| D03 ReplicationAdmission | store/filter 已實作，產品 writer 缺失 | 保留未完成的 authority endpoint/readback |
| D04 Teaching conditional consult | code 已實作 | 不重做；只修 worker auth 與 cross-service E2E |
| D05 Agora drainer single authority | drainer code 存在但未部署，scanner 仍在 production path | 保留並加入 scanner retirement gate |
| D06 Imitation→Research | 以 direct-store 錯誤方式實作 | 原實作不適用；改用已存在 HTTP endpoint並刪 direct path |
| D07 Consultation workflow/provider | workflow code 已實作，compose wiring 缺失 | 保留 wiring/health 工作，不建立第二 workflow |
| D08 Deployment integration | saga code 已實作但 runtime 403 | 轉為既有 client auth/health 修正，不重做 saga |
| D09 Artifact-driven paper signal | optional artifact fallback 仍會固定 BUY | 尚未達成；必須改為 artifact-required 並汰除 normal smoke fallback |
| D10 Telemetry/Reconciliation | workers 已實作 | 只補真實 runtime cross-service E2E |
| D11 Evolution client/auth | client 已實作，postmortem compose 未傳 token | 保留最小 wiring 與 readback |
| D12 BFF typed targets | monitor 支援 typed registry，compose 未配置 | 保留設定與 acceptance；不新增 monitor |
| D13 owner observations | catalog/controller contract 只有 3/12 | 仍缺，但應由現有 owner worker發 common observation，不建 wrapper workers |
| D14 catalog/Management truth | live catalog 明顯過時 | 仍缺；必須從 code/runtime truth重建，不讀 task archive |
| D15 verifier/cleanup | 交付的 verifier 無效且 evidence 矛盾 | 舊實作應汰除，改由真實 E2E tests直接擔任 gate |

因此，舊 18-task R4 catalog 不可整包重送。下一階段只能把本文件仍適用的最小 slices
重新去重後 materialize；已完成的 domain code 不得再做一次。

## 6. E2E 測試現況

### 6.1 現有測試能證明什麼

目前有價值但不足以宣稱 deployed closure 的測試包括：

- Source/Distill：`test_e2e_source_ingest_distillation_acceptance.py` 與
  `test_l12_mfc_r4_distill_001.py`，證明 tmp-store/domain flow。
- Alpha：`alpha_replication/test_admission.py`，證明 private admission store 與 filter。
- Teaching：preview worker 與 consultation client tests，使用受控 token/mocks。
- Agora：BFF dataset handoff integration 與 drainer unit tests，沒有 compose consumer。
- Imitation：default-compose/scheduler/candidate-handoff tests，但 candidate handoff 測的是
  in-process Research store，正好掩蓋跨 container 問題。
- Consultation：workflow executor tests 用本地 HTTP fixture provider/handoff sink。
- Deployment：`test_l12_mfc_r4_deploy_001_contract.py` 與 outbox tests 使用 TestClient/
  mocked authorities；dev 的 403 未被覆蓋。
- Capital：paper runtime/signal producer tests 與 LEAN E2E；目前沒有「缺 artifact 必須
  不產生 signal」的 normal-runner gate。
- Telemetry/Evolution：reconciliation、evochain HTTP/component tests；未從真實 Capital
  event 串到 EvolutionDecision。
- BFF：monitor、loop inventory、loop health contract tests；大多注入 target/health record，
  沒驗證 default compose。

### 6.2 無效的 L12 acceptance

已移除的 `tests/integration/l12/test_verify_l12_harness.py` 只有 4 個 harness/static tests；
已移除的 `scripts/verify_l12_minimum_functional_closure.py` 的 12 cases 沒有真實 trigger，且打的是
不存在的 `/bff/v1/loops/inventory`。它不能回答任何一個循環是否運作，也不能回答跨循環
是否閉環；現行 repo 不保留能宣稱 L12 closure 的本地 wrapper。

### 6.3 必須新增的真實測試

每個循環各一個 deployed E2E，均須記錄：trigger ID、owner worker identity、terminal
output ID、authority readback、next-consumer readback、git SHA、compose service、開始/完成時間。

跨循環不是錯誤地硬串 1→2→…→12，而是以下 DAG：

1. Research-to-runtime：Source → Distill → Alpha → Approval/Deployment → Capital →
   Telemetry/Reconciliation → Evolution。
2. Human-learning：Teaching + Agora → durable dataset handoff → Imitation → Research/
   Consultation → Approval/Deployment。
3. Health-incident：BFF Health → Telemetry infrastructure event → Incident → Postmortem →
   Evolution。
4. Concurrency：distill draft 與 approved promotion 併發、reconciliation 與 evolution
   cooldown、deployment 與 kill-switch。
5. Management readback：上述三條 chain 的 exact IDs 必須在 Management loop inventory、
   health、trade journey/incident surfaces 可追溯，不能讀 fixture/task archive。

任何 E2E failure 只輸出 run report、最後成功 stage、第一個失敗 boundary 與 readback；
不得自動 materialize repair task。

## 7. 最小開發設計

### 7.1 目標架構

```text
domain trigger
  -> existing owner API/store
  -> existing supervised worker
  -> existing owner terminal record
  -> existing HTTP/event handoff
  -> next owner readback

owner worker health
  -> existing loop-control observation contract
  -> existing BFF DownstreamHealthMonitor / loop-health projection
  -> Management UI
```

禁止加入 fourth store、second dispatcher、repair shadow queue、verification-only fake
controller 或 task-state-to-product-health bridge。

### 7.2 可平行開發 slices

下列是設計 slices，**不是 execution task IDs**，本輪不 materialize：

| Slice | 內容與主要 scope | 依賴 | 驗收 |
|---|---|---|---|
| S1 Source activation | `docker-compose.yml`、existing source controller/health tests | 無 | default compose 連續兩 tick產生/讀回 SourceRecord；one-shot smoke 不與 durable owner 同時跑 |
| S2 Alpha admission boundary | research main/alpha admission/BFF command wiring | 無 | reviewed command→same admission store→one ExperimentRun；unreviewed spec為 0 run |
| S3 Teaching worker identity | preview worker、training inbound authority、compose | 無 | worker不再 401；session preview/eval/terminal exact readback |
| S4 Consultation wiring | consultation executor、existing provider adapter、compose health | 無 | provider contribution→published memo→ack handoff；DLQ=0、last_success current |
| S5 Deployment consumer repair | outbox consumer existing clients、owner auth headers、compose health | 無 | idle-success可健康；approved plan建立且讀回 exact RuntimeBinding；403 時健康失敗 |
| S6 BFF typed health targets | existing monitor、compose target JSON/telemetry identity | 無 | 每個 target使用正確 path；failure產生 telemetry/incident；API alive 不掩蓋 worker degraded |
| S7 Agora durable cutover | existing BFF handoff/drainer/policy-learning intake | 無 | handoff claim→candidate admission→ack exactly once，然後 scanner production caller為 0 |
| S8 Imitation Research cutover | policy-learning candidate handoff、existing Research endpoint/client | S7 | processed candidate→Research task/run exact readback；刪 direct Research store import |
| S9 Capital artifact execution | paper producer、artifact loader、RuntimeBinding reader | 無 | no artifact/input→0 signal+degraded；approved exact artifact→expected non-hardcoded decision→order/fill/heartbeat |
| S10 Evolution feedback wiring | postmortem outbox、existing EvolutionClient、compose | 無 | published postmortem→proposal→EvolutionDecision→linkage exact readback |
| S11-A Backend truth cleanup | invalid verifiers/evidence、catalog、loop-control observations | 可先獨立刪 false gate；final catalog maturity update 依 S1–S10 | current catalog 不含 historical tasks；12 owner observations 能反映 success/degraded |
| S11-B Management display | `execute-plans` 既有 `LoopTruthView` count/classification | 無；與 backend 分 repo 平行 | UI 明確分開 12 canonical 與 composite overlays；non-live count 不漏 unobserved |
| S12 Deployed E2E suite | per-loop tests與 5 組 cross-loop DAG | 對應 slice 完成；各 per-loop test可隨 slice平行 | 12 per-loop + 5 cross-loop 全部使用真實 compose boundaries，失敗只出報告 |

### 7.3 合併順序

1. S1–S7、S9、S10 可平行，檔案 scope 必須互斥；compose delta 最後由一個 integration
   owner 合併，避免多人同改 compose。
2. S8 只依賴 S7 的 durable dataset intake。
3. 各 slice 自帶該循環 deployed E2E，不等所有功能完成才一次測。
4. S11-A 先移除 false gate/標示 invalid evidence；catalog maturity 最後才依真實 E2E
   更新。S11-B 在獨立 frontend repository 平行進行，不與 Pantheon backend scope 混在同一 PR。
5. S12 cross-loop DAG 在相關 per-loop E2E 全綠後執行。

### 7.4 Rollback

- 每個 wiring slice 只允許回退到「停用該 consumer 並明確 degraded」，不可回退到
  fabricated success、direct store write 或 smoke signal normal path。
- durable handoff cutover 在 ack/readback 連續成功前不刪 scanner；成功後若 rollback，
  暫停 consumer並保留 backlog，不重新啟用雙重 intake。
- Management catalog rollback 不得恢復 historical task completion 為 live truth。

## 8. 明確不做的工作

本輪後續開發只為 12 循環最小可用閉環，不包含：

- Supervisor、fleet、auto-worker、task-state V2 改造。
- 新的資安框架、HA、壓測、合規、secret rotation。
- live capital、real broker、canary/live promotion。
- 新 Management 功能頁；只修既有 12-loop truth readback。
- 因 E2E failure 自動建立 repair task。

上述 auth 項目只是讓既有 service-to-service contract 能夠通過，屬功能 wiring，不是資安
強化專案。

## 9. 下一步

本文件已對 active PR/task/branch/worktree 去重，具體 execution catalog 位於
`CURRENT_EXECUTION_TASKS_2026-08-14.md` 與 `execution-tasks-current-2026-08-14.json`。
materialization 必須透過 governed dev bridge／canonical command，並以 supervisor receipt
與 canonical readback 為準。不得直接重送舊 R4 18-task catalog，也不得一邊保留舊錯誤
mechanism、一邊再加 repair layer。

## 10. 程式碼證據索引

以下是本次結論的主要 owner paths；後續開發應修改這些既有機制，不應先建立平行實作：

| 判定 | 主要程式碼／wiring 證據 |
|---|---|
| Source bounded opt-in | `docker-compose.yml` 的 `source-ingest-scheduler`；`services/source_ingestion/controller_worker.py`；`services/source_ingestion/scheduler_worker.py` |
| Alpha private admission | `services/research/alpha_replication/admission.py`；`services/research/alpha_replication/replication_controller.py` |
| Teaching 既有 consult client | `services/training-session/consultation_client.py`；`services/training-session/preview_eval_worker.py`；`docker-compose.yml` |
| Agora 雙重 intake | `services/control-plane/bff/agora/dataset_extraction/extractor.py`；`services/control-plane/bff/agora/dataset_extraction/router.py`；`services/policy-learning/agora_handoff_drainer.py`；`services/policy-learning/agora_dataset_authority.py` |
| Imitation direct Research store write | `services/policy-learning/candidate_experiment_handoff.py`；owner endpoint 位於 `services/research/main.py` |
| Consultation provider 未 wiring | `services/consultation/provider.py`；`services/consultation/workflow_executor.py`；`services/openclaw-gateway-adapter/consultation_provider.py`；`docker-compose.yml` |
| Deployment 既有單一 consumer | `services/deployment/outbox_consumer_worker.py`；`services/deployment/service.py`；`docker-compose.yml` |
| Capital normal smoke fallback | `services/execution/lean_runtime/paper_signal_producer.py`；`services/execution/lean_runtime/paper_runtime.py`；`docker-compose.yml` |
| Telemetry/Reconciliation workers | `services/telemetry/consumer.py`；`services/reconciliation-drift/scheduler_worker.py`；`services/reconciliation-drift/incident_listener.py` |
| Evolution feedback auth | `services/postmortems/main.py`；`services/evolution/client.py`；`services/evolution/threshold_sweep_worker.py`；`services/evolution/dispatch_worker.py`；`docker-compose.yml` |
| BFF generic health fallback | `services/control-plane/bff/downstream_health_monitor.py`；`services/control-plane/bff/loop_inventory.py`；`docker-compose.yml` |
| 已汰除的無效 closure gate | historical paths: `scripts/verify_l12_minimum_functional_closure.py`；`tests/integration/l12/test_verify_l12_harness.py`；`scripts/verify_product_v2_current_closure.py` |
| Management 計數／分類 | `execute-plans/src/components/management/LoopTruthView.tsx`；`execute-plans/src/pages/management/V5Pages.tsx`；`execute-plans/src/lib/bff-v1/paths.ts` |

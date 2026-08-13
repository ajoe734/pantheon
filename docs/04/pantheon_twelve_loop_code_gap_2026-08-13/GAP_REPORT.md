# Pantheon 十二循環程式碼 GAP 報告

日期：2026-08-13

## 1. 盤點方法與限制

本報告從程式碼反向追查每個循環的 trigger、owner、durable output、readback 與 next
consumer，不以舊開發紀錄或 evidence manifest 判斷完成。盤點包含：

- `docker-compose.yml` 的預設啟動、依賴、環境變數與 worker command；
- 12 個 domain service 的 API、scheduler、queue/outbox、store 與 consumer；
- BFF loop inventory、downstream health monitor 與 loop catalog；
- 相關單元／整合測試是否使用 monkeypatch、mock transport 或 fixture；
- 最新 `execute-plans/dev` 的 Management loop truth 頁面；
- 舊 28-task DAG、2026-08-08 minimum closure plan、open PR、canonical task state、
  branches 與 worktrees 的重疊。

盤點時 Pantheon `origin/dev` 為
`3307552b55af75850dab1d50e58cef9f86e10b53`；前端 `execute-plans/origin/dev`
為 `3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f`。Pantheon canonical task journal
沒有 active L12/Product-V2 implementation task，GitHub 沒有 open PR；因此以下缺口不是
正在執行工作的暫時中間態。

## 2. 總判定

| # | 循環 | 判定 | 直接根因或主要缺口 |
|---:|---|---|---|
| 1 | Source Ingestion | 部分閉環 | 正式 scheduled owner 被包成 opt-in、單次、`restart: no` 的 smoke profile |
| 2 | Strategy Distillation | 路徑存在、未實證 | default-on controller 與 durable output 存在，但測試沒有真實 service-bound consumer readback |
| 3 | Alpha Replication | 直接阻斷 | 以 seed `source_id` 查需要 `strategy_id` 的 Registry API，且違反 review-driven admission |
| 4 | Persona Teaching | 部分閉環 | session/eval 可終結，但沒有由 consultation authority 產生規格要求的 ConsultMemo 路徑 |
| 5 | Agora Evidence | 部分閉環 | durable handoff API 無 production drainer；policy-learning 另以跨 schema 直讀繞過 handoff/ack |
| 6 | Human Imitation | 部分閉環 | candidate 可終結，但沒有進 Research experiment→approval 的 downstream handoff |
| 7 | Consultation | 直接阻斷 | compose 未配置 provider；candidate intake 又直接偽造 auto-approved terminal memo |
| 8 | Promotion/Deployment | 路徑存在、未實證 | approval→plan→binding 具備，缺真實跨服務 E2E 與 next-consumer readback |
| 9 | Capital Execution | 部分閉環 | default signal producer 永遠用固定 AAPL BUY smoke strategy，未執行 RuntimeBinding artifact |
| 10 | Telemetry/Reconciliation | 路徑存在、未實證 | ingest、drift、incident 路徑具備，缺真實 runtime event 至 terminal incident 的整合證明 |
| 11 | Evolution | 部分閉環 | postmortem outbox 未帶 Evolution 必要 bearer token 與 tenant header，正常 compose 會 401 |
| 12 | BFF Health Monitoring | 直接阻斷 | 所有動態 target 預設 `/__health__`，多數服務只有 `/health`；telemetry JWT 亦未配置 |
| M | Management | 真相錯誤 | 9 個 catalog row 仍 `not_implemented`；前端吞掉 API error 並呈現 0 loops |

因此：3 個直接阻斷、6 個部分閉環、3 個未發現明確程式阻斷但缺真實整合 E2E；
12/12 均不能宣稱完整閉環。

## 3. 逐循環程式碼盤點

### 3.1 Source Ingestion — 部分閉環

已存在：

- `services/source_ingestion/controller_worker.py` 會 reconcile desired schedules、執行
  scheduled pull、持久化 controller state 並寫 loop observation。
- Source service 會持久化 `SourceRecord`／evidence；Distillation controller 讀取相同的
  `source_evidence.jsonl`。
- manual API 與 bounded tick 可作測試入口。

阻斷／缺口：

- `docker-compose.yml` 的 `source-ingest-scheduler` 使用正確的
  `controller_worker`，但被放在 `source-ingest-scheduler` opt-in profile，
  `restart: "no"`，且 `SOURCE_INGEST_CONTROLLER_MAX_TICKS` 預設為 `1`。
- 這代表預設 stack 沒有持續 scheduled owner；目前只有手動啟用的一次性 smoke。
- `source-ingest-agora-projector` 又依賴該一次性 service 完成，讓 smoke profile 同時承擔
  正式排程與資料投影責任。

不合適設計：把「正式 scheduled controller」與「有限次數 provider smoke」設成同一個
Compose service。不能再新增另一個平行 scheduler 來補，否則會有雙重 tick authority。

缺失開發：以既有 `controller_worker` 為唯一正式 scheduler，default-on、長駐 reconcile；
另保留明確命名的一次性 smoke command/profile。`scheduler_worker.main` 若只重複 loop，應退役
其獨立 entrypoint，但保留 `run_tick` library 給 controller 使用。

缺失驗證：default compose 啟動後，由一個真實 scheduled tick 產生 SourceRecord，重啟後
readback 仍存在，且 Distillation 讀到同一 `source_id`。現有
`services/source_ingestion/tests/test_e2e_source_ingest_distillation_acceptance.py` 仍以測試替身
隔離部分 controller/Registry，不能當 service-bound E2E。

### 3.2 Strategy Distillation — 路徑存在、未實證

已存在：

- `strategy-distillation-worker` 是 default-on、`restart: unless-stopped`，且 max ticks
  獨立預設為 0。
- `services/source_ingestion/distillation_controller.py` 讀 Source evidence、持久化 job queue
  與 StrategySpecSeed，向 Registry 寫 draft 並保存 controller state/readback。
- 寫入仍維持規格要求的 mutable draft authority，沒有直接碰 approved artifact。

未發現需要重寫的架構缺陷。此循環應保留現有 owner 與 store，不新增第二個
distillation orchestrator。

缺失開發：只需補齊 service integration contract、錯誤狀態的 actual-state observation，並
確保輸出的 canonical `strategy_id`、`registry_id`、version/checksum 能直接交給下一個經審核
的 Alpha admission，而不是靠 seed 猜 identity。

缺失驗證：真實 Source service→Distillation worker→Registry draft readback；測試不得 patch
Registry call，並需證明重送同一 source 不產生錯誤的雙重 draft identity。

### 3.3 Alpha Replication — 直接阻斷

已存在：

- Alpha queue、worker、ExperimentTask/ExperimentRun 與 Registry writeback 已存在。
- `replication_controller.py` 會驗證只允許 approved StrategySpec 入 queue。

直接阻斷：

- `replication_controller.py` 從 seed JSONL 擷取 `source_id`，再把它傳給
  `_get_approved_specs_for_strategy()`。
- 該函式呼叫 `/api/registry/strategies/{strategy_id}/strategy-specs`，參數語意是
  `strategy_id`，不是 `source_id`。
- Distillation 產生的 `strategy_id` 是
  `strat-{source.source_id}-{digest}`，兩者不相等。
- `services/research/alpha_replication/test_product_v2_research_alpha_r3.py` patch 掉 Registry
  lookup，因此未揭露 production identity mismatch。
- Controller 會把發現的 approved spec 自動 enqueue，也違反規格「human/review-driven，
  新 StrategySpec 不自動全量複製」。

不合適設計：用 Source seed file 充當 Alpha work discovery queue。只在 seed 再補一個
`strategy_id` 欄位仍會保留錯誤權威，也無法表達誰核准 replication。

缺失開發：建立 durable replication admission command，至少攜帶 tenant、registry entry、
strategy/version/checksum 與 review/request actor；controller 只消費已 admission 的 request。
scheduled revalidation 只重驗「曾經被 admission」的 strategy。seed 保留 lineage，不再是
production queue authority。

缺失驗證：真實 Registry approved StrategySpec 經明確 review/admission 後產生 terminal
ExperimentRun；未 admission 的 approved spec 不得自動執行；下一個 Teaching/Research
readback 能讀同一 `run_id` 與 lineage。

### 3.4 Persona Teaching — 部分閉環

已存在：

- training-session service、preview/evaluation worker、durable TeachingSession/TeachingEvent、
  eval job、persona commit 與 terminal readback 均已存在。
- async preview/eval 符合規格，不應另建 teaching scheduler。

缺口：production teaching completion 沒有產生或請求規格中的 `ConsultMemo`。目前 terminal
session 可被 operator/Learning surface 讀取，但 consultation downstream 不存在。

不合適設計：不能讓 training service 直接寫 `ConsultMemo`；memo 的權威屬於 Consultation，
直接補寫會製造第二個 memo authority。也不應每次 teaching 都強制建立 memo。

缺失開發：Teaching 完成後持久化 `TeachingEvaluationResult`（可使用現有 session/eval
結構）；只有需要 advisory/review 的結果才透過 Consultation intake 建立 ConsultRequest，
由 Consultation workflow 產生 ConsultMemo。Teaching 只保存 handoff receipt。

缺失驗證：一個 teaching command 完成 eval 與 terminal readback；需要 review 的 case 會
建立 ConsultRequest 並由 Consultation 完成 memo；不需要 review 的 case 正常終結且不偽造
memo。

### 3.5 Agora / Human Trader Interaction Evidence — 部分閉環

已存在：

- Agora interaction、feedback、journal/dataset extraction 會持久化 DatasetVersion 與 pending
  handoff。
- Policy-learning 已提供 `POST /api/policy-learning/agora-handoff`。
- Agora BFF 已提供 handoff acknowledge route。

缺口：沒有 production worker/drainer 呼叫上述兩端。相反地，
`services/policy-learning/agora_dataset_authority.py` 直接讀 Agora owner 的
`agora.agora_dataset_records`，scheduler 由該跨 schema reader 發現資料。handoff 與 ack 因此
成為未接上的旁路。

不合適設計：handoff/ack 與跨 schema 掃描並存，形成雙重消費權威。不能再加入第三個
sync job 或另一份 dataset mirror。

缺失開發：建立單一、可重送的 handoff drainer：列出 Agora pending handoffs，送入
policy-learning canonical intake，確認 candidate/backlog durable readback 後 ack Agora。
切換完成後，跨 schema scanner 退出正式 scheduled path；如為遷移診斷而保留，必須明確
read-only 且不能 enqueue。

缺失驗證：由真實 Agora command 產生 evidence→DatasetVersion→pending handoff→policy
candidate/backlog→Agora ack，整條鏈使用同一 handoff/dataset identity；重送不重複建立
candidate。

### 3.6 Human Imitation / Shadow Evaluation — 部分閉環

已存在：

- default scheduler、DatasetVersion consumption、BC training、evaluation、candidate persist
  與 targeted readback 已存在。
- candidate 正確標記 `experiment_approval_gate=required`。
- `/api/policy-learning/candidates/{id}/promote` 固定拒絕，正確防止 policy-learning 越權部署。

缺口：terminal candidate 沒有 production producer 把它送入 Research ExperimentTask 或
其他正式 experiment admission；因此規格的 `experiment -> approval -> deployment` 在第一步
就中斷。

不合適設計：不能把 promote 功能加回 policy-learning，這會破壞 Research、Approval 與
Deployment authority。

缺失開發：processed candidate 產生 durable research experiment handoff，重用既有
ExperimentTask/Run authority；Policy-learning 只記錄 handoff/receipt。Experiment terminal
result 再走既有 Consultation/Approval/Deployment，不建立 imitation 專用 promotion 平行路徑。

缺失驗證：真實 DatasetVersion 產生 candidate，candidate 進 ExperimentTask，ExperimentRun
terminal readback 保留 candidate/dataset/checksum lineage；candidate 在 approval 前仍不能進
RuntimeBinding。

### 3.7 Consultation — 直接阻斷

已存在：

- Consultation API、durable request/memo/handoff store、workflow executor 與 HTTP
  `QualifiedContribution` contract 已存在。
- `HttpContributionProvider` 會嚴格檢查 tenant、request、participant、evidence、findings、
  recommendation 與 confidence。

直接阻斷與錯誤設計：

- compose 未配置 `CONSULTATION_PROVIDER_URL` 與 `CONSULTATION_PROVIDER_TOKEN`；executor
  會以 non-retryable blocked 結束。
- `POST /api/consult/intake/policy-learning-candidate` 不只建立 ConsultRequest，還同步建立
  confidence `0.95`、預設 approved、PUBLISHED 的 committee memo，並把 request 標成
  PUBLISHED。這沒有呼叫 committee/provider，卻假裝已完成 committee recommendation。
- 兩條路徑同時存在：generic workflow 要真 provider，special intake 則繞過 provider。

缺失開發：Policy/Teaching intake 只能 idempotently 建立 ConsultRequest；所有 memo 必須由
同一 generic workflow 產生。為既有 HTTP provider contract 提供一個真實 in-stack
contribution adapter，重用現有 OpenClaw provider invocation，但用 Consultation 專用 request
與 response schema，不呼叫 operator-facing Management NL endpoint。移除 auto memo path；
既有資料只做相容 readback，不回寫偽造新 memo。

缺失驗證：真實 ConsultRequest→provider call→qualified contribution→published ConsultMemo→
handoff/next consumer，並驗證 provider 不可用時明確 blocked，不能變成 approved。

### 3.8 Promotion / Deployment — 路徑存在、未實證

已存在：

- explicit command、ApprovalDecision、DeploymentPlan、deployment outbox consumer、Registry
  approved artifact、governance、capital handoff、RuntimeBinding 與 readback 路徑均存在。
- immutable approved artifact 與 command-driven authority 方向符合規格。

未發現需要另建 deployment orchestrator 的程式阻斷。應保留現有 promotion/deployment/
runtime-manager authority。

缺失開發：只補齊前序 Consultation/Approval 的 canonical identity 接合、terminal observation
與 next-consumer receipt；不要建立 imitation-deployment 或 consultation-deployment 捷徑。

缺失驗證：真實 approved immutable artifact 經 explicit paper deployment command 產生
DeploymentPlan 與 active RuntimeBinding，Capital 讀取同一 artifact pin。失敗測試需產出報告，
不能自動產 repair task。

### 3.9 Capital Pool Execution — 部分閉環

已存在：

- default-on paper fleet reconciler、signal store、paper runtime，以及 order/fill/position/
  heartbeat 的 durable plumbing 已存在。
- `services/execution/artifact_loader.py` 已能驗證 approved artifact、deployment stage 與
  checksum。
- `services/registry/strategy_artifact.py` 已有策略 artifact evaluator；專用 Taiwan script 亦
  證明參數化策略可產生 signal，但該 script 依賴 hard-coded host path 與 `docker exec`，不是
  可重用 production worker。

缺口：`paper_signal_producer.main()` 永遠建構 `BoundedPaperStrategy()`；它每 tick 對固定
`AAPL.US` 產生固定 BUY/LONG/quantity 決策。訊號雖帶 binding/artifact identity，實際 decision
並未載入或執行該 artifact。

不合適設計：smoke generator 被當作 default paper decision source。只把更多 artifact ID
塞進固定 BUY payload，不能把它變成真 execution。

缺失開發：default paper signal producer 依 active RuntimeBinding 取得 immutable artifact
projection，使用既有 artifact loader 驗 checksum/stage，再以 StrategyArtifact evaluator／
正式 signal interface 執行策略。`BoundedPaperStrategy` 保留為明確 smoke profile/test fixture，
不能是 default。`scripts/tw_signal_producer.py` 不直接升格，應把可用 evaluator 邏輯收斂到
service worker，移除 hard-coded `/home/lupin/pantheon` 與 container-name assumptions。

缺失驗證：一個 active paper RuntimeBinding 載入指定 artifact，市場輸入造成由 artifact
邏輯決定的 signal，後續產生 paper order/fill/position 與 telemetry；切換另一 artifact 時
決策隨 artifact 改變。全程不啟用 live capital。

### 3.10 Telemetry / Reconciliation — 路徑存在、未實證

已存在：

- telemetry ingest、reconciliation consumer/scheduler、drift service、incident listener、
  terminal DriftReport/IncidentCase 與 readback 均存在且 default-on。
- reconciliation 不直接改 running runtime，符合規格。

未發現需要另建 observability bus 的直接阻斷。應保留現有 telemetry、reconciliation、
incidents authority。

缺失開發：對齊 Capital 的真實 artifact execution event、correlation identity 與 terminal
loop observation；不要以 synthetic telemetry 或 evidence manifest 補閉環。

缺失驗證：由 loop 9 的真實 paper fill/heartbeat 進 telemetry，reconciliation 產出
DriftReport 或 IncidentCase，Evolution 可讀同一 incident/postmortem identity；不得 patch
HTTP client 或直接把資料寫進下游 store。

### 3.11 Evolution — 部分閉環

已存在：

- Evolution API、threshold evaluator、daily scheduler、dispatch worker、cooldown/single-active
  與 terminal EvolutionDecision readback 已存在。
- Postmortem outbox 有 durable claim/retry/replay。

直接 integration 缺口：

- Evolution compose 預設 `EVOLUTION_AUTH_MODE=token`，API middleware 對
  `/api/evolution/*` 要求 bearer token 與 `X-Tenant-Id`。
- Postmortems compose 只提供 `EVOLUTION_URL`；`process_postmortems_outbox()` 用
  `client.post(url, json=...)`，沒有 Authorization 或 tenant header，正常路徑會 401。

不合適設計：每個 producer 各自手刻 Evolution HTTP/auth，導致 scheduler 有正確 client
語意、postmortem producer 卻沒有。這不是要重做 auth，而是要收斂 outbound client。

缺失開發：建立最小共享 `EvolutionClient`，由 postmortem 及現有 producer 重用；compose
配置 URL、既有 service token 與 tenant。Outbox 只有在 Evolution terminal readback 確認後
才完成 receipt；保留原 outbox authority。

缺失驗證：真實 published Postmortem 經 authenticated outbox delivery 建立或合併
EvolutionDecision，readback 與 postmortem backlink 一致；現有
`test_evochain_003_full_chain.py` 的 mocked HTTP 只能保留為 component test。

### 3.12 BFF Health Monitoring — 直接阻斷

已存在：

- `DownstreamHealthMonitor` 具 durable SQLite state、probe windows、telemetry/incident outbox、
  failure/recovery 與 readback。
- Telemetry 有專用 infrastructure-health schema/route，沒有混用 RuntimeBinding telemetry。

直接阻斷：

- `DownstreamTarget.health_path` 與 monitor 預設都是 `/__health__`。
- compose 沒有設定 `PANTHEON_BFF_HEALTH_TARGETS_JSON`，因此 dynamic env discovery 對所有
  service 都套同一路徑。
- Capital、Consultation、Source、Search、Training、Policy-learning、Research、Research
  worker gateway、Reconciliation、Evolution、Deployment 等服務只提供 `/health` 或其他
  health path，會被錯誤判斷為 degraded。
- operator-bff compose 未配置 `PANTHEON_BFF_HEALTH_TELEMETRY_JWT`（或 infra JWT）；delivery
  時 `_headers_for_delivery()` 直接拋出 unconfigured。Incident token 也未形成明確 service
  contract。

不合適設計：以 URL env 自動猜 target 並套 universal health path。新增 `/health` fallback
retry 只會隱藏錯誤 contract，且每次 probe 可能產生雙請求與假 recovery。

缺失開發：以顯式 typed target registry 宣告 name/base URL/exact health path；production/dev
startup 對無 path 的 target fail fast，不做 heuristic fallback。配置既有 infrastructure
telemetry service identity；health event 只有在 telemetry receipt 後完成，incident 依賴該
receipt。保留現有 monitor/outbox，不另建 health service。

缺失驗證：至少一個 `/health` target 與一個 `/__health__` target 的真實 fail→threshold→
telemetry→incident→recover 鏈；錯 path 要在 config validation 階段失敗，不是運行後把所有
服務標紅。

## 4. Management 管理系統真相缺口

### 4.1 Backend catalog 與 runtime observation

- `docs/deployment/loop-catalog.registry.json` 只有 Source、Distillation、Alpha 三個 controller
  標為 `implemented`，其餘九個仍為 `not_implemented`。
- Runtime 程式只有上述三個 owner 使用 `LoopControllerWriter`。
- `services/control-plane/bff/loop_inventory.py` 只接受 catalog status 為 `implemented`／
  `proven_live` 的 runtime record；即使其餘 owner 寫入 observation，也會被 catalog contract
  拒絕。

不合適設計：把「每個 loop 必須新增一個獨立 controller process」當作 Management truth
前提。多數 loop 已有自然 owner（command handler、scheduler、worker/outbox consumer）；再建
九個 controller 只會重複調度。

正確方向：由每個既有 owner 在 reconcile/terminal transition 時寫統一 observation；只有
確實沒有定期 owner 的 command loop，才在 owner process 內加小型 reconcile，不新增通用
controller service。Catalog status 在 owner writer 與 readback contract 落地的同一 integration
change 更新，不能先手動塗成 green。

### 4.2 Frontend truth 被吞掉

最新 `execute-plans/dev` 的 `src/management/pages/v5/V5Pages.tsx`：

```ts
try {
  return await bffFetch(...)
} catch {
  return []
}
```

因此 auth、network、5xx 或 contract error 都變成空陣列；頁面顯示
`Twelve Loop Ground Truth (0)`，`LoopTruthView` 沒有 error prop，也無法區分「0 rows」與
「真相 API 失敗」。Component test 只測 sample loops，沒有測 page loader failure。

正確方向：保留 request error，LoopTruthView 顯示 degraded/error 與 correlation；成功回應
應固定呈現 catalog 的 12 rows（沒有 live observation 時標 unobserved），不能呈現 0。此修正
屬 `ajoe734/execute-plans` 的 `dev`，不能把 frontend source 放進 Pantheon repo。

### 4.3 Mock/fallback 邊界

Management 多個 list 仍可經 `liveTransport` 在 network/5xx 後回 seed。這不需要在本次把整個
frontend transport 重寫；最小要求是「十二循環真相」這個 surface 必須 strict live，hosted
build 設 `VITE_BFF_MODE=live`、`VITE_BFF_FALLBACK=strict`，且 LoopsPage 不自行把 error 轉
成空資料。

## 5. 測試與驗證缺口

### 5.1 現有測試不能證明的事項

- Source/Distillation R3 測試隔離或替換部分 controller/Registry；不能證明 compose-bound
  service chain。
- Alpha R3 測試 patch `_get_approved_specs_for_strategy`，掩蓋 source_id/strategy_id 錯配。
- Agora pending handoff、policy intake、ack 分別有測試，但沒有 production drainer 的整鏈。
- Consultation special intake 的測試反而固定了 auto-approved memo 錯誤行為。
- Postmortem→Evolution full-chain test mock HTTP，不會發現 compose token/tenant 缺失。
- Capital tests證明固定 smoke signal plumbing，未證明 RuntimeBinding artifact 決策。
- Frontend `LoopTruthView.test.tsx` 只傳 sample data，未測 `LoopsPage` 讀取失敗。
- 尚不存在舊 minimum plan 宣告的
  `scripts/verify_l12_minimum_functional_closure.py`。

### 5.2 誤導性的 closure verifier

`scripts/verify_product_v2_current_closure.py` 只檢查八個 evidence 目錄，並接受 manifest
status 為 `in_progress`、`done` 甚至 `None`；它還會自己寫出 status=`passed` 的 integrated
evidence。它不啟動服務、不觸發循環、不讀 terminal output、不驗 next consumer，也沒有
覆蓋 12 個規格循環。

此 script 不應再叫 closure verifier 或產生「Hosted R3 closure passed」。可保留的功能只有
「evidence manifest inventory audit」，且輸出不得代表產品閉環。

### 5.3 真實 E2E 尚缺

後續需要一個 compose-bound verifier：

- 不 monkeypatch product code、不 mock HTTP、不直接寫下游 store；
- 使用隔離 test tenant、paper-only runtime 與可控 provider fixture/service；
- 12 個單循環 case 各證明 trigger→terminal→readback→next consumer；
- 至少一個 correlated chain case；
- failure 只輸出 gap report，不能自動建 repair task、改 code 或重送舊 task；
- hosted browser test 另證明 Management strict-live 呈現 12 rows 與 error state。

## 6. 廢棄、重複或誤導內容

| 內容 | 判定 | 處理方向 |
|---|---|---|
| `scripts/verify_product_v2_current_closure.py` | 名稱與輸出誤導，非 closure verifier | 改為純 evidence inventory audit，停止寫「closure passed」 |
| `services/consultation/main.py` candidate auto memo | 錯誤捷徑、繞過唯一 workflow authority | 移除新資料寫入路徑；intake 只建 request |
| `services/policy-learning/agora_dataset_authority.py` scheduled direct scan | 與 handoff/ack 雙重 authority | handoff cutover 後退出正式 enqueue path；只可保留 migration diagnostic |
| `BoundedPaperStrategy` | 有用的 smoke fixture，但放錯 production default | 保留測試/profile，從 default producer 移除 |
| `scripts/tw_signal_producer.py` | 有可用 evaluator 概念，但 hard-coded repo/container | 不直接部署；抽取／重用 evaluator，退役 host-specific script |
| `services/source_ingestion/scheduler_worker.py` | `run_tick` 仍被使用，不是整檔 dead code | 保留 library；確認無 caller 後才退役重複 `main` |
| Pantheon `apps/management` | legacy frontend，仍被測試/validator 引用 | 先遷移 `tests/management`、audit script、persona validator，再刪；不可盲刪 |
| `services/policy-learning/main.py` debug print | production 雜訊 | 後續 component scope 內移除 |
| static loop catalog | 必要 metadata 但目前 stale | 跟 owner observation 同步更新，不可當 live truth |
| 舊 L12 plans/evidence | 歷史規劃，不是 runtime truth | 保留歷史，禁止直接重送或用 evidence 宣稱 closure |

## 7. 舊計畫與工作適用性

### 7.1 2026-07-26 舊 28-task DAG

可保留：

- clean worktree、focused tests、PR→`dev`、independent review、merge、hosted exact identity 的
  delivery discipline；
- 依 domain owner 分工、shared integration 單一 owner、最後集中 E2E 的大方向；
- 既有 Telemetry、Reconciliation、Deployment component 成果作為現況輸入。

必須淘汰／重設：

- 「九個 controller task」不能原樣重送；應由自然 owner 寫 observation，不建立九個平行
  controller processes。
- evidence-only task 不能當功能 blocker 或 closure proof。
- universal acceptance 中 HA、chaos、exhaustive security、load 等超出本次最小閉環。
- `L12-CLOSE-001`、`L12-HOSTED-001` 等舊 ID 不得再復活；其當時 catalog scope 與本輪設計
  已不同。

### 7.2 2026-08-08 minimum functional closure plan

可保留：

- `trigger -> owner -> durable terminal -> readback -> next consumer` 的 objective；
- M1 domain fixes→M2 shared integration→M3 real E2E→M4 hosted readback 的 merge order；
- paper-only、排除 security/HA/compliance/live capital 的範圍。

必須重設的 task scope：

| 舊 scope | 問題 | 新設計要求 |
|---|---|---|
| `L12-MIN-SRC-*` 泛稱 readiness/worker | 未指出 smoke profile 取代正式 scheduler | 拆清 default controller 與 bounded smoke |
| `L12-MIN-ALPHA-*` consume approved strategy | 未指出 identity mismatch 與 review admission | 新 durable admission command，seed 不再 discovery authority |
| `L12-MIN-TEACH-*` terminal session | 漏掉 ConsultMemo authority | conditional ConsultRequest，memo 由 Consultation 產生 |
| `L12-MIN-AGORA-*` handoff/read identifier | 未處理 direct DB scan 雙重 authority | 單一 drainer、ack、cutover direct scan |
| `L12-MIN-IMIT-*` expose candidate | 未接 ExperimentTask | candidate→Research experiment handoff |
| `L12-MIN-CONS-*` terminal memo | 未處理 provider 未配置與 fake memo | in-stack provider adapter、唯一 generic workflow |
| `L12-MIN-CAP-*` consume one signal | 會讓固定 AAPL BUY 也通過 | 必須證明 artifact-driven decision |
| `L12-MIN-EVO-*` consume incident | 未處理實際 auth header | shared Evolution client 與 compose token/tenant |
| `L12-MIN-BFF-*` observe health | 未處理 wrong path 與 telemetry JWT | explicit typed target registry 與 real receipt |
| `L12-MIN-INTEGRATE-*` register 12 controllers | 暗示新增九個 controller | 改成自然 owner observation + catalog integration |
| `L12-MIN-E2E-*` 12 cases | 原則可留，但 declared script 不存在 | 依本報告 contract 重建 verifier，failure 只報告 |

結論：舊 task IDs 與 catalog 不適合原樣執行；它們只能當歷史輸入。下一階段必須依新 SD
建立新的 task packet 並重新做 active task／PR／branch／worktree 衝突檢查。

## 8. 本輪未做事項

- 未修改任何 product code、compose runtime 或 hosted deployment。
- 未建立 execution task JSON/Markdown、未 dispatch、未要求 supervisor materialization。
- 未修改舊 28-task DAG、舊 canonical rows 或 evidence。
- 未把 E2E failure 自動變成 repair task。

上述工作要等 `SYSTEM_DESIGN.md` 被接受後，下一階段才可轉成 governed execution tasks。

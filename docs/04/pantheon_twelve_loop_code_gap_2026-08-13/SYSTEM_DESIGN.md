# Pantheon 十二循環最小功能閉環 System Design

日期：2026-08-13

狀態：供下一階段產生 execution tasks；本文件本身不建立 task ID 或 dispatch

## 1. 設計目標

只完成 12 個 Pantheon 產品循環與 Management 真相頁的最小可用閉環：

```text
real trigger
  -> existing domain owner
  -> durable terminal output
  -> authoritative readback
  -> next consumer receipt
```

本設計不增加 security hardening、enterprise HA、chaos/load test、合規、live capital，
也不改 Supervisor/fleet/task-state 架構。已存在且方向正確的 queue、outbox、store、service
與 authority 一律重用。

## 2. 必守設計決策

1. 每個資料種類只有一個 write authority；handoff 可以複製 envelope，不能另建第二份
   canonical state。
2. 不為 12 個循環各建一個新 controller service。既有 scheduler、worker、command handler、
   outbox consumer 就是 owner；它們負責寫標準 loop observation。
3. Scheduled loop 才需要長駐 scheduler；command/event loop 由命令或事件觸發，不能為了
   Management 看起來 alive 而改成 polling loop。
4. Smoke fixture 不得成為 production/default source：bounded source tick、fake consultation
   memo、fixed paper BUY、seed fallback 均只能在明確測試模式存在。
5. Identity 必須從上游 canonical output 明確傳遞，不能由下一個 consumer 猜測或用另一個
   domain ID 代替。
6. Consultation 只提供 advisory/recommendation；Policy-learning 不 promotion；LEAN 不做
   governance/learning；Deployment 只吃 immutable approved artifact。
7. E2E failure 產生報告，不產生 repair task、不改 code、不改 canonical state。

## 3. 最小閉環共用契約

### 3.1 Correlation envelope

跨循環 handoff 至少攜帶：

| 欄位 | 用途 |
|---|---|
| `tenant_id` | authoritative tenant scope |
| `correlation_id` | 同一次閉環 chain |
| `producer_loop_id` | 來源循環 |
| `source_type` / `source_id` | 上游 terminal object |
| `source_version` / `checksum` | immutable 或可驗 draft identity |
| `requested_by` | command/review actor；scheduled event 使用 service actor |
| `idempotency_key` | 相同 handoff 的 stable retry identity |
| `created_at` | admission timestamp |

不要求現在引入新的全域 message bus schema。各 domain 可使用現有 request model/outbox，
但欄位必須可對應上述 identity，且 next consumer receipt 要保存上游 ID。

### 3.2 Terminal 與 receipt

每個 loop observation 必須區分：

- `last_triggered_at`：owner 接受 trigger；
- `last_terminal_at`：domain terminal output durable；
- `last_output_type` / `last_output_id`：實際 terminal object；
- `last_readback_at`：owner 從 authority read回 terminal；
- `next_consumer` / `next_receipt_id`：下游已接受；
- `status`：`unobserved | running | succeeded | degraded | blocked`；
- `failure_reason`：失敗不得以空資料或 process alive 代替。

重用 `services/loop-control/LoopControllerWriter` 或相同 adapter；不是要求每個 loop 新增
controller process。

## 4. 目標資料流

```text
SourceRecord
  -> StrategySpec draft
  -> reviewed ReplicationAdmission -> ExperimentRun
  -> TeachingEvaluationResult -> optional ConsultRequest

AgoraEvidence -> DatasetVersion -> AgoraHandoff
  -> ShadowImitationCandidate -> ExperimentTask/Run
  -> ConsultRequest -> qualified ConsultMemo
  -> ApprovalDecision -> DeploymentPlan -> RuntimeBinding
  -> artifact-driven paper signal -> order/fill/position
  -> TelemetryEvent -> DriftReport/IncidentCase -> Postmortem
  -> EvolutionDecision

BFF health observation -> infrastructure telemetry receipt -> incident/recovery

All owners -> loop observations -> BFF 12-row read model -> execute-plans strict-live view
```

Teaching 與 Agora/Imitation 是可獨立觸發的循環，不表示每次都要串成單一線性交易流程；
上圖只表達可被下一權威消費的最小接點。

## 5. 設計切片

以下 `D01`～`D15` 是設計切片，不是 execution task ID。下一階段產生 task 時必須重新命名、
去重並補 owner/reviewer/branch/worktree/merge target。

### D01 — Source 正式 scheduler 與 smoke 分離

保留：`services/source_ingestion/controller_worker.py`、existing DB/state、`run_tick`。

替換：

- `source-ingest-scheduler` 改為 default-on 長駐 owner：`restart: unless-stopped`、max ticks=0。
- 另以清楚名稱保留 opt-in、max ticks=1 的 provider smoke command/profile。
- Agora projector 不得用「一次性 scheduler completed」代表 source scheduler 長駐成功；依
  Source API/record readback 或獨立 projection trigger。
- 不新增第二個 scheduler implementation。

預期主要 scope：

- `docker-compose.yml`
- `services/source_ingestion/controller_worker.py`
- `services/source_ingestion/scheduler_worker.py`（只在退役重複 entrypoint 時）
- Source compose/acceptance tests

驗收：default compose 不帶 profile 即有 scheduled owner；一個 controlled schedule 產生
SourceRecord，Distillation 讀到同一 ID；worker restart 後不重複產生錯誤 identity。

回復：還原 Compose activation；保留 merged controller code 與 bounded smoke。

### D02 — Distillation identity/readback contract

保留整個現有 Distillation controller/queue/Registry draft 架構。

調整：terminal output 明確回傳並持久化 `strategy_id`、`registry_id`、spec version/checksum；
loop observation 只在 Registry readback 成功後標 succeeded。不得把 seed `source_id` 當下游
strategy identity。

預期 scope：

- `services/source_ingestion/distillation_controller.py`
- distillation focused integration tests

驗收：真實 SourceRecord 經 worker 在 Registry 可 readback draft；輸出 identity 可直接放入
D03 admission。

### D03 — Alpha review admission 取代 seed discovery

新增最小 durable `ReplicationAdmission`（名稱可在 task design 定稿），欄位：

```text
admission_id, tenant_id, registry_id, strategy_id,
strategy_spec_version, checksum, requested_by, review_ref,
mode(initial|revalidation), status, created_at
```

寫入者是 review/command authority；Alpha controller 是唯一 consumer。初次 replication 沒有
admission 就不執行；scheduled revalidation 只能針對已有 accepted admission 的 strategy。

移除／停用：production loop 由 seed JSONL 掃 `source_id` 查 Registry。seed 仍可作
ExperimentRun lineage。

預期 scope：

- `services/research/alpha_replication/replication_controller.py`
- `services/research/alpha_replication/queue.py` 或同 domain admission store
- Research/BFF 既有 experiment admission route（只做必要接線）
- Alpha tests

驗收：未 review 的 approved spec 不執行；reviewed admission 產生一個 terminal
ExperimentRun；重送 admission idempotent；scheduled revalidation 不擴及未 admission spec。

資料遷移：舊 seed 不自動轉 admission，避免把過去 discovered seed 假裝成已 review。

### D04 — Teaching terminal 與 conditional consultation handoff

保留 TeachingSession/Event、eval worker、persona commit。

新增／調整：

- terminal readback 明確提供 evaluation result identity；
- 只有 teaching policy 判定 `consultation_required=true` 時，透過 Consultation intake 建立
  ConsultRequest；
- Teaching 保存 request/receipt，不寫 ConsultMemo。

預期 scope：training-session service/worker、consultation client/intake model、focused tests。

驗收：一般 teaching 可自行 terminal；review-required teaching 產生 request，後續 memo 由 D07
generic workflow 產生。

### D05 — Agora durable handoff drainer 與單一消費權威

Owner：Agora handoff owner 或明確的 integration adapter；不得同時由 Agora 與
Policy-learning 各自掃描同一 pending set。

流程：

1. list/claim pending Agora handoff；
2. POST existing policy-learning `/api/policy-learning/agora-handoff`；
3. read back accepted backlog/candidate identity；
4. ack Agora handoff，寫 `next_receipt_id`；
5. retry 使用 stable handoff idempotency key。

切換：`agora_dataset_authority.py` 的 direct Postgres scan 不再從正式 scheduler enqueue；可暫留
diagnostic read-only mode，下一清理切片確認無 caller 後移除。

預期 scope：

- Agora BFF handoff endpoints/store
- `services/policy-learning/main.py`
- `services/policy-learning/scheduler_worker.py`
- `services/policy-learning/agora_dataset_authority.py`
- Compose drainer activation（若自然 owner 尚無 background worker）

驗收：真實 handoff end-to-end ack、duplicate retry 不重複 candidate、direct scan 關閉時正式
flow 仍運作。

### D06 — Imitation candidate 送入既有 Research experiment authority

保留 candidate training/eval/readback 與 promote=409。

新增 durable handoff：processed candidate 的 artifact/checksum/dataset lineage 轉成既有
ExperimentTask admission；Research 回 terminal ExperimentRun receipt。Policy-learning 不寫
Registry approved state、不建立 RuntimeBinding。

預期 scope：policy-learning candidate/outbox、Research experiment intake/client、focused tests。

驗收：candidate→ExperimentTask→ExperimentRun identity 可追蹤；沒有 approved decision 前，
`promotion_allowed=false` 與 runtime effect=none。

### D07 — Consultation 唯一 workflow 與真實 provider adapter

保留：`HttpContributionProvider` validation、workflow executor、Consultation stores。

替換：

- `policy-learning-candidate` 與 Teaching intake 只 idempotently 建 ConsultRequest，不再建
  auto-approved memo；
- generic workflow executor 是唯一自動 memo producer；人工 reviewer 仍可走既有明確人工
  memo authority；
- 在 `services/openclaw-gateway-adapter` 增加 Consultation 專用 internal contribution route，
  接收現有 provider request，呼叫既有 OpenClaw provider，要求 JSON-only qualified
  contribution，validate 後回傳 `QualifiedContribution` shape；
- compose 把 `CONSULTATION_PROVIDER_URL/TOKEN` 指向該 internal route/service identity。

不使用 `/bff/management/nl/ask`，因它是 operator conversation/session endpoint，不是 domain
provider contract。也不建立固定 recommendation 的 deterministic fake provider；test fixture
只可在 test profile。

預期 scope：

- `services/consultation/main.py`
- `services/consultation/provider.py`／workflow executor（僅必要 contract adjustment）
- `services/openclaw-gateway-adapter/main.py`
- `docker-compose.yml`
- Consultation/OpenClaw integration tests

驗收：provider 有效時產 terminal memo；provider unavailable 時 request blocked/degraded，絕不
approved；special intake 與 generic request 都走同一 workflow。

相依：D07 必須先完成，D04/D06 的完整 downstream proof 才能結束。

### D08 — Promotion/Deployment identity integration

保留現有 Promotion、Deployment outbox、Runtime Manager 與 binding authority，不新增
orchestrator。

只調整：接收 D07 output/ApprovalDecision 的 canonical IDs；DeploymentPlan/RuntimeBinding
terminal observation 在 readback 後寫出；next receipt 指向 Capital binding consumer。

預期 scope：僅在 integration test 揭露 identity mismatch 時才進 product file；不得先假設
重構 Deployment。

驗收：approved immutable artifact→explicit paper deploy→DeploymentPlan→active
RuntimeBinding；artifact id/version/checksum 全程一致。

### D09 — Capital artifact-driven paper signal producer

保留：paper fleet reconciler、signal store、paper runtime、artifact loader、paper-only guards。

替換 default strategy selection：

1. 從 Runtime Manager 讀 active binding；
2. 取得 binding 的 artifact projection；
3. 用 `services/execution/artifact_loader.py` 驗 approved、paper stage、checksum；
4. 根據 artifact `signal_interface` 或既有
   `services.registry.strategy_artifact.evaluate_strategy_action` 執行；
5. 用真實 market input 建 decision signal，帶完整 binding/artifact identity；
6. 推入既有 signal store。

`BoundedPaperStrategy` 只在 `PAPER_SIGNAL_STRATEGY=smoke` 之類的明確 test/profile 啟用；default
必須是 artifact。`scripts/tw_signal_producer.py` 的 evaluator 概念可重用，但 hard-coded
filesystem、container name、`docker exec` 必須消失，不能成為 service dependency。

預期 scope：

- `services/execution/lean_runtime/paper_signal_producer.py`
- `services/execution/artifact_loader.py`（只有缺少 runtime adapter 時）
- Registry strategy artifact evaluator/interface
- Compose env 與 Capital integration tests

驗收：兩個不同 artifact 對相同 market snapshot 可產不同 decision；signal→paper fill/position
成功；smoke strategy 非 default；所有 live flags false。

### D10 — Telemetry/Reconciliation 真實 runtime event 接合

保留所有現有 service/store/scheduler。只補 D09 event identity 與 loop observation/readback。

預期 scope：Capital telemetry emitter、Telemetry/Reconciliation focused integration tests；若測試
顯示 contract 已相容，product diff 可為零，只建立 verifier case。

驗收：paper fill/heartbeat→TelemetryEvent→DriftReport 或 IncidentCase，Evolution 可讀同一
incident/postmortem identity。

### D11 — Postmortem→Evolution 共用 client

建立最小 `EvolutionClient`，負責 URL、Authorization bearer、`X-Tenant-Id`、idempotency 與
target readback；Postmortem outbox 改用此 client。重用既有 `EVOLUTION_AUTH_TOKEN` 與 tenant
設定，不改 auth model。

Compose 為 postmortems 提供：Evolution URL、service token、tenant。Outbox success 定義為
Evolution 接受且 target decision 可 readback，不只 HTTP 201。

預期 scope：

- `services/evolution/client.py` 或清楚的 shared client module
- `services/postmortems/main.py`
- `docker-compose.yml`
- Postmortem/Evolution tests

驗收：正常 token mode 下真實 delivery 不是 401；retry idempotent；postmortem backlink 與
EvolutionDecision 一致。

### D12 — BFF Health typed target registry

保留 monitor、SQLite/outbox、Telemetry infrastructure route、Incidents consumer。

替換 target resolution：

- `PANTHEON_BFF_HEALTH_TARGETS_JSON` 在 compose 明確列出每個 name/base_url/health_path；
- 每個 target 必須有 exact path，不再把 `_DEFAULT_TARGET_SPECS` 和任意 URL env 自動套
  `/__health__`；
- config parse 對缺 path/duplicate name/invalid URL fail fast；
- 不做 path fallback probing；
- compose 提供現有 infrastructure telemetry JWT/service identity 與 incident token/tenant。

如果保留 dynamic discovery，只能用於 diagnostic「未註冊 target」列表，不得進正式 probe
結果。

預期 scope：

- `services/control-plane/bff/downstream_health_monitor.py`
- `docker-compose.yml`
- Telemetry/Incidents auth wiring tests

驗收：真實 `/health`、`/__health__`、`/healthz` targets 均用各自 path；fail→telemetry receipt→
incident→recovery；credential missing 在 readiness/config 階段明確失敗。

### D13 — 12 個自然 owner 的 Management observations

不建立九個 controller services。對應 owner：

| Loop | Observation writer 所在自然 owner |
|---|---|
| Source | source controller |
| Distillation | distillation controller |
| Alpha | alpha admission/worker controller |
| Teaching | training/eval worker terminal transition |
| Agora | interaction/handoff owner |
| Imitation | policy-learning scheduler/worker |
| Consultation | consultation workflow executor |
| Deployment | deployment outbox consumer/runtime binding readback |
| Capital | paper fleet/runtime worker |
| Telemetry/Reconciliation | reconciliation scheduler/consumer |
| Evolution | evolution scheduler/decision service |
| BFF Health | downstream health monitor |

每個 owner 以共用 adapter 寫第 3.2 節 observation。D01～D12 domain change 可各自加入 writer，
但 shared catalog/BFF integration 由單一後續切片收斂，避免 catalog scope 衝突。

### D14 — Catalog、BFF read model 與 Management frontend 真相

Pantheon integration：

- 只有 owner writer、terminal/readback contract 已落地的 row 才由 `not_implemented` 改為
  `implemented`；
- `loop_inventory.py` 對 12 rows 顯示 catalog metadata + current observation；缺 observation
  是 `unobserved`，不是不存在；
- process alive、catalog metadata 不得是 accepted-as-live；需 terminal/readback/receipt freshness。

`execute-plans` integration（獨立 repo/task/PR，target `dev`）：

- `LoopsPage` 不 catch 成 `[]`；
- `useV5Live` error 傳給 `LoopTruthView`；
- API error 顯示 degraded/error 與 retry，不顯示 `(0)` 假真相；
- successful response 必須顯示 12 rows；
- loop truth surface 使用 strict-live，不從 seed/mock fallback。

預期 Pantheon scope：

- `docs/deployment/loop-catalog.registry.json`
- `services/control-plane/bff/loop_inventory.py`
- loop-control/BFF tests

預期 execute-plans scope：

- `src/management/pages/v5/V5Pages.tsx`
- `src/management/pages/v5/LoopTruthView.tsx`
- 對應 page/component tests

驗收：BFF 正常時固定 12 rows；未觀測 row 明確 degraded；BFF 失敗時 UI 顯示 error 而非
0 loops/seed；hosted build identity 精確對應兩 repo commits。

### D15 — 真實 12-loop verifier 與誤導內容清理

新增一個 compose-bound verifier，但不在此文件指定舊 task ID 或沿用不存在的 script。

Verifier 規則：

- 先驗 dev stack identity 與 service readiness；
- 每個 loop 一個 case，輸入、terminal ID、readback、next receipt 均保存；
- 至少一條 correlated chain；
- 禁止 monkeypatch、mock HTTP、直接寫下游 store、seed fallback；
- test tenant、paper-only、bounded timeout；
- failure 寫 JSON/Markdown GAP result 並 non-zero exit；不 dispatch、不修 code；
- hosted browser case 驗 LoopsPage 12 rows/error state。

同切片或獨立 cleanup：

- 將 `verify_product_v2_current_closure.py` 改名/降級為 evidence inventory audit，停止自行寫
  closure passed；
- 移除 policy-learning debug print；
- 確認 direct Agora scanner、source duplicate main、host-specific TW script 無 production caller
  後再移除；
- Pantheon `apps/management` 先遷移現存 tests/validators，再做 legacy removal，不能跟 12-loop
  功能 task 混在一起。

## 6. 相依與合併順序

為避免「遇到一個問題改一個、每改一個就部署」的反覆方式，後續 execution plan 應先完整
materialize design-approved packets，再按下列 wave 合併：

| Wave | 可並行設計切片 | Gate |
|---:|---|---|
| 0 | 重新對 active task/PR/branch/worktree 去重；凍結本 SD digest | 不實作、不 dispatch 舊 ID |
| 1 | D01, D02, D03, D05, D07, D09, D11, D12 | 各 domain root cause 修正；不得碰 shared catalog |
| 2 | D04, D06, D08, D10 | 前序 provider/identity/handoff 可用後接 downstream |
| 3 | D13 | 12 owners observation contract 完整，component readback 通過 |
| 4 | D14 Pantheon + execute-plans（兩 repo 各自 PR） | catalog/BFF/UI 真相一致 |
| 5 | D15 verifier/cleanup | 12 cases + chain 全過；failure 只回報 |
| 6 | 一次 bounded dev rollout + hosted verification | exact FE/BFF manifest；不逐 task 部署 |

D08/D10 若真實 integration test 證明 product code 已相容，可以是 validation-only task，不應
為了「每 loop 都有 code diff」而添加無用程式。

## 7. Execution task 產生要求

下一階段每個 governed task packet 至少包含：

- objective 與對應 Dxx design slice；
- current code evidence 與明確 root cause；
- declared file scope，shared files 只能由指定 integration owner 使用；
- out-of-scope（尤其 security/HA/live capital/supervisor）；
- dependencies 與 merge target `dev`；frontend task 明確標記
  `ajoe734/execute-plans`，不是 Pantheon 子目錄；
- owner capability 與不同的 reviewer capability；
- clean branch/worktree；
- component acceptance、real integration validation、rollback；
- required artifacts：PR、checks、review、merge SHA、功能 readback；
- 禁止自動把 E2E failure 轉成 repair task。

不得：

- 重用或復活舊 `L12-*` task ID；
- 原地修改舊 28-task DAG 或 canonical task state；
- 同一 scope 同時 queue 給兩個 worker；
- 以 evidence manifest 或 test mock 取代 terminal readback；
- 在 domain task 提前修改 shared loop catalog；
- 把 Supervisor V2 work 混入 Pantheon 12-loop DAG。

## 8. 最終 acceptance matrix

| # | 必須實證的最小結果 |
|---:|---|
| 1 | default scheduled Source owner 產 SourceRecord，Distillation receipt 同 ID |
| 2 | SourceRecord 產 Registry StrategySpec draft，Alpha admission 可用 canonical IDs |
| 3 | reviewed admission 產 ExperimentRun；未 review 不執行 |
| 4 | teaching terminal eval；必要時 Consultation request/memo 完成 |
| 5 | Agora evidence/dataset handoff 經唯一 drainer 被 ack |
| 6 | shadow candidate 進 Research ExperimentTask/Run，未 approval 不部署 |
| 7 | 真 provider contribution 產 memo；provider unavailable 不會 approved |
| 8 | approved artifact 經 explicit paper deployment 產 Plan/Binding |
| 9 | binding 的 artifact 邏輯產 paper signal/fill/position，非 fixed smoke decision |
| 10 | runtime event 產 DriftReport/IncidentCase，下游可讀 |
| 11 | postmortem 以正確 auth/tenant 產 EvolutionDecision/readback |
| 12 | exact health paths 產 telemetry receipt、incident 與 recovery |
| M | BFF/Management 固定呈現 12 rows；error 不變成 0 或 seed |

只有 13 列全部以同一部署的真實 readback 通過，才能宣稱「12 個循環與 Management 真相
正常運作」。

## 9. Rollout 與 rollback

- Domain PR 合併後先保持未啟用或相容模式，等 shared integration 與 verifier 都完成再做一次
  bounded dev rollout。
- Agora cutover 前保留 direct scanner 的 diagnostic mode；handoff drainer 驗證後關閉 enqueue
  authority。回復時只能回到單一路徑，不能兩路同時開。
- Source、Capital、Consultation 的 smoke/fake 路徑保留為 explicit test profile，不能作 hosted
  fallback。
- Hosted verification 失敗時保留上一個 served release，只輸出 failing loop report；不得在
  host 上臨時疊 patch 或自動 dispatch repair。
- 全部 Capital 驗證維持 paper-only；rollback 不得開啟 live flags。

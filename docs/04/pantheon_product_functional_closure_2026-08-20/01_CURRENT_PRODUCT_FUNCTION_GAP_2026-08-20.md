# Pantheon 完整產品功能 GAP — 2026-08-20

## 1. 結論

截至本次凍結基線，Pantheon 是「多數元件已存在，但主要產品旅程仍未 current-dev 閉環」。
目前不能用完成過的 task、合併過的 PR、process `/readyz`、fixture、seed 或舊 evidence 宣稱
產品完成。

產品層級最重要的七個事實是：

1. 受管 dev 部署已把 Source 外拉停在 `reconcile_only`，符合操作者最新方向；但 raw
   Compose 的 fallback 目前仍是 `reconcile_and_pull`，必須由 integration task 收斂。Source API
   也因 state/readiness 成本過高而 unhealthy。開發與驗收要繼續，外部資料只允許測試時
   bounded one-shot。
2. SourceRecord 到 Distillation durable admission 已在 current code 接上，8/18 的 F02 不再是功能
   缺口；後續只需要隨真實 one-shot stimulus 驗證。
3. 所謂 executable RuntimeBinding 的舊 task 只合併 evidence；目前 9/9 active paper bindings
   仍沒有 Object Store projection/checksum，正常 Registry → Deployment → Runtime Manager 契約未完成。
4. Paper producer 參考的 `/api/source-ingest/snapshots/latest` 在 Source service 沒有實作；同時每個
   paper child 都反覆掃描約 179 MB lifecycle outbox，導致約 12 GiB fleet memory 與數百 % CPU。
5. Agora 的 Workshop、Research、Candidate、Trading Room UI/API 很多，但 reconstruction、research
   dispatch、decision/performance producer 與 pool/lens identity 沒串成正常旅程。
6. Management 不是只差「畫面」。hosted frontend 的 real writes 關閉時會回 mock completed；即使
   開啟，generic BFF action adapter 也只回 `admitted` 而不執行 domain mutation。另有四個明顯
   synthetic/seed surface 與 strict-live contract mismatch 回 seed 的路徑。
7. Hosted FE manifest、實際 BFF 與 `origin/dev` 是三個不同 Pantheon SHA，因此目前 hosted
   `accepted` 標記不能作為 current product closure。

最終判定：**12 個循環、Agora、Management 與 Management AI 都尚未共同完成產品級簽收。**

## 2. 判定方法

一個功能只有同時滿足下列鏈條，才算完成：

```text
使用者或規格指定 stimulus
  -> current Compose / hosted owner
  -> durable command / event / terminal output
  -> canonical authority readback
  -> 下一個 consumer 讀到相同 identity
  -> Management 顯示同一筆真實狀態
  -> exact FE/BFF current-dev evidence
```

以下不算完成：

- test 自己建立下游物件或補 `object_store` / `recent_closes`；
- in-process ASGI、tmp store、fake provider、seed 或 synthetic series；
- 預先提供 IDs 後只做 GET readback；
- `container running`、API process healthy 或 static catalog `implemented=true`；
- task `done` 但 delivery 只有 evidence file，或 evidence 綁的是舊 SHA；
- frontend toast 顯示成功，但 backend 沒有 domain terminal state/readback。

## 3. 全產品功能矩陣

| Domain | 已存在且應保留 | Current functional gap | 優先級 |
|---|---|---|---|
| Product shell / navigation | Management routes、sidebar、detail/list patterns、strict live transport | 大量 surface 存在不等於資料真實；錯誤/contract mismatch 仍可能被 seed 蓋掉 | P1 |
| Management read models | BFF 有廣泛 management endpoints，shell/cockpit/operations/lineage 等已有 adapter | `safeAdapt` 在 HTTP 200 contract mismatch 時回 seed；Persona fleet、data sources latency過高；部分頁面讀固定資料 | P0/P1 |
| Management commands | 18 個 production `runActionSafe` call sites、write gate、receipt UI | read-only build回 mock completed；generic BFF adapter只 admission、不執行或讀回 domain state | P0/P1 |
| Management AI | conversation persistence、NL endpoint、OpenClaw adapter、7 種 UI action contract | 實際 provider因 Claude session expired而 degraded；一次 call 約32秒後失敗；`openDrawer`/`focusPanel`未執行，`runBffAction`未接 HighRiskConfirm/domain action | P1 |
| Persona / lifecycle / governance | Persona fleet、provisioning、approval、governance與多數 read surfaces存在 | 不重造；透過 Management真實 command/readback journey驗收，避免只以 endpoint count判定 | P1 acceptance |
| Source Ingestion | connector/schedule authority、manual job、single controller、SourceRecord、Distillation event admission | controller state遞迴嵌入 readback/reconcile，約280 MB；readyz掃大 store；API 80%+ CPU/1.6 GiB且 timeout | P0 |
| Research / Loops 2–4 | Distillation durable queue、Registry draft、Alpha admission、Teaching worker | 需從一次 bounded manual source pull證明 current owners自然完成；不得改回連續外拉 | P1 |
| Agora Workshop | create/message、durable store、deterministic reconstruct endpoint、card renderer | 正常 composer不呼叫 reconstruct；沒有 durable reconstruction consumer/result projection | P1 |
| Agora Research | plan/run/stage/outbox、`ResearchDispatcher.execute_stage`、candidate pool contracts | 非 test 路徑沒有 consumer呼叫 dispatcher；outbox不會自然完成 stage/result/candidate | P1 |
| Agora Trading Room | candidate APIs、workspace、score/review contracts、獨立 BFF-wired drawer元件 | active頁仍用固定 `lens-A..E` 當 pool ID；頁內另有只改 React state的 duplicate drawer；多數 widget沒有真資料 producer | P1 |
| Agora learning / consultation | durable Agora→policy handoff drainer、policy scheduler、Consultation executor/provider | decision event、performance suggestion沒有 production producer；完整 Workshop→Consultation旅程未 current hosted驗收 | P1 |
| Deployment / RuntimeBinding | approval、deployment plan/outbox、Runtime Manager、artifact loader | 正常 pipeline未持久化 execution-required Object Store/checksum/policy；9個 active binding均不可執行 | P0/P1 |
| Paper execution | paper fleet、producer、order/fill/position/heartbeat owners | snapshot endpoint不存在；lifecycle outbox無 cursor/retention，9 workers反覆掃約179 MB檔；producer unhealthy | P0 |
| Telemetry / Reconciliation / Evolution | ingest、reconciler、incident、postmortem、evolution workers與多個 observation writer | 上游 execution不工作；缺同一次真實 fill→telemetry→incident/postmortem/evolution deployed proof | P1 |
| Twelve-loop truth | loop-control store、BFF v5 projection、已合併 Loops 3/4/5/6/7/10/11 observation code；F06已把 file-worker health、error outcome與Loop 12 controller truth接入 | Loops 8/9 current journey與跨loop truth仍未驗收；static catalog仍混入 runtime/task claims | P1 |
| Delivery / hosted acceptance | Pantheon-owned FE host、strict live build、release manifest/gates | FE manifest宣稱 BFF `e50af43`，實際 BFF `26a4fd`，source `cd93c20`；部分 integration cases skipped | P0 final gate |

## 4. Runtime 現況

### 4.1 Source

2026-08-20 live observation：

- `pantheon-source-ingest-1` unhealthy，約 82% CPU、1.6 GiB memory；`/readyz` 3秒 timeout。
- `source-ingest-scheduler` 已停止，沒有持續對外拉資料；Distillation controller仍可處理既有
  durable queue。
- source volume 約293 MB，其中 `controller_state.json` 約280 MB、schedule journal約8.8 MB。
- `ControllerStateStore` 保存整份 `reconcile` 與 `actual_readback`；actual readback又包含上一版
  `controller_state`，reconcile也包含 pre/post readback，形成每 tick 巢狀成長。
- 受管 non-production deploy wrapper 目前明確注入
  `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`，所以 hosted dev 沒有長駐 provider pull；但是
  raw `docker-compose.yml` 在沒有環境變數時的 fallback 仍是 `reconcile_and_pull`。這不是可接受的
  最終 default，必須由 `PFG-DEV-INTEGRATION-20260820` 收斂為 `reconcile_only`，而不是另建第二個
  scheduler/owner。

因此 Source 的第一個 task不是恢復排程外拉，而是把 state投影改成 bounded summary/cursor、
遷移或重建爆量狀態、讓 readiness能在固定成本內回應。只有這之後才接受測試時手動單次外拉。

### 4.2 Paper

2026-08-20 live observation：

- `paper-signal-producer` unhealthy，logs持續對 9 個 bindings回報
  `artifact_store_missing`。
- Runtime Manager有9個 active paper bindings；9/9 metadata沒有 `object_store`，也沒有可用的
  artifact checksum projection。
- paper fleet約386% CPU、11.8 GiB memory；9個 `paper_runtime.py` child各約60% CPU與
  1.2–1.5 GiB RSS。
- runtime volume約1.6 GiB；9個 lifecycle outbox各約179 MB。檔案是反覆重寫/掃描的單一大
  JSON state，沒有 durable cursor、acknowledged compaction與 retention boundary。

這表示「修 producer一個 fallback URL」不足以完成 paper loop。必須先讓 worker不再無限掃描
歷史，再補正常 executable binding與 canonical snapshot，最後用正式 API retire/redeploy舊 bindings。

## 5. 十二循環重新校正

| Loop | 2026-08-20 corrected truth | Required closure |
|---|---|---|
| 1 Source | dev平時禁止外拉是刻意 policy；API/state目前不健康 | 修 state/readiness；測試明確啟動一次 bounded pull，完成即停 |
| 2 Distillation | SourceRecord commit後的 event admission已在 `main.py`實作 | 用同一次 one-shot record驗證 queue/worker/Registry，不再開新功能 task |
| 3 Alpha | owner flow與 observation code已存在 | current-dev stimulus/readback |
| 4 Teaching | owner flow與 observation code已存在 | current-dev user command/terminal/readback |
| 5 Agora | durable evidence/handoff存在 | 完成 Agora product journey與 deployed worker proof |
| 6 Imitation | durable handoff scheduler與Research HTTP handoff存在 | 移除 direct discovery duplicate；真 scheduler/candidate/readback |
| 7 Consultation | executor/provider/handoff存在 | 真 OpenClaw contribution與Governance receipt，不用 fake provider |
| 8 Deployment | 舊 task只證明既有 loader tests；正常 active binding仍不可執行 | Registry→Plan→Binding完整 artifact projection；active前驗證 |
| 9 Capital | producer與fleet存在，但snapshot endpoint不存在且state loop失控 | canonical snapshot + bounded state/cursor + order/fill/position |
| 10 Reconciliation | observation code與owner存在 | 從本次真 fill/heartbeat產生 Drift/Incident |
| 11 Evolution | observation code與owner存在 | 從本次 incident/postmortem產生decision/receipt |
| 12 BFF health | F06已於`cd93c2010...`完成 functional worker attribution、error outcome與Loop 12 controller truth | 以current owner records和同ID Management readback做跨loop驗收，不重做F06 |

舊 `L12-GAP-F03-EXECUTABLE-RUNTIME-BINDING-20260818` 和
`L12-GAP-F04-CONTINUOUS-MARKET-INPUT-20260818` 雖為 terminal，但 live readback直接否定其產品
完成結論。新 work使用新的 follow-up task ID，不重開或改寫terminal history。

## 6. Agora current journey gap

目標旅程：

```text
Workshop message
  -> durable reconstruction
  -> Registry StrategySpec draft
  -> Research outbox consumer/stages
  -> real candidate pool
  -> Trading Room pool/workspace
  -> durable review/decision
  -> performance/learning evidence
  -> policy learning
  -> independent consultation/governance receipt
```

Current breaks：

1. `POST /reconstruct` 已存在，但正常 frontend message flow只呼叫 message與daily interaction。
2. Research有 durable outbox和 `ResearchDispatcher`，但 production沒有 stage consumer。
3. Trading Room把 fixed lens ID傳給需要 candidate pool ID的 API。
4. active page-local CandidateReviewDrawer只做 React state update；另一個真正BFF-wired drawer未被
   active頁使用。
5. workspace只有少數 widget有資料 derivation，其餘未定義。
6. `upsert_decision_event`、`upsert_suggestion` 只有 tests/直接 store caller，沒有正常 producer。
7. 先前完成的 Agora candidate fixture correction、durable policy handoff與Consultation submitted
   semantics應保留，不得再做一套。

## 7. Management current gap

### 7.1 Reads

Management已有大規模頁面與BFF契約，但以下頁面仍不能算 production truth：

- `FormulaStudio.tsx` dynamic import `@/mocks/seed`、用 `setTimeout`產生固定成功job/metrics/chart。
- `ActivityMonitor.tsx` 顯示由前端產生、卻標示為 live 的事件。
- `StrategyPaperLiveTab.tsx` 以 strategy ID hash產生 paper/live series。
- `PostmortemLibrary.tsx` 固定顯示3筆；真 postmortem API目前可能回0，且該頁未接 canonical BFF adapter。
- `management.ts::safeAdapt` 在 strict live收到200但不符合contract時回seed，掩蓋backend錯誤。

Read performance snapshot（三次樣本）也顯示功能性問題：cockpit約1秒/172 KB、persona fleet中位
約3.2秒且可到6.9秒、data sources約2.1秒且因Source unhealthy回0項。這批只做必要查詢/投影
成本改善，不另開泛用壓測或SRE專案。

### 7.2 Writes

- Hosted build是 `VITE_BFF_REAL_WRITES=false`；這本身可作安全的read-only profile，但不能在此
  profile把mutation模擬成 `completed`並顯示「Action applied」。應明確顯示disabled/unavailable。
- 即使啟用real writes，`command_executor.py::_execute_bff_action_adapter`只產生`admitted`
  receipt；註解/實作明確不做domain mutation或terminal readback。
- 已有61個 `NonProductionActionButton` 是誠實disabled surface，應保留；問題是18個看似可操作
  的 `runActionSafe` call sites。

### 7.3 Management AI

- outer OpenClaw readiness顯示 gateway reachable，但實際provider request約32秒後degraded；logs
  顯示Claude session expired且refresh失敗，沒有可用fallback回答。
- conversation persistence已存在，不需重做。
- UI registry宣告7種actions，但`openDrawer`、`focusPanel`未執行；`runBffAction`只回必須另路由，
  尚未真正接到confirm+domain command。
- `kernel_debug`維持read-only；產品BFF不得拿來寫repo或建立開發task。

## 8. Hosted release truth

目前 hosted frontend `deployment.json`：

- FE `729baba8...`；
- declared BFF `e50af43...`；
- `VITE_BFF_MODE=live`、`VITE_BFF_FALLBACK=strict`；
- `VITE_BFF_REAL_WRITES=false`；
- manifest標記 `accepted`。

實際 public BFF `/bff/version` 是 `26a4fd...`，而 current `origin/dev` 是 `cd93c20...`。
因此這是 functional delivery identity drift。最終驗收必須由同一release manifest指向實際
服務中的exact SHAs，且所有要求的 authenticated/browser journeys不得skipped。

## 9. 明確不納入

本輪不建立以下tasks：

- 新資安架構、MFA/RBAC重設、secret rotation、security hardening；
- HA、DR、泛用壓測、合規、production on-call設計；
- live broker、real capital、live trading activation；
- supervisor/auto-worker/TaskStore機制改造；
- 新Management資訊架構或更多靜態頁面；
- 以自動產生repair task掩蓋E2E失敗。

既有認證、tenant、audit與write guard仍要維持；只有當現有功能路徑因必要identity/wiring無法
工作時，才做最小接線，不擴張為資安專案。

## 10. 主要程式碼證據

| Evidence | Path |
|---|---|
| Source controller/state/readback | `services/source_ingestion/controller_worker.py`, `controller_state.py`, `main.py` |
| Distillation event admission | `services/source_ingestion/main.py` |
| Paper snapshot client | `services/execution/lean_runtime/paper_signal_producer.py` |
| Runtime binding/fleet | `services/execution/runtime-manager/`, `services/execution/lean_runtime/paper_runtime.py` |
| Agora reconstruction | `services/control-plane/bff/agora/strategy_workshop/` |
| Agora Research dispatcher | `services/control-plane/bff/agora/research/dispatcher.py` |
| Agora decision/performance stores | `services/control-plane/bff/agora/trading_room/store.py`, `agora/performance/store.py` |
| Generic management action | `services/control-plane/bff/command_executor.py` |
| Management NL | `services/control-plane/bff/main.py`, `assistant/` |
| Frontend live/seed adapters | `execute-plans:src/lib/bff-v1/` |
| Synthetic Management panels | `execute-plans:src/management/pages/studios/FormulaStudio.tsx`, `components/detail/ActivityMonitor.tsx`, `components/detail/StrategyPaperLiveTab.tsx`, `pages/phase2/PostmortemLibrary.tsx` |
| Trading Room duplicate paths | `execute-plans:src/agora/pages/trading-room/TradingRoomPage.tsx`, `src/agora/components/CandidateReviewDrawer.tsx` |

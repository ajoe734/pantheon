# Pantheon 架構簡化審查：BFF、Gateway、Cron 與開發工具

審查日期：2026-09-06。基準：GitHub `dev` commit `471dc5391a0f9cbde54d51730891583043708e42`（查核時以 GitHub API 確認遠端相同）。查核工作為唯讀建議；查核期間未修改 repository、dispatch 任務、操作部署或 runtime，也未讀取 secrets。後續文件版控交付不代表實作建議已落地。

## 1. 基準與限制

查核時共用工作目錄 HEAD 為 `be67218f2`（2026-09-02），相對上述 `origin/dev` ahead 6 / behind 294，且有大量其他工作者與 operator 未提交修改。審查後半改用 `git archive` 擷取上述固定 SHA 的 source snapshot；下方所有 GitHub 程式碼連結固定至完整 SHA，避免將舊工作目錄缺陷重複列入。

這是 source/configuration 審查，不是部署驗證。Compose service / profile 定義數量不等於正在運行的服務數量；未使用舊 hostname、舊 VM 或舊環境路徑做探測，也不根據較舊 AGENTS 或 architecture 文件判定目前有沒有 FE hostname。實际 hosted identity 由當前部署證據另行確認。

行數是維護複雜度訊號，不是可刪行數承諾。非 `test*` 的 Python 檔仍可能是 smoke 或 acceptance harness，而非 production runtime。

## 2. 最優先的可刪／合併候選

### A1 — BFF / Persona 拆檔後，複製的核心邏輯仍未收斂（P1，高信心）

對 `bff/main.py` 與 `bff/personas/service.py` 的 top-level functions 做 `ast.dump`（排除位置属性）精確比較：161 個同名函式，其中 **136 個 AST 完全相同，合計 3,515 行重複函式定義**。同一份 code motion 不應被視為最終去重完成。

代表例子：

| 函式 | main | personas/service | 完全相同定義行數 |
|---|---|---|---:|
| `_extract_identity_jwt` | [services/control-plane/bff/main.py:1230](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L1230) | [services/control-plane/bff/personas/service.py:8618](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/personas/service.py#L8618) | 120 |
| `_checkpoint_persona_provisioning_readback` | [services/control-plane/bff/main.py:9051](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L9051) | [services/control-plane/bff/personas/service.py:890](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/personas/service.py#L890) | 164 |
| `_project_persona_dto` | [services/control-plane/bff/main.py:9984](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L9984) | [services/control-plane/bff/personas/service.py:1841](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/personas/service.py#L1841) | 148 |
| `_pm12_resolve_quarterly_recommendation_submit_params` | [services/control-plane/bff/main.py:4790](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L4790) | [services/control-plane/bff/personas/service.py:8966](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/personas/service.py#L8966) | 231 |

`_evaluate_persona_provisioning_status` 更有兩份已經不同的 578 / 588 行實作，且都被呼叫：[services/control-plane/bff/main.py:9406](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L9406)、[services/control-plane/bff/personas/service.py:1250](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/personas/service.py#L1250)。這是需要先處理的行為漂移風險。

建議：按身份驗證、persona provisioning、read projection、recommendation submission 抽出唯一 implementation owner，讓 BFF 只引用或注入；先核對兩份差異與 global dependencies，再刪副本。不能直接宣稱這 3,515 行全可無條件刪除。驗收應覆蓋不同入口同一輸入得到相同授權、provisioning 狀態及 DTO，以及既有 owner 的 mutation/replay 語意。

全 BFF ≥20 行 top-level 函式另做精確 AST 去重，共 60 組、3,103 行額外定義；與上面的全長度 main/persona 統計口徑不同，不可相加。明細在 `bff-exact-duplicates.json`。

### A2 — OpenClaw 一般呼叫與 stream/large prompt 維持兩套 transport（P1，高信心）

[services/openclaw-gateway-adapter/assistant_openclaw_provider.py:568](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/openclaw-gateway-adapter/assistant_openclaw_provider.py#L568) 的一般 invoke 要求 CLI；[services/openclaw-gateway-adapter/assistant_openclaw_provider.py:582](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/openclaw-gateway-adapter/assistant_openclaw_provider.py#L582) 的 prompt 超過 96 KiB 時改走 HTTP；[services/openclaw-gateway-adapter/assistant_openclaw_provider.py:906](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/openclaw-gateway-adapter/assistant_openclaw_provider.py#L906) 本來已有 `/v1/responses` SSE transport。

因此不需要另起第三套 SDK abstraction；可先讓所有 user-mode invocation 收斂到已存在的 HTTP implementation，再刪：CLI subprocess/stdout 解析、argv 上限與 oversized 分支、CLI local-state/HOME 配合碼、重複的 selected-model/fallback 狀態。只有完成 transport/identity parity 後才刪除。

已有具體行為差異：large-prompt 分支在 `:584` 只傳 prompt/mode/operator/session/metadata，未沿用 selected agent、model、context/messages、trace、deadline；HTTP `:965` 使用 `self._agent_id`。統一時需要測試 operator/tenant、agent、session、explicit model、timeout、tool policy、error/stream cancellation 一致性，而非只測得到文字回答。

[OpenClaw 官方 OpenResponses API](https://docs.openclaw.ai/gateway/openresponses-http-api) 確認 HTTP 走同一 Gateway agent codepath，支援 function tools、SSE 和 session continuity。Endpoint 預設關閉，並屬 Gateway operator-access surface；必須以實際 pinned 版本與設定驗證，保留 Pantheon BFF 的身份、tenant、授權、審核和 command admission 邊界。`kernel_debug` 的 scoped runtime 不能因統一 user-mode transport 被一起刪除。

Gateway 已有 [auth-profile rotation、cooldown 和 model failover](https://docs.openclaw.ai/concepts/model-failover)。本地 provider 的 `_resolve_model_candidates`（[services/openclaw-gateway-adapter/assistant_openclaw_provider.py:211](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/openclaw-gateway-adapter/assistant_openclaw_provider.py#L211)）及 readiness 選 active model 可評估交還 Gateway，但 explicit user model 與 default fallback 語意必須保留；不能把 readiness 探測當成替使用者改模型的權限。

### A3 — 自行重做 FastAPI request/dependency injection（P1，高信心）

[services/control-plane/bff/core/app_factory.py:79](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/core/app_factory.py#L79) 至 `:193` 使用 `inspect.signature/get_type_hints` 手動解析 Header/Query/Body 與 scalar coercion，只為呼叫仍在 main 的 legacy endpoint。

建議改成真正 typed APIRouter endpoint + native Depends / Security，刪手寫 dispatcher 和 handler registry，保留 identity/role guards。這不需要 LLM 或新版 FastAPI 才能做；[FastAPI 官方 modular router/dependency 機制](https://fastapi.tiangolo.com/tutorial/bigger-applications/) 早已提供。驗收輸入驗證、header aliases、400/422 狀態碼、OpenAPI、鑑權順序與 domain command 行為。

### A4 — 為舊測試保留的 production 動態 wiring（P1，高信心）

[services/control-plane/bff/main.py:22945](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L22945) 動態替換 module class，攔截 `main.read_store = ...` 並修改 `_active_delegate`；[services/control-plane/bff/ports/read_surface_ports.py:96](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/ports/read_surface_ports.py#L96) 又使用 `__setattr__/__getattribute__` 轉送。[services/control-plane/bff/main.py:986](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L986) 則明確為舊 fake read-store 留 mutation audit writer fallback。

建議讓測試透過既有 [services/control-plane/bff/bootstrap/dependencies.py:29](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/bootstrap/dependencies.py#L29) `AppDependencies` 或 FastAPI overrides 注入，再刪 module monkeypatch 和 read-port-to-write fallback。這能降低「宣稱 read-only interface、卻因 fake object shape 選另一 write path」的維護負擔。保留獨立、正式的 audit writer 與測試隔離能力。

另有同一 runtime client 的兩份 dynamic file loader：[services/control-plane/bff/command_adapters/runtime_adapter.py:28](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/command_adapters/runtime_adapter.py#L28)、[services/control-plane/bff/command_executor.py:94](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/command_executor.py#L94)。可合併成一個正式可匯入 package/client factory，避免兩套 instance lifecycle。

### A5 — Cron 的 subprocess / polling wrapper 可用上游能力收斂（P2，高信心，替代版本待驗證）

[services/control-plane/cron/openclaw_client.py:67](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/cron/openclaw_client.py#L67) 每次 RPC 啟動 CLI；[services/control-plane/cron/openclaw_client.py:89](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/cron/openclaw_client.py#L89) 輪詢最近 5 個 run，找不到 exact run ID 會 fallback 到 `entries[0]`。這不僅冗長，也可能把另一個執行當成本次完成。

[OpenClaw automations](https://docs.openclaw.ai/cli/cron) 已記載 run wait / exact-run 操作；[Gateway client 官方套件](https://docs.openclaw.ai/gateway/clients) 提供 reference connection/reconnect/events 實作。文件目前指定 client/protocol `2026.8.1`，須與 Gateway 實際版本一起釘版驗證。Node client 並非 Python 的直接 drop-in；不能為了「簡化」無條件新增 Node sidecar。優先採能減少總元件數的現有 Gateway contract。

可刪的是自行管理 polling/CLI lifecycle 的 plumbing。[services/control-plane/cron/service.py:47](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/cron/service.py#L47) 的 business handoff schema、StagePlanner、approval/saga、runtime-capital compatibility 必須保留；定時本身也不能讓 LLM 自由記住何時執行而取代 durable scheduler。

### A6 — 已 sunset 的 BFF tombstone routes（P2，高信心，刪除須確認 consumer）

[services/control-plane/bff/main.py:1399](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L1399) 記載 sunset 為 2026-05-25；仍有 12 個只回 410 的 handler：ranking 5、tools 5、runtime 1、deployment 1。

來源：[services/control-plane/bff/management_read_models/ranking_router.py:434](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/management_read_models/ranking_router.py#L434)、[services/control-plane/bff/tools_integrations/router.py:717](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/tools_integrations/router.py#L717)、[services/control-plane/bff/runtime/router.py:1201](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/runtime/router.py#L1201)、[services/control-plane/bff/deployment/router.py:739](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/deployment/router.py#L739)。統一 response helper 位於 [services/control-plane/bff/main.py:7652](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/main.py#L7652)。

先驗證 execute-plans consumer 與足夠 observation window 的 access logs，再移除 route / alias / 僅為其存在的參數 wiring。尚需外部相容時，集中小型 explicit tombstone router。不可把「410」誤判為仍有完整 obsolete business implementation；其 unreachable bodies 已被刪掉。

### A7 — 開發流程自己製造的 approval 後續修補（P2，中高信心，migration 後刪）

[scripts/git/github_review_bridge.py:358](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/scripts/git/github_review_bridge.py#L358) 的 `task_brief_only_successor` 及 [scripts/git/task_review_merge_gate.py:1052](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/scripts/git/task_review_merge_gate.py#L1052) 專門處理「核准後又提交 task brief」的 approval carry-forward。

先停止製造核准後的 generated brief commit，讓完成事實留在 canonical task state；待舊 pending PR 清空，再刪此類 successor exception。保留 scope、independent reviewer eligibility、reviewed commit identity；這些不是新 LLM 能力的替代對象，也不能順便放寬 code-change approval。

這是降低程序步驟與補丁的流程簡化，不是 LLM 取代 deterministic review evidence。[docs/02-architecture/development-tooling-product-boundary.md:42](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/docs/02-architecture/development-tooling-product-boundary.md#L42) 已要求 generated task context 為 runtime state，不能當 task evidence 或 approval ledger。

### A8 — 一次性歷史事故 migration 不應永久留在 hot path（P2，中信心，先盤點）

[.orchestrator/supervisor.py:11602](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/.orchestrator/supervisor.py#L11602) 至 `:11709` 為 legacy review-intent collision migration；`:11746` 仍由一般 recovery 呼叫。先盤點所有舊任務、證明格式遷移已完成，再移出正常調度路徑，必要時保留獨立 offline recovery 工具。

不能只因名字包含 legacy 就刪：有效 pending review/lease 可能仍需 recovery。CAS、原 actor / nonce、idempotency、audit journal 都必須保持。對儲存格式的兼容應分開討論：舊 archive 的稽核讀取可以留在 offline 工具，無須放進 scheduler hot read。

## 3. 已完成的簡化：本次不重複立項

- BFF main 已由舊工作目錄 60,472 行降為 dev 的 **22,959 行**。目前 `AppDependencies`、typed ports、domain router factories 已存在；要做的是去重與移除殘留相容層。
- 最新 dev 的 BFF 和 `.orchestrator` 非測試 Python 經 AST 掃描，沒有 statement-list 中 unconditional return/raise 後仍有 statements 的明顯 unreachable tails。這個檢查不是完整 reachability proof，但不能再直接引用舊 BFF-DEADCODE 缺陷。
- [.orchestrator/rewrite/task_state_store.py:1](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/.orchestrator/rewrite/task_state_store.py#L1) 已使用 small head + transition delta journal；完整 chain/legacy archive hashing 是 offline validation，已非正常排程讀取。
- [.orchestrator/supervisor.py:14236](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/.orchestrator/supervisor.py#L14236) 已有 explain-dispatch；8 月舊 proposal 說沒有的狀況不適用。
- [docs/02-architecture/development-tooling-product-boundary.md:133](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/docs/02-architecture/development-tooling-product-boundary.md#L133) 已訂出開發工具可移除順序。產品不依賴 `.orchestrator`；但目前還有 tasks/workers/leases 時，不能因 LLM 能力提高而直接刪掉開發工具權威狀態。
- `services/persona/agent_usability_validation.py` 為 23,384 行 3000-case **acceptance harness**，不是 production persona 智慧層。可移到 acceptance tooling、去重 fixtures；保留 no-leakage / holdout / paper-lifecycle assurance，不能把它列成「LLM 可取代的 23k 行 business code」。參見 [services/persona/agent_usability_validation.py:1](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/persona/agent_usability_validation.py#L1)。

## 4. Source complexity metrics

計算方法：snapshot 下所有 `.py`，排除路徑元件名以 `test_` 起頭或等於 `tests`；不排除 smoke/acceptance，所以不等同 production runtime LOC。字串與空行計入。

| 範圍 | 非 test Python 檔 | 行數 |
|---|---:|---:|
| `.orchestrator` | 50 | 47,439 |
| `scripts` | 169 | 92,848 |
| BFF | 250 | 157,704 |
| Persona | 16 | 34,205 |
| OpenClaw adapter | 16 | 12,753 |

最大檔案：BFF main 22,959、persona service 14,050、supervisor 15,386、ai_status 9,401。拆檔目標不應只設定 main LOC；還要量測相同決策的 implementation owner 數量、duplicate functions、舊入口數量、必要服務啟動集合與 operator 完成任務的步驟。

## 5. 對最小架構提案的獨立檢視

對「FE + BFF/domain owners、一個 LLM Gateway、deterministic quant/execution、optional research jobs、獨立 dev tooling」的方向沒有根本異議，但需把容易被省略的能力明確列在相應層。這不是要多保留一堆微服務：可以合併 process/deployment units，仍保留 module、資料與權威邊界。

### 5.1 建議的 core / optional profile 能力分組（提案，不是現有 running topology）

| 建議 profile / 層 | 可合併的現有能力 | 必須保存的責任 |
|---|---|---|
| `core-control` | FE/BFF projection；Persona、Strategy、Registry、Capital、Governance、Deployment、Trade Journal 的 owner modules 可評估 modular monolith | BFF 不成為 shadow write authority；每一 aggregate 仍只有一個 command/store owner；身份、tenant、RBAC、approval、risk limits、版本、idempotency |
| `core-agent` | 一個 OpenClaw Gateway + 薄 Pantheon adapter；conversation/tool catalog/context projection 收斂 | Gateway 管 model/session/LLM orchestration，Pantheon adapter 管有效 tool 與 domain admission；conversation memory 不是資金或核准 ledger |
| `core-runtime` | RuntimeManager、broker adapters、paper/live runner、signal producer、reconciliation、kill/safe-mode control | 實際成交、部位、資金、風控、取消、重試、停機與回滾必須是 deterministic 且 durable；不依賴模型可用性 |
| `core-data-observability` | 必要 source ingestion、telemetry、lifecycle/lineage projection、incident/feedback、outbox consumers 可在有隔離的 worker host 合併 | 資料 freshness/provenance、投影進度與 durable jobs；模型摘要可替代文字整理，不能替代交易或 readiness 真相 |
| `research`（按功能啟用） | 研究 orchestrator、distillation、alpha replication、training preview、policy-learning、evaluation、optimizer、evolution/RL、experiments OSS | 昂貴 compute 按需 job；啟用研究能力時仍有 dataset/model/artifact registry、holdout 和 promotion evidence |
| `dev-tooling`（獨立） | Supervisor/TaskStore/workers/bridge/git integration | 不進 product image/runtime、不與 product readiness 綁定；可另做「精簡開發流程」計畫 |

DB、event bus、object/artifact store、backup/recovery 是這些 profile 的共同持久化基礎。不能只為減 container 數就把其 durability/transaction 語意改成 LLM conversation。至於 NATS / Redis / Postgres 是否能進一步合併，必須另做實際 consumer、吞吐、重播、ordering 與 failure-domain 驗證；本審查沒有證明它們可互相替代。

Compose [docker-compose.yml:10](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/docker-compose.yml#L10) 定義上述多種能力，並含 smoke/dormant/benchmark profiles；這些是宣告，不是 live container count。`core-*` 名稱是本報告的新分組建議，並非既有 config。也不建議為了達到固定 service 數量，先搬一堆 code 然後再補分散式一致性。

### 5.2 最小架構容易漏掉的產品責任

1. **Capital / governance / authorization 必須显式存在。**「deterministic execution」不足以表達配額、資金保留、persona-capital binding、approval 到期、risk-increasing vs risk-decreasing 行為。[services/capital/main.py:188](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/capital/main.py#L188) 有 owner-side idempotency，`:460` 後處理 risk-increasing eligibility。模型只能提出 proposal；不可自行批准或跳過 owner 判斷。
2. **Readiness、telemetry、incident、reconciliation 與緊急控制是 core。**不是 optional research。[services/runtime_manager/kill_switch_controller.py:1](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/runtime_manager/kill_switch_controller.py#L1) 定義 safe-mode fast path；[services/trade_journey/lifecycle_projector.py:1](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/trade_journey/lifecycle_projector.py#L1) 使用 transactional projection 與 consumed receipts。LLM 暫時不可用時，既有部位仍要可觀察、可對帳、可減風險。
3. **Data plane 與 artifact lineage 必須保留。**長 context/模型記憶可以簡化知識摘要或 conversation cache，但不能取代 source timestamps、licensed dataset identity、strategy/model versions、backtest inputs、order/fill history 和帳務 truth。必要 ingestion 不应因將 research 設為 optional 而消失。
4. **Deployment/promotion 與 durable background work 有獨立生命週期。**[services/deployment/outbox_consumer_worker.py:1](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/deployment/outbox_consumer_worker.py#L1) 已明確要求 durable consumption 和 duplicate idempotency。可以合併 worker process，但不可把 transaction/outbox 或 job ownership 改成模型「稍後執行」。persona first evaluation、lifecycle projection、paper fleet reconcile 也不全是可關的研究任務。
5. **一個 Gateway 不代表只剩一個授權邊界。**[docs/decisions/control-plane-router-enforcement-ownership.md:15](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/docs/decisions/control-plane-router-enforcement-ownership.md#L15) 把 ingress auth、routing intent、approval、domain execution 分開。Gateway native tool discovery 不能成為合法下單的充分條件；product user 與 read-only kernel diagnostic / development agent sandbox 不應被混為同一權限。
6. **Conversation、Persona、Memory、Decision Journal 是不同 truth。**可以共享 storage/library，但不能用模型 session continuity 取代 canonical PersonaMandate、CapitalBinding、approval audit、TradeJournal。[docs/02-architecture/product-aggregate-ownership.yaml:5](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/docs/02-architecture/product-aggregate-ownership.yaml#L5) 已列單一 owner；保留語意時才可減少 process。
7. **研究元件 optional 是產品能力選擇，不能靠沒有流量推導。**目前若 UI/mandate/acceptance 要求 evolution、training、search、multi-persona consultation，則關閉應有清楚的 capability/readiness 表現；不能讓 UI 仍顯示可用、後端回 synthetic success。

### 5.3 推薦的接受標準

先對一條 paper-only persona → research artifact（若啟用）→ approval → deployment/runtime → signal/order/fill → telemetry/journal 的代表性 journey 證明新分組保留 behavior。再做 duplicate request、worker crash/restart、model unavailable、stale source、錯 tenant、expired approval、kill/safe-mode、rollback 的 bounded checks。不要把 supervisor healthy 或 repository task done 當成產品驗收。

第一輪優先 A1–A4（去重現有 implementation、收斂 transport、刪 framework/test 相容碼）；第二輪做 A5–A8 與 profile 分組；第三輪才討論 research capability 下架及更大範圍服務合併。模型升級和 topology 變更分開比較，可避免無法判斷行為差異來自哪一項。

## 6. 產物與重現

- Source snapshot：可由上方固定完整 SHA 重建；暫存程式碼副本不納入文件交付。
- 精確 AST 重複明細：[bff-exact-duplicates.json](bff-exact-duplicates.json)。
- 本資料夾保存審查產物；未包含 runtime 狀態或任何簡化實作。
- 所有 source links 固定至 `471dc5391a0f9cbde54d51730891583043708e42`；官方 OSS 文件則是 2026-09-06 查閱的現行能力描述，實際可用版本由 dependency/lock 與 hosted identity 審查另外確認。

# Pantheon Trade Journey 端到端監看與管理 Gap 規格

**文件狀態：** Proposed
**日期：** 2026-07-11
**範圍：** Pantheon control plane、BFF、telemetry/lineage、execution plane、`execute-plans` Management Console
**目標環境：** paper、canary、live
**主要使用者：** 投資主管、交易主管、風控、值班營運、Persona 管理者、稽核人員

## 1. Executive summary

Pantheon 已分別提供 Persona、研究、演化、候選排名、治理、資金綁定、runtime、execution、audit、evidence 與 reconciliation 等領域頁面，但操作人員目前必須自行在不同頁面與 API 之間拼接識別碼，才能回答一個基本問題：

> 某個 Persona 為什麼提出這個策略，它如何成為可交易候選，誰批准、使用哪筆資金、經過哪些風控，最後送出什麼委託、成交多少，以及帳務是否一致？

這是端到端可觀測性與營運管理缺口，不是單純導覽或前端排版問題。目標狀態必須提供 canonical `Trade Journey` read model 與主入口，將下列生命週期呈現在同一個可搜尋、可即時監控、可歷史重播、可治理操作的工作台：

```text
研究理由 → 策略候選 → 候選評分 → 晉級決策 → 資金綁定 →
部署/Runtime → 交易訊號 → 交易決策 → 風控 → 委託 → 成交 → 對帳
```

現有領域頁面保留為專業 drill-down；`Trade Journey` 成為 Cockpit 與日常交易營運的主幹。

## 2. 問題定義

### 2.1 現況

目前相關資訊分散於：

- Persona Intent、Persona Detail、Experiment、Evolution Journal、Alpha Factory；
- Promotion & Allocation、Persona League、Human Inbox、Governance；
- Strategy、Deployment、Runtime、Execution Loop、Trading Pulse；
- Risk、Audit、Incident、Evidence、Broker Readiness。

這些頁面以後端 bounded context 為中心，而不是以營運人員需要處理的交易生命週期為中心。即使每個頁面各自正確，整體仍缺少一致的關聯、狀態與時間觀。

### 2.2 具體缺口

| Gap | 現況影響 | 目標能力 |
|---|---|---|
| 無 canonical journey identity | 相同交易在不同系統只能靠人工猜測關聯 | 每次 trade intent 有穩定 `journey_id`，並保存所有 correlation ids |
| 無統一 read model | 前端需 client-side join，多來源時間點不一致 | BFF 回傳 server-composed snapshot 與 timeline |
| 研究到交易關係不完整 | 無法證明交易源自哪個理由、候選與版本 | Research Journey、Strategy Lifecycle、Trade Journey 可追溯關聯 |
| 無階段狀態模型 | 無法判斷目前停在哪裡或是否逾時 | 明確 stage、status、owner、SLA、blocked reason |
| 無任意 ID 解析 | 值班人員拿到 broker order id 後難以反查 | 任一 persona/strategy/decision/order/fill id 可 resolve journey |
| 無歷史重播 | 現值覆蓋當時事實，無法重建決策 | append-only event timeline、版本化 payload 與 as-of replay |
| 無端到端即時更新 | 必須刷新多頁面追進度 | journey SSE stream，支援 reconnect/cursor/dedup |
| 無一致證據鏈 | audit、evidence、broker readback 分離 | 每階段顯示 evidence refs 與 completeness |
| 無旅程層級治理操作 | 異常後需跨頁處理，容易失去上下文 | pause、cancel、escalate、retry 等 governed actions |
| 無 journey SLO | 不知道卡住、延遲或資料缺漏 | stage latency、stalled、orphan、reconciliation mismatch alerts |

## 3. 核心設計原則

1. **Journey-first，domain drill-down second。** 主畫面回答端到端問題，既有頁面處理領域細節。
2. **Backend-composed truth。** 前端不得自行把多個 API 組成「看似完整」的交易旅程。
3. **Correlation is a contract。** 所有 producer 都必須攜帶標準 correlation envelope。
4. **Append-only historical truth。** 更正以新事件表達，不覆寫歷史決策與 broker 回應。
5. **Read 與 action 分離。** Journey read model 不因使用者有寫入權限而改變；操作另走 governed command。
6. **No invented completeness。** 缺少事件時必須顯示 `unknown/incomplete`，不得推測成 completed。
7. **Research 與 trade 非一對一。** 一個研究可產生零到多個策略；一個部署策略可產生多個 Trade Journey。
8. **Paper/canary/live 同構。** 相同 schema 與 UI，清楚標示 environment 與 capital impact。
9. **安全優先。** Journey 頁面不成為繞過 Human Gate、risk gate 或 broker policy 的捷徑。

## 4. Domain boundary 與關係模型

```text
Research Journey
  ├─ hypothesis / rationale / evidence
  └─ Strategy Candidate(s)
       └─ Strategy Lifecycle
            ├─ evaluation / ranking / promotion
            ├─ approved artifact version
            └─ deployment + capital binding
                 ├─ Trade Journey A
                 ├─ Trade Journey B
                 └─ Trade Journey C
```

### 4.1 三種 journey

- `research_journey_id`：研究問題到候選產物；允許被淘汰而沒有交易。
- `strategy_lifecycle_id`：某個 strategy lineage/version 從候選、評估、晉級到部署。
- `journey_id`：單次 trade intent 從訊號形成到 reconciliation 終態。

本規格的主要 UI 是 Trade Journey，但頁首必須能向上連結 Strategy Lifecycle 與 Research Journey。

### 4.2 Trade Journey cardinality

- 一個 signal 可產生零或多個 decision（例如分帳戶、分 tranche）。
- 一個 decision 可產生零或多個 order intent。
- 一個 order intent 可因 replace 形成多個 broker orders。
- 一個 broker order 可有零或多個 fills。
- 一個 journey 可在 blocked/rejected/cancelled/expired 終止，不能假設一定成交。

## 5. Canonical correlation contract

所有新事件與逐步補強的既有事件至少應提供：

```json
{
  "journey_id": "tj_...",
  "research_journey_id": "rj_...",
  "strategy_lifecycle_id": "sl_...",
  "causation_id": "event_or_command_id",
  "correlation_id": "cross_service_trace_id",
  "persona_id": "persona_...",
  "strategy_id": "strategy_...",
  "strategy_version": "immutable_version",
  "artifact_id": "artifact_...",
  "candidate_id": "candidate_...",
  "promotion_decision_id": "decision_...",
  "capital_pool_id": "pool_...",
  "persona_capital_binding_id": "pcb_...",
  "deployment_id": "deployment_...",
  "runtime_id": "runtime_...",
  "runtime_binding_id": "binding_...",
  "signal_id": "signal_...",
  "trade_decision_id": "trade_decision_...",
  "risk_decision_id": "risk_...",
  "order_intent_id": "oi_...",
  "client_order_id": "client_...",
  "broker_order_id": "broker_...",
  "fill_ids": ["fill_..."],
  "reconciliation_id": "recon_...",
  "environment": "paper|canary|live",
  "occurred_at": "RFC3339",
  "recorded_at": "RFC3339",
  "schema_version": "1"
}
```

不是每個階段都會填滿所有欄位，但 producer 不得丟失已知的 upstream identifiers。敏感的 broker/account identifiers 必須依 RBAC 遮罩，但仍保留穩定可搜尋 token。

## 6. Journey stage 與狀態機

### 6.1 標準 stages

1. `research_rationale`
2. `strategy_candidate`
3. `candidate_evaluation`
4. `promotion_decision`
5. `capital_binding`
6. `deployment_runtime`
7. `signal_generation`
8. `trade_decision`
9. `risk_evaluation`
10. `order_submission`
11. `broker_acknowledgement`
12. `fill_management`
13. `ledger_booking`
14. `reconciliation`

### 6.2 Stage status

`not_applicable | pending | active | waiting_human | blocked | succeeded | partially_succeeded | rejected | failed | cancelled | expired | unknown`

### 6.3 Journey roll-up status

`open | waiting_human | blocked | executing | partially_filled | completed | completed_with_variance | failed | cancelled | expired | incomplete`

Roll-up 必須由明文化規則計算，不得讓前端自行推導。`completed` 至少要求 execution 終態、ledger booking 與 reconciliation 完整；只有成交不能顯示 completed。

### 6.4 Terminal 與 correction

- Broker reject、risk reject、operator cancel 可以是合法終態。
- Reconciliation mismatch 為 `completed_with_variance`，直到 correction event 封閉。
- Late-arriving event 不可改寫舊事件；read model 重算 snapshot 並增加 revision。
- 一個 journey 不得同時顯示互斥終態；發生衝突時標記 `incomplete` 並產生 data-quality incident。

## 7. Canonical Journey read model

### 7.1 List row

至少包含：

- `journey_id`、environment、current stage/status、severity；
- Persona、strategy/version、instrument、side、target quantity/notional；
- decision、capital pool、runtime、broker 摘要；
- created/updated/terminal timestamps、目前階段耗時與總耗時；
- waiting human、blocked/rejected/mismatch/orphan flags；
- evidence completeness 與 data freshness；
- masked live-capital indicator。

### 7.2 Detail snapshot

- identity 與 immutable provenance；
- current status、stage progress、SLA；
- research rationale 摘要與來源；
- candidate evaluation、ranking formula/version、score breakdown；
- promotion/human decisions、actor、comment、policy version；
- capital binding、limit snapshot、deployment/runtime version；
- signal input、trade decision rationale、model/persona/runtime version；
- pre-trade risk checks，每項 pass/fail/waive 與 policy version；
- order intent、broker request/ack/reject/replace/cancel；
- fills、fees、slippage、partial fill 狀態；
- ledger entries 與 reconciliation 差異；
- incidents、interventions、audit receipts、evidence refs；
- completeness、missing stages、source freshness、read-model revision。

### 7.3 Timeline event

```json
{
  "event_id": "evt_...",
  "journey_id": "tj_...",
  "stage": "risk_evaluation",
  "event_type": "risk.check.completed",
  "status": "blocked",
  "occurred_at": "...",
  "recorded_at": "...",
  "actor": {"type": "persona|human|service|broker", "id": "..."},
  "summary": "Position limit exceeded",
  "reason_code": "POSITION_LIMIT",
  "input_refs": [],
  "output_refs": [],
  "evidence_refs": [],
  "policy_refs": [],
  "causation_id": "...",
  "correlation_id": "...",
  "payload_redaction": "none|partial|full",
  "schema_version": "1"
}
```

## 8. BFF/API gap

### 8.1 Required read endpoints

| Method | Route | 用途 |
|---|---|---|
| GET | `/bff/management/trade-journeys` | 搜尋、篩選、分頁、排序的 journey list |
| GET | `/bff/management/trade-journeys/{journeyId}` | canonical detail snapshot |
| GET | `/bff/management/trade-journeys/{journeyId}/timeline` | cursor-paginated append-only timeline |
| GET | `/bff/management/trade-journeys/{journeyId}/graph` | research/strategy/trade 關係 graph |
| GET | `/bff/management/trade-journeys/resolve` | 由任意已知 ID 解析一或多個 journeys |
| GET | `/bff/management/trade-journeys/{journeyId}/evidence` | evidence completeness 與 refs |
| GET | `/bff/management/trade-journeys/{journeyId}/replay` | `as_of` 歷史 snapshot |
| GET | `/bff/management/trade-journeys/stream` | journey SSE updates |
| GET | `/bff/management/trade-journeys/metrics` | stage latency、stalled、error、mismatch aggregates |

### 8.2 Query contract

List 至少支援：`q`、`persona_id`、`strategy_id`、`candidate_id`、`decision_id`、`order_id`、`broker_order_id`、`environment`、`stage`、`status`、`severity`、`instrument`、`capital_pool_id`、`from`、`to`、`stalled`、`waiting_human`、`reconciliation_state`、cursor 與 sort。

`resolve?q=` 必須回傳 match type、matched identifier、候選 journeys 與 ambiguity，不可任意選第一筆。若一個 strategy 對應多筆交易，回傳可篩選結果集合。

### 8.3 Required governed actions

| Action | 適用情境 | 最低 gate |
|---|---|---|
| `escalate` | stalled、data gap、風險或 broker 異常 | operator |
| `request_human_review` | promotion/trade/risk 需要覆核 | policy + reviewer role |
| `pause_runtime` | 防止新 trade intents | runtime policy + reason |
| `cancel_order` | 可撤委託 | broker readback + permission + confirmation |
| `retry_reconciliation` | 暫時性對帳失敗 | idempotency + operator |
| `acknowledge_incident` | 值班接手 | incident permission |
| `open_kill_switch` | 僅導向既有受治理流程 | 不得在 Journey 內弱化既有 gate |

命令回應必須包含 receipt、idempotency key、policy result、actor、before/after refs；UI 不得 optimistic 顯示成功。

## 9. Event ingestion 與 materialization

### 9.1 Producer inventory

必須盤點並補齊以下 producer 的 correlation envelope：Persona/OODA、research、evolution/optimizer、ranking/promotion、human gate、capital binding、deployment、runtime manager、signal/decision engine、risk、execution router、broker adapter、fill handler、ledger、reconciliation、audit、incident 與 evidence service。

### 9.2 Materializer responsibilities

- 消費 versioned events，依 `event_id` 冪等去重；
- 支援 out-of-order 與 late events；
- 維護 snapshot、timeline index、identifier reverse index 與 graph edges；
- 檢查必需事件、互斥終態、orphan ids 與 temporal inconsistency；
- 記錄 source watermark、lag、revision 與 rebuild status；
- 可由 event history deterministic rebuild；
- 不把 read-model 修補寫回 execution source of truth。

### 9.3 Legacy/backfill

- 先對可可靠關聯的歷史紀錄建立 `journey_id` backfill mapping；
- 低信心關聯標示 `inferred` 與 confidence，不能冒充原生 correlation；
- 無法關聯的紀錄進入 orphan queue；
- backfill 前後數量、遺失率與衝突率需有 evidence report。

## 10. Frontend information architecture

### 10.1 Canonical routes

- `/management/trade-journeys`
- `/management/trade-journeys/{journey_id}`
- `/management/trade-journeys/resolve?q={identifier}`

### 10.2 List/workbench

預設優先顯示 live/canary 的 `blocked`、`waiting_human`、`stalled`、`rejected`、`reconciliation mismatch`，並提供：

- 全域任意 ID 搜尋；
- environment、stage、status、Persona、strategy、instrument、time range filters；
- saved views：In flight、Needs attention、Awaiting human、Broker rejects、Recon mismatch、Completed；
- status、current stage、age/SLA、Persona/strategy、capital impact、evidence completeness；
- URL-persisted filters，能分享與返回原 context；
- server-side pagination/sort，禁止下載全量後 client-side 過濾。

### 10.3 Journey detail

頁面資訊階層：

1. **Header：** journey id、environment、status、severity、freshness、live capital warning。
2. **Stage rail：** 14 個 stage 的狀態、耗時、owner、block reason。
3. **Current attention panel：** 現在需要誰做什麼、deadline 與允許動作。
4. **Timeline：** 可按 stage/event/actor 篩選的事件序列。
5. **Decision rationale：** research、candidate、promotion、trade 與 risk reason。
6. **Execution：** intent、orders、replace chain、fills、slippage。
7. **Reconciliation：** broker/ledger/position 差異與 closure。
8. **Evidence & audit：** receipts、policy、artifact、raw evidence drill-down。
9. **Related journeys：** 同 strategy、signal、batch、parent/child journeys。

Payload 預設摘要，原始內容依權限展開。所有時間同時提供 UTC 精確值與使用者時區顯示。

### 10.4 Cross-entry integration

以下頁面必須提供 `View Trade Journey(s)`：Persona、Strategy、Candidate、Human Inbox、Capital Binding、Deployment、Runtime、Trading Pulse、Order、Fill、Incident、Audit、Evidence、Performance Attribution。Cockpit 必須新增 Needs Attention journey cards。

## 11. 即時監控與歷史重播

### 11.1 Live monitoring

- SSE 事件具 `event_id`、`journey_id`、revision 與 cursor；
- reconnect 使用 last-event-id，前端 dedup；
- SSE 中斷時顯示 stale banner，不可暗示即時；
- snapshot revision 落後時 refetch；
- stalled threshold 依 stage/environment 配置；
- live order reject、risk bypass attempt、kill-switch、recon mismatch 觸發高優先 alert。

### 11.2 Replay

- 支援 `as_of` 與 timeline scrubber；
- 重現當時 Persona/model、strategy/artifact、policy、binding、risk limit 與 broker payload 版本；
- 清楚區分 `occurred_at` 與 `recorded_at`；
- correction/late event 以 overlay 顯示；
- replay 全程 read-only，不能從歷史狀態直接觸發命令。

## 12. Governance、RBAC 與資料保護

- Research rationale、帳戶、broker payload、人工 comment 依角色與 environment 遮罩；
- live capital write actions 預設不可用，必須經既有 RBAC、MFA/confirmation、human gate 與 audit；
- UI 顯示「為何不可操作」，但不得洩漏敏感 policy internals；
- resolve/search 也要受 row-level scope，不能透過 ID existence side channel 洩漏；
- export 需另有權限、watermark、scope、理由與 audit receipt；
- retention 依 audit/compliance policy，刪除請求不能破壞必需交易稽核鏈。

## 13. SLO、data quality 與告警

初始建議目標（正式值由營運與風控核定）：

| Metric | Paper | Canary/Live |
|---|---:|---:|
| producer event 到 read model p95 | ≤ 10s | ≤ 3s |
| journey detail API p95 | ≤ 1.5s | ≤ 1.0s |
| 任意 ID resolve p95 | ≤ 1.0s | ≤ 1.0s |
| 原生 correlation 完整率 | ≥ 99% | ≥ 99.9% |
| reconciliation 終態完整率 | ≥ 99% | 100% 或明確 incident |

必須量測：stage latency、stalled count、orphan event rate、missing identifier rate、conflicting terminal rate、late-event lag、materializer lag、SSE disconnect、broker reject、partial fill aging、reconciliation mismatch aging。

## 14. Non-functional requirements

- List 與 timeline 使用 cursor pagination；
- identifier reverse lookup 有索引且 tenant/environment scoped；
- snapshot 與 timeline response 有 schema version；
- materializer 支援 replay/rebuild 與 blue-green schema migration；
- BFF source timeout 採 partial/degraded response，列出 unavailable sources；
- 不得因單一 enrichment service 故障讓 execution truth 消失；
- accessibility：鍵盤操作、非顏色唯一狀態、screen-reader stage labels；
- responsive：手機可處理 alert 與批准，但複雜 graph 可轉為線性 timeline；
- localization：狀態 code 穩定，顯示文案可翻譯。

## 15. Current-to-target gap matrix

| Capability | Current | Target | Priority |
|---|---|---|---|
| Persona/research visibility | 分散可見 | 向上 lineage 與 rationale snapshot | P0 |
| Candidate/promotion | 有領域頁面 | 與 trade journey 可雙向追蹤 | P0 |
| Capital/runtime | 可個別查詢 | 保存 decision-time binding snapshot | P0 |
| Signal/trade decision | 未形成統一 journey | canonical decision stage | P0 |
| Risk result | 分散/摘要 | 每項 check、policy/version、reason | P0 |
| Order/fill/recon | 各來源證據 | replace/fill/ledger/recon 完整鏈 | P0 |
| 任意 ID resolve | 無統一入口 | reverse index + ambiguity handling | P0 |
| 即時更新 | 領域式更新 | journey SSE + freshness | P1 |
| 歷史重播 | 不完整 | as-of deterministic replay | P1 |
| Journey actions | 跨頁處理 | governed contextual actions | P1 |
| Journey analytics | 無 | funnel、latency、failure/mismatch | P2 |

## 16. Delivery plan 與 task decomposition

### Phase 0 — Discovery and contract freeze

**TJ-001 Producer/correlation inventory**

- 盤點每階段 source of truth、event schema、ID 與 retention。
- 產出 missing-correlation matrix 與 owner。
- 驗收：至少用一筆 paper 與一筆 broker sandbox flow 證明現況可/不可關聯。

**TJ-002 Journey domain/state contract**

- 固化三種 journey、stage/status、terminal、cardinality 與 schema versioning。
- 驗收：Architecture、Execution、Risk、Governance、Frontend 共同 sign-off。

### Phase 1 — P0 canonical truth

**TJ-003 Correlation envelope propagation**

- 補齊 producer propagation、idempotency 與 trace context。
- 驗收：新 paper flow 從 signal 到 recon 無人工 join；缺欄位會被 contract test 擋下。

**TJ-004 Journey materializer and reverse index**

- 建 snapshot、timeline、graph、reverse lookup、quality checks、rebuild。
- 驗收：out-of-order、duplicate、late event、replace、partial fill、reject、cancel 測試通過。

**TJ-005 BFF read API**

- 實作 list/detail/timeline/graph/resolve/evidence 與 degraded semantics。
- 驗收：禁止 client-side domain join；OpenAPI/schema/route shadowing/authorization tests 通過。

**TJ-006 Trade Journey frontend P0**

- 實作 canonical routes、list、detail stage rail、timeline、任意 ID resolve。
- 驗收：paper happy path、risk reject、broker reject、partial fill、recon mismatch 均正確呈現。

### Phase 2 — Operations

**TJ-007 Live SSE and attention model**

- SSE、freshness、stalled detection、Cockpit cards。
- 驗收：disconnect/reconnect/dedup/stale/refetch 行為自動化測試通過。

**TJ-008 Governed journey actions**

- contextual actions、receipts、Human Inbox return context。
- 驗收：RBAC deny、confirmation、idempotent retry、broker readback 與 audit tests 通過。

**TJ-009 Cross-entry links and IA cleanup**

- 各領域頁面新增 Journey 入口，更新 sidebar、command palette、breadcrumbs。
- 驗收：route crawl、query preservation、back navigation、no dead link。

### Phase 3 — Replay, quality and production closure

**TJ-010 Historical replay and legacy backfill**

- as-of replay、correction overlay、confidence-labelled backfill、orphan queue。
- 驗收：指定歷史案例可重現且 hash/versions 一致。

**TJ-011 SLO dashboards and data-quality incidents**

- metrics、alerts、runbook、materializer health。
- 驗收：故障注入可觸發 stalled/orphan/conflict/lag 告警。

**TJ-012 Hosted acceptance and production closeout**

- dev hosted E2E、paper/canary soak、security/a11y/performance、evidence archive。
- 驗收：所有 Definition of Done 與 rollout/rollback gate 完成。

### Dependency order

```text
TJ-001 → TJ-002 → TJ-003 → TJ-004 → TJ-005 → TJ-006
                           ├────────→ TJ-007 → TJ-009
                           ├────────→ TJ-008
                           └────────→ TJ-010 → TJ-011 → TJ-012
```

## 17. End-to-end acceptance scenarios

1. **Paper happy path：** Persona research → candidate → human promotion → binding → signal → risk pass → order → fills → ledger → exact reconciliation。
2. **Candidate rejected：** 旅程止於 promotion，清楚顯示理由且沒有 execution records。
3. **Risk blocked：** 顯示 failing check、policy/version、輸入 snapshot，證明未送 broker。
4. **Broker rejected：** 顯示 request、ack/reject、reason、incident 與未成交狀態。
5. **Partial fill + replace：** order chain、fills、remaining quantity、cancel/replace causation 正確。
6. **Human waiting：** list/Cockpit 顯示 owner、deadline；Human Inbox 返回原 journey context。
7. **Reconciliation mismatch：** 不顯示 completed，差額、來源與 remediation 可見。
8. **Late event：** snapshot revision 更新，timeline 保留 occurred/recorded ordering。
9. **任意 ID：** persona/strategy/decision/client order/broker order/fill 任一 ID 都能 resolve，歧義時列出選項。
10. **RBAC：** 無權限者看見遮罩且不能透過 resolve 推測 live account/order existence。
11. **Degraded source：** enrichment 故障仍保留 execution truth，顯示 unavailable source 與 freshness。
12. **Replay：** 使用 as-of 重建當時版本，不受現在 Persona/policy/binding 變更影響。

## 18. Definition of Done

此 gap 只有在以下條件全部成立時才能關閉：

- 新交易 100% 具有原生 `journey_id`，且 canary/live correlation 達核定門檻；
- 由任一核心 ID 可解析到 journey，歧義處理明確；
- Journey detail 完整呈現 research/strategy/promotion/binding/decision/risk/order/fill/recon；
- stage/status 由 canonical backend 規則計算，前端沒有跨 domain join；
- live monitoring 有 freshness、reconnect、stalled 與 alert；
- replay 保留當時版本與 append-only history；
- 所有 write actions 經既有 governance/RBAC/audit，無旁路；
- paper、risk reject、broker reject、partial fill、recon mismatch hosted E2E 通過；
- accessibility、security、performance、schema compatibility 與 disaster rebuild tests 通過；
- runbook、SLO dashboard、rollout/rollback 與 evidence packet 完成；
- Pantheon BFF 變更與 `execute-plans` 前端變更各自走完 branch/PR/checks/merge/deploy 流程。

## 19. Rollout and rollback

1. Shadow materialization：只建 read model，與現有 audit/evidence 對照。
2. Internal paper beta：只讀，顯示 completeness 與 source discrepancies。
3. Paper default：成為主要入口，但保留既有頁面。
4. Canary/live read-only：達 correlation/SLO gate 後開放。
5. Governed actions：逐 action feature flag 開放，不一次開啟全部寫入。
6. Production default：Cockpit 導向 Journey Needs Attention。

Rollback 應可停用新 UI、SSE 或 actions，而不停止 execution producer；materializer 可重建，且不得成為 execution plane 的同步依賴。

## 20. Risks and explicit non-goals

### Risks

- 以時間鄰近推測 correlation 會產生錯誤稽核鏈；只能用於標示 inferred 的 backfill。
- 前端先做視覺拼接會掩蓋 source inconsistency。
- Journey 成為單一巨型 payload 會造成效能與權限問題；snapshot、timeline、evidence 分頁載入。
- 把「成交」誤當「完成」會漏掉 booking/reconciliation 風險。
- Journey actions 若重做既有 policy，會形成治理旁路。

### Non-goals

- 不取代專業 Persona、Research、Risk、Execution、Audit、Evidence 頁面。
- 不改變策略如何產生或風控如何批准；本 gap 建立觀測、關聯與受治理操作面。
- 不允許由 UI 修改歷史事件。
- 不保證所有舊資料能可靠回填；不確定資料必須誠實標示。
- 不讓 read-model/materializer 成為 broker 下單的同步依賴。

## 21. Open decisions requiring owners

| Decision | 建議 owner | 關閉 Phase |
|---|---|---|
| `journey_id` 最初由 signal engine 或 decision engine 產生 | Architecture + Execution | Phase 0 |
| 一個 batch/order basket 是 parent journey 或同 journey children | Trading + Execution | Phase 0 |
| 各 stage stalled/SLO threshold | Operations + Risk | Phase 1 |
| rationale/raw payload 的遮罩與 retention | Governance + Security | Phase 1 |
| legacy backfill 可接受 confidence threshold | Audit + Data | Phase 2 |
| live contextual actions 的 rollout 順序 | Trading Ops + Risk | Phase 2 |

## 22. Outcome

完成後，操作人員不再需要知道 Pantheon 的服務邊界才能管理交易。他們可以從任意已知 ID 進入同一條可驗證生命週期，立即回答：

- 為什麼產生這筆交易？
- 使用哪個 Persona、策略、資料、模型與 policy 版本？
- 誰批准、哪個 gate 阻擋或放行？
- 使用哪筆資金與 runtime？
- broker 收到什麼、成交什麼？
- ledger 與 broker 是否一致？
- 現在需要誰採取什麼受治理動作？

這才是 Pantheon 端到端交易監看與管理的完成定義。

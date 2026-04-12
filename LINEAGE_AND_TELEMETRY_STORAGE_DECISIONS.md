# LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md

Last updated: 2026-04-09
Status: canonical lineage and telemetry storage policy
Tier: L1 Platform Architecture & Policy
Scope: normalized lineage edges, derived read model, telemetry canonical store, and analytical replica strategy
Conflict rule: this document overrides broader storage wording in overview/planning docs; service-local implementation details may refine indexing or partitioning without changing truth ownership

## 1. 文件目的

本文件收斂兩個核心設計決策：

1. Pantheon 的 **端到端 lineage** 如何跨服務組裝
2. Pantheon 的 **telemetry canonical store** 應如何選型與分層

> 核心決議：
>
> 1. **Lineage 採「寫正規化 edge、讀集中組裝」模式**
> 2. **Telemetry canonical store 預設採 Postgres partition；ClickHouse 作分析副本；TSDB 不作唯一 source-of-truth**

---

## 2. 端到端 lineage 範圍

Pantheon 需要能追：

```text
SourceRecord
-> StrategySpec
-> ExperimentRun
-> CandidateArtifact / AllocationPolicyArtifact
-> ApprovalDecision
-> DeploymentPlan
-> RuntimeBinding
-> TelemetryEvent / IncidentCase / Postmortem / EvolutionDecision
```

這條鏈跨越至少五個 domain：

- source ingest
- registry / research
- governance
- execution
- telemetry / evolution

---

## 3. Lineage 的正式模型

## 3.1 寫路徑（Write Path）原則

各服務只負責：

- 寫自己擁有的主資料
- 寫必要的 lineage edges（foreign keys / refs）

### 範例
- `ExperimentRun.strategy_id`
- `CandidateArtifact.run_id`
- `ApprovalDecision.target_id`
- `DeploymentPlan.artifact_id`
- `RuntimeBinding.artifact_id`
- `TelemetryEvent.binding_id`

### 核心決議
**各服務不負責組裝整條 lineage。**

---

## 3.2 讀路徑（Read Path）原則

由單一 **lineage read model** 組裝完整鏈。

### owner 建議
- `registry-core` 內的 lineage module
或
- 獨立 `lineage-read-svc`

### 功能
- 給 UI / BFF 查 lineage
- 給 incident / postmortem 取 evidence graph
- 給 audit / runtime trace 查 root chain

---

## 3.3 `lineage_json` 的正式角色

### 決議
- 正規化 foreign key / edge = source of truth
- `lineage_json` 若存在，只能是：
  - denormalized cache
  - search view
  - materialized UI payload

### 禁止
- 只寫 `lineage_json` 不寫 refs
- 讓 `lineage_json` 成為唯一真相來源

---

## 4. Lineage Query 模型

### 4.1 常見 query
- 這筆 live position 來自哪個 artifact？
- 這個 artifact 來自哪次 experiment？
- 那次 experiment 又源於哪個 StrategySpec / SourceRecord？
- 這次事故是否和某次 trainer patch / consult memo / approval 決策有關？

### 4.2 query owner
- UI / BFF 不跨服務自己 join
- lineage read model 統一組裝

---

## 5. Lineage Projection 與 Read Model 分層

BFF **不做深度跨表 lineage join**。
Pantheon 的 lineage 查詢分成兩種路徑：

### 5.1 標準 UI / BFF 查詢

用途：

- artifact detail
- strategy detail
- runtime detail
- incident detail 中的 lineage 摘要

這類查詢**一律走 lineage projection / materialized read model**，不現場 join 10+ 張表。

### 5.2 Forensic / Root-Cause 深查詢

用途：

- 事故深挖
- 完整研究→部署→事件鏈追查
- 稽核 / 法遵輸出

這類查詢才允許走 **full lineage reconstruction**，可同步或 async job。

---

### 5.3 lineage_projection 設計

`lineage_projection` 是 **read-optimized denormalized 視圖**，至少對下列 target 維護一份：

- `artifact_lineage_summary`
- `strategy_lineage_summary`
- `runtime_lineage_summary`
- `incident_lineage_summary`

每個 projection 至少包含：

```text
target_type
target_id
upstream_chain[]      # source → strategy → experiment → artifact 摘要
downstream_chain[]    # artifact → approval → deploy → binding → runtime 摘要
conflict_markers[]    # 若有治理衝突或 loader mismatch 標記於此
projection_updated_at
```

### 5.3.1 Read Model 欄位正規化

write path 保留各 domain 自己的 raw field names，但 read model 不得把模糊欄位名直接外露給
incident / evolution / BFF consumers。

lineage read model 必須把下列 raw 欄位正規化成**語意明確**的 projection 欄位：

| Raw field | Projection field | 說明 |
|---|---|---|
| `DeploymentPlan.binding_id` | `persona_capital_binding_id` | governance-local raw 名稱；projection 必須指出這是 persona-capital binding |
| `RuntimeBinding.binding_id` | `runtime_binding_id` | 避免和 governance binding 混淆 |
| `TelemetryEvent.binding_id` | `runtime_binding_id` | telemetry raw event 仍用 `binding_id`，但 read model 要輸出語意欄位 |
| `TelemetryEvent.plan_id` | `deployment_plan_id` | projection 使用 object-specific name |
| `TelemetryEvent.environment` | `deployment_stage` | `environment` 僅為 backward-compatible alias |

如果 raw field 與 projection alias 同時存在且值不一致，projection 必須產生
`conflict_markers[]`，不得靜默覆蓋。

### 5.3.2 Derived Record / Summary Contract

`lineage_projection` 與 `lineage_summary_json` 都必須標示自己是 derived-only，至少包含：

```text
target_type
target_id
derived_only = true
projection_updated_at

upstream_chain[]
downstream_chain[]
conflict_markers[]

refs:
  strategy_ids[]
  registry_ids[]
  runtime_binding_ids[]
  deployment_plan_ids[]
  capital_pool_ids[]
  persona_capital_binding_ids[]
  artifact_refs[]
  trace_ids[]
```

service-local refinement 可以新增 UI-friendly nesting，但不得把 projection payload 包裝成新的
source-of-truth object。

### 5.4 Source of Truth 不變

projection 是 denormalization，不是真相來源。
權威真相仍是：

- 各服務的 normalized edges / FK
- canonical Postgres tables 中的正式紀錄

projection 僅是 read acceleration layer。

### 5.5 refresh / repair / replay policy

- projection 由 lineage read model 負責 refresh
- 正常情況：<= 5s 落後
- degraded 情況：<= 30s 落後
- projection 被 repair job 定期驗證
- 若 projection 遺失或損壞，可由 normalized edges 重建

---

### 5.6 SLA

v1 查詢延遲 SLA：

| 查詢類型 | p95 目標 | 模式 |
|---|---|---|
| artifact detail lineage summary | < 300ms | 同步 |
| strategy detail lineage summary | < 500ms | 同步 |
| forensic full lineage query | < 5s | 允許 async job |

### 5.7 Freshness Contract

| 狀態 | freshness | 說明 |
|---|---|---|
| normal | <= 5s | 一般運作 |
| degraded | <= 30s | 高寫入負載或 repair 中 |

UI 必須顯示 `projection_updated_at`，讓使用者知道資料的新鮮度。

---

### 5.8 Materialized View / Denormalization 必要性

要。不是 optional。

v1 至少要有：

- `lineage_projection`
- `lineage_summary_json`（materialized JSON payload for BFF）

兩者都需要：

- refresh policy（event-driven + periodic fallback）
- repair policy（從 normalized edges 重建）
- replay policy（指定 aggregate / time window 重建）

---

## 6. Telemetry Store 的技術選型

## 6.1 問題背景

Pantheon 的 telemetry workload 有兩種：

### A. 強關聯 / 強對帳 / 跨物件 lineage 查詢
例如：
- artifact -> deployment -> runtime binding -> telemetry
- backtest vs live reconciliation
- incident evidence collection

### B. 大量時序聚合與 dashboard
例如：
- latency series
- fill ratio
- slippage over time
- heartbeat trend

如果用單一純 TSDB 做全部，A 類查詢會很痛苦。

---

## 6.2 正式決議

### Canonical store
**Postgres partitioned tables**

### Analytical mirror
**ClickHouse**

### 不採用的主策略
**TSDB 作為唯一 telemetry source-of-truth**

---

## 6.3 為什麼是 Postgres 作 canonical

因為 Pantheon 的 telemetry 不只是 charting，它還要：

- join RuntimeBinding / DeploymentPlan / ApprovalDecision
- 支援 incident evidence collection
- 做 backtest / paper / live reconciliation
- 與 registry / governance schema 交叉查詢

這些都更適合關聯式 schema。

---

## 6.4 ClickHouse 的角色

ClickHouse 適合承接：

- 大量 append-only events 的聚合分析
- dashboard
- latency / slippage / exposure 大範圍統計
- heatmap / long-range trend 查詢

### 同步方式
- 由 canonical Postgres 透過 CDC / sink / ETL 複寫
- ClickHouse 不是 write-authority

---

## 6.5 TSDB 的角色

TSDB 若使用，只適合：
- heartbeat / host metrics / infra metrics
- 獨立的 infra observability

**不應該**承接：
- business telemetry source of truth
- cross-domain lineage joins
- incident evidence source

---

## 7. Telemetry schema 分層

## 7.1 Raw Event Layer
不可變事件層。

欄位最少包含：
- `event_id`
- `event_type`
- `event_time`
- `ingest_time`
- `environment`
- `capital_pool_id`
- `runtime_id`
- `binding_id`
- `artifact_id`
- `persona_id`
- `strategy_id`
- `trace_id`
- `payload`

## 7.2 Normalized Event Layer
把各類事件正規化後供 domain service 查詢。

## 7.3 Metrics / Materialized Layer
給 dashboard / alert / analytics 用。

---

## 8. Canonical lineage edges 建議

下列 semantic edges 必須有正式欄位承載，不可只放 json：

| Semantic edge id | From | To | Physical field |
|---|---|---|---|
| `strategy_spec.source_record` | `StrategySpec` | `SourceRecord` | `StrategySpec.source_id` |
| `experiment_run.strategy_spec` | `ExperimentRun` | `StrategySpec` | `ExperimentRun.strategy_id` |
| `candidate_artifact.experiment_run` | `CandidateArtifact` | `ExperimentRun` | `CandidateArtifact.run_id` |
| `approval_decision.registry_target` | `ApprovalDecision` | governed artifact | `ApprovalDecision.target_id` |
| `deployment_plan.artifact` | `DeploymentPlan` | governed artifact | `DeploymentPlan.artifact_id` |
| `deployment_plan.capital_pool` | `DeploymentPlan` | `CapitalPool` | `DeploymentPlan.capital_pool_id` |
| `deployment_plan.persona_binding` | `DeploymentPlan` | `PersonaCapitalBinding` | `DeploymentPlan.binding_id` |
| `runtime_binding.artifact` | `RuntimeBinding` | governed artifact | `RuntimeBinding.artifact_id` |
| `runtime_binding.capital_pool` | `RuntimeBinding` | `CapitalPool` | `RuntimeBinding.capital_pool_id` |
| `runtime_binding.deployment_plan` | `RuntimeBinding` | `DeploymentPlan` | `RuntimeBinding.plan_id` |
| `runtime_binding.persona_binding` | `RuntimeBinding` | `PersonaCapitalBinding` | `RuntimeBinding.persona_capital_binding_id` |
| `runtime_binding.rollback_parent` | `RuntimeBinding` | `RuntimeBinding` | `RuntimeBinding.rollback_parent` |
| `telemetry_event.runtime_binding` | `TelemetryEvent` | `RuntimeBinding` | `TelemetryEvent.binding_id` |
| `telemetry_event.deployment_plan` | `TelemetryEvent` | `DeploymentPlan` | `TelemetryEvent.plan_id` |
| `telemetry_event.capital_pool` | `TelemetryEvent` | `CapitalPool` | `TelemetryEvent.capital_pool_id` |
| `telemetry_event.persona_binding` | `TelemetryEvent` | `PersonaCapitalBinding` | `TelemetryEvent.persona_capital_binding_id` |
| `incident_case.runtime_binding` | `IncidentCase` | `RuntimeBinding` | `IncidentCase.binding_id` |
| `postmortem.incident_case` | `Postmortem` | `IncidentCase` | `Postmortem.incident_id` |
| `evolution_decision.postmortem` | `EvolutionDecision` | `Postmortem` | `EvolutionDecision.linked_postmortem_id` |

補充規則：

- semantic edge id 是 read-model / audit / BFF 的穩定名稱
- raw field 是否較短或歷史命名較模糊，不影響 semantic edge id
- 若未來要 rename raw field，必須另開 migration task；不能由 projection 悄悄改寫 owner truth

---

## 9. Traceability 規則

所有事件 / 物件必須至少帶：
- `trace_id`
- `request_id`
- `environment`
- `capital_pool_id`（若適用）
- `runtime_id`（若適用）
- `artifact_id` 或 `binding_id`（若適用）

### 目的
要能回答：
- 這次問題來自哪個 deploy？
- 由誰批准？
- 對應哪個策略、哪個 artifact、哪個 pool、哪個 runtime？

---

## 10. Reconciliation 對資料庫的要求

Reconciliation / drift service 需要：

- 把 experiment baseline 與 live metrics 對齊
- 把 runtime binding 與 fill / position / broker snapshot 對齊
- 把 approval / deploy history 納入根因分析

因此要求：

- business telemetry 不能只存在 TSDB
- lineage edges 必須可 join
- canonical store 需支援 partition + relational joins

---

## 11. 建議的服務 ownership

| 能力 | owner |
|---|---|
| source/registry lineage write | source + registry services |
| approval/deploy lineage write | governance service |
| runtime binding lineage write | runtime manager |
| telemetry event write | telemetry ingest gateway |
| lineage assembly read | lineage read model / registry-core module |
| analytical mirror | telemetry analytics pipeline |

---

## 12. API 草案

### Lineage APIs
- `GET /api/lineage/strategy/{strategy_id}`
- `GET /api/lineage/artifact/{artifact_id}`
- `GET /api/lineage/runtime-binding/{binding_id}`
- `GET /api/lineage/trace/{trace_id}`

### Telemetry APIs
- `POST /api/telemetry/events`
- `GET /api/telemetry/events?binding_id=...`
- `GET /api/telemetry/metrics?capital_pool_id=...`

---

## 13. 遷移 / 落地建議

### Phase 1
- 先用 Postgres partition 做 canonical telemetry
- 先把 normalized lineage edges 補齊
- 先做 lineage read model
- **新增 lineage_projection 與 materialized summary**

### Phase 2
- 把高量 telemetry mirror 到 ClickHouse
- 做長時序 dashboard / analytics
- **驗證 projection SLA 與 freshness contract**

### Phase 3
- 若 infra metrics 量大，再引入 TSDB 專管 infra 層

---

## 14. 結論

Pantheon 若要真的做到端到端可追溯，就不能只喊 lineage，也不能把 telemetry store 隨便留白。

正式決議如下：

1. **Lineage 採寫正規化 edge、讀集中組裝**
2. **`lineage_json` 只作 cache / materialized view，不作真相來源**
3. **Postgres partition 是 telemetry canonical store**
4. **ClickHouse 是 analytics mirror**
5. **TSDB 不作業務 telemetry 唯一真相來源**
6. **BFF 不做深度跨表 join，預設讀 lineage projection**
7. **lineage projection 是 v1 必要，非 optional**
8. **SLA: artifact summary p95 < 300ms, strategy summary p95 < 500ms, forensic p95 < 5s**
9. **Freshness: normal <= 5s, degraded <= 30s, UI 顯示 `projection_updated_at`**

這樣才能同時支援：
- governance
- reconciliation
- incident
- postmortem
- evolution
- UI lineage tracing

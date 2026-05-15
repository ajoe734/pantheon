---
project: Pantheon
document_type: System Analysis Gap Report
batch: SA-06 to SA-10
language: zh-TW
assumption: >
  本批 SA 文件採用最新校正：目前實際在 VS Code 中被修改、用於 execution substrate 判讀的是 `ajoe734/Lean`；
  `ajoe734/lean-platform` 暫列為幾乎未動、歷史分支或待決 execution repo。
---

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.





# SA-07 — `pantheon` repo 差異分析

## 1. 本章目的

本章分析 `ajoe734/pantheon` 相對於 Pantheon 設計藍圖的落差。
`pantheon` 是目前最接近 **Governance + Registry Core / BFF / Data Plane / Telemetry Core** 的 repo，因此本章是整份 SA 報告的核心。

本章回答：

1. `pantheon` 已經完成哪些 canonical control-plane / registry-plane 元件？
2. 哪些仍是文件、schema、contract 或 local seed / mock？
3. `pantheon` 是否已能產生 `DeploymentPlan` 並交給目前實際 execution substrate：`Lean`？
4. `pantheon` 是否已接收 `Lean` 的 canonical `TelemetryEvent`？
5. `pantheon` 是否能形成 reconciliation / incident / evolution 閉環？
6. `pantheon` 與前端 / Lean / lean-platform 的責任邊界是否需要修正？

---

## 2. 藍圖與 Target Architecture 要求

### 2.1 Pantheon 母文件要求

Pantheon 母文件定義 `pantheon` repo 的定位是 **Governance + Registry Core**，承接：

- registry / lineage / artifact governance 核心
- review / promotion / deployment plan / rollback 主幹
- postmortem / freeze / retire / evolution 回寫的治理核心

母文件也要求 Knowledge & Registry Plane 是全系統真相來源，包含：

```text
source registry
strategy registry
alpha registry
experiment registry
artifact registry
insight bus
evidence store
approval registry
lineage registry
```

### 2.2 Target Architecture 要求

`TARGET_ARCHITECTURE.md` 提出以下硬性要求：

- Pantheon separates governance maturity from runtime deployment。
- `artifact_state` 與 `deployment_stage` 必須分離。
- Registry / governance owns `ArtifactRecord` and `ApprovalDecision`。
- Governance / promotion owns `DeploymentPlan`。
- Capital / runtime owns `PersonaCapitalBinding` and `RuntimeBinding`。
- Runtime manager writes `RuntimeBinding` and LEAN loads only the approved artifact projection。
- Telemetry and feedback write normalized operational evidence with deployment-stage and binding references。
- Evolution review decides whether to freeze, rollback, retrain, mutate, or retire。

這些要求構成本章差異分析的主要準繩。

---

## 3. `pantheon` 已觀察到的強證據

### 3.1 `TARGET_ARCHITECTURE.md`

此文件已建立 L1 platform architecture，包括：

- top-level rule
- canonical lifecycle model
- artifact state / deployment stage split
- responsibility split
- core canonical objects
- end-to-end governed flow
- preferred framework roles
- current repo interpretation

這代表 `pantheon` 不是單純文件散落，而是已建立相當清楚的 target architecture。

### 3.2 BFF API Contract

`services/control-plane/bff/BFF_API_CONTRACT.md` 已正式定義：

- API route families
- standard query envelope
- standard response envelope
- error response contract
- staleness / degradation model
- RBAC matrix
- composed views
- SSE contract
- read-only guarantee

這是很重要的 SA 證據，代表 BFF surface 已有正式 contract。

### 3.3 TelemetryEvent Schema

`services/telemetry/telemetry_event.schema.json` 已明確要求每筆 telemetry event 必須帶：

```text
event_id
event_type
created_at
execution_mode
binding_id
runtime_id
capital_pool_id
artifact_id
artifact_version
deployment_stage
plan_id
persona_capital_binding_id
target
metrics
```

這表示 Pantheon 對 telemetry evidence contract 的理解是正確的：runtime event 不能只是 log，而必須能連回 runtime binding、deployment plan、artifact、capital pool、persona-capital binding。

### 3.4 Data / Lineage / Incident / Consistency 文檔

Repo 搜尋顯示 `pantheon` 已包含：

- `Pantheon_總索引版系統分析文件.md`
- `Pantheon_資料表_Schema_設計版.md`
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `services/incident/contract.md`
- `services/incident/incident_case.schema.json`
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`

這些證據顯示 `pantheon` 已經在 L1/L2 設計層有不錯的結構，但仍需判斷是否已進入 executable implementation。

---

## 4. BFF Plane 差異分析

### 4.1 現況

BFF contract 已非常完整，並且定義 40 個 v1 endpoint，包括：

- Persona surfaces
- Capital Pool & Binding surfaces
- Deployment surfaces
- Runtime surfaces
- Telemetry surfaces
- Lineage surfaces
- Incident surfaces
- Evolution surfaces
- Composed views
- SSE streams

### 4.2 差異

最主要差異不是 contract 缺，而是 **contract 與 implementation / frontend 使用方式可能已分叉**。

BFF contract 聲稱：

```text
BFF is read-oriented
All BFF endpoints are GET only
BFF does not maintain canonical state
```

但前端 `bffClient.ts` 已經使用多個 `POST` command path。這表示：

1. `BFF_API_CONTRACT.md` 可能仍是 APP-001 read model contract。
2. 實際系統已進入 APP-002 command facade，但未完全反映在文件。
3. 若沒有明確 namespace，BFF 會從 read-oriented facade 演變成混合 command gateway。

### 4.3 建議修正

建立：

```text
services/control-plane/bff/READ_API_CONTRACT.md
services/control-plane/commands/COMMAND_API_CONTRACT.md
```

或至少在 OpenAPI 裡明確區分：

```text
/api/v1/read/...
/api/v1/commands/...
/api/v1/admin/...
```

### 4.4 風險

| 風險 | 說明 |
|---|---|
| Parallel truth risk | BFF 若承接 write path 但不寫 canonical store，會產生 ghost command |
| RBAC drift | read role 與 command role 混用 |
| Audit gap | command path 若沒有 idempotency_key / trace_id / actor_ref，事後不可追 |
| UI readiness illusion | 前端 command 可點，但後端未必有 authoritative executor |

---

## 5. Registry / Lineage 差異分析

### 5.1 藍圖要求

Knowledge & Registry Plane 應包含：

```text
Source Registry
Strategy Registry
Alpha Registry
Experiment Registry
Artifact Registry
Insight Bus / Research Notes
Evidence Store
Approval Registry
Model / Artifact Lineage
```

並且任何 live 問題都必須能追到：

```text
source → StrategySpec → experiment → artifact → approval → deployment → runtime → pool → human action / trainer / consult
```

### 5.2 現況判斷

`pantheon` 已有多份 registry / lineage / schema 相關文件，並已明確建立 canonical object backbone。這是正面訊號。

### 5.3 主要缺口

| 缺口 | 類型 | 說明 |
|---|---|---|
| StrategySpec → ExperimentRun → CandidateArtifact 的實際 orchestration 不明 | Behavioral | 有 schema / registry 設計不代表 research 工廠能產 artifact |
| Evidence Store 是否 authoritative 不明 | Structural | notes / evidence / search 是否只是 BFF surface 還是正式 store |
| Approval Registry 是否真正驅動 DeploymentPlan 不明 | Behavioral | approval 若只是頁面紀錄，不能算閉環 |
| Lineage 是否接到 Lean runtime event 不明 | Runtime integration | telemetry schema 要求 binding_id，但 Lean 是否提供未驗證 |
| Source → StrategySpec Seed Builder 缺口 | Data / Research | 藍圖要求 source normalizer 與 seed builder，目前需專章驗證 |

### 5.4 建議修正

建立 unified lineage spine：

```text
source_ref
strategy_spec_id
experiment_run_id
artifact_id
approval_decision_id
deployment_plan_id
runtime_binding_id
telemetry_event_id
incident_id
evolution_decision_id
```

任何 registry object 都要能至少支援：

```text
id
version
state
created_at
updated_at
lineage_refs[]
evidence_refs[]
actor_ref
trace_id
```

---

## 6. Artifact State / Deployment Stage 差異分析

### 6.1 藍圖要求

Target Architecture 明確規定：

```text
artifact_state:
  draft
  candidate
  approved
  retired

deployment_stage:
  none
  paper
  canary
  live
  frozen
```

規則：

- `artifact_state` 描述 artifact 是否 governable / promotable。
- `deployment_stage` 描述 approved artifact 實際在哪裡跑。
- `canary` 是 deployment stage，不是 artifact state。
- lineage read model 是 derived only，normalized edges 才是真 source of truth。

### 6.2 差異風險

若 `gate.py` 或其他 legacy promotion state 仍使用：

```text
candidate → paper → live → retired
```

就會把 artifact maturity 與 runtime deployment 混在一起。

### 6.3 影響

| 影響 | 說明 |
|---|---|
| 回放困難 | 無法分辨 artifact 被 approve，還是已經在 paper/live 跑 |
| rollback 困難 | rollback 是 deployment 層動作，不是 artifact state |
| canary 混淆 | canary 應是 deployment stage |
| telemetry join 困難 | telemetry event 應連 runtime_binding，而非只連 artifact |
| front UI 混淆 | UI 可能把 approved artifact 當 running strategy |

### 6.4 建議修正

1. 建立 migration map：

```text
legacy paper promotion_state → artifact_state=approved + deployment_stage=paper
legacy live promotion_state → artifact_state=approved + deployment_stage=live
legacy candidate → artifact_state=candidate + deployment_stage=none
```

2. 所有 BFF / front / registry contract 禁止再用 `paper` / `live` 作為 artifact state。

3. 新增 invariant tests：

```text
candidate artifact cannot have deployment_stage=live
deployment_stage cannot exist without approved artifact
canary is never artifact_state
```

---

## 7. Governance / Promotion / Deployment 差異分析

### 7.1 藍圖要求

Promotion / Deployment 回路應該是：

```text
candidate artifact
→ validators
→ review gates
→ ApprovalDecision
→ DeploymentPlan
→ RuntimeBinding
→ LEAN runtime
```

### 7.2 現況判斷

`pantheon` 已有：

- deployment / promotion / registry 相關文件
- BFF deployment surfaces
- approval decision surfaces
- telemetry schema
- target architecture 要求 runtime binding

但 SA 關鍵問題是：

```text
ApprovalDecision 是否真的自動 / 半自動產 DeploymentPlan？
DeploymentPlan 是否真的 materialize 成 Lean 可消費 manifest？
RuntimeBinding 是否真的由 runtime manager 寫入？
Lean runtime 是否真的帶 RuntimeBinding 回吐 telemetry？
```

目前僅從已檢索證據看，仍無法確認這條閉環全部成立。

### 7.3 差異總表

| Requirement | 現況 | Gap |
|---|---|---|
| CandidateArtifact registration | 有設計證據 | 實作閉環需驗證 |
| Validators | 有 promotion / policy 文件 | 是否 runnable 未完全確認 |
| Review Gates | 有 UI / BFF / policy | 是否 authoritative 未完全確認 |
| ApprovalDecision | contract 層存在 | 是否驅動 deployment 未完全確認 |
| DeploymentPlan | contract / target 架構存在 | 是否可 materialize 到 Lean 未確認 |
| RuntimeBinding | target architecture / telemetry schema 要求 | 實作與 Lean 接口未確認 |
| RollbackController | 藍圖要求 | 是否有 runtime-level action dispatch 未確認 |

### 7.4 建議修正

新增 `governance-promotion` e2e test：

```text
given candidate artifact
and approval decision approved
and capital pool binding valid
when create deployment plan
then runtime binding request is produced
and artifact materialization manifest is created
and Lean launch payload includes runtime_binding_id
```

---

## 8. Capital Pool / Risk / Broker Account 差異分析

### 8.1 藍圖要求

Capital Pool Plane 應包含：

```text
Capital Pool Registry
Risk Policy Registry
Broker Account Registry
Persona-Capital Binding Registry
Pool State Manager
```

資金池是正式治理對象，而不是 execution config 裡的一個欄位。

### 8.2 `pantheon` 應有責任

`pantheon` 應負責：

- capital pool canonical registry
- risk policy
- broker account reference
- persona-capital binding
- pool state
- pool admissibility check
- live owner uniqueness
- deployment gate

### 8.3 與 Lean 的邊界

Lean 不應自行決定：

```text
which persona can trade this pool
which artifact may trade this broker account
which broker secret to use without Pantheon authorization
whether live is allowed
```

Lean 應該只接收已由 Pantheon 驗證過的 launch manifest / runtime binding context。

### 8.4 主要缺口

| 缺口 | 風險 |
|---|---|
| Broker credential 若仍由 Lean config 直接讀取 | persona / pool boundary 可能繞過 Pantheon |
| Capital pool 與 broker account mapping 不明 | live isolation 無法驗證 |
| RiskPolicy 是否能 veto deployment 不明 | 風控可能變成建議而非硬限制 |
| RuntimeBinding 是否帶 `capital_pool_id` 不明 | telemetry / incident 無法歸因 |
| paper / canary / live credentials 是否隔離不明 | 環境混用風險 |

### 8.5 建議修正

在 `pantheon` 新增或強化：

```text
CapitalPoolStore
RiskPolicyStore
BrokerAccountRegistry
PersonaCapitalBindingStore
PoolAdmissibilityService
RuntimeLaunchAuthorizationService
```

並且要求 Lean launch manifest 至少包含：

```text
capital_pool_id
broker_account_ref
credential_ref_alias
deployment_stage
runtime_binding_id
risk_policy_id
```

---

## 9. Telemetry / Reconciliation / Evolution 差異分析

### 9.1 TelemetryEvent schema 已相當成熟

`telemetry_event.schema.json` 已要求非常完整的 evidence 欄位，這是 `pantheon` 最正確的地方之一。

### 9.2 主要差異

成熟 schema 不等於 runtime 已接上。仍需檢查：

```text
Lean 是否產生這個 schema？
pantheon ingest_svc 是否驗證這個 schema？
telemetry 是否進 store？
是否轉成 runtime summary / metrics / lineage edges？
是否觸發 reconciliation？
是否觸發 incident？
是否形成 evolution decision？
```

### 9.3 Reconciliation 缺口

藍圖要求：

```text
live 表現必須和 backtest / paper / canary 做 reconciliation
```

而實務上至少需要：

```text
Backtest-Paper-Live Reconciliation
Position / Order / Fill Reconciliation
Feature / Label / Policy Drift Detector
Execution Drift Detector
Runtime Baseline Comparator
Drift Report Store
```

目前需驗證這些是否是 runnable service，而不只是文件 / schema / UI。

### 9.4 Evolution 缺口

Evolution 不是一個 dashboard，而是能根據 telemetry / incident / postmortem 決定：

```text
freeze
rollback
retrain
revalidate
retire
mutate persona
update risk policy
```

若 EvolutionDecision 只是紀錄，不會 dispatch action，那閉環仍未完成。

---

## 10. OpenClaw / Search / Source Ingestion 差異分析

### 10.1 藍圖要求

OpenClaw / LLM / agent 主要放在研究、控制、治理，不直接當 execution kernel。Source Ingestion Plane 要能將：

```text
paper
repo
internal research
news
social
alpha database
filings
macro
market data
```

透過 normalizer 進 Source Registry / Evidence Store / StrategySpec Seed Builder。

### 10.2 目前差異

`pantheon` 已有 research ingest / adapters 的證據，但外部資料完整 plane 仍需補齊：

- Data Gateway
- Source Registry
- EvidenceBundle
- Search Gateway
- ACL-aware retrieval
- License / entitlement policy
- OpenClaw governed search tool
- StrategySpec Seed Builder

### 10.3 重要邊界

OpenClaw 不應直接接外部 API / broker / Lean runtime。正確模式：

```text
OpenClaw
→ governed tool
→ pantheon Search Gateway / Source Gateway
→ EvidenceBundle / StrategySpec Seed
→ registry / review / research
```

---

## 11. `pantheon` 缺口總表

| Gap ID | 缺口 | 類型 | 嚴重度 | 建議修補 |
|---|---|---|---|---|
| PAN-GAP-001 | BFF read-only contract 與 command usage 分叉 | Contract | High | 拆 READ_API / COMMAND_API |
| PAN-GAP-002 | artifact_state / deployment_stage 仍可能混用 | State Machine | High | migration + invariant tests |
| PAN-GAP-003 | DeploymentPlan → Lean launch manifest 未驗證 | Runtime Integration | Critical | 實作 materializer / launch contract |
| PAN-GAP-004 | RuntimeBinding canonical store / runtime manager 不明 | Runtime Integration | Critical | 新增 RuntimeBindingStore |
| PAN-GAP-005 | Lean telemetry 是否符合 schema 未驗證 | Telemetry | Critical | 實作 Lean exporter + schema validator |
| PAN-GAP-006 | Reconciliation / drift service 未形成閉環 | Behavioral | High | 新增 reconciliation writer |
| PAN-GAP-007 | Incident → Evolution → Action dispatch 未閉合 | Evolution | High | 新增 evolution action executor |
| PAN-GAP-008 | Data Gateway / Search Gateway 未完整 | Data / Search | High | 新增 source/search bounded contexts |
| PAN-GAP-009 | CapitalPool / BrokerAccount / RiskPolicy 與 Lean 邊界不明 | Governance | High | 新增 launch authorization service |
| PAN-GAP-010 | front / BFF / Lean contract CI 缺 | Verification | High | 建立 cross-repo contract tests |

---

## 12. 建議 Codex Task Packets

### PAN-TP-001 — Split BFF Read and Command Contracts

```text
Repo: pantheon
Goal: 拆分 BFF read API contract 與 command API contract。
Acceptance:
  - read endpoints GET-only
  - command endpoints require actor_ref, idempotency_key, trace_id
  - frontend bffClient 可以分別對應
```

### PAN-TP-002 — Artifact State / Deployment Stage Migration

```text
Repo: pantheon
Goal: 消除 artifact_state 與 deployment_stage 混用。
Acceptance:
  - all schemas use artifact_state in draft/candidate/approved/retired only
  - deployment_stage only none/paper/canary/live/frozen
  - legacy paper/live state migrated
```

### PAN-TP-003 — DeploymentPlan to Lean Launch Manifest

```text
Repo: pantheon
Goal: 將 approved DeploymentPlan materialize 成 Lean launch manifest。
Acceptance:
  - manifest includes runtime_binding_id / artifact_id / capital_pool_id / plan_id
  - manifest validates approval / risk / binding
  - unsupported runtime target fails closed
```

### PAN-TP-004 — Telemetry Projection Writer

```text
Repo: pantheon
Goal: TelemetryEvent ingest 後寫入 runtime summary / metrics / lineage projection。
Acceptance:
  - TelemetryEvent schema validation required
  - runtime summary visible from BFF
  - missing binding_id rejected
```

### PAN-TP-005 — Minimum Operating Loop E2E Test

```text
Repo: pantheon
Goal: 新增 paper-only e2e test。
Flow:
  CandidateArtifact → ApprovalDecision → DeploymentPlan → RuntimeBinding → Lean launch manifest → TelemetryEvent → ReconciliationRecord
Acceptance:
  - all IDs preserved
  - no mock data used
```

---

## 13. 本章結論

`pantheon` 的狀態是：

```text
Architecture / policy / schema maturity: 高
BFF contract maturity: 中高
Registry / lineage maturity: 中
Promotion / deployment maturity: 中
Capital pool / runtime binding maturity: 中低到中
Telemetry schema maturity: 高
Telemetry → reconciliation → evolution behavior: 中低
Lean runtime integration: 未充分驗證 / 可能缺口最大
```

SA 判斷：

> `pantheon` 已經具備成為 Pantheon control-plane / registry-plane 的核心條件，但還不是完整 operating system truth source。最大的工程缺口在於：將 ApprovalDecision / DeploymentPlan / RuntimeBinding / TelemetryEvent 這條鏈與目前實際 execution substrate `Lean` 形成可執行、可測試、可回放的閉環。

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





# SA-10 — Plane-by-Plane 差異分析

## 1. 本章目的

本章依照 Pantheon 藍圖中的 14 個 plane，逐一盤點目前實作與藍圖的差異。

本章採用最新校正：

```text
front-ai-trading-system = Console / UI / BFF client
pantheon = Governance / Registry / BFF / Data / Telemetry core
Lean = 目前實際 execution substrate / modified runtime path
lean-platform = 原藍圖 substrate，但現況暫列未採用 / 幾乎未動 / 待決
```

本章不把「有頁面 / 有 README / 有 schema」視為 complete。每個 plane 都用以下維度分析：

```text
Blueprint requirement
Current implementation signal
Gap
Risk
Required SA / SD / implementation work
Priority
```

---

## 2. 總體評分方法

| 狀態 | 定義 |
|---|---|
| Implemented | 有可執行 service / state machine / test，且接到上游與下游 |
| Partial | 有文件 / schema / UI / local service，但閉環未完全驗證 |
| Stub / Mock | 主要靠 mock、preview fallback、seed data 或 local placeholder |
| Documented-only | 只有 README / design doc |
| Absent | 未見明確證據 |
| Misplaced | 實作存在，但放在錯 repo / 錯 plane |
| Conflicting | 文件與實作互相矛盾 |

---

## 3. Plane 1 — Pantheon Console Plane

### 3.1 Blueprint Requirement

Console Plane 應提供：

```text
Operator Console
Persona Workbench
Research Workbench
Knowledge Workbench
Trainer Workbench
Consultation Workbench
Governance Workbench
Evolution Workbench
```

### 3.2 Current Implementation Signal

`front-ai-trading-system` 已明確定位為 Pantheon UI repo，負責 pages、components、UX states、BFF client wiring。`bffClient.ts` 涵蓋 research、knowledge、trainer、persona、capital pool、deployment、governance、operator、lineage、consultation、evolution 等 surface。

### 3.3 Gap

| Gap | 說明 |
|---|---|
| UI surface 不等於 authoritative data | 多數頁面能渲染不代表 pantheon / Lean 閉環存在 |
| preview fallback 仍存在 | Lovable preview 可能回 mock |
| runtime identity 缺口 | UI 需要明確顯示 execution substrate 是 Lean |
| source_mode 不明 | 每個 surface 應標示 authoritative / derived / stale / preview mock |

### 3.4 Risk

```text
Codex / reviewer 可能因 UI 完整誤判系統已完成。
```

### 3.5 Required Work

```text
新增 source_mode badges
新增 RuntimeBinding detail
新增 DeploymentPlan → Lean runtime trace timeline
拆 read client / command client
導入 OpenAPI-generated TS types
```

### 3.6 Status

```text
Partial / High UI coverage but not full operating truth.
```

---

## 4. Plane 2 — Pantheon BFF Plane

### 4.1 Blueprint Requirement

BFF Plane 應為前台唯一聚合入口，負責：

```text
UI Aggregation API
Auth / Session / RBAC
Read Model / Command Facade
Realtime / Notification Layer
```

BFF 不應成為 canonical truth source。

### 4.2 Current Implementation Signal

`BFF_API_CONTRACT.md` 已定義 BFF routes、RBAC、error contract、staleness、SSE，且明確寫 BFF read-oriented / GET-only / no parallel truth。前端 `bffClient.ts` 則已使用 GET 與 POST。

### 4.3 Gap

| Gap | 說明 |
|---|---|
| read-only contract vs command reality | 文件說 GET-only，但前端已有 POST command path |
| command facade 未正式分層 | 需要 command API contract |
| BFF implementation 與 contract drift 風險 | 前端可呼叫，不代表後端 canonical write |
| secondary control path 未與 Lean runtime 完全驗證 | kill switch / rollback 需要非 BFF path |

### 4.4 Risk

```text
BFF 可能從 read facade 演變成混合 command gateway，造成 RBAC、audit、canonical ownership 不清。
```

### 4.5 Required Work

```text
READ_API_CONTRACT.md
COMMAND_API_CONTRACT.md
idempotency_key / trace_id / actor_ref
RBAC per command
secondary control path contract
```

### 4.6 Status

```text
Partial / Contract mature but boundary drift.
```

---

## 5. Plane 3 — Shared Capability Plane

### 5.1 Blueprint Requirement

Shared Capability Plane 應包含：

```text
Plugin Tools
Shared Skills Pack
Workflow Templates
Hooks / Cron / Background Jobs
Agent Router / Session Binder
```

OpenClaw 是控制 / 研究 / governance 能力，不是 execution kernel。

### 5.2 Current Implementation Signal

Pantheon 文件已有 OpenClaw governance 概念，並要求 deny-first、approval token、workspace isolation、no direct LEAN。這方向正確。

### 5.3 Gap

| Gap | 說明 |
|---|---|
| OpenClaw tool registry 與 Pantheon policy 是否實作不明 | 文件存在不等於 runtime enforcement |
| Search Gateway 缺 | OpenClaw 需要 governed search，而不是自由網路 |
| Workflow templates 與 Lobster / cron 實作不明 | 主回路 orchestration 未驗證 |
| Agent router / session binder 是否接 persona registry 不明 | persona effective capability 可能只是 UI / config |

### 5.4 Risk

```text
LLM / OpenClaw 若直接接外部 API 或 runtime command，會違反藍圖中 agent 不直接當 execution kernel 的公理。
```

### 5.5 Required Work

```text
OpenClaw tool entitlement service
Search Gateway
Workflow template registry
Session binder → Persona Registry
Tool call audit log
```

### 5.6 Status

```text
Documented / Partial.
```

---

## 6. Plane 4 — Source Ingestion Plane

### 6.1 Blueprint Requirement

Source Ingestion Plane 應包含：

```text
Paper Ingest
Repo Ingest
Internal Research Ingest
Source Normalizer
Source Registry
StrategySpec Seed Builder
```

使用者也明確補充外部資料源包括：

```text
news
social media
external alpha database
LLM search
```

### 6.2 Current Implementation Signal

`pantheon` 有 research ingest / adapters 相關文件與部分實作訊號。`Lean` / `lean-platform` 類 repo 含有標準 LEAN market / data provider 能力，但那不等同於 Pantheon canonical Data Gateway。

### 6.3 Gap

| Gap | 說明 |
|---|---|
| Data Gateway 未完整 | market/news/social/filings/macro/alpha DB 沒有統一 gateway |
| SourceRecord / EvidenceBundle 是否 authoritative 不明 | 研究素材可能未接到 registry |
| Source Normalizer 缺 | 不同 source 可能格式分散 |
| StrategySpec Seed Builder 缺 | 資料不能自動蒸餾成 strategy hypothesis |
| Execution data 與 research data 容易混淆 | Lean feed 不等於 canonical research dataset |

### 6.4 Risk

```text
外部資料源直接散落到 research / Lean / UI，會造成無法回放、無法控權、無法稽核。
```

### 6.5 Required Work

```text
services/data-gateway/
services/source-registry/
services/evidence-store/
StrategySpecSeedBuilder
PIT fields: event_time / available_time / ingest_time
license / entitlement policy
```

### 6.6 Status

```text
Partial / Major implementation gap.
```

---

## 7. Plane 5 — Persona Plane

### 7.1 Blueprint Requirement

Persona Plane 應包含：

```text
Persona Registry
Private Workspace
Route Policy Manager
Consult Policy Manager
Capability Resolver
Teaching Session Coordinator
Persona Lifecycle Manager
```

persona 是正式一級物件，不是 prompt。

### 7.2 Current Implementation Signal

前端有 persona catalog / details / sessions / teaching / capabilities / capital pool binding API surface。Pantheon 文件也明確定義 persona 一級物件。

### 7.3 Gap

| Gap | 說明 |
|---|---|
| authoritative Persona Registry 是否完整不明 | UI / BFF surface 不等於 store |
| Capability Resolver 是否 runtime-enforced 不明 | 可能只是顯示 effective capabilities |
| Route Policy / Consult Policy 是否參與 command gate 不明 | persona 可能仍未真正限制 tools |
| Persona lifecycle 是否與 capital binding / deployment gate 相連不明 | paper_owner / live_owner 必須進 governance |
| Private workspace / memory boundary 未驗證 | 搜尋 / evidence / OpenClaw 必須遵守 workspace ACL |

### 7.4 Risk

```text
persona 若只是 UI entity 而不是 policy-bound object，就無法支撐多人格 governance。
```

### 7.5 Required Work

```text
PersonaRegistry authoritative store
CapabilityResolver service
RoutePolicyEvaluator
ConsultPolicyEvaluator
TeachingSession authoritative events
PersonaLifecycle invariant tests
```

### 7.6 Status

```text
Partial.
```

---

## 8. Plane 6 — Capital Pool Plane

### 8.1 Blueprint Requirement

Capital Pool Plane 應包含：

```text
Capital Pool Registry
Risk Policy Registry
Broker Account Registry
Persona-Capital Binding Registry
Pool State Manager
```

它是 live 隔離核心。

### 8.2 Current Implementation Signal

前端已存在 capital pool / binding API surface。Pantheon target architecture 定義 `PersonaCapitalBinding` 和 `RuntimeBinding` 為核心 canonical objects。

### 8.3 Gap

| Gap | 說明 |
|---|---|
| CapitalPool canonical store 需驗證 | 是否是 seed / mock / real store 不明 |
| RiskPolicy veto 未驗證 | risk 是否能阻止 DeploymentPlan / Lean launch |
| BrokerAccount Registry 未驗證 | broker secret 是否由 Pantheon 管 |
| PersonaCapitalBinding → Lean runtime 未驗證 | Lean 是否知道 binding id |
| PoolState Manager 未驗證 | risk_off / paused / liquidating 是否能驅動 Lean |

### 8.4 Risk

```text
若 Lean 直接讀 broker config 而非 Pantheon BrokerAccount / CapitalPool，live isolation 失效。
```

### 8.5 Required Work

```text
CapitalPoolStore
RiskPolicyStore
BrokerAccountRegistry
PersonaCapitalBindingStore
PoolAdmissibilityChecker
Lean Launch Authorization Service
```

### 8.6 Status

```text
Partial / High-risk boundary.
```

---

## 9. Plane 7 — Knowledge & Registry Plane

### 9.1 Blueprint Requirement

Knowledge & Registry Plane 是全系統真相來源，包含：

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

### 9.2 Current Implementation Signal

`pantheon` repo 含總索引、schema 設計、lineage/telemetry storage decisions、registry / promotion 相關文件。

### 9.3 Gap

| Gap | 說明 |
|---|---|
| registry backbone 分散 | 是否有統一 store / API 不明 |
| Evidence Store 與 Search Gateway 未完整 | OpenClaw / research / review 無共同 evidence source |
| ExperimentRun → ArtifactRecord linkage 未完整驗證 | 研究到治理可能斷 |
| Approval Registry → DeploymentPlan 未完整驗證 | approval 可能未驅動 runtime |
| Lineage → Lean Telemetry 未完整驗證 | runtime event 未必帶 binding / artifact / pool |

### 9.4 Risk

```text
無統一 registry backbone，系統會出現多個真相來源。
```

### 9.5 Required Work

```text
RegistryCore
LineageSpine
EvidenceStore
ArtifactRegistry
ApprovalRegistry
ExperimentRegistry
OpenAPI / event contracts
```

### 9.6 Status

```text
Partial / Design mature, executable closure pending.
```

---

## 10. Plane 8 — Consultation Plane

### 10.1 Blueprint Requirement

Consultation Plane 包含：

```text
Consult Request Manager
Agent-to-Agent Bus
Committee Orchestrator
Red-Team Orchestrator
Consult Memo Store
Consult Audit Log
```

### 10.2 Current Implementation Signal

前端 `bffClient.ts` 有 consult request / transcript / committee / redteam memo API surface。

### 10.3 Gap

| Gap | 說明 |
|---|---|
| consultation backend bounded context 未驗證 | 前端 surface 可能先行 |
| consult memo 是否能進 ReviewGate 不明 | memo 需要影響 approval |
| red-team output 是否有 evidence_refs 不明 | review 需可回放 |
| agent-to-agent bus 是否存在不明 | multi-persona consult 可能尚未實作 |

### 10.4 Risk

```text
沒有 consultation backend，會診只是 UI / memo，無法成為治理 gate。
```

### 10.5 Required Work

```text
services/consultation/
ConsultRequestStore
CommitteeOrchestrator
RedTeamMemoStore
ConsultAuditLog
ReviewGate integration
```

### 10.6 Status

```text
Stub / Partial.
```

---

## 11. Plane 9 — Research & Learning Plane

### 11.1 Blueprint Requirement

Research & Learning Plane 包含：

```text
Qlib Research Factory
vectorbt Rapid Prototype
statsmodels Econometrics / Regime
QuantLib Pricing / Rates / Vol
RL Lab
Experiment Orchestrator
Rapid Eval Service
```

### 11.2 Current Implementation Signal

`pantheon` 有 research ingest / adapters / data-plane / schema 相關訊號，前端有 research tickets / experiments / artifacts surface。

### 11.3 Gap

| Gap | 說明 |
|---|---|
| Experiment Orchestrator 未完全驗證 | UI 有 experiments 不代表 orchestration runnable |
| backend adapters 是否可跑不明 | Qlib/vectorbt/FinRL 等是否只是文件 |
| DatasetVersion binding 是否 enforced 不明 | 研究不可 replay 會破壞藍圖 |
| ExperimentRun → CandidateArtifact packaging 未驗證 | 研究到治理交接可能斷 |
| Rapid Eval 是否接 Trainer 不明 | teaching preview 可能是 mock |

### 11.4 Risk

```text
研究工廠若未閉合，StrategySpec 無法穩定變成 governed CandidateArtifact。
```

### 11.5 Required Work

```text
ExperimentOrchestrator
BackendRegistry
DatasetVersionBinder
MetricsNormalizer
CandidateArtifactPackager
RapidEvalService
```

### 11.6 Status

```text
Partial.
```

---

## 12. Plane 10 — Policy Learning Plane

### 12.1 Blueprint Requirement

Policy Learning Plane 需要分開管理：

```text
Persona Policy Learning
Alpha Policy Learning
Human Trader Imitation
Preference / Correction Dataset Builder
```

### 12.2 Current Implementation Signal

前端有 trainer / teaching / mutation / evolution surface。Pantheon 藍圖明確要求可學習物件分開。

### 12.3 Gap

| Gap | 說明 |
|---|---|
| teaching trace dataset 未驗證 | coaching 是否變 dataset 不明 |
| preference / correction dataset 未驗證 | review / approval choices 是否可學習不明 |
| human trader imitation path 未驗證 | trader trajectory 是否結構化不明 |
| persona mutation gate 未驗證 | learning output 不可直接改 live behavior |
| alpha policy learning 未驗證 | alpha 何時使用 / 失效條件是否學習不明 |

### 12.4 Risk

```text
若 learning 與 governance 未隔離，可能形成 unsafe online adaptation。
```

### 12.5 Required Work

```text
TeachingTraceDatasetBuilder
PreferenceDatasetBuilder
ImitationDatasetBuilder
PersonaMutationPlanner
MutationReviewGate
```

### 12.6 Status

```text
Documented / Early partial.
```

---

## 13. Plane 11 — Portfolio / Risk Optimizer Layer

### 13.1 Blueprint Requirement

Optimizer Layer 應支援：

```text
skfolio
PyPortfolioOpt
cvxportfolio
Riskfolio-Lib
Allocation Policy Artifact Builder
```

輸出應是 allocation policy artifact，而不是直接下單。

### 13.2 Current Implementation Signal

藍圖有定義，但在目前已檢視證據中未確認完整實作。

### 13.3 Gap

| Gap | 說明 |
|---|---|
| optimizer backend registry 未驗證 | backend 是否只是規劃 |
| AllocationPolicyArtifact 未驗證 | optimizer output 是否進 registry 不明 |
| risk constraints / capital pool constraints 接合不明 | optimizer 需遵守 pool policy |
| Lean execution compatibility 未驗證 | target weights 如何進 Lean runtime 不明 |

### 13.4 Risk

```text
optimizer 若直接影響 execution，會繞過 artifact / promotion / risk gates。
```

### 13.5 Required Work

```text
OptimizerBackendRegistry
AllocationPolicyArtifact schema
OptimizerRunStore
PoolConstraintAdapter
LeanTargetAdapter
```

### 13.6 Status

```text
Documented / Unverified.
```

---

## 14. Plane 12 — Governance & Promotion Plane

### 14.1 Blueprint Requirement

Governance & Promotion Plane 包含：

```text
Patch Validators
Review Gates
Approval Decision Store
Promotion Controller
Deployment Planner
Rollback Controller
Execution Loader Checks
```

### 14.2 Current Implementation Signal

`pantheon` 有 target architecture、registry/promotion design、BFF deployment surfaces。前端有 governance / approval / rollback UI。Telemetry schema 與 BFF contract 都能支撐此 plane。

### 14.3 Gap

| Gap | 說明 |
|---|---|
| artifact_state / deployment_stage 可能混用 | 需 migration |
| ApprovalDecision → DeploymentPlan 未驗證 | approval 可能未驅動 plan |
| DeploymentPlan → Lean launch 未驗證 | 最大 runtime connector gap |
| rollback target / rollback controller 未驗證 | live 安全性不足 |
| Execution Loader Checks 未驗證 | Lean 是否拒絕未 approved artifact 不明 |

### 14.4 Risk

```text
沒有真正 DeploymentPlan → RuntimeBinding → Lean 的 handoff，promotion plane 只是治理文件，不是 deployable core。
```

### 14.5 Required Work

```text
PromotionController
DeploymentPlanner
RuntimeBindingWriter
LeanLaunchMaterializer
RollbackController
ExecutionLoaderCheckService
```

### 14.6 Status

```text
Partial / Critical gap at Lean handoff.
```

---

## 15. Plane 13 — Execution Plane

### 15.1 Blueprint Requirement

Execution Plane 包含：

```text
Runtime Manager
Artifact Loader
Runtime Binding Store
LEAN Paper Runtime
LEAN Canary Runtime
LEAN Live Runtime
Broker / Exchange / Subaccounts
Pause / Liquidate / Replace Actions
```

### 15.2 Current Implementation Signal

2026-05-03 校正後，正式 execution bridge 是 `pantheon/lean` submodule / `ajoe734/pantheon-lean.git`。
後續不再把 `lean-platform` 當 active gap；此 plane 的剩餘差異是 `pantheon-lean` runtime contract、launcher maturity、TelemetryEvent、broker entitlement 與 kill-switch bridge 是否完整。

### 15.3 Gap

| Gap | 說明 |
|---|---|
| official execution substrate | 已 canonicalize 為 `pantheon/lean` / `pantheon-lean` |
| RuntimeBinding consumer 未完整證明 | `pantheon-lean` bridge 需證明完整 Pantheon binding |
| DeploymentPlan consumer 未完整證明 | launcher 是否讀 Pantheon manifest / RuntimeBootstrap contract 需補強 |
| Artifact Loader 未見 | approved artifact projection guard 不明 |
| TelemetryEvent exporter 未完整證明 | runtime events 是否轉 canonical schema 需補強 |
| Broker entitlement boundary 未見 | broker secret / capital pool 是否由 Pantheon 授權不明 |
| paper/canary/live segregation 未完整證明 | `pantheon-lean` 是否 stage-aware 需補強 |
| kill-switch bridge 未完整證明 | Pantheon command 是否能 pause / liquidate / replace runtime 需補強 |

### 15.4 Risk

```text
Execution Plane 是目前最大的 structural / runtime integration gap。
```

### 15.5 Required Work

```text
PantheonLaunchManifest
PantheonRuntimeContext
RuntimeBinding injection
TelemetryEvent exporter
BrokerEntitlementGuard
Stage-aware launch profiles
KillSwitchBridge
```

### 15.6 Status

```text
`pantheon-lean` bridge canonicalized; Pantheon execution integration partially implemented but still needs launcher / binding / telemetry / kill-switch hardening.
```

---

## 16. Plane 14 — Telemetry / Postmortem / Evolution Plane

### 16.1 Blueprint Requirement

此 plane 包含：

```text
Event Ingest Gateway
Canonical Event Normalizer
Telemetry Store
Metrics / Time-Series Store
Audit / Action Log
Heartbeat / Runtime Health
Reconciliation / Drift
Incident / Postmortem
Evolution Controller
Kill Switch / Safe Mode
```

### 16.2 Current Implementation Signal

`pantheon` 有 `TelemetryEvent` schema，要求 runtime event 帶完整 evidence fields。BFF contract 有 telemetry / incident / evolution surfaces。

### 16.3 Gap

| Gap | 說明 |
|---|---|
| Lean → Pantheon telemetry exporter 未驗證 | runtime event source 不明 |
| telemetry ingest → projection writer 未驗證 | BFF runtime summary 是否真實不明 |
| reconciliation service 未驗證 | backtest/paper/canary/live 對帳不明 |
| incident trigger 未驗證 | drift → incident 是否自動不明 |
| postmortem builder 未驗證 | evidence collection 是否自動不明 |
| evolution action dispatcher 未驗證 | decision 是否能 freeze / rollback / retrain 不明 |
| kill switch path 未驗證 | BFF / secondary path / Lean runtime 是否串起不明 |

### 16.4 Risk

```text
若 telemetry 無法連回 runtime_binding_id，整個 postmortem / evolution loop 無法成立。
```

### 16.5 Required Work

```text
LeanTelemetryExporter
TelemetryIngestValidator
ProjectionWriter
ReconciliationService
DriftDetector
IncidentService
PostmortemBuilder
EvolutionActionDispatcher
KillSwitchBridge
```

### 16.6 Status

```text
Schema mature; runtime-to-evolution behavior partial / unverified.
```

---

## 17. Plane Gap Matrix 總表

| Plane | Status | Major Gap | Severity | Primary Owner |
|---|---|---|---|---|
| Console | Partial / high UI coverage | truth source / runtime identity visibility | Medium | front |
| BFF | Partial | read vs command boundary drift | High | pantheon |
| Shared Capability | Documented / partial | governed search / tool entitlement | High | pantheon + OpenClaw |
| Source Ingestion | Partial | Data Gateway / Source Registry / Seed Builder | High | pantheon |
| Persona | Partial | capability resolver / lifecycle enforcement | Medium-High | pantheon |
| Capital Pool | Partial | broker / risk / pool binding to `pantheon-lean` | High | pantheon + pantheon-lean |
| Knowledge & Registry | Partial | unified registry backbone / evidence store | High | pantheon |
| Consultation | Stub / partial | backend bounded context / review gate integration | Medium | pantheon + front |
| Research & Learning | Partial | experiment orchestrator / artifact packager | High | pantheon |
| Policy Learning | Early | dataset builders / mutation gate | Medium | pantheon |
| Optimizer | Documented | allocation artifact / Lean target adapter | Medium | pantheon |
| Governance & Promotion | Partial | DeploymentPlan → `pantheon-lean` handoff | Critical | pantheon + pantheon-lean |
| Execution | Canonical bridge, partial maturity | RuntimeBinding / telemetry exporter / launcher hardening | Critical | pantheon-lean |
| Telemetry / Evolution | Schema mature / behavior partial | pantheon-lean telemetry → reconciliation → evolution action | Critical | pantheon + pantheon-lean |

---

## 18. Minimum Operating Loop Gap

### 18.1 Required loop

```text
StrategySpec
→ ExperimentRun
→ CandidateArtifact
→ ApprovalDecision
→ DeploymentPlan
→ RuntimeBinding
→ pantheon-lean Paper Runtime
→ TelemetryEvent
→ ReconciliationRecord
→ IncidentCase
→ EvolutionDecision
```

### 18.2 Current bottlenecks

```text
1. ExperimentRun → CandidateArtifact packaging not fully verified
2. ApprovalDecision → DeploymentPlan not fully verified
3. DeploymentPlan → pantheon-lean launch manifest incomplete / unverified
4. RuntimeBinding injection into pantheon-lean incomplete / unverified
5. pantheon-lean → TelemetryEvent exporter incomplete / unverified
6. Telemetry → Reconciliation writer missing / unverified
7. Incident → Evolution action dispatch missing / unverified
```

### 18.3 SA recommendation

先做 paper-only loop：

```text
single persona
single capital pool
single approved artifact
single pantheon-lean paper runtime
single telemetry stream
basic reconciliation only
manual approval
no live
no canary
no automatic mutation
```

---

## 19. Immediate P0 Work Across Planes

| Priority | Work | Plane | Owner |
|---|---|---|---|
| P0 | pantheon-lean launcher / runtime maturity | Execution | pantheon + pantheon-lean |
| P0 | DeploymentPlan → pantheon-lean launch manifest | Governance / Execution | pantheon + pantheon-lean |
| P0 | RuntimeBinding store + injection | Capital / Execution | pantheon + pantheon-lean |
| P0 | pantheon-lean TelemetryEvent exporter | Execution / Telemetry | pantheon-lean |
| P0 | Telemetry projection writer | Telemetry | pantheon |
| P0 | artifact_state / deployment_stage migration | Registry / Governance | pantheon |
| P0 | BFF read / command contract split | BFF | pantheon + front |
| P0 | front source_mode / runtime identity UI | Console | front |

---

## 20. 本章結論

整體 plane-by-plane 判斷：

```text
Console / BFF / architecture documentation: relatively mature
Registry / governance / telemetry schema: medium to high maturity
Research / source / persona / capital pool: partial
Execution integration with actual Lean repo: biggest uncertainty
Telemetry → reconciliation → incident → evolution closure: not yet proven
```

SA 結論：

> Pantheon 目前不是「空藍圖」，也不是「差不多完成」。它是 control-plane / UI / schema 已快速成形，但 operating system 後半圈仍未被跨 repo 實作驗證的狀態。最大阻塞點已從「是否有頁面 / schema」轉為「DeploymentPlan 是否能啟動 Lean，Lean 是否能帶 RuntimeBinding 回吐 TelemetryEvent，Pantheon 是否能據此 reconciliation / incident / evolution」。

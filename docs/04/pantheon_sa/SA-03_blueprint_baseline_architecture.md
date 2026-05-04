# SA-03 — 藍圖基準：Pantheon Target Baseline Architecture

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.


**文件編號**：SA-03
**文件類型**：System Analysis / Blueprint Baseline
**範圍**：固定本次 gap analysis 的 target architecture 依據
**版本**：v0.1 Draft

---

## 1. 本章目的

本章先定義「什麼叫對齊藍圖」。如果沒有 target baseline，後續分析很容易變成各自解讀：

```text
工程師看 repo 有沒有檔案；
Codex 看目錄名稱是否相似；
前端看頁面是否存在；
後端看 schema 是否存在；
operator 看 runtime 是否能跑；
SA 則必須看整個 operating system 閉環是否成立。
```

因此，本章把 Pantheon 藍圖濃縮成後續差異分析的基準線。

---

## 2. Pantheon 系統定義

Pantheon 的 target definition 是：

```text
以前台工作台驅動，
以控制平面協調多人格，
以研究與知識平面生產策略與 artifact，
以治理平面控制 promotion / rollback，
以每資金池獨立 runtime 實現 paper / canary / live，
並由 telemetry / postmortem / evolution 回灌形成閉環的多人格量化 operating system。
```

這個定義有三個重要排除：

```text
Pantheon 不是單一會下單的模型。
Pantheon 不是單純回測平台。
Pantheon 不是只有聊天介面的 agent 應用。
```

所以，若 repo 只做出 chat UI、backtest script、generic LEAN runtime 或 agent prompt，都不能視為 Pantheon 完成。

---

## 3. 系統憲法：共享與隔離

Pantheon 的一句話架構是：

```text
研究共享、知識共享、會診共享，但資金池與 live 執行隔離。
```

這是後續所有 gap analysis 的最高準繩。

### 3.1 共享的是什麼

```text
source / evidence
research notes
StrategySpec
experiment results
alpha templates
consult memo
persona learning traces
search / RAG evidence
shared workflows / tools
```

### 3.2 不共享的是什麼

```text
broker credentials
capital pool runtime state
live book
runtime binding
position / fill authority
kill-switch authority
live deployment permission
```

### 3.3 對 repo 的含義

```text
front-ai-trading-system 不應是 source of truth。
pantheon 應是 registry / governance / telemetry truth core。
Lean / execution substrate 只 consume approved deployment artifacts，並輸出 telemetry。
OpenClaw / LLM 不應直接當 execution kernel。
```

---

## 4. 十大系統公理

後續 gap 項目若違反以下公理，應視為高嚴重度。

### 4.1 研究與執行分離

```text
Research produces artifact.
Execution consumes approved artifact.
```

### 4.2 Persona 是正式一級物件

Persona 不是 prompt，而是：

```text
workspace
route policy
consult policy
capability snapshot
capital binding
lifecycle
teaching history
```

### 4.3 風控有否決權

Risk policy 是 veto layer，不是 dashboard 裝飾。

### 4.4 所有資料、artifact、部署都可回放、可版本化

必要欄位包括：

```text
source_ref
dataset_version_id
code_version
artifact_hash
approval_id
deployment_plan_id
runtime_binding_id
```

### 4.5 所有策略與人格都必須有 lineage

必須能回答：

```text
這個策略從哪裡來？
哪次 experiment 產生它？
哪個 artifact 被批准？
誰批准？
在哪個 capital pool 執行？
現在 runtime 是哪個？
出事時回退到哪裡？
```

### 4.6 所有上線都經過 paper / canary / rollback 路徑

不能：

```text
candidate → live
```

必須：

```text
candidate → approved → paper → canary → live
```

且 live 必須有 rollback target。

### 4.7 LLM / agent 不直接當 execution kernel

OpenClaw / LLM 可：

```text
研究
查 evidence
產生 proposal
發起 review request
輔助治理
```

不可：

```text
直接持有 broker secret
直接下單
直接改 live runtime 行為
直接繞過 approval
```

### 4.8 可學習物件要分開

```text
persona policy
alpha policy
human trader imitation
```

不得混成一個黑箱模型。

### 4.9 回饋回模型、persona、知識庫

Telemetry 不只是 monitor，而是 evolution input。

### 4.10 live 表現必須與 backtest / paper / canary reconciliation

如果沒有 reconciliation，就不能說是 operating system。

---

## 5. 14 個 target planes

### 5.1 Console Plane

使用者與系統的正式工作台群：

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

### 5.2 BFF Plane

前端唯一聚合入口：

```text
UI Aggregation API
Session / Auth / RBAC
Read Model / Command Facade
Realtime / Notifications
```

重要公理：BFF 不應成為 parallel truth source。

### 5.3 Shared Capability Plane

自然由 OpenClaw 承接，但必須受 Pantheon governance 約束：

```text
plugin tools
shared skills pack
workflow templates
hooks / cron / background jobs
agent router / session binder
```

### 5.4 Source Ingestion Plane

受控研究素材入口：

```text
paper ingest
repo ingest
internal research ingest
news ingest
social ingest
external alpha DB ingest
source normalizer
source registry
StrategySpec seed builder
```

### 5.5 Persona Plane

persona governance：

```text
persona registry
private workspace
route policy manager
consult policy manager
capability resolver
teaching session coordinator
persona lifecycle manager
```

### 5.6 Capital Pool Plane

live 隔離核心：

```text
capital pool registry
risk policy registry
broker account registry
persona-capital binding registry
pool state manager
```

### 5.7 Knowledge & Registry Plane

全系統真相來源：

```text
source registry
strategy registry
alpha registry
experiment registry
artifact registry
insight bus / research notes
evidence store
approval registry
model / artifact lineage
```

### 5.8 Consultation Plane

多人格補盲與審議：

```text
consult request manager
agent-to-agent bus
committee orchestrator
red-team orchestrator
consult memo store
consult audit log
```

### 5.9 Research & Learning Plane

正式研究工廠：

```text
Qlib research factory
vectorbt rapid prototype
statsmodels econometrics / regime
QuantLib pricing / rates / vol
RL lab
experiment orchestrator
rapid eval service
```

### 5.10 Policy Learning Plane

```text
persona policy learning
alpha policy learning
human trader imitation
preference / correction dataset builder
```

### 5.11 Portfolio / Risk Optimizer Layer

```text
skfolio
PyPortfolioOpt
cvxportfolio
Riskfolio-Lib
allocation policy artifact
```

### 5.12 Governance & Promotion Plane

部署前主閘門：

```text
patch validators
review gates
approval decision store
promotion controller
deployment planner
rollback controller
execution loader checks
```

### 5.13 Execution Plane

只 consume approved artifact：

```text
runtime manager
artifact loader
runtime binding store
LEAN paper runtime
LEAN canary runtime
LEAN live runtime
broker / exchange / subaccounts
pause / liquidate / replace actions
```

在本報告最新前提下，這一層現況主要映射到 `Lean`，不是 `lean-platform`。

### 5.14 Telemetry / Postmortem / Evolution Plane

讓系統可監控、可歸因、可演化：

```text
event ingest gateway
canonical event normalizer
telemetry store
metrics / time-series store
audit / action log
heartbeat / runtime health
reconciliation / drift
incident / postmortem
evolution controller
kill switch / safe mode
```

---

## 6. Target operating loops

Pantheon 不是一條 pipeline，而是多條回路。

### 6.1 研究素材回路

```text
cron / researcher action
→ paper / repo / internal / news / social ingest
→ normalize
→ Source Registry
```

### 6.2 策略蒸餾回路

```text
discovered material
→ StrategySpecSeed
→ StrategySpec / AlphaTemplate
```

### 6.3 Alpha 複製 / 研究回路

```text
StrategySpec
→ backend selection
→ experiment / prototype / RL lab
→ replicated artifact
```

### 6.4 Persona 教學回路

```text
researcher
→ Trainer Workbench
→ teaching events
→ rapid eval
→ persona patch / dataset
```

### 6.5 Human Trader 模仿回路

```text
teaching traces / trader trajectories
→ imitation dataset
→ behavior policy candidate
```

### 6.6 Consultation / Committee 回路

```text
persona / researcher
→ consult request
→ committee / red-team
→ memo
→ registry / review
```

### 6.7 Promotion / Deployment 回路

```text
candidate artifact
→ validators
→ review gates
→ approved
→ paper / canary / live
```

### 6.8 Capital Pool Execution 回路

```text
approved artifact
→ runtime binding
→ LEAN runtime
→ broker / subaccounts
→ fills / positions
```

### 6.9 Telemetry / Postmortem / Evolution 回路

```text
events
→ reconciliation / drift
→ incident
→ postmortem
→ evolution decision
→ retrain / freeze / rollback / mutate
```

---

## 7. Canonical state machines

### 7.1 Strategy / Alpha Lifecycle

```text
discovered
→ scaffolded
→ replicated
→ approved
→ paper
→ canary
→ live
→ frozen / retired
```

### 7.2 Artifact State vs Deployment Stage

Target architecture 更精確地要求分離：

```text
artifact_state:
  draft → candidate → approved → retired

deployment_stage:
  none → paper → canary → live → frozen
```

這個分離是後續 gap analysis 的關鍵。

### 7.3 Persona Lifecycle

```text
draft
→ research_only
→ consultable
→ paper_owner
→ live_owner
→ frozen / retired
```

### 7.4 Capital Pool Lifecycle

```text
provisioned
→ paper_bound
→ canary_bound
→ live_bound
→ risk_off
→ paused
→ liquidating
→ archived
```

### 7.5 Runtime Lifecycle

```text
created
→ loading
→ active
→ degraded
→ paused
→ replacing
→ terminated
```

### 7.6 Incident / Postmortem / Evolution Lifecycle

```text
alert_open
→ alert_ack
→ incident_triaged
→ incident_active
→ mitigated
→ postmortem_pending
→ postmortem_published
→ evolution_proposed
→ evolution_reviewed
→ evolution_approved
→ evolution_executed
```

---

## 8. Core canonical objects

後續 gap analysis 應以這些物件是否存在、是否有 owner、是否有 contract、是否有 producer/consumer、是否有 tests 來判斷。

### 8.1 第一包物件

```text
Persona
RoutePolicy
ConsultPolicy
CapabilitySnapshot
TeachingSession
TeachingEvent
ConsultRequest
ConsultMemo
```

### 8.2 第二包物件

```text
SourceRecord
StrategySpecSeed
StrategySpec
AlphaTemplate
ExperimentTask
ExperimentRun
CandidateArtifact
AllocationPolicyArtifact
InsightCard
EvidenceBundle
PreferenceExample
TeachingDatasetRef
```

### 8.3 第三包物件

```text
CapitalPool
RiskPolicy
BrokerAccount
PersonaCapitalBinding
ApprovalDecision
DeploymentPlan
RuntimeBinding
RuntimeStatus
LoaderReport
```

### 8.4 第四包物件

```text
TelemetryEvent
RuntimeHeartbeat
ReconciliationRecord
DriftReport
AlertEvent
IncidentCase
Postmortem
EvolutionDecision
AuditAction
KillSwitchAction
```

---

## 9. Target repo ownership

### 9.1 原藍圖 repo ownership

```text
front-ai-trading-system → Console
pantheon → Governance + Registry Core
lean-platform → Execution Substrate
```

### 9.2 本 SA 報告的現況校正

```text
front-ai-trading-system → Console
pantheon → Governance + Registry + BFF + Telemetry Core
Lean → actual modified Execution Substrate
lean-platform → pending / historical / not currently active
```

### 9.3 Gap analysis 的判定準則

若某責任在原藍圖與現況 repo 不一致，標為：

```text
Repo Ownership Gap
```

若該偏移未經 ADR 正式化，嚴重度至少為 High。

---

## 10. Baseline acceptance criteria

一個最低限度可接受的 Pantheon operating loop 應至少能跑通：

```text
StrategySpec
→ ExperimentRun
→ CandidateArtifact
→ ApprovalDecision
→ DeploymentPlan
→ RuntimeBinding
→ Lean Paper Runtime
→ TelemetryEvent
→ ReconciliationRecord
→ IncidentCase / DriftReport
→ EvolutionDecision
```

若任一段只有文件或 UI 而沒有真實 producer/consumer，則不能視為閉環完成。

---

## 11. 本章結論

後續所有 SA 差異分析都應以本章基準判斷：

```text
不是問「repo 裡有沒有看起來像的檔案」，
而是問「這個 plane 的 authority、contract、state machine、event producer/consumer、runtime effect 是否成立」。
```

尤其 Execution Plane 的基準要更新為：

```text
若 Lean 是實際修改 repo，則 Lean 必須正式承擔原本 lean-platform 應承擔的 RuntimeBinding / DeploymentPlan / TelemetryEvent 責任。
```

---

## 附錄：本章主要依據來源

- `pantheon/Pantheon_總索引版系統分析文件.md`
- `pantheon/TARGET_ARCHITECTURE.md`
- `pantheon/services/telemetry/telemetry_event.schema.json`

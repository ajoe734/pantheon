---
project: Pantheon
document_type: System Analysis Gap Report
batch: SA-11 to SA-15
language: zh-TW
assumption: >
  本批 SA 文件採用最新校正：目前實際在 VS Code 中被修改、用於 execution substrate 判讀的是 `ajoe734/Lean`；
  `ajoe734/lean-platform` 暫列為幾乎未動、歷史分支或待決 execution repo。
evidence_baseline: >
  Pantheon 總索引版系統分析文件、TARGET_ARCHITECTURE、BFF_API_CONTRACT、TelemetryEvent schema、
  front-ai-trading-system README / bffClient、Lean README / Launcher，以及本對話已產出的 SA-01～SA-10。
---

# SA-11 — Operating Loop 差異分析

## 1. 本章目的

本章專門分析 Pantheon 的 **operating loops** 是否已經從藍圖上的回路變成可執行、可回放、可治理的系統行為。

前面 SA-06～SA-10 已經分別從 repo 與 plane 檢查差異。本章改用「回路」作為檢查單位。這很重要，因為 Pantheon 藍圖的核心不是「有多少模組」，而是多個 plane 之間能不能形成閉環：

```text
source / evidence
→ research / experiment
→ artifact
→ governance / approval
→ deployment / runtime
→ telemetry
→ reconciliation / incident
→ postmortem / evolution
→ 回寫 source / knowledge / persona / research / governance
```

本章採用最新校正：

```text
front-ai-trading-system = Console / UI / BFF client
pantheon = Registry / Governance / BFF / Data / Telemetry core
Lean = 實際被修改、目前應視為 execution substrate
lean-platform = 原藍圖指定但現況暫列未採用 / 幾乎未動 / 待決
```

---

## 2. 判斷方法

### 2.1 Operating loop 的完成標準

一條 loop 不是「文件畫出來」就算完成。至少要具備：

| 層級 | 完成標準 |
|---|---|
| Object | 回路中的每個主要物件都有 canonical schema / identifier |
| Command | 每個 state transition 有明確 command 或 workflow |
| Event | 每個 state transition 有 domain event / telemetry event |
| Store | 每個關鍵事實有 authoritative store |
| Policy | 每個分支決策由 policy 或 gate 決定，不由 UI 任意跳轉 |
| Integration | 上游輸出能被下游消費 |
| Audit | 高風險動作可追 actor / reason / trace / before-after |
| Replay | 同一 `dataset_version + code_version + artifact_version` 可重放 |
| Test | 有 e2e / integration tests 驗證回路不只是 mock |

### 2.2 狀態標記

```text
Implemented          = 有完整可執行閉環與測試
Partial              = 有多數物件 / contract / UI，但關鍵行為未閉合
Stub / Mock          = 主要依賴 mock / seed / preview fallback
Documented-only      = 只有文件 / Mermaid / README
Absent               = 未見明確證據
Misplaced            = 實作在錯 repo / 錯 plane
Unverified           = 可能存在，但目前證據不足
```

---

## 3. Loop 1 — 研究素材回路

### 3.1 Blueprint Loop

```text
cron / researcher action
→ paper / repo / internal / news / social / alpha DB ingest
→ normalize
→ Source Registry
→ Evidence Store
```

### 3.2 期望物件

```text
SourceRecord
EvidenceBundle
SourceIngestJob
SourceNormalizerResult
IngestAuditEvent
SearchIndexDocument
```

### 3.3 期望 commands / events

```text
Commands:
  StartSourceIngest
  NormalizeSource
  RegisterSourceRecord
  CreateEvidenceBundle
  IndexEvidence

Events:
  SourceIngestRequested
  SourceIngestCompleted
  SourceNormalized
  SourceRegistered
  EvidenceBundleCreated
  EvidenceIndexed
```

### 3.4 現況判斷

`pantheon` 已有 research ingest / adapters / data-plane 類訊號，前端也有 knowledge / research surface。但依目前證據，完整 Source Ingestion Plane 還沒有被證明已覆蓋：

```text
news
social media
external alpha database
filings / fundamentals
macro
market data
broker / execution telemetry as evidence
```

此外，`Lean` 或 `lean-platform` 內若有 data provider / news downloader，那仍屬於 execution / toolbox layer，不等於 Pantheon canonical Source Ingestion Plane。

### 3.5 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| Source Registry 是否 authoritative 不明 | Structural | 前端可顯示 evidence，不代表有統一 source truth |
| Source Normalizer 缺口 | Behavioral | 不同 source 可能未正規化成同一 schema |
| EvidenceBundle 缺口 | Contract | review / search / postmortem 需要共用 evidence |
| StrategySpec Seed Builder 未驗證 | Behavioral | source 未必能轉成 strategy hypothesis |
| News / social / alpha DB 未完整 | Data | 使用者特別指出的資料類型仍需補 |
| OpenClaw 搜尋未接 governed Search Gateway | Governance | LLM 搜尋不能直接繞過 ACL / license |

### 3.6 風險

```text
如果研究素材不進 canonical Source Registry / Evidence Store，
後面 strategy、artifact、approval、postmortem 都無法追溯 evidence。
```

### 3.7 修補工作

```text
P0:
  - SourceRecord schema
  - EvidenceBundle schema
  - SourceRegistryStore
  - SourceNormalizer interface
  - EvidenceStore
  - ACL / license metadata

P1:
  - news connector
  - filings / fundamentals connector
  - macro / calendar connector
  - social connector
  - external alpha DB connector
  - OpenClaw governed Search Gateway
```

### 3.8 狀態

```text
Partial / Major Data-Governance Gap
```

---

## 4. Loop 2 — 策略蒸餾回路

### 4.1 Blueprint Loop

```text
discovered material
→ StrategySpec seed
→ StrategySpec / AlphaTemplate
```

### 4.2 期望物件

```text
StrategySpecSeed
StrategySpec
AlphaTemplate
HypothesisRecord
RequiredDataSpec
FeatureSpec
LabelSpec
CostAssumption
RiskConstraint
```

### 4.3 期望 commands / events

```text
Commands:
  BuildStrategySpecSeed
  PromoteSeedToStrategySpec
  RegisterAlphaTemplate
  LinkEvidenceToStrategy

Events:
  StrategySpecSeedCreated
  StrategySpecCreated
  AlphaTemplateRegistered
  StrategyEvidenceLinked
```

### 4.4 現況判斷

前端有 StrategySpec list / detail / compare surface。Pantheon 藍圖也定義 StrategySpec / AlphaTemplate。但從 SA 角度，關鍵不是是否能顯示 StrategySpec，而是：

```text
SourceRecord / EvidenceBundle 是否能產生 StrategySpecSeed？
StrategySpec 是否能進 Experiment Orchestrator？
StrategySpec 是否能保留 required_data / backend_hint / risk_constraints？
```

目前這條回路看起來更像「schema / UI / 文檔成熟」，但蒸餾 pipeline 尚未被證明可執行。

### 4.5 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| Source → StrategySpecSeed 未驗證 | Behavioral | 研究素材可能無法自動蒸餾 |
| StrategySpecSeed → StrategySpec 未驗證 | State Machine | 缺 seed review / commit 流程 |
| AlphaTemplate registry 未驗證 | Registry | alpha 使用時機與適用 regime 可能未結構化 |
| Evidence linkage 未驗證 | Lineage | StrategySpec 需可追 evidence refs |
| Backend hint / required data 未驗證 | Contract | Research Orchestrator 需要此欄位 |

### 4.6 修補工作

```text
P0:
  - StrategySpecSeed schema
  - StrategySpec canonical schema
  - BuildStrategySpecSeed service
  - StrategySpec review / commit command
  - evidence_refs mandatory

P1:
  - AlphaTemplate registry
  - regime / asset_class / holding_period classifiers
  - LLM-assisted seed builder with human review
```

### 4.7 狀態

```text
Partial / Research-to-Registry Gap
```

---

## 5. Loop 3 — Alpha 複製 / 研究回路

### 5.1 Blueprint Loop

```text
StrategySpec
→ backend selection
→ experiment / prototype / RL lab
→ replicated artifact
```

### 5.2 期望物件

```text
ExperimentTask
ExperimentRun
DatasetVersion
BackendRunConfig
ResearchBackendResult
MetricsSummary
CandidateArtifact
ReplicationGateResult
```

### 5.3 期望 commands / events

```text
Commands:
  CreateExperimentTask
  SelectResearchBackend
  StartExperimentRun
  CompleteExperimentRun
  RegisterCandidateArtifact

Events:
  ExperimentTaskCreated
  ExperimentRunStarted
  ExperimentRunCompleted
  ReplicationGatePassed
  CandidateArtifactRegistered
```

### 5.4 現況判斷

前端 `researchBffApi` 覆蓋 tickets / analyses / experiments / artifacts。`pantheon` 有 data-plane / dataset lineage / research ingest / adapters 的訊號。  
但完整 Research Factory 仍需檢查：

```text
Qlib / vectorbt / statsmodels / QuantLib / RL workers 是否可實際由 orchestrator 呼叫？
ExperimentRun 是否 pin dataset_version？
ExperimentRun 是否產生 CandidateArtifact？
CandidateArtifact 是否進 ArtifactRegistry？
```

### 5.5 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| Experiment Orchestrator 未完全驗證 | Structural | 有 experiments surface 不代表有 orchestrator |
| BackendRegistry 未驗證 | Contract | backend selection 需要明確 interface |
| DatasetVersion binding 未驗證 | Replay | 若不 pin dataset version，回測不可回放 |
| Metrics normalizer 未驗證 | Contract | 不同 backend 結果要統一 |
| CandidateArtifact packager 未驗證 | Behavioral | experiment run 需能生成 artifact |
| ReplicationGate 是否可執行不明 | Governance | candidate 不能只靠人手標記 |

### 5.6 修補工作

```text
P0:
  - ExperimentOrchestrator
  - ResearchBackend interface
  - DatasetVersionBinder
  - MetricsNormalizer
  - CandidateArtifactPackager
  - ReplicationGate

P1:
  - Qlib adapter
  - vectorbt adapter
  - statsmodels adapter
  - FinRL / RL lab adapter
```

### 5.7 狀態

```text
Partial / Backend-Orchestration Gap
```

---

## 6. Loop 4 — Persona 教學回路

### 6.1 Blueprint Loop

```text
researcher
→ Trainer Workbench
→ teaching events
→ rapid eval
→ persona patch / dataset
```

### 6.2 期望物件

```text
TeachingSession
TeachingEvent
ControlPatch
PreviewRequest
RapidEvalResult
PersonaPatch
TeachingDatasetRef
CapabilitySnapshot
```

### 6.3 現況判斷

前端有 trainer sessions / preview / replay surface，也有 persona teaching history / capabilities surface。藍圖要求 persona 是 workspace、policy、capability、binding、lifecycle 的組合體。

### 6.4 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| TeachingSession authoritative store 未驗證 | Registry | teaching history 不應只是 UI log |
| RapidEvalService 未驗證 | Behavioral | preview 是否真跑 evaluation 不明 |
| PersonaPatch gate 未驗證 | Governance | teaching 不能直接改 live persona |
| TeachingDataset builder 未驗證 | Policy Learning | coaching trace 要能變 dataset |
| CapabilitySnapshot runtime enforcement 未驗證 | Governance | snapshot 不只是顯示欄位 |

### 6.5 修補工作

```text
P1:
  - TeachingSessionStore
  - TeachingEvent append-only log
  - RapidEvalService
  - PersonaPatchReviewGate
  - TeachingDatasetBuilder
  - CapabilityResolver integration
```

### 6.6 狀態

```text
Partial / Policy-Learning Gap
```

---

## 7. Loop 5 — Human Trader 模仿回路

### 7.1 Blueprint Loop

```text
teaching traces / trader trajectories
→ imitation dataset
→ behavior policy candidate
```

### 7.2 期望物件

```text
TraderTrajectory
HumanDecisionTrace
PreferenceExample
ImitationDataset
BehaviorPolicyCandidate
PolicyEvalRun
```

### 7.3 現況判斷

目前已知藍圖有 Human Trader Imitation，但 repo 實作證據不足。這是較後期能力，短期可不列 P0，但要避免混入 persona policy / alpha policy。

### 7.4 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| Trader trajectory schema 未驗證 | Contract | 需要紀錄人類判斷 / 不下單理由 |
| ImitationDataset 未驗證 | Policy Learning | 不能和一般 experiment dataset 混用 |
| BehaviorPolicyCandidate gate 未驗證 | Governance | behavior policy 不可直接進 live |
| Evaluation protocol 未驗證 | Safety | 模仿策略需離線評估 |

### 7.5 修補工作

```text
P2:
  - HumanDecisionTrace schema
  - ImitationDatasetBuilder
  - BehaviorPolicyCandidate registry
  - OfflineEvalGate
```

### 7.6 狀態

```text
Documented / Early
```

---

## 8. Loop 6 — Consultation / Committee 回路

### 8.1 Blueprint Loop

```text
persona / researcher
→ consult request
→ committee / red-team
→ memo
→ registry / review
```

### 8.2 期望物件

```text
ConsultRequest
ConsultSession
ConsultTranscript
CommitteeDecision
RedTeamMemo
ConsultMemo
ConsultAuditLog
```

### 8.3 現況判斷

前端 BFF client 已有 consultation request / transcript / committee / redteam memo surface。但後端 bounded context 是否完整仍未驗證。

### 8.4 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| Consult backend store 未驗證 | Structural | UI 有 surface 不代表有 authoritative service |
| CommitteeOrchestrator 未驗證 | Behavioral | committee 成員 / quorum / decision 需要 formal policy |
| RedTeamMemo 是否 mandatory 不明 | Governance | 某些 strategy 應要求 red-team |
| ConsultMemo → ReviewGate 未驗證 | Behavioral | memo 必須影響 approval |
| AuditLog 未驗證 | Audit | 會診意見需可回放 |

### 8.5 修補工作

```text
P1:
  - services/consultation/
  - ConsultRequestStore
  - CommitteePolicyEvaluator
  - RedTeamMemoStore
  - ConsultAuditLog
  - ReviewGate integration
```

### 8.6 狀態

```text
Partial / UI ahead of backend
```

---

## 9. Loop 7 — Promotion / Deployment 回路

### 9.1 Blueprint Loop

```text
candidate artifact
→ validators
→ review gates
→ approved
→ paper / canary / live
```

### 9.2 期望物件

```text
CandidateArtifact
PatchValidationResult
ReviewGateResult
ApprovalDecision
DeploymentPlan
RuntimeBinding
LoaderReport
RollbackRecord
```

### 9.3 現況判斷

`pantheon` Target Architecture 對這條回路非常清楚，前端 governance / deployment surfaces 也完整。但目前最大問題是：

```text
DeploymentPlan 是否真的能啟動 Lean？
Lean 是否真的消費 RuntimeBinding / artifact manifest？
```

### 9.4 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| artifact_state / deployment_stage 混用風險 | State Machine | Target Architecture 已要求分離 |
| ApprovalDecision → DeploymentPlan 未驗證 | Behavioral | approval 需驅動 plan |
| DeploymentPlan → Lean launch manifest 未驗證 | Runtime Integration | 目前最大阻塞 |
| RuntimeBinding store / writer 未驗證 | Contract | telemetry 需要 binding |
| LoaderReport 未驗證 | Governance | Lean loader 應拒絕未 approved artifact |
| rollback path 未驗證 | Safety | live 必須可 rollback |

### 9.5 修補工作

```text
P0:
  - ApprovalDecision → DeploymentPlan integration
  - DeploymentPlanMaterializer
  - RuntimeBindingStore
  - LeanLaunchManifest
  - ArtifactLoaderGuard
  - RollbackController
```

### 9.6 狀態

```text
Partial / Critical Handoff Gap
```

---

## 10. Loop 8 — Capital Pool Execution 回路

### 10.1 Blueprint Loop

```text
approved artifact
→ runtime binding
→ LEAN runtime
→ broker / subaccounts
→ fills / positions
```

### 10.2 期望物件

```text
CapitalPool
RiskPolicy
BrokerAccount
PersonaCapitalBinding
RuntimeBinding
RuntimeStatus
OrderEvent
FillEvent
PositionSnapshot
```

### 10.3 現況判斷

Lean 具備標準 brokerage / engine 能力，但 Pantheon capital pool abstraction 是否注入 Lean 不明。

### 10.4 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| CapitalPool → Lean runtime mapping 未驗證 | Runtime Integration | Lean 可能只知道 account / algorithm |
| BrokerAccountRegistry → Lean credential 未驗證 | Security | credential boundary 需治理 |
| PersonaCapitalBinding → runtime 未驗證 | Governance | persona authority 必須進 runtime context |
| RiskPolicy veto → Lean launch 未驗證 | Governance | risk 需能阻止 runtime start |
| paper/canary/live credential segregation 未驗證 | Environment | 環境不可混用 |
| fills/positions → Pantheon telemetry 未驗證 | Telemetry | reconciliation 需要 canonical events |

### 10.5 修補工作

```text
P0:
  - RuntimeLaunchAuthorizationService
  - BrokerAccountRef / CredentialRefAlias
  - LeanRuntimeContext
  - Stage-aware launch profile
  - Position/Fill telemetry exporter
```

### 10.6 狀態

```text
Generic Lean capability exists; Pantheon capital-pool execution loop unverified.
```

---

## 11. Loop 9 — Telemetry / Postmortem / Evolution 回路

### 11.1 Blueprint Loop

```text
events
→ reconciliation / drift
→ incident
→ postmortem
→ evolution decision
→ retrain / freeze / rollback / mutate
```

### 11.2 期望物件

```text
TelemetryEvent
RuntimeHeartbeat
ReconciliationRecord
DriftReport
AlertEvent
IncidentCase
Postmortem
EvolutionDecision
KillSwitchAction
AuditAction
```

### 11.3 現況判斷

Pantheon `TelemetryEvent` schema 很成熟，要求 runtime event 必須帶 binding_id、runtime_id、capital_pool_id、artifact_id、deployment_stage、plan_id 等欄位。  
但 Lean 是否實際發出此 schema，目前未見明確證據。

### 11.4 差異

| 差異 | 類型 | 說明 |
|---|---|---|
| LeanTelemetryExporter 未驗證 | Runtime Integration | schema mature 但 producer 不明 |
| TelemetryIngest → ProjectionWriter 未驗證 | Behavioral | BFF runtime state 需要 projection |
| ReconciliationService 未驗證 | Behavioral | backtest/paper/live 對帳需 runnable |
| Incident auto-open 未驗證 | Behavioral | drift/breach 是否會開 incident 不明 |
| Postmortem evidence collection 未驗證 | Behavioral | postmortem 需自動收證 |
| EvolutionActionDispatcher 未驗證 | Behavioral | decision 是否能 freeze / rollback / retrain 不明 |

### 11.5 修補工作

```text
P0:
  - Lean TelemetryEvent exporter
  - Telemetry schema validator
  - Runtime summary projection writer
  - Basic paper-vs-baseline reconciliation
  - Incident open on threshold breach

P1:
  - PostmortemBuilder
  - EvolutionDecision engine
  - Evolution action dispatcher
```

### 11.6 狀態

```text
Schema mature; runtime-to-evolution loop not proven.
```

---

## 12. Minimum Operating Loop 建議

### 12.1 不要一開始做完整 loop

完整 Pantheon loop 太大，應先做最小閉環：

```text
single persona
single capital pool
single approved artifact
paper-only
manual approval
Lean paper runtime
basic telemetry
basic reconciliation
manual incident / simple threshold incident
no canary
no live
no automatic mutation
```

### 12.2 MVP Loop

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
→ IncidentCase
→ EvolutionDecision(proposed only)
```

### 12.3 MVP 必要驗收

```text
1. CandidateArtifact 有 dataset_version / code_version / artifact checksum。
2. ApprovalDecision approved 後才能產 DeploymentPlan。
3. DeploymentPlan materialize 成 Lean launch manifest。
4. Lean 啟動時持有 runtime_binding_id。
5. Lean heartbeat 送回 Pantheon TelemetryEvent schema。
6. TelemetryEvent 可在 BFF runtime summary 看到。
7. ReconciliationRecord 可比較 baseline vs paper runtime。
8. Threshold breach 可開 IncidentCase。
9. EvolutionDecision 可 proposed，但不自動執行 live action。
```

### 12.4 P0-LOOP-001 closeout evidence

截至 2026-05-01，`P0-LOOP-001` 已補上最小 paper operating loop smoke，驗證範圍是：

```text
DeploymentPlan
→ RuntimeBinding
→ RuntimeBootstrapRequest / PantheonRuntimeContext
→ PaperRuntimeService heartbeat
→ TelemetryIngestService / RuntimeSummaryProjectionStore
→ BFF GET /api/v1/operator/runtime-state
```

此 smoke 使用 `pantheon/lean` bridge identity，不使用 `lean-platform`，且明確保持
`live_broker_enabled == False`、BFF runtime summary 的 broker health 為 `not_applicable`。

Closeout verification:

```bash
pytest -q services/control-plane/bff/test_p0_paper_operating_loop_smoke.py \
  services/telemetry/test_runtime_summary_projection.py \
  services/telemetry/test_paper_runtime_ingest_contract.py \
  services/execution/lean_runtime/test_bootstrap_contract.py \
  services/execution/lean_runtime/test_runtime_context.py
# 29 passed
```

這只關閉最小 paper smoke 證明；`ReconciliationRecord`、threshold incident、以及
`EvolutionDecision(proposed only)` 仍由後續 task 補齊，不在本 task 宣告完成。

---

## 13. Loop Gap Matrix

| Loop | Status | Biggest Gap | Severity | Primary Fix |
|---|---|---|---|---|
| Research Material | Partial | Source Registry / Evidence Store / Search Gateway | High | Data Gateway + EvidenceBundle |
| Strategy Distillation | Partial | StrategySpecSeed pipeline | High | SeedBuilder |
| Alpha Research | Partial | Experiment Orchestrator / CandidateArtifact packager | High | Research Orchestrator |
| Persona Teaching | Partial | TeachingDataset / mutation gate | Medium | Teaching trace pipeline |
| Human Imitation | Documented | Imitation dataset / policy gate | Medium | Imitation dataset builder |
| Consultation | Partial | Backend bounded context / ReviewGate integration | Medium-High | Consultation service |
| Promotion / Deployment | Partial | DeploymentPlan → Lean runtime | Critical | Lean launch manifest |
| Capital Pool Execution | Unverified | RuntimeBinding / broker entitlement | Critical | Lean RuntimeContext |
| Telemetry / Evolution | Partial | Lean telemetry exporter / reconciliation writer | Critical | Telemetry exporter + projection writer |

---

## 14. 本章結論

Pantheon 目前的 operating loop 狀態可以概括為：

```text
前半圈：source / research / artifact / governance 的概念與 UI / schema 已相當成形，但 orchestration 仍需補。
中間關鍵斷點：DeploymentPlan → RuntimeBinding → Lean runtime。
後半圈：TelemetryEvent schema 成熟，但 Lean producer、projection writer、reconciliation、incident、evolution action 尚未證明閉合。
```

SA 判斷：

> 現在不能只問「有沒有 registry / 有沒有 UI / 有沒有 Lean」。真正的驗收問題是：能否跑通一條 paper-only 的 Minimum Operating Loop，並保證每一步都有 canonical object、state transition、event、audit、lineage 和 test。若這條最小閉環跑不通，Pantheon 仍只是高覆蓋率控制台 + 治理骨架，而不是完整 operating system。

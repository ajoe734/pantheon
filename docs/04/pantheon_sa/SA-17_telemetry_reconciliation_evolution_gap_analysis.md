---
project: Pantheon
document_type: System Analysis Gap Report
batch: SA-16 to SA-20
language: zh-TW
assumption: >
  本批 SA 文件採用最新校正：目前實際在 VS Code 中被修改、用於 execution substrate 判讀的是 `ajoe734/Lean`；
  `ajoe734/lean-platform` 暫列為幾乎未動、歷史分支或待決 execution repo。
baseline: >
  以 Pantheon 總索引版系統分析文件為主準繩。該母文件定義 Pantheon 是多人格量化 operating system，
  由 Console / BFF / Shared Capability / Source Ingestion / Persona / Capital Pool / Knowledge & Registry /
  Consultation / Research / Policy Learning / Optimizer / Governance / Execution / Telemetry-Evolution 等 plane 組成，
  並要求 paper / canary / live、telemetry、reconciliation、postmortem、evolution 形成閉環。
---

# SA-17 — Telemetry / Reconciliation / Evolution 閉環差異分析

## 1. 本章目的

本章分析 Pantheon 後半圈：

```text
Lean runtime
→ TelemetryEvent
→ Telemetry Store
→ Reconciliation / Drift
→ Incident / Postmortem
→ EvolutionDecision
→ freeze / rollback / retrain / mutate / retire
```

是否已形成真正 operating system 閉環。

這是 Pantheon 從「能研究 / 能部署」變成「能治理 / 能自我修正 / 能演化」的關鍵。

---

## 2. Blueprint Requirement

Pantheon 母文件要求：

```text
live 表現必須持續和 backtest / paper / canary 做 reconciliation。
回饋不只回模型，也回 persona 與知識庫。
所有高風險操作要有 trace / audit。
Telemetry / Postmortem / Evolution Plane 要形成閉環。
```

第四包包含：

```text
Event Ingest Gateway
Canonical Event Normalizer
Telemetry Store
Metrics / Time-Series Store
Audit / Action Log
Heartbeat / Runtime Health
Backtest-Paper-Live Reconciliation
Position / Order / Fill Reconciliation
Feature / Label / Policy Drift Detector
Execution Drift Detector
Incident Classifier
Postmortem Builder
Evolution Controller
Kill Switch / Safe Mode
```

---

## 3. 現況摘要

### 3.1 強訊號

`pantheon` 的 `TelemetryEvent` schema 已經非常成熟，要求 event 帶：

```text
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

這表示 telemetry contract 的設計方向正確。

### 3.2 主要不確定點

但 schema 成熟不代表閉環完成。核心不確定點：

```text
Lean 是否真的產生此 schema？
pantheon ingest 是否強制驗證？
TelemetryEvent 是否寫入 authoritative telemetry store？
是否產生 runtime summary / BFF projection？
是否產生 ReconciliationRecord / DriftReport？
是否觸發 IncidentCase？
Postmortem 是否自動收 evidence？
EvolutionDecision 是否能 dispatch action？
```

---

## 4. Telemetry Producer Gap

### 4.1 Required Lean producer

如果 Lean 是 actual execution substrate，Lean 必須輸出：

```text
deploy_started
deploy_completed
heartbeat
pnl_snapshot
drawdown_snapshot
fill_observation
order_rejection
slippage_observation
pause_triggered
liquidate_triggered
rollback_started
rollback_completed
telemetry_mirror_mismatch
```

### 4.2 Required context

每個 Lean event 必須帶：

```text
runtime_binding_id / binding_id
runtime_id
deployment_plan_id / plan_id
artifact_id
artifact_version
capital_pool_id
deployment_stage
persona_capital_binding_id
strategy_id
trace_id
event_time
```

### 4.3 Gap

| Gap | Type | Severity |
|---|---|---|
| LeanTelemetryExporter 未驗證 | Runtime Integration | Critical |
| LeanRuntimeContext 未驗證 | Contract | Critical |
| Event context injection 未驗證 | Contract | Critical |
| retry / dedup 未驗證 | Operational | High |
| broker order/fill mapping 未驗證 | Reconciliation | High |

### 4.4 Required Work

```text
Lean/Pantheon/Telemetry/PantheonTelemetryEmitter
Lean/Pantheon/Bootstrap/PantheonRuntimeContext
pantheon/services/telemetry/ingest_validator.py
```

---

## 5. Telemetry Ingest Gap

### 5.1 Required ingest pipeline

```text
HTTP / queue / stream input
→ schema validation
→ binding validation
→ deployment_stage validation
→ idempotency dedup
→ telemetry store
→ metrics store
→ lineage edge writer
→ projection writer
```

### 5.2 Required validation

```text
binding_id exists
runtime_id matches binding
deployment_stage matches binding
artifact_id matches binding
capital_pool_id matches binding
event_time within binding active window
event_id not duplicate
```

### 5.3 Gap

| Gap | 說明 |
|---|---|
| ingest 是否驗證 binding identity 未完全確認 | schema 有，但 validator 需驗證 |
| telemetry store 是否 authoritative 未完全確認 | BFF / UI 需要真 read model |
| DLQ / replay policy 未確認 | event failure 需可重放 |
| telemetry-to-lineage writer 未確認 | postmortem 需要 trace |
| telemetry-to-projection writer 未確認 | UI runtime state 需更新 |

---

## 6. Runtime Health / Heartbeat Gap

### 6.1 Required

Runtime heartbeat 需至少包含：

```text
runtime_id
runtime_binding_id
capital_pool_id
deployment_stage
artifact_id
last_heartbeat_at
latency_ms
broker_connectivity
datafeed_connectivity
algorithm_status
error_summary
telemetry_lag_ms
```

### 6.2 Gap

Lean 標準 engine 有狀態 / log / result，但 Pantheon 需要 governance-grade heartbeat。

缺口：

```text
HEALTH-GAP-001: Lean heartbeat 是否轉 Pantheon heartbeat 未驗證。
HEALTH-GAP-002: heartbeat 是否含 runtime_binding_id 未驗證。
HEALTH-GAP-003: runtime health 是否能觸發 incident 未驗證。
HEALTH-GAP-004: BFF runtime board 是否來自 authoritative projection 未驗證。
```

### 6.3 P0-TEL-PROJ-001 Implementation Note

`P0-TEL-PROJ-001` closes the paper heartbeat projection slice without claiming
full reconciliation:

```text
services/telemetry/runtime_summary.py
→ TelemetryIngestService runtime_summary_store
→ GET /api/telemetry/runtime-summaries
→ BFF PANTHEON_TELEMETRY_API_URL service client
→ /api/v1/operator/runtime-state telemetry_summary
```

Delivered P0 projection fields:

```text
runtime_id
runtime_binding_id / binding_id
deployment_stage
capital_pool_id
artifact_id / artifact_version
plan_id / deployment_plan_id
persona_capital_binding_id
last_heartbeat_at
state
health_summary
engine_bridge_repo
engine_bridge_commit
engine_bridge_path
```

This is still a paper-only runtime status projection. Full reconciliation,
incident creation, and evolution dispatch remain downstream work.

---

## 7. Order / Fill / Position Reconciliation Gap

### 7.1 Required

```text
target order
submitted order
broker accepted order
fill
fee
position
cash
margin
broker snapshot
internal portfolio state
```

### 7.2 ReconciliationRecord contract

```json
{
  "record_id": "rec-...",
  "recon_type": "order_fill|position|cash|broker_snapshot",
  "runtime_binding_id": "...",
  "capital_pool_id": "...",
  "expected_ref": "...",
  "actual_ref": "...",
  "delta_summary": {},
  "severity": "none|low|medium|high|critical",
  "status": "open|acknowledged|resolved",
  "generated_at": "RFC3339"
}
```

### 7.3 Gap

| Gap | 說明 |
|---|---|
| target order capture 未驗證 | Lean 要知道 intended target |
| broker accepted order event 未驗證 | order lifecycle 需完整 |
| fill / fee / position snapshot 是否 canonical 未驗證 | reconciliation 需要 normalized records |
| broker snapshot polling fallback 未驗證 | stream 遺漏時需補 |
| cash / margin reconciliation 未驗證 | live 風險需要 |

### 7.4 P0-REC-001 Implementation Note

`P0-REC-001` closes the minimum paper-run reconciliation slice in
`services/reconciliation-drift` without claiming full broker / live
reconciliation:

```text
POST /api/reconciliation-drift/paper-runs/reconcile
→ reconciliation_records.json
→ optional IncidentCase create request to incidents service on threshold breach
→ EvolutionDecision proposal envelope only; no review / approval / execute dispatch
```

Delivered P0 `ReconciliationRecord` identity links:

```text
runtime_binding_id / binding_id
runtime_id
deployment_plan_id
deployment_stage = paper
artifact_id
artifact_version
capital_pool_id
persona_capital_binding_id
trace_id
telemetry_event_ids via evidence / delta summary
```

This is intentionally a paper-only threshold seed. Full order/fill/broker
snapshot reconciliation, Postgres-owned `telemetry.reconciliation_records`, and
automatic evolution lifecycle dispatch remain follow-on work.

---

## 8. Backtest / Paper / Canary / Live Reconciliation Gap

### 8.1 Required

```text
ExperimentRun baseline
Paper runtime actual
Canary runtime actual
Live runtime actual
```

比較：

```text
returns
drawdown
turnover
slippage
cost
fill rate
signal coverage
position drift
risk exposure
regime conditions
```

### 8.2 Gap

| Gap | 說明 |
|---|---|
| ExperimentRun baseline 是否與 DeploymentPlan 連結未驗證 | 需要 run_id / artifact lineage |
| paper telemetry 是否足以比較未驗證 | Lean producer 缺 |
| canary / live stage 未證明 | stage-aware runtime 未證明 |
| RuntimeBaselineComparator 未驗證 | 比較器需實作 |
| DriftReportStore 未驗證 | 結果需 persist / review |

---

## 9. Feature / Label / Policy / Execution Drift Gap

### 9.1 Drift Types

```text
feature_drift
label_drift
policy_drift
execution_drift
broker_drift
data_vendor_drift
risk_exposure_drift
```

### 9.2 Required DriftReport

```json
{
  "report_id": "drift-...",
  "drift_type": "execution_drift",
  "scope_ref": {
    "runtime_binding_id": "...",
    "artifact_id": "...",
    "capital_pool_id": "..."
  },
  "baseline_ref": "...",
  "current_ref": "...",
  "severity": "low|medium|high|critical",
  "metrics": {},
  "evidence_refs": [],
  "recommended_action": "observe|pause|rollback|retrain|retire",
  "generated_at": "RFC3339"
}
```

### 9.3 Gap

```text
DRIFT-GAP-001: feature / label drift baseline 不明。
DRIFT-GAP-002: execution drift detector 未驗證。
DRIFT-GAP-003: drift severity policy 未驗證。
DRIFT-GAP-004: drift → incident / evolution handoff 未驗證。
```

---

## 10. Incident Pipeline Gap

### 10.1 Required

```text
AlertRule
AlertEvent
IncidentClassifier
IncidentCase
OwnerAssignment
MitigationAction
EvidenceCollector
PostmortemTrigger
```

### 10.2 Gap

| Gap | 說明 |
|---|---|
| AlertRuleEngine 未驗證 | threshold / rule 需正式化 |
| IncidentClassifier 未驗證 | telemetry breach 需分類 |
| IncidentCase 是否連 RuntimeBinding 未驗證 | 事故 target 需明確 |
| mitigation action 是否驅動 Lean 未驗證 | pause / rollback / liquidate 要能執行 |
| incident audit trail 未驗證 | operator action 需留痕 |

### 10.3 Required

```text
runtime health degraded → alert
order rejection spike → alert
drawdown breach → incident
broker disconnect → incident
telemetry missing → incident
```

---

## 11. Postmortem Pipeline Gap

### 11.1 Required

Postmortem 應自動收：

```text
incident timeline
runtime_binding
deployment_plan
artifact
approval decision
risk policy
telemetry events
reconciliation records
operator actions
Lean runtime logs
broker events
```

### 11.2 Gap

```text
PM-GAP-001: EvidenceCollector 未驗證。
PM-GAP-002: timeline builder 未驗證。
PM-GAP-003: root cause taxonomy 未定義。
PM-GAP-004: corrective actions 是否可追蹤未驗證。
PM-GAP-005: postmortem → EvolutionDecision 未驗證。
```

### 11.3 P1-EVO-001 Implementation Note

`P1-EVO-001` closes the narrow postmortem evidence baseline without claiming
the full postmortem automation pipeline:

```text
services/incident/evidence_collector.py
  → PostmortemEvidenceCollector
  → EvidenceBundle
  → validated IncidentCase
```

Delivered evidence captured on `IncidentCase`:

```text
telemetry_event_ids
runtime_id
binding_id
deployment_stage
deployment_plan_id
artifact_id / artifact_version
capital_pool_id
persona_capital_binding_id
trace_id
lineage_ref
evidence_summary
```

Residual postmortem gaps remain:

```text
PM-GAP-002: timeline builder remains unverified.
PM-GAP-003: root cause taxonomy remains undefined.
PM-GAP-004: corrective actions tracking remains unverified.
```

---

## 12. Evolution Pipeline Gap

### 12.1 Required

```text
EvolutionDecision:
  proposed
  reviewed
  approved
  executed
  superseded
```

Decision types:

```text
freeze_strategy
rollback_runtime
retrain_model
revalidate_strategy
retire_artifact
mutate_persona
update_risk_policy
split_persona
merge_persona
```

### 12.2 Gap

| Gap | 說明 |
|---|---|
| EvolutionDecision proposal 是否由 drift/postmortem 自動產生未驗證 | 行為閉環缺 |
| Evolution review gate 未驗證 | live-impact action 必須 approval |
| EvolutionActionDispatcher 未驗證 | decision 可能只是 record |
| freeze / rollback 是否驅動 Lean 未驗證 | runtime action bridge 缺 |
| retrain / revalidate 是否驅動 research orchestrator 未驗證 | 回研究閉環缺 |
| mutate persona 是否走 persona mutation gate 未驗證 | learning safety 缺 |

### 12.3 P1-EVO-001 Implementation Note

`P1-EVO-001` adds invariant coverage for the governed dispatch baseline:

```text
services/control-plane/governance/test_evolution_dispatcher_invariants.py
```

Verified baseline:

```text
dispatch_approved() rejects proposed / reviewed / rejected / canceled decisions
EvolutionDecision.execute() rejects non-approved decisions
live freeze and force_risk_off cannot dispatch before approval
linked_postmortem_id / linked_incident_id survive the dispatch metadata path
EvolutionDecisionStore preserves the single-active-decision invariant
```

This does not claim direct Lean mutation, research job completion, or persona
mutation automation. It proves the required approval gate and prevents
`EvolutionDecision` from becoming an unreviewed live mutation surface.

---

## 13. Kill Switch / Safe Mode Gap

### 13.1 Required

```text
pool risk-off
pause new entries
liquidate
fallback artifact
environment-wide safe mode
runtime pause / replace
```

### 13.2 Gap

```text
SAFE-GAP-001: kill switch 是否有 secondary path 未驗證。
SAFE-GAP-002: kill switch 是否能作用到 Lean 未驗證。
SAFE-GAP-003: kill switch action 是否產 TelemetryEvent / AuditAction 未驗證。
SAFE-GAP-004: safe mode 是否限制 OpenClaw / new deployments 未驗證。
```

---

## 14. Minimum Telemetry Loop

### 14.1 P0 要先做到

```text
Lean emits heartbeat
Lean emits deploy_started / deploy_completed
Lean emits pnl_snapshot / drawdown_snapshot
Lean emits order_rejection / fill_observation
pantheon validates TelemetryEvent
pantheon writes runtime summary
pantheon creates simple ReconciliationRecord
pantheon opens IncidentCase on threshold breach
```

### 14.2 P0 不需要先做

```text
full live automation
automatic persona mutation
full RL retrain
advanced social drift
complex multi-pool reconciliation
```

---

## 15. Telemetry-to-Evolution Gap Matrix

| Segment | Status | Gap | Severity |
|---|---|---|---|
| Lean → TelemetryEvent | Unverified | exporter / context missing | Critical |
| TelemetryEvent → Ingest | Partial | validator / binding check needs proof | High |
| Ingest → Projection | Unverified | runtime summary writer | High |
| Projection → Reconciliation | Unverified | comparator / record store | High |
| Reconciliation → DriftReport | Unverified | drift detector | High |
| DriftReport → Incident | Unverified | alert/classifier | High |
| Incident → Postmortem | Partial | P1-EVO evidence collector baseline; timeline/root-cause/actions still open | Medium-High |
| Postmortem → Evolution | Partial | postmortem/incident link preserved through EvolutionDecision dispatch; automatic proposal still open | High |
| Evolution → Action | Partial / governed baseline | approved-only dispatch invariant proven; direct Lean/research/persona completion still open | Critical |

---

## 16. Required Tests

```text
test_lean_heartbeat_emits_valid_telemetry_event
test_telemetry_event_missing_binding_rejected
test_telemetry_event_stage_mismatch_rejected
test_pnl_snapshot_updates_runtime_summary
test_order_rejection_spike_opens_incident
test_fill_observation_creates_reconciliation_record
test_runtime_degraded_creates_alert
test_incident_resolution_generates_postmortem_draft
test_postmortem_publish_proposes_evolution_decision
test_approved_evolution_rollback_dispatches_runtime_command
```

---

## 17. 本章結論

Telemetry / Reconciliation / Evolution 是 Pantheon 後半圈的核心。目前：

```text
TelemetryEvent schema maturity: 高
Lean telemetry producer proof: 低 / 未驗證
Telemetry ingest-to-projection proof: 中低
Reconciliation service proof: 低
Incident / postmortem automation proof: 低到中
Evolution action dispatch proof: 低
```

SA 判斷：

> Pantheon 後半圈目前最可能是「schema 與 UI 先行，runtime producer 與行為閉環尚未完整」。P0 不應追求完整自動 evolution，而應先打通 Lean → TelemetryEvent → runtime summary → basic reconciliation → incident 的 paper-only 最小閉環。

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

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.





# SA-13 — Contract / Schema 差異分析

## 1. 本章目的

本章分析 Pantheon 目前的 contract / schema 是否足以支撐完整 operating system 閉環。

本章重點不是列出所有 schema，而是檢查：

```text
1. 必要 canonical object 是否存在？
2. schema 是否含足夠 lineage / versioning / audit 欄位？
3. API contract 是否與前端 / 後端 / Lean runtime 實作一致？
4. event contract 是否足以驅動 telemetry / reconciliation / evolution？
5. command contract 是否有 RBAC / idempotency / actor / trace？
6. contract 是否因 Lean vs lean-platform 混淆而產生 repo ownership drift？
```

---

## 2. Contract 分類

Pantheon 至少需要下列 contract 類型：

```text
Domain Object Contract
API Contract
Command Contract
Event Contract
Runtime Launch Contract
Telemetry Contract
Error Contract
Idempotency Contract
RBAC / Entitlement Contract
Lineage Contract
Storage Contract
```

目前 repo 內已看到較成熟的：

```text
BFF_API_CONTRACT.md
TelemetryEvent schema
Target Architecture
資料表 / schema 設計文件
Lineage / telemetry storage decisions
Incident contract
```

但仍要檢查它們是否已和 front、pantheon implementation、Lean runtime 串起。

---

## 3. Domain Object Contract 差異

### 3.1 必要 canonical objects

依藍圖與 Target Architecture，最低限度需要：

```text
StrategySpec
ArtifactRecord
ApprovalDecision
DeploymentPlan
PersonaCapitalBinding
RuntimeBinding
TelemetryEvent
EvolutionDecision
```

完整 operating system 還需要：

```text
SourceRecord
EvidenceBundle
StrategySpecSeed
AlphaTemplate
ExperimentTask
ExperimentRun
CandidateArtifact
AllocationPolicyArtifact
CapitalPool
RiskPolicy
BrokerAccount
RuntimeStatus
ReconciliationRecord
DriftReport
IncidentCase
Postmortem
AuditAction
KillSwitchAction
```

### 3.2 Gap Matrix

| Object | Required For | Current Risk | Gap Type |
|---|---|---|---|
| SourceRecord | source ingestion / evidence | 是否 authoritative 不明 | Registry |
| EvidenceBundle | search / review / postmortem | Search Gateway 未驗證 | Contract |
| StrategySpecSeed | source → strategy | pipeline 未驗證 | Behavioral |
| StrategySpec | research input | UI surface 有，canonical store 需驗證 | Store |
| ExperimentRun | research replay | dataset_version binding 需驗證 | Replay |
| CandidateArtifact | promotion input | artifact packager 需驗證 | Behavioral |
| ApprovalDecision | governance gate | 是否驅動 DeploymentPlan 需驗證 | Behavioral |
| DeploymentPlan | runtime planning | Lean manifest materialization 需補 | Runtime |
| RuntimeBinding | runtime identity | Lean injection 需補 | Critical |
| TelemetryEvent | runtime evidence | schema 成熟，Lean producer 未驗證 | Runtime |
| ReconciliationRecord | drift / evidence | service 需補 | Behavioral |
| EvolutionDecision | feedback action | action dispatcher 需補 | Behavioral |

### 3.3 最重要的缺口

```text
RuntimeBinding 是整條 chain 的 pivot。
沒有 RuntimeBinding，DeploymentPlan 不能和 Lean runtime 對接；
TelemetryEvent 不能正確歸因；
Reconciliation / Incident / Postmortem / Evolution 都會斷。
```

---

## 4. API Contract 差異

### 4.1 BFF Read API

BFF contract 已定義 read-oriented routes：

```text
/personas
/capital-pools
/bindings
/deployment-plans
/approval-decisions
/runtime-bindings
/runtimes/{id}/status
/telemetry
/lineage
/incidents
/evolution-decisions
```

這對 Console Plane 足夠合理。

### 4.2 Command API 缺口

前端已使用 POST 類 command，但 canonical BFF contract 仍以 read-only 為主。需要補：

```text
SubmitReviewDecision
CreateDeploymentPlan
RequestRuntimeDeploy
RequestRollback
RequestPause
RequestLiquidate
ApproveEvolutionDecision
ExecuteEvolutionAction
CreateConsultRequest
RecordCommitteeDecision
CommitTeachingSession
```

### 4.3 Command Contract 必要欄位

每個 command 必須有：

```json
{
  "command_id": "uuid",
  "command_type": "...",
  "actor_ref": "...",
  "actor_role": "...",
  "target_ref": {
    "target_type": "...",
    "target_id": "..."
  },
  "idempotency_key": "...",
  "trace_id": "uuid",
  "correlation_id": "uuid",
  "reason": "...",
  "requested_at": "RFC3339",
  "payload": {}
}
```

### 4.4 Gap

| Gap | 說明 |
|---|---|
| Read API / Command API 未分層 | BFF boundary drift |
| command idempotency 未確認 | deploy / rollback / kill switch 必須冪等 |
| command RBAC 未完整驗證 | operator / approver / admin 必須分 |
| command audit 未確認 | 高風險動作需 before/after state |
| command-to-event mapping 未完整 | command 成功後應產 domain event |

### 4.5 P0-BFF-CMD-001 Disposition

`P0-BFF-CMD-001` closes the BFF read/command layering gap for the P0 paper
loop contract baseline:

```text
BFF_API_CONTRACT.md remains the GET-only read API contract.
BFF_COMMAND_API_CONTRACT.md defines the governed command facade.
POST /api/v1/operator/commands requires authenticated actor identity, trace,
X-Idempotency-Key, command-specific RBAC/policy validation, and audit reason.
GET /api/v1/operator/commands/{command_id} is the read projection for command
status; it is not a mutation or retry endpoint.
```

Focused BFF contract tests cover idempotency-key rejection, idempotency replay,
and runtime / deployment / approval / incident command admission evidence.

---

## 5. Runtime Launch Contract 差異

### 5.1 為什麼需要 Launch Contract

目前最新前提是 `Lean` 承接 execution substrate。
因此 `pantheon` 與 `Lean` 之間必須有正式 launch contract，而不是讓 Lean 任意讀 config。

### 5.2 Lean Launch Manifest 建議 schema

```json
{
  "schema_version": "pantheon.runtime.launch.v1",
  "launch_id": "uuid",
  "runtime_binding_id": "uuid",
  "deployment_plan_id": "dp-...",
  "artifact_id": "art-...",
  "artifact_version": "1.2.3",
  "artifact_checksum": "sha256:...",
  "strategy_id": "strat-...",
  "capital_pool_id": "pool-...",
  "risk_policy_id": "risk-...",
  "persona_capital_binding_id": "pcb-...",
  "deployment_stage": "paper",
  "execution_mode": "paper",
  "broker_account_ref": "broker-...",
  "credential_ref_alias": "secret-alias",
  "runtime_profile": {
    "engine_repo": "Lean",
    "engine_commit": "...",
    "image": "...",
    "config_profile": "paper-default"
  },
  "rollback_parent": null,
  "created_at": "2026-05-01T00:00:00Z",
  "expires_at": "2026-05-01T01:00:00Z",
  "issued_by": "pantheon",
  "signature": "..."
}
```

### 5.3 必要 guard

Lean 讀 manifest 時必須檢查：

```text
schema_version supported
artifact_checksum valid
deployment_stage allowed
credential_ref_alias stage-compatible
runtime_binding_id present
deployment_plan_id present
manifest not expired
signature valid
```

### 5.4 Gap

目前 `Lean/Launcher/Program.cs` 顯示標準 Lean 啟動流程，但未見明確 Pantheon manifest consumer。這是最關鍵 runtime contract gap。

---

## 6. Telemetry Contract 差異

### 6.1 已有 schema 很成熟

`TelemetryEvent` schema 要求：

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

這非常接近 Pantheon 需要的 runtime evidence contract。

### 6.2 Gap

| Gap | 說明 |
|---|---|
| Lean producer 未驗證 | schema 有，但 runtime 是否產生未知 |
| runtime context injection 未驗證 | 事件需要 binding / plan / pool / artifact |
| event type coverage 需擴充 | order / fill / position / heartbeat / broker disconnect |
| account_ref / broker 欄位 optional 可能不足 | reconciliation 需 broker-level trace |
| engine_repo / engine_commit 缺 | Lean vs lean-platform 混淆後建議新增 |
| ingestion validator 是否強制 schema 未驗證 | invalid events 應 fail closed |

### 6.3 建議新增欄位

因目前有 repo ambiguity，建議 telemetry metadata 或 top-level 增加：

```text
engine_repo
engine_commit
runtime_image
launch_manifest_hash
runtime_adapter_version
```

這能在事件層面明確知道 event 來自 `Lean` 還是其他 runtime。

---

## 7. Event Contract 差異

### 7.1 必要 domain events

```text
SourceIngested
StrategySpecSeedCreated
StrategySpecRegistered
ExperimentRunCompleted
CandidateArtifactRegistered
ReviewGatePassed
ApprovalDecisionRecorded
DeploymentPlanCreated
RuntimeBindingCreated
RuntimeStarted
RuntimeHeartbeatReceived
OrderSubmitted
OrderFilled
PositionSnapshotReceived
RuntimeDegraded
DriftDetected
IncidentOpened
PostmortemPublished
EvolutionDecisionProposed
EvolutionDecisionApproved
RollbackRequested
RollbackCompleted
KillSwitchActivated
```

### 7.2 Event Envelope

建議所有 event 使用同一 envelope：

```json
{
  "event_id": "uuid",
  "event_type": "...",
  "event_version": "1.0.0",
  "event_time": "RFC3339",
  "producer": {
    "service": "...",
    "repo": "...",
    "commit": "..."
  },
  "actor_ref": "...",
  "trace_id": "uuid",
  "correlation_id": "uuid",
  "idempotency_key": "...",
  "target_ref": {
    "type": "...",
    "id": "..."
  },
  "payload": {}
}
```

### 7.3 Gap

| Gap | 說明 |
|---|---|
| Domain event catalog 未完全落地 | 物件 state transition 需要 events |
| Event producer / consumer matrix 未完整 | 不知道誰產誰消費 |
| Lean event conversion 未驗證 | engine event 需轉 Pantheon event |
| event ordering / delivery policy 需接到 CI | 投遞失敗需 retry / DLQ |
| correlation across repo 需明確 | front → pantheon → Lean → pantheon |

---

## 8. Error Contract 差異

### 8.1 BFF Error Contract

BFF contract 已定義：

```text
INVALID_REQUEST
UNKNOWN_FILTER_FIELD
INVALID_FILTER_VALUE
INVALID_TIME_RANGE
PAGINATION_OUT_OF_RANGE
UNAUTHORIZED
FORBIDDEN
NOT_FOUND
DOWNSTREAM_UNAVAILABLE
DOWNSTREAM_TIMEOUT
INTERNAL_ERROR
```

### 8.2 需要補的 governance/runtime errors

```text
INVALID_STATE_TRANSITION
ARTIFACT_NOT_APPROVED
DEPLOYMENT_PLAN_REQUIRED
RUNTIME_BINDING_REQUIRED
CAPITAL_POOL_NOT_ADMISSIBLE
RISK_POLICY_REJECTED
BROKER_ACCOUNT_NOT_ENTITLED
LIVE_REQUIRES_ROLLBACK_TARGET
MANIFEST_EXPIRED
MANIFEST_SIGNATURE_INVALID
TELEMETRY_BINDING_MISMATCH
KILL_SWITCH_ACTIVE
SAFE_MODE_BLOCKED
```

### 8.3 Gap

BFF generic error 不足以描述 promotion / runtime / governance failure。需要 domain-specific error catalog。

---

## 9. Idempotency Contract 差異

### 9.1 必須冪等的操作

藍圖與 NFR 要求以下操作必須冪等：

```text
ingest
experiment submission
artifact registration
deploy / replace / rollback
pause / liquidate
telemetry ingest
alert / incident creation
trainer commit
```

### 9.2 建議規則

```text
command_id = unique command identity
idempotency_key = client retry identity
event_id = event retry identity
runtime_binding_id = runtime identity
deployment_plan_id = deployment attempt identity
```

### 9.3 Gap

| Gap | 風險 |
|---|---|
| deployment command 無 idempotency | 重複啟動 runtime |
| telemetry event 無 event_id dedup | duplicate fill / duplicate incident |
| rollback command 無 idempotency | 重複 liquidation |
| trainer commit 無 idempotency | 重複 persona mutation |
| source ingest 無 idempotency | duplicated evidence |

---

## 10. RBAC / Entitlement Contract 差異

### 10.1 BFF RBAC

BFF contract 定義 viewer / operator / approver / admin 等 role，但 live runtime 與 broker entitlement 需要更細。

### 10.2 需要補的 scopes

```text
source.read
source.ingest
search.read
strategy.write
experiment.run
artifact.register
approval.record
deployment.plan
runtime.deploy.paper
runtime.deploy.canary
runtime.deploy.live
runtime.pause
runtime.liquidate
runtime.rollback
capital_pool.read
capital_pool.bind
broker_account.use
telemetry.write
incident.manage
evolution.approve
evolution.execute
```

### 10.3 Lean runtime entitlement

Lean launch 不應只看 config，而要驗證：

```text
credential_ref_alias allowed for deployment_stage
broker_account_ref allowed for capital_pool_id
runtime_action allowed by actor / policy
risk_policy pass marker present
```

### 10.4 Gap

RBAC 不能只存在於 BFF。runtime launch 和 broker credential 使用也要有 entitlement contract。

---

## 11. Lineage Contract 差異

### 11.1 Required lineage chain

```text
SourceRecord
→ EvidenceBundle
→ StrategySpecSeed
→ StrategySpec
→ ExperimentRun
→ CandidateArtifact
→ ApprovalDecision
→ DeploymentPlan
→ RuntimeBinding
→ TelemetryEvent
→ ReconciliationRecord
→ IncidentCase
→ Postmortem
→ EvolutionDecision
```

### 11.2 Required edge fields

```text
edge_id
from_type
from_id
to_type
to_id
edge_type
created_at
actor_ref
trace_id
evidence_refs
```

### 11.3 Gap

| Gap | 說明 |
|---|---|
| lineage read model 可能有，但 normalized edges 需驗證 | Target Architecture 說 read model derived only |
| Lean telemetry 必須寫 runtime-to-telemetry edges | producer 未驗證 |
| postmortem / evolution 需回寫 lineage | action loop 未驗證 |

---

## 12. Storage Contract 差異

### 12.1 Suggested truth stores

```text
Registry Store:
  StrategySpec / ArtifactRecord / ApprovalDecision / DeploymentPlan

Runtime Store:
  RuntimeBinding / RuntimeStatus / RuntimeAction

Telemetry Store:
  TelemetryEvent / RuntimeHeartbeat / Metrics

Evidence Store:
  SourceRecord / EvidenceBundle / SearchIndex metadata

Incident Store:
  IncidentCase / Postmortem / EvolutionDecision

Audit Store:
  AuditAction / command history
```

### 12.2 Gap

目前需要明確區分：

```text
canonical store
derived read model
cache
mock data
seed data
preview fallback
```

如果 BFF read_store / front mock 與 canonical store 混用，會造成 truth ambiguity。

### 12.3 P1-PERSIST-001 disposition

`P1-PERSIST-001` adds a shared staging/prod persistence posture guard:

```text
services.foundation.persistence_posture
```

In `PANTHEON_PERSISTENCE_POSTURE` / `PANTHEON_ENV` staging-prod modes, the
Postgres owner-store services now fail fast unless their service backend env is
`postgres`, `DATABASE_URL` is a Postgres DSN, and the shared object-store env
vars are present. JSON/JSONL fallback remains a dev posture only and is surfaced
as `dev_fallback_allowed=true` only outside enforced modes.

The guard is wired into `/healthz` dependencies and legacy health payloads for:

```text
consultation
training-session
policy-learning
research-orchestrator
research-worker-gateway
governance
capital
incidents
postmortems
promotion
memory
reconciliation-drift
```

Source-ingest and search keep their existing source/search-specific posture
guard; the platform check script includes both posture families.

---

## 13. Contract-to-Repo Mapping

| Contract | Owner Repo | Consumer Repo | Current Gap |
|---|---|---|---|
| BFF Read API | pantheon | front | contract mature; implementation drift possible |
| BFF Command API | pantheon | front | needs formal contract |
| Runtime Launch Manifest | pantheon | Lean | missing / unverified |
| RuntimeBinding | pantheon | Lean + front | store / injection unverified |
| TelemetryEvent | pantheon | Lean producer / pantheon consumer | schema mature; producer missing |
| EvidenceBundle | pantheon | OpenClaw + front | Search Gateway missing |
| CapitalPool / BrokerAccount | pantheon | Lean | entitlement boundary unverified |
| Incident / Evolution | pantheon | front + runtime actions | action dispatch unverified |

---

## 14. P0 Contract Work

```text
1. RuntimeLaunchManifest schema
2. RuntimeBinding schema + store contract
3. Command API contract
4. Telemetry producer contract for Lean
5. DeploymentPlan → LaunchManifest materializer contract
6. CapitalPool / BrokerAccount entitlement contract
7. Event envelope schema
8. Domain error catalog
9. Cross-repo OpenAPI / JSON schema CI
```

---

## 15. 本章結論

Pantheon 的 contract maturity 呈現不均衡：

```text
BFF Read Contract: 高
TelemetryEvent Schema: 高
Target Architecture: 高
Runtime Launch Contract: 低 / 缺
Command Contract: 中低 / 需正式化
Source / Evidence Contract: 中低
Lean Runtime Producer Contract: 低 / 未驗證
Reconciliation / Evolution Event Contract: 中低
```

SA 判斷：

> 目前最大 contract 缺口不是沒有 BFF 或 telemetry schema，而是沒有把 `pantheon` 的 DeploymentPlan / RuntimeBinding / TelemetryEvent 與實際 execution substrate `Lean` 之間的 launch、runtime context、event producer contract 正式化。這個 contract 不補，所有 UI、governance、telemetry 文件都無法形成真正 operating loop。

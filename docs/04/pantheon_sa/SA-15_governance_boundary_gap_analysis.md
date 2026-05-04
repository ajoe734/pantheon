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





# SA-15 — Governance Boundary 差異分析

## 1. 本章目的

本章分析 Pantheon 在 governance boundary 上的差異與風險。

Pantheon 藍圖的核心不是讓 agent 直接交易，而是建立一套「研究共享、知識共享、會診共享，但資金池與 live 執行隔離」的量化 operating system。因此 governance boundary 是安全底線。

本章聚焦：

```text
Research vs Execution
LLM / OpenClaw vs Runtime
Persona vs Capital Pool
BFF vs Canonical Store
Pantheon vs Lean
Broker Secret Boundary
Risk Veto Boundary
Human Approval Boundary
Kill Switch Boundary
Telemetry / Evolution Boundary
```

---

## 2. Governance Boundary 的基本公理

根據 Pantheon 藍圖與 Target Architecture，以下規則不可被實作繞過：

```text
1. 研究產出 artifact，execution consume artifact。
2. persona 是正式一級物件，不是 prompt。
3. risk 是 veto layer，不是 advisory note。
4. artifact / deployment / runtime 必須可版本化、可回放。
5. all live paths go through paper / canary / rollback.
6. OpenClaw / LLM / agent 不直接當 execution kernel。
7. live feedback 不可直接突變 live behavior。
8. capital pool 和 broker state 隔離。
9. telemetry 必須連回 runtime binding。
10. postmortem / evolution 必須走 review。
```

---

## 3. Boundary 1 — Research vs Execution

### 3.1 Required Boundary

```text
Research Plane:
  StrategySpec
  ExperimentRun
  CandidateArtifact

Governance Plane:
  ApprovalDecision
  DeploymentPlan

Execution Plane:
  RuntimeBinding
  Lean runtime
  broker / orders / fills / positions
```

### 3.2 禁止行為

```text
Research worker 直接啟動 Lean live runtime
ExperimentRun 直接下單
CandidateArtifact 直接 deployment
LLM 直接修改 Lean config
Research data feed 直接進 live broker decision
```

### 3.3 Current Gap

目前已知 `Lean` 是實際修改的 execution repo，但未驗證 `Lean` 是否只接受 approved artifact projection。  
若 Lean 仍接受一般 LEAN config / job packet 直接啟動，則可能存在 research → execution bypass。

### 3.4 Required Controls

```text
DeploymentPlanMaterializer
RuntimeLaunchManifest
ArtifactApprovalGuard
RuntimeBindingRequiredGuard
Lean startup manifest validation
```

### 3.5 Acceptance Criteria

```text
Lean cannot start a Pantheon-managed runtime without:
  - deployment_plan_id
  - runtime_binding_id
  - approved artifact_id/version/checksum
  - capital_pool_id
  - persona_capital_binding_id
  - deployment_stage
```

---

## 4. Boundary 2 — LLM / OpenClaw vs Execution

### 4.1 Required Boundary

OpenClaw / LLM 可負責：

```text
research
summarization
evidence search
strategy ideation
consultation
review assistance
workflow triggering under policy
```

OpenClaw / LLM 不可直接：

```text
place order
load broker credentials
modify live runtime config
bypass approval
kill / liquidate without governed command path
mutate live strategy from short-term feedback
```

### 4.2 Current Gap

Pantheon governance docs 已有 deny-first、no direct LEAN 等方向，但需要確認：

```text
OpenClaw tool call 是否經 CapabilityResolver？
Search 是否經 ACL-aware Search Gateway？
Workflow trigger 是否經 policy evaluator？
Runtime command 是否需要 human / admin approval？
```

### 4.3 Required Controls

```text
ToolEntitlementService
SearchGateway
CapabilityResolver
CommandPolicyEvaluator
OpenClawAuditLog
NoDirectLeanTool policy
```

### 4.4 Acceptance Criteria

```text
Any OpenClaw-triggered action that affects runtime must create:
  - command_id
  - actor_ref
  - policy_decision
  - approval_ref if required
  - audit_action
```

---

## 5. Boundary 3 — Persona vs Capital Pool

### 5.1 Required Boundary

persona 不因擁有某些 tools 就能管理資金池。必須透過：

```text
PersonaCapitalBinding
CapitalPool
RiskPolicy
BrokerAccountRegistry
DeploymentPlan
RuntimeBinding
```

### 5.2 禁止行為

```text
persona directly chooses broker account
persona directly gets broker secret
persona directly changes capital pool risk limit
persona directly deploys live artifact
persona shares live pool state with unrelated persona
```

### 5.3 Current Gap

前端有 persona / capital pool / binding surface，Target Architecture 也把 PersonaCapitalBinding 和 RuntimeBinding 列為核心物件。  
但 Lean runtime 是否帶 `persona_capital_binding_id` 仍未驗證。TelemetryEvent schema 已要求該欄位，這是正確方向，但 runtime producer 還需補證。

### 5.4 Required Controls

```text
PersonaCapitalBindingStore
PoolAdmissibilityChecker
DeploymentPlanBindingValidator
LeanRuntimeContext.persona_capital_binding_id
TelemetryEvent.persona_capital_binding_id required
```

### 5.5 Acceptance Criteria

```text
No DeploymentPlan can be created unless:
  - persona has active binding
  - binding role allows target deployment_stage
  - capital pool status allows deployment
  - risk policy passes
```

---

## 6. Boundary 4 — BFF vs Canonical Store

### 6.1 Required Boundary

BFF 應是 read model / command facade，不是 canonical store。  
BFF contract 明確寫 BFF read-oriented、no parallel truth。

### 6.2 Current Gap

前端使用 BFF POST command path，而 BFF contract v1 仍聲稱 GET-only / read-only。這表示 read / command boundary 需要正式拆分。

### 6.3 風險

```text
BFF 若直接保存或改 canonical state，會產生 parallel truth。
BFF 若發 command 但不留 idempotency / audit，會產生 ghost action。
BFF 若用 mock / seed 補資料，operator 可能誤判 runtime truth。
```

### 6.4 Required Controls

```text
Read API Contract
Command API Contract
CommandQueue
CommandExecutor
IdempotencyStore
AuditActionStore
CanonicalStoreOnlyWrites
```

### 6.5 Acceptance Criteria

```text
Every BFF command must:
  - write through command executor
  - include idempotency_key
  - pass RBAC / policy
  - emit command accepted / completed / failed event
  - never silently update UI-only state as canonical truth
```

### 6.6 P0-BFF-CMD-001 Disposition

`P0-BFF-CMD-001` formalizes this boundary for the P0 paper-loop baseline:

```text
Read API:
  services/control-plane/bff/BFF_API_CONTRACT.md
  GET-only read and composed-view surfaces

Command API:
  services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md
  POST /api/v1/operator/commands
  GET  /api/v1/operator/commands/{command_id}
```

The command facade is explicitly not a canonical domain store. Accepted
runtime, deployment, approval, and incident commands must persist actor,
trace/correlation, idempotency, RBAC/policy decision, target, audit reason, and
command receipt evidence before dispatching to the owning control-plane service.

---

## 7. Boundary 5 — Pantheon vs Lean

### 7.1 Required Boundary

Pantheon owns:

```text
permissions
governance checks
artifact registry
approval decisions
deployment plans
runtime bindings
telemetry ingest
evolution review
```

Lean owns:

```text
engine execution
algorithm lifecycle
datafeed / brokerage execution
orders / fills / portfolio state
runtime engine events
```

Lean should not own:

```text
who is allowed to trade
which artifact is approved
which capital pool is admissible
which persona can sponsor live deployment
what evolution action is approved
```

### 7.2 Current Gap

Because `Lean` is now the actual modified repo, it is especially important that Pantheon-specific governance code does not disappear into generic engine config.

### 7.3 Required Controls

```text
PantheonLaunchManifest
PantheonRuntimeContext
PantheonArtifactGuard
PantheonBrokerEntitlementGuard
PantheonTelemetryEmitter
LeanAdapter boundary
```

### 7.4 Acceptance Criteria

```text
Lean can reject a launch manifest but cannot approve one.
Pantheon approves; Lean executes.
Pantheon authorizes broker use; Lean connects.
Pantheon records RuntimeBinding; Lean reports with binding_id.
```

---

## 8. Boundary 6 — Broker Secret Boundary

### 8.1 Required Boundary

Broker secrets must not be visible to:

```text
front-ai-trading-system
OpenClaw / LLM
persona memory
research notes
StrategySpec
CandidateArtifact payload
unprivileged BFF read endpoints
```

Broker secrets may be resolved only by:

```text
authorized runtime manager
secret manager
Lean runtime bootstrap under approved manifest
```

### 8.2 Current Gap

Lean standard engine normally supports broker config. Pantheon governance requires that broker config be derived from approved BrokerAccountRegistry / credential_ref_alias, not hand-edited config.

### 8.3 Required Controls

```text
BrokerAccountRegistry
CredentialRefAlias
SecretManagerResolver
Stage-aware credential policy
NoSecretInManifest rule
NoSecretInTelemetry rule
Secret access audit
```

### 8.4 Acceptance Criteria

```text
Launch manifest contains credential_ref_alias, never raw secret.
Lean resolves credential only after verifying deployment_plan_id and runtime_binding_id.
All secret access is audited with runtime_binding_id and capital_pool_id.
```

---

## 9. Boundary 7 — Risk Veto Boundary

### 9.1 Required Boundary

RiskPolicy must be able to veto:

```text
DeploymentPlan creation
RuntimeBinding activation
Lean runtime launch
Live promotion
Canary promotion
Position-affecting actions
```

### 9.2 Current Gap

If risk policy exists only as metadata in DeploymentPlan but Lean does not check stage / risk pass marker, runtime can still start.

### 9.3 Required Controls

```text
RiskPolicyEvaluator
RiskVetoEvent
PoolAdmissibilityCheck
RuntimeLaunchAuthorization
Lean pre-start guard
Runtime health risk-off bridge
```

### 9.4 Acceptance Criteria

```text
When RiskPolicy returns rejected:
  - DeploymentPlan cannot progress
  - RuntimeBinding cannot become active
  - Lean launch fails closed
  - BFF shows blocking reason
```

---

## 10. Boundary 8 — Human Approval Boundary

### 10.1 Required Boundary

Human approval is mandatory for:

```text
candidate → approved
paper → canary
canary → live
live rollback replacement
risk override
kill switch release
persona mutation affecting live behavior
evolution execution for live target
```

### 10.2 Current Gap

ApprovalDecision contract / surfaces exist, but must verify whether downstream actions are blocked without approval.

### 10.3 Required Controls

```text
ApprovalDecisionStore
RequiredApproverPolicy
ApprovalScope
OverridePolicy
MFA for high-risk action
ApprovalAudit
```

### 10.4 Acceptance Criteria

```text
DeploymentPlan target_stage=live requires:
  - ApprovalDecision.decision=approved
  - approver role sufficient
  - rollback_target present
  - risk pass
  - no active kill switch conflict
```

---

## 11. Boundary 9 — Kill Switch / Safe Mode Boundary

### 11.1 Required Boundary

Kill switch must be a formal system component, not a UI button.

It must support:

```text
pool risk-off
pause new entries
liquidate
fallback artifact
environment-wide safe mode
runtime pause / replace
```

### 11.2 Current Status

BFF contract includes kill-switch status surfaces, but a read-only kill-switch
status is not enough. As of P1-KILL-001, runtime-manager has the authoritative
secondary command path: it writes KillSwitchAuditEntry / AuditAction evidence,
mutates RuntimeBinding state for pause / risk_off / liquidate / replace, and
returns a `telemetry_ack`. Missing runtime follow-through is represented as
`telemetry_ack.ack_status = fail_closed`, not as a successful command.

### 11.3 Required Controls

```text
KillSwitchAction
SecondaryControlPath
RuntimePauseCommand
RuntimeLiquidateCommand
LeanKillSwitchBridge
AuditAction
SafeModePolicy
```

### 11.4 Acceptance Criteria

```text
Kill switch activation:
  - does not depend solely on BFF availability
  - writes AuditAction
  - changes CapitalPool state
  - sends command to Lean runtime
  - telemetry ack confirms pause / liquidation / risk-off or fail-closes when missing
```

---

## 12. Boundary 10 — Telemetry / Evolution Boundary

### 12.1 Required Boundary

Telemetry can trigger recommendations / decisions, but live behavior cannot mutate directly from telemetry.

Correct flow:

```text
TelemetryEvent
→ ReconciliationRecord / DriftReport
→ Incident / Postmortem
→ EvolutionDecision proposed
→ reviewed
→ approved
→ executed through governed command
```

### 12.2 禁止行為

```text
Telemetry drift directly retrains live model
Telemetry drift directly mutates persona
Telemetry event directly changes Lean config
Postmortem recommendation directly deploys replacement
```

### 12.3 Current Gap

TelemetryEvent schema is mature, but Lean producer / reconciliation / evolution action dispatcher remain unverified.

### 12.4 Required Controls

```text
DriftPolicy
IncidentClassifier
EvolutionDecisionReviewGate
EvolutionActionDispatcher
CooldownPolicy
NoDirectRuntimeMutation invariant
```

### 12.5 Acceptance Criteria

```text
No EvolutionDecision can execute unless:
  - status = approved
  - evidence_refs present
  - target current state matches expected
  - command path is authorized
  - audit action recorded
```

---

## 13. Boundary 11 — Data / Search Entitlement Boundary

### 13.1 Required Boundary

OpenClaw / front / persona should query external knowledge through a governed Search Gateway.

Search must enforce:

```text
workspace ACL
persona scope
source license
environment
available_time
data sensitivity
```

### 13.2 Current Gap

Research / knowledge surfaces exist, but governed Search Gateway / vector ACL / evidence packaging need verification.

### 13.3 Required Controls

```text
SearchGateway
EvidenceStore
SourceEntitlementPolicy
ACL-aware retrieval
Citation / evidence bundle
SearchAuditLog
```

### 13.4 Acceptance Criteria

```text
Search result must include:
  - source_id
  - evidence_ref
  - license_scope
  - available_time
  - retrieved_by
  - persona_id / workspace_id
```

---

## 14. Cross-Boundary Risk Matrix

| Boundary | Current Risk | Severity | Primary Fix |
|---|---|---|---|
| Research → Execution | Candidate may bypass DeploymentPlan if Lean launch not guarded | Critical | Lean ArtifactGuard |
| OpenClaw → Runtime | LLM tool authority may exceed governance | High | ToolEntitlement + no direct Lean |
| Persona → Capital Pool | Binding not proven at runtime | High | PersonaCapitalBinding enforcement |
| BFF → Canonical Store | read/write contract drift | High | Split read/command API |
| Pantheon → Lean | launch / telemetry contract missing | Critical | LaunchManifest + RuntimeContext |
| Broker Secrets | Lean config may hold secrets directly | High | CredentialRefAlias + secret resolver |
| Risk Veto | risk may not block Lean runtime | Critical | RuntimeLaunchAuthorization |
| Human Approval | approval may not gate downstream action | High | ApprovalDecision guard |
| Kill Switch | secondary runtime-manager command path exists; Lean bridge proof remains stage-specific | High | RuntimeManager KillSwitchBridge + telemetry ack |
| Telemetry → Evolution | decision may not dispatch or may bypass review | High | EvolutionActionDispatcher |
| Search / Data | OpenClaw may retrieve ungated info | Medium-High | SearchGateway |

---

## 15. Required Governance Invariants

```text
INV-GOV-001:
  No CandidateArtifact can create RuntimeBinding directly.

INV-GOV-002:
  DeploymentPlan requires approved artifact and active PersonaCapitalBinding.

INV-GOV-003:
  Lean launch requires valid RuntimeBinding and signed launch manifest.

INV-GOV-004:
  Broker secret cannot appear in front, OpenClaw memory, StrategySpec, Artifact payload, or telemetry.

INV-GOV-005:
  TelemetryEvent must reference RuntimeBinding.

INV-GOV-006:
  RiskPolicy rejection blocks DeploymentPlan and Lean launch.

INV-GOV-007:
  EvolutionDecision cannot mutate live target without approval.

INV-GOV-008:
  KillSwitchAction must have secondary path, audit, and telemetry ack; missing runtime ack is fail-closed.

INV-GOV-009:
  BFF cannot be canonical truth source.

INV-GOV-010:
  OpenClaw cannot directly call Lean runtime or broker API.
```

---

## 16. Required Tests

```text
test_research_artifact_cannot_deploy_without_approval
test_deployment_plan_requires_persona_capital_binding
test_lean_launch_rejects_missing_runtime_binding
test_lean_launch_rejects_unapproved_artifact
test_risk_policy_rejection_blocks_runtime_launch
test_broker_secret_never_in_launch_manifest
test_openclaw_cannot_call_runtime_directly
test_bff_command_requires_idempotency_and_actor
test_kill_switch_writes_audit_and_pauses_runtime
test_telemetry_drift_creates_evolution_proposal_not_direct_mutation
```

---

## 17. Governance Boundary 修補 Roadmap

### P0

```text
ADR-EXEC-001 official execution substrate
RuntimeLaunchManifest
RuntimeBinding required guard
DeploymentPlan → Lean guard
TelemetryEvent producer contract
BFF read/command split
RiskPolicy launch veto
```

### P1

```text
BrokerAccountRegistry
CredentialRefAlias resolver
OpenClaw SearchGateway
PersonaCapabilityResolver enforcement
KillSwitchBridge
EvolutionActionDispatcher
```

### P2

```text
Policy learning mutation gate
Committee / RedTeam governance linkage
Automated postmortem evidence collector
Advanced safe mode
```

---

## 18. 本章結論

Pantheon 的 governance boundary 目前最危險的地方不是「完全沒有治理設計」，而是治理設計已有，但它是否被 `Lean` runtime 實際 enforced 還未被證明。

SA 判斷：

> Pantheon 的控制平面 / BFF / registry / telemetry schema 已經朝正確方向前進，但真正的治理底線必須落在 cross-boundary enforcement：DeploymentPlan 必須 gate Lean launch，RuntimeBinding 必須注入 Lean context，TelemetryEvent 必須從 Lean 帶回 binding identity，RiskPolicy 必須能 veto runtime，OpenClaw / persona / front 必須永遠不能直接越權控制 broker 或 live runtime。

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

# SA-12 — State Machine 差異分析

## 1. 本章目的

本章分析 Pantheon 設計藍圖中的核心 state machines，並檢查目前實作是否存在：

```text
state 混用
transition 缺口
guard 缺口
repo ownership 不明
UI state 與 canonical state 混淆
runtime state 與 deployment state 混淆
```

本章特別關注一個核心問題：

> Pantheon 不是一條 `draft → live` 的簡單流程，而是多條 state machine 並行：artifact、deployment、persona、capital pool、runtime、incident、postmortem、evolution、safe mode 都必須分開。

---

## 2. State Machine 判斷原則

### 2.1 不同 state machine 不可混用

例如：

```text
artifact_state != deployment_stage
deployment_stage != runtime_state
runtime_state != capital_pool_state
persona_lifecycle_state != persona_capital_binding_status
incident_status != evolution_decision_status
```

### 2.2 每個 transition 必須具備

```text
actor
command
precondition
policy / gate
state transition
domain event
audit action
idempotency key
rollback / compensating action if needed
```

### 2.3 UI state 不是 canonical state

前端可以顯示 optimistic / loading / degraded / preview state，但 canonical state 必須由 `pantheon` 對應 store 或 `Lean` runtime event 回寫決定。

---

## 3. State Machine 1 — Strategy / Alpha Lifecycle

### 3.1 Blueprint State

```text
discovered
→ scaffolded
→ replicated
→ approved
→ paper
→ canary
→ live
→ frozen
→ retired
```

藍圖說明：

- `discovered / scaffolded / replicated`：研究成熟度
- `approved / paper / canary / live`：部署成熟度
- `frozen / retired`：運維 / 終止狀態

### 3.2 SA 問題

這條 lifecycle 在藍圖中是高層敘事，但 Target Architecture 已進一步要求：

```text
artifact_state 與 deployment_stage 必須分離
```

因此不應把上面整條直接實作成單一 enum。更正確的拆法是：

```text
StrategyMaturityState:
  discovered
  scaffolded
  replicated

ArtifactState:
  draft
  candidate
  approved
  retired

DeploymentStage:
  none
  paper
  canary
  live
  frozen
```

### 3.3 差異

| 差異 | 說明 |
|---|---|
| 一條 lifecycle 容易被誤實作成單一 enum | 會混淆研究成熟度與 runtime deployment |
| `paper` / `live` 不能作 artifact_state | 它們是 deployment stage |
| `canary` 是 first-class deployment stage | 不能被省略成 live 的子狀態 |
| `frozen` 是 deployment / risk 狀態 | 不應被當作 artifact retired |

### 3.4 Required Transition Guards

```text
discovered → scaffolded:
  requires SourceRecord / EvidenceBundle

scaffolded → replicated:
  requires StrategySpec + ExperimentTask

replicated → candidate:
  requires ExperimentRun completed + metrics + dataset_version

candidate → approved:
  requires ReviewGate pass + ApprovalDecision

approved → paper:
  requires DeploymentPlan + RuntimeBinding + Lean manifest

paper → canary:
  requires paper telemetry + reconciliation pass + approval

canary → live:
  requires canary telemetry + risk pass + rollback target + human approval

live → frozen:
  requires drift / incident / risk-off / operator command

frozen → live:
  requires revalidation + approval

any → retired:
  requires retirement decision + audit
```

### 3.5 Status

```text
High-risk if currently implemented as single promotion_state.
```

---

## 4. State Machine 2 — Artifact State

### 4.1 Canonical State

根據 Target Architecture：

```text
draft
candidate
approved
retired
```

### 4.2 定義

| State | 定義 |
|---|---|
| draft | 研究 / 建構中，未進 governance |
| candidate | 已完成基本 replication / packaging，可送 review |
| approved | 已通過 governance，可用於 deployment planning |
| retired | 不再可 promote 或 deploy |

### 4.3 允許 transition

```text
draft → candidate
candidate → approved
candidate → retired
approved → retired
```

### 4.4 禁止 transition

```text
draft → approved
draft → live
candidate → live
approved → live  # artifact_state 不可變 live；live 是 deployment_stage
retired → approved without new version
```

### 4.5 Gap

| Gap | 風險 |
|---|---|
| legacy promotion state 若含 paper/live | artifact state polluted |
| approved artifact 若直接觸發 Lean | 跳過 DeploymentPlan |
| retired artifact 若仍被 runtime 讀取 | safety risk |
| artifact version / checksum 不明 | rollback / replay 弱 |

### 4.6 Required Tests

```text
test_candidate_requires_experiment_lineage
test_approved_requires_approval_decision
test_artifact_state_cannot_be_paper_or_live
test_retired_artifact_cannot_create_deployment_plan
```

---

## 5. State Machine 3 — Deployment Stage

### 5.1 Canonical State

```text
none
paper
canary
live
frozen
```

### 5.2 定義

| Stage | 定義 |
|---|---|
| none | artifact 未綁定 runtime |
| paper | 紙上 / 模擬交易 |
| canary | 小資金 / 受限風險 live-like rollout |
| live | 正式 live |
| frozen | 已凍結，不可新增風險 |

### 5.3 允許 transition

```text
none → paper
paper → canary
canary → live
paper → frozen
canary → frozen
live → frozen
frozen → paper
frozen → canary
frozen → live  # only after revalidation / approval
paper → none
canary → none
live → none  # only via retire / terminate path
```

### 5.4 Guard

```text
none → paper:
  approved artifact
  valid DeploymentPlan
  valid PersonaCapitalBinding
  valid CapitalPool
  Lean launch manifest generated

paper → canary:
  paper telemetry coverage
  paper reconciliation pass
  risk policy pass
  reduced budget

canary → live:
  canary telemetry coverage
  rollback target
  human approval
  broker capability check
  live risk policy pass

any → frozen:
  risk breach OR incident OR operator command OR evolution decision

frozen → live:
  revalidation + approval + rollback target
```

### 5.5 Gap

目前最大 gap 是：

```text
DeploymentStage 是否能真正寫入 RuntimeBinding，
並由 Lean runtime 消費與回吐 telemetry。
```

若 Lean 不知道 `deployment_stage`，pantheon 的 telemetry schema 雖然要求該欄位，但 runtime 無法證明。

---

## 6. State Machine 4 — Persona Lifecycle

### 6.1 Blueprint State

```text
draft
→ research_only
→ consultable
→ paper_owner
→ live_owner
→ frozen
→ retired
```

### 6.2 定義

| State | 定義 |
|---|---|
| draft | 尚未可操作 |
| research_only | 可做研究，不可 consult / deploy |
| consultable | 可被會診引用 |
| paper_owner | 可管理 paper-bound strategy |
| live_owner | 可管理 live-bound strategy，但仍需 capital binding |
| frozen | 暫停能力 |
| retired | 退役 |

### 6.3 關鍵 guard

```text
research_only → consultable:
  requires capability snapshot + consult policy

consultable → paper_owner:
  requires route policy + capital pool paper binding

paper_owner → live_owner:
  requires live binding + human approval + risk policy

any → frozen:
  incident / policy breach / admin command

frozen → previous:
  requires review
```

### 6.4 Gap

| Gap | 說明 |
|---|---|
| persona lifecycle 是否只是 UI 欄位不明 | 需要 canonical store |
| capability resolver 是否參與 tool call 不明 | shared skill 不等於 shared authority |
| live_owner 是否真的需要 PersonaCapitalBinding 不明 | 權限可能繞過 |
| OpenClaw tool call 是否檢查 persona state 不明 | agent boundary risk |

---

## 7. State Machine 5 — Persona-Capital Binding State

### 7.1 建議 State

```text
proposed
approved
active
suspended
expired
revoked
```

### 7.2 Binding Role

```text
research_observer
paper_owner
canary_sponsor
live_owner
risk_reviewer
operator
```

### 7.3 Guard

```text
proposed → approved:
  requires approver + risk policy

approved → active:
  requires effective_from <= now and pool status compatible

active → suspended:
  risk_off / incident / admin

active → revoked:
  governance decision

active → expired:
  effective_to passed
```

### 7.4 Gap

藍圖要求 persona-capital binding，但目前需驗證：

```text
deployment plan 是否檢查 binding？
Lean runtime 是否帶 persona_capital_binding_id？
telemetry 是否回填 persona_capital_binding_id？
```

TelemetryEvent schema 已要求 `persona_capital_binding_id`，這是正確方向；但 Lean producer 是否提供仍未驗證。

---

## 8. State Machine 6 — Capital Pool Lifecycle

### 8.1 Blueprint State

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

### 8.2 Guard

```text
provisioned → paper_bound:
  requires paper binding + paper deployment

paper_bound → canary_bound:
  requires canary plan

canary_bound → live_bound:
  requires live plan + risk pass

live_bound → risk_off:
  risk breach / incident / kill switch

risk_off → paused:
  operator / policy action

paused → liquidating:
  liquidation command

liquidating → archived:
  positions flat + audit complete
```

### 8.3 Gap

| Gap | 說明 |
|---|---|
| pool state 是否驅動 Lean runtime 未驗證 | risk_off 必須能 pause / liquidate Lean |
| broker account registry 是否與 pool 綁定不明 | secrets / account mapping risk |
| live_bound 是否唯一 active runtime 不明 | duplicated live runtime risk |
| pool state telemetry 是否存在不明 | runtime health 需要 pool state |

---

## 9. State Machine 7 — Runtime Lifecycle

### 9.1 Blueprint State

```text
created
→ loading
→ active
→ degraded
→ paused
→ replacing
→ active
→ terminated
```

### 9.2 Lean 對應

Lean 標準 engine 有 AlgorithmStatus / engine lifecycle，但 Pantheon runtime lifecycle 需要更多治理欄位：

```text
runtime_binding_id
deployment_plan_id
capital_pool_id
deployment_stage
artifact_id
broker_account_ref
heartbeat
health
pending_action
```

### 9.3 Guard

```text
created → loading:
  requires valid launch manifest

loading → active:
  artifact loaded + broker/data feed initialized

active → degraded:
  heartbeat lag / broker disconnect / telemetry lag

degraded → paused:
  operator / risk policy

paused → replacing:
  rollback / replace command

replacing → active:
  replacement binding started

active → terminated:
  retire / shutdown

any → terminated:
  kill switch / fatal error
```

### 9.4 Gap

最大 gap：

```text
Lean 是否能把 AlgorithmStatus 映射成 Pantheon RuntimeStatus？
Lean 是否把 runtime_binding_id 注入所有 lifecycle event？
```

若沒有，BFF runtime board 只能顯示 generic engine state，不能做 governance-grade trace。

---

## 10. State Machine 8 — Incident Lifecycle

### 10.1 Blueprint State

```text
alert_open
→ alert_ack
→ incident_triaged
→ incident_active
→ mitigated
→ postmortem_pending
→ postmortem_published
```

或更細：

```text
new
→ triaged
→ active
→ mitigated
→ postmortem_pending
→ closed
```

### 10.2 Guard

```text
alert_open → alert_ack:
  operator ack

alert_ack → incident_triaged:
  classify severity / category / owner

incident_triaged → incident_active:
  confirm impact

incident_active → mitigated:
  pause / rollback / freeze / manual mitigation

mitigated → postmortem_pending:
  close immediate risk

postmortem_pending → postmortem_published:
  evidence collected + review done
```

### 10.3 Gap

| Gap | 說明 |
|---|---|
| telemetry breach → incident 未驗證 | incident 可能手動建立 |
| Lean runtime event → alert 未驗證 | runtime errors 需進 alert |
| IncidentCase 是否連 RuntimeBinding 不明 | 事故需要 precise target |
| mitigation action 是否能作用到 Lean 不明 | incident 不能只記錄 |

---

## 11. State Machine 9 — Postmortem Lifecycle

### 11.1 建議 State

```text
draft
→ evidence_collecting
→ review
→ published
→ action_tracked
→ archived
```

### 11.2 必要關聯

```text
incident_id
runtime_binding_id
deployment_plan_id
artifact_id
capital_pool_id
telemetry_event_ids[]
reconciliation_record_ids[]
actor_actions[]
root_cause
corrective_actions[]
```

### 11.3 Gap

Postmortem 不是普通 memo。若 evidence 沒有自動從 telemetry / lineage / audit 收集，它就只是人工文件，無法驅動 evolution。

---

## 12. State Machine 10 — Evolution Decision Lifecycle

### 12.1 Blueprint State

```text
proposed
→ reviewed
→ approved
→ executed
→ superseded
```

### 12.2 Decision Types

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

### 12.3 Guard

```text
proposed → reviewed:
  evidence_refs complete

reviewed → approved:
  required approvers

approved → executed:
  action dispatcher exists and target plane accepts command

executed → superseded:
  newer decision replaces it
```

### 12.4 Gap

常見風險：

```text
EvolutionDecision 可建立，但 execute action 仍是 local record / stub。
```

若無法真正 dispatch 到 research / governance / Lean runtime / persona plane，它不是 closed loop。

---

## 13. State Machine 11 — Safe Mode / Kill Switch Lifecycle

### 13.1 Blueprint State

```text
normal
→ guarded
→ risk_off
→ paused
→ recovery_testing
→ normal
```

### 13.2 Guard

```text
normal → guarded:
  early warning / degraded telemetry

guarded → risk_off:
  risk threshold breach / incident

risk_off → paused:
  operator or automatic safe mode

paused → recovery_testing:
  mitigation complete + revalidation

recovery_testing → normal:
  approval + telemetry stable
```

### 13.3 Gap

| Gap | 說明 |
|---|---|
| kill switch 是否有 secondary path 不明 | 不應只經 BFF |
| kill switch 是否能直接作用到 Lean runtime 不明 | 必須能 pause / liquidate / freeze |
| kill switch action 是否進 audit 不明 | 高風險 action 需 trace |
| safe mode 是否影響 OpenClaw / tool execution 不明 | risk_off 時應限制 new actions |

---

## 14. Cross-State Consistency Rules

### 14.1 Artifact vs Deployment

```text
DeploymentStage != none requires artifact_state = approved.
artifact_state = retired implies no active deployment_stage.
```

### 14.2 Deployment vs Runtime

```text
DeploymentPlan.stage = paper/canary/live requires RuntimeBinding.
RuntimeBinding.deployment_stage must equal DeploymentPlan.target_stage.
```

### 14.3 Runtime vs Telemetry

```text
TelemetryEvent.binding_id must reference active or recently retired RuntimeBinding.
TelemetryEvent.deployment_stage must match binding.deployment_stage.
```

### 14.4 Persona vs Capital

```text
DeploymentPlan requires active PersonaCapitalBinding.
Persona with no binding cannot create live deployment.
```

### 14.5 Capital Pool vs Runtime

```text
CapitalPool.risk_off implies runtime cannot open new risk.
CapitalPool.paused implies Lean runtime must pause trading actions.
```

---

## 15. State Machine Gap Matrix

| State Machine | Status | Highest Risk Gap | Required Fix |
|---|---|---|---|
| Strategy / Alpha | Partial | research maturity mixed with deployment maturity | Split state model |
| Artifact | Partial | paper/live as artifact state | Migration + invariant tests |
| Deployment | Partial | DeploymentPlan → Lean RuntimeBinding unverified | RuntimeBinding + Lean manifest |
| Persona | Partial | capability resolver not enforced | Policy evaluator |
| Persona-Capital Binding | Partial | telemetry requires id but Lean producer unverified | Runtime context injection |
| Capital Pool | Partial | risk_off not proven to control Lean | PoolState → Lean action bridge |
| Runtime | Generic Lean exists | Pantheon RuntimeStatus not mapped | Runtime lifecycle adapter |
| Incident | Partial | telemetry breach → incident unverified | Incident trigger service |
| Postmortem | Partial | evidence collection not automated | Evidence collector |
| Evolution | Partial | execute action likely incomplete | Evolution dispatcher |
| Safe Mode | Partial | secondary kill switch / Lean bridge unverified | KillSwitchBridge |

---

## 16. Required Invariant Tests

```text
test_artifact_state_does_not_include_paper_or_live
test_deployment_stage_requires_approved_artifact
test_runtime_binding_requires_deployment_plan
test_telemetry_event_requires_runtime_binding
test_telemetry_stage_matches_runtime_binding_stage
test_live_deployment_requires_persona_capital_binding
test_live_deployment_requires_risk_policy_pass
test_live_deployment_requires_rollback_target
test_retired_artifact_cannot_have_active_runtime
test_capital_pool_risk_off_blocks_new_orders
test_kill_switch_requires_audit_action
test_evolution_execution_requires_approved_decision
```

---

## 17. 本章結論

Pantheon 的 state machine 風險集中在三點：

```text
1. artifact_state / deployment_stage / runtime_state 容易混用。
2. Lean 標準 engine state 尚未被證明映射成 Pantheon RuntimeLifecycle。
3. telemetry / incident / evolution 的 state transition 需要由 canonical event 驅動，而不是 UI 或人工 memo 驅動。
```

SA 判斷：

> 下一步的重點不是新增更多 state name，而是建立 cross-state invariants 與 transition guards。只要 DeploymentPlan、RuntimeBinding、TelemetryEvent、CapitalPool、PersonaCapitalBinding 之間的狀態一致性沒有被測試鎖住，Pantheon 的 operating loop 就不能視為治理級完成。

# SD-05 — Consultation / Red-Team Plane / 多人格會診與反方審查設計

版本：v0.1 Codex-ready draft
適用範圍：Consultation Plane、Committee Orchestrator、Red-Team Orchestrator、Consult Memo Store、Consult Audit Log
前置依賴：SD-00 Architecture Invariants、SD-01 Domain Model & Registry Backbone、SD-02 Persona Governance、SD-03 Source / Evidence、SD-04 Research Orchestrator

---

## 1. Purpose

本文件定義 Pantheon 的 Consultation / Red-Team Plane。此 plane 的目的不是讓 persona 彼此聊天，而是形成可治理、可引用、可進入 promotion gate 的審議輸出。

Consultation Plane 主要處理：

```text
ConsultRequest
→ participant selection
→ committee / red-team execution
→ ConsultMemo
→ evidence-linked recommendation
→ audit log
→ review gate input
```

所有 consult / red-team 結果都必須可追溯，並且可被 Governance & Promotion Plane 作為 hard gate 或 soft evidence 使用。

---

## 2. Repo ownership

| Repo | Ownership |
|---|---|
| `pantheon` | Primary owner：consult request manager、committee/red-team orchestrator、memo store、audit log、policy evaluation。 |
| `front-ai-trading-system` | UI consumer：Consultation Workbench、Red-Team Queue、Memo Viewer、Review Inputs。 |
| OpenClaw integration | May execute persona-mediated consult sessions through governed tools; cannot bypass Pantheon consult policy。 |
| `pantheon-lean` | No ownership；may appear as evidence/runtime context in red-team review only。 |

---

## 3. Module paths

### `pantheon`

```text
services/consultation/
  __init__.py
  models.py
  commands.py
  queries.py
  events.py
  policies.py
  request_manager.py
  participant_resolver.py
  committee_orchestrator.py
  redteam_orchestrator.py
  memo_store.py
  audit.py
  evidence_linker.py
  api.py
  tests/

services/consultation/templates/
  strategy_review.md
  data_leakage_review.md
  execution_risk_review.md
  capital_pool_review.md
  incident_review.md

docs/contracts/consult_request.schema.json
docs/contracts/consult_memo.schema.json
docs/contracts/redteam_report.schema.json
docs/sd/05_consultation_redteam.md
docs/codex/SD-05_task_packets.md
```

### `front-ai-trading-system`

```text
src/pages/consultation/*
src/pages/governance/ReviewInputsPanel.tsx
src/types/consultation.ts
src/lib/consultationClient.ts
```

---

## 4. Domain model

### 4.1 `ConsultRequest`

```yaml
ConsultRequest:
  request_id: string
  request_type: enum[strategy_review, redteam, data_leakage, execution_risk, capital_pool, incident, persona_policy]
  requested_by: actor_ref
  from_persona_id: string | null
  target_type: enum[strategy, artifact, experiment_run, deployment_plan, runtime_binding, incident, persona]
  target_id: string
  context_refs: string[]
  evidence_refs: string[]
  priority: enum[low, normal, high, urgent]
  status: enum[draft, submitted, assigned, in_progress, memo_pending, published, cancelled, failed]
  policy_id: string
  trace_id: string
  created_at: datetime
```

### 4.2 `ConsultParticipant`

```yaml
ConsultParticipant:
  participant_id: string
  request_id: string
  participant_type: enum[persona, human_reviewer, committee, external_tool]
  participant_ref: string
  role: enum[primary_reviewer, red_team, risk_reviewer, data_reviewer, execution_reviewer, observer]
  status: enum[pending, accepted, declined, completed]
  assigned_at: datetime
```

### 4.3 `CommitteePolicy`

```yaml
CommitteePolicy:
  policy_id: string
  target_type: string
  required_roles: string[]
  min_participants: integer
  conflict_rules: object
  required_memo_types: string[]
  allow_self_review: boolean
  escalation_rules: object
```

### 4.4 `RedTeamScenario`

```yaml
RedTeamScenario:
  scenario_id: string
  request_id: string
  scenario_type: enum[data_leakage, overfit, regime_failure, execution_slippage, liquidity, tail_risk, security_boundary, capital_pool_mismatch]
  prompt_template_id: string
  required_evidence_types: string[]
  severity_floor: enum[info, low, medium, high, critical]
  status: enum[pending, running, completed, failed]
```

### 4.5 `ConsultMemo`

```yaml
ConsultMemo:
  memo_id: string
  request_id: string
  memo_type: enum[committee_summary, redteam_report, risk_review, data_review, execution_review, dissent]
  author_type: enum[persona, human, committee, system]
  author_ref: string
  target_type: string
  target_id: string
  summary: string
  findings:
    - severity: enum[info, low, medium, high, critical]
      category: string
      claim: string
      evidence_refs: string[]
      recommendation: string
  recommendation: enum[approve, approve_with_conditions, reject, request_more_research, freeze, rollback, escalate]
  confidence: number
  status: enum[draft, submitted, published, superseded]
  trace_id: string
  created_at: datetime
```

### 4.6 `ConsultAuditEvent`

```yaml
ConsultAuditEvent:
  audit_id: string
  request_id: string
  actor_ref: actor_ref
  action: string
  before_state: string | null
  after_state: string | null
  payload_hash: string
  timestamp: datetime
  trace_id: string
```

---

## 5. Commands

| Command | Input | Output | Notes |
|---|---|---|---|
| `CreateConsultRequest` | request payload | request_id | Draft or submitted。 |
| `SubmitConsultRequest` | request_id | status=submitted | Validates target and context refs。 |
| `AssignConsultParticipants` | request_id | participant list | Uses CommitteePolicy。 |
| `StartConsultation` | request_id | status=in_progress | Emits event。 |
| `CreateRedTeamScenario` | request_id + scenario type | scenario_id | May be policy-generated。 |
| `SubmitConsultMemo` | memo payload | memo_id | Draft/submitted。 |
| `PublishConsultMemo` | memo_id | status=published | Locks memo for review gate use。 |
| `CancelConsultRequest` | request_id + reason | status=cancelled | Requires role check。 |
| `AttachMemoToReview` | memo_id + review target | link_id | Used by SD-07。 |

---

## 6. Queries

| Query | Output |
|---|---|
| `GetConsultRequest` | request detail + participants + memos |
| `ListConsultRequests` | filtered queue |
| `GetConsultMemo` | memo detail |
| `ListMemosForTarget` | all memos for strategy/artifact/deployment |
| `GetRedTeamScenarios` | scenarios for request |
| `GetConsultAuditLog` | immutable audit trail |
| `GetRequiredConsultInputs` | required memos by policy for a review target |

---

## 7. Events

```yaml
ConsultRequestCreated:
  request_id: string
  target_type: string
  target_id: string
  requested_by: actor_ref

ConsultRequestSubmitted:
  request_id: string
  policy_id: string

ConsultParticipantsAssigned:
  request_id: string
  participants: string[]

ConsultationStarted:
  request_id: string

RedTeamScenarioCompleted:
  scenario_id: string
  request_id: string
  severity: string

ConsultMemoSubmitted:
  memo_id: string
  request_id: string
  recommendation: string

ConsultMemoPublished:
  memo_id: string
  request_id: string
  target_type: string
  target_id: string

ConsultRequestCompleted:
  request_id: string
  memo_ids: string[]
```

---

## 8. State machines

### 8.1 ConsultRequest state

```text
draft → submitted → assigned → in_progress → memo_pending → published
```

Alternative paths:

```text
draft / submitted / assigned / in_progress → cancelled
submitted / assigned / in_progress → failed
published → superseded  # only via new request version
```

### 8.2 ConsultMemo state

```text
draft → submitted → published → superseded
```

### 8.3 RedTeamScenario state

```text
pending → running → completed
pending / running → failed
```

---

## 9. Hard invariants

1. A required consult memo must be `published` before it can satisfy a promotion review gate.
2. Self-review is disallowed unless `CommitteePolicy.allow_self_review=true` and the request is not live-affecting.
3. Red-team memos must include at least one evidence reference or explicitly declare `evidence_unavailable_reason`.
4. Consult outputs cannot directly approve deployment; they only feed ApprovalDecision or gate evaluation.
5. Every consult request must have target_type and target_id.
6. Every published memo must be immutable except supersession.
7. Every participant assignment and memo publication must be audit-logged.
8. LLM/persona-generated memo must be marked with `author_type=persona` or `system`, never disguised as human.
9. Consult requests that touch live/capital/execution targets require RBAC and may require MFA.
10. Red-team scenarios cannot fetch arbitrary external data except through SD-03 governed evidence search.

---

## 10. Policy hooks

| Policy | Purpose |
|---|---|
| `consult_trigger_policy` | Determines when a strategy/artifact/deployment must request consultation。 |
| `committee_policy` | Selects required reviewers/personas/roles。 |
| `redteam_policy` | Generates red-team scenarios by target type。 |
| `conflict_of_interest_policy` | Prevents self-review and owner-only approval。 |
| `memo_requirement_policy` | Determines required memo types for promotion gate。 |
| `escalation_policy` | Escalates high severity finding to governance or incident。 |
| `evidence_requirement_policy` | Requires evidence bundle types for memo categories。 |

---

## 11. Storage model

```text
consult_requests
consult_participants
committee_policies
redteam_scenarios
consult_memos
consult_memo_findings
consult_target_links
consult_audit_events
```

Published memo body should be content-addressed:

```text
object://pantheon-consult-memos/{memo_id}/{content_hash}.md
```

---

## 12. API endpoints

```text
POST   /api/consult/requests
GET    /api/consult/requests
GET    /api/consult/requests/{request_id}
POST   /api/consult/requests/{request_id}/submit
POST   /api/consult/requests/{request_id}/assign
POST   /api/consult/requests/{request_id}/start
POST   /api/consult/requests/{request_id}/cancel
GET    /api/consult/requests/{request_id}/participants
GET    /api/consult/requests/{request_id}/memos
POST   /api/consult/requests/{request_id}/redteam-scenarios
GET    /api/consult/redteam-scenarios/{scenario_id}
POST   /api/consult/memos
GET    /api/consult/memos/{memo_id}
POST   /api/consult/memos/{memo_id}/publish
GET    /api/consult/targets/{target_type}/{target_id}/memos
GET    /api/consult/targets/{target_type}/{target_id}/requirements
```

---

## 13. Integration points

| Integration | Direction | Contract |
|---|---|---|
| SD-02 Persona Governance | read | participant resolution, capability checks。 |
| SD-03 Evidence | read | evidence bundles for memos。 |
| SD-04 Research | read | experiment metrics and run context。 |
| SD-06 Capital Pool | read | capital pool context for risk review。 |
| SD-07 Promotion | write/read | published memos satisfy gates。 |
| BFF / Console | read/command | workbench queue, memo viewer。 |

---

## 14. Tests

### Unit tests

- consult request validates target references.
- participant resolver applies required roles.
- self-review is rejected by default.
- published memo cannot be edited.
- red-team scenario requires evidence or explicit unavailable reason.

### Integration tests

- CandidateArtifact review policy requires red-team memo; gate fails before memo, passes after published memo.
- Persona consult request resolves participants using consult policy.
- High-severity red-team finding emits escalation event.

### Security tests

- persona cannot publish memo as human reviewer.
- unauthorized actor cannot cancel live-affecting request.
- consult process cannot call raw search without governed evidence gateway.

---

## 15. Definition of Done

1. Consultation service has request, participant, red-team, memo, and audit models.
2. Published memos are immutable and evidence-linked.
3. Required memo checks are available to Promotion Plane.
4. Committee/red-team policy is configurable.
5. UI can show request queue and memo details.
6. Tests cover self-review, memo immutability, and gate requirement integration.

---

## 16. Codex task packets

### PTH-SD05-001 — Implement consultation models and repository

```text
Repo: ajoe734/pantheon
Target paths:
  services/consultation/models.py
  services/consultation/repository.py
  docs/contracts/consult_request.schema.json
  docs/contracts/consult_memo.schema.json
Goal:
  Define ConsultRequest, ConsultParticipant, CommitteePolicy, RedTeamScenario, ConsultMemo, ConsultAuditEvent.
Acceptance tests:
  - invalid target_type is rejected
  - memo without target_id is rejected
  - published memo is immutable
```

### PTH-SD05-002 — Implement participant resolver and policy checks

```text
Repo: ajoe734/pantheon
Target paths:
  services/consultation/participant_resolver.py
  services/consultation/policies.py
  services/consultation/tests/test_participant_resolver.py
Goal:
  Assign participants from CommitteePolicy and reject conflicts.
Acceptance tests:
  - required roles are assigned
  - self-review rejected by default
  - manual override requires allowed role
```

### PTH-SD05-003 — Implement memo store and publication flow

```text
Repo: ajoe734/pantheon
Target paths:
  services/consultation/memo_store.py
  services/consultation/evidence_linker.py
  services/consultation/tests/test_memo_store.py
Goal:
  Store draft/submitted/published memos with evidence refs and content hash.
Acceptance tests:
  - published memo cannot be modified
  - memo must declare recommendation
  - evidence refs are preserved
```

### PTH-SD05-004 — Implement promotion gate requirement query

```text
Repo: ajoe734/pantheon
Target paths:
  services/consultation/queries.py
  services/consultation/api.py
  services/consultation/tests/test_gate_requirements.py
Goal:
  Expose required/published consult memo status for review targets.
Acceptance tests:
  - target with missing memo returns unsatisfied requirement
  - target with published memo returns satisfied requirement
  - superseded memo does not satisfy requirement
```

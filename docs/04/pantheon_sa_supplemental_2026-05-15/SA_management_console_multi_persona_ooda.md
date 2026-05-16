# Pantheon Management Console & Multi-Persona OODA — Supplemental SA

Date: 2026-05-15
Audience: Pantheon / execute-plans system development team
Scope: **Management Console only** for frontend product surface; Agora intentionally excluded except where it contributes backend evidence or persona artifacts.
Baseline repositories: `ajoe734/pantheon@backend-dev-publish-20260429`, `ajoe734/execute-plans@main`
Document tier: L2/L3 supplemental System Analysis
Conflict rule: This document does not override L1 canonical policy. If conflict exists, follow `TARGET_ARCHITECTURE.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PAPER_CANARY_LIVE_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, and the BFF contracts.

---

## 0. Executive Summary

The current implementation baseline has materially advanced beyond an early mock-shell state.

The latest repository state indicates:

1. **Management Console information architecture and route shell are substantially complete.**
2. **BFF Consolidation has reached final acceptance** through `BFF-CONSOL-027`, with `BFF-CONSOL-001..026` evidence summarized, authenticated read smoke passing, hosted browser BFF/SSE probe passing, strict fallback regression evidence present, command receipt evidence present, and seed/fallback post-state recorded.
3. **Pantheon canonical architecture is mature**: research, governance, deployment, runtime, telemetry, incident, evolution, persona, binding, and multi-persona aggregation semantics are defined.
4. **The system is not yet a fully production-live self-evolving trading platform**: broker production live remains fail-closed, capital binding live activation remains fail-closed, canary still requires risk-owner and operator gate, Qlib activation and Shioaji sandbox evidence are active track items.

Therefore the correct development framing is:

> Management Console + BFF live/strict integration has passed a delivery gate.
> Pantheon as a multi-persona trading system has strong OODA/governance architecture and service baselines.
> The next work is to convert this accepted management surface into a verified **paper-loop / canary-readiness / governed OODA operating system**, without prematurely opening live broker or capital-binding side effects.

---

## 1. Definitions

### 1.1 Management Console

For this document, **Management Console** means the `/management/*` surface in `execute-plans`, including:

- control room
- loops
- sentinel
- interventions
- strategy registry
- persona registry
- capital pools
- ranking
- rebalance
- evolution
- experiments / research
- governance
- knowledge
- postmortems
- lineage
- deployments
- runtimes
- jobs
- alerts
- incidents
- approvals
- tools / MCP / skills / channels
- studios
- audit
- settings

**Agora is excluded** from the frontend scope of this SA. Backend artifacts used by both Management and Agora, such as persona sessions, consultation, trainer records, source/search, and decision journals, may still be referenced as domain dependencies.

### 1.2 Governed Closed Loop

A Management closed loop is considered valid only when the following chain can be executed or truthfully displayed:

```text
Observe -> Orient -> Decide -> Act -> Learn/Evolve
```

Mapped to Pantheon:

```text
Research / telemetry / feedback
-> strategy / persona / allocation / risk interpretation
-> ApprovalDecision / DeploymentPlan / EvolutionDecision
-> RuntimeBinding / paper or canary action / rollback / safe-mode
-> telemetry / incident / postmortem / evolution review
```

### 1.3 Completion Levels

This document distinguishes four completion levels:

| Level | Meaning | Example |
|---|---|---|
| UI Complete | Page and UI route exist | `/management/runtimes` renders |
| Integration Complete | BFF route and frontend live transport verified | `/bff/runtimes` read smoke passes |
| Operational Complete | User can perform complete workflow with evidence | deployment review -> command receipt -> audit -> runtime state |
| OODA Complete | Resulting evidence drives a governed improvement loop | telemetry -> evolution decision -> approved retrain/redeploy |

A feature is not considered OODA-complete merely because its page exists.

### 1.4 Fail-Closed

Fail-closed means a path is intentionally unavailable unless an explicit activation gate is passed.

Current fail-closed categories:

- broker production live
- capital-binding live activation
- OpenClaw direct broker / paper / canary / live execution kernel role
- autonomous live strategy mutation
- production RL / TRL / FinRL / RLlib learning adapters
- canary without risk-owner + operator approval

---

## 2. Current State Assessment

### 2.1 Completed / Accepted Baseline

The following are treated as current accepted baseline:

1. Management Console UI route shell exists.
2. BFF Consolidation final acceptance exists.
3. BFF route manifest / frontend route comparison exists.
4. Authenticated BFF read smoke passes in final evidence.
5. Hosted browser BFF/SSE probe passes in final evidence.
6. `/bff/me` startup is live in final evidence.
7. Runtime strict fallback is validated in final evidence.
8. Command envelope / receipt / idempotency / dual-write evidence exists.
9. Seed/fallback taxonomy and strict handling are documented.
10. L1 architecture defines the canonical research-to-runtime flow.

### 2.2 Still Not Complete

The following are **not** completed by BFF acceptance:

1. Production live broker enablement.
2. Capital binding live activation.
3. Canary deployment without human gate.
4. Production autonomous OODA.
5. Production-grade multi-persona portfolio synthesis service.
6. Production Qlib / TRL / RL / imitation loop.
7. Fully automated telemetry-to-live-mutation path.
8. BFF HA/LB production topology.
9. Lovable build-time strict env publish, where recorded as non-blocking follow-up.

### 2.3 Practical Interpretation

The system is now past "frontend mock shell only."

The system is not yet past "governed activation / proof / evidence gate."

The next development work should focus on **paper-loop proof**, **canary-readiness evidence**, and **OODA auditability**, not on indiscriminately opening live trading paths.

---

## 3. Stakeholders and Actors

| Actor | Description | Needs |
|---|---|---|
| Operator | Uses Management Console to monitor and act | clear state, no mock confusion, safe commands |
| Risk Owner | Approves canary/live and risk-off decisions | evidence, kill switch state, rollback readiness |
| Reviewer / Approver | Reviews artifacts, deployments, evolution decisions | approval queue, rationale, lineage, command receipts |
| Portfolio / Capital Manager | Manages capital pools and binding scope | admissibility, exposure, pool status |
| Persona Manager | Manages persona registry, policies, and health | route policy, capability snapshot, performance |
| Quant / Research Engineer | Produces StrategySpec, artifacts, Qlib evidence | experiment lineage, admission packet |
| Runtime Operator | Monitors RuntimeBinding and execution health | deployment state, telemetry, rollback/safe-mode |
| System Developer | Implements service/BFF/frontend integrations | contracts, route maps, acceptance criteria |
| Auditor | Reviews decision trace and incident history | immutable evidence chain, audit log, lineage |

---

## 4. Domain Boundaries

### 4.1 Plane Ownership

| Plane | Owns |
|---|---|
| Research and Learning | source ingest, StrategySpec, experiments, replication, evaluator/optimizer inputs |
| Registry and Governance | ArtifactRecord, ApprovalDecision, DeploymentPlan gate semantics |
| Capital / Runtime / Execution | CapitalPool, PersonaCapitalBinding, RuntimeBinding, LEAN load, broker behavior |
| Telemetry / Incident / Evolution | TelemetryEvent, IncidentCase, Postmortem, EvolutionDecision |
| Persona Plane | Registry Persona, Session Persona, CapabilitySnapshot, trainer/consult sessions |
| BFF | read facade, command admission facade, not canonical truth |
| execute-plans Management | operator UX, live BFF client, strict transport, visual state |

### 4.2 Write Ownership Rules

| Object | Write owner | Non-owner rule |
|---|---|---|
| ApprovalDecision | governance service | BFF only admits command |
| DeploymentPlan / DeploymentSaga | deployment service | runtime-manager reads, does not own plan |
| CapitalPool / PersonaCapitalBinding | capital service | binding is not runtime deployment |
| RuntimeBinding | runtime-manager | governance/capital/BFF cannot write directly |
| TelemetryEvent | telemetry ingest service | producers do not directly write canonical telemetry tables |
| EvolutionDecision | evolution service | runtime follow-through is dispatched to runtime-manager |
| Persona | persona service / persona plane | trainer does not directly mutate runtime |
| CapabilitySnapshot | persona plane at session start | immutable after session start |

---

## 5. Management Flow Inventory

### 5.1 Control Room / Closed-Loop OS

**Purpose:** Provide operator-level system overview.

Flow:

```text
BFF read surfaces -> control room summary -> operator focus -> loop detail / intervention / runtime / incident
```

Required surfaces:

- runtime state
- loop runs
- sentinel findings
- interventions
- approvals
- alerts
- safe-mode / fail-closed gate state
- BFF/source state

Completion target:

- all displayed cards must declare source freshness and degradation status
- all actionable cards must route to canonical command or read detail
- no card may hide seed/fallback state in strict mode

### 5.2 Strategy Lifecycle

Flow:

```text
Strategy registry -> Strategy detail -> StrategySpec -> research experiment
-> artifact -> approval -> deployment plan -> runtime binding -> telemetry -> evolution
```

Required:

- strategy list/detail
- strategy specs
- research experiment links
- artifact links
- approval links
- deployment/runtime links
- lineage/audit
- high-risk actions through command envelope

### 5.3 Persona Management

Flow:

```text
Persona registry -> route policy / consult policy / memory / eval
-> capability snapshot -> session / trainer / consult history
-> capital binding admissibility -> paper/live sponsorship eligibility
```

Required:

- persona list/detail
- route policy
- capabilities
- evaluation summary
- memory governance
- session summary
- binding summary
- persona health
- restrict / suspend / retire command path

### 5.4 Capital and Binding

Flow:

```text
CapitalPool -> PersonaCapitalBinding request -> governance validation
-> capital service activation -> admissibility read
-> deployment eligibility -> runtime binding reference
```

Required:

- capital pool list/detail
- binding list/detail
- admissibility query
- live owner query
- binding status lifecycle
- audit
- no direct deployment on binding activation

### 5.5 Ranking / Rebalance / Optimization

Flow:

```text
Ranking formula -> metric freeze -> ranking calculation -> simulation
-> rebalance review -> approval -> scheduled apply -> applied rebalance
-> telemetry / audit / rollback if needed
```

Required:

- ranking dashboard
- formula registry
- rebalance list/detail
- metric freeze audit
- simulation evidence
- review/approval command
- apply command gated
- rollback command gated

### 5.6 Governance / Approval

Flow:

```text
Candidate target -> ApprovalDecision proposed -> review -> decide
-> conditions / rejection / approval -> downstream deployment/evolution eligibility
```

Required:

- governance queue
- approval queue
- approval detail
- decide endpoint
- batch decision
- write authority / permission view
- audit trail
- evidence refs
- confirm-token for high-risk approvals

### 5.7 Deployment / Runtime

Flow:

```text
Approved artifact + active admissible binding
-> DeploymentPlan -> loader checks -> runtime-manager
-> RuntimeBinding -> paper/canary/live/frozen state
-> telemetry -> rollback/safe-mode/evolution
```

Required:

- deployment list/detail
- runtime list/detail
- deployment review composed view
- RuntimeBinding state
- rollback target
- loader check summary
- command receipt
- kill-switch/safe-mode visibility

### 5.8 Telemetry / Incident / Postmortem

Flow:

```text
Runtime event / alert / manual action
-> telemetry ingest -> incident / alert surface
-> triage -> risk response -> postmortem
-> evolution evidence
```

Required:

- alert rail
- incident list/detail
- postmortem library
- lineage explorer
- telemetry summary
- risk center
- kill-switch/safe-mode read state
- no sampled critical order/fill/deploy/audit events

### 5.9 Evolution

Flow:

```text
Telemetry / incident / postmortem / drift / human correction
-> EvolutionDecision proposal -> review -> approve
-> execute -> freeze / rollback / retrain / redeploy
-> observation / cooldown -> telemetry
```

Required:

- evolution list/detail
- evolution center
- mutation review
- inspiration graph
- action boundary
- cooldown/observation window
- follow-through command state
- evolution audit and evidence refs

### 5.10 Capabilities / Tools / MCP / Skills

Flow:

```text
Tool/MCP/Skill registry -> route policy -> capability snapshot
-> persona session effective tools -> invocation audit
```

Required:

- tools list/detail
- MCP server/tool import
- skill list/detail
- channel list/detail
- workflow/hook management
- capability snapshot linkage
- deny-by-default for broker/live tools

---

## 6. OODA Maturity Analysis

### 6.1 Observe

Current baseline:

- Management read surfaces verified through BFF consolidation gate.
- Telemetry, incident, source/search, postmortem services exist in baseline.
- Shioaji sandbox and Qlib dataset evidence remain activation tracks.

Required next:

- paper-loop telemetry packet
- runtime state replay packet
- source/search-to-strategy evidence packet
- broker sandbox smoke evidence
- dataset manifest / Qlib admission packet

### 6.2 Orient

Current baseline:

- decision-front objects defined: RegimeState, UniverseSelection, SignalInference, AllocationDecision, RiskAdjudication.
- persona proposal and multi-persona aggregation semantics exist.
- route/consult/capability model exists.

Required next:

- implement/verify PersonaAllocationProposal ingestion
- implement portfolio synthesis evidence
- map research/signal/risk facts into replayable orientation bundle
- expose orientation bundle in Management detail pages

### 6.3 Decide

Current baseline:

- approval, deployment, capital, runtime, evolution objects exist.
- BFF command admission gate exists.
- command receipt evidence exists.

Required next:

- decision journal or decision packet for each high-risk management command
- approval rationale and evidence completeness checks
- sponsor selection policy in multi-persona context
- human gate audit for canary readiness

### 6.4 Act

Current baseline:

- RuntimeBinding semantics exist.
- runtime-manager owns deploy/rollback/kill-switch path.
- BFF write paths are gated.
- live broker and capital binding remain fail-closed.

Required next:

- paper deployment proof
- Shioaji sandbox proof
- canary readiness packet
- rollback drill
- safe-mode drill
- no-live-side-effect tests for every preactivation path

### 6.5 Learn / Evolve

Current baseline:

- EvolutionDecision flow exists.
- trainer and teaching history model exists.
- Qlib and imitation/RL framework roles are defined.

Required next:

- telemetry-to-evolution evidence packet
- retrain/revalidate job creation packet
- model/artifact comparison packet
- post-evolution observation window report
- persona teaching regression suite

---

## 7. Functional Requirements

### FR-01 Management Source Honesty

Every Management surface must expose whether it is:

- service-backed
- fixture-backed
- degraded
- unavailable
- fail-closed
- gated
- strict-live

Acceptance:

- source metadata visible in debug or status drawer
- strict mode never silently falls back to seed
- operator can distinguish paper, canary, live, frozen, sandbox

### FR-02 Control Room Must Be Composed from Canonical Surfaces

Control Room must not invent canonical state.

Acceptance:

- every card links to a service source or documented read model
- every card includes snapshot time
- stale/degraded cards have visible status
- intervention cards link to command/admission flow

### FR-03 Strategy-to-Artifact Trace

Strategy detail must trace to:

- StrategySpec
- research experiment
- artifact
- approval decision
- deployment plan
- runtime binding
- telemetry
- evolution decisions

Acceptance:

- missing links shown as absent, not synthesized
- artifact state and deployment stage are displayed separately

### FR-04 Persona-to-Capital Admissibility

Persona detail and capital detail must display admissibility status.

Acceptance:

- route policy status displayed
- consult policy status displayed
- capital binding status displayed
- deployment scope ceiling displayed
- live owner conflict displayed
- no UI path implies binding equals deployment

### FR-05 Governance Approval Flow

Approval flows must support:

- propose
- review
- decide
- revoke
- evidence refs
- audit trail
- role and policy check
- command receipt

Acceptance:

- approval decision action emits command receipt
- approval result is referenced by DeploymentPlan or EvolutionDecision
- high-risk action requires confirm token when configured

### FR-06 Deployment Review Flow

Deployment review must show:

- approved artifact
- approval decision
- active binding
- admissibility
- target stage
- loader checks
- rollback target
- risk conditions
- runtime plan

Acceptance:

- deployment cannot proceed without all preconditions
- DeploymentPlan and RuntimeBinding remain separate
- runtime-manager is only RuntimeBinding writer

### FR-07 Runtime Operations Flow

Runtime pages must show:

- RuntimeBinding
- deployment stage
- capital pool
- artifact/version
- status
- telemetry summary
- rollback options
- safe-mode / kill-switch state

Acceptance:

- rollback and kill-switch commands require appropriate role/MFA
- write operation returns command receipt
- active live effects remain disabled unless explicit activation gate

### FR-08 Incident and Postmortem Flow

Incident flow must support:

- alert triage
- incident detail
- evidence attachment
- runtime link
- postmortem
- evolution trigger

Acceptance:

- postmortem links to telemetry and evolution refs
- incident commands go through command envelope
- critical telemetry is not sampled

### FR-09 Evolution Flow

Evolution flow must support:

- proposal
- boundary query
- review
- approve/reject/cancel
- execute
- follow-through
- cooldown/observation

Acceptance:

- evolution does not directly mutate RuntimeBinding unless routed through runtime-manager
- retrain/revalidate routes to research plane
- redeploy routes to deployment/governance plane

### FR-10 Multi-Persona Portfolio Synthesis

Management must support a multi-persona proposal-to-artifact flow.

Acceptance:

- PersonaAllocationProposal is persisted or recorded
- aggregation output is a single AllocationPolicyArtifact
- conflict_resolution_log is visible
- sponsor persona is explicit
- governance can override
- optimizer does not act as arbitrator

### FR-11 OODA Loop Packet

Every OODA loop run must produce a packet:

```text
OodaLoopPacket
- observe_refs
- orient_bundle_ref
- decision_ref
- action_ref
- telemetry_refs
- evolution_ref
- audit_refs
- operator_refs
- fail_closed_checks
```

Acceptance:

- packet can be replayed
- packet can be linked from Control Room
- missing evidence prevents OODA-complete status

---

## 8. Non-Functional Requirements

### NFR-01 Safety

- live broker disabled until activation
- capital binding live activation disabled until activation
- OpenClaw cannot become execution kernel
- short-term feedback cannot directly mutate live behavior

### NFR-02 Auditability

Every command must have:

- actor
- roles
- trace_id
- correlation_id
- idempotency key
- target object
- policy decision
- command receipt
- audit action

### NFR-03 Replayability

Every closed-loop action must be replayable by:

- source/evidence refs
- state transition timestamps
- command receipts
- telemetry refs
- lineage refs

### NFR-04 Strict Mode

Strict mode must:

- disallow silent mock fallback
- show typed error for unavailable surfaces
- preserve fail-closed behavior
- not perform live capital side effects

### NFR-05 Degradation

Degraded state must be explicit and partial:

- one degraded surface must not hide entire page
- stale surfaces must show last_known_at
- unavailable surfaces must show typed error

### NFR-06 Observability

Required metrics:

- BFF read route pass rate
- BFF command rejection reasons
- SSE active connections
- replay misses
- runtime binding count
- incident count
- evolution decision count
- fail-closed gate posture
- broker sandbox readiness
- Qlib admission readiness

---

## 9. Gap Register

| Gap | Severity | Current posture | Required closure |
|---|---:|---|---|
| Broker production live | High | fail-closed | separate activation gate |
| Capital binding live activation | High | fail-closed | governance/risk activation |
| Canary | High | human-gated | risk-owner + operator approval |
| Qlib alpha production admission | Medium | activation track | admission packet |
| Shioaji sandbox | Medium | evidence track | smoke packet |
| Multi-persona synthesis proof | Medium | policy exists | sample proposal -> artifact -> approval |
| Full OODA replay packet | Medium | design required | OodaLoopPacket implementation |
| Lovable build-time strict env | Low | non-blocking follow-up | rebuild/publish verification |
| Production BFF HA/LB | Medium | deferred | re-entry gate |

---

## 10. Delivery Milestones

### M0 — Accepted Management/BFF Baseline Freeze

Goal: Treat BFF-CONSOL-027 as frozen baseline.

Deliverables:

- acceptance packet linked
- route manifest archived
- authenticated live smoke archived
- strict runtime cutover archived
- command receipt evidence archived
- seed/fallback post-state archived

### M1 — Management OODA Packet Foundation

Goal: Add OODA packet data model and read surfaces.

Deliverables:

- OodaLoopPacket schema
- observe/orient/decide/act/evolve refs
- control-room OODA status card
- detail replay drawer

### M2 — Paper Loop Proof

Goal: Complete a full paper-loop without live side effects.

Deliverables:

- research candidate
- approval
- deployment plan
- runtime binding paper stage
- telemetry event
- evolution review
- replay packet

### M3 — Shioaji Sandbox Evidence

Goal: Broker sandbox proof without production live.

Deliverables:

- account readiness
- place/cancel/readback/reconcile
- fail-closed posture
- human gate packet

### M4 — Qlib Alpha Admission

Goal: Submit Qlib LightGBM alpha candidate into registry/governance.

Deliverables:

- StrategySpec
- dataset manifest
- training/eval result
- artifact refs
- admission packet

### M5 — Multi-Persona Synthesis Proof

Goal: Show multiple persona proposals producing one artifact.

Deliverables:

- two or more PersonaAllocationProposal records
- conflict_resolution_log
- AllocationPolicyArtifact
- governance approval packet

### M6 — Evolution Follow-Through Proof

Goal: Trigger an evolution action from telemetry/postmortem evidence.

Deliverables:

- EvolutionDecision proposal
- review/approval
- retrain or rollback follow-through
- observation window evidence
- OODA packet closed

### M7 — Canary Readiness Gate

Goal: Prepare but not automatically enable canary.

Deliverables:

- canary readiness packet
- risk-owner approval checklist
- operator approval checklist
- no-live-side-effect tests
- rollback/safe-mode drill

---

## 11. Definition of Done

A flow is considered delivery complete only if:

1. frontend route exists
2. BFF route exists
3. downstream owner service exists
4. response carries source status
5. command path carries idempotency/audit/trace
6. strict mode works without seed fallback
7. telemetry/audit/lineage evidence exists
8. degraded path is tested
9. fail-closed boundaries are preserved
10. replay packet exists for closed-loop flows
11. acceptance evidence is archived

---

## 12. Final SA Position

Management is no longer blocked at BFF route coverage. The next system objective is:

> Turn the accepted Management/BFF baseline into a verified **paper OODA operating loop** with multi-persona synthesis, Qlib admission, broker sandbox evidence, runtime telemetry, and evolution follow-through — while keeping live broker and capital-binding side effects fail-closed until explicit activation.

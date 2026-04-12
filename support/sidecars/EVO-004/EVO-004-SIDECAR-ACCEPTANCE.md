# EVO-004 Acceptance Packet (Sidecar)

**Parent Task**: `EVO-004` — Wire operational evolution boundaries  
**Parent Owner**: Claude  
**Parent Reviewer**: Gemini  
**Parent Status**: `todo`  
**Sidecar Owner**: Codex  
**Sidecar Reviewer**: Claude  
**Helper Kind**: `acceptance_packet`  
**Generated**: 2026-04-10T22:57:44Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. It packages the dependency state, action-boundary map, and parent acceptance checklist for `EVO-004`.

Shared-truth note:

- `ai-status.json` / `current-work.md` show `EVO-004` is still `todo`, but all three formal dependencies are already `done`
- `ai-activity-log.jsonl` shows the latest parent worker started at `2026-04-10T22:53:28Z` and was then suspended waiting approval (`apr-20260410T225347Z-8f881d65`) at `2026-04-10T22:54:44Z`

This packet is meant to reduce restart cost once the parent task resumes.

---

## 1. Dependency Map

### 1.1 Formal Parent Dependencies

| Dependency | Task ID | Status | What EVO-004 can reuse |
|---|---|---|---|
| EvolutionDecision first-class contract | `EVO-003` | done | Normalized action families, risk tiers, owner matrices, cooldown / observation semantics, incident / postmortem linkage |
| Rollback execution semantics | `EX-002` | done | Canonical rollback vocabulary (`replace`, `pause_then_replace`, `liquidate_then_replace`), RuntimeBinding replacement semantics, telemetry cutover rules |
| Incident / postmortem backbone | `INC-001` | done | Formal incident / postmortem evidence objects, propagated deployment/runtime refs, reverse link target for evolution follow-up |

### 1.2 Additional Locked Truth EVO-004 Should Reuse Instead Of Redefine

| Source | Locked truth |
|---|---|
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | `freeze` is governance quarantine, `rollback` is runtime/deployment mitigation, thresholds and review / approval owner tiers are canonical |
| `services/control-plane/governance/evolution_decision.contract.md` | `EvolutionDecision` already has `ExecutionResult.execution_plane`, `approval_decision_id`, and normalized `freeze` + `target_stage` mapping |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Rollback never rewrites an existing binding in place; Runtime Manager creates a replacement binding and owns cutover |
| `services/execution/runtime-manager/rollback_action_matrix.md` | Runtime-side execution steps and position treatment are already explicit for all three rollback modes |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | `ApprovalDecision -> DeploymentPlan -> RuntimeBinding` is the only allowed deploy / execution chain; Runtime Manager is the sole writer of `RuntimeBinding` |
| `PAPER_CANARY_LIVE_POLICY.md` | paper / canary / live approval and veto rules are already defined; deployment-stage overrides live in `DeploymentPlan` |
| `services/incident/contract.md` | `INC-001` explicitly lists `EVO-004` as a downstream consumer of incident/postmortem status |

### 1.3 Downstream Consumers Waiting On EVO-004

| Consumer | Task ID | Status | Why EVO-004 matters |
|---|---|---|---|
| Kill-switch / safe-mode fast path | `EVO-005` | todo | Needs the normal governed boundary first so the emergency fast path can remain a deliberate exception |
| Operator-facing deployment / incident / evolution surfaces | `APP-002` | in_progress | Already shaped, but still needs final evolution-to-runtime/deployment action semantics |

### 1.4 Readiness Verdict On Dependencies

**EVO-004 is dependency-unblocked.**

What is already true:

- `EVO-003` finalized the governance-side object, risk tiers, and execution metadata shape
- `EX-002` finalized rollback execution vocabulary and Runtime Manager ownership
- `INC-001` finalized the incident/postmortem evidence objects that operational evolution decisions must consume

What is still missing:

- the parent task must wire these already-approved pieces into one explicit operational boundary for `freeze`, `rollback`, `retrain`, and redeploy follow-through

---

## 2. Action-Boundary Map

This section separates what is already locked from what `EVO-004` itself still has to formalize.

| Action path | Locked today | EVO-004 still needs to make explicit |
|---|---|---|
| `freeze` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` already defines `freeze` as governance quarantine, not rollback. `freeze` on `paper` / `canary` is medium risk; `freeze` on `live` is high risk. `EvolutionDecision` already normalizes this as `action_type = "freeze"` plus `target_stage`. | Whether and how a freeze decision propagates into downstream runtime/deployment action when there is an active runtime. The parent must say when freeze is governance-only and when it must be paired with a separate operational mitigation path. |
| `rollback` | `ROLLBACK_AND_POSITION_SEMANTICS.md`, `deployment_plan.py`, `deployment_saga.py`, and `rollback_action_matrix.md` already lock the operational side: rollback is runtime/deployment mitigation, uses `DeploymentPlan.rollback.action_type`, and Runtime Manager creates the replacement `RuntimeBinding`. | How incident / postmortem / EvolutionDecision evidence causes the rollback-controller path to fire, and what approval / review boundary applies before that operational command is issued under normal (non-fast-path) conditions. |
| `retrain` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` already defines `retrain` as low-risk and research-side. `EvolutionDecision` already treats it as a low-risk action with cooldown / observation metadata available on execution. | The exact handoff seam from an executed `EvolutionDecision(action_type = retrain)` into the research workflow and then back into governed artifact / deployment readiness, without implying direct runtime mutation. |
| redeploy follow-through | Deployment truth already exists through `ApprovalDecision`, `DeploymentPlan`, paper/canary/live stage policy, and Runtime Manager consumption of `DeploymentPlan`. | The current canonical truth does not define a standalone `EvolutionDecision.action_type = redeploy`. The parent must choose and document the formal bridge: whether an approved evolution outcome creates / references a `DeploymentPlan`, who submits it, and how it stays distinct from rollback. |

### 2.1 Freeze

Locked now:

- `freeze` is a governance state change
- it may happen with or without a current runtime
- high-risk incidents may trigger both freeze and rollback, but they are separate modeled actions

Parent must add:

- the routing rule for "freeze only" vs. "freeze plus operational mitigation"
- what `ExecutionResult.execution_plane` should record when freeze propagates downstream
- whether freeze on `live` requires operator acknowledgement before any accompanying deployment/runtime action under the normal path

### 2.2 Rollback

Locked now:

- rollback is operational mitigation
- `Rollback Controller` issues the rollback command
- `Runtime Manager` is the only writer of replacement binding state and position-owner cutover
- artifact loader does not decide rollback strategy and does not mutate bindings

Parent must add:

- the exact normal-path trigger chain from `IncidentCase` / `Postmortem` / `EvolutionDecision` into `Rollback Controller`
- how review / approval on the evolution side maps to a rollback request under normal conditions
- how rollback outcomes are reflected back into `EvolutionDecision.execution_result` and incident follow-up

### 2.3 Retrain

Locked now:

- retrain is low-risk and research-facing
- proposals can be created from performance / drift / human-correction evidence
- cooldown / observation windows already exist on the decision object

Parent must add:

- the downstream research execution owner and handoff envelope
- whether a retrain execution immediately creates a governed research job, registry submission, or only a queued work item
- the return path from retrain completion back into redeploy eligibility

### 2.4 Redeploy Follow-Through

Locked now:

- deploy / promote / rollback / freeze / resume transitions already flow through `DeploymentPlan`
- canary / live approvals already require Reviewer + Risk Owner + Operator
- Runtime Manager consumes `DeploymentPlan`; it does not self-author deployment from a binding request

Parent must add:

- the formal relation between approved evolution output and a new / updated `DeploymentPlan`
- whether redeploy is represented as:
  - a new `DeploymentPlan` spawned by the evolution plane
  - an operator-submitted deployment using evolution output as evidence
  - or another already-canonical pattern
- an explicit statement that `EVO-004` does not create a shadow runtime command surface outside `ApprovalDecision -> DeploymentPlan -> RuntimeBinding`

---

## 3. Parent Acceptance Checklist Expansion

The parent task acceptance line is:

> `each action path has owner, threshold, cooldown, and execution boundary`

This packet expands that into a reviewable checklist for Claude and later Gemini.

| # | Parent check | Status now | What "done" looks like |
|---|---|---|---|
| A1 | `freeze` owner path is explicit | OPEN | Stage-specific freeze risk maps to the right reviewed / approved owners, and the packet / contract says whether freeze stays governance-only or also triggers a separate operational action |
| A2 | `rollback` owner path is explicit | OPEN | The normal path from evolution / incident evidence to `Rollback Controller` is written down, including who approves it and which plane writes what |
| A3 | `retrain` owner path is explicit | OPEN | The research-side execution owner and handoff object are named, and the path does not imply direct deploy/runtime mutation |
| A4 | redeploy path is explicit | OPEN | The parent states exactly how approved evolution output turns into deploy follow-through without inventing a non-canonical shadow command |
| A5 | thresholds are mapped to action paths | OPEN | Performance, execution drift, feature drift, human correction, Severity-1 / repeated Severity-2 incident, and "rollback executed but issue persists" all map to explicit candidate actions |
| A6 | cooldown / observation boundary is explicit | OPEN | The parent states what counts as `executed` for freeze / rollback / retrain / redeploy and how downstream completion feeds the `EvolutionDecision` active window |
| A7 | execution boundary preserves existing write owners | OPEN | Governance / evolution does not write `RuntimeBinding`; runtime does not redefine risk tiers; deployment still flows through `DeploymentPlan` |
| A8 | incident/postmortem evidence is reused rather than duplicated | OPEN | The parent cites `IncidentCase` / `Postmortem` as the operational evidence source and does not invent parallel incident truth |
| A9 | freeze and rollback can co-exist without collapsing into one object | OPEN | The parent explicitly states freeze-without-runtime, rollback-without-freeze, and dual-trigger cases |
| A10 | downstream consumers get a stable seam | OPEN | `EVO-005` and `APP-002` can depend on the parent result without re-deriving rollback / freeze / redeploy semantics themselves |

---

## 4. Reviewer Focus Areas

These are the highest-signal points for parent review. They are not new truth; they are the places most likely to drift if `EVO-004` is written too loosely.

### 4.1 Do not re-collapse freeze and rollback

Current L1 truth is already explicit:

- freeze = governance quarantine / future deployability control
- rollback = operational mitigation on an active deployment/runtime

If the parent writes them as one blended action, it will contradict both `EVOLUTION_REVIEW_AND_THRESHOLDS.md` and `ROLLBACK_AND_POSITION_SEMANTICS.md`.

### 4.2 Do not let evolution bypass DeploymentPlan

Current truth already says:

- deploy execution flows through `ApprovalDecision -> DeploymentPlan -> RuntimeBinding`
- Runtime Manager is the only writer of binding state

If the parent invents a direct evolution-to-runtime deploy command, it will create a shadow control path outside the canonical deployment chain.

### 4.3 Redeploy needs a named canonical bridge

There is currently no standalone `EvolutionDecision.action_type = "redeploy"` in the normalized action catalog.

That is acceptable, but the parent must make the bridge explicit:

- either redeploy is downstream deployment work triggered by an approved evolution decision
- or redeploy stays operator-submitted while evolution only supplies evidence / recommendation

What must be avoided is leaving redeploy as implied narrative rather than a formal handoff.

### 4.4 Keep normal path separate from EVO-005 emergency path

`EVO-005` is the fast-path exception for kill-switch / safe mode.

`EVO-004` should therefore formalize the normal governed boundary for:

- rollback
- risk-off under ordinary review/governance flow
- post-incident operational follow-through

without silently turning every mitigation into the fast path.

---

## 5. Suggested Parent Deliverables

If Claude wants the shortest route to a reviewable `EVO-004`, this sidecar suggests three concrete outputs:

1. **Action Routing Table**
   - one row each for freeze / rollback / retrain / redeploy follow-through
   - columns for trigger evidence, risk tier, reviewed owner, approved owner, execution plane, write owner, and return signal

2. **Execution Boundary Note**
   - explicit bridge from `EvolutionDecision.execution_result.execution_plane` to deployment/runtime/research execution
   - clear statement that deployment uses `DeploymentPlan` and runtime uses `RuntimeBinding`

3. **Incident-to-Evolution-to-Deployment Handoff Example**
   - one worked scenario covering Severity-1 incident, optional freeze, rollback request, postmortem linkage, and later retrain / redeploy readiness

This is enough to satisfy the acceptance line without changing any L1 document in the sidecar lane.

---

## 6. Files Referenced

### Shared Truth

- `ai-status.json`
- `current-work.md`
- `ai-activity-log.jsonl`

### Canonical / Contract Sources

- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `services/control-plane/governance/contract.md`
- `services/control-plane/governance/evolution_decision.contract.md`
- `services/control-plane/governance/review_evo003_qwen.md`
- `services/execution/review_ex002_claude_approved_zh.md`
- `services/incident/contract.md`
- `services/incident/review_inc001_codex_approved_zh.md`

### Supporting Execution / Runtime Evidence

- `services/control-plane/governance/deployment_plan.py`
- `services/control-plane/governance/deployment_saga.py`
- `services/execution/runtime-manager/rollback_action_matrix.md`

### Adjacent Downstream Support Artifact

- `support/sidecars/APP-002/APP-002-SIDECAR-BFF-HANDOFF.md`

### This Sidecar

- `support/sidecars/EVO-004/EVO-004-SIDECAR-ACCEPTANCE.md`

---

## 7. Handoff To Reviewer (Claude)

Claude, this packet is ready for review and parent-owner reuse.

What it gives you:

1. a dependency-confirmed starting point: `EVO-003`, `EX-002`, and `INC-001` are all done
2. a boundary map separating what is already locked from what `EVO-004` itself still has to formalize
3. a concrete acceptance checklist that can be carried into the parent task and later handed to Gemini for formal review

Recommended next step:

- absorb the action-boundary map into the parent `EVO-004` work
- keep the parent output anchored to existing canonical objects (`EvolutionDecision`, `ApprovalDecision`, `DeploymentPlan`, `RuntimeBinding`, `IncidentCase`, `Postmortem`)
- once the parent is reviewable, hand it to Gemini with the acceptance checklist in §3 as the review frame

---

*Generated by Codex as a sidecar `acceptance_packet` helper for EVO-004. This file is a support artifact and does not modify canonical truth.*

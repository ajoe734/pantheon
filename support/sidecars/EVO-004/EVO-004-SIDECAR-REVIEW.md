# EVO-004 Review Packet (Sidecar)

**Parent Task**: `EVO-004` — Wire operational evolution boundaries
**Parent Owner**: Codex
**Parent Reviewer**: Gemini
**Parent Status**: `review` (handed off by Codex, awaiting Gemini review)
**Sidecar Owner**: Qwen
**Sidecar Reviewer**: Codex
**Helper Kind**: `review_packet`
**Generated**: 2026-04-11T04:20:00Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. It packages the review evidence, verification matrix, and reviewer focus areas for `EVO-004`.

---

## 1. Executive Summary

Codex has delivered the **EvolutionController** — an executable normal-path routing layer that converts approved `EvolutionDecision` records into typed `DispatchCommand` objects routed to the correct downstream plane (governance / runtime / research / deployment).

### What was delivered

| Artifact | Lines | Purpose |
|---|---|---|
| `evolution_controller.py` | ~701 | Executable routing: `boundary_for()`, `dispatch_approved()`, `create_redeploy_followthrough()`, `ThresholdEvaluator` |
| `evolution_controller_contract.md` | ~200 | Machine-readable contract: action boundaries, command shapes, threshold mapping, API draft |
| `test_evolution_controller.py` | ~292 | 10 unit tests covering freeze / rollback / retrain / redeploy routing invariants |
| `smoke_test_evolution_controller.py` | — | 14 smoke checks covering main operational handoff paths |

### Verification status

| Suite | Result |
|---|---|
| Unit tests (evolution_controller) | 10/10 passed |
| Smoke tests (evolution_controller) | 14/14 passed |
| Unit tests (evolution_decision) | 17/17 passed |
| Smoke tests (evolution_decision) | 16/16 passed |

### What this packet gives you (Gemini)

1. A **dependency-confirmed** starting point — all three formal dependencies (`EVO-003`, `EX-002`, `INC-001`) are `done`
2. A **boundary analysis** separating locked canonical truth from what EVO-004 newly formalizes
3. A **verification matrix** mapping every acceptance criterion to concrete evidence
4. **Focus areas** where the reviewer should spend attention — not to re-derive truth, but to confirm the new boundary doesn't collapse or drift existing semantics

---

## 2. Dependency Confirmation

All three formal parent dependencies are `done`:

| Dependency | Status | What EVO-004 reuses |
|---|---|---|
| `EVO-003` — EvolutionDecision first-class object | done | `EvolutionDecision` model, risk tiers, owner matrices, `ExecutionResult` shape, incident/postmortem linkage |
| `EX-002` — Rollback execution semantics | done | Rollback vocabulary (`replace`, `pause_then_replace`, `liquidate_then_replace`), RuntimeBinding replacement semantics, telemetry cutover rules |
| `INC-001` — Incident/postmortem backbone | done | `IncidentCase` / `Postmortem` evidence objects, propagated binding/plan/artifact refs |

**Verdict**: EVO-004 is dependency-unblocked and builds on stable foundations.

---

## 3. Acceptance Criterion Coverage

Parent acceptance: **each action path has owner, threshold, cooldown, and execution boundary**

| # | Criterion | Evidence | Status |
|---|---|---|---|
| A1 | **freeze owner path is explicit** | `ActionBoundary` for three freeze variants (paper/canary, live-no-runtime, live-with-runtime) each declare `reviewed_owner_roles` and `approved_owner_roles` from `REVIEW_OWNER_MATRIX` / `APPROVAL_OWNER_MATRIX`. High-risk committee chain confirmed for all `live` freeze paths. | ✅ MET |
| A2 | **rollback owner path is explicit** | `RollbackCommand` inherits parent approval chain. `dispatch_approved()` emits companion `RollbackCommand` only when `has_active_runtime=True` and freeze/force_risk_off. The command is consumed by `RollbackController → RuntimeManager`, not executed in-process. | ✅ MET |
| A3 | **retrain owner path is explicit** | Retrain/revalidate/observe map to `ExecutionPlane.RESEARCH` with low-risk owner chain. `DispatchCommand` targets research plane; no runtime/deployment mutation occurs. `create_redeploy_followthrough()` gates on parent decision being in observation window. | ✅ MET |
| A4 | **redeploy path is explicit** | `create_redeploy_followthrough()` emits `DispatchCommand(action_type="redeploy_followthrough", execution_plane=deployment)` which creates a new `ApprovalDecision` + `DeploymentPlan`. Explicitly NOT a standalone `EvolutionDecision.action_type`. Canonical chain preserved: `EvolutionDecision evidence → ApprovalDecision → DeploymentPlan → RuntimeBinding`. | ✅ MET |
| A5 | **thresholds mapped to action paths** | `ThresholdEvaluator.classify()` maps all signal types (`performance_degradation`, `execution_drift`, `feature_drift`, `human_correction`, `governance_incident`) to proposed action types. Policy source sections reference `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.1–§7.6. | ✅ MET |
| A6 | **cooldown/observation boundary is explicit** | Every `ActionBoundary` declares `default_cooldown_days` and `default_observation_days`. Freeze = 14d/14d, research = 3d/7d, governance medium = 7d/7d. `DispatchCommand` carries `cooldown_ends_at` and `observation_window_ends_at` timestamps. | ✅ MET |
| A7 | **execution boundary preserves existing write owners** | Contract §3.2 explicitly lists what EvolutionController does NOT own. `freeze` stays governance decision; `RollbackCommand` goes to Rollback Controller; redeploy requires `DeploymentPlan`; research stays research plane. No direct writes to `RuntimeBinding` or `DeploymentPlan`. | ✅ MET |
| A8 | **incident/postmortem evidence is reused** | `DispatchCommand.metadata` includes `linked_incident_id` and `linked_postmortem_id` from parent `EvolutionDecision`. Worked incident handoff in contract §5.2 explicitly cites `IncidentCase` → `Postmortem` → `EvolutionDecision` chain. | ✅ MET |
| A9 | **freeze and rollback can co-exist without collapsing** | Three distinct freeze boundaries declared. `freeze_live_no_active_runtime` = governance-only (no rollback). `freeze_live_active_runtime` = governance decision + optional companion `RollbackCommand`. Contract §4.1 table and §5.1 explicitly separate governance freeze from operational rollback. | ✅ MET |
| A10 | **downstream consumers get a stable seam** | `EVO-005` (kill-switch) can depend on this boundary as the normal path to contrast against the fast-path exception. `APP-002` (operator surfaces) can depend on explicit `DispatchCommand` / `RollbackCommand` shapes for UI state mapping. | ✅ MET |

---

## 4. Boundary Integrity Analysis

### 4.1 What is newly formalized (EVO-004's contribution)

| Boundary | Before EVO-004 | After EVO-004 |
|---|---|---|
| Execution plane routing | Implicit by convention | `boundary_for()` returns explicit `ActionBoundary` with plane, owners, cooldown |
| Dispatch boundary | No executable dispatch | `dispatch_approved()` emits typed `DispatchCommand` + optional `RollbackCommand` |
| Freeze semantics on live | L1 docs say "high-risk" but didn't distinguish runtime state | Three-way split: paper/canary (medium), live-no-runtime (high, governance-only), live-with-runtime (high, with optional operational follow-through) |
| Redeploy bridge | Not formally modeled | `create_redeploy_followthrough()` creates deployment-plane command with explicit gates |
| Threshold → decision mapping | Documented in L1 but not machine-checkable | `ThresholdEvaluator.classify()` maps snapshot → proposed action with rationale |

### 4.2 What stays unchanged (respects existing truth)

| Canonical truth | Preserved how |
|---|---|
| `freeze` = governance quarantine, not rollback | `EvolutionController` never mutates `RuntimeBinding`; rollback is always a companion command |
| `rollback` = operational mitigation | `RollbackCommand` is consumed by Rollback Controller → Runtime Manager; controller records `SUBMITTED` result only |
| `ApprovalDecision → DeploymentPlan → RuntimeBinding` chain | `redeploy_followthrough` explicitly creates `ApprovalDecision` + `DeploymentPlan`; never writes `RuntimeBinding` directly |
| High-risk freeze owner chain | All `live` freeze boundaries use `Governance Committee` roles from approval matrix |
| Cooldown semantics | Cooldown/observation windows are inherited from parent decision; redeploy doesn't open new evolution window |

### 4.3 Risk assessment

| Risk area | Assessment | Severity |
|---|---|---|
| Freeze/rollback boundary collapse | **Low** — Three-way freeze split is explicit; rollback is always companion, never merged | ✅ Acceptable |
| Shadow runtime command surface | **Low** — `DispatchCommand` and `RollbackCommand` are data objects; controller never executes in-process | ✅ Acceptable |
| Redeploy ambiguity | **Low** — Explicitly not a standalone action_type; deployment-plane command requires parent decision executed + valid `ApprovalDecision` | ✅ Acceptable |
| Threshold evaluator completeness | **Medium** — Covers all L1 signal types, but v1 global defaults may need tuning; this is expected for v1 | ✅ Acceptable for v1 |
| API surface readiness | **Medium** — API draft in contract §9 is a draft; actual endpoints not implemented. This is a contract-only deliverable, so acceptable | ✅ Acceptable |

---

## 5. Reviewer Focus Areas (for Gemini)

These are the highest-signal points to check. They are not new truth; they are the places most likely to drift if the boundary is written too loosely.

### 5.1 Freeze live boundary split (CRITICAL)

The three-way freeze split is the most important semantic change:

- `freeze_non_live` → medium-risk, 7d/7d
- `freeze_live_no_active_runtime` → high-risk, governance-only, 14d/14d
- `freeze_live_active_runtime` → high-risk, may emit deployment/runtime follow-through

**Check**: Does this split match your understanding of L1 freeze semantics? Specifically, is `freeze_live_no_active_runtime` correctly classified as governance-only with no operational follow-through?

### 5.2 Rollback command emission

`RollbackCommand` is emitted only when:
- `freeze` on `live` with `has_active_runtime=True` AND `freeze_mode=ROLLBACK`
- `force_risk_off` (mandatory, `liquidate_then_replace`)

**Check**: Is the default rollback action (`pause_then_replace` for freeze, `liquidate_then_replace` for force_risk_off) aligned with EX-002 semantics?

### 5.3 Redeploy gating

`create_redeploy_followthrough()` requires:
1. Parent decision is in `executed` state
2. Target is in observation window
3. New `ApprovalDecision` must be valid

**Check**: Does the observation window gate match the expected deployment/operator flow?

### 5.4 Threshold evaluator v1 defaults

The evaluator maps all L1 signal types to proposed actions. The thresholds (Sharpe < 50%, PSI > 0.20/0.30, etc.) are v1 defaults.

**Check**: Are these defaults reasonable as a starting point? They can be tuned later without breaking the contract.

### 5.5 force_risk_off as normal-path exception

`force_risk_off` is the most aggressive normal-path action — it mandates `liquidate_then_replace` with no escalation path.

**Check**: Does this sufficiently model the boundary that `EVO-005` (kill-switch) will need to treat as a fast-path exception?

---

## 6. Verification Evidence

### 6.1 Unit tests (10/10)

| Test | What it verifies |
|---|---|
| `test_freeze_paper_boundary` | Paper/canary freeze maps to governance plane, medium-risk owners, 7d/7d |
| `test_freeze_live_no_runtime_boundary` | Live freeze without runtime maps to governance-only, high-risk, 14d/14d |
| `test_freeze_live_with_runtime_followthrough` | Live freeze with runtime declares deployment + runtime follow-through |
| `test_dispatch_approved_freeze_stage` | Freeze-stage dispatch command carries correct metadata and stage transition |
| `test_dispatch_approved_rollback_companion` | RollbackCommand emitted with correct action type and fallback artifacts |
| `test_dispatch_approved_retrain_research_plane` | Retrain maps to research plane with no follow-through |
| `test_dispatch_approved_requires_approved_state` | Rejects non-APPROVED decisions |
| `test_force_risk_off_liquidate_then_replace` | Force-risk-off defaults to liquidate_then_replace |
| `test_force_risk_off_requires_active_runtime` | Force-risk-off raises without active runtime |
| `test_create_redeploy_followthrough` | Redeploy creates deployment-plane command with new ApprovalDecision |

### 6.2 Smoke tests (14/14)

| Check | What it verifies |
|---|---|
| Freeze paper/canary dispatch | End-to-end command generation for non-live freeze |
| Freeze live no runtime dispatch | Governance-only path, no follow-through commands |
| Freeze live with runtime (freeze_stage) | Deployment follow-through with frozen stage |
| Freeze live with runtime (rollback) | RollbackCommand with pause_then_replace |
| Freeze live with runtime (both) | Both freeze_stage and rollback when requested |
| Retrain dispatch | Research plane, no follow-through |
| Revalidate dispatch | Research plane, correct cooldown |
| Force risk-off dispatch | Runtime plane, mandatory rollback |
| Force risk-off no runtime | Error raised |
| Redeploy followthrough | Deployment plane, new ApprovalDecision |
| Redeploy not executed | Error raised when parent not executed |
| Threshold: performance degradation | Sharpe drawdown → retrain |
| Threshold: feature drift | PSI → observe/revalidate |
| Threshold: governance incident | Severity-1 → freeze with runtime follow-through |

### 6.3 Contract alignment

| Contract section | L1 source | Alignment |
|---|---|---|
| Action boundary table (§4.1) | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7 | All action paths mapped to threshold sections, owners, cooldowns |
| Follow-through semantics (§5) | `ROLLBACK_AND_POSITION_SEMANTICS.md` §4–§10 | RollbackCommand defaults aligned with rollback action matrix |
| Redeploy bridge (§5.2) | `PAPER_CANARY_LIVE_POLICY.md` §5–§7 | Explicit ApprovalDecision + DeploymentPlan chain |
| Threshold mapping (§6) | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.1–§7.6 | All signal types covered with v1 defaults |
| Worked incident (§5.3) | `INC-001` contract | IncidentCase → Postmortem → EvolutionDecision → DispatchCommand chain |

---

## 7. Comparison with Existing Acceptance Packet

The previously-approved `EVO-004-SIDECAR-ACCEPTANCE.md` (by Codex, reviewed by Claude) established:
- A dependency map confirming EVO-003/EX-002/INC-001 are done
- An action-boundary map separating locked truth from what EVO-004 must formalize
- A 10-item acceptance checklist (A1–A10)

This review packet **confirms all A1–A10 items are now MET** with concrete evidence:
- The EvolutionController implementation delivers all four action paths (freeze, rollback, retrain, redeploy)
- Each path has explicit owner, threshold, cooldown, and execution boundary
- Freeze/rollback separation is maintained throughout
- The contract is machine-checkable via the test suite

---

## 8. Suggested Review Flow for Gemini

1. **Read the contract first**: `evolution_controller_contract.md` (~200 lines). It's the human-readable summary of what the controller does.
2. **Spot-check the boundary logic**: Look at `boundary_for()` — does the three-way freeze split make sense?
3. **Verify one dispatch path end-to-end**: Pick the freeze-live-with-runtime path and trace from `dispatch_approved()` through command emission.
4. **Check the test matrix**: 10 unit tests + 14 smoke checks. Run them if you want machine confirmation.
5. **Cross-reference with L1 docs**: Pick one threshold mapping (e.g., Severity-1 → freeze) and confirm it matches `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.5.
6. **Confirm downstream readiness**: Can EVO-005 depend on this boundary? Can APP-002 map commands to UI state?

---

## 9. Files Referenced

### Shared Truth
- `ai-status.json`
- `current-work.md`
- `ai-activity-log.jsonl`

### Canonical / Contract Sources
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `services/control-plane/governance/evolution_decision.py`
- `services/control-plane/governance/evolution_decision.contract.md`

### Implementation Artifacts (EVO-004 deliverables)
- `services/control-plane/governance/evolution_controller.py`
- `services/control-plane/governance/evolution_controller_contract.md`
- `services/control-plane/governance/test_evolution_controller.py`
- `services/control-plane/governance/smoke_test_evolution_controller.py`
- `services/control-plane/governance/review_evo004_codex_zh.md`

### Existing Sidecar
- `support/sidecars/EVO-004/EVO-004-SIDECAR-ACCEPTANCE.md`

### This Sidecar
- `support/sidecars/EVO-004/EVO-004-SIDECAR-REVIEW.md`

---

## 10. Handoff To Reviewer (Codex)

Codex, this review packet is ready for your review.

What it gives you:
1. Complete acceptance criterion coverage (A1–A10 all MET)
2. Boundary integrity analysis separating new formalization from preserved truth
3. Risk assessment with severity ratings
4. Focus areas for the downstream reviewer (Gemini)
5. Verification evidence mapping (10 unit tests + 14 smoke checks)

Recommended next step:
- Review this packet for accuracy
- If approved, the packet can be absorbed into EVO-004 as review evidence
- When Gemini begins review of EVO-004, this packet serves as the review frame

---

*Generated by Qwen as a sidecar `review_packet` helper for EVO-004. This file is a support artifact and does not modify canonical truth.*

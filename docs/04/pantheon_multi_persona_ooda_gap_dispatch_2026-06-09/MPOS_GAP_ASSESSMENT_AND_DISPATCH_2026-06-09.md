# Multi-Persona OODA Gap Assessment and Dispatch

Generated: 2026-06-09

Status: implementation gap assessment and worker dispatch packet

Owner: Codex

Reviewer: Claude

Task: MPOS-GAP-DISPATCH-001

## 1. Executive Conclusion

截至 2026-06-09 最新 `dev`，Pantheon 仍不能完整證明使用者要求的全流程：

```text
Persona A/B/C each:
  Observe -> Orient -> Decide -> Act -> Learn
        -> pre-LEAN consultation / committee / conflict classification /
           homogeneity-correlation review / artifact synthesis / governance
        -> approved AllocationPolicyArtifact
        -> DeploymentPlan
        -> RuntimeBinding
        -> LEAN execution only
        -> broker/fills/telemetry
        -> per-persona or sponsor-attributed Learn feedback
```

但結論必須比前一版更精準：`MPOS-P1-ART-001` 已補上
`AllocationPolicyArtifact -> registry candidate -> approved -> DeploymentPlan reference`
這段核心橋接。現在的主要缺口不再是 artifact 無法被 DeploymentPlan 引用，而是「多人格 allocation policy 是否真正跑到 runtime/LEAN/telemetry/Learn」尚未有端到端證明。

目前已能證明：

- 單一策略 paper OODA：source/StrategySpec/ExperimentRun/admission/DeploymentPlan/RuntimeBinding/paper LEAN/telemetry/evolution proposal。
- multi-persona synthesis：至少兩個 persona proposal 合成一個 `AllocationPolicyArtifact`，含 sponsor resolution 與 conflict log。
- allocation artifact registry bridge：`AllocationPolicyArtifact` 可登錄為 registry artifact type `allocation_policy`，從 candidate advance 到 approved，並可被 DeploymentPlan reference。
- persona memory primitives：已存在 persona memory writeback/retrieval endpoint 與 institutional memory。

目前仍缺：

- Persona A/B/C 各自從真實 Observe/Orient/Decide 產出 proposal，再合成 allocation policy 的 E2E。
- approved `AllocationPolicyArtifact` 一路進 DeploymentPlan、RuntimeBinding、paper LEAN run、fills/telemetry、Learn 的 E2E。
- consultation/committee handoff 被 allocation approval path 強制消費的 gate。
- homogeneity/correlation review 被放入 pre-LEAN allocation gate 的硬門檻。
- telemetry/postmortem/evolution 自動寫回 per-persona 或 sponsor-attributed Learn 的閉環。
- Qlib/QuantLib/vectorbt/statsmodels 在 MPOS Observe 矩陣中的 production/readiness 語意仍需整理成一致驗收證據。

## 2. Target Flow Under Audit

### 2.1 Per-Persona Loop

每個 persona 必須獨立走過：

| Stage | Required behavior | Current state |
|---|---|---|
| Observe | market data / research notes / telemetry / incidents become evidence | partial |
| Research tools | source ingest, StrategySpecSeed, Qlib/vectorbt/statsmodels/QuantLib, telemetry query | partial |
| Output | alpha idea / StrategySpec / ExperimentRun | partial |
| Validation | backtest / rolling OOS / validation / no-order-route proof | partial |
| Orient | regime, OOS metrics, risk, mandate fit, evidence quality | partial |
| Decide | PersonaAllocationProposal + evidence_refs | partial |
| Act | submit proposal / consult request / governance request | partial |
| Learn | telemetry, postmortem, evolution, memory | partial |

### 2.2 Shared Control Plane

多人格 proposal 進入 pre-LEAN control plane 後，必須證明：

| Gate | Required behavior | Current state |
|---|---|---|
| Conflict classification | direction, weight, horizon, risk posture, regime, sponsor conflict | implemented |
| Homogeneity/correlation review | detect over-concentration, highly correlated signals, duplicate exposure | missing as hard gate |
| Consultation/committee | material committee memo/handoff must be consumed before high-risk approval | partial |
| Artifact synthesis | one AllocationPolicyArtifact with lineage and conflict log | implemented |
| Governance approval | allocation artifact can be approved with evidence refs | partial |
| Deployment | approved allocation artifact can produce DeploymentPlan | partial |
| Runtime | DeploymentPlan creates RuntimeBinding and runs LEAN paper execution | missing E2E for allocation artifact |
| Feedback | telemetry/fills/postmortem/evolution write back per persona/sponsor Learn | missing E2E |

## 3. Current-State Evidence

### 3.1 Single-Strategy Paper OODA Is Proven

The repo has focused E2E coverage for a single strategy paper loop:

- `tests/e2e/test_source_to_strategy_spec.py`
- `tests/e2e/test_strategy_spec_to_experiment_run.py`
- `tests/e2e/test_experiment_run_to_admission.py`
- `tests/e2e/test_admission_to_deployment_plan.py`
- `tests/e2e/test_deployment_plan_to_paper_run.py`
- `tests/e2e/test_paper_run_to_evolution_decision.py`

This proves a paper-only route can move from research evidence through governance, DeploymentPlan, RuntimeBinding, paper LEAN execution, simulated fills, telemetry, incident/postmortem, and evolution proposal. It also proves the route remains no-order-route / no-live-broker for paper.

Limit: this is not yet multi-persona allocation policy execution.

### 3.2 Research Observe and No-Order-Route Are Mostly Proven

Relevant files:

- `services/source_ingestion/strategy_seed_builder.py`
- `services/research/strategy_spec/conversion.py`
- `services/research/experiment_orchestrator/parallel_dispatch.py`
- `services/governance/research_activation/admission_gate.py`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` (`MPOS Observe Backend Matrix (G6)`)

Current interpretation:

- source ingest and StrategySpecSeed conversion are research-only building blocks.
- conversion emits registry-ready draft/candidate style payloads but does not itself create execution routes.
- parallel dispatch includes vectorbt, Qlib, and statsmodels backend ids.
- QuantLib exists as a governed research path, but it is not part of the default dispatcher list reviewed here.
- admission gate requires no-order-route / no-broker / no-capital assertions before research artifacts move toward candidate state.
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` now carries the MPOS Observe backend matrix for G6, including posture, no-order-route guarantees, proof tests, and the Qlib/QuantLib dispatch distinction.

Gap: the MPOS persona loop does not yet prove Persona A/B/C each run Observe/Orient with real StrategySpecSeed / ExperimentRun / OOS evidence before producing `PersonaAllocationProposal`.

### 3.3 Multi-Persona Synthesis Is Proven Up To Governance Memo

Relevant files:

- `tests/e2e/test_multi_persona_ooda_packet.py`
- `services/optimizer-svc/portfolio_synthesis/models.py`
- `services/optimizer-svc/portfolio_synthesis/synthesizer.py`
- `services/optimizer-svc/portfolio_synthesis/conflict_classifier.py`
- `services/governance/multi_persona/sponsor_resolver.py`

Current state:

- at least two active personas participate.
- suspended persona is excluded by persona registry health gate.
- `PersonaAllocationProposal` includes evidence refs, regime, conviction, uncertainty, and fit scores.
- synthesis emits a single `AllocationPolicyArtifact`.
- sponsor resolver emits sponsor persona and conflict resolution log.
- evidence packet is written for `MPO-003-V2`.

Limit: this E2E stops at governance memo/evidence packet. It does not register the allocation artifact, approve it, deploy it, bind runtime, run LEAN, ingest fills, or write Learn feedback.

### 3.4 AllocationPolicyArtifact Registry Bridge Is Now Addressed

Relevant files:

- `services/registry/test_allocation_policy_artifact.py`
- `services/registry/service.py`
- `services/registry/models.py`
- `services/control-plane/governance/deployment_plan.py`
- `.orchestrator/reviews/MPOS-P1-ART-001-review.md`

Current state after `MPOS-P1-ART-001`:

- `ArtifactType.ALLOCATION_POLICY = "allocation_policy"` is supported.
- allocation artifacts register as `artifact_state = candidate`.
- lineage maps `provenance_refs` to source run ids.
- conflict log id is preserved.
- full allocation artifact is preserved in metadata.
- allocation artifacts can advance candidate -> approved.
- `strategy_id = capital_pool_id` gives pool-scoped identity.
- `StagePlanner.create_plan()` accepts any approved registry entry and does not restrict artifact type away from allocation policy.

Residual risk:

- the proof currently validates DeploymentPlan readiness properties and generic planner acceptance.
- there is not yet a full E2E where the concrete registered allocation policy becomes a paper RuntimeBinding and produces LEAN telemetry/fills.

### 3.5 Consultation and Committee Are Implemented But Not Mandatory For Allocation Approval

Relevant files:

- `services/consultation/test_e2e_consult_review.py`
- `services/consultation/sponsor_decision_bridge.py`
- `services/control-plane/bff/test_cw03_committee_board_contract.py`
- `services/consultation/store.py`

Current state:

- consult request -> committee -> memo -> management review handoff is proven.
- BFF committee board can record sponsor decision and produce service handoff refs.
- `sponsor_decision_bridge` maps sponsor decisions to governance approval/evolution proposals.

Gap:

- allocation artifact approval does not appear to require a consultation handoff when conflict severity/risk demands committee review.
- committee evidence is available as data, but not enforced as a pre-LEAN hard gate for `AllocationPolicyArtifact` approval.

### 3.6 Conflict Classification Exists, But Homogeneity/Correlation Review Is Missing As A Gate

Relevant files:

- `services/optimizer-svc/portfolio_synthesis/conflict_classifier.py`
- `services/capital/risk_policy.py`
- `services/evaluation/models.py`
- `services/telemetry/lineage_read/service.py`

Current state:

- conflict classifier handles direction, weight, horizon, risk posture, regime, and sponsor ambiguity.
- RiskPolicy evaluator exists and is consumed by optimizer, deployment, promotion, and runtime manager paths.

Gap:

- there is no first-class allocation conflict type or hard gate for homogeneity/correlation.
- scattered correlation concepts exist, but they do not block multi-persona synthesis/approval when multiple personas converge on highly correlated exposures or duplicate strategies.

### 3.7 Learn Primitives Exist, But Automatic Attribution Is Not Closed

Relevant files:

- `services/telemetry/feedback_adapter.py`
- `services/incident/incident.py`
- `services/evolution/postmortem_bridge.py`
- `services/memory/institutional_memory_store.py`
- `services/memory/persona_memory_store.py`
- `services/memory/main.py`

Current state:

- telemetry lineage can expose runtime binding, deployment plan, capital pool, persona capital binding, strategy, registry, trace, and artifact refs.
- incidents and postmortems carry `persona_capital_binding_id`.
- evolution bridge emits proposal-only objects.
- institutional memory supports `contributing_persona_ids`.
- persona memory writeback and retrieval exist.

Gap:

- no E2E proves broker/fill telemetry or postmortem/evolution outcomes automatically write a persona memory record or sponsor-attributed institutional memory entry.
- no acceptance packet proves learn feedback is split correctly between contributing personas and the sponsor persona.

## 4. Gap Register

| Gap ID | Severity | Summary | Blocking reason | Required closure evidence |
|---|---|---|---|---|
| G1 | P1 blocker | Full allocation policy to RuntimeBinding/LEAN E2E missing | Current allocation policy bridge stops before runtime/LEAN telemetry | Test starts from registered approved AllocationPolicyArtifact and ends with paper LEAN fills plus telemetry lineage |
| G2 | P1 blocker | Persona A/B/C individual OODA evidence missing | Current multi-persona E2E uses proposal fixtures, not complete per-persona Observe/Orient/Decide chains | Three persona packets with StrategySpecSeed or StrategySpec, ExperimentRun/OOS metrics, risk/mandate fit, no-order-route proof, and proposal evidence_refs |
| G3 | P1 blocker | Consultation/committee is not a hard allocation gate | Committee handoff exists but approval path can be reasoned about without consuming it | Allocation approval rejects missing committee handoff when conflict/risk policy demands it |
| G4 | P1 blocker | Homogeneity/correlation review absent from pre-LEAN gate | Existing classifier lacks correlation or homogeneity conflict type | Synthesis/approval blocks or escalates highly correlated/duplicative persona proposals |
| G5 | P1 blocker | Per-persona/sponsor Learn writeback not automatic | Memory primitives exist but no telemetry -> memory feedback closure | Runtime telemetry/postmortem/evolution creates persona memory and sponsor-attributed institutional memory evidence |
| G6 | P2 clarity | Research backend maturity matrix is not MPOS-specific | Qlib/vectorbt/statsmodels/QuantLib status is spread across docs and code | `RESEARCH_BACKEND_MATURITY_MATRIX.md` section `MPOS Observe Backend Matrix (G6)` states supported path, activation status, no-order-route guarantees, and proof tests for each backend |

## 5. Dispatch Plan

The following tasks should be materialized through `scripts/ai_status.py assign` so the supervisor and auto workers receive normal task briefs, dashboard entries, and current-work sync.

| Task ID | Owner | Reviewer | Purpose |
|---|---|---|---|
| MPOS-P1-PER-002 | Copilot | Codex | Build Persona A/B/C Observe/Orient/Decide proof from research evidence into real PersonaAllocationProposal records |
| MPOS-P1-E2E-002 | Claude | Codex | Prove approved AllocationPolicyArtifact -> DeploymentPlan -> RuntimeBinding -> paper LEAN -> telemetry/fills |
| MPOS-P1-CONSULT-001 | Claude2 | Codex | Make committee/consultation handoff mandatory for high-risk allocation approval |
| MPOS-P1-RISK-002 | Codex | Claude | Add homogeneity/correlation review to pre-LEAN allocation gate |
| MPOS-P1-MEM-002 | Codex2 | Claude | Automate per-persona and sponsor-attributed Learn writeback from telemetry/postmortem/evolution |
| MPOS-P1-VERIFY-001 | Gemini2 | Codex | Produce supervisor closure packet after the implementation tasks land |
| MPOS-P2-BACKEND-001 | Codex | Claude | Normalize MPOS Observe backend matrix for Qlib/vectorbt/statsmodels/QuantLib |

## 6. Acceptance Details By Task

### MPOS-P1-PER-002

Acceptance:

- Build three persona fixtures A/B/C with distinct mandates, risk posture, and strategy-family fit.
- Each persona starts from source/strategy evidence instead of hand-authored final proposal only.
- Each persona packet includes StrategySpecSeed or StrategySpec, ExperimentRun or OOS validation evidence, regime/risk/mandate/evidence quality orientation, no-order-route proof, and final `PersonaAllocationProposal`.
- Proposal `evidence_refs` point back to each persona packet.
- Suspended or ineligible persona remains excluded by persona policy/health gate.

### MPOS-P1-E2E-002

Acceptance:

- Start with the synthesized `AllocationPolicyArtifact` from multi-persona proposals.
- Register it through allocation-policy registry facade.
- Advance candidate -> approved using an `ApprovalDecision` with evidence refs.
- Create a `DeploymentPlan` whose artifact type is `allocation_policy`.
- Create `RuntimeBinding` with persona capital binding and sponsor attribution.
- Run paper LEAN only and assert no live broker order route.
- Capture fills/telemetry and query lineage by runtime binding, deployment plan, capital pool, artifact, and persona capital binding.

### MPOS-P1-CONSULT-001

Acceptance:

- Allocation conflicts that require committee review produce or require a consultation request.
- Committee memo/handoff refs are stored as governance evidence.
- Allocation approval rejects missing committee handoff for high-risk or open-conflict paths.
- Sponsor decision bridge output can create an approval proposal for `allocation_policy`.
- Tests include approve, approve-with-conditions, reject, missing-handoff, and stale-handoff cases.

### MPOS-P1-RISK-002

Acceptance:

- Add homogeneity/correlation review to allocation conflict taxonomy or adjacent risk gate.
- Detect duplicated strategy family, high target overlap, high correlation bucket, and concentration by capital pool.
- Escalate or reject according to RiskPolicy evaluator precedence.
- Ensure risk veto still outranks committee escalation.
- Tests include low correlation pass, high correlation committee escalation, and hard veto.

### MPOS-P1-MEM-002

Acceptance:

- Convert telemetry/postmortem/evolution outcomes into persona memory writebacks.
- Sponsor-attributed institutional memory entry includes sponsor persona and contributing persona ids.
- Contributor persona memory entries link back to proposal ids and runtime telemetry evidence.
- Writeback is idempotent by source event id.
- Tests cover success, duplicate replay, missing persona attribution, and unauthorized writeback.

### MPOS-P1-VERIFY-001

Acceptance:

- Build one supervisor-visible closure packet summarizing all MPOS P1 gates.
- Include task/PR/commit/check refs for MPOS-P1-PER-002, E2E-002, CONSULT-001, RISK-002, and MEM-002.
- Include a requirement-by-requirement matrix against the target flow in this document.
- Include local validation commands and CI status.
- Mark unresolved live/canary broker activation as intentionally fail-closed, not complete production live proof.

### MPOS-P2-BACKEND-001

Acceptance:

- Create MPOS Observe backend matrix for vectorbt, Qlib, statsmodels, and QuantLib.
- For each backend, state production/readiness posture, no-order-route guarantees, and proof tests.
- Clarify whether Qlib remains activation-ready or production-active.
- Clarify whether QuantLib is default-dispatch, separate governed production path, or deferred.
- Link the matrix from the gap assessment or successor closure packet.

Closure link:

- `RESEARCH_BACKEND_MATURITY_MATRIX.md` section `MPOS Observe Backend Matrix (G6)` is the task-scoped closure artifact for MPOS-P2-BACKEND-001.

## 7. Validation Plan

Recommended local validation after implementation tasks:

```text
PYTHONDONTWRITEBYTECODE=1 PANTHEON_VECTORBT_BACKEND=stub python3 -m pytest -q -p no:cacheprovider \
  tests/e2e/test_source_to_strategy_spec.py \
  tests/e2e/test_strategy_spec_to_experiment_run.py \
  tests/e2e/test_experiment_run_to_admission.py \
  tests/e2e/test_admission_to_deployment_plan.py \
  tests/e2e/test_deployment_plan_to_paper_run.py \
  tests/e2e/test_paper_run_to_evolution_decision.py \
  services/registry/test_allocation_policy_artifact.py \
  services/control-plane/governance/test_persona_proposal_runtime_binding_e2e.py \
  services/optimizer-svc/test_portfolio_synthesis.py \
  services/optimizer-svc/test_allocation_conflict_classifier.py \
  services/optimizer-svc/test_allocation_policy_artifact_output.py \
  services/optimizer-svc/test_allocation_synthesis_method.py \
  services/consultation/test_e2e_consult_review.py \
  services/consultation/test_sponsor_decision_bridge.py \
  services/telemetry/test_feedback_adapter.py \
  services/memory/test_main.py
```

Do not run evidence-writing tests such as `tests/e2e/test_multi_persona_ooda_packet.py` in a dirty shared worktree unless the evidence output is intentionally part of the task.

## 8. Completion Definition

This dispatch packet is complete only when:

- the document is committed and merged;
- the dispatcher is committed and merged;
- generated task board artifacts are committed and merged;
- supervisor-visible tasks exist for the P1 blocker gaps and P2 backend clarity gap;
- final answer reports PR number and merge commit SHA, or reports the exact blocker.

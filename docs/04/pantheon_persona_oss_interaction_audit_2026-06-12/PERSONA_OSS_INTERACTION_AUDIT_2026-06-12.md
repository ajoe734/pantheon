# Persona OSS Interaction Audit

Date: 2026-06-12
Owner: Codex
Scope: Persona-facing OSS only

## Scope Definition

This report covers OSS components that a Pantheon persona can directly use during
registry resolution, session execution, research/learning work, consultation,
evidence production, experiment tracking, or proposal synthesis.

Direct persona interaction means one of these is true:

1. The component hosts or mediates the persona runtime/session.
2. The component is selected by a persona route/capability policy as a research,
   learning, or evaluation backend.
3. The component returns evidence, metrics, artifacts, or experiment references
   that a persona can cite in an OODA/proposal packet.
4. The component records or optimizes persona behavior, policy, preference, or
   research evidence.

Explicitly out of scope for this report:

- LEAN Launcher, LEAN strategy execution, and LEAN deployment.
- broker adapter internals, broker SDK sessions, order routes, and order
  placement.
- RuntimeBinding mutation, RuntimeBootstrapRequest creation, capital binding,
  paper/canary/live promotion, and approval authority.
- Execution-plane internals that happen after a persona-authored proposal leaves
  the persona/research/governance handoff.

## Full Persona OSS Flow

1. Registry Persona is loaded from Persona Registry with mandate, route policy,
   consult policy, workspace, and lifecycle state.
2. Session Persona is created for a concrete task. Its effective capability
   snapshot is frozen for audit/replay.
3. Runtime Persona enters OpenClaw-compatible runtime through an agent/session,
   consult session, committee, or workflow-triggered background session.
4. Route policy resolves persona-visible OSS tools/workflows/backends. Denied
   or downstream execution tools stay hidden or fail closed.
5. Persona invokes research/learning OSS only through governed adapters. Outputs
   are evidence artifacts, metrics, model candidates, or experiment refs.
6. Evidence stays non-executable by default: `artifact_state=draft`,
   `deployment_summary.current_stage=none`, `no_order_route=true`, and no
   broker/capital/live authority.
7. Persona cites evidence in OODA notes, allocation proposals, consult packets,
   or governance memos.
8. Any later movement to candidate, approval, RuntimeBinding, LEAN, broker,
   paper/canary/live, or capital authority exits the Persona OSS scope and must
   pass downstream registry/governance/execution controls.

## Persona OSS Inventory

| Component | Persona-facing interaction | Status | Boundary | Verification |
|---|---|---|---|---|
| `OpenClaw` | Runtime/session substrate for Runtime Persona, tools, workflows, skills, consult, committee, and audit trail | `governed` | May prepare research/review/support packets; must not create RuntimeBootstrapRequest, mutate RuntimeBinding, invoke LEAN, invoke broker SDK routes, or approve capital | `services/openclaw-gateway-adapter/test_session_lifecycle.py`; `services/openclaw-gateway-adapter/test_tool_workflow_bridge.py`; `OPENCLAW_RUNTIME_CONTRACT.md` |
| `DSPy` | Persona policy/prompt optimization and decision-module tuning evidence | `governed` | Produces governed learning artifacts, not orders or deployment authority | `services/learning/dspy/test_adapter.py`; `integrations/dspy/{integration,governance,smoke_test}.md` |
| `imitation` | Behavior cloning from trader/persona trajectory data | `governed` | BC artifacts can inform persona learning, not direct execution | `services/learning/imitation/test_adapter.py`; `integrations/imitation/{integration,governance,smoke_test}.md` |
| `TRL` | Persona preference learning / DPO from governed feedback pairs | `smoke-tested` | Produces draft/candidate learning artifacts; runtime-data and consumer gates remain closed | `services/learning/trl/test_adapter.py`; `services/learning/trl/test_activation_smoke.py`; `integrations/trl/{integration,governance,smoke_test,activation_packet}.md` |
| `Qlib` | Supervised alpha research and rolling OOS evidence cited by persona proposals | `smoke-tested` | Review-only research evidence; no direct registry truth write, broker route, or capital binding | `services/research/qlib/test_adapter.py`; `services/research/qlib/test_rolling_pipeline.py`; `tests/governance/test_qlib_proof_artifacts.py`; `integrations/qlib/{integration,governance,smoke_test,activation_packet}.md` |
| `vectorbt` | Fast strategy prototype backtest/scoring evidence for Observe phase | `governed` | Scoring-only draft evidence; may not write SignalStore, LEAN, broker, or live state | `services/research/vectorbt/test_adapter.py`; `integrations/vectorbt/{integration,governance,smoke_test}.md` |
| `statsmodels` | Econometric, cointegration, factor, and regime evidence for Observe/Orient | `governed` | Research-only non-executable artifacts | `services/research/statsmodels/test_adapter.py`; `tests/governance/test_statsmodels_proof_artifacts.py`; `integrations/statsmodels/{integration,governance,smoke_test}.md` |
| `QuantLib` | Pricing, Greeks, option-chain, and fixed-income risk evidence | `governed` | Separate governed research path; not default dispatcher fanout and not execution authority | `services/research/quantlib/test_adapter.py`; `tests/governance/test_quantlib_proof_artifacts.py`; `integrations/quantlib/{integration,governance,smoke_test}.md` |
| `FinRL` | Research-only single-agent RL policy evidence for future RL lane | `smoke-tested` | Explicit-gate offline evidence only; no broker, order route, paper/canary/live, or capital binding | `services/research/finrl/test_adapter.py`; `services/research/finrl/test_production_drl_run.py`; `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/` |
| `RLlib` | Research-only scalable/multi-agent train/eval evidence | `smoke-tested` | Explicit-gate offline evidence only; train/eval is not production activation | `services/research/rllib/test_adapter.py`; `services/research/rllib/test_production_ppo_run.py`; `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/` |
| `Ray Tune` | Research-only hyperparameter search evidence for RL/learning candidates | `smoke-tested` | Optimizer result output only; no registry-writing production lane by default | `services/research/rllib/test_ray_tune_adapter.py`; `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/` |
| `MLflow` | Primary experiment registry backend for persona-cited runs, metrics, and artifact refs | `governed` | Stores experiment evidence; does not authorize deployment or orders | `services/registry/experiments/test_adapter.py`; `integrations/mlflow/{integration,governance,smoke_test}.md` |
| `W&B` | Optional experiment tracking/visualization backend for persona-cited metrics/artifacts | `activation-gated` | Offline/online sync is explicit-gated and non-ordering; credentialed online activation remains closed | `tests/integrations/test_wandb_sync.py`; `integrations/wandb/credentialed_sync_proof.md`; `services/registry/experiments/WANDB_ACTIVATION.md` |

## End-to-End Scenarios

### Scenario 1: Persona Runtime Session Through OpenClaw

Registry Persona `persona-alpha` is resolved with its mandate, route policy,
consult policy, workspace, and lifecycle state. A Session Persona is created for
a research task, freezing the effective capability snapshot. The OpenClaw
adapter creates or resumes the runtime session, maps persona context into
OpenClaw session context, exposes only allowlisted tools/workflows, and writes
operator/session audit events. Broker, live, paper, canary, and capital
workflow prefixes remain permanently denied by adapter policy.

### Scenario 2: Observe Evidence With vectorbt, statsmodels, and MLflow

A research-only persona evaluates a candidate strategy. Route policy allows
`vectorbt` for rapid backtest scoring and `statsmodels` for regime or
cointegration evidence. Both adapters emit draft research artifacts with no
direct live influence. MLflow records run metadata and artifact references.
The persona cites those refs in an OODA packet or allocation proposal. No LEAN,
broker adapter, RuntimeBinding, or capital path is touched.

### Scenario 3: Supervised Alpha / Rolling OOS With Qlib

A persona needs supervised alpha evidence. The session invokes Qlib through the
governed research adapter or rolling OOS path. Qlib validates data/proof inputs,
produces draft artifacts and review-only candidate handoff evidence, and keeps
`deployment_stage=none`, `no_order_route=true`, and `order_route=none`. The
persona may cite the evidence, but production alpha activation remains gated.

### Scenario 4: Derivatives Risk Evidence With QuantLib

A persona analyzing option or fixed-income exposure invokes QuantLib through its
separate governed request path. QuantLib emits pricing reports, Greeks, or risk
snapshots as non-executable evidence. The persona can use those artifacts in a
proposal or risk consult, but they do not grant execution authority.

### Scenario 5: Persona Policy Optimization With DSPy

A persona improvement task sends governed examples, feedback, or decision traces
to DSPy. DSPy returns optimization artifacts for persona policy/prompt modules.
The artifacts are recorded as learning evidence and can later be reviewed or
registered. They do not mutate live persona capabilities by themselves.

### Scenario 6: Behavior Cloning And Preference Learning With imitation And TRL

Trader/persona trajectories feed the imitation adapter for behavior-cloning
evidence. Governed feedback pairs can feed TRL for DPO/preference learning.
Both return model or learning artifacts in draft/candidate posture. Persona
policy changes still require downstream governance/review before activation.

### Scenario 7: Research-Only RL With FinRL, RLlib, And Ray Tune

An exploratory persona requests RL evidence. FinRL, RLlib, and Ray Tune are
available only as explicit-gate, offline, smoke-tested research baselines. The
outputs are policy/search evidence and evaluator packets, with the RL production
gate closed. They cannot place orders, open broker sessions, or promote to
paper/canary/live.

### Scenario 8: Optional W&B Tracking

When experiment backend policy selects W&B, persona-cited metrics/artifacts can
flow to the offline store or explicit-gated online sync path. Online W&B requires
credentials, project configuration, and an activation gate. The path records
experiment evidence only; it is not a dispatch, broker, or capital path.

### Scenario 9: Multi-Persona OODA Proposal Synthesis

Two or more active personas cite OSS evidence refs in StrategySpec-backed
allocation proposals. The multi-persona OODA packet enforces registry health,
proposal schema, sponsor resolution, and governance memo construction. OSS
evidence remains input context for persona reasoning and proposal synthesis.
Deployment, RuntimeBinding, LEAN, broker adapter, and capital authority are
separate downstream gates after this handoff.

## Fixes From This Audit

1. Added `activation-gated` to OSS status-code definitions because W&B already
   uses that status in the canonical checklist.
2. Updated `RESEARCH_BACKEND_MATURITY_MATRIX.md` so FinRL, RLlib, and Ray Tune
   match the canonical `smoke-tested` status from `OSS_INTEGRATION_CHECKLIST.md`.
3. Updated W&B from `criteria-defined` to `activation-gated` in the research
   maturity matrix.
4. Added this Persona OSS audit as the scoped interaction inventory and scenario
   reference.
5. Added regression tests that check Persona OSS scope, status alignment, proof
   references, and downstream execution exclusions.

## Validation

Baseline validation before fixes:

- `python3 -m pytest tests/docs/test_mpos_backend_maturity_matrix.py services/control-plane/bff/test_research_oss_preactivation_contract.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q`
- Result: 79 passed in 22.93s

Post-fix validation is recorded in the task final report and must include the
new Persona OSS audit tests plus the targeted persona/research/learning adapter
test set.

Post-fix validation:

- `python3 -m pytest tests/docs/test_persona_oss_interaction_audit.py tests/docs/test_mpos_backend_maturity_matrix.py services/control-plane/bff/test_research_oss_preactivation_contract.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q`
- Result: 85 passed in 12.40s
- `python3 -m pytest tests/docs/test_persona_oss_interaction_audit.py tests/docs/test_mpos_backend_maturity_matrix.py services/control-plane/bff/test_research_oss_preactivation_contract.py services/control-plane/bff/test_per002_bff_persona_skills_tools_capabilities_contract.py services/openclaw-gateway-adapter/test_session_lifecycle.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py services/research/vectorbt/test_adapter.py services/research/statsmodels/test_adapter.py services/research/quantlib/test_adapter.py services/research/qlib/test_adapter.py services/research/qlib/test_rolling_pipeline.py services/learning/dspy/test_adapter.py services/learning/imitation/test_adapter.py services/learning/trl/test_adapter.py services/learning/trl/test_activation_smoke.py services/research/finrl/test_adapter.py services/research/finrl/test_production_drl_run.py services/research/rllib/test_adapter.py services/research/rllib/test_ray_tune_adapter.py services/research/rllib/test_production_ppo_run.py services/registry/experiments/test_adapter.py tests/integrations/test_wandb_sync.py services/optimizer-svc/test_persona_allocation_proposal_schema.py services/optimizer-svc/test_persona_allocation_proposal_store.py tests/e2e/test_multi_persona_ooda_packet.py -q`
- Result: 377 passed, 2 skipped, 5 subtests passed in 90.96s
- `python3 scripts/smoke_oss_activation_ready_matrix.py`
- Result: 16/16 passed; forbidden writes were `registry_write=false`, `governance_write=false`, `broker_write=false`, and `live_write=false`
- `python3 -m pytest tests/e2e/test_persona_abc_ooda_evidence_chain.py tests/governance/test_trl_proof_artifacts.py -q`
- Result: 6 passed in 1.03s

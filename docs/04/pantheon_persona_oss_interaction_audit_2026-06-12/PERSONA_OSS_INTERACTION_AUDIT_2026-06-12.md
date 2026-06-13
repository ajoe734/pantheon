# Persona OSS Runtime Interaction Audit

Date: 2026-06-12
Owner: Codex
Scope: persona-facing OSS request/result/OODA runtime paths

## Scope Definition

OSS in this document means open-source software components that Pantheon exposes
to personas through governed adapters. This audit is about whether a persona can
actually use those components, not just whether the adapter modules import.

In scope:

1. Persona request construction and dispatch.
2. The real governed OSS adapter entrypoint called by that request.
3. The real fixture or payload used by the adapter.
4. The result returned to the persona: artifacts, metrics, registry entry,
   experiment reference, or runtime handoff packet.
5. The response-driven OODA follow-up that the persona should take after the
   OSS result arrives.
6. Persona-side LEAN handoff materialization from OSS evidence, limited to the
   bootstrap/context packet that can be handed to execution review.

Out of scope:

- LEAN algorithm execution, LEAN Launcher process management, and LEAN internal
  order/event loops after a handoff packet exists.
- Broker adapter internals, broker SDK sessions, order routes, and order
  placement.
- The phrase "broker adapter internals" here means adapter SDK/session/order
  implementation after the persona has produced evidence or a handoff packet.
- Runtime ownership after execution review accepts a handoff.
- Capital approval, paper/canary/live promotion decisions, and downstream
  deployment authority beyond the persona result packet.

## Runtime Contract

The executable contract is implemented in `services/persona/oss_runtime.py` and
covered by `tests/e2e/test_persona_oss_runtime_matrix.py`.

Every persona-facing OSS interaction follows the same shape:

1. `PersonaOSSRequest` carries `persona_id`, `session_id`, `component`,
   `intent`, optional `payload`, and a generated `request_id`.
2. `run_persona_oss_request()` dispatches to the real repo adapter workflow for
   that component.
3. The adapter runs against a governed repo fixture or a concrete payload built
   by the persona harness.
4. `PersonaOSSResult` returns `status=completed`, `artifact_family`,
   `primary_output`, `metrics`, optional `registry_entry`, optional
   `artifact_bundle`, and evidence `refs`.
5. The OSS response is mapped into `persona_followup`, including OODA phase,
   next action, reason, and evidence refs.

The response matters as much as the request. A successful run is not complete
unless the persona can continue the OODA loop from the OSS output.

## Persona OSS Inventory

| Component | Status | Persona request | Real adapter/workflow called | Real payload or fixture | Persona receives | Response-driven OODA follow-up |
|---|---|---|---|---|---|---|
| `OpenClaw` | `governed` | Start or resume a persona runtime session | `SessionLifecycleStore.create_session()` from `services/openclaw-gateway-adapter/session_lifecycle.py` | Persona/session context with idempotency key | Active session record, upstream session id, audit event count | `observe` -> `continue_runtime_session` |
| `DSPy` | `governed` | Optimize persona prompt/policy behavior | `run_dspy_workflow()` | `services/learning/dspy/examples/preference_dataset_sample.json` | `prompt_bundle`, evaluation report, registry entry | `learn` -> `open_learning_candidate_review` |
| `imitation` | `governed` | Clone behavior from governed persona/trader trajectories | `run_imitation_workflow()` | `services/learning/imitation/examples/trajectory_dataset_sample.json` | Behavior policy, evaluation summary, registry entry | `learn` -> `open_learning_candidate_review` |
| `TRL` | `smoke-tested` | Train preference model from governed feedback events | `run_trl_dpo_workflow()` | Persona-built FB-002-like approve/reject/edit events | `model_artifact` projection, evaluator packet, candidate packet | `learn` -> `open_learning_candidate_review` |
| `Qlib` | `smoke-tested` | Produce supervised alpha evidence | `run_qlib_workflow()` | `services/research/qlib/examples/equity_dataset_sample.json` | Model payload, model artifact ref, evaluation report ref, registry entry | `decide` -> `draft_strategy_proposal` |
| `vectorbt` | `governed` | Backtest a strategy template on historical bars | `run_vectorbt_workflow()` | `services/research/vectorbt/examples/strategy_dataset_sample.json` with 35 bars each for `ALPHA` and `BETA` | Backtest result, aggregate/per-instrument metrics, registry entry | `decide` -> `draft_strategy_proposal` |
| `statsmodels` | `governed` | Analyze regime/factor evidence | `run_statsmodels_workflow()` | `services/research/statsmodels/examples/regime_dataset_sample.json` | Results summary, analysis path, registry entry | `orient` -> `attach_risk_or_regime_interpretation` |
| `QuantLib` | `governed` | Price option/bond risk evidence | `run_quantlib_workflow()` | `services/research/quantlib/examples/pricing_dataset_sample.json` | Pricing/risk summary, analysis path, registry entry | `orient` -> `attach_risk_or_regime_interpretation` |
| `FinRL` | `smoke-tested` | Train offline RL policy evidence | `run_finrl_workflow()` | `services/research/finrl/examples/policy_dataset_sample.json` | RL policy, evaluation summary, registry entry | `learn` -> `open_learning_candidate_review` |
| `RLlib` | `smoke-tested` | Train/evaluate offline RL policy evidence | `run_rllib_workflow()` | `services/research/rllib/examples/train_eval_input_sample.json` | RL policy, rollout/evaluation summary, registry entry | `learn` -> `open_learning_candidate_review` |
| `Ray Tune` | `smoke-tested` | Search RL hyperparameters | `run_ray_tune_workflow()` | `services/research/rllib/examples/train_eval_input_sample.json` | Optimizer result, top trials, registry entry | `learn` -> `open_learning_candidate_review` |
| `MLflow` | `governed` | Track persona-cited experiment evidence | `RegistryExperimentAdapter.sync_registry_entry()` with `InMemoryMlflowBackend` | Registry entry produced from a real vectorbt run | MLflow experiment ref, metrics, artifact refs | `observe` -> `cite_experiment_ref` |
| `W&B` | `activation-gated` | Track persona-cited experiment evidence in offline W&B form | `RegistryExperimentAdapter.sync_registry_entry()` with `OfflineWandbLocalBackend` | Registry entry produced from a real vectorbt run | Offline W&B run ref, local artifact refs, metrics | `observe` -> `cite_experiment_ref` |
| `lean_handoff` | persona-side handoff | Materialize runtime handoff from OSS evidence | vectorbt run -> MLflow sync -> `materialize_runtime_bootstrap_request()` -> `PantheonRuntimeContext.from_mapping()` | Approved paper-stage projection of the vectorbt registry entry | Runtime bootstrap request, runtime env, runtime context, MLflow ref | `act` -> `submit_runtime_handoff_for_execution_review` |

`lean_handoff` is not an OSS backend. It is included because it proves the
usable end-to-end persona path from OSS research evidence into an execution
handoff packet without describing LEAN or broker internals.

## Response To OODA Mapping

| OSS response family | Components | Persona OODA phase | Persona next action |
|---|---|---|---|
| Runtime session active | `OpenClaw` | `observe` | Continue the runtime session with the active session id and audit refs |
| Backtest/alpha evidence returned | `vectorbt`, `Qlib` | `decide` | Draft a strategy proposal using the returned evidence refs |
| Risk/regime analysis returned | `statsmodels`, `QuantLib` | `orient` | Attach interpretation to the persona's market/risk context |
| Learning or policy artifact returned | `DSPy`, `imitation`, `TRL`, `FinRL`, `RLlib`, `Ray Tune` | `learn` | Open candidate review for persona or policy improvement |
| Experiment ref returned | `MLflow`, `W&B` | `observe` | Cite experiment reference in evidence packet or proposal |
| Runtime handoff packet returned | `lean_handoff` | `act` | Submit bootstrap/context packet for execution review |

## Complete Runtime Scenarios

### 1. OpenClaw Runtime Session

Persona `persona-alpha` sends `component=openclaw` with an intent to create a
runtime research session. The harness loads the OpenClaw session lifecycle store,
creates the session with persona context and idempotency key, and returns an
active session record. The response moves persona to `observe` with
`continue_runtime_session`.

### 2. DSPy Persona Optimization

Persona sends `component=dspy` to optimize prompt/policy behavior. DSPy runs the
preference dataset fixture and returns a real `prompt_bundle`, evaluation
metrics, and registry entry. The response moves persona to `learn` with
`open_learning_candidate_review`.

### 3. Imitation Behavior Cloning

Persona sends `component=imitation` to learn from governed trajectories. The
imitation adapter runs the trajectory fixture and returns a behavior policy,
evaluation summary, and registry entry. The response moves persona to `learn`.

### 4. TRL Preference Learning

Persona sends `component=trl` with governed feedback-like events. TRL builds
preference pairs, runs DPO workflow, emits a `model_artifact` registry
projection, and returns evaluator/candidate packets. The response moves persona
to `learn` and gives evidence refs for candidate review.

### 5. Qlib Supervised Alpha

Persona sends `component=qlib` to train supervised alpha evidence. Qlib runs the
equity dataset fixture, emits model payload, evaluation summary, model artifact
ref, evaluation report ref, candidate packet, and registry entry. The response
moves persona to `decide` so a strategy proposal can cite those refs.

### 6. vectorbt Historical Backtest

Persona sends `component=vectorbt` with a moving-average strategy template. The
adapter runs the historical fixture with `ALPHA` and `BETA`, 35 bars per
instrument, and strategy params `short_window=5`, `long_window=20`. The persona
receives aggregate metrics including non-zero trades and per-instrument metrics.
The response moves persona to `decide`.

### 7. statsmodels Regime Interpretation

Persona sends `component=statsmodels` for factor/regime evidence. The adapter
runs the regime dataset fixture as a `GovernedDataset` and returns analysis
summary, result count, analysis path, and registry entry. The response moves
persona to `orient`.

### 8. QuantLib Pricing/Risk Interpretation

Persona sends `component=quantlib` for derivative/fixed-income pricing evidence.
The adapter runs the governed pricing snapshot fixture and returns pricing/risk
summary, result count, analysis path, and registry entry. The response moves
persona to `orient`.

### 9. FinRL Offline Policy Evidence

Persona sends `component=finrl` for offline RL evidence. FinRL runs the policy
dataset fixture, returns policy payload, evaluation summary, candidate packet,
and registry entry. The response moves persona to `learn`.

### 10. RLlib Offline Train/Eval Evidence

Persona sends `component=rllib` for offline train/eval evidence. RLlib runs the
train/eval fixture and returns policy payload, rollout summary, evaluation
summary, candidate packet, and registry entry. The response moves persona to
`learn`.

### 11. Ray Tune Optimizer Evidence

Persona sends `component=ray_tune` for hyperparameter search. Ray Tune runs
search over the RLlib fixture, returns optimizer summary, top trial payloads,
candidate artifacts, and registry entry. The response moves persona to `learn`.

### 12. MLflow Experiment Tracking

Persona sends `component=mlflow` after vectorbt has produced registry-ready
evidence. The harness runs vectorbt first, then syncs the registry entry to the
in-memory MLflow backend. The persona receives an MLflow experiment ref, run id,
metrics, and artifact refs. The response moves persona to `observe` so it can
cite the run.

### 13. W&B Offline Experiment Tracking

Persona sends `component=wandb` after vectorbt has produced registry-ready
evidence. The harness runs vectorbt first, then syncs the registry entry to the
offline W&B local backend. The persona receives an offline run ref, local store
path, metrics, and artifact refs. The response moves persona to `observe`.

### 14. vectorbt -> MLflow -> LEAN Handoff Packet

Persona sends `component=lean_handoff`. The harness runs vectorbt on the
historical fixture, marks the resulting registry entry as `approved` for `paper`
stage, syncs it to MLflow, materializes a runtime bootstrap request, and validates
that request through `PantheonRuntimeContext.from_mapping()`. The persona
receives a runtime bootstrap request, runtime env, runtime context, MLflow ref,
and source vectorbt metrics. The response moves persona to `act`.

This scenario stops at the handoff packet. It does not launch LEAN, inspect LEAN
algorithm internals, place orders, or touch broker adapters.

## 100 Alpha-Seed Round-Trip Spec Matrix

The executable 100-case matrix is
`tests/e2e/test_persona_oss_100_alpha_seed_roundtrips.py`. It is intentionally
not 100 copies of vectorbt. It distributes persona requests across every
persona-facing OSS/component path and asserts that each response returns enough
component-specific evidence for the persona's OODA follow-up.

It exercises 100 persona -> OSS -> persona round trips. Each spec case has a
unique `spec_id`, `intent`, `assertion_label`, payload fingerprint, and alpha
seed binding. Every case calls `run_persona_oss_request()` with a concrete
`PersonaOSSRequest`, then checks the returned `PersonaOSSResult`,
persona/session/request identity, OODA phase, next action, evidence refs, and
component-specific payload echo or lineage.

| Component | Case count | Distinct persona assertion focus |
|---|---:|---|
| `openclaw` | 8 | Session type, operator id, active upstream state, alpha seed context bundle |
| `dspy` | 7 | Prompt bundle strategy/version and parent seed spec lineage |
| `imitation` | 7 | Behavior policy registry lineage, epoch count, training seed |
| `trl` | 7 | Preference model strategy id, beta, strategy family, feedback prefix |
| `qlib` | 8 | Supervised alpha config, estimator/leaves values, usable metrics |
| `vectorbt` | 10 | Historical backtest template windows, cash, fees, instrument metrics |
| `statsmodels` | 7 | Regime dataset metadata, series suffixing, factor/price counts |
| `quantlib` | 7 | Pricing dataset metadata, valuation date, shifted instrument ids |
| `finrl` | 7 | Offline RL policy lineage, seed, learning rate, lookback window |
| `rllib` | 7 | Offline train/eval policy lineage, seed, learning rate, lookback window |
| `ray_tune` | 6 | Optimizer id, search strategy, trial count, top-k, trigger |
| `mlflow` | 7 | vectorbt-produced registry entry synced to MLflow tracking URI |
| `wandb` | 6 | vectorbt-produced registry entry synced to offline W&B local store |
| `lean_handoff` | 6 | vectorbt -> MLflow evidence materialized into runtime handoff packet |

The matrix is backed by existing alpha strategy seed sources in the repository,
not invented strategy ids:

- `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`
- `tests/e2e/fixtures/strategy_spec_for_experiment.json`
- `tests/e2e/fixtures/experiment_run_for_admission.json`
- `tests/e2e/fixtures/candidate_artifact_for_decision.json`
- `tests/e2e/test_persona_abc_ooda_evidence_chain.py`
- `services/source_ingestion/tests/test_strategy_seed_builder.py`

The test file verifies those source files exist and contain the expected seed
anchors before it runs the 100 round trips.

## Existing Adapter Proof References

These tests remain the component-level proof set beneath the persona runtime
matrix:

- `services/openclaw-gateway-adapter/test_session_lifecycle.py`
- `services/openclaw-gateway-adapter/test_tool_workflow_bridge.py`
- `services/learning/dspy/test_adapter.py`
- `services/learning/imitation/test_adapter.py`
- `services/learning/trl/test_adapter.py`
- `services/learning/trl/test_activation_smoke.py`
- `services/research/qlib/test_adapter.py`
- `services/research/qlib/test_rolling_pipeline.py`
- `tests/governance/test_qlib_proof_artifacts.py`
- `services/research/vectorbt/test_adapter.py`
- `services/research/statsmodels/test_adapter.py`
- `tests/governance/test_statsmodels_proof_artifacts.py`
- `services/research/quantlib/test_adapter.py`
- `tests/governance/test_quantlib_proof_artifacts.py`
- `services/research/finrl/test_adapter.py`
- `services/research/finrl/test_production_drl_run.py`
- `services/research/rllib/test_adapter.py`
- `services/research/rllib/test_production_ppo_run.py`
- `services/research/rllib/test_ray_tune_adapter.py`
- `services/registry/experiments/test_adapter.py`
- `tests/integrations/test_wandb_sync.py`

## Validation

Current branch validation:

- `python3 -m pytest tests/e2e/test_persona_oss_100_alpha_seed_roundtrips.py -q`
- Result: `101 passed in 6.82s`
- `python3 -m pytest tests/docs/test_persona_oss_interaction_audit.py tests/e2e/test_persona_oss_100_alpha_seed_roundtrips.py -q`
- Result: `109 passed in 11.83s`
- `python3 -m pytest tests/e2e/test_persona_oss_runtime_matrix.py -q`
- Result: `7 passed in 0.72s`
- `python3 -m pytest tests/e2e/test_persona_oss_runtime_matrix.py tests/e2e/test_persona_oss_100_alpha_seed_roundtrips.py tests/docs/test_persona_oss_interaction_audit.py services/openclaw-gateway-adapter/test_session_lifecycle.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py services/learning/dspy/test_adapter.py services/learning/imitation/test_adapter.py services/learning/trl/test_adapter.py services/research/qlib/test_adapter.py services/research/vectorbt/test_adapter.py services/research/statsmodels/test_adapter.py services/research/quantlib/test_adapter.py services/research/finrl/test_adapter.py services/research/rllib/test_adapter.py services/research/rllib/test_ray_tune_adapter.py services/registry/experiments/test_adapter.py tests/integrations/test_wandb_sync.py -q`
- Result: `417 passed, 1 skipped, 5 subtests passed in 50.79s`

The E2E tests assert that every component in `PERSONA_OSS_COMPONENTS` produces a
completed `PersonaOSSResult`, non-empty primary output, expected OODA follow-up,
and component-specific usable evidence. The vectorbt test verifies a real
historical fixture backtest with 35 bars each for `ALPHA` and `BETA`. The MLflow
and W&B tests verify experiment tracking from a vectorbt-produced registry entry.
The LEAN handoff test verifies a runtime bootstrap/context packet built from
vectorbt plus MLflow evidence.

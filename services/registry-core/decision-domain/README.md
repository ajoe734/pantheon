# Decision Domain — Five-Stage Decision Chain

Owner plane: Decision Plane (front-half)
Task: `BG-003` — formalize decision-front objects and adjudication boundaries

This directory is the canonical contract for the front-half decision objects:

`RegimeState -> UniverseSelection -> SignalInference -> AllocationDecision -> RiskAdjudication`

Stage 5 closes the front-half chain by linking to the back-half governance object `ApprovalDecision` in `services/control-plane/governance/`.

## Contract Rules

1. The JSON schemas in this directory are the source of truth for field names and required relationships.
2. The example files under `examples/` must validate against those schemas.
3. `examples/five_stage_chain.json` is the canonical end-to-end replay packet. The single-stage example files mirror the same payloads.
4. These objects are first-class decision provenance, not raw research artifacts or governance approvals.
5. BG-000 market scope and BG-001 lineage/master references must remain visible in the contract where they materially affect replay.

## Object Map

### Stage 1 — `RegimeState`

Captures the inferred market regime over an evaluation window.

| Canonical field | Purpose |
|---|---|
| `regime_id` | Unique ID for the regime inference |
| `strategy_id` | Strategy this inference belongs to |
| `artifact_id` | Artifact or model build that produced the inference |
| `version` | Semver of this record shape/version |
| `regime_class` | Final regime label consumed downstream |
| `confidence` | Confidence in the selected regime label |
| `window_start` / `window_end` | Observation window used for the inference |
| `input_refs` | Dataset, feature, master, and calendar inputs |
| `output_refs` | Downstream decision objects, typically `universe_selection` |
| `model_ref` | Model provenance that distinguishes the decision record from research inputs |
| `decision_reasoning` | Human-readable explanation of the decision |

Optional context: `regime_scores`, `feature_snapshot`, `market_scope`, `state`, `superseded_by`, `persona_id`, `capital_pool_id`, `metadata`.

### Stage 2 — `UniverseSelection`

Records the eligible tradable set under the active regime.

| Canonical field | Purpose |
|---|---|
| `universe_id` | Unique ID for the selected universe |
| `regime_ref` | Cached pointer to the upstream `RegimeState` |
| `selected_universe` | Selected instruments with `symbol`, `asset_class`, BG-001 refs, and inclusion rationale |
| `exclusion_reasons` | Explicit audit trail for excluded candidates |
| `input_refs` | Regime, master, dataset, risk-budget, and calendar inputs |
| `output_refs` | Downstream decision objects, typically `signal_inference` |
| `decision_reasoning` | Why this universe was chosen |

Optional context: `model_ref`, `universe_stats`, `state`, `superseded_by`, `persona_id`, `capital_pool_id`, `metadata`.

### Stage 3 — `SignalInference`

Transforms the selected universe into per-instrument trading directives.

| Canonical field | Purpose |
|---|---|
| `signal_id` | Unique ID for this signal batch |
| `regime_ref` | Cached regime context |
| `universe_ref` | Cached pointer to the upstream `UniverseSelection` |
| `signals` | Per-instrument directives with `symbol`, `direction`, `magnitude`, `confidence`, and optional BG-000/BG-001 context |
| `input_refs` | Regime, universe, dataset, and feature inputs |
| `output_refs` | Downstream decision objects, typically `allocation_decision` |
| `model_ref` | Signal-model provenance |
| `decision_reasoning` | Why these signals were emitted |

Optional context: `inference_stats`, inherited `market_scope`, `state`, `superseded_by`, `persona_id`, `capital_pool_id`, `metadata`.

### Stage 4 — `AllocationDecision`

Converts signals into a portfolio allocation proposal. This is a calculation object, not a governance approval.

| Canonical field | Purpose |
|---|---|
| `allocation_id` | Unique ID for the allocation proposal |
| `regime_ref` | Cached regime context for replay and filtering |
| `signal_ref` | Cached pointer to the upstream `SignalInference` |
| `allocations` | Per-instrument targets with `target_weight`, `target_value`, and constraint annotations |
| `portfolio_summary` | Portfolio-level exposure and turnover summary |
| `input_refs` | Regime, universe, signal, budget, capital-pool, and current-portfolio inputs |
| `output_refs` | Downstream decision objects, typically `risk_adjudication` |
| `model_ref` | Allocation methodology provenance |
| `decision_reasoning` | Why the proposal differs from raw signals, if at all |

Optional context: inherited `market_scope`, `state`, `superseded_by`, `persona_id`, `capital_pool_id`, `metadata`.

### Stage 5 — `RiskAdjudication`

Assesses the allocation proposal against risk policy before the back-half governance gate.

| Canonical field | Purpose |
|---|---|
| `adjudication_id` | Unique ID for the adjudication record |
| `allocation_ref` | Cached pointer to the upstream `AllocationDecision` |
| `risk_assessment` | Structured risk checks and stress-test results |
| `verdict` | Risk verdict: `approved`, `approved_with_conditions`, `requires_revision`, or `rejected` |
| `input_refs` | Allocation plus any direct dataset, master, capital-pool, or policy evidence used by the risk engine |
| `output_refs` | Downstream governance objects such as `approval_decision` and `deployment_plan` |
| `decision_reasoning` | Why the verdict was reached |

Optional context: `conditions`, inherited `market_scope`, `model_ref`, `state`, `superseded_by`, `persona_id`, `capital_pool_id`, `metadata`.

## Provenance Chain

The canonical replay chain is:

```text
RegimeState.regime_id = "regime-20260413-001"
  -> UniverseSelection.regime_ref.regime_id
UniverseSelection.universe_id = "universe-20260413-001"
  -> SignalInference.universe_ref.universe_id
SignalInference.signal_id = "signal-20260413-001"
  -> AllocationDecision.signal_ref.signal_id
AllocationDecision.allocation_id = "alloc-20260413-001"
  -> RiskAdjudication.allocation_ref.allocation_id
RiskAdjudication.adjudication_id = "risk-20260413-001"
  -> RiskAdjudication.output_refs[*].ref_id = "approval-20260413-001"
ApprovalDecision.decision_id = "approval-20260413-001"
```

Every stage also preserves `input_refs` and `output_refs`, so the chain can be traversed forward or backward without depending on implicit runtime state.

## Example Files

- `examples/five_stage_chain.json` — end-to-end replay packet for a US equities momentum family
- `examples/regime_state_example.json`
- `examples/universe_selection_example.json`
- `examples/signal_inference_example.json`
- `examples/allocation_decision_example.json`
- `examples/risk_adjudication_example.json`

The five single-stage examples are intentionally the same payloads embedded inside `five_stage_chain.json`, which prevents the example surfaces from drifting.

## Validation

Run either validator from the repo root:

```bash
python3 services/registry-core/decision-domain/validate_schemas.py
python3 scripts/validate_bg003.py
```

`validate_schemas.py` performs Draft-07 checks, validates every single-stage example, validates every stage inside `five_stage_chain.json`, verifies cross-stage reference closure, and confirms the single-stage examples mirror the chain payloads.

## Acceptance Snapshot

- [x] Five decision-front object schemas exist and validate as Draft-07
- [x] README field map matches the schema field names
- [x] Example payloads validate against the published schemas
- [x] A complete five-stage provenance chain is documented and replayable
- [x] Allocation and approval remain separate concerns
- [x] Risk adjudication and governance rejection remain separate concerns
- [x] BG-000 market scope vocabulary is visible in the front-half contract
- [x] BG-001 dataset/master references are visible where replay needs them

# Decision Domain — Five-Stage Decision Chain

**Owner Plane:** Decision Plane (front-half)
**Schema directory:** `services/registry-core/decision-domain/`
**Closure task:** `BG-003` — Formalize decision-front objects and adjudication boundaries

---

## Overview

This directory defines the **five-stage decision chain** that forms the front-half of the Pantheon decision pipeline. Every trading decision flows through these five stages in order, producing an auditable provenance chain that can be replayed, attributed, and debugged.

```
RegimeState → UniverseSelection → SignalInference → AllocationDecision → RiskAdjudication
   (Stage 1)       (Stage 2)           (Stage 3)          (Stage 4)           (Stage 5)
```

The output of Stage 5 (RiskAdjudication) flows downstream to the **back-half governance gate** (`ApprovalDecision` in `services/control-plane/governance/`), which then triggers `DeploymentPlan`, `RuntimeBinding`, and eventual LEAN execution.

## Design Principles

1. **First-class auditable objects.** Each stage produces a versioned JSON document with unique IDs, upstream/downstream references, lifecycle state, and decision reasoning.
2. **Provenance chain.** Every object references its immediate upstream input(s) and downstream consumer(s), enabling end-to-end replay from regime inference through risk adjudication.
3. **BG-000 / BG-001 alignment.** Objects reference the BG-000 market scope vocabulary (market_class, instrument_type) and BG-001 data plane IDs (SecurityMaster, ContractMaster, DatasetVersion) where applicable.
4. **Back-half separation.** These are *decision calculation/validation* objects, distinct from the back-half *governance/approval* objects. `AllocationDecision` is a portfolio weight proposal; `ApprovalDecision` is the governance yes/no. `RiskAdjudication` is a risk validation; it is not a governance rejection.
5. **Lifecycle states.** Each object carries a `decision_state` (or `allocation_state` / `adjudication_state`) that tracks it through `proposed` → `validated` → `active` → `superseded` / `invalidated`.

---

## Object Summaries

### Stage 1 — RegimeState

**File:** `regime_state.schema.json`

Captures the inferred market regime at a point in time. Drives everything downstream.

| Required field | Purpose |
|---|---|
| `regime_id` | Unique instance ID |
| `strategy_id` | Owning strategy |
| `version` | Semver of the inference artifact |
| `evaluated_at` | RFC 3339 evaluation timestamp |
| `regime_classification` | Enum: trending, mean_reverting, high_volatility, crisis, etc. |
| `confidence` | [0, 1] confidence score |
| `input_refs` | Upstream datasets / features used |
| `decision_reasoning` | Human-readable reasoning |

**Key relationships:**
- **Upstream:** feature_dataset, normalized_dataset, experiment_result, telemetry_summary
- **Downstream:** UniverseSelection (via `output_refs`)
- **Optional:** market_scope_refs (BG-000 vocabulary), model_ref (HMM, clustering, etc.)

---

### Stage 2 — UniverseSelection

**File:** `universe_selection.schema.json`

Records which instruments were selected for trading given a market regime.

| Required field | Purpose |
|---|---|
| `universe_id` | Unique instance ID |
| `strategy_id` | Owning strategy |
| `version` | Semver of the selection artifact |
| `evaluated_at` | RFC 3339 evaluation timestamp |
| `regime_ref` | Reference to the upstream RegimeState |
| `selected_instruments` | Array of instrument_type + instrument_id (BG-001 SecurityMaster/ContractMaster IDs) |
| `input_refs` | Upstream datasets / regime_state used |
| `decision_reasoning` | Human-readable reasoning |

**Key relationships:**
- **Upstream:** RegimeState (via `regime_ref`), feature_dataset, security_master
- **Downstream:** SignalInference (via `output_refs`)
- **Optional:** universe_criteria (min_liquidity, max_instruments, etc.), model_ref

---

### Stage 3 — SignalInference

**File:** `signal_inference.schema.json`

Records the inferred trading signals (alpha, direction, magnitude) for instruments in the selected universe.

| Required field | Purpose |
|---|---|
| `signal_id` | Unique instance ID |
| `strategy_id` | Owning strategy |
| `version` | Semver of the inference artifact |
| `evaluated_at` | RFC 3339 evaluation timestamp |
| `universe_ref` | Reference to the upstream UniverseSelection |
| `signals` | Array of instrument_id + signal_type + signal_value |
| `input_refs` | Upstream datasets / regime_state / universe_selection used |
| `decision_reasoning` | Human-readable reasoning |

**Key relationships:**
- **Upstream:** UniverseSelection (via `universe_ref`), feature_dataset, regime_state
- **Downstream:** AllocationDecision, RiskAdjudication (via `output_refs`)
- **Signal types:** alpha, direction, mean_reversion, momentum, statistical_arbitrage, sentiment, macro, volatility
- **Optional:** model_ref (linear_regression, tree_ensemble, neural_network, factor_model, etc.), decay_estimate

---

### Stage 4 — AllocationDecision

**File:** `allocation_decision.schema.json`

Translates signal scores into concrete portfolio weight/size proposals. This is a **calculation** object, not a governance gate.

| Required field | Purpose |
|---|---|
| `allocation_decision_id` | Unique instance ID |
| `strategy_id` | Owning strategy |
| `artifact_id` | Model/artifact that produced the allocation |
| `version` | Semver of the allocation artifact |
| `evaluated_at` | RFC 3339 evaluation timestamp |
| `signal_inference_id` | The SignalInference that gates this allocation |
| `allocations` | Array of instrument_id + weight + direction + notional_target |
| `input_refs` | Upstream signal_inference, universe_selection, regime_state, capital_pool |
| `output_refs` | Downstream risk_adjudication |
| `decision_reasoning` | Human-readable reasoning |

**Key relationships:**
- **Upstream:** SignalInference (via `signal_inference_id`), risk_budget, capital_pool
- **Downstream:** RiskAdjudication (via `output_refs`)
- **Allocation methods:** equal_weight, signal_proportional, risk_parity, mean_variance, black_litterman, kelly_criterion, volatility_targeting
- **Lifecycle state:** `allocation_state` (proposed, accepted, rejected_by_risk, superseded, invalidated)

---

### Stage 5 — RiskAdjudication

**File:** `risk_adjudication.schema.json`

Validates the AllocationDecision against risk constraints before it proceeds to the governance approval gate.

| Required field | Purpose |
|---|---|
| `risk_adjudication_id` | Unique instance ID |
| `strategy_id` | Owning strategy |
| `artifact_id` | Risk engine artifact that produced the adjudication |
| `version` | Semver of the adjudication artifact |
| `evaluated_at` | RFC 3339 evaluation timestamp |
| `allocation_decision_id` | The AllocationDecision being validated |
| `risk_outcome` | approved, approved_with_conditions, rejected, requires_manual_review |
| `input_refs` | Upstream allocation_decision, signal_inference, stress_scenario_set |
| `output_refs` | Downstream approval_decision, deployment_plan |
| `decision_reasoning` | Human-readable reasoning |

**Key relationships:**
- **Upstream:** AllocationDecision (via `allocation_decision_id`), stress_scenario_set, correlation_matrix
- **Downstream:** ApprovalDecision (back-half governance gate), DeploymentPlan
- **Risk checks:** max_position_size, portfolio_var_limit, sector_concentration, drawdown_limit, stress_test_scenario, etc.
- **Lifecycle state:** `adjudication_state` (proposed, accepted, escalated, superseded, invalidated)

---

## End-to-End Provenance Chain

A complete decision provenance for one evaluation cycle looks like:

```
RegimeState.regime_id = "regime-20260413-001"
  ↓ UniverseSelection.regime_ref.regime_id = "regime-20260413-001"
UniverseSelection.universe_id = "univ-20260413-001"
  ↓ SignalInference.universe_ref.universe_id = "univ-20260413-001"
SignalInference.signal_id = "signal-20260413-001"
  ↓ AllocationDecision.signal_inference_id = "signal-20260413-001"
AllocationDecision.allocation_decision_id = "alloc-20260413-001"
  ↓ RiskAdjudication.allocation_decision_id = "alloc-20260413-001"
RiskAdjudication.risk_adjudication_id = "risk-20260413-001"
  ↓ RiskAdjudication.output_refs[0].ref_id = "approval-20260413-001"
ApprovalDecision.decision_id = "approval-20260413-001"  ← back-half governance gate
```

Each object's `input_refs` and `output_refs` arrays create a **bidirectional graph** that can be traversed forward (what did this decision produce?) or backward (what inputs drove this decision?).

---

## Relationship to Back-Half Objects

| Front-half object | Back-half consumer | Relationship |
|---|---|---|
| `RiskAdjudication` | `ApprovalDecision` | Risk outcome feeds governance decision |
| `AllocationDecision` | `DeploymentPlan` | Approved allocations become deployment targets |
| All five stages | `TelemetryEvent` | Telemetry events carry decision object IDs for attribution |
| All five stages | `IncidentCase` | Incidents reference the decision chain at time of failure |
| All five stages | `EvolutionDecision` | Evolution actions reference the decision chain for root cause |

---

## Schema Validation

All five schemas conform to JSON Schema Draft-07 and share a consistent structure:

- Unique instance ID field (stage-specific naming)
- `strategy_id`, `artifact_id`, `version` (semver), `evaluated_at` (RFC 3339)
- `input_refs` array with typed references and optional storage_ref
- `output_refs` array pointing to downstream consumers
- `decision_reasoning` (non-empty string, required for provenance)
- `model_ref` (optional, with model_type enum per domain)
- Lifecycle state enum with `superseded_by` forward pointer
- Optional `persona_id`, `capital_pool_id` for scoping
- Conditional `allOf` rules enforcing cross-field constraints

---

## Acceptance Criteria for BG-003

- [x] Five decision-front object schemas defined and validated
- [x] Decision Layer Object Map documented (this file)
- [x] Provenance chain documented with forward/backward traversal
- [x] Back-half separation clarified (AllocationDecision ≠ ApprovalDecision; RiskAdjudication ≠ governance rejection)
- [x] BG-000 market scope vocabulary referenced (instrument_type, market_class, region)
- [x] BG-001 data plane object IDs referenced (SecurityMaster, ContractMaster, DatasetVersion)
- [x] TARGET_ARCHITECTURE.md updated to list decision-front objects in core canonical objects

# Qlib Activation Criteria

**Task**: OSS-003 (Qlib path)
**Owner**: Qwen
**Reviewer**: Codex
**Scope**: Define hard entry criteria for Microsoft Qlib activation as the supervised alpha research path
**Status**: APPROVED for OSS-003 activation-gate lock
**Last Updated**: 2026-05-01

---

## Executive Summary

This document defines the activation criteria, workflow, and governance constraints for **Microsoft Qlib** as the supervised alpha research engine in the Pantheon platform.

**Key Principle**: Qlib is the **first learning framework to activate** in the research pipeline. It provides supervised alpha signal discovery and feature engineering. RL and TRL paths are explicitly deferred until Qlib reaches diminishing returns on its targets.

---

## 1. Entry Criteria for Qlib Activation

### When to Activate Qlib

Qlib is justified when:

1. **Baseline Strategy Exists**: A `StrategySpec` candidate has passed the replication gate (RS-003) and entered the registry as a `candidate` artifact.
   - The strategy has a defined alpha target (e.g., cross-sectional equity signal, factor model).
   - It currently relies on hand-crafted features or simple heuristics.

2. **Feature Engineering Need**: The strategy would benefit from systematic feature engineering, including:
   - Technical indicators across multiple timeframes.
   - Cross-sectional factor construction (momentum, value, quality, volatility).
   - Label generation for supervised learning (e.g., forward returns, rank labels).

3. **Sufficient Data Depth**: A minimum of **2+ years** of daily or intraday OHLCV data is available for the target universe.
   - Data must include: open, high, low, close, volume, and optionally order-book snapshots.
   - Universe size: at least 50+ instruments for cross-sectional models.

4. **Supervised Learning is Appropriate**: The alpha target can be framed as a prediction problem (e.g., predict next-day returns, rank assets by expected return).
   - Non-example: Sequential decision-making with state-dependent actions → use RL path (LP-005).
   - Non-example: Preference learning from human feedback → use TRL path (LP-004).

5. **No Conflicting Upstream Constraints**: The Qlib version and dependencies do not conflict with already-integrated packages (DSPy, imitation, MLflow).

---

## 2. Qlib Workflow Design

### 2.1 Workflow Overview

```
Governance Layer (FB-001 filtering)
    ↓
Research Intake → RS-001 (Ingestion) → RS-002 (Normalization)
    ↓
Replication Gate (RS-003: validates research candidate)
    ↓
Qlib Candidate Selection (this layer)
    ↓
Qlib Data Handler + Feature Pipeline
    ↓
Model Training (LightGBM / LSTM / Transformer)
    ↓
Backtesting via Qlib's built-in backtester
    ↓
Output: Alpha Signal + Feature Importance
    ↓
Registry Admission (REG-001 gate) + Qlib-specific constraints
    ↓
Registry progression (`draft` → `candidate` → `approved`)
    ↓
Deployment planning (`none` → `paper` / `canary` / `live`)
    ↓
LEAN Execution (signal consumed as alpha input once the approved artifact is staged)
```

### 2.2 Candidate Selection

**Input**: Approved research strategies from RS-003 (replication-gate-passed strategy candidates).

**Filter**: Apply entry criteria (§1) to determine if the candidate should use Qlib or skip to a simpler baseline.

**Output**: Qlib candidates identified with:
- Problem statement (e.g., "cross-sectional equity alpha signal")
- Data sources (tickers, OHLCV frequency, historical depth)
- Label specification (e.g., 5-day forward return, rank-based labels)
- Model family (LightGBM recommended for v1; deep models deferred)

### 2.3 Data Handler

#### Data Format

Qlib requires data in its native format or a compatible adapter. The Pantheon adapter must:

1. **Convert** Pantheon-governed data sources into Qlib-compatible format without bypassing governance boundaries.
2. **Preserve lineage**: Every Qlib dataset must reference the source registry IDs and dataset refs.
3. **Filter by governance**: Only governed dataset refs or approved upstream artifacts may feed the adapter; no ad hoc local files, notebook exports, or direct live-runtime side channels may bypass lineage and registry boundaries.
4. **Attach production-data proof before candidate handoff**: first production-data activation packets must name provider/source class, entitlement/license and allowed-use terms, freshness, point-in-time fields, durable storage refs/checksum, rate-limit/audit refs, and explicit no-order-route controls.

#### Minimum Data Requirements

| Field | Required | Notes |
|---|---|---|
| `$open` | Yes | OHLCV standard |
| `$high` | Yes | |
| `$low` | Yes | |
| `$close` | Yes | |
| `$volume` | Yes | |
| `$factor` | Optional | Adjustment factor for splits/dividends |
| `$change` | Optional | Pre-computed returns |

#### Universe Specification

- **Default**: Top 100 instruments by market cap or liquidity.
- **Minimum**: 50 instruments for cross-sectional validity.
- **Frequency**: Daily (v1); intraday (deferred to v1.5+).

### 2.4 Model Training

#### v1 Model: LightGBM

**Rationale**: LightGBM is the most sample-efficient, interpretable, and robust baseline for tabular financial data. It should be the default model for the first Qlib activation.

**Configuration**:
```yaml
model:
  class: LGBModel
  kwargs:
    loss: mse
    num_leaves: 63
    max_depth: 8
    learning_rate: 0.05
    n_estimators: 200
    subsample: 0.8
    colsample_bytree: 0.8
    random_state: 42
```

**Feature Set** (v1 recommended):
- Price-based: momentum (5d, 10d, 20d), volatility (20d), mean reversion.
- Volume-based: volume change, volume-price divergence.
- Cross-sectional: rank-based z-scores within universe.
- Technical: RSI, MACD, Bollinger Band width (limited set, avoid overfitting).

**Deferred Models** (require Qlib v1.5+ activation):
- LSTM/GRU sequence models.
- Transformer-based models (TCTS, HIST).
- Reinforcement learning integration within Qlib.

### 2.5 Backtesting

Qlib's built-in backtester provides:

- **Top-K strategy**: Select top-K assets by predicted score, equal-weight allocation.
- **Metrics**: Annualized return, Sharpe ratio, max drawdown, information ratio, turnover.
- **Benchmark**: Market-cap weighted or equal-weight universe benchmark.

**Minimum Backtest Requirements**:
- Train/validation/test split by time (no leakage).
- At least 6 months of held-out test data.
- Report: Sharpe ratio, max drawdown, annualized return, turnover rate, IC/IR.

---

## 3. Registry and Deployment Constraints

### 3.1 Artifact Model for Qlib Alpha Signals

Each Qlib alpha artifact follows the canonical registry target shape from `services/registry/contract.md`.
During the LP-003 / EX-001 compatibility window, downstream code may still mirror legacy `lifecycle_state` or
`promotion_state` fields, but this activation gate treats `artifact_state` and `deployment_summary.current_stage`
as the source-of-truth vocabulary.

```json
{
  "registry_id": "qlib-alpha-equity-cross-sectional-v1.0.0",
  "artifact_type": "model_artifact",
  "strategy_id": "equity_cross_sectional_alpha",
  "version": "1.0.0",
  "artifact_state": "draft",
  "created_at": "2026-04-15T10:00:00Z",
  "lineage": {
    "parent_registry_ids": [],
    "source_run_ids": ["qlib-run-2026-04-15-equity-alpha"],
    "source_dataset_refs": ["dataset:equity-universe-2023-2025-daily"],
    "source_strategy_spec_id": "strategy-spec:equity-cross-sectional-v1"
  },
  "checksum": "sha256:abc123def456...",
  "storage_ref": {
    "backend": "object_store",
    "path": "openclaw/registry/equity_cross_sectional_alpha/1.0.0/artifact.bin"
  },
  "metadata": {
    "framework": "qlib",
    "model_family": "lightgbm",
    "problem_statement": "Cross-sectional equity alpha signal prediction",
    "entry_criteria_satisfied": {
      "baseline_strategy_exists": true,
      "feature_engineering_need": true,
      "data_sufficiency": true,
      "supervised_learning_appropriate": true,
      "no_upstream_conflicts": true
    },
    "training_data": {
      "tickers": ["universe:top100-by-marketcap"],
      "period": "2023-01-01 to 2025-06-30",
      "frequency": "daily",
      "samples": 50400,
      "num_instruments": 100
    },
    "features": {
      "num_features": 30,
      "feature_categories": ["momentum", "volatility", "volume", "cross_sectional_rank", "technical"],
      "feature_importance_top5": ["momentum_20d", "volatility_20d", "volume_change_5d", "zscore_return_10d", "rsi_14d"]
    },
    "hyperparameters": {
      "loss": "mse",
      "num_leaves": 63,
      "max_depth": 8,
      "learning_rate": 0.05,
      "n_estimators": 200
    },
    "performance": {
      "train_sharpe": 1.85,
      "validation_sharpe": 1.42,
      "test_sharpe": 1.38,
      "test_annualized_return": 0.16,
      "test_max_drawdown": 0.14,
      "test_information_ratio": 0.92,
      "test_turnover": 2.1,
      "test_ic": 0.045,
      "test_ir": 0.62
    }
  },
  "deployment_summary": {
    "current_stage": "none"
  },
  "approved_at": null,
  "evaluation_summary": {
    "market_regime_stress": {
      "status": "passed",
      "notes": "Tested on 2022 bear market data"
    },
    "feature_stability": {
      "status": "passed",
      "notes": "Top-5 feature importance stable across train/validation"
    }
  },
  "promoted_at": null,
  "approver": null,
  "rollback_target": null
}
```

### 3.2 Registry Gate (REG-001 + OSS-003 Qlib Constraints)

Before a Qlib alpha artifact reaches the registry, it must pass:

#### Gate 1: Replication Criteria (RS-003)
- **Input**: Research strategy candidate that satisfies entry criteria (§1).
- **Requirement**: RS-003 validates the source research strategy before Qlib training begins.

#### Gate 1A: Production Dataset Proof
- **Input**: Governed market-data proof attached to the Qlib activation packet.
- **Requirement**: proof must validate provider/source class, entitlement/license,
  freshness, point-in-time fields, durable storage, audit/rate-limit evidence,
  and `no_order_route=true`.
- **Implementation**: `services/research/qlib/adapter/production_activation.py`.
- **Output boundary**: the packet may request only `artifact_state=draft -> candidate`
  with `deployment_summary.current_stage=none`; registry writes remain owned by
  the registry service.
- **Artifact**: Replication report confirming strategy normalization and first-pass validation.

#### Gate 2: Entry Criteria Verification (OSS-003)
- **Input**: Trained Qlib model based on the governed strategy candidate selected in §1.
- **Requirement**: Trained model explicitly demonstrates it satisfies §1 entry criteria.
- **Checksum**:
  ```
  ✓ Baseline strategy exists: registry_id points to a governed StrategySpec candidate from RS-003
  ✓ Feature engineering need: documented feature categories and importance
  ✓ Data sufficiency: training set covers 2+ years, 50+ instruments
  ✓ Supervised learning appropriate: problem is prediction, not sequential decision
  ✓ No upstream conflicts: Qlib version compatible with existing packages
  ```

#### Gate 3: Out-of-Sample Robustness (REG-001)
- **Train Period**: 2023–01 to 2025–06.
- **Validation Period**: 2025–07 to 2025–12 (held-out).
- **Test Period**: 2026–01 to 2026–03 (walk-forward holdout, not deployment evidence).
- **Requirement**: Test Sharpe ≥ 80% of validation Sharpe; test IC ≥ 0.03.

#### Gate 4: Stress Tests
- **Market regime shift**: Does alpha signal degrade gracefully in new market conditions?
- **Feature stability**: Are top-5 feature importances stable (correlation > 0.7) across train/validation?
- **Universe expansion**: Does signal hold if universe expands by 20%?
- **Requirement**: No single test fails by > 20% vs. validation performance.

---

### 3.3 Governance Progression and Deployment Rules

#### Versioning

Qlib alpha artifacts follow semantic versioning:
- **Major**: Alpha objective or label definition changed fundamentally.
- **Minor**: Feature set or model hyperparameters updated; alpha objective unchanged.
- **Patch**: Bug fix in feature engineering or data pipeline; model weights unchanged.

Example: `qlib_alpha_equity_cross_sectional_v1.2.3`

#### Canonical Progression

```text
draft (new, not yet replication-validated)
    -> candidate (trained and ready for governance review)
    -> approved (governance-approved for possible deployment)
    -> retired
```

Deployment is a separate read model layered on top of an `approved` artifact:

```text
deployment_stage: none -> paper -> canary -> live
deployment_stage: paper/canary/live -> frozen (runtime safety action)
```

#### Transition Rules

- `draft` → `candidate`: Qlib model trained, entry criteria verified, passes replication gate.
- `candidate` → `approved`: Out-of-sample robustness confirmed, stress tests passed, governance review complete.
- `approved` + `DeploymentPlan` / `RuntimeBinding`: deployment stage may move to `paper`, `canary`, or `live`.
- `approved` → `retired`: Explicit retirement or supersession.
- `paper`, `canary`, `live`, and `frozen` are deployment/runtime stages, not registry states.

#### Operational Monitoring

Once the approved artifact is staged for paper, canary, or live use:

1. **Daily Performance Tracking**:
   - Realized IC (information coefficient) vs. predicted IC.
   - Rolling Sharpe ratio (30-day window).
   - Feature drift (PSI on top-5 features).

2. **Monthly Review**:
   - Rolling 30-day Sharpe degradation vs. validation performance.
   - Feature importance drift: if PSI > 0.30 for any top-5 feature → flag for review.

3. **Retraining Trigger**:
   - If 30-day rolling IC drops below 0.02: flag for retraining.
   - If market regime shifts significantly: consider retraining on new regime.

#### Rollback Criteria

Immediate rollback means rebinding runtime to the prior approved version if:
- Alpha signal IC drops below 0.01 for 5+ consecutive trading days.
- Sharpe drops to baseline − 0.5 for 2+ consecutive weeks.
- Feature drift (PSI > 0.40) on 3+ top-5 features simultaneously.

---

### 3.4 Integration with LEAN Execution

#### Input Contract

LEAN receives Qlib alpha signals through governed registry channels:

1. **Registry Stage**: Qlib alpha artifact reaches `artifact_state=approved`.
2. **Deployment Planning**: Governance/runtime objects decide whether the approved artifact is staged at `paper`, `canary`, or `live`.
3. **Artifact Materialization**: Registry/promotion tooling materializes artifact bytes + metadata projection to Object Store.
4. **LEAN Loader** (EX-001): Validates the loader-facing projection. During the current compatibility window, EX-001 may still mirror legacy `promotion_state`, but the source truth remains `artifact_state` plus deployment stage.
5. **Alpha Signal Consumer**: LEAN uses Qlib signal as an input factor in its portfolio construction.

#### Signal Consumption Pattern

The Qlib alpha signal is a **scoring model**, not an action policy. LEAN consumes it as:

- **Feature input**: Qlib produces a score per asset; LEAN uses this score in its optimizer.
- **Not direct actions**: Qlib does NOT produce buy/sell/hold actions. That distinction is critical.
- **Optimizer role**: The score feeds into the portfolio optimizer (e.g., mean-variance, risk parity) which decides allocations.

This contrasts with RL policies (LP-005), which produce direct actions from state.

---

## 4. Transition Path: When to Move from Qlib to RL

### Decision Tree

```
Has Qlib alpha signal been stable (Sharpe ≥ baseline + 0.2) for 3+ months?
  └─ NO → Continue Qlib iteration (features, labels, model tuning)
  └─ YES → Is sequential decision-making essential (not just signal scoring)?
           └─ NO → Stay with Qlib + alpha policy
           └─ YES → Check RL entry criteria (LP-005 §1)
                    └─ MET → Train RL candidate alongside Qlib (A/B comparison)
                    └─ NOT MET → Extend data collection or refine RL problem statement
```

### Key Distinction

| Aspect | Qlib | RL (LP-005) |
|---|---|---|
| Problem type | Supervised prediction | Sequential decision-making |
| Output | Score/signal per asset | Action (buy/sell/hold) per asset |
| Data need | 2+ years OHLCV | 2+ years intraday + order fills |
| Governance gate | REG-001 + feature stability | REG-001 + entry criteria verification |
| Consumption | LEAN optimizer input | LEAN policy executor |

---

## 5. Relationship to TRL

TRL activation is **independent** from Qlib activation:

- **Qlib** produces supervised alpha scores for downstream optimizers.
- **TRL** produces preference models from governed feedback events.
- Activating Qlib does **not** automatically activate TRL, and vice versa.
- If a Qlib-generated artifact later participates in preference-learning loops, TRL must still satisfy its own entry criteria in `services/learning/trl/ACTIVATION_CRITERIA.md`.

This section intentionally avoids restating TRL thresholds so the dedicated TRL activation document remains the only source of truth for preference-learning prerequisites.

---

## 6. Success Criteria (OSS-003 Qlib Acceptance)

- [x] Entry criteria documented (§1)
- [x] Workflow design defined (§2: Data Handler → Model Training → Backtesting)
- [x] Registry and promotion constraints specified (§3)
- [x] Integration points with RS-003, REG-001, and LEAN execution documented (§3.4)
- [x] Decision tree provided for when to use Qlib vs. RL (§4)
- [x] Example Qlib alpha artifact structure in registry (§3.1)
- [x] TRL boundary and cross-reference established (§5)

---

## 7. Next Steps

1. **Qlib Version Selection**: Pin Qlib version and define adapter boundary (separate task or OSS-003 follow-up).
2. **Data Pipeline Adapter**: Build Pantheon-governed data handler that feeds Qlib without bypassing governance.
3. **Smoke Test**: Train a single LightGBM model on a small equity universe (e.g., 10 tickers, 1 year) to validate the full path end-to-end.
4. **REG-001 Alignment**: Ensure follow-on adapters emit the canonical `artifact_state` / `deployment_stage` projection described in §3.1–§3.4.

---

## References

- `TARGET_ARCHITECTURE.md`: Learning objects, preferred frameworks (Qlib as supervised alpha research).
- `ROADMAP.md`: LP-001 through LP-005 learning integration roadmap; OSS-003 deferred path criteria.
- `services/learning/rl/PATH_DEFINITION.md`: RL path definition (LP-005) — contrasts with Qlib's supervised approach.
- `services/learning/trl/ACTIVATION_CRITERIA.md`: dedicated TRL activation gate; do not duplicate its thresholds here.
- `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md`: TRL preference learning contract — downstream of Qlib/imitation.
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`: Thresholds for evolution review; Qlib alpha degradation triggers feed into this.
- `OSS_INTEGRATION_CHECKLIST.md`: Qlib component status and required evidence artifacts.

---

**Document Status**: Approved for OSS-003 activation-gate lock after Codex reviewer cleanup
**Reviewer Verification**:
- [x] Shared-state metadata now matches `OSS-003` ownership (`Qwen` owner, `Codex` reviewer)
- [x] Registry vocabulary uses canonical `artifact_state` plus derived deployment stage rather than collapsing `paper/live` into lifecycle state
- [x] Qlib output stays inside existing REG-001 / REG-003 / EX-001 governance boundaries
- [x] LEAN execution contract remains scoring-only, not direct action execution
- [x] TRL is referenced only as a neighboring path, without duplicating or overriding dedicated TRL thresholds

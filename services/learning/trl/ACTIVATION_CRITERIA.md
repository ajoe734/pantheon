# TRL Activation Criteria

**Task**: OSS-003 (TRL path)
**Owner**: Qwen
**Reviewer**: Codex
**Scope**: Define hard entry criteria for TRL (Transformers for Reinforcement Learning) activation as the governed preference-learning path
**Status**: APPROVED for OSS-003 activation-gate lock
**Last Updated**: 2026-04-10

---

## Executive Summary

This document defines the activation criteria, workflow, and governance constraints for **TRL** (Transformers for Reinforcement Learning, from Hugging Face) as the preference-learning engine in the Pantheon platform.

**Key Principle**: TRL is a **preference-learning optimizer**, not a policy generator or alpha signal discoverer. It learns from governed human feedback (FB-002) to model operator preferences, which then feed into evaluators (EV-001), reward shaping for RL (LP-005), or persona policy mutation (LP-001). TRL activation requires an established imitation baseline and sufficient governed feedback volume.

---

## 1. Entry Criteria for TRL Activation

### When to Activate TRL

TRL is justified when:

1. **Feedback Store Volume Sufficient**: FB-002 (trader approve/edit/reject events) has produced **≥ 200 governed feedback events** with complete metadata (actor_role, promotion_state, artifact linkage).
   - Events must span at least **2 different strategy families** (e.g., equity cross-sectional, stat arb).
   - Events must include all three action types: approve, edit, reject.

2. **Imitation Baseline Active**: LP-002 (imitation workflow) is already integrated and producing behavior-cloning artifacts that pass registry admission.
   - Rationale: Imitation learns *what* operators do; TRL learns *why* they prefer one outcome over another. The behavioral baseline grounds the preference model.

3. **Preference Pair Volume Sufficient**: ≥ **100 valid preference pairs** have been constructed from FB-002 events using the pair construction rules in `PREFERENCE_LEARNING_CONTRACT.md` §4.
   - Pairs must pass quality gates: valid promotion_state (candidate/paper), complete artifact linkage, deduplicated within 24-hour windows.

4. **Baseline Model Performance Documented**: At least one baseline preference model (logistic regression or gradient boosted trees per `PREFERENCE_LEARNING_CONTRACT.md` §3) has been trained and evaluated with:
   - Holdout accuracy ≥ 0.65
   - AUC-ROC ≥ 0.70
   - This establishes that preference signal exists and is learnable.

5. **Downstream Consumer Ready**: At least one of:
   - EV-001 evaluator is ready to consume preference models for scoring.
   - LP-005 RL path is active and can use preference models for reward shaping.
   - LP-001 DSPy persona policy can use preference models for prompt optimization.

6. **No Upstream Conflicts**: TRL version (expected `trl>=0.8.0`) and its dependencies (transformers, accelerate, peft) do not conflict with already-integrated packages (DSPy, imitation, MLflow, Qlib).

---

## 2. TRL Workflow Design

### 2.1 Workflow Overview

```
Governance Layer (FB-002 filtering, ALLOWED_ACTOR_ROLES, ALLOWED_PROMOTION_STATES)
    ↓
Preference Pair Construction (from FB-002 events per PREFERENCE_LEARNING_CONTRACT.md §4)
    ↓
Pair Quality Gates (dedup, linkage validation, temporal window filtering)
    ↓
Dataset Assembly (train/val/test split — temporally separated)
    ↓
TRL DPO Training (Direct Preference Optimization)
    ↓
Evaluation (accuracy, AUC-ROC, coverage, temporal stability, drift)
    ↓
Registry Admission (REG-001 gate) + TRL-specific constraints
    ↓
Consumption: EV-001 evaluator / LP-005 reward shaping / LP-001 persona policy
```

### 2.2 Preference Pair Construction

**Input**: FB-002 trader feedback events.

**Construction Rules** (from `PREFERENCE_LEARNING_CONTRACT.md` §4):

| Event action | Pair construction |
|---|---|
| `approve` | (artifact, null) → positive example |
| `reject` | (null, artifact) → negative example |
| `edit` | (artifact_edited, artifact_original) → preference pair |

**Quality Gates**:
- Exclude pairs where `promotion_state` ∉ `["candidate", "paper"]`.
- Exclude pairs missing artifact linkage.
- Deduplicate pairs from the same artifact within 24-hour windows.
- Minimum pair count: 100 (entry criterion §1.3).

### 2.3 Dataset Assembly

**Temporal Split**:
- **Training**: events older than 21 days.
- **Validation**: events from 14–21 days ago.
- **Holdout test**: events from the past 14 days.

This ensures the model is evaluated on recent operator behavior.

**Dataset Properties**:
- Class balance: approve/edit/reject distribution documented.
- Feature coverage: all required features computable for ≥ 95% of pairs.
- Operator diversity: at least 2 distinct operator_ids represented.

### 2.4 Model Training: TRL DPO

#### Why DPO

**Direct Preference Optimization (DPO)** is the recommended TRL algorithm for v1 because:

1. It optimizes directly on preference pairs, avoiding the intermediate reward model step of RLHF.
2. It is more stable and sample-efficient than PPO-based RLHF for this use case.
3. It produces a policy model that directly outputs preference scores.

#### Model Architecture

**Base Model**: A small transformer encoder (e.g., DistilBERT or a custom small transformer) that takes serialized artifact metadata + context as input.

**Input Representation**:
```
[CLS] artifact_a_features [SEP] artifact_b_features [SEP] context_features [SEP]
```

Where:
- `artifact_a_features`: serialized metadata of artifact A (strategy_id, Sharpe, drawdown, sector, etc.)
- `artifact_b_features`: serialized metadata of artifact B (or zeros if null)
- `context_features`: operator_id embedding, domain, timestamp features

**Output**: Scalar preference score P(A preferred over B).

#### Training Configuration

```yaml
model:
  base_model: distilbert-base-uncased  # or custom small transformer
  max_length: 512

training:
  method: dpo
  beta: 0.1  # KL penalty relative to reference model
  learning_rate: 5e-6
  batch_size: 16
  gradient_accumulation_steps: 4
  num_epochs: 3
  warmup_ratio: 0.1
  weight_decay: 0.01
  max_grad_norm: 1.0

reference_model:
  # Frozen copy of initial model for KL regularization
  same_architecture: true
  load_from: initial_checkpoint
```

#### Hyperparameter Search

For v1, a **limited grid search** is recommended:
- `beta`: [0.05, 0.1, 0.2]
- `learning_rate`: [1e-6, 5e-6, 1e-5]
- `num_epochs`: [2, 3, 5]

Total: 27 combinations maximum. Select best by validation AUC-ROC.

### 2.5 Evaluation

**Minimum Evaluation Criteria** (from `PREFERENCE_LEARNING_CONTRACT.md` §5):

| Criterion | Threshold (v1) |
|---|---|
| Holdout Accuracy | ≥ 0.70 |
| AUC-ROC | ≥ 0.75 |
| Coverage | ≥ 0.85 |
| Temporal Stability | < 0.08 swing between consecutive weeks |
| Drift Detection | KL divergence < 0.1 nats from baseline |
| Baseline Improvement | ≥ 5% above majority-class baseline |

**Additional v1 Checks**:
- **Calibration**: Predicted probabilities should align with observed approval rates (reliability diagram slope ≈ 1.0, intercept ≈ 0.0).
- **Fairness**: Model performance should not vary > 10% across different operator_ids (avoid operator-specific overfitting).

---

## 3. Registry Constraints for TRL Preference Models

### 3.1 Artifact Model for TRL Preference Models

Each TRL preference model artifact follows the canonical registry target shape from `services/registry/contract.md`.
Because TRL outputs are non-executable governed artifacts, they use registry `artifact_state` and normally keep
`deployment_summary.current_stage=none`.

```json
{
  "registry_id": "trl-preference-model-multi-asset-v1.0.0",
  "artifact_type": "model_artifact",
  "strategy_id": "preference_learning_multi_asset",
  "version": "1.0.0",
  "artifact_state": "draft",
  "created_at": "2026-04-20T10:00:00Z",
  "lineage": {
    "parent_registry_ids": [],
    "source_run_ids": ["trl-dpo-run-2026-04-20"],
    "source_dataset_refs": ["fb002-events-2026-03-01-to-2026-04-15"],
    "source_strategy_spec_id": "strategy-spec:preference-learning-v1",
    "source_feedback_event_ids": ["fb-uuid-001", "fb-uuid-002", "..."]
  },
  "checksum": "sha256:abc123def456...",
  "storage_ref": {
    "backend": "object_store",
    "path": "openclaw/registry/preference_learning_multi_asset/1.0.0/artifact.bin"
  },
  "metadata": {
    "framework": "trl",
    "model_family": "preference_model",
    "algorithm": "dpo",
    "problem_statement": "Multi-asset operator preference prediction",
    "entry_criteria_satisfied": {
      "feedback_volume_sufficient": true,
      "imitation_baseline_active": true,
      "preference_pair_volume_sufficient": true,
      "baseline_model_performance_documented": true,
      "downstream_consumer_ready": true,
      "no_upstream_conflicts": true
    },
    "training_data": {
      "num_feedback_events": 250,
      "num_preference_pairs": 120,
      "period": "2026-03-01 to 2026-04-15",
      "strategy_families": ["equity_cross_sectional", "stat_arb"],
      "action_distribution": {
        "approve": 140,
        "edit": 60,
        "reject": 50
      },
      "num_operators": 3
    },
    "model_config": {
      "base_model": "distilbert-base-uncased",
      "method": "dpo",
      "beta": 0.1,
      "learning_rate": 5e-6,
      "batch_size": 16,
      "num_epochs": 3
    },
    "performance": {
      "holdout_accuracy": 0.74,
      "holdout_auc_roc": 0.79,
      "coverage": 0.88,
      "temporal_stability": 0.05,
      "drift_score": 0.06,
      "baseline_accuracy": 0.62,
      "improvement_over_baseline": 0.12,
      "calibration_slope": 0.97,
      "calibration_intercept": 0.02
    }
  },
  "deployment_summary": {
    "current_stage": "none"
  },
  "approved_at": null,
  "evaluation_summary": {
    "fairness_check": {
      "status": "passed",
      "notes": "Performance variation across operators: 4.2% (within 10% threshold)"
    },
    "feature_importance": {
      "prior_sharpe": 0.28,
      "artifact_type": 0.22,
      "sector_concentration": 0.18,
      "operator_id": 0.15,
      "recency": 0.10,
      "max_drawdown": 0.07
    }
  },
  "promoted_at": null,
  "approver": null,
  "rollback_target": null
}
```

### 3.2 Registry Gate (REG-001 + OSS-003 TRL Constraints)

Before a TRL preference model reaches the registry, it must pass:

#### Gate 1: FB-002 Data Quality Verification
- **Input**: FB-002 feedback events used for training.
- **Requirement**: All events have complete metadata (actor_role, promotion_state, artifact linkage).
- **Checksum**: ≥ 200 events, ≥ 100 valid preference pairs, ≥ 2 strategy families, all three action types present.

#### Gate 2: Entry Criteria Verification (OSS-003)
- **Input**: Trained TRL model.
- **Requirement**: All §1 entry criteria verified and documented.

#### Gate 3: Out-of-Sample Robustness (REG-001)
- **Train**: events older than 21 days.
- **Validation**: events from 14–21 days ago.
- **Test**: events from the past 14 days.
- **Requirement**: All §2.5 evaluation criteria met.

#### Gate 4: Downstream Integration Test
- **Requirement**: At least one downstream consumer (EV-001, LP-005, or LP-001) successfully loads and uses the model in a governed evaluation path.

### 3.3 Governance Progression

#### Versioning

TRL preference models follow semantic versioning:
- **Major**: Preference objective or architecture changed fundamentally (e.g., DPO → RLHF).
- **Minor**: Hyperparameters, base model, or training data distribution updated; objective unchanged.
- **Patch**: Bug fix in pair construction or feature engineering; model weights unchanged.

```text
draft (new, not yet evaluated)
    -> candidate (passes evaluation, ready for governance review)
    -> approved (governed for downstream consumption)
    -> retired
```

**Note**: TRL models are evaluation and reward-shaping inputs, not execution-stage artifacts. They do not use
`paper` / `canary` / `live` as registry states, and their `deployment_summary.current_stage` should normally remain `none`.

#### Rollback Criteria

Rollback means reverting the consumer reference to the previous approved version if:
- Holdout accuracy drops below 0.60 for 2+ consecutive evaluation windows.
- Model calibration drift: reliability diagram slope < 0.8 or intercept > 0.1.
- Fairness violation: performance variation across operators > 15%.

---

## 4. Downstream Consumption

### 4.1 EV-001 Evaluator Consumption

The EV-001 evaluator consumes TRL preference models as:

- **Scoring input**: The model produces P(approve | artifact, context) for candidate artifacts.
- **Evaluator augmentation**: The preference score is one component of the evaluator's composite score (alongside performance metrics, risk metrics, etc.).
- **Not a veto**: The preference model score does not veto other evaluator criteria.

### 4.2 LP-005 RL Reward Shaping

When the RL path (LP-005) is active, TRL preference models can be used for:

- **Reward shaping**: The preference model score modifies the RL reward function to align with operator preferences.
- **Constraint**: The shaped reward is blended with the primary task reward (e.g., Sharpe ratio) with a configurable weight (default: 0.3 preference, 0.7 task).

### 4.3 LP-001 DSPy Persona Policy

TRL preference models inform DSPy persona policy optimization by:

- **Intent signal**: The model predicts which prompt bundles the operator is likely to approve.
- **Constraint**: DSPy uses this as an auxiliary signal, not the sole optimization target.

---

## 5. Relationship to Other Learning Frameworks

| Framework | Role | Relationship to TRL |
|---|---|---|
| **Qlib** | Supervised alpha signal discovery | Independent; Qlib produces market signals, TRL produces preference models |
| **Imitation (LP-002)** | Behavior cloning from trader trajectories | Prerequisite; imitation learns *what*, TRL learns *why* |
| **DSPy (LP-001)** | Persona policy optimization | Consumer; DSPy uses TRL preference scores as intent signal |
| **RL (LP-005)** | Sequential decision-making | Consumer; RL uses TRL for reward shaping |
| **MLflow (LP-003)** | Experiment tracking | Infrastructure; MLflow tracks TRL training runs |

---

## 6. Success Criteria (OSS-003 TRL Acceptance)

- [x] Entry criteria documented (§1)
- [x] Workflow design defined (§2: Pair Construction → DPO Training → Evaluation)
- [x] Registry and promotion constraints specified (§3)
- [x] Downstream consumption patterns documented (§4)
- [x] Relationship to other learning frameworks clarified (§5)
- [x] Example TRL preference model artifact structure in registry (§3.1)

---

## 7. Next Steps

1. **TRL Version Selection**: Pin TRL version (`trl>=0.8.0` recommended) and verify compatibility with existing packages.
2. **Pair Construction Implementation**: Build the preference pair extraction pipeline from FB-002 events.
3. **Smoke Test**: Train a minimal DPO model on mock/synthetic preference pairs (50-100 pairs) to validate the training pipeline.
4. **REG-001 Alignment**: Ensure registry gate accepts TRL preference model artifacts with the metadata shape defined in §3.1.
5. **EV-001 Integration Test**: Validate that an evaluator can load and consume a TRL preference model in a governed evaluation path.

---

## References

- `TARGET_ARCHITECTURE.md`: Learning objects, preferred frameworks (TRL as governed preference learning).
- `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md`: LP-004 preference learning contract (approved v1).
- `services/learning/trl/WORKFLOW_DEFINITION.md`: LP-004 workflow implementation details.
- `services/learning/rl/PATH_DEFINITION.md`: RL path definition (LP-005) — downstream consumer of TRL.
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`: Thresholds for evolution review; TRL preference degradation triggers.
- `OSS_INTEGRATION_CHECKLIST.md`: TRL component status and required evidence artifacts.

---

**Document Status**: Approved for OSS-003 activation-gate lock after Codex reviewer cleanup
**Reviewer Verification**:
- [x] Shared-state metadata now matches `OSS-003` ownership (`Qwen` owner, `Codex` reviewer)
- [x] TRL remains a governed preference-learning path rather than an execution deployment path
- [x] Registry vocabulary uses canonical `artifact_state`; `paper/live` are no longer treated as lifecycle states for TRL artifacts
- [x] Downstream consumption remains scoped to evaluator augmentation, reward shaping, and persona-policy support
- [x] Success criteria remain verifiable from the documented thresholds and gate sequence

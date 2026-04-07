# Preference Learning Contract

**Task:** LP-004  
**Owner:** Grok  
**Reviewer:** Codex  
**Status:** APPROVED for v1 contract lock

---

## 1. Purpose

Preference learning is the process of building models that predict human preferences about strategy quality, approval decisions, and portfolio fit based on governed feedback data.

A preference model captures:
- which strategies a given operator tends to approve
- what factors the operator values (e.g., low drawdown, Sharpe ratio, sector focus)
- implicit decision-making patterns from the history of approvals and rejections

Preference models are used to:
- feed reward shaping for policy optimization (LP-002, LP-005)
- provide context to evaluators and critics (EV-001)
- personalize recommendations and persona policies
- create evaluation criteria for new strategy candidates

Preference learning is **not**:
- direct policy optimization (that is DSPy or sequential RL)
- live strategy replacement (that requires explicit operator approval)
- model training on live execution data (only on governed feedback)

---

## 2. Design Principles

### 2.1 Governed Data Only

Preference models train exclusively on feedback events from the FB-002 store.

Allowed data sources:
- `trader_feedback_event` with `actor_role` ∈ `["operator", "approver"]`
- Only events where `promotion_state` ∈ `["candidate", "paper"]`
- Artifact metadata linked in the event (strategy_id, artifact_version, promotion_state)
- Trader rationale and feedback action (approve, edit, reject)

Forbidden data sources:
- Live execution telemetry (PnL, drawdown, slippage)
- Raw market data (OHLCV, signals)
- Ungoverned audit logs or chat transcripts
- Intra-strategy parameter tuning results

Rationale: Preference learning models operator intent, not market outcomes. Mixing market data with preference data creates implicit reward function bias toward recent market conditions, not operator values.

### 2.2 Immutable Artifacts

Once a preference model is committed to the registry, it cannot be modified in-place.

- Retraining always produces a new artifact with a new `model_version`
- The registry tracks promotion state separately from model identity
- Rollback means reverting to a prior `model_version`, not modifying the current one

### 2.3 Preference Pairs Are the Unit of Training

Models train on **preference pairs**: structured comparisons of two outcomes.

Examples:
- "Operator approved strategy A, rejected strategy B → (A, B) is a preference pair"
- "Operator edited strategy C (changing X to Y) → ((C_original), (C_edited)) is a preference pair"
- "Operator approved strategy D in context 1, rejected strategy D in context 2 → implicit context-dependent preference"

Preference pairs are constructed deterministically from FB-002 events, ensuring reproducibility and traceability.

### 2.4 No Live Promotion Bypass

Preference models are inputs to decision-making, never substitutes for human approval.

- A model saying "operator would approve this" does not make it approved
- Models feed into evaluators (EV-001) or reward shaping (LP-005), not directly to registry promotion gates
- Registry gates remain the single source of truth for promotion decisions

---

## 3. Approved Model Architectures

Preference models in v1 can use the following architectures:

| Architecture | Framework | Use case | Constraints |
|---|---|---|---|
| Logistic Regression | scikit-learn or statsmodels | Binary approval prediction | Linear decision boundary |
| Gradient Boosted Trees | XGBoost, LightGBM | Non-linear approval patterns | ≤ 100 trees in v1 |
| Neural Network (MLP) | PyTorch or TensorFlow | Complex preference dependencies | ≤ 3 hidden layers, ReLU/Tanh activation |
| Transformer Encoder | PyTorch transformers | Artifact metadata sequences | ≤ 2 attention heads in v1 |
| SVM with RBF kernel | scikit-learn | Multiclass preference classification | C ≤ 100 |

**Not approved in v1**: LLMs, reinforcement learning models (those go to LP-005), feature selection pipelines (use standard feature engineering or domain knowledge).

### 3.1 Input Feature Space

Models should be constructed from:
- **Artifact metadata**: strategy_id, artifact_type, creation_timestamp, prior_promotion_state
- **Trader context**: operator_id, domain_familiarity, historical approval rate
- **Registry-backed evaluation summaries**: mean Sharpe, sector concentration, maximum drawdown, but only when those values have already been materialized into registry-linked evaluation artifacts
- **Temporal features**: time since artifact creation, feedback recency

Features must be **computable from registry and feedback store only**, not from live market data, raw execution telemetry streams, or real-time signals.

### 3.2 Output Format

Models output one of:
- **Binary classification**: `P(approve | artifact, context)` in [0, 1]
- **Multiclass classification**: probability distribution over `{approve, edit, reject}`
- **Ranking**: preference ordering of multiple artifacts by expected operator approval

Output must include:
- **logits**: raw model scores before probability scaling
- **confidence**: uncertainty estimate (e.g., prediction margin for binary, entropy for multiclass)
- **feature_importance**: which input features drove the prediction (for interpretability)

---

## 4. Preference Pair Construction

### 4.1 From FB-002 Events

Each `trader_feedback_event` yields one or more preference pairs:

| Event action | Pair construction | Example |
|---|---|---|
| `approve` | (artifact, ∅) → positive example | Operator approved strategy S1 → model learns S1 is preferred |
| `reject` | (∅, artifact) → negative example | Operator rejected strategy S2 → model learns S2 is dispreferred |
| `edit` | (artifact_edited, artifact_original) → preference | Operator changed parameter X to Y → model learns edited version is preferred |

### 4.2 Preference Pair Properties

Each pair records:
- **artifact_a**: first artifact (or null)
- **artifact_b**: second artifact (or null)
- **context**: trader, domain, timestamp, promotion_state
- **confidence**: human confidence in this preference (derived from rationale length, feedback metadata)
- **source_event_id**: FB-002 event UUID for traceability

### 4.3 Pair Quality Gates

Before training, filter pairs to ensure quality:
- Exclude pairs where `context.promotion_state` ∉ `["candidate", "paper"]` (draft feedback is too preliminary)
- Exclude pairs missing artifact linkage (incomplete governance metadata)
- Deduplicate pairs from the same artifact within a 24-hour window (avoid overweighting recent feedback)
- Optional: weight pairs by rationale length (longer explanations → higher weight)

---

## 5. Evaluation Criteria

Before a preference model transitions from `draft` to `candidate` state:

| Criterion | Threshold (v1) | Measurement |
|---|---|---|
| **Holdout Accuracy** | ≥ 0.70 | accuracy on preference pairs from recent (past 7 days) feedback |
| **AUC-ROC** | ≥ 0.75 | for binary approval prediction |
| **Coverage** | ≥ 0.85 | model assigns >0 confidence to both positive and negative examples |
| **Temporal Stability** | `max(eval_week_t - eval_week_t-1) < 0.08` | model performance should not swing >8% between consecutive weeks |
| **Drift Detection** | `KL_divergence(model_pred, baseline) < 0.1` | model predictions should stay within 0.1 nats of prior distribution |
| **Baseline Anchoring** | `accuracy >= baseline_majority_accuracy` | model should beat always-predicting-majority class |

### 5.1 Baseline

Before training a new model, compute a baseline:
- Simple majority classifier on holdout set (always predict most common class)
- Record baseline accuracy and class distribution
- New model must improve on baseline

### 5.2 Holdout Evaluation Set

Holdout sets must be **temporally separated**:
- Training: events older than 14 days
- Validation: events from 7–14 days ago
- Holdout test: events from the past 7 days

This ensures the model is evaluated on recent operator behavior, not stale preferences.

### 5.3 Evaluation Metrics Artifact

Models must produce an `eval_metrics.json` at submission time:

```json
{
  "eval_date": "2026-04-06T13:00:00Z",
  "train_set_size": 250,
  "eval_set_size": 50,
  "holdout_test_size": 40,
  "accuracy": 0.75,
  "auc_roc": 0.78,
  "coverage": 0.90,
  "temporal_stability": 0.05,
  "drift_score": 0.07,
  "baseline_accuracy": 0.60,
  "improvement_over_baseline": 0.15,
  "class_distribution": {
    "approve": 0.55,
    "edit": 0.25,
    "reject": 0.20
  },
  "feature_importance": {
    "artifact_type": 0.35,
    "prior_sharpe": 0.25,
    "sector_concentration": 0.20,
    "operator_id": 0.15,
    "recency": 0.05
  }
}
```

---

## 6. Registry Handoff

### 6.1 Artifact Structure

A preference model artifact contains:

```
preference_model/
  model_bundle.joblib          # serialized model (sklearn) or state_dict (torch)
  input_schema.json            # feature names, types, valid ranges
  eval_metrics.json            # §5.3 — evaluation results
  preference_pair_stats.json   # training data characteristics
  model_metadata.json          # governance metadata (§6.2)
  training_config.yaml         # reproducibility: hyperparameters, train/val/test splits
```

### 6.2 model_metadata.json

| Field | Required | Description |
|---|---|---|
| `model_id` | yes | unique identifier for this model |
| `model_version` | yes | semantic version (e.g., 1.0.0) |
| `architecture` | yes | which model type (`logistic_regression`, `xgboost`, `neural_net`, etc.) |
| `base_strategy_id` | yes | which strategy family this preference model predicts for |
| `training_operator_id` | no | if training is operator-specific, note it here |
| `operator_domain` | no | domain focus (e.g., "equity", "crypto", "multi-asset") |
| `preference_pair_count` | yes | number of pairs used for training |
| `preference_source_window` | yes | time window of FB-002 events used (start, end timestamps) |
| `target_metric` | yes | what the model predicts (`approval_probability`, `approval_rank`, etc.) |
| `framework_version` | yes | package versions: scikit-learn, PyTorch, etc. |
| `created_at` | yes | RFC3339 timestamp |
| `created_by` | yes | agent/operator that submitted this artifact |

### 6.3 Registry Lifecycle

The artifact enters REG-001 as:

```
artifact_type:    model_artifact
lifecycle_state:  draft       (output of training run)
                → candidate   (passes evaluation criteria from §5)
                → paper       (operator review, deployed to paper evaluation)
```

`live` state is not used for preference models; instead, models are referenced by evaluators and optimizers that themselves move through the lifecycle.

### 6.4 Registry Linkage

Every preference model must link back to:
- The governed feedback store (FB-002) that supplied training data
- The base strategy or domain it was trained on
- Upstream training operators or analyst teams

Registry entries for this family should be tagged with:

- `artifact_type=model_artifact`
- `metadata.model_family=preference_model`

Query pattern:
```sql
SELECT * FROM registry_entries
WHERE artifact_type = 'model_artifact'
AND metadata->>'model_family' = 'preference_model'
AND strategy_id = 'strat_xyz'
AND lifecycle_state = 'paper'
ORDER BY created_at DESC
LIMIT 1
```

---

## 7. Governance Enforcement

### 7.1 Data Access Control

- TRL training scripts must read from FB-002 store, not directly from files or live execution systems
- All training data must be logged: which feedback events were included, which were filtered
- Training data provenance must be traceable: every preference pair links back to a FB-002 event_id

### 7.2 Model Deployment

- Preference models deployed to `paper` state can be used in paper evaluations and reward shaping for paper RL experiments
- Models at `candidate` state are limited to offline validation and registry-side checks; they must not be consumed by evaluators that influence promotion decisions
- `draft` models are for development only

### 7.3 Audit Trail

- Every preference model submission records:
  - which operator requested training
  - which feedback events were included
  - which hyperparameters were used
  - which evaluation criteria were met
- Model retraining produces a new artifact with a new model_id, not an update to the existing one

---

## 8. Non-Negotiable Constraints

1. **No market data**: Models train on feedback events, not market prices or execution outcomes.
2. **No live influence**: Models cannot directly trigger strategy promotion or live execution changes.
3. **Immutable once registered**: Artifacts cannot be modified in-place; retraining creates new artifacts.
4. **Governance-linked training data**: Every training example must trace back to a registered feedback event with complete metadata.
5. **Holdout evaluation**: Models must be evaluated on temporally separated test data before registry admission.
6. **Operator accountability**: The operator who requested training must be recorded, and training must use their actual feedback events.

---

## 9. Success Criteria (LP-004 Acceptance)

- [x] Preference learning scope defined and separated from DSPy and sequential RL
- [x] Data governance principles articulated (FB-002 only, no live data)
- [x] Approved model architectures and feature spaces documented
- [x] Preference pair construction logic specified with FB-002 linkage
- [x] Evaluation criteria defined (accuracy, coverage, drift detection, temporal stability)
- [x] Registry handoff contract specified (artifact structure, lifecycle, metadata)
- [x] Governance constraints listed (immutability, approval gates, audit trail)
- [x] Workflow implementation details in WORKFLOW_DEFINITION.md
- [x] Integration pattern with EV-001 sketched (how evaluators consume preference models)

---

## 10. Next Steps

1. **Codex Review**: Align with TARGET_ARCHITECTURE and FB-002 governance boundary.

2. **WORKFLOW_DEFINITION.md**: Document step-by-step implementation for training, evaluation, and registry submission.

3. **Smoke Test**: Build minimal preference model using mock FB-002 data:
   - Logistic regression on 50–100 preference pairs
   - Holdout evaluation with ≥70% accuracy
   - Submit to registry as draft

4. **EV-001 Integration**: Define how evaluators consume preference models (reference pattern for §7 non-negotiable constraints).

5. **Feature Engineering Guide**: Document approved features and how to compute them from registry + feedback store.

---

## References

- `TARGET_ARCHITECTURE.md`: Feedback governance and learning layers
- `services/feedback/schema/contract.md`: FB-001 and FB-002 event definitions
- `services/control-plane/persona/lp001/contract.md`: DSPy preference pair construction (pattern reference)
- `services/learning/rl/README.md`: Sequential RL (LP-005) for scope separation
- `services/learning/trl/EV-001_INTEGRATION.md`: Integration with evaluators and critics
- `ROADMAP.md`: LP-001 through LP-005 timeline

---

**Document Status**: APPROVED for v1 contract lock  
**Owner**: Grok  
**Reviewer**: Codex  
**Last Updated**: 2026-04-06

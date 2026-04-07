# TRL Preference Learning Workflow

**Task:** LP-004  
**Owner:** Grok  
**Reviewer:** Codex  
**Status:** APPROVED reference workflow for v1 implementation

---

## 1. Overview

This document describes the step-by-step workflow for:
1. Extracting preference pairs from governed feedback (FB-002)
2. Training a preference model
3. Evaluating on holdout data
4. Packaging the artifact
5. Submitting to registry (REG-001)

The workflow is designed to be:
- **Reproducible**: deterministic pair construction and hyperparameter logging
- **Governable**: all inputs traced back to FB-002 events with governance metadata
- **Incremental**: can be run in a CI/CD pipeline or interactive development loop
- **Auditable**: complete record of what data was used and why

---

## 2. Phase 1: Data Extraction and Pair Construction

### 2.1 Query FB-002 Store

**Input**: Target operator, strategy_id, time window  
**Output**: set of `trader_feedback_event` records

Query constraints:
```
WHERE
  actor_role IN ('operator', 'approver')
  AND event_type IN ('approve', 'edit', 'reject')
  AND target.promotion_state IN ('candidate', 'paper')
  AND target.strategy_id = $STRATEGY_ID
  AND created_at >= $WINDOW_START
  AND created_at <= $WINDOW_END
```

Example query (pseudocode):
```python
events = feedback_store.query(
    actor_role=['operator', 'approver'],
    event_type=['approve', 'edit', 'reject'],
    target={"promotion_state": ['candidate', 'paper'], "strategy_id": 'strat_xyz'},
    start_time='2026-03-06T00:00:00Z',
    end_time='2026-04-06T00:00:00Z'
)
print(f"Found {len(events)} feedback events")
```

**Quality checks**:
- Verify events have complete governance metadata (all fields non-null)
- Log: how many events, date range, operator distribution

### 2.2 Construct Preference Pairs

**Input**: `trader_feedback_event` records  
**Output**: list of (artifact_a, artifact_b, context, confidence) tuples

Algorithm:

```python
def construct_preference_pairs(events):
    """
    Deterministically construct preference pairs from feedback events.
    Each event generates one pair per decision (approve, reject, edit).
    Canonical input shape follows trader_feedback_event.schema.json.
    """
    pairs = []
    
    for event in events:
        action = event['event_type']  # 'approve', 'reject', or 'edit'
        target = event['target']
        base_artifact = registry.resolve_artifact(target)
        context = {
            'operator_id': event['actor_id'],
            'timestamp': event['created_at'],
            'promotion_state': target['promotion_state'],
            'strategy_id': target['strategy_id'],
            'artifact_type': target.get('artifact_type'),
            'artifact_version': target.get('artifact_version'),
            'registry_id': target.get('registry_id'),
        }
        confidence = len(event.get('rationale', '')) / 500.0  # scale by rationale length
        confidence = min(confidence, 1.0)  # cap at 1.0
        
        if action == 'approve':
            pair = (base_artifact, None, context, confidence)
        elif action == 'reject':
            pair = (None, base_artifact, context, confidence)
        elif action == 'edit':
            # Original artifact is dispreferred; edited variant is reconstructed from canonical edits
            edited_artifact = materialize_feedback_edit(base_artifact, event['edits'])
            pair = (edited_artifact, base_artifact, context, confidence)
        
        pairs.append(pair)
    
    return pairs
```

**Deduplication**:
- Group pairs by (strategy_id, registry_id || artifact_version || lineage_ref)
- If >1 pair within 24 hours, keep only the most recent (feedback supersedes prior judgment)
- If >3 pairs for same artifact, sample evenly across time window (avoid overweighting recent feedback)

**Output file**: `preference_pairs.jsonl`  
```json
{"artifact_a": {...}, "artifact_b": {...}, "context": {...}, "confidence": 0.85}
{"artifact_a": {...}, "artifact_b": null, "context": {...}, "confidence": 0.60}
```

### 2.3 Log Data Extraction

Record provenance:

```json
{
  "extraction_timestamp": "2026-04-06T13:00:00Z",
  "extraction_query": {
    "actor_role": ["operator", "approver"],
    "promotion_state": ["candidate", "paper"],
    "strategy_id": "strat_xyz",
    "time_window": {
      "start": "2026-03-06T00:00:00Z",
      "end": "2026-04-06T00:00:00Z"
    }
  },
  "extraction_results": {
    "raw_events": 300,
    "after_dedup": 280,
    "final_pairs": 260
  },
  "event_ids_included": ["event_uuid_1", "event_uuid_2", ...],
  "quality_checks": {
    "governance_metadata_complete": 280,
    "promotion_state_valid": 280
  }
}
```

---

## 3. Phase 2: Feature Engineering

### 3.1 Extract Features

**Input**: preference pairs + registry/artifact store  
**Output**: feature vectors X, labels y

For each artifact, compute:

| Feature | Source | Type | Example |
|---|---|---|---|
| `artifact_type` | artifact metadata | categorical | "strategy_spec", "signal_snapshot" |
| `strategy_id` | artifact metadata | categorical | "strat_xyz" |
| `days_since_creation` | artifact.created_at | numeric | 14.5 |
| `prior_promotion_state` | registry | categorical | "candidate", "paper" |
| `operator_id` | event context | categorical | "op_001" |
| `operator_approve_rate` | feedback store stats | numeric | 0.65 |
| `mean_sharpe` | registry-linked evaluation summary | numeric | 1.2 (or null if not evaluated) |
| `max_drawdown` | registry-linked evaluation summary | numeric | -0.15 (or null) |
| `sector_concentration` | artifact metadata | numeric | 0.45 |
| `num_positions` | artifact metadata | numeric | 12 |

### 3.2 Feature Encoding

- **Categorical features**: one-hot encoding or label encoding (if tree-based model, label encoding is fine)
- **Numeric features**: standardize or normalize (z-score or min-max)
- **Missing values**: 
  - For mean_sharpe, max_drawdown: impute with 0 or median of non-null values
  - For operator_approve_rate: compute from feedback store; never null
  - Document imputation strategy

### 3.3 Label Construction

For each pair (artifact_a, artifact_b, context):

```python
def pair_to_label(pair, label_type='binary_approval'):
    """
    Convert (artifact_a, artifact_b) pair to label.
    label_type: 'binary_approval' or 'multiclass'
    """
    artifact_a, artifact_b, context, confidence = pair
    
    if label_type == 'binary_approval':
        # artifact_a is preferred
        if artifact_a is not None and artifact_b is None:
            label = 1  # positive example
        elif artifact_a is None and artifact_b is not None:
            label = 0  # negative example
        else:
            # edit case: edited (artifact_a) is preferred
            label = 1
    
    elif label_type == 'multiclass':
        # {0: reject, 1: edit, 2: approve}
        if artifact_b is None:
            label = 2  # approve
        elif artifact_a is None:
            label = 0  # reject
        else:
            label = 1  # edit
    
    return label, confidence
```

### 3.4 Output

**features.csv**:
```
artifact_type,strategy_id,days_since_creation,...,label,confidence
strategy_spec,strat_xyz,14.5,...,1,0.85
signal_snapshot,strat_xyz,7.2,...,0,0.60
...
```

**feature_schema.json**:
```json
{
  "feature_names": ["artifact_type", "strategy_id", "days_since_creation", ...],
  "feature_types": ["categorical", "categorical", "numeric", ...],
  "categorical_values": {
    "artifact_type": ["strategy_spec", "signal_snapshot", ...],
    "operator_id": ["op_001", "op_002", ...]
  },
  "numeric_ranges": {
    "days_since_creation": [0, 365],
    "operator_approve_rate": [0, 1],
    "mean_sharpe": [-2, 5]
  }
}
```

---

## 4. Phase 3: Train/Val/Test Split

### 4.1 Temporal Split

Split data by event timestamp to ensure no data leakage:

```python
def temporal_train_val_test_split(df, val_fraction=0.17, test_fraction=0.17):
    """
    Split data temporally.
    Train: earliest 66%
    Val: middle 17%
    Test: most recent 17%
    """
    df_sorted = df.sort_values('timestamp')
    n = len(df_sorted)
    
    train_size = int(n * (1 - val_fraction - test_fraction))
    val_size = int(n * val_fraction)
    
    train = df_sorted[:train_size]
    val = df_sorted[train_size:train_size + val_size]
    test = df_sorted[train_size + val_size:]
    
    print(f"Train: {len(train)} ({len(train)/n:.1%})")
    print(f"Val: {len(val)} ({len(val)/n:.1%})")
    print(f"Test: {len(test)} ({len(test)/n:.1%})")
    
    return train, val, test
```

### 4.2 Stratification (Optional)

For imbalanced classes (e.g., 80% approve, 20% reject):
- Use stratified split to maintain class distribution in each fold
- For temporal split with stratification: within each temporal window, shuffle and stratify

### 4.3 Output

```
train.csv: 200 samples
val.csv: 30 samples
test.csv: 30 samples

data_split_metadata.json:
{
  "train": {"size": 200, "timestamp_range": ["2026-03-06", "2026-03-25"]},
  "val": {"size": 30, "timestamp_range": ["2026-03-25", "2026-04-01"]},
  "test": {"size": 30, "timestamp_range": ["2026-04-01", "2026-04-06"]}
}
```

---

## 5. Phase 4: Model Training

### 5.1 Baseline

First, compute a baseline (always-predict-majority-class):

```python
from sklearn.preprocessing import LabelEncoder
from collections import Counter

label_counts = Counter(y_train)
baseline_pred = max(label_counts, key=label_counts.get)
baseline_accuracy = label_counts[baseline_pred] / len(y_train)

print(f"Baseline (majority class): {baseline_pred}")
print(f"Baseline accuracy: {baseline_accuracy:.1%}")
```

### 5.2 Model Training (Logistic Regression Example)

```python
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

# Create pipeline
pipeline = Pipeline([
    ('scaler', StandardScaler()),
    ('model', LogisticRegression(
        max_iter=1000,
        random_state=42,
        class_weight='balanced'  # handle class imbalance
    ))
])

# Train on training set
pipeline.fit(X_train, y_train)

# Evaluate on validation set
val_accuracy = pipeline.score(X_val, y_val)
print(f"Validation accuracy: {val_accuracy:.3f}")
```

### 5.3 Hyperparameter Tuning (Optional for v1)

For simple models (logistic regression), skip this.  
For tree-based or neural networks:

```python
from sklearn.model_selection import GridSearchCV

param_grid = {
    'model__C': [0.1, 1, 10],
    'model__penalty': ['l1', 'l2']
}

grid_search = GridSearchCV(pipeline, param_grid, cv=3, scoring='roc_auc')
grid_search.fit(X_train, y_train)

best_model = grid_search.best_estimator_
print(f"Best params: {grid_search.best_params_}")
```

### 5.4 Training Config Log

Record everything for reproducibility:

```json
{
  "model_architecture": "logistic_regression",
  "framework": "scikit-learn",
  "framework_version": "1.3.0",
  "training_config": {
    "max_iter": 1000,
    "random_state": 42,
    "class_weight": "balanced"
  },
  "feature_schema": "feature_schema.json",
  "training_set_size": 200,
  "validation_set_size": 30,
  "training_time_seconds": 2.5,
  "training_timestamp": "2026-04-06T13:00:00Z"
}
```

---

## 6. Phase 5: Evaluation

### 6.1 Holdout Test Evaluation

```python
from sklearn.metrics import accuracy_score, roc_auc_score, classification_report, confusion_matrix

y_pred = best_model.predict(X_test)
y_pred_proba = best_model.predict_proba(X_test)[:, 1]

accuracy = accuracy_score(y_test, y_pred)
auc = roc_auc_score(y_test, y_pred_proba)

print(f"\n=== Holdout Test Results ===")
print(f"Accuracy: {accuracy:.3f}")
print(f"AUC-ROC: {auc:.3f}")
print(f"\nClassification Report:\n{classification_report(y_test, y_pred)}")
print(f"\nConfusion Matrix:\n{confusion_matrix(y_test, y_pred)}")

# Check against criteria
if accuracy >= 0.70 and auc >= 0.75:
    print("✓ PASS: Meets evaluation criteria")
else:
    print("✗ FAIL: Does not meet evaluation criteria")
```

### 6.2 Coverage Check

```python
# Check that model assigns confidence to both classes
pred_proba = best_model.predict_proba(X_test)
min_class_coverage = min(pred_proba[:, 0].mean(), pred_proba[:, 1].mean())
print(f"Coverage (min class prob mass): {min_class_coverage:.3f}")

if min_class_coverage >= 0.15:  # at least 15% for each class
    print("✓ PASS: Coverage check")
```

### 6.3 Temporal Stability (Week-over-Week)

If training regularly, compare model predictions:

```python
# Evaluate on previous week's holdout set
prev_test_file = "test_2026-03-30.csv"
prev_df = pd.read_csv(prev_test_file)

y_prev = prev_df['label']
X_prev = prev_df.drop(['label', 'confidence'], axis=1)

prev_accuracy = best_model.score(X_prev, y_prev)
current_accuracy = accuracy

drift = abs(current_accuracy - prev_accuracy)
print(f"Accuracy drift week-over-week: {drift:.3f}")

if drift < 0.08:
    print("✓ PASS: Temporal stability")
```

### 6.4 Baseline Improvement

```python
improvement = accuracy - baseline_accuracy
print(f"Improvement over baseline: {improvement:.1%}")

if improvement > 0:
    print("✓ PASS: Beats baseline")
else:
    print("✗ FAIL: Does not beat baseline")
```

### 6.5 Feature Importance

```python
# For linear models, use coefficients
if hasattr(best_model.named_steps['model'], 'coef_'):
    coefs = best_model.named_steps['model'].coef_[0]
    importance = pd.DataFrame({
        'feature': X_train.columns,
        'importance': np.abs(coefs)
    }).sort_values('importance', ascending=False)
    print(importance)

# For tree-based models
elif hasattr(best_model.named_steps['model'], 'feature_importances_'):
    importance = pd.DataFrame({
        'feature': X_train.columns,
        'importance': best_model.named_steps['model'].feature_importances_
    }).sort_values('importance', ascending=False)
    print(importance)
```

---

## 7. Phase 6: Artifact Packaging

### 7.1 Serialize Model

```python
import joblib

# Save model pipeline
joblib.dump(best_model, 'model_bundle.joblib')
print("Saved model_bundle.joblib")
```

### 7.2 Create Input Schema

```python
import json

input_schema = {
    "feature_names": list(X_train.columns),
    "feature_types": {
        "artifact_type": "categorical",
        "days_since_creation": "numeric",
        ...
    },
    "categorical_values": {...},
    "numeric_ranges": {...}
}

with open('input_schema.json', 'w') as f:
    json.dump(input_schema, f, indent=2)
```

### 7.3 Create Evaluation Metrics File

```python
eval_metrics = {
    "eval_date": "2026-04-06T13:30:00Z",
    "train_set_size": 200,
    "eval_set_size": 30,
    "holdout_test_size": 30,
    "accuracy": float(accuracy),
    "auc_roc": float(auc),
    "coverage": float(min_class_coverage),
    "temporal_stability": float(drift) if 'drift' in locals() else None,
    "drift_score": 0.07,
    "baseline_accuracy": float(baseline_accuracy),
    "improvement_over_baseline": float(improvement),
    "class_distribution": {
        "approve": float((y_test == 1).mean()),
        "reject": float((y_test == 0).mean())
    },
    "feature_importance": importance.to_dict()
}

with open('eval_metrics.json', 'w') as f:
    json.dump(eval_metrics, f, indent=2)
```

### 7.4 Create Model Metadata

```python
model_metadata = {
    "model_id": "pm_strat_xyz_2026_04_06_v1",
    "model_version": "1.0.0",
    "architecture": "logistic_regression",
    "base_strategy_id": "strat_xyz",
    "training_operator_id": "op_001",
    "operator_domain": "equity",
    "preference_pair_count": 260,
    "preference_source_window": {
        "start": "2026-03-06T00:00:00Z",
        "end": "2026-04-06T00:00:00Z"
    },
    "target_metric": "approval_probability",
    "framework_version": "scikit-learn==1.3.0",
    "created_at": "2026-04-06T13:30:00Z",
    "created_by": "grok_agent"
}

with open('model_metadata.json', 'w') as f:
    json.dump(model_metadata, f, indent=2)
```

### 7.5 Create Training Config

```yaml
model:
  type: logistic_regression
  framework: scikit-learn
  config:
    max_iter: 1000
    random_state: 42
    class_weight: balanced

data:
  features: feature_schema.json
  train_fraction: 0.66
  val_fraction: 0.17
  test_fraction: 0.17
  temporal_split: true

training:
  timestamp: "2026-04-06T13:00:00Z"
  duration_seconds: 2.5
  hyperparameter_tuning: false
```

### 7.6 Package as TAR

```bash
tar -czf preference_model_pm_strat_xyz_2026_04_06_v1.tar.gz \
    model_bundle.joblib \
    input_schema.json \
    eval_metrics.json \
    model_metadata.json \
    training_config.yaml \
    data_extraction_provenance.json

echo "Packaged artifact: preference_model_pm_strat_xyz_2026_04_06_v1.tar.gz"
```

---

## 8. Phase 7: Registry Submission

### 8.1 Prepare Submission

```json
{
  "artifact_type": "model_artifact",
  "artifact_package": "preference_model_pm_strat_xyz_2026_04_06_v1.tar.gz",
  "metadata": {
    "model_family": "preference_model",
    "model_id": "pm_strat_xyz_2026_04_06_v1",
    "model_version": "1.0.0",
    "base_strategy_id": "strat_xyz",
    "created_at": "2026-04-06T13:30:00Z"
  },
  "requested_lifecycle_state": "draft",
  "submitted_by": "grok_agent",
  "submission_timestamp": "2026-04-06T13:35:00Z"
}
```

### 8.2 Submit to Registry

```python
import requests

response = requests.post(
    "http://registry/api/v1/artifacts",
    json=submission_payload,
    headers={"Authorization": "Bearer REGISTRY_TOKEN"}
)

if response.status_code == 201:
    artifact_entry = response.json()
    print(f"✓ Submitted to registry")
    print(f"  artifact_id: {artifact_entry['id']}")
    print(f"  lifecycle_state: {artifact_entry['lifecycle_state']}")
else:
    print(f"✗ Submission failed: {response.status_code}")
    print(response.text)
```

### 8.3 Registry Promotion

Once submitted at `draft`, registry validation step:

1. Automated checks:
   - Extract and validate `eval_metrics.json` against criteria (§5 of PREFERENCE_LEARNING_CONTRACT.md)
   - Verify package structure and all required files present
   - Check that all referenced FB-002 event_ids still exist

2. If checks pass → registry promotes to `candidate`

3. Operator review:
   - Codex (or designate) reviews feature importance and model rationale
   - If approved, registry promotes to `paper`

4. Deployment:
   - At `paper` state, preference model can be:
     - Used by evaluators to score strategy candidates
     - Referenced by reward shaping in RL training (LP-005)

---

## 9. Iteration and Retraining

### 9.1 When to Retrain

Retrain when:
- ≥50 new feedback events accumulated since last training
- Model performance drifts >5% on new holdout data
- Operator preferences shift (e.g., new domain focus)
- Feature schema changes (new artifact types available)

### 9.2 Retraining Workflow

1. Run Phase 1–2 (data extraction, feature engineering) with updated time window
2. Keep prior model version and compare metrics
3. If new model meets criteria and improves over prior, package as new artifact
4. Submit to registry as new `draft` with `base_model_ref` pointing to prior version
5. Registry tracks lineage: prior version → new version

---

## 10. Validation Checklist

Before submitting to registry:

- [ ] Data extraction provenance logged (Phase 1)
- [ ] Feature engineering reproducible from raw data (Phase 2)
- [ ] Train/val/test split temporally separated (Phase 3)
- [ ] Model trained on train set only (Phase 4)
- [ ] Evaluation on holdout test set, not training set (Phase 5)
- [ ] All eval criteria met:
  - [ ] Accuracy ≥ 0.70
  - [ ] AUC-ROC ≥ 0.75
  - [ ] Coverage ≥ 0.85
  - [ ] Temporal stability < 0.08
  - [ ] Improvement over baseline > 0
- [ ] Model serialized and schema documented (Phase 6)
- [ ] Artifact packaged with all required files (Phase 6)
- [ ] Submission payload valid (Phase 7)

---

## References

- `PREFERENCE_LEARNING_CONTRACT.md`: Evaluation criteria and registry contract
- `services/feedback/schema/contract.md`: FB-002 event definitions
- `services/control-plane/persona/lp001/contract.md`: DSPy preference pair construction (pattern reference)
- `services/learning/trl/EV-001_INTEGRATION.md`: How evaluators consume preference models

---

**Document Status**: APPROVED reference workflow for v1 implementation  
**Owner**: Grok  
**Reviewer**: Codex  
**Last Updated**: 2026-04-06

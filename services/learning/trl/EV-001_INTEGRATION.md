# Preference Learning Integration with Evaluators (EV-001)

**Task:** LP-004 (Integration sketch with EV-001)  
**Owner:** Grok / Copilot  
**Date:** 2026-04-06

---

## 1. Overview

This document sketches how preference models (trained via LP-004 TRL workflows) integrate with evaluators and critics (EV-001) to assess and score strategy candidates in the Pantheon registry.

Preference models are **not evaluators themselves**, but rather provide learned representations of operator intent that evaluators can consume as input signals when scoring strategy candidates.

---

## 2. Conceptual Integration Pattern

```
┌─────────────────────────────────────────────────────────────┐
│ Registry (REG-001): Strategy Candidate                       │
│  - strategy_id: strat_xyz                                    │
│  - artifact_version: 1.3.0                                   │
│  - promotion_state: candidate                                │
│  - evaluation_status: awaiting_evaluation                    │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Evaluator (EV-001): Scored Evaluation                        │
│  - Queries registry for artifact metadata                    │
│  - Computes built-in evaluation metrics (Sharpe, drawdown)  │
│  - Queries preference model registry for operator_id         │
│  - CONSUMES: Preference model output                         │
│  - Produces: evaluation_score with confidence               │
│  - Returns: evaluation_result artifact to registry          │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Preference Model (LP-004): Learned Signal                    │
│  - model_id: pm_strat_xyz_2026_04_06_v1                      │
│  - base_strategy_id: strat_xyz                               │
│  - training_operator_id: op_001                              │
│  - State: paper (approved for use in evaluations)           │
│  - Input: artifact metadata (Sharpe, drawdown, etc.)        │
│  - Output: approval_probability [0, 1] with confidence     │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Registry (REG-001): Evaluation Result                        │
│  - artifact_type: evaluation_result                          │
│  - target_artifact_id: (strat_xyz v1.3.0)                   │
│  - evaluator_id: evaluator_primary_v1                        │
│  - scores: {overall: 0.78, preference_alignment: 0.85}      │
│  - promotion_state: candidate (awaiting operator review)    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Evaluator API Contract

Evaluators (EV-001) that consume preference models must follow this contract:

### 3.1 Input: Artifact Being Evaluated

```json
{
  "artifact_id": "strat_xyz_v1.3.0",
  "artifact_type": "strategy_spec",
  "base_strategy_id": "strat_xyz",
  "promotion_state": "candidate",
  "operator_id": "op_001",
  "metadata": {
    "created_at": "2026-04-02T12:00:00Z",
    "mean_sharpe": 1.50,
    "max_drawdown": -0.10,
    "sector_concentration": 0.6,
    "num_positions": 8
  }
}
```

### 3.2 Preference Model Query

When evaluating an artifact for operator_id, the evaluator:

1. **Discovers** the appropriate preference model:
   ```sql
   SELECT * FROM registry_entries
   WHERE artifact_type = 'model_artifact'
   AND metadata->>'model_family' = 'preference_model'
   AND strategy_id = $artifact.base_strategy_id
   AND metadata->>'training_operator_id' = $artifact.operator_id
   AND lifecycle_state = 'paper'
   ORDER BY created_at DESC
   LIMIT 1
   ```

2. **Loads** the preference model artifact from registry (if it exists)

3. **Constructs** feature vector from artifact metadata:
   ```python
   features = {
       "artifact_type": "strategy_spec",
       "days_since_creation": 4.5,
       "prior_promotion_state": "candidate",
       "operator_approve_rate": 0.65,
       "mean_sharpe": 1.50,
       "max_drawdown": -0.10,
       "sector_concentration": 0.6,
       "num_positions": 8
   }
   ```

4. **Invokes** model prediction:
   ```python
   approval_prob = preference_model.predict_proba(features)
   # Returns: P(approve | artifact, operator_context) ∈ [0, 1]
   ```

### 3.3 Output: Evaluation Score Incorporating Preference

Evaluators produce a structured evaluation result:

```json
{
  "artifact_type": "evaluation_result",
  "target_artifact_id": "strat_xyz_v1.3.0",
  "evaluator_id": "evaluator_primary_v1",
  "evaluation_timestamp": "2026-04-06T14:00:00Z",
  "evaluation_metrics": {
    "sharpe_ratio": {
      "value": 1.50,
      "weight": 0.30,
      "score": 0.80,
      "interpretation": "Good risk-adjusted return"
    },
    "max_drawdown": {
      "value": -0.10,
      "weight": 0.25,
      "score": 0.85,
      "interpretation": "Acceptable downside risk"
    },
    "sector_concentration": {
      "value": 0.60,
      "weight": 0.20,
      "score": 0.75,
      "interpretation": "Moderate concentration (acceptable)"
    },
    "preference_alignment": {
      "preference_model_id": "pm_strat_xyz_2026_04_06_v1",
      "approval_probability": 0.82,
      "confidence": 0.88,
      "weight": 0.25,
      "score": 0.82,
      "interpretation": "Operator has shown similar approval patterns for artifacts with these characteristics"
    }
  },
  "overall_score": 0.81,
  "overall_confidence": 0.85,
  "recommendation": "candidate_to_paper",
  "rationale": "Artifact meets quantitative thresholds (Sharpe, drawdown) and aligns well with learned operator preferences (82% approval likelihood). Ready for operator review and paper promotion.",
  "auditable_fields": {
    "features_used": ["artifact_type", "days_since_creation", "mean_sharpe", "max_drawdown", "sector_concentration"],
    "preference_model_version": "1.0.0",
    "preference_model_training_window": "2026-03-06 to 2026-04-06",
    "preference_training_data_size": 260,
    "built_on_fb002_events": true,
    "feature_importance": {
      "mean_sharpe": 0.35,
      "sector_concentration": 0.25,
      "preference_alignment": 0.20,
      "max_drawdown": 0.15,
      "artifact_type": 0.05
    }
  }
}
```

---

## 4. Key Integration Points

### 4.1 Governance: Preference Models Don't Bypass Approval

**Critical**: A high preference_alignment score does NOT automatically promote an artifact.

- Preference models inform evaluators, not registry promotion gates
- Evaluators produce scores; **operators make promotion decisions**
- Registry promotion state is determined by explicit operator approval, not model confidence
- Preference models can be wrong; evaluators contextualize them

### 4.2 Feature Compatibility

Preference model features must be **computable from registry metadata** at evaluation time:

| Feature | Source | Availability |
|---|---|---|
| `artifact_type` | artifact metadata | always |
| `days_since_creation` | artifact.created_at | always |
| `prior_promotion_state` | registry history | always |
| `operator_approve_rate` | feedback store stats | always |
| `mean_sharpe` | prior evaluations | when available (null-safe) |
| `max_drawdown` | prior evaluations | when available (null-safe) |
| `sector_concentration` | artifact metadata | always |

**Implementation**: Evaluators must gracefully handle missing evaluation metrics (e.g., new artifact with no prior Sharpe). Use sensible defaults or imputation.

### 4.3 Preference Model Freshness

Evaluators should respect model versioning and staleness:

- Prefer most recent model (by created_at) that is in `paper` lifecycle state
- If preferred model is >60 days old, flag uncertainty in evaluation output
- If >3 new feedback events have arrived since model training, consider retraining notification
- Track model prediction accuracy over time for audit trails

### 4.4 Feedback Loop (Optional v2+)

For future versions, consider:

1. Evaluator produces evaluation_result with preference_alignment score
2. Operator confirms or overrides
3. If override differs materially from model prediction, log as training signal
4. Next model retraining incorporates override feedback
5. Measurement: operator override rate per model (should decrease as model quality improves)

---

## 5. Implementation Checklist for EV-001

When EV-001 defines the evaluator contract, it should ensure:

- [ ] Evaluators have a `query_preference_model(base_strategy_id, operator_id)` method
- [ ] Preference model queries return a `PreferenceModelArtifact` with feature schema
- [ ] Evaluators handle missing/stale models gracefully (return null for preference_alignment)
- [ ] Feature construction logic is documented (convert artifact metadata → feature vector)
- [ ] Evaluator output includes `preference_alignment` as a scored component
- [ ] Evaluation result includes traceable links to preference model (model_id, version, training window)
- [ ] Evaluators log which preference models were consulted (for audit)
- [ ] Evaluator tests include mock preference models for integration validation

---

## 6. Example: Evaluator Pseudocode

```python
class Evaluator:
    def evaluate_artifact(self, artifact_id: str, operator_id: str) -> EvaluationResult:
        # Load artifact
        artifact = self.registry.get_artifact(artifact_id)
        
        # 1. Compute built-in metrics
        metrics = {
            "sharpe_ratio": artifact.metadata.get("mean_sharpe", None),
            "max_drawdown": artifact.metadata.get("max_drawdown", None),
            "sector_concentration": artifact.metadata.get("sector_concentration", 0),
        }
        
        # 2. Query and load preference model (if available)
        pref_model_artifact = self.registry.query(
            artifact_type="model_artifact",
            metadata={"model_family": "preference_model", "training_operator_id": operator_id},
            strategy_id=artifact.base_strategy_id,
            lifecycle_state="paper"
        ).first_or_none()
        
        preference_alignment = None
        if pref_model_artifact:
            # 3. Load model from registry
            model = self.load_preference_model(pref_model_artifact)
            
            # 4. Construct features from artifact
            features = {
                "artifact_type": artifact.artifact_type,
                "days_since_creation": (now() - artifact.created_at).days,
                "prior_promotion_state": artifact.promotion_state,
                "operator_approve_rate": self.feedback_store.operator_approval_rate(operator_id),
                "mean_sharpe": metrics["sharpe_ratio"],
                "max_drawdown": metrics["max_drawdown"],
                "sector_concentration": metrics["sector_concentration"],
            }
            
            # 5. Score artifact
            approval_prob = model.predict_proba(features)
            preference_alignment = {
                "approval_probability": float(approval_prob),
                "model_id": pref_model_artifact.model_id,
                "model_version": pref_model_artifact.model_version,
            }
        
        # 6. Combine into overall evaluation score
        overall_score = self.combine_scores({
            "sharpe_ratio": metrics["sharpe_ratio"],
            "max_drawdown": metrics["max_drawdown"],
            "sector_concentration": metrics["sector_concentration"],
            "preference_alignment": preference_alignment,
        })
        
        # 7. Return evaluation result
        return EvaluationResult(
            target_artifact_id=artifact_id,
            evaluator_id=self.evaluator_id,
            overall_score=overall_score,
            metrics=metrics,
            preference_alignment=preference_alignment,
            recommendation=self.make_recommendation(overall_score),
        )
```

---

## 7. Non-Negotiable Constraints

1. **Preference models inform, do not override**: Evaluators use preference scores as one input; registry promotion gates remain the single source of truth.
2. **Traceability**: Every preference model query must be logged and auditable.
3. **Graceful degradation**: Evaluators work correctly even if no preference model exists.
4. **Feature immutability**: Preference model feature schema cannot change in-place; retraining produces new models.
5. **Governance boundary**: Preference models train on FB-002 feedback only; evaluators can use any signal for scoring, but preference models remain governance-bound.

---

## 8. Success Criteria for Integration

- [x] Integration pattern articulated (evaluators consume preference models as input signals)
- [x] Evaluator API contract sketched (query, feature construction, output format)
- [x] Non-negotiable constraints documented
- [x] Implementation checklist for EV-001 provided
- [x] Example evaluator pseudocode included
- [ ] EV-001 review and alignment (deferred to EV-001 implementation)
- [ ] Runtime validation in smoke test (deferred to smoke test phase)

---

## 9. Next Steps

1. **EV-001 Implementation**: Use this sketch as a reference when defining the evaluator contract.
2. **Smoke Test Integration**: Build a minimal evaluator that loads a preference model and produces scores.
3. **Feedback**: Codex review this integration pattern for consistency with evaluator/critic contract design.

---

## References

- `PREFERENCE_LEARNING_CONTRACT.md` (§6: Registry Handoff)
- `WORKFLOW_DEFINITION.md` (§7: Registry Submission)
- `services/feedback/schema/contract.md` (FB-002 event definitions)
- `TARGET_ARCHITECTURE.md` (Evaluators and critics layer)

---

**Document Status**: APPROVED reference sketch for EV-001 alignment  
**Owner**: Grok / Copilot  
**Reviewer**: Codex  
**Last Updated**: 2026-04-06

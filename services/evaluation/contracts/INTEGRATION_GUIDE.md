# EV-001 Integration Guide

**Task:** EV-001  
**Owner:** Copilot  
**Reviewer:** Codex  
**Created:** 2026-04-07

This document provides implementation guidance for integrating evaluators and critics with the broader Pantheon governance system, specifically focusing on:

1. Feedback store (FB-003) integration
2. Registry (REG-001) storage and lineage
3. Promotion gate (REG-002) decision flow
4. Preference model (LP-004) consumption

---

## 1. Evaluator + Feedback Store Integration

### 1.1 Query Pattern: Fetching Execution Telemetry

Evaluators query the feedback store adapter to retrieve execution metrics.

```python
from services.telemetry.feedback_adapter import FeedbackStoreAdapter

adapter = FeedbackStoreAdapter(feedback_store_path="/data/feedback_store")

# Query 1: Get all telemetry for a strategy in paper execution mode
telemetry_events = adapter.get_telemetry_for_strategy(
    strategy_id="strat_xyz",
    execution_mode="paper"
)
# Returns: [
#   {"event_id": "t1", "event_type": "pnl_snapshot", "metrics": {"pnl": 1.5}},
#   {"event_id": "t2", "event_type": "drawdown_snapshot", "metrics": {"drawdown_pct": 0.12}},
#   {"event_id": "t3", "event_type": "slippage_observation", "metrics": {"slippage_bps": 2.1}}
# ]

# Query 2: Get telemetry by promotion state
telemetry_by_state = adapter.get_telemetry_by_promotion_state(
    promotion_state="candidate"
)

# Query 3: Advanced query with multiple filters
telemetry_filtered = adapter.query_telemetry(
    strategy_id="strat_xyz",
    promotion_state="candidate",
    created_at_after="2026-04-01T00:00:00Z",
    created_at_before="2026-04-06T23:59:59Z"
)
```

### 1.2 Critical Isolation Guarantee

The feedback store adapter MUST isolate execution telemetry from trader feedback events.

```python
# CORRECT: Evaluator queries only telemetry events
telemetry = adapter.get_telemetry_for_strategy("strat_xyz")
# Result: [pnl_snapshot, drawdown_snapshot, slippage_observation]
# Should NOT include: [approve, edit, reject, rationale]

# WRONG: If adapter returns mixed event families
mixed_results = adapter.query("strategy_id=strat_xyz")
# Result should NOT be: [pnl_snapshot, approve, edit, drawdown_snapshot, reject]
```

**Integration blocker (FB-003)**: The shared feedback store adapter must implement event family filtering. See `services/telemetry/feedback_adapter.py` and `services/feedback/schema/contract.md:226` for details.

### 1.3 Aggregating Metrics for Scoring

Evaluators aggregate telemetry into summary metrics for scoring.

```python
class SimpleEvaluator:
    def compute_metrics(self, telemetry_events):
        """Aggregate telemetry into evaluation metrics."""
        pnl_snapshots = [e for e in telemetry_events if e["event_type"] == "pnl_snapshot"]
        drawdown_snapshots = [e for e in telemetry_events if e["event_type"] == "drawdown_snapshot"]
        
        # Compute summary statistics
        pnl_values = [e["metrics"]["pnl"] for e in pnl_snapshots]
        mean_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else None
        
        # Compute Sharpe ratio (simplified)
        sharpe = self._compute_sharpe(pnl_values) if pnl_values else None
        
        # Get max drawdown
        max_drawdown = min(
            [e["metrics"]["drawdown_pct"] for e in drawdown_snapshots],
            default=None
        )
        
        # Get average slippage
        slippage_snapshots = [e for e in telemetry_events if e["event_type"] == "slippage_observation"]
        avg_slippage_bps = sum(
            [e["metrics"]["slippage_bps"] for e in slippage_snapshots]
        ) / len(slippage_snapshots) if slippage_snapshots else None
        
        return {
            "mean_pnl": mean_pnl,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "avg_slippage_bps": avg_slippage_bps
        }
```

### 1.4 Snapshot Immutability

When creating evaluation_data_snapshot, reference the exact telemetry window queried.

```json
{
  "evaluation_data_snapshot": {
    "target_artifact_version": "1.3.0",
    "target_artifact_checksum": "sha256:abc123...",
    "target_created_at": "2026-04-02T10:00:00Z",
    "execution_telemetry_window": {
      "start_date": "2026-04-02T00:00:00Z",
      "end_date": "2026-04-06T23:59:59Z",
      "execution_mode": "paper",
      "num_executions": 15,
      "event_types_included": ["pnl_snapshot", "drawdown_snapshot", "slippage_observation"]
    }
  }
}
```

This ensures reproducibility and auditability. A future evaluator run can re-query the same time window and verify consistency.

---

## 2. Evaluator → Registry Integration

### 2.1 Storing Evaluation Results

Evaluator outputs are governed artifacts stored in the registry.

```python
from services.registry.registry_client import RegistryClient

registry = RegistryClient()

evaluation_result = {
    "artifact_type": "evaluation_result",
    "strategy_id": "strat_xyz",
    "target_artifact_id": "strat_xyz_v1.3.0",
    # ... (full evaluation_result from contract §3.7)
}

# Register the evaluation result
entry = registry.register(
    artifact_type="evaluation_result",
    content=evaluation_result,
    strategy_id="strat_xyz",
    version="1.0.0",  # Version of this evaluation snapshot
    lineage={
        "parent_registry_ids": ["strat_xyz_v1.3.0"],  # Target artifact
        "source_run_ids": ["evaluator_run_2026_04_07_001"],
        "source_evaluator_id": "evaluator_primary_v1"
    },
    storage_ref={
        "backend": "object_store",
        "path": f"evaluation_results/evaluator_primary_v1/{target_artifact_id}/2026-04-07T14-30-00Z.json"
    }
)

print(f"Evaluation registered: {entry['registry_id']}")
# Output: Evaluation registered: eval-strat_xyz-v1-3-0
```

### 2.2 Querying Evaluations in Registry

Promotion gates and operators can retrieve evaluations.

```python
# Get latest evaluation for a strategy
evaluations = registry.list_by_strategy(
    strategy_id="strat_xyz",
    artifact_type="evaluation_result",
    limit=5,
    order_by="created_at DESC"
)

# Find evaluation for specific target artifact
target_eval = registry.get(registry_id="eval-strat_xyz-v1-3-0")
print(f"Overall score: {target_eval['content']['overall_score']}")
print(f"Recommendation: {target_eval['content']['recommendation']}")
```

### 2.3 Evaluation Result Lineage

Evaluations carry lineage pointing back to:
1. The artifact being evaluated
2. The evaluator system and version
3. The execution telemetry window used
4. Any preference models consulted

```json
{
  "registry_id": "eval-strat_xyz-v1-3-0",
  "lineage": {
    "parent_registry_ids": ["strat_xyz_v1.3.0"],
    "source_run_ids": ["evaluator_run_2026_04_07_001"],
    "source_evaluator_id": "evaluator_primary_v1",
    "source_evaluator_version": "1.0.0"
  }
}
```

This enables:
- Tracing evaluations back to target artifacts
- Auditing which evaluator version was used
- Identifying all evaluations from a particular run
- Finding which evaluations consulted which preference models

---

## 3. Critic + Evaluator Integration

### 3.1 Critic Input: Evaluator Context

Critics read evaluator outputs from the registry.

```python
class PromotionGateCritic:
    def __init__(self, registry_client):
        self.registry = registry_client
    
    def critique_borderline_evaluation(self, target_artifact_id):
        """Critique an artifact where evaluation is borderline."""
        
        # Get target artifact
        target = self.registry.get(registry_id=target_artifact_id)
        
        # Get latest evaluation
        evals = self.registry.list_by_strategy(
            strategy_id=target["strategy_id"],
            artifact_type="evaluation_result",
            limit=1,
            order_by="created_at DESC"
        )
        evaluation = evals[0] if evals else None
        
        if not evaluation:
            return None  # No evaluation to critique
        
        # Determine if score is borderline
        score = evaluation["content"]["overall_score"]
        if 0.4 < score < 0.6:
            # Score is borderline, generate critique
            critique = {
                "critique_trigger": "borderline_score",
                "evaluation_context": {
                    "referenced_evaluation_id": evaluation["registry_id"],
                    "evaluator_overall_score": score
                },
                "findings": self.analyze_score_components(evaluation),
                "key_risks": self.assess_risks(evaluation, target),
                "decision_guidance": self.generate_guidance(evaluation, target)
            }
            return critique
        
        return None  # Score is not borderline
```

### 3.2 Critic Output Registration

Critics register their findings in the registry.

```python
critic_result = {
    "artifact_type": "critique_result",
    "strategy_id": "strat_xyz",
    "target_artifact_id": "strat_xyz_v1.3.0",
    "critique_trigger": "borderline_score",
    "evaluation_context": {
        "referenced_evaluation_id": "eval-strat_xyz-v1-3-0",
        "evaluator_overall_score": 0.52
    },
    # ... (full critique from contract §4.7)
}

entry = registry.register(
    artifact_type="critique_result",
    content=critic_result,
    strategy_id="strat_xyz",
    version="1.0.0",
    lineage={
        "parent_registry_ids": ["strat_xyz_v1.3.0"],
        "source_run_ids": ["critic_run_2026_04_07_001"],
        "referenced_evaluation_ids": ["eval-strat_xyz-v1-3-0"]
    }
)
```

---

## 4. Preference Model Integration (LP-004)

### 4.1 Querying Preference Models

Evaluators query the registry for preference models trained for a specific strategy and operator.

```python
class PreferenceModelAwareEvaluator:
    def evaluate_artifact(self, target_artifact_id, operator_id):
        target = self.registry.get(registry_id=target_artifact_id)
        
        # Query for preference model
        preference_model_artifact = self.query_preference_model(
            base_strategy_id=target["strategy_id"],
            training_operator_id=operator_id
        )
        
        # If model exists and is fresh, use it
        if preference_model_artifact and self.is_model_fresh(preference_model_artifact):
            pref_score = self.score_with_preference_model(
                target, preference_model_artifact
            )
        else:
            pref_score = None
        
        return self.build_evaluation(target, pref_score)
    
    def query_preference_model(self, base_strategy_id, training_operator_id):
        """Find the most recent preference model for a strategy/operator pair."""
        models = self.registry.list_by_strategy(
            strategy_id=base_strategy_id,
            artifact_type="model_artifact",
            limit=10,
            order_by="created_at DESC"
        )
        
        for model in models:
            # Check if this is a preference model for the right operator
            if (model["content"].get("model_family") == "preference_model" and
                model["content"].get("training_operator_id") == training_operator_id and
                model["lifecycle_state"] == "paper"):
                return model
        
        return None  # No suitable model found
    
    def is_model_fresh(self, model_artifact, threshold_days=60):
        """Check if preference model is within freshness threshold."""
        from datetime import datetime, timezone, timedelta
        
        created_at = datetime.fromisoformat(model_artifact["created_at"])
        age = datetime.now(timezone.utc) - created_at
        
        is_fresh = age <= timedelta(days=threshold_days)
        
        if not is_fresh:
            print(f"Warning: Preference model is {age.days} days old (threshold: {threshold_days})")
        
        return is_fresh
```

### 4.2 Feature Construction from Artifact Metadata

Evaluators construct feature vectors from the artifact and registry state.

```python
def build_preference_model_features(self, artifact, registry_client):
    """Construct feature vector for preference model prediction."""
    
    # Artifact-based features
    days_since_creation = (
        datetime.now(timezone.utc) - 
        datetime.fromisoformat(artifact["created_at"])
    ).days
    
    # Registry-based features (from prior evaluations)
    prior_evals = registry_client.list_by_strategy(
        strategy_id=artifact["strategy_id"],
        artifact_type="evaluation_result"
    )
    
    prior_sharpes = [e["content"]["score_components"]["sharpe_ratio"]["value"] 
                     for e in prior_evals if "sharpe_ratio" in e["content"]["score_components"]]
    
    # Feedback-based features
    operator_approve_rate = self.feedback_store.get_operator_approval_rate(
        operator_id=artifact.get("operator_id")
    )
    
    return {
        "artifact_type": artifact["artifact_type"],
        "days_since_creation": days_since_creation,
        "prior_promotion_state": artifact.get("lifecycle_state"),
        "operator_approve_rate": operator_approve_rate,
        "mean_sharpe": statistics.mean(prior_sharpes) if prior_sharpes else None,
        "sector_concentration": artifact["content"].get("sector_concentration", 0.5),
        "num_positions": artifact["content"].get("num_positions", 0)
    }
```

### 4.3 Preference Alignment Score Component

The evaluator includes preference model output as a scored component.

```json
{
  "score_components": {
    "preference_alignment": {
      "model_id": "pm_strat_xyz_v1.0.0",
      "model_version": "1.0.0",
      "model_training_window": "2026-03-01 to 2026-04-06",
      "approval_probability": 0.82,
      "confidence": 0.88,
      "weight": 0.25,
      "dimension_score": 0.82,
      "interpretation": "Operator has shown similar approval patterns for artifacts with these characteristics"
    }
  }
}
```

---

## 5. Promotion Gate Decision Flow

### 5.1 Sequence Diagram

```
┌─────────┐         ┌──────────┐         ┌──────────┐         ┌────────┐
│ Strategy│         │Evaluator │         │Feedback  │         │Registry│
│ Promote │         │System    │         │Store     │         │        │
└────┬────┘         └────┬─────┘         └────┬─────┘         └───┬────┘
     │                   │                     │                   │
     │ evaluate()        │                     │                   │
     │──────────────────>│                     │                   │
     │                   │ query_telemetry()   │                   │
     │                   │────────────────────>│                   │
     │                   │<────────────────────│                   │
     │                   │                     │                   │
     │                   │ query_preference_model()                │
     │                   │────────────────────────────────────────>│
     │                   │<────────────────────────────────────────│
     │                   │                     │                   │
     │                   │ score_artifact()    │                   │
     │                   │────┐                │                   │
     │                   │    │ (internal)     │                   │
     │                   │<───┘                │                   │
     │                   │                     │                   │
     │<──────────────────│ EvaluationResult    │                   │
     │ overall_score: 0.52                    │                   │
     │ recommendation: needs_critique         │                   │
     │                   │                     │                   │
     │ if borderline:    │                     │                   │
     │ critique()        │                     │                   │
     │                   │     (critic runs)   │                   │
     │                   │                     │                   │
     │ get_decision_guidance()                 │                   │
     │────────────────────────────────────────────────────────────>│
     │<─ evaluation_result, critique_result ─┤                    │
     │                   │                     │                   │
     │ approve_promotion()                     │                   │
     │────────────────────────────────────────────────────────────>│
     │<─ promotion_state → "paper"            │                   │
```

### 5.2 Promotion Gate Logic

The registry promotion gate consumes evaluator and critic outputs.

```python
def promote_artifact(registry_id, target_state):
    """Promotion gate that consumes evaluation and critique."""
    
    artifact = self.registry.get(registry_id=registry_id)
    
    # Get latest evaluation
    evals = self.registry.list_by_strategy(
        strategy_id=artifact["strategy_id"],
        artifact_type="evaluation_result",
        limit=1
    )
    
    if not evals:
        raise ValueError(f"No evaluation for {registry_id}")
    
    evaluation = evals[0]["content"]
    
    # Check if score meets minimum threshold
    if evaluation["overall_score"] < PROMOTION_THRESHOLD:
        raise PromotionGateError(f"Score {evaluation['overall_score']} below threshold")
    
    # Check evaluator recommendation
    if target_state == "paper" and evaluation["recommendation"] != "candidate_to_paper":
        # Score is OK but recommendation doesn't match; get critique
        critique = self.get_critique_for_artifact(registry_id)
        if critique and critique["decision_guidance"]["recommended_action"] == "approve_candidate_to_paper":
            # Proceed based on critic guidance
            pass
        else:
            raise PromotionGateError("Evaluator and critic disagree on promotion")
    
    # All checks passed; promote
    return self.registry.promote(registry_id, target_state=target_state)
```

---

## 6. Testing Checklist

### Unit Tests

- [ ] Evaluator computes score components with weights summing to ~1.0
- [ ] Evaluator handles missing telemetry gracefully
- [ ] Evaluator queries preference models and handles None gracefully
- [ ] Critic generates findings with categorized severity levels
- [ ] Critic ranks risks by likelihood × impact
- [ ] Both schemas validate minimal examples

### Integration Tests

- [ ] Evaluator queries FB-003 feedback store without mixing event families
- [ ] Evaluator registers results in registry with correct lineage
- [ ] Critic reads evaluator outputs from registry
- [ ] Critic registers results with reference to evaluation
- [ ] Promotion gate reads both evaluation and critique
- [ ] Promotion gate promotes based on recommendation

### End-to-End Tests

- [ ] Candidate strategy flows through: evaluator → registry → promotion gate → live
- [ ] Borderline score triggers: evaluator → critic → registry → operator review
- [ ] Preference model is queried and tracked in evaluation_data_snapshot
- [ ] Operator can view evaluation score, recommendation, and critique findings

---

## 7. Common Pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Evaluator mixes event families | Trader feedback (approve/edit) pollutes telemetry queries | Use FB-003 adapter's `event_type` filter or implement your own |
| Score weights don't sum to 1.0 | Weights sum to 0.8 or 1.2 | Normalize weights before building score_components |
| Missing evaluation_data_snapshot | Can't reproduce evaluation later | Always capture target version, checksum, telemetry window |
| Preference model staleness ignored | Old models mislead decisions | Check model created_at; flag if >60 days old |
| Critic runs but recommendation ignored | Borderline scores still promote without escalation | Tie promotion gate to both evaluator recommendation AND critic guidance |

---

## 8. References

- `contract.md` – Full evaluator and critic contract
- `services/telemetry/feedback_adapter.py` – FB-003 feedback store adapter
- `services/registry/registry_client.py` – REG-001 registry client
- `services/feedback/schema/contract.md` – FB-001 feedback event families
- `services/learning/trl/EV-001_INTEGRATION.md` – Preference model integration
- `TARGET_ARCHITECTURE.md` – System architecture

---

**Status**: APPROVED for implementation  
**Created**: 2026-04-07  
**Owner**: Copilot  
**Reviewer**: Codex

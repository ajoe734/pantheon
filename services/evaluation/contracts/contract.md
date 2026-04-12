# Evaluator and Critic Output Contracts

**Task:** EV-001  
**Owner:** Copilot  
**Reviewer:** Codex  
**Status:** APPROVED for v1 implementation

---

## 1. Purpose

Evaluators and critics are the scored assessment layers that inform registry promotion decisions.

This contract defines the output shapes for:

- **Evaluators**: systems that score governed artifacts against quantitative and learned criteria
- **Critics**: systems that provide rationale and failure analysis for scored decisions

Both produce **guidance artifacts** that are stored in the registry but do NOT directly trigger promotion. Promotion remains explicit via human or gateway approval.

---

## 2. Design Principles

### 2.1 Scoring is advisory, not directive

Evaluator and critic outputs inform registry promoters and operators, but do not bypass promotion gates.

- Evaluators produce `evaluation_result` artifacts
- Critics produce `critique_result` artifacts
- Registry promotion remains the single source of truth
- Operators make final promotion decisions

### 2.2 Traceability is mandatory

Every evaluation and critique must link back to:

- The artifact being evaluated
- The evaluator/critic that produced it
- The data (execution results, feedback) the assessment was based on
- The preferences or models that informed the score

### 2.3 Registry linkage

Evaluator and critic outputs are themselves governed artifacts. They must:

- Be versioned in the registry
- Carry enough lineage to support audit trails
- Be linkable to downstream promotion decisions
- Potentially be rolled back if they influence reversals

### 2.4 Multiple evaluators and critics can coexist

The system may have multiple evaluators (e.g., risk-focused, alpha-focused, preference-model-based).
Different critics may provide different rationale focuses.
Operators can weight and reconcile multiple scores.

---

## 3. Evaluator Output Contract

Evaluators produce `evaluation_result` artifacts when assessing governed strategy, model, or signal artifacts.

### 3.1 Artifact Type

```
artifact_type: evaluation_result
```

### 3.2 Required Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `registry_id` | string | yes | unique id for this evaluation result entry |
| `artifact_type` | string | yes | always `"evaluation_result"` |
| `strategy_id` | string | yes | stable family id of the **target** artifact being evaluated |
| `target_artifact_id` | string | yes | registry_id of the artifact under evaluation |
| `target_artifact_type` | string | yes | type of artifact being evaluated (e.g., `strategy_spec`, `model_artifact`, `signal_snapshot`) |
| `target_promotion_state` | string | yes | promotion state of the target at evaluation time (e.g., `candidate`, `paper`) |
| `evaluator_id` | string | yes | stable id of the evaluator system that produced this assessment |
| `evaluator_version` | string | yes | semantic version of the evaluator (e.g., `1.0.0`) |
| `evaluation_timestamp` | string | yes | RFC3339 timestamp when evaluation was performed |
| `evaluation_data_snapshot` | object | yes | immutable snapshot of data used for evaluation (see §3.3) |
| `score_components` | object | yes | individual scored dimensions (see §3.4) |
| `overall_score` | number | yes | final composite score ∈ [0, 1] |
| `recommendation` | string | yes | actionable recommendation (see §3.5) |
| `confidence` | number | yes | evaluator confidence in score ∈ [0, 1] |
| `rationale` | string | no | human-readable summary of the evaluation logic |
| `auditable_fields` | object | no | metadata for audit and compliance (see §3.6) |

### 3.3 Evaluation Data Snapshot

Captures the **immutable state** at evaluation time so results remain reproducible.

```json
{
  "evaluation_data_snapshot": {
    "target_artifact_version": "1.2.3",
    "target_artifact_checksum": "sha256:abc123...",
    "target_created_at": "2026-04-01T10:00:00Z",
    "target_lineage": {
      "parent_registry_ids": ["reg-xyz-v1-2-2"],
      "source_run_ids": ["run-research-2026-04-01"]
    },
    "execution_telemetry_window": {
      "start_date": "2026-04-01T00:00:00Z",
      "end_date": "2026-04-06T23:59:59Z",
      "execution_mode": "paper",
      "num_executions": 15
    },
    "feedback_events_considered": {
      "count": 3,
      "types": ["rationale", "edit"]
    },
    "preference_model_used": {
      "model_artifact_id": "pm_strat_xyz_v1",
      "model_version": "1.0.0",
      "model_training_window": "2026-03-01 to 2026-04-06"
    }
  }
}
```

### 3.4 Score Components

Evaluators break down their assessment into weighted dimensions.

```json
{
  "score_components": {
    "sharpe_ratio": {
      "value": 1.50,
      "weight": 0.25,
      "dimension_score": 0.80,
      "interpretation": "Above threshold for candidate promotion"
    },
    "max_drawdown": {
      "value": -0.12,
      "weight": 0.25,
      "dimension_score": 0.75,
      "interpretation": "Within acceptable risk band"
    },
    "replication_fidelity": {
      "value": 0.95,
      "weight": 0.20,
      "dimension_score": 0.95,
      "interpretation": "High replication success across test window"
    },
    "preference_alignment": {
      "model_id": "pm_strat_xyz_v1",
      "approval_probability": 0.78,
      "confidence": 0.82,
      "weight": 0.20,
      "dimension_score": 0.78,
      "interpretation": "Moderate alignment with operator learned preferences"
    },
    "volatility_consistency": {
      "value": 0.12,
      "weight": 0.10,
      "dimension_score": 0.88,
      "interpretation": "Stable risk profile across execution window"
    }
  }
}
```

Rules for score components:

- All `weight` values must sum to 1.0 ± 0.01
- Each `dimension_score` must be ∈ [0, 1]
- `value` is the raw metric; `dimension_score` is the normalized assessment
- `weight` expresses evaluator's priority for this dimension
- `interpretation` provides context for operators

### 3.5 Recommendation Field

The `recommendation` field guides the operator on promotion path options.

Valid values:

| Recommendation | Meaning |
|---|---|
| `candidate_to_paper` | Ready for paper promotion; suggests moving to backtesting/paper execution |
| `candidate_hold` | Not ready yet; recommend refinement or further data collection |
| `paper_to_live` | Ready for live execution; sufficient evidence of stability and alignment |
| `paper_hold` | Not ready for live; recommend extended paper period or further evaluation |
| `retire` | Signal degradation or risk detected; recommend retirement from active rotation |
| `needs_critique` | Evaluation is indeterminate; request critic input before promotion decision |

### 3.6 Auditable Fields

Optional metadata for compliance, audit, and model governance.

```json
{
  "auditable_fields": {
    "evaluator_parameters": {
      "risk_threshold": 0.12,
      "min_replication_fidelity": 0.90,
      "preference_model_staleness_threshold_days": 60
    },
    "features_considered": [
      "sharpe_ratio",
      "max_drawdown",
      "sector_concentration",
      "position_count",
      "replication_fidelity"
    ],
    "missing_data_handling": {
      "field": "sector_concentration",
      "action": "imputed",
      "imputation_value": 0.5,
      "confidence_impact": -0.05
    },
    "previous_evaluations": [
      {
        "registry_id": "eval-xyz-v1",
        "target_artifact_id": "strat_xyz_v1.1.0",
        "overall_score": 0.72,
        "evaluation_timestamp": "2026-03-31T14:00:00Z"
      }
    ],
    "model_performance_metrics": {
      "preference_model_accuracy": 0.81,
      "preference_model_calibration": 0.88
    }
  }
}
```

### 3.7 Minimal Evaluator Output Example

```json
{
  "registry_id": "eval-strat_xyz-v1-3-0",
  "artifact_type": "evaluation_result",
  "strategy_id": "strat_xyz",
  "target_artifact_id": "strat_xyz_v1.3.0",
  "target_artifact_type": "strategy_spec",
  "target_promotion_state": "candidate",
  "evaluator_id": "evaluator_primary_v1",
  "evaluator_version": "1.0.0",
  "evaluation_timestamp": "2026-04-06T14:30:00Z",
  "evaluation_data_snapshot": {
    "target_artifact_version": "1.3.0",
    "target_artifact_checksum": "sha256:abc123",
    "target_created_at": "2026-04-02T10:00:00Z",
    "execution_telemetry_window": {
      "start_date": "2026-04-02T00:00:00Z",
      "end_date": "2026-04-06T23:59:59Z",
      "execution_mode": "paper",
      "num_executions": 10
    }
  },
  "score_components": {
    "sharpe_ratio": {
      "value": 1.48,
      "weight": 0.35,
      "dimension_score": 0.82,
      "interpretation": "Strong risk-adjusted returns"
    },
    "max_drawdown": {
      "value": -0.11,
      "weight": 0.35,
      "dimension_score": 0.78,
      "interpretation": "Acceptable drawdown profile"
    },
    "replication_fidelity": {
      "value": 0.94,
      "weight": 0.30,
      "dimension_score": 0.94,
      "interpretation": "High replication reliability"
    }
  },
  "overall_score": 0.83,
  "recommendation": "candidate_to_paper",
  "confidence": 0.87,
  "rationale": "Artifact meets quantitative thresholds for paper promotion. Sharpe and drawdown are stable across backtesting window. Replication fidelity is strong, indicating low implementation risk."
}
```

---

## 4. Critic Output Contract

Critics provide **rationale and failure analysis** to supplement evaluator scores.

Critics are called when:

- An evaluator score is borderline (e.g., 0.45-0.55) and needs human-context reasoning
- An artifact fails evaluation and needs forensic analysis
- Multiple evaluators disagree and rationale reconciliation is needed
- An approved artifact shows unexpected degradation in execution

### 4.1 Artifact Type

```
artifact_type: critique_result
```

### 4.2 Required Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `registry_id` | string | yes | unique id for this critique result entry |
| `artifact_type` | string | yes | always `"critique_result"` |
| `strategy_id` | string | yes | stable family id of the **target** artifact |
| `target_artifact_id` | string | yes | registry_id of the artifact under critique |
| `target_artifact_type` | string | yes | type of artifact being critiqued |
| `target_promotion_state` | string | yes | promotion state at critique time |
| `critic_id` | string | yes | stable id of the critic system that produced this assessment |
| `critic_version` | string | yes | semantic version of the critic (e.g., `1.0.0`) |
| `critique_timestamp` | string | yes | RFC3339 timestamp when critique was produced |
| `critique_trigger` | string | yes | reason critique was requested (see §4.3) |
| `evaluation_context` | object | no | reference to evaluations being critiqued (if applicable) |
| `findings` | array | yes | list of findings and observations (see §4.4) |
| `key_risks` | array | yes | prioritized risk analysis (see §4.5) |
| `decision_guidance` | object | yes | structured guidance for promotion decision (see §4.6) |
| `rationale` | string | no | human-readable explanation of critique reasoning |

### 4.3 Critique Trigger Types

| Trigger | Meaning | When Used |
|---|---|---|
| `evaluation_disagreement` | Multiple evaluators produced conflicting scores | Ask critic to reconcile divergent assessments |
| `borderline_score` | Evaluator score is near decision boundary | Provide nuance and context for borderline cases |
| `failure_forensics` | Artifact failed evaluation or execution | Analyze root cause of degradation |
| `risk_flagged` | Evaluation raised risk concerns | Provide detailed risk rationale |
| `promotion_override_requested` | Operator wants to override evaluator recommendation | Document reasoning for override |
| `baseline_drift_detected` | Artifact performance degraded unexpectedly | Investigate change sources |

### 4.4 Findings Array

Each finding captures an observation or analysis point.

```json
{
  "findings": [
    {
      "finding_id": "f1",
      "category": "replication_risk",
      "severity": "medium",
      "description": "Sector concentration increased 15% from training window to backtesting window",
      "evidence": [
        "Training sector weights: {tech: 0.35, healthcare: 0.25}",
        "Backtest sector weights: {tech: 0.48, healthcare: 0.20}"
      ],
      "impact": "May indicate drift in signal or overfitting to training window",
      "recommendation": "Investigate signal stability; consider retraining with broader window"
    },
    {
      "finding_id": "f2",
      "category": "execution_fidelity",
      "severity": "low",
      "description": "Slippage averaged 2.1 bps, slightly above historical median of 1.8 bps",
      "evidence": [
        "Slippage median: 2.1 bps",
        "Historical baseline: 1.8 bps",
        "Delta: +0.3 bps (+17%)"
      ],
      "impact": "Expected performance improvement reduced by ~0.02% annually",
      "recommendation": "Monitor for broker-side changes; acceptable variance within tolerance"
    }
  ]
}
```

### 4.5 Key Risks Array

Ranked list of promotion-relevant risks.

```json
{
  "key_risks": [
    {
      "rank": 1,
      "risk_type": "model_degradation",
      "risk_level": "medium",
      "description": "Preference model used in evaluation is 45 days old; new feedback has arrived",
      "likelihood": "medium",
      "impact": "high",
      "mitigation": "Consider updating preference model before final promotion decision"
    },
    {
      "rank": 2,
      "risk_type": "market_regime_sensitivity",
      "risk_level": "low",
      "description": "Artifact performance depends heavily on elevated volatility regime observed during backtest",
      "likelihood": "low",
      "impact": "medium",
      "mitigation": "Request extended paper period to validate performance in current market regime"
    },
    {
      "rank": 3,
      "risk_type": "correlation_change",
      "risk_level": "low",
      "description": "Sector correlations may have shifted post-backtest; requires live validation",
      "likelihood": "low",
      "impact": "low",
      "mitigation": "Standard paper-to-live transition validation"
    }
  ]
}
```

### 4.6 Decision Guidance

Structured advice for promoters and operators.

```json
{
  "decision_guidance": {
    "recommended_action": "approve_candidate_to_paper",
    "confidence_in_recommendation": 0.78,
    "rationale_summary": "Artifact meets core quantitative thresholds despite minor sector drift. Execution fidelity acceptable. Recommend paper period for live-environment stress-testing.",
    "conditions_for_approval": [
      "Operator confirms understanding of sector concentration shift",
      "Preference model can be updated if desired before paper launch",
      "Paper period extends at least 2 weeks to capture diverse market regimes"
    ],
    "conditions_for_rejection": [
      "Sector drift is deemed systematic overfitting requiring redesign",
      "Preference model is deemed too stale to guide final decision"
    ],
    "escalation_recommendation": "If confidence < 0.70, escalate to experienced alpha researcher for final review",
    "next_evaluation_trigger": "after_paper_period_exceeds_2_weeks"
  }
}
```

### 4.7 Minimal Critic Output Example

```json
{
  "registry_id": "crit-strat_xyz-v1-3-0",
  "artifact_type": "critique_result",
  "strategy_id": "strat_xyz",
  "target_artifact_id": "strat_xyz_v1.3.0",
  "target_artifact_type": "strategy_spec",
  "target_promotion_state": "candidate",
  "critic_id": "critic_alpha_v1",
  "critic_version": "1.0.0",
  "critique_timestamp": "2026-04-06T15:00:00Z",
  "critique_trigger": "risk_flagged",
  "evaluation_context": {
    "referenced_evaluation_id": "eval-strat_xyz-v1-3-0",
    "evaluator_overall_score": 0.83
  },
  "findings": [
    {
      "finding_id": "f1",
      "category": "execution_fidelity",
      "severity": "low",
      "description": "Replication fidelity is strong at 0.94",
      "evidence": ["94% of backtests matched implementation behavior"],
      "impact": "Low implementation risk",
      "recommendation": "Proceed with confidence in execution"
    }
  ],
  "key_risks": [
    {
      "rank": 1,
      "risk_type": "market_regime_sensitivity",
      "risk_level": "low",
      "description": "Performance based on elevated volatility during backtest",
      "likelihood": "low",
      "impact": "low",
      "mitigation": "Paper period validation sufficient"
    }
  ],
  "decision_guidance": {
    "recommended_action": "approve_candidate_to_paper",
    "confidence_in_recommendation": 0.86,
    "rationale_summary": "Score is above threshold. Replication is solid. Risks are manageable through standard paper transition. Ready for promotion.",
    "conditions_for_approval": [
      "Operator confidence in signal quality"
    ]
  },
  "rationale": "Score of 0.83 is above the candidate-to-paper threshold (0.75). Replication quality is excellent. The remaining concern is mild market-regime sensitivity, which is addressable through the normal paper validation window."
}
```

---

## 5. Registry Integration

Evaluator and critic outputs are themselves governed artifacts. They flow through the registry as follows:

These entries extend the REG-001 artifact type set as non-executable reference artifacts. They participate in lineage and audit, but are not eligible for EX-001 paper/live loading.

### 5.1 Evaluator Result Registration

```
evaluator_output
  ├── artifact_type: "evaluation_result"
  ├── registry lifecycle_state: "candidate" while active
  ├── non-executable: may later move to "retired" when superseded
  ├── lineage: 
  │   ├── target_artifact_id: registry_id of the strategy being evaluated
  │   └── source_run_ids: [evaluator run id]
  └── storage_ref: Object Store at `evaluation_results/{evaluator_id}/{target_artifact_id}/{timestamp}.json`
```

### 5.2 Critic Result Registration

```
critic_output
  ├── artifact_type: "critique_result"
  ├── registry lifecycle_state: "candidate" while active
  ├── non-executable: may later move to "retired" when superseded
  ├── lineage:
  │   ├── target_artifact_id: registry_id of artifact being critiqued
  │   ├── referenced_evaluation_ids: [evaluation result ids used]
  │   └── source_run_ids: [critic run id]
  └── storage_ref: Object Store at `critique_results/{critic_id}/{target_artifact_id}/{timestamp}.json`
```

### 5.3 Feedback Store Linkage

Evaluator and critic results are NOT events in the feedback store. However, they reference feedback store data:

- Evaluators may query `execution_telemetry_events` for metrics
- Evaluators may query `trader_feedback_events` for preference context
- Critics may cross-reference feedback events in findings

---

## 6. Evaluator and Critic Interaction with Feedback Store

### 6.1 What Evaluators Query from Feedback Store

Evaluators may retrieve:

- **Execution telemetry**: pnl, drawdown, slippage observations for the artifact
- **Trader feedback**: rationale or edits that inform preference model training (indirectly)

Query interface:

```python
telemetry = feedback_adapter.get_telemetry_for_strategy(
    strategy_id="strat_xyz",
    promotion_state="candidate",
    mode="paper"
)

feedback_filters = build_query_filters(
    strategy_id="strat_xyz",
    event_type="rationale",
    created_after="2026-04-01T00:00:00Z",
    created_before="2026-04-06T23:59:59Z",
    limit=100,
)
feedback = trader_feedback_store.list(feedback_filters)
```

### 6.2 Evaluator Isolation

Evaluators must NOT write back to the feedback store. Evaluator outputs go to the registry, not the feedback store.

The feedback store remains event-sourced, immutable, and separate from evaluation results.

---

## 7. Shared Linkage Object

Both evaluators and critics use the shared linkage object from the feedback store contract (FB-001).

The linkage object in evaluation/critique inputs is:

```json
{
  "strategy_id": "strat_xyz",
  "registry_id": "strat_xyz_v1.3.0",
  "artifact_version": "1.3.0",
  "artifact_type": "strategy_spec",
  "promotion_state": "candidate",
  "lineage_ref": "run-research-2026-04-01"
}
```

This ensures:

- Traceability back to the governed artifact
- Consistency with registry and feedback store schemas
- Auditability of evaluation decisions

---

## 8. Acceptance Criteria

✓ **Evaluator output schema defined**:
  - Required fields, score components, recommendations
  - Data snapshot for reproducibility
  - Auditable fields for compliance
  - Examples of minimal and rich evaluation outputs

✓ **Critic rationale shape defined**:
  - Critique triggers and findings array
  - Risk ranking and decision guidance
  - Structured but flexible for different critic implementations
  - Examples of minimal and rich critique outputs

✓ **Registry handoff fields included**:
  - Both evaluator and critic outputs link back to target artifacts
  - Lineage captures source runs and evaluator/critic versions
  - Promotion states and lifecycle transitions documented
  - Storage refs follow registry Object Store naming conventions

---

## 9. Open Items for Future Refinement

1. **Evaluator Ensemble**: How to handle multiple evaluators with conflicting recommendations
   - Planned: reconciliation logic in promotion gate (REG-002 or REG-003)
   
2. **Critic Feedback Loop**: Should operator overrides of evaluator scores retrain preference models?
   - Planned: LP-004 v2 feedback loop
   
3. **Real-time Evaluation**: Should evaluators run continuously or on-demand?
   - Planned: evaluation scheduling in orchestration layer
   
4. **Model Governance**: Should evaluators and critics themselves be versioned and reviewed?
   - Planned: model artifact lifecycle in registry

---

## 10. Review Focus

Codex should review this contract for:

- **Completeness**: Are the required fields sufficient for promotion decisions?
- **Registry fit**: Do evaluation/critic artifacts integrate smoothly with REG-001 and REG-002?
- **Feedback store boundaries**: Are the query patterns and isolation rules clear?
- **Auditable fields**: Are these sufficient for compliance and incident response?
- **Critic scope**: Is the critique trigger list exhaustive? Are findings and risks actionable?
- **Operators' usability**: Can operators easily understand scores and rationale to make decisions?

---

## 11. Success Criteria

- [ ] Evaluator output schema is implementable as JSON Schema or Python dataclass
- [ ] Critic output schema is implementable as JSON Schema or Python dataclass
- [ ] Both schemas fit into existing registry entry model (REG-001)
- [ ] Feedback store query patterns in §6.1 are validated against actual FB-003 adapter
- [ ] Minimal examples pass schema validation
- [ ] Codex review completed and concerns addressed

---

**Document Status**: APPROVED for v1 implementation  
**Owner**: Copilot  
**Reviewer**: Codex  
**Created**: 2026-04-07  
**Last Updated**: 2026-04-07

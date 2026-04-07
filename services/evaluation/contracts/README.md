# Evaluation System Contracts

**Task:** EV-001  
**Owner:** Copilot  
**Reviewer:** Codex  
**Status:** APPROVED v1

This directory defines the output contracts for evaluators and critics — the assessment systems that score governed artifacts for promotion decisions.

---

## Documents

### `contract.md`

**Status**: APPROVED for v1 implementation

Comprehensive specification of:
- Evaluator output shape and semantics
- Critic output shape and semantics
- Registry integration patterns
- Feedback store query boundaries
- Shared linkage object usage
- Acceptance criteria and open items

**Key Sections:**
- §3: Evaluator Output Contract (required fields, score components, recommendations)
- §4: Critic Output Contract (findings, risks, decision guidance)
- §5: Registry Integration (how evaluation/critique artifacts flow)
- §6-7: Feedback Store Interaction (query patterns and isolation)

### `evaluator_result.schema.json`

JSON Schema v7 for evaluator outputs.

**Validates:**
- Required fields from contract §3.2
- Score components structure (weights sum to ~1.0)
- Recommendation enum values
- Data snapshot immutability

**Usage:**
```python
import jsonschema

evaluator_output = {
    "registry_id": "eval-...",
    "artifact_type": "evaluation_result",
    # ... required fields ...
}

jsonschema.validate(evaluator_output, schema)
```

### `critic_result.schema.json`

JSON Schema v7 for critic outputs.

**Validates:**
- Required fields from contract §4.2
- Findings array structure and severity levels
- Risk ranking and likelihood/impact
- Decision guidance recommendations

**Usage:**
```python
import jsonschema

critic_output = {
    "registry_id": "crit-...",
    "artifact_type": "critique_result",
    # ... required fields ...
}

jsonschema.validate(critic_output, schema)
```

---

## Implementation Checklist

### For Evaluator Builders

- [ ] Implement `Evaluator` base class with `evaluate_artifact(artifact_id, operator_id) → EvaluationResult` method
- [ ] Add `query_preference_model(strategy_id, operator_id)` for integration with LP-004 models
- [ ] Compute score components with weights summing to 1.0
- [ ] Capture immutable evaluation_data_snapshot (artifact version, checksum, execution window)
- [ ] Handle missing telemetry gracefully (null-safe metrics)
- [ ] Generate structured rationale tied to score components
- [ ] Produce recommendation enum (candidate_to_paper, candidate_hold, etc.)
- [ ] Implement `recommendation_logic()` mapping scores to promotability
- [ ] Add auditable_fields for compliance and model governance
- [ ] Write integration tests with mock preferences and telemetry
- [ ] Validate output against `evaluator_result.schema.json`

### For Critic Builders

- [ ] Implement `Critic` base class with `critique_artifact(target_id, trigger, evaluation_context=None) → CriticResult` method
- [ ] Support critique triggers: `evaluation_disagreement`, `borderline_score`, `failure_forensics`, `risk_flagged`, `promotion_override_requested`, `baseline_drift_detected`
- [ ] Build findings array with structured evidence and impact
- [ ] Rank key_risks by likelihood × impact
- [ ] Generate decision_guidance with conditions for approval/rejection
- [ ] Support critic chaining (critic reads prior evaluator/critic outputs)
- [ ] Implement forensic analysis for failure cases
- [ ] Validate output against `critic_result.schema.json`
- [ ] Write tests for each critique trigger type

### For Registry Integration (REG-002 / REG-003)

- [ ] Load evaluation_result artifacts as `artifact_type: "evaluation_result"`
- [ ] Load critique_result artifacts as `artifact_type: "critique_result"`
- [ ] Link evaluation/critique to target artifacts via `target_artifact_id`
- [ ] Store in Object Store at:
  - Evaluations: `evaluation_results/{evaluator_id}/{target_artifact_id}/{timestamp}.json`
  - Critiques: `critique_results/{critic_id}/{target_artifact_id}/{timestamp}.json`
- [ ] Ensure lineage includes `source_run_ids` for audit
- [ ] Verify promotion gate uses evaluation/critique recommendations
- [ ] Test escalation to critics when evaluator score is borderline

---

## Key Design Constraints

1. **Evaluators score; operators decide**
   - Evaluation outputs inform but do not bypass promotion gates
   - Registry promotion state remains explicit

2. **Critics provide rationale, not override**
   - Critics reconcile disagreements, analyze failures, provide nuance
   - Critic outputs are advisory, not deterministic

3. **Preference models are one input**
   - Evaluators can consume LP-004 models as signals
   - Preference scores must be traced to model version and training data
   - Model staleness and calibration must be documented

4. **Feedback store isolation**
   - Evaluators query `execution_telemetry_events` and `trader_feedback_events`
   - Evaluators do NOT write to feedback store (only read)
   - Evaluation/critique results go to registry, not feedback store

5. **Traceability is mandatory**
   - Every score component must be traceable to source data
   - Auditable_fields must support compliance reviews
   - Links to preference models, data windows, and feature schemas

---

## Integration Points

### Upstream Dependencies

- **FB-001**: Feedback store contract (shared linkage object, query interfaces)
- **FB-003**: Execution telemetry events (pnl, drawdown, slippage for scoring)
- **REG-001**: Registry entry model (evaluation/critique artifacts stored as governed entries)
- **REG-002**: Promotion gate (uses evaluation/critique recommendations)
- **LP-004**: Preference models (optional input to preference_alignment scoring)

### Downstream Dependents

- **REG-002**: Promotion gate logic reads evaluation/critique scores and recommendations
- **EV-002**: Optimizer handoff uses evaluation results as input
- **Operator dashboards**: Display evaluation scores, recommendations, and critic findings

---

## Testing Strategy

### Unit Tests

- **Evaluator tests**: Validate score calculation, component isolation, snapshot capture
- **Critic tests**: Validate finding categorization, risk ranking, decision logic
- **Schema validation**: Ensure all outputs pass JSON Schema validation

### Integration Tests

- **Evaluator + Feedback Store**: Load telemetry, compute scores, verify isolation
- **Evaluator + Preference Model**: Query preference models, handle missing models, track staleness
- **Critic + Multiple Evaluators**: Reconcile disagreements, generate findings
- **Registry Storage**: Verify evaluation/critique artifacts can be retrieved by target_artifact_id

### Smoke Tests

1. Create a strategy candidate
2. Run evaluator on candidate
3. Check evaluation_result in registry
4. If borderline score, run critic
5. Verify operator can see scores and recommendation

---

## Error Handling

### Evaluator Failures

| Scenario | Behavior |
|---|---|
| Artifact not found | Return evaluation_result with confidence: 0.0, recommendation: "needs_critique" |
| Telemetry missing | Impute with sensible defaults, flag in auditable_fields.missing_data_handling |
| Preference model stale (>60 days) | Include preference_alignment score but flag staleness in rationale |
| No preference model available | Omit preference_alignment from score_components, return other scores |

### Critic Failures

| Scenario | Behavior |
|---|---|
| Evaluation context unavailable | Proceed with findings based on artifact state alone |
| Multiple evaluators inconsistent | Add "evaluation_disagreement" finding, escalate for review |
| Risk assessment fails | Return findings with confidence: 0.0, escalate |

---

## References

- `contract.md` – Full contract specification
- `services/feedback/schema/contract.md` – FB-001 feedback store contract
- `services/registry/contract.md` – REG-001 registry contract
- `services/learning/trl/EV-001_INTEGRATION.md` – Preference model integration sketch
- `TARGET_ARCHITECTURE.md` – System architecture and evaluator/critic role

---

## Versioning

**v1.0.0** (2026-04-07):
- Evaluator output schema with score components and data snapshots
- Critic output schema with findings, risks, and decision guidance
- Registry integration and feedback store isolation patterns

**Future v2.0.0 (planned)**:
- Evaluator ensemble conflict resolution
- Critic feedback loop for model retraining
- Real-time evaluation scheduling
- Evaluator/critic versioning and review workflows

---

## Status

- ✓ Evaluator output schema defined (§3 contract.md)
- ✓ Critic rationale shape defined (§4 contract.md)
- ✓ Registry handoff fields included (§5 contract.md)
- ✓ JSON schemas for validation (evaluator_result.schema.json, critic_result.schema.json)
- ⏳ Implementation checklist provided (this README)
- ⏳ Awaiting Codex review and REG-002/REG-003 integration

**Next Phase**: REG-002 / REG-003 promotion gate logic consumes evaluator/critic outputs

---

**Created**: 2026-04-07  
**Owner**: Copilot  
**Reviewer**: Codex

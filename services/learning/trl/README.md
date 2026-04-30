# services/learning/trl

**Purpose**: Define and govern the integration path for Transformer Reward Learning (TRL) preference-learning workflows in Pantheon.

**Status**: LP-004 contract locked; activation-ready adapter present behind explicit gate
**Owner**: Grok  
**Reviewer**: Codex

---

## Overview

This directory contains the specification for when, how, and under what constraints to integrate TRL preference-learning pipelines into the Pantheon platform.

TRL sits between persona policy optimization (LP-001, DSPy) and full sequential RL (LP-005) in the learning continuum. It focuses on learning trader preferences and approval patterns from governed feedback events, without requiring complex environment modeling or sequential decision-making.

**Key Documents**:

1. **PREFERENCE_LEARNING_CONTRACT.md**: Scope, data sources, approved workflows, governance constraints, and success criteria.
2. **WORKFLOW_DEFINITION.md**: Step-by-step workflow for preference pair construction, model training, evaluation, and registry handoff.

---

## Quick Reference

### When to Use TRL Preference Learning

Use TRL when:
- You want to learn from explicit trader approve/edit/reject feedback (FB-002 events)
- The goal is to optimize a reward model or preference predictor, not directly generate trading actions
- You have labeled preference pairs (human judgments about good vs. bad outcomes)
- You want to improve personalized decision-making (e.g., which strategies this operator prefers)
- You're building a foundation for reward-based policy optimization later

Use DSPy instead if:
- You're optimizing persona decision programs (tool selection, intent classification)
- Training data exists in audit logs, not in the feedback store

Use sequential RL instead if:
- You need to learn sequential decision-making (multi-step action sequences)
- The problem requires exploration-exploitation trade-offs
- You have 2+ years of intraday data and multiple market regimes

Use imitation learning (LP-002) if:
- You want to clone entire trader trajectories (behavior cloning)
- The goal is to learn action distributions directly from demonstrations

---

## Integration Points

### Upstream: FB-002 (Governed Feedback)
- Input: trader approval, edit, reject, and rationale events from the feedback store
- Governance: only events with `actor_role` ∈ `["operator", "approver"]` and `promotion_state` ∈ `["candidate", "paper"]`
- Guarantee: TRL receives only governance-validated feedback, never live execution outcomes directly

### Upstream: LP-001 (DSPy Persona Optimization)
- Reference: DSPy preference pair construction pattern
- Pattern: TRL reuses the "preference store reader" logic to build labeled training examples
- Note: TRL preference pairs are constructed the same way as DSPy pairs, but used for reward modeling instead of prompt optimization

### Downstream: REG-001 (Registry Gate)
- Output: preference model artifact + metadata
- Registry entry shape: `artifact_type=model_artifact` with `metadata.model_family=preference_model`
- Lifecycle: `draft` → `candidate` → `approved`; deployment remains `none` until a separate deployment owner acts
- Governance: artifact must follow registry contract and include governance metadata

### Downstream: EV-001 (Evaluator and Critic Contracts)
- Integration: TRL models can be used as input to evaluators to score candidate strategies
- Pattern: critics can use learned preference models to assess alignment with operator intent
- Candidate preference models are offline-review inputs only; evaluator scoring requires a separately approved preference model.

## Activation-Ready Adapter

The executable adapter is in `adapter/trl_adapter.py`; the container entrypoint is `worker.py`.

- Default behavior is fail-closed. `worker.py` exits without training unless `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1` is set.
- The adapter can ingest normalized FB-002 dictionaries or `TraderFeedbackEvent` objects from `services.feedback.models`.
- Activation-ready data floors are enforced by `validate_activation_ready_dataset()`:
  - at least 200 governed FB-002 source events
  - at least 100 constructed preference pairs
  - at least 2 strategy families represented
- The workflow emits three non-writing handoff artifacts:
  - `artifact_bundle.json`
  - `registry_entry.json`
  - `candidate_packet.json`
- `candidate_packet.json` requests registry review to `candidate` only; it does not write to registry, governance, LEAN, or deployment state.

---

## File Structure

```
services/learning/trl/
├── README.md                    (this file)
├── PREFERENCE_LEARNING_CONTRACT.md   (scope, data, constraints, evaluation)
├── WORKFLOW_DEFINITION.md        (implementation workflow)
├── EV-001_INTEGRATION.md         (integration with evaluators and critics)
├── worker.py                     (gated activation-ready container entrypoint)
├── adapter/trl_adapter.py        (FB-002 ingestion, DPO backend, handoff packets)
└── examples/
    ├── preference_pair_sample.json
    └── training_config_sample.yaml
```

---

## Governance Principles

### 1. Data Source Governance
- TRL receives only governed feedback from FB-002 (trader_feedback_event)
- No direct access to live execution outcomes, market data, or strategy internals
- All training data must be traceable back to registry entries with promotion state validation

### 2. Approval-Before-Use Guarantee
- Preference models cannot influence live promotion decisions directly
- Models are used only as inputs to evaluators, critics, and reward shaping for later learning phases
- Lifecycle promotion still requires human operator approval at each gate

### 3. Preference Model Isolation
- Learned preference models are separate artifacts, not modifications to existing strategies
- A preference model is immutable once committed to the registry
- Retraining produces a new artifact, not an update to an existing one

### 4. Evaluation Before Registry Admission
- Before a preference model can transition to `candidate` state, it must pass:
  - Accuracy threshold on held-out preference prediction
  - Coverage check: model predictions should distribute across approval/rejection/edit classes
  - Drift detection: model should not diverge materially from baseline human behavior

---

## Entry Checklist for TRL Preference Learning

Before training a new preference model, verify:

- [ ] **Data Readiness**
  - [ ] Feedback events collected in FB-002 store
  - [ ] Events have `actor_role` ∈ `["operator", "approver"]`
  - [ ] Events have `promotion_state` ∈ `["candidate", "paper"]`
  - [ ] Preference pairs can be constructed (event linkage to artifact is complete)

- [ ] **Problem Scope**
  - [ ] Clear objective (e.g., "predict this operator's approval probability for new candidates")
  - [ ] Identified decision point (e.g., strategy_id, artifact_type, or portfolio context)
  - [ ] Baseline behavior documented (current accept/reject distribution)

- [ ] **Model Definition**
  - [ ] Model architecture selected (e.g., logistic regression, neural net with transformer encoder)
  - [ ] Input features defined (artifact metadata, trader context, historical patterns)
  - [ ] Output: preference logits or prediction confidence

- [ ] **Evaluation Plan**
  - [ ] Train/val/test splits defined (temporal: past → recent → current)
  - [ ] Holdout evaluation set separated (recent events not seen during training)
  - [ ] Success metric identified (accuracy, AUC-ROC, or custom preference metric)

---

## Training Workflow (High-Level)

1. **Data Extraction**: Query FB-002 feedback store with governance filters
2. **Preference Pair Construction**: Build labeled examples (this action better than that one)
3. **Feature Engineering**: Extract input features from artifact metadata and trader context
4. **Model Training**: Train preference model on constructed pairs
5. **Holdout Evaluation**: Test on held-out recent feedback events
6. **Artifact Packaging**: Bundle model + config + evaluation metrics for registry submission
7. **Registry Submission**: Submit as `draft` artifact (REG-001)
8. **Registry Promotion**: Registry validates artifact, promotes through `candidate` → `paper`

See `WORKFLOW_DEFINITION.md` for step-by-step implementation details.

---

## Success Criteria (LP-004 Acceptance)

- [x] Preference-learning scope documented in PREFERENCE_LEARNING_CONTRACT.md
- [x] Governance boundary articulated (FB-002 as input, no direct live influence)
- [x] Approved model architectures and feature spaces defined
- [x] Evaluation criteria (accuracy, coverage, drift detection) documented
- [x] Preference pair construction logic specified (matching FB-002 events to artifacts)
- [x] Registry handoff contract defined (artifact shape, metadata, promotion states)
- [x] Workflow steps documented in WORKFLOW_DEFINITION.md
- [x] Integration with EV-001 (evaluator contract) sketched
- [x] Activation-ready adapter emits artifact/checksum, registry entry, and candidate handoff packet behind explicit gate

---

## Next Steps

1. **Runtime FB-002 Run**: Point `TRL_PREFERENCE_EVENTS_PATH` at a governed FB-002 export that satisfies the activation-ready floors.

2. **Real Backend Validation**: Run with `TRL_BACKEND=real` in the TRL container after package installation and record the upstream install/runtime result.

3. **Registry Review**: Submit `candidate_packet.json` through the registry review path; do not let the worker write registry state directly.

4. **Evaluator Use**: Allow EV-001 consumption only after the preference model is approved.

---

## References

- `TARGET_ARCHITECTURE.md`: Learning objects and feedback governance principles
- `ROADMAP.md`: LP-001 through LP-005 timeline and dependencies
- `AI_COLLABORATION_GUIDE.md`: Multi-agent collaboration rules
- `services/feedback/schema/contract.md`: FB-001 trajectory and preference store
- `services/learning/rl/`: Sequential RL (LP-005) for comparison
- `services/control-plane/persona/lp001/`: DSPy optimization patterns
- `services/learning/trl/EV-001_INTEGRATION.md`: Integration with evaluators and critics

---

**Document Status**: APPROVED for v1 contract lock; activation-ready adapter documented
**Owner**: Grok  
**Reviewer**: Codex  
**Last Updated**: 2026-04-30

# services/learning/rl

**Purpose**: Define and govern the integration path for sequential RL (FinRL/RLlib + Ray Tune) in Pantheon.

**Status**: LP-005 in progress  
**Owner**: Grok  
**Reviewer**: Codex

---

## Overview

This directory contains the specification for when, how, and under what constraints to integrate sequential RL policies into the Pantheon platform.

**Key Documents**:

1. **PATH_DEFINITION.md**: Entry criteria, search/tuning workflow, registry constraints, and success criteria.
2. **ENV_CONTRACT.md**: RLlib environment interface, data formats, training configurations, and reproducibility guarantees.

---

## Quick Reference

### When to Use RL

Use sequential RL when:
- Supervised alpha (Qlib) has plateaued.
- The problem requires sequential decision-making (not just signal scoring).
- You have 2+ years of intraday OHLCV + order fills.
- The domain benefits from exploration-exploitation trade-offs.

Use Qlib instead if:
- You're optimizing static signal features.
- You're learning trader preferences (use TRL + imitation instead).
- You don't have enough historical data.

### Workflow Overview

```
Candidate Selection (apply entry criteria)
    ↓
RLlib Training (PPO + Ray Tune hyperparameter search)
    ↓
Out-of-Sample Validation
    ↓
Registry Admission (REG-001) + RL evaluation
    ↓
Promotion through Lifecycle (draft → candidate → paper → live)
    ↓
Staged Deployment via LEAN (0.5% → 2% → full allocation)
    ↓
Live Monitoring & Rollback
```

### Key Constraints

| Constraint | Rule |
|-----------|------|
| **Framework** | RLlib (default PPO) or FinRL (simplified single-agent) |
| **Search** | Ray Tune with Population-Based Training (PBT) |
| **Validation Horizon** | Train: 2023–2025-06, Validate: 2025-07–12, Test: 2026-01–03 |
| **Stress Tests** | Must pass market regime shift, slippage sensitivity, vol regime shift (20% degradation max) |
| **Lifecycle** | draft → candidate → paper → live (governance vocabulary per registry contract) |
| **Rollback** | Automatic if single-day loss > 2% or rolling Sharpe drops > 20% |

---

## File Structure

```
services/learning/rl/
├── README.md                 (this file)
├── PATH_DEFINITION.md        (entry criteria + workflow)
├── ENV_CONTRACT.md           (RLlib interface spec)
└── DECISION_TREES_AND_EDGE_CASES.md  (edge case handling)
```

---

## Integration Points

### Upstream: RS-003 (Replication Gate)
- Input: Research strategies identified as RL candidates must pass RS-003 replication gate *before* RL training begins.
- Role: RS-003 validates the source strategy spec (research normalization + first-pass replication).
- Output: Approved strategy candidate ready for registry admission and RL training.

### Downstream: REG-001 (Registry Gate)
- Input: RL policy artifact + metadata (performance, hyperparameters, entry criteria checklist).
- Output: RL policy promoted through lifecycle states (draft → candidate → paper → live).
- Governance: Artifact must follow registry contract (section §3.1 of PATH_DEFINITION.md).

### Downstream: LEAN Execution (EX-001 Loader)
- Input: RL policy artifact at `paper` or `live` state, materialized to Object Store by registry.
- Output: Action inference per decision epoch via RLPolicyExecutor.
- Integration: EX-001 artifact loader validates promotion state and checksum before RLPolicyExecutor receives artifact.

---

## Entry Checklist for RL Candidates

Before training a new RL policy, verify:

- [ ] **Entry Criteria Satisfied**
  - [ ] Supervised alpha exhausted: validation Sharpe ≥ baseline + 0.2
  - [ ] Sequential dependency: problem requires state-dependent action sequences
  - [ ] Exploration benefit: policy should discover actions outside historical distribution
  - [ ] Data sufficiency: 2+ years OHLCV + 3+ market regimes available
  - [ ] Framework match: Problem fits RLlib (multi-agent) or FinRL (single-agent)
  - [ ] Source strategy passed RS-003 replication gate

- [ ] **Problem Statement**
  - [ ] Clear objective (e.g., "optimize exit timing for tech sector")
  - [ ] Reward function defined (Sharpe, return, max drawdown weights)
  - [ ] Baseline performance known (Qlib comparison)

- [ ] **Data Readiness**
  - [ ] Data collected and validated (no gaps, correct OHLCV format)
  - [ ] Train/val/test splits defined (66% / 17% / 17% temporal)
  - [ ] Data checksummed for reproducibility

- [ ] **Configuration Ready**
  - [ ] Ray Tune search space defined (3–8 hyperparameters)
  - [ ] Allocation plan: 16–64 trials, 4–24 hour timeline
  - [ ] Monitoring setup: daily metrics tracking, monthly review cadence

---

## Training Workflow

The RL training flow requires implementation of support infrastructure. The conceptual workflow is:

1. **Data Preparation**: Fetch OHLCV + portfolio state, validate splits (66% train / 17% val / 17% test).
2. **Environment Setup**: Create RLlib environment per ENV_CONTRACT.md, implement state space + reward function.
3. **Ray Tune Search**: Configure PBT or Bayesian search over hyperparameters (3–8 params, 16–64 trials).
4. **Evaluation**: Post-search, evaluate top trials on held-out test period, verify entry criteria gates.
5. **Artifact Packaging**: Bundle trained policy + config + entry criteria report for registry submission.
6. **Registry Submission**: Submit to registry as `draft` lifecycle state (REG-001).
7. **Registry Promotion**: Registry validates artifact structure, promotes through `candidate` → `paper` → `live`.
8. **LEAN Integration**: Once at `paper` or `live`, registry materializes artifact to Object Store; LEAN loader consumes via EX-001 contract.

---

## Success Criteria (LP-005 Acceptance)

- [x] Entry criteria documented in PATH_DEFINITION.md
- [x] RLlib + Ray Tune workflow defined in PATH_DEFINITION.md
- [x] Registry and promotion constraints defined in PATH_DEFINITION.md (lifecycle states: draft/candidate/paper/live)
- [x] Environment interface specified in ENV_CONTRACT.md
- [x] Data format and normalization rules in ENV_CONTRACT.md
- [x] RL artifact model aligned with REG-001/REG-003/EX-001 governance in PATH_DEFINITION.md
- [x] Integration points with RS-003, REG-001, LEAN documented (RS-003 role clarified as upstream)
- [x] Decision tree for when to use RL vs. Qlib in PATH_DEFINITION.md

---

## Next Steps

1. **Codex Review**: Submit PATH_DEFINITION.md and ENV_CONTRACT.md to Codex for review against TARGET_ARCHITECTURE and registry constraints.

2. **Smoke Test** (Grok + Codex): Train a single small RL candidate (1 ticker, 1 year data) to validate:
   - Data loading and normalization
   - RLlib environment step/reset mechanics
   - Ray Tune search execution
   - Artifact packaging and registry submission
   - LEAN policy executor loading

3. **RS-003 + REG-001 Readiness**: Ensure replication gate and registry gate can accept RL policy artifacts per the constraints in PATH_DEFINITION.md.

4. **Integration** (Claude + Codex): LEAN execution engine must implement RLPolicyExecutor to load and inference RL policies at runtime.

---

## References

- `TARGET_ARCHITECTURE.md`: Learning objects and preferred frameworks
- `ROADMAP.md`: LP-001 through LP-005 timeline
- `AI_COLLABORATION_GUIDE.md`: Multi-agent collaboration rules
- `services/research/replication/`: RS-003 replication gate (upstream)
- `services/registry/`: REG-001 registry gate (downstream)
- `lean/`: LEAN execution engine (policy consumer)

---

**Document Status**: Revised to address Codex review comments  
**Reviewer**: Codex  
**Approval Criteria**:
- [x] Lifecycle vocabulary aligned with registry contract (draft/candidate/paper/live)
- [x] RL artifact model aligned with REG-001/REG-003/EX-001 governance metadata
- [x] RS-003 role clarified as upstream research gate (not post-training policy gate)
- [x] Non-existent script and example file references removed
- [ ] Entry criteria alignment with TARGET_ARCHITECTURE confirmed
- [ ] Ray Tune workflow matches RLlib conventions
- [ ] Registry integration is feasible with REG-001
- [ ] LEAN execution contract is implementable via EX-001
- [ ] All links and references are valid

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
Replication Gate (RS-003)
    ↓
Registry Admission (REG-001)
    ↓
Staged Deployment (0.5% → 2% → full allocation)
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
| **Promotion** | Staged: development → staging → approved → production (0.5% → 2% → full) |
| **Rollback** | Automatic if single-day loss > 2% or rolling Sharpe drops > 20% |

---

## File Structure

```
services/learning/rl/
├── README.md                 (this file)
├── PATH_DEFINITION.md        (entry criteria + workflow)
├── ENV_CONTRACT.md           (RLlib interface spec)
└── examples/                 (added as reference implementations)
    └── config_ppo_portfolio.yaml  (example Ray Tune config)
```

---

## Integration Points

### Upstream: RS-003 (Replication Gate)
- Input: Approved research strategies from replication gate.
- Output: RL candidates that satisfy entry criteria → forwarded to replication gate.

### Upstream: REG-001 (Registry Gate)
- Input: RL policy artifact + metadata (performance, hyperparameters, entry criteria checklist).
- Output: RL policy promoted through registry stages (staging → approved → production).

### Downstream: LEAN Execution
- Input: RL policy artifact (model URI + config).
- Output: Action inference per decision epoch.
- Integration: RLPolicyExecutor loads policy, computes action from LEAN signal state.

---

## Entry Checklist for RL Candidates

Before training a new RL policy, verify:

- [ ] **Entry Criteria Satisfied**
  - [ ] Supervised alpha exhausted: validation Sharpe ≥ baseline + 0.2
  - [ ] Sequential dependency: problem requires state-dependent action sequences
  - [ ] Exploration benefit: policy should discover actions outside historical distribution
  - [ ] Data sufficiency: 2+ years OHLCV + 3+ market regimes available
  - [ ] Framework match: Problem fits RLlib (multi-agent) or FinRL (single-agent)

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

## Training Quickstart

### 1. Prepare Data

```bash
# Fetch OHLCV and portfolio state from data warehouse
python3 scripts/fetch_training_data.py \
  --problem exit_timing \
  --tickers MSFT AAPL NVDA \
  --start 2023-01-01 \
  --end 2025-06-30 \
  --output s3://pantheon-data/portfolio_exit_training.parquet
```

### 2. Configure RLlib Trainer

Edit or copy `ENV_CONTRACT.md` config template:

```bash
cp services/learning/rl/examples/config_ppo_portfolio.yaml \
   services/learning/rl/config_ppo_exit_timing.yaml

# Customize config with your tickers, data path, hyperparameter ranges
```

### 3. Run Ray Tune Search

```bash
python3 scripts/train_rl_policy.py \
  --config services/learning/rl/config_ppo_exit_timing.yaml \
  --num_trials 32 \
  --log_dir s3://pantheon-artifacts/rl_training_runs/
```

### 4. Evaluate Best Trial

```bash
python3 scripts/evaluate_rl_policy.py \
  --trial_dir s3://pantheon-artifacts/rl_training_runs/best_trial/ \
  --test_data s3://pantheon-data/portfolio_exit_test.parquet \
  --output report_exit_timing_v1.json
```

### 5. Submit to Registry

```bash
python3 scripts/registry_submit.py \
  --policy_uri s3://pantheon-artifacts/rl_policy_exit_timing_v1/model.zip \
  --config_uri s3://pantheon-artifacts/rl_policy_exit_timing_v1/config.yaml \
  --entry_criteria_report entry_criteria_verified.json \
  --evaluation_report report_exit_timing_v1.json
```

---

## Success Criteria (LP-005 Acceptance)

- [x] Entry criteria documented in PATH_DEFINITION.md
- [x] RLlib + Ray Tune workflow defined in PATH_DEFINITION.md
- [x] Registry and promotion constraints defined in PATH_DEFINITION.md
- [x] Environment interface specified in ENV_CONTRACT.md
- [x] Data format and normalization rules in ENV_CONTRACT.md
- [x] Training configuration template in ENV_CONTRACT.md
- [x] Integration points with RS-003, REG-001, LEAN documented
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

**Document Status**: Ready for Codex review  
**Reviewer**: Codex  
**Approval Criteria**:
- [ ] Entry criteria alignment with TARGET_ARCHITECTURE confirmed
- [ ] Ray Tune workflow matches RLlib conventions
- [ ] Registry integration is feasible with REG-001
- [ ] LEAN execution contract is implementable
- [ ] All links and references are valid

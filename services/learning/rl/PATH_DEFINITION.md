# RL Integration Path Definition

**Task**: LP-005  
**Owner**: Grok  
**Reviewer**: Codex  
**Scope**: Define FinRL/RLlib + Ray Tune integration when sequential RL is justified  
**Status**: Draft  
**Last Updated**: 2026-04-06

---

## Executive Summary

This document defines the entry criteria, search/tuning workflow, and governance constraints for sequential RL (FinRL/RLlib + Ray Tune) in the Pantheon platform.

**Key Principle**: Sequential RL should be an *optional path*, not the default. The platform first uses Qlib for supervised alpha and persona policy optimization. RL is added only when sequential decision-making (e.g., optimal exit timing, position sizing under uncertainty) becomes a primary constraint.

---

## 1. Entry Criteria for Sequential RL

### When to Use Sequential RL

RL is justified when:

1. **Supervised Alpha Exhausted**: Qlib-based feature engineering and signal discovery have reached diminishing returns on the alpha target (e.g., Sharpe ratio is stable across backtests).

2. **Sequential Decision Dependency**: The optimization target requires deciding actions *in sequence* based on live state, not just scoring signals statically.
   - Examples: Optimal exit timing, dynamic position sizing, risk-aware rebalancing.
   - Non-examples: Signal ranking (use Qlib), feature selection (use DSPy + Qlib), TRL preference learning (use imitation first).

3. **Exploration-Exploitation Trade-off**: The domain benefits from exploring multiple action sequences and learning their outcomes, not just repeating the same signal.
   - Example: Testing different liquidation strategies for different market regimes.
   - Non-example: Consistent thesis (use persona policy) or trading rules (use alpha policy).

4. **Sufficient Historical Data**: A minimum of 2+ years of intraday OHLCV + order fills to support realistic simulations.

5. **Approved RL Framework Match**: The problem maps to one of:
   - **RLlib + Ray Tune**: Multi-agent portfolio optimization, model-free learning with scalable search.
   - **FinRL**: Simplified single-agent portfolio management, pre-configured environments.

---

## 2. Search and Tuning Path

### Workflow Overview

```
Governance Layer
    ↓
Research Intake (RS-003: Replication Gate) 
    ↓
RL Candidate Selection (this layer)
    ↓
RLlib Environment Setup + Policy Trainer
    ↓
Ray Tune Hyperparameter Search
    ↓
Backtesting & Evaluation (LEAN)
    ↓
Registry Admission (REG-001 gate)
    ↓
Promotion & Live Execution
```

### 2.1 Candidate Selection

**Input**: Approved research from RS-003 (replication-gate-passed strategies).

**Filter**: Apply entry criteria (§1) to determine if the candidate should try RL or skip to registry admission.

**Output**: RL candidates identified with:
- Problem statement (e.g., "optimize exit timing")
- Data sources (tickers, OHLCV frequency, historical depth)
- Reward function specification (Sharpe, return, max drawdown, etc.)
- Baseline performance (Qlib + alpha policy results)

---

### 2.2 RLlib Environment Setup

#### State Space

**Definition**: Observable market state + portfolio state at each decision point.

**Components**:
- OHLCV bars (current + lookback window, e.g., 20 bars)
- Portfolio holdings (quantity, average cost, current value)
- Account state (cash, margin, risk limits)
- Market microstructure (bid-ask spread, volume profile)
- Portfolio metrics (Sharpe, max drawdown, return YTD)

**Normalization**: Min-max scaling to [0, 1] per asset class per backtesting run.

**Shape**: (batch_size, lookback_window, num_features) for use in RLlib's Policy API.

#### Action Space

**Definition**: Discrete or continuous portfolio adjustments per decision epoch.

**Discrete** (recommended for initial RL):
- Action per asset: {HOLD, BUY_SMALL, BUY_LARGE, SELL_SMALL, SELL_LARGE}
- Or: {-100%, -50%, -20%, 0%, +20%, +50%, +100%} (target position delta)

**Continuous** (if search justifies):
- Action per asset: [-1, 1] normalized position change, mapped to order quantity.

**Constraints**:
- No single action exceeds max position size (e.g., 50% portfolio).
- No single action triggers slippage > acceptable threshold.
- Risk limits enforced: max leverage, max sector concentration.

#### Reward Function

**Formula** (example for exit timing):
```
reward = (pnl_realized - trading_costs) / episode_length
         - penalty_max_drawdown * max_dd
         - penalty_slippage * (estimated_slippage / portfolio_value)
```

**Tuning Parameters**:
- `penalty_max_drawdown`: Weight drawdown risk (typical: 0.1–1.0)
- `penalty_slippage`: Penalize overtrading (typical: 0.01–0.1)
- Hyperparameter search: Ray Tune will sweep these.

#### Episode Length

- **Intraday**: 30–60 decision points per trading day (e.g., 5-min bars).
- **Daily**: 252 decision points per year.
- **Typical**: 252–2000 steps per episode (1 year of daily or intraday data).

---

### 2.3 Policy Trainer (RLlib)

#### Algorithm Selection

**Default**: PPO (Proximal Policy Optimization)
- Stable, sample-efficient.
- Works well for portfolio management with continuous/discrete actions.
- Scales with Ray Tune for distributed search.

**Alternative if state space is large**:
- SAC (Soft Actor-Critic) for continuous action spaces.
- PG (Policy Gradient) for smaller action spaces.

#### Training Loop

1. **Initialization**: Policy initialized with small random weights.
2. **Sampling**: Collect trajectories from simulator (LEAN integration in REG-001).
3. **Batch Updates**: Every N samples, update policy with PPO loss.
4. **Validation**: Every M batches, evaluate on validation period (held-out historical data).
5. **Early Stopping**: Stop if validation reward plateaus for K epochs.

#### Hyperparameters to Tune (via Ray Tune)

- `lr`: Learning rate (sweep: 1e-5 to 1e-3)
- `gamma`: Discount factor (sweep: 0.99 to 0.999)
- `gae_lambda`: GAE lambda for advantage estimation (sweep: 0.95 to 0.99)
- `clip_param`: PPO clip range (fixed: 0.2, or sweep 0.1 to 0.3)
- `entropy_coeff`: Exploration weight (sweep: 0.0 to 0.01)
- Environment reward penalties (as above)

---

### 2.4 Ray Tune Hyperparameter Search

#### Search Space Configuration

**Objective**: Maximize validation Sharpe ratio.

**Search Strategy**:
- **Population Based Training (PBT)** (recommended): Enables dynamic hyperparameter mutation mid-training.
  - Start with broad ranges, then narrow.
  - Mutate underperforming trials and re-seed from best trials.
- **Grid Search** (for small spaces, e.g., 3–4 hyperparameters): Exhaustive.
- **Bayesian Optimization** (for medium spaces, e.g., 5–8 hyperparameters): Efficient sampling.

**Trial Allocation**:
- Small search space: 16–32 trials.
- Medium search space: 32–64 trials.
- Large search space: 64–128 trials.

**Resource Allocation**:
- CPU per trial: 2–4 cores.
- GPU (optional): 1 per 4–8 trials if state/action spaces are large.
- Walltime: 4–24 hours depending on episode length and num trials.

#### Metrics Tracked

For each trial:
- Validation Sharpe ratio (primary metric)
- Mean episode reward
- Max drawdown
- Win rate (% profitable episodes)
- Total trading costs (commissions + slippage)
- Convergence speed (steps to reach 80% of best reward)

---

### 2.5 Candidate Ranking

**Post-Search Evaluation**:

1. Filter trials by validation Sharpe ≥ baseline (Qlib alpha policy).
2. Rank by:
   a. Validation Sharpe (primary).
   b. Stability (std dev of episode rewards, lower is better).
   c. Robustness (performance on held-out test period).
3. Top 3–5 candidates proceed to registry evaluation (REG-001 gate).

---

## 3. Registry and Promotion Constraints

### 3.1 Artifact Model for RL Policies

Each RL policy artifact contains:

```json
{
  "id": "rl_policy_portfolio_exit_v1",
  "type": "rl_policy",
  "framework": "rllib",
  "algorithm": "ppo",
  "created_at": "2026-04-15T10:00:00Z",
  "metadata": {
    "problem_statement": "Optimize exit timing for tech sector positions",
    "entry_criteria_satisfied": {
      "supervised_alpha_exhausted": true,
      "sequential_dependency": true,
      "exploration_benefit": true,
      "data_sufficiency": true
    },
    "training_data": {
      "tickers": ["MSFT", "AAPL", "NVDA"],
      "period": "2023-01-01 to 2025-12-31",
      "frequency": "daily",
      "samples": 504000
    },
    "hyperparameters": {
      "lr": 5e-4,
      "gamma": 0.99,
      "gae_lambda": 0.97,
      "entropy_coeff": 0.002
    },
    "performance": {
      "validation_sharpe": 1.42,
      "validation_return": 0.18,
      "validation_max_dd": 0.12,
      "test_sharpe": 1.38,
      "test_return": 0.16,
      "test_max_dd": 0.14
    },
    "environment": {
      "state_dim": 128,
      "action_space": "discrete",
      "action_size": 5,
      "episode_length": 252
    }
  },
  "model_uri": "s3://pantheon-artifacts/rl_policy_portfolio_exit_v1/model.zip",
  "config_uri": "s3://pantheon-artifacts/rl_policy_portfolio_exit_v1/config.yaml",
  "promotion_status": "staging",
  "promotion_tests": [
    {
      "name": "market_regime_stress",
      "status": "passed",
      "notes": "Tested on 2008 financial crisis data"
    },
    {
      "name": "slippage_sensitivity",
      "status": "passed",
      "notes": "Robust to 2–10 bps additional slippage"
    }
  ]
}
```

### 3.2 Registry Gate (REG-001 + LP-005 Constraints)

Before an RL policy reaches registry, it must pass:

#### Gate 1: Replication Criteria (RS-003)
- **Input**: RL policy trained and evaluated on historical data.
- **Requirement**: First-pass replication gate confirms the policy works in a live simulation (LEAN mock execution).
- **Artifact**: Replication report with trade-by-trade logs.

#### Gate 2: Entry Criteria Verification (LP-005)
- **Requirement**: Trained policy explicitly demonstrates it satisfies §1 entry criteria.
- **Checksum**:
  ```
  ✓ Supervised alpha exhausted: validation Sharpe ≥ baseline + 0.2
  ✓ Sequential dependency: policy uses full state history, not just last bar
  ✓ Exploration benefit: policy discovers actions outside historical distribution
  ✓ Sufficient data: training set covers 2+ years, 3+ market regimes
  ✓ Framework match: RLlib + Ray Tune configuration documented
  ```

#### Gate 3: Out-of-Sample Robustness (REG-001)
- **Train Period**: 2023–01 to 2025–06.
- **Validation Period**: 2025–07 to 2025–12 (held-out).
- **Test Period**: 2026–01 to 2026–03 (live or walk-forward).
- **Requirement**: Test Sharpe ≥ 80% of validation Sharpe (some degradation expected).

#### Gate 4: Stress Tests
- **Market regime shift**: Does policy degrade gracefully in new market conditions?
- **Slippage sensitivity**: Does policy hold PnL with 2–10 bps additional slippage?
- **Vol regime shift**: Does policy adapt if realized vol changes 50%+?
- **Requirement**: No single test fails by > 20% vs. validation performance.

---

### 3.3 Promotion Constraints

#### Versioning

RL policies follow semantic versioning:
- **Major**: Policy objective or reward function changed fundamentally.
- **Minor**: Hyperparameters or training data distribution updated; policy objective unchanged.
- **Patch**: Bug fix in training or environment code; model weights unchanged.

Example: `rl_policy_portfolio_exit_v1.2.3`

#### Promotion Workflow

```
development
    ↓ (pass entry + replication gate)
staging
    ↓ (pass stress tests)
approved
    ↓ (manual approval gate)
production (0.5% portfolio allocation)
    ↓ (monitor 2+ weeks)
production (2% portfolio allocation)
    ↓ (if risk-adjusted Sharpe > baseline + 0.1)
production (full allocation or trading-strategy-specific % cap)
```

#### Live Monitoring

Once in production:

1. **Daily Performance Tracking**:
   - Realized Sharpe, max drawdown, return vs. baseline.
   - Trade count, slippage realized.
   - Market regime (VIX, sector rotation, volatility clustering).

2. **Monthly Review**:
   - Rolling 30-day Sharpe degradation vs. validation performance.
   - Any trades with > 5% individual loss → review for distribution shift.

3. **Retraining Trigger**:
   - If 30-day rolling Sharpe drops > 20% below validation: flag for retraining.
   - If market regime shifts significantly (e.g., VIX doubles): consider retraining on new regime.

#### Rollback Criteria

Immediate rollback to previous version if:
- Single day max loss > 2% portfolio (without prior approval).
- Sharpe drops to baseline − 0.5 for 2+ consecutive weeks.
- Policy behavior diverges from expected action distribution.

---

### 3.4 Integration with LEAN Execution

#### Input Contract

LEAN receives RL policy as:

```python
class RLPolicyExecutor:
    def __init__(self, policy_uri: str, config_uri: str):
        # Load pre-trained RLlib policy and environment config
        self.policy = load_rllib_policy(policy_uri)
        self.env_config = load_config(config_uri)
    
    def get_action(self, state: np.ndarray) -> np.ndarray:
        # state: (batch_size, state_dim) from LEAN.Signal
        # returns: action indices or continuous values
        return self.policy.compute_single_action(state)
    
    def get_metadata(self) -> dict:
        return {
            "framework": "rllib",
            "algorithm": "ppo",
            "state_requirements": self.env_config.state_fields,
            "action_space": self.env_config.action_space,
        }
```

#### Execution Loop

1. **LEAN** collects signal data (OHLCV, portfolio state).
2. **LEAN** normalizes state per RL policy's preprocessing.
3. **RLPolicyExecutor** computes action from state.
4. **LEAN** maps action to orders (discrete → quantity, continuous → % position change).
5. **LEAN** executes orders with broker interaction.
6. **LEAN** logs trade outcome (fill price, slippage, PnL).

---

## 4. Transition Path: When to Migrate from Qlib to RL

### Decision Tree

```
Is supervised alpha exhausted (Sharpe stable for 3+ months)?
  └─ NO → Use Qlib + persona policy, revisit in 1 month
  └─ YES → Is sequential decision-making essential (not just signal scoring)?
           └─ NO → Stay with Qlib + alpha policy (use TRL for preference learning if needed)
           └─ YES → Collect 2+ years intraday data on candidate tickers
                    └─ COLLECTED → Train RL candidate with Ray Tune search (3–7 days)
                    └─ UNDER-COLLECTED → Extend data collection (backfill historical)
                    └─ RL candidate passes entry criteria gate?
                       └─ NO → Revise reward function or state space, retry
                       └─ YES → Pass to replication gate (RS-003) → registry gate (REG-001)
                                └─ PASSED → Staged deployment (0.5% allocation)
                                └─ FAILED → Investigate, retry, or revert to Qlib
```

---

## 5. Success Criteria (LP-005 Acceptance)

- [x] Entry criteria documented (§1)
- [x] Search and tuning path defined (§2: RLlib + Ray Tune workflow)
- [x] Registry and promotion constraints specified (§3)
- [x] Integration points with RS-003, REG-001, and LEAN execution documented
- [x] Decision tree provided for when to use RL vs. Qlib (§4)
- [x] Example RL policy artifact structure in registry (§3.1)

---

## 6. Next Steps

1. **RS-003 Completion** (Grok): First-pass replication gate must be operational before LP-005 is blocked on it.
2. **REG-001 Completion** (Codex + Grok): Registry gate must validate RL policies against the constraints in §3.
3. **LEAN RL Integration** (Claude): Execution engine must support RLlib policy loading and action inference.
4. **Smoke Test** (Grok + Codex): Train a single small RL candidate (e.g., 1 ticker, 1 year of data) to validate the full path end-to-end.

---

## References

- `TARGET_ARCHITECTURE.md`: Learning objects, preferred frameworks (FinRL, RLlib, Ray Tune).
- `ROADMAP.md`: LP-001 through LP-005 learning integration roadmap.
- `audits/oss-alignment/grok_audit.md`: Research workflow constraints.
- `AI_COLLABORATION_GUIDE.md`: Collaboration rules for multi-agent implementation.

---

**Document Status**: Ready for Codex review (Reviewer: Codex)  
**Review Checklist**:
- [ ] Entry criteria alignment with TARGET_ARCHITECTURE
- [ ] Ray Tune workflow is realistic and matches RLlib conventions
- [ ] Registry integration is feasible with existing REG-001 infrastructure
- [ ] LEAN execution contract is implementable
- [ ] Success criteria are verifiable

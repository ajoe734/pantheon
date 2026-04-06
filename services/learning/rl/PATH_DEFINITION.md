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
Research Intake → RS-001 (Ingestion) → RS-002 (Normalization)
    ↓
Replication Gate (RS-003: validates research candidate)
    ↓
RL Candidate Selection (this layer)
    ↓
RLlib Environment Setup + Policy Trainer
    ↓
Ray Tune Hyperparameter Search
    ↓
Backtesting & Evaluation (LEAN)
    ↓
Registry Admission (REG-001 gate) + RL-specific constraints
    ↓
Promotion through Registry (draft → candidate → paper → live)
    ↓
LEAN Execution
```

### 2.1 Candidate Selection

**Input**: Approved research strategies from RS-003 (replication-gate-passed strategy candidates).

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

Each RL policy artifact stored in the registry follows the governance contract (REG-001/REG-003/EX-001):

```json
{
  "registry_id": "rl-policy-portfolio-exit-v1.0.0",
  "artifact_type": "rl_policy",
  "strategy_id": "portfolio_exit_optimization",
  "version": "1.0.0",
  "lifecycle_state": "draft",
  "created_at": "2026-04-15T10:00:00Z",
  "lineage": {
    "parent_registry_ids": [],
    "source_run_ids": ["ray-tune-run-2026-04-15-portfolio-exit"],
    "source_dataset_refs": ["dataset:tech-portfolio-2023-2025-daily"],
    "source_strategy_spec_id": "strategy-spec:portfolio-exit-v1"
  },
  "checksum": "sha256:abc123def456...",
  "storage_ref": "openclaw/registry/portfolio_exit_optimization/1.0.0/artifact.bin",
  "metadata": {
    "framework": "rllib",
    "algorithm": "ppo",
    "problem_statement": "Optimize exit timing for tech sector positions",
    "entry_criteria_satisfied": {
      "supervised_alpha_exhausted": true,
      "sequential_dependency": true,
      "exploration_benefit": true,
      "data_sufficiency": true
    },
    "training_data": {
      "tickers": ["MSFT", "AAPL", "NVDA"],
      "period": "2023-01-01 to 2025-06-30",
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
  "evaluation_summary": {
    "market_regime_stress": {
      "status": "passed",
      "notes": "Tested on 2008 financial crisis data"
    },
    "slippage_sensitivity": {
      "status": "passed",
      "notes": "Robust to 2–10 bps additional slippage"
    }
  },
  "promoted_at": null,
  "approver": null,
  "rollback_target": null
}
```

**Registry Projection for Execution (Object Store)**:

When promoted to `paper` or `live`, the registry materializes a metadata.json projection into Object Store:

```json
{
  "registry_id": "rl-policy-portfolio-exit-v1.0.0",
  "strategy_id": "portfolio_exit_optimization",
  "version": "1.0.0",
  "promotion_state": "live",
  "checksum": "sha256:abc123def456...",
  "artifact_type": "rl_policy",
  "artifact_path": "openclaw/registry/portfolio_exit_optimization/1.0.0/artifact.bin",
  "rollback_target": "rl-policy-portfolio-exit-v0.9.9",
  "metadata": {
    "framework": "rllib",
    "algorithm": "ppo",
    "environment": {
      "state_dim": 128,
      "action_space": "discrete",
      "action_size": 5
    }
  }
}
```

The LEAN execution loader reads this Object Store projection before loading the artifact bytes, validating governance and promotion state. See EX-001 contract for details.

### 3.2 Registry Gate (REG-001 + LP-005 Constraints)

Before an RL policy reaches registry, it must pass:

#### Gate 1: Replication Criteria (RS-003)
- **Input**: RL-selected research strategy candidate that satisfies entry criteria (§1).
- **Requirement**: RS-003 validates the source research strategy before it enters the registry. This is upstream of RL policy training.
- **Artifact**: Replication report confirming strategy normalization and first-pass validation.
- **Next Step**: Approved strategy candidate enters registry as StrategySpec candidate.

#### Gate 2: Entry Criteria Verification (LP-005)
- **Input**: Training run of RL policy based on the approved strategy candidate.
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
draft (new, not yet replication-validated)
    ↓ (pass entry criteria + RS-003 replication gate)
candidate (passed replication gate, ready for registry evaluation)
    ↓ (pass stress tests and REG-001 evaluation)
paper (approved for paper/backtest execution)
    ↓ (pass live monitoring checkpoint)
live (approved for live execution, staged allocation)
    ↓ (if performance degrades beyond thresholds)
retired (no longer approved for promotion or loading)
```

#### Transition Rules

- `draft` → `candidate`: RL policy trained, entry criteria verified, passes replication gate validation.
- `candidate` → `paper`: Out-of-sample robustness confirmed, stress tests passed.
- `paper` → `live`: Manual approval gate passed, ready for staged live deployment (start 0.5% allocation).
- `live` → `retired`: Monitoring detects unrecoverable performance degradation or fundamental policy failure.
- Any state → `retired`: Explicit retirement (e.g., research direction changed).

No direct transitions between `paper` ↔ `live` and other non-adjacent states.

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

LEAN receives RL policy through governed registry channels:

1. **Registry Stage**: RL policy artifact reaches `paper` or `live` in registry (REG-001).
2. **Artifact Materialization**: Registry/promotion tooling materializes artifact bytes + metadata to Object Store at:
   - `openclaw/registry/{strategy_id}/{version}/metadata.json`
   - `openclaw/registry/{strategy_id}/{version}/artifact.bin`
3. **LEAN Loader** (EX-001): Validates metadata.json, verifies promotion state, loads artifact bytes.
4. **Policy Executor**: RLlib policy inference.

```python
class RLPolicyExecutor:
    def __init__(self, registry_metadata: dict, artifact_bytes: bytes):
        # Receives validated governance metadata from artifact loader (EX-001)
        self.registry_id = registry_metadata["registry_id"]
        self.strategy_id = registry_metadata["strategy_id"]
        self.promotion_state = registry_metadata["promotion_state"]  # 'paper' or 'live'
        
        # Load RLlib policy from artifact bytes
        self.policy = load_rllib_policy(artifact_bytes)
        self.env_config = registry_metadata["metadata"]["environment"]
    
    def get_action(self, state: np.ndarray) -> np.ndarray:
        # state: (batch_size, state_dim) from LEAN signal
        # returns: action indices or continuous values
        return self.policy.compute_single_action(state)
    
    def get_metadata(self) -> dict:
        return {
            "registry_id": self.registry_id,
            "strategy_id": self.strategy_id,
            "promotion_state": self.promotion_state,
            "framework": "rllib",
            "algorithm": "ppo",
            "state_requirements": self.env_config["state_dim"],
            "action_space": self.env_config["action_space"],
        }
```

#### Execution Loop

1. **Registry** materializes RL policy to Object Store (after promotion to `paper` or `live`).
2. **LEAN** loader (EX-001) reads metadata.json, validates governance, loads artifact bytes.
3. **RLPolicyExecutor** is initialized with validated registry metadata + artifact.
4. **LEAN Signal** collects state data (OHLCV, portfolio state).
5. **LEAN** normalizes state per RL policy's environment config.
6. **RLPolicyExecutor.get_action()** computes action from normalized state.
7. **LEAN** maps action to orders (discrete → quantity, continuous → % position change).
8. **LEAN** executes orders through broker integration.
9. **LEAN** logs trade outcome (fill price, slippage, PnL) → used for daily monitoring.

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

1. **RS-003 Integration** (Grok): RL-selected research strategies must pass RS-003 replication gate *before* RL training begins (upstream validation).
2. **REG-001 Integration** (Codex + Grok): Registry gate must accept RL policy artifacts and validate against the constraints in §3.
3. **RL Artifact Materialization** (Codex): Registry promotion tooling must materialize RL artifacts to Object Store with correct governance metadata (see §3.1).
4. **LEAN RL Integration** (Claude): Execution engine must implement RLPolicyExecutor and use EX-001 loader to validate promotion state before policy load.
5. **Smoke Test** (Grok + Codex): Train a single small RL candidate (e.g., 1 ticker, 1 year of data) to validate the full path end-to-end.

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

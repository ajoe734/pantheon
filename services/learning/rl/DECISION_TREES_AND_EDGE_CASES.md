# RL Integration: Decision Trees & Edge Cases

**Scope**: Decision support for practitioners implementing LP-005 RL workflow.  
**Status**: Reference guide  
**Audience**: Pantheon research and execution teams

---

## Part A: When to Integrate RL (Decision Tree)

```
START: Should we use sequential RL for this problem?
│
├─ Q1: Is supervised alpha (Qlib + DSPy) reaching diminishing returns?
│  │
│  ├─ NO → Use Qlib. Optimize features, expand training data, or refine reward signal.
│  │        Revisit RL in 1–2 months when Sharpe plateaus.
│  │
│  └─ YES (Sharpe stable for 3+ months, rolling improvements < 0.05)
│     │
│     └─ Q2: Is the optimization target inherently sequential?
│        (i.e., does the optimal action depend on past actions + future state uncertainty?)
│        │
│        ├─ NO → Use deterministic policy or Qlib ranking.
│        │        Examples: signal scoring, feature weighting.
│        │
│        └─ YES (action sequence matters: exit timing, position sizing under uncertainty, regime adaptation)
│           │
│           └─ Q3: Do you have 2+ years of quality intraday OHLCV + fills?
│              │
│              ├─ NO → Backfill historical data (DataBricker, vendor APIs).
│              │        Timeline: 2–4 weeks.
│              │
│              └─ YES
│                 │
│                 └─ Q4: Does exploration benefit this domain?
│                    (i.e., is there significant action distribution coverage gain from learning?)
│                    │
│                    ├─ NO → Use imitation learning (BC/GAIL) or expert system.
│                    │        Don't add exploration cost if deterministic policy is sufficient.
│                    │
│                    └─ YES
│                       │
│                       └─ Q5: Can you define a realistic reward function?
│                          (i.e., reward is observable from live trades, not synthetic/approximate)
│                          │
│                          ├─ NO → Revise problem, use proxy reward, or defer RL.
│                          │        (E.g., if reward is "was this the optimal trade?", too sparse.)
│                          │
│                          └─ YES
│                             │
│                             └─ PROCEED: Train RL candidate with Ray Tune.
│                                Follow PATH_DEFINITION.md § 2 workflow.
```

### Decision Tree Summary Table

| Decision Point | YES Path | NO Path |
|---|---|---|
| Supervised alpha exhausted? | Q2 | Use Qlib, revisit later |
| Inherently sequential problem? | Q3 | Use deterministic policy |
| 2+ years OHLCV + fills? | Q4 | Backfill data, timeline 2–4 weeks |
| Exploration benefit clear? | Q5 | Use imitation learning |
| Realistic reward function? | PROCEED to training | Revise problem or defer |

---

## Part B: Risk Assessment Matrix

Before training, assess risk in this matrix:

```
Risk Factor          |  Low Risk      |  Medium Risk   |  High Risk
─────────────────────┼────────────────┼────────────────┼─────────────────
Data Freshness       | < 3 mo old     | 3–6 mo old     | > 6 mo old
Data Completeness    | 98%+ bars      | 95–98% bars    | < 95% bars
Market Regime Shifts | 1–2 per period | 3–5 per period | > 5 per period
Sharpe Baseline      | > 0.5          | 0.2–0.5        | < 0.2
Reward Signal Delay  | 0–1 bar        | 1–5 bars       | > 5 bars
Action Space Size    | < 25 actions   | 25–125 actions | > 125 actions
─────────────────────┴────────────────┴────────────────┴─────────────────

LOW RISK: Proceed to training. Expect convergence in 24–48 hours.
MEDIUM RISK: Proceed with caution. Increase search trials (32–64).
HIGH RISK: Do NOT train yet. Fix underlying issues first.
```

---

## Part C: Troubleshooting Common Issues

### Issue 1: Policy converges but validation Sharpe < baseline

**Root Causes**:
- Reward function is misaligned (e.g., penalizes winning trades).
- Action space is too large; policy can't explore effectively.
- Market regime in validation is incompatible with training data.

**Fix**:
1. Sanity-check reward function: Plot reward vs. realized PnL for best 10 episodes.
2. Reduce action space: Move from continuous to discrete (5 actions instead of 25).
3. Retrain on uniform market regime (e.g., high-vol only, or regime-specific data).

---

### Issue 2: Policy memorizes training data (high train Sharpe, low test Sharpe)

**Root Causes**:
- Episode length too short (< 20 steps).
- Validation horizon not held-out (model sees validation data during training).
- No regularization (entropy coefficient too low).

**Fix**:
1. Increase episode length: Use full 252-day year or multi-year episodes.
2. Verify data split is truly temporal (no future leakage).
3. Increase entropy coefficient: Try 0.01–0.05 (more exploration).

---

### Issue 3: Training diverges (policy collapses or explodes)

**Root Causes**:
- Learning rate too high.
- Reward scale too large (unbounded).
- Environment state space has NaN or infinity values.

**Fix**:
1. Reduce learning rate: 5e-4 → 1e-4 or 5e-5.
2. Normalize reward: reward = (return - mean) / std (per episode).
3. Add state space validation: Assert all values in [0, 1] before policy input.

---

### Issue 4: Training is too slow (> 24 hours for small dataset)

**Root Causes**:
- Batch size too large (computationally inefficient).
- Number of workers too high (overhead > benefit).
- Episode length too long (too much data per episode).

**Fix**:
1. Tune batch size: Try 1000–4000 (Ray Tune default is 4000).
2. Reduce workers: Try 2–4 instead of 8–16.
3. Shorten episodes for initial tuning (100–200 steps), extend later.

---

### Issue 5: Policy behaves strangely in live execution

**Root Causes**:
- State preprocessing differs between training and execution.
- Market conditions in live are far from training/test distribution.
- Policy was trained on simulated fills; real fills have slippage/latency.

**Fix**:
1. Log state preprocessing in training and execution; assert they match.
2. Compare live market regime (VIX, sector rotation) to training regime.
3. Add slippage buffer: If trained on 5 bps, expect 10 bps in production.
4. Set up daily monitoring: If rolling 5-day Sharpe drops > 20%, flag for review.

---

## Part D: Edge Cases & Special Handling

### Edge Case 1: Very Long Episodes (1000+ steps)

**Problem**: Model can't fit in memory; training is too slow.

**Solution**:
- Truncate episodes to 252–500 steps.
- Use episode fragmentation: Train on sliding windows (e.g., each 3-month period).
- Re-aggregate results post-training (e.g., evaluate on full year as sequence of episodes).

---

### Edge Case 2: Multi-Asset Portfolio with Correlated Returns

**Problem**: Actions on one asset affect others; state space becomes high-dimensional.

**Solution**:
- Use attention-based policy: Multi-head attention over assets.
- Or use independent agents per asset (simpler, but ignores correlations).
- Start with independent agents; add attention if performance gap is significant.

---

### Edge Case 3: Reward Function is Sparse (e.g., only end-of-episode reward)

**Problem**: Policy has little signal during episode; learning is slow.

**Solution**:
- Add shaped rewards: Intermediate rewards for staying in profit, avoiding max DD, etc.
- Use potential-based shaping: reward_shaped = reward + γ * φ(s') - φ(s).
- Or use value function bootstrapping: Critic learns V(state) as proxy for future reward.

---

### Edge Case 4: Regime Change Between Training and Live

**Problem**: Market conditions shift (e.g., VIX doubles, sector rotation). Policy underperforms.

**Solution**:
1. Online retraining: Every month, retrain on latest 2 years data (keep model weights as initialization).
2. Ensemble policies: Maintain separate policies per regime (VIX < 20, VIX 20–40, VIX > 40).
3. Rollback: If live Sharpe < validation − 0.5 for 2 weeks, revert to baseline Qlib policy.

---

### Edge Case 5: Insufficient Exploration (Policy gets stuck in local optimum)

**Problem**: Policy converges to suboptimal actions (e.g., always HOLD).

**Solution**:
- Increase entropy coefficient: 0.002 → 0.01–0.02.
- Use different random seeds: Train 3–5 policies with different seeds, ensemble best 2–3.
- Curriculum learning: Start with simpler reward (e.g., just maximize return, ignore risk), then add penalties.

---

### Edge Case 6: Action Explosion (Policy outputs invalid orders)

**Problem**: Policy tries to sell more shares than it holds, or violates risk limits.

**Solution**:
1. Hard constraints in environment: Clip action to valid range before execution.
2. Penalty-based constraints: Add large negative reward for invalid actions.
3. Mask invalid actions: Set probability to 0 for invalid actions in policy output.

---

## Part E: Typical Training Timeline & Milestones

### Week 1: Setup & Baseline

| Day | Task | Success Criteria |
|-----|------|------------------|
| 1–2 | Data collection & validation | 98%+ bar completeness, no NaN |
| 3–4 | Environment implementation & testing | Random policy runs 100 episodes without error |
| 5 | Compute baseline (Qlib on same data) | Baseline Sharpe documented |
| 6–7 | Config draft & small search (4 trials) | Search completes without error |

### Week 2: Full Search

| Day | Task | Success Criteria |
|-----|------|------------------|
| 8–12 | Ray Tune search (32–64 trials) | Best trial validation Sharpe > baseline |
| 13–14 | Evaluation & ranking | Top 3 candidates identified |

### Week 3: Validation & Registry Submission

| Day | Task | Success Criteria |
|-----|------|------------------|
| 15–16 | Test set evaluation & stress tests | Test Sharpe ≥ 80% of validation |
| 17 | Entry criteria verification | All 5 criteria signed off |
| 18–19 | Replication gate (RS-003) | First-pass replication confirmed |
| 20–21 | Registry submission & review (REG-001) | Codex/Grok review passed |

---

## Part F: Rollback & Monitoring Checklist

### Daily Monitoring

```
✓ Single-day max loss < 2% portfolio value
✓ Daily Sharpe in reasonable range (e.g., -0.5 to +3.0)
✓ Trade count reasonable (e.g., 1–10 per day)
✓ Slippage costs < 10 bps
✓ No error logs or missing observations
```

### Weekly Review

```
✓ 5-day rolling Sharpe ≥ validation − 0.3
✓ Max drawdown YTD < historical max DD + 2%
✓ Win rate ≥ 45%
✓ Compare to baseline Qlib policy
```

### Monthly Review

```
✓ 30-day rolling Sharpe ≥ validation − 0.5
✓ Action distribution matches training (no mode collapse)
✓ Compare to other deployed policies
✓ Identify regime changes or data quality issues
```

### Rollback Triggers

```
IMMEDIATE ROLLBACK if:
✓ Single-day loss > 2%
✓ 2-day max loss > 3%
✓ 5-day rolling Sharpe < validation − 1.0
✓ Unexplained divergence from expected action distribution
✓ Data quality issue (e.g., missing bars, staleness)

REVIEW & CONSIDER ROLLBACK if:
✓ 10-day rolling Sharpe < validation − 0.7
✓ Win rate drops below 40%
✓ Market regime changed significantly (document in log)
✓ Competitor/market event causes unexpected losses
```

---

## Part G: Documentation Checklist

Before deploying an RL policy, ensure:

```
TRAINING DOCUMENTATION
✓ Config YAML with all hyperparameters
✓ Data checksums (training, validation, test)
✓ Random seeds (for reproducibility)
✓ Best trial metrics (train/val/test Sharpe, return, DD)
✓ Training run logs (convergence plot)

EVALUATION DOCUMENTATION
✓ Stress test results (regime shift, slippage, vol)
✓ Entry criteria verification form (all 5 signed off)
✓ Replication gate report (RS-003)
✓ Registry submission form (REG-001)

DEPLOYMENT DOCUMENTATION
✓ Model artifact checksums
✓ LEAN integration checklist
✓ Monitoring dashboard links
✓ Rollback procedure (point to automation)
✓ Team on-call schedule

LIVE OPERATIONS
✓ Daily monitoring log
✓ Weekly review summary
✓ Monthly performance report
✓ Incident/degradation log
```

---

## References

- PATH_DEFINITION.md: Entry criteria, workflow, registry constraints
- ENV_CONTRACT.md: RLlib interface, data formats, training configs
- TARGET_ARCHITECTURE.md: Learning objects and framework selection
- ROADMAP.md: LP-001 through LP-005 timeline

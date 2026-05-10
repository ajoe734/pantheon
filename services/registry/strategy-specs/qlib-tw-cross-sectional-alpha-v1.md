# StrategySpec: TW Cross-Sectional Equity Supervised Alpha v1

**Registry Artifact Type**: `strategy_spec`
**Registry ID**: `qlib-tw-cross-sectional-alpha-spec-v1`
**Strategy ID**: `tw-cross-sectional-equity-alpha`
**Version**: `1.0.0`
**Artifact State**: `draft`
**Deployment Stage**: `none`
**Task**: QLIB-ACT-001
**Owner**: Claude2
**Reviewer**: Codex
**RS-003 Gate**: candidate submission — Codex review approved 2026-05-10; QLIB-ACT-002/003 pending
**Created**: 2026-05-10
**Last Updated**: 2026-05-10

---

## 1. Problem Statement

Predict the cross-sectional relative performance of Taiwan-listed equities over a short forward horizon. The goal is an alpha signal that ranks instruments by expected excess return within the universe so that a downstream portfolio optimizer (not this model) can construct a long-short or long-only allocation.

This is a **supervised prediction problem**: the model learns from historical OHLCV features to predict a continuous forward return or a cross-sectional rank label. It is not a sequential decision-making problem and does not produce buy/sell/hold actions.

Target outcome: a per-instrument score that can be consumed as a factor input by the LEAN execution layer after governed registry promotion. The signal is scoring-only; it does not route directly to broker, order, or capital systems.

---

## 2. Universe

| Attribute | Specification |
|---|---|
| Market | Taiwan equity market |
| Exchange segments | TWSE (Taiwan Stock Exchange) listed + TPEx (Taipei Exchange) listed |
| Instrument count (minimum) | ≥ 50 instruments; v1 target ≈ top 100 by 30-day average turnover |
| Instrument type | Common stock (ordinary shares); exclude warrants, REITs, ETFs, preferred shares |
| Universe refresh | Static for v1 (defined at data-prep time); dynamic reconstitution deferred to v1.5+ |
| Delistings | Drop instrument from training set at delisting date to avoid survivorship bias |
| Currency | TWD (New Taiwan Dollar); all price/volume fields in local currency |
| Market sessions | Regular auction session only; pre/post market and block trades excluded in v1 |

Minimum activation-ready floor (aligned with `services/learning/qlib/ACTIVATION_CRITERIA.md §1.3` and `services/research/qlib/adapter/qlib_adapter.py validate_activation_ready_dataset()`):

- ≥ 50 instruments
- ≥ 2.0 years of daily source history per instrument (≥ 504 trading days)
- `data_frequency = daily`

---

## 3. Label Definition

| Label attribute | Value |
|---|---|
| Label type | Forward return (continuous regression) |
| Primary horizon | 5 trading days (1-week forward return) |
| Formula | `label_5d = (close[t+5] − close[t]) / close[t]` |
| Alternative horizon | 1 trading day (`label_1d = (close[t+1] − close[t]) / close[t]`); used for IC evaluation |
| Cross-sectional normalization | Label is z-scored within the universe on each trading day before training |
| Look-ahead guard | Labels are computed from point-in-time close prices only; no intraday or after-close data may enter the label window |
| PIT compliance | Aligned with `validate_production_dataset_proof()` PIT contract in `services/research/qlib/adapter/production_activation.py` |

Rationale for 5-day horizon: the 5-day window reduces noise from daily bid-ask bounce and short-term reversal while remaining short enough for the supervised framing to remain informative. The 1-day forward return is used as a secondary IC measurement and is available as `label_1d` for evaluation runs.

---

## 4. Feature Set (v1)

All features are computed per instrument per trading day from point-in-time OHLCV. Cross-sectional ranks are computed within the universe on the same trading day.

| Feature | Formula / description | Window |
|---|---|---|
| `momentum_1d` | `(close[t] − close[t−1]) / close[t−1]` | 1d |
| `momentum_5d` | `(close[t] − close[t−5]) / close[t−5]` | 5d |
| `momentum_10d` | `(close[t] − close[t−10]) / close[t−10]` | 10d |
| `momentum_20d` | `(close[t] − close[t−20]) / close[t−20]` | 20d |
| `volatility_10d` | Rolling 10d std of daily returns | 10d |
| `volatility_20d` | Rolling 20d std of daily returns | 20d |
| `volume_change_1d` | `(volume[t] − volume[t−1]) / volume[t−1]` | 1d |
| `volume_change_5d` | `(volume[t] − volume[t−5]) / volume[t−5]` | 5d |
| `volume_price_divergence_5d` | Difference between 5d price momentum and 5d volume momentum | 5d |
| `rsi_14d` | 14-day Relative Strength Index | 14d |
| `zscore_return_10d` | 10d cumulative return z-scored cross-sectionally on day t | 10d × cross-sect |
| `zscore_volume_5d` | 5d average volume z-scored cross-sectionally on day t | 5d × cross-sect |
| `high_low_range_5d` | `(max_high[t−4:t] − min_low[t−4:t]) / close[t]` | 5d |

Deep learning feature sets (LSTM, Transformer) and order-book microstructure features are deferred to v1.5+.

---

## 5. Horizon and Training Splits

| Split | Period | Purpose |
|---|---|---|
| Train | First 70% of history by date (roughly 2022-01 to 2024-12 given 2-year minimum) | Model fitting |
| Validation | Next 15% by date | Early stopping, hyperparameter search |
| Test (holdout) | Last 15% by date, walk-forward | Out-of-sample robustness report |

Splits are strictly time-ordered with no leakage between windows.

Minimum backtest output required before RS-003 candidate gate:

- Annualized return, Sharpe ratio, max drawdown, information ratio, turnover rate
- IC (information coefficient) and IR (information ratio) on the test window
- Test IC ≥ 0.03 (RS-003 gate threshold from `ACTIVATION_CRITERIA.md §3.2`)
- Test Sharpe ≥ 80% of validation Sharpe

---

## 6. Model: LightGBM

**Selected model**: LightGBM (`LGBModel` via `pyqlib==0.9.6`)

### 6.1 Configuration (v1 baseline)

```yaml
model:
  class: LGBModel
  kwargs:
    loss: mse
    num_leaves: 63
    max_depth: 8
    learning_rate: 0.05
    n_estimators: 200
    subsample: 0.8
    colsample_bytree: 0.8
    random_state: 42
    n_jobs: -1
```

### 6.2 Why Supervised LightGBM — Not RL

| Consideration | Supervised LightGBM | RL (LP-005) |
|---|---|---|
| Problem type | Predict continuous forward return or cross-sectional rank → **prediction** | Learn policy over state-action sequences → **sequential decision-making** |
| Output | Per-instrument score (ranking signal) | Buy/sell/hold action per instrument |
| Data requirement | 2+ years daily OHLCV tabular → available now | Dense intraday + order fills + reward specification → not yet available |
| Sample efficiency | High: LightGBM converges well on O(10k–100k) row tabular data | Low: RL requires orders of magnitude more samples for policy exploration |
| Interpretability | Feature importance, SHAP values tractable | Policy interpretation harder; risk governance requires additional explainability tooling |
| Governance readiness | Registry artifact is a deterministic scoring model; audit trail straightforward | Stochastic policy execution creates more complex audit and rollback requirements |
| Activation readiness | Qlib LightGBM adapter is already implemented, gated, and smoke-tested in repo | RL path (LP-005) entry criteria not yet met; sequential decision-making not the current need |

**Conclusion**: The TW cross-sectional alpha target is a prediction problem, not a sequential decision problem. LightGBM on tabular OHLCV features is the most sample-efficient, interpretable, and governance-compatible baseline. RL is explicitly deferred until supervised alpha shows diminishing returns per `ACTIVATION_CRITERIA.md §4` transition tree.

### 6.3 Why Not TRL

TRL (LP-004) is designed for preference learning from human feedback events. The cross-sectional alpha target is a market-observable outcome (forward return) with no human preference labeling involved. TRL is not applicable here. The TRL path remains independent and governed by `services/learning/trl/ACTIVATION_CRITERIA.md`.

---

## 7. Evaluation Metrics

| Metric | Target (RS-003 gate) | Description |
|---|---|---|
| IC (Information Coefficient) | ≥ 0.03 on test window | Rank correlation between predicted score and realized forward return |
| IR (Information Ratio) | Report (no hard floor for v1 draft) | IC mean / IC std over rolling windows |
| Annualized return | Report | Top-K long-only backtest annualized return |
| Sharpe ratio | Test Sharpe ≥ 80% of validation Sharpe | Risk-adjusted return |
| Max drawdown | Report | Peak-to-trough drawdown in backtest |
| Turnover rate | Report | Average daily portfolio turnover in Top-K strategy |
| Feature stability | Top-5 importance rank correlation ≥ 0.7 between train and validation | Feature set reliability check |

---

## 8. RS-003 Replication Gate Evidence

This StrategySpec is the candidate submission for the RS-003 replication gate. The gate is satisfied when:

1. This StrategySpec document is registered as `artifact_state=candidate` after reviewer approval.
2. The governed dataset proof (≥50 TWSE/TPEx instruments, ≥2 years OHLCV) passes `validate_production_dataset_proof()`.
3. The LightGBM activation run (QLIB-ACT-003) produces test IC ≥ 0.03 and test Sharpe ≥ 80% of validation Sharpe.
4. `build_production_activation_packet()` emits a non-writing `candidate_packet` with `artifact_state=draft`, `deployment_summary.current_stage=none`, `registry_service_only` write authority.

Gate status as of 2026-05-10:

| Gate step | Status | Notes |
|---|---|---|
| StrategySpec authored | done — Codex review approved 2026-05-10 | `artifact_state=draft` |
| Governed dataset (QLIB-ACT-002) | pending | requires TWSE/TPEx OHLCV dataset packet |
| LightGBM activation run (QLIB-ACT-003) | pending | depends on QLIB-ACT-002 dataset |
| Registry admission | pending review | no production registry write until Codex reviewer approves |

---

## 9. Candidate Registry Artifact Record

This block is the **draft candidate artifact record** for downstream tasks QLIB-ACT-002 (governed dataset packet) and QLIB-ACT-003 (LightGBM activation run) to reference. It is not a registry write — it is the submission record.

```json
{
  "registry_id": "qlib-tw-cross-sectional-alpha-spec-v1",
  "artifact_type": "strategy_spec",
  "strategy_id": "tw-cross-sectional-equity-alpha",
  "version": "1.0.0",
  "artifact_state": "draft",
  "created_at": "2026-05-10T00:00:00Z",
  "lineage": {
    "parent_registry_ids": [],
    "source_run_ids": [],
    "source_dataset_refs": [],
    "source_strategy_spec_id": null
  },
  "storage_ref": {
    "backend": "repo",
    "path": "services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md"
  },
  "checksum": "pending — assigned after QLIB-ACT-002 dataset binding",
  "deployment_summary": {
    "current_stage": "none"
  },
  "metadata": {
    "framework": "qlib",
    "model_family": "lightgbm",
    "market": "TW",
    "universe": "TWSE + TPEx listed equities",
    "label": "5d_forward_return_zscored",
    "horizon": "5 trading days",
    "problem_statement": "Cross-sectional equity alpha signal prediction for TW equity universe"
  },
  "approved_at": null,
  "approver": null,
  "rollback_target": null
}
```

**Downstream reference key**: `strategy-spec:qlib-tw-cross-sectional-alpha-spec-v1`

QLIB-ACT-002 must cite `source_strategy_spec_id = "qlib-tw-cross-sectional-alpha-spec-v1"` in the governed dataset packet.
QLIB-ACT-003 must cite this registry ID in the `lineage.source_strategy_spec_id` of the model artifact candidate packet.

---

## 10. Acceptance Checklist (RS-003 Replication Gate)

- [x] Problem statement defined (§1)
- [x] Universe defined with minimum floors (§2)
- [x] Label definition and PIT compliance stated (§3)
- [x] Feature set v1 documented (§4)
- [x] Horizon and train/validation/test splits defined (§5)
- [x] Model family selected and configured (§6.1)
- [x] Why LightGBM and not RL explicit (§6.2)
- [x] Why not TRL explicit (§6.3)
- [x] Evaluation metrics and RS-003 gate thresholds stated (§7)
- [x] RS-003 gate evidence block and pending steps cited (§8)
- [x] Candidate registry artifact record issued with `artifact_state=draft`, `deployment_summary.current_stage=none` (§9)
- [ ] Governed dataset proof attached (QLIB-ACT-002 — pending)
- [ ] LightGBM activation run results with IC ≥ 0.03 (QLIB-ACT-003 — pending)
- [ ] Production registry admission (pending Codex reviewer approval of this document)

---

## 11. Constraints and Safety Rules

- This StrategySpec is `artifact_state=draft`. No production registry write may occur before Codex reviewer approves this document.
- `deployment_summary.current_stage=none` must be preserved until the full activation sequence (QLIB-ACT-001 → QLIB-ACT-002 → QLIB-ACT-003) is complete and registry admission is granted.
- The signal output from any model trained against this spec is scoring-only. It must not route to broker, order, paper/canary/live execution, or capital binding systems before governance promotion.
- Qlib LightGBM backend activation requires `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1` gate per `services/research/qlib/worker.py`.
- Data sourced from TWSE/TPEx public feeds must include entitlement and allowed-use evidence in the production dataset proof before `build_production_activation_packet()` is called.

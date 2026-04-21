# Research Backend Maturity Matrix and Production-Path Mapping

**Task**: BG-002  
**Owner**: Codex  
**Reviewer**: Qwen  
**Phase**: Blueprint Gap P1  
**Depends On**: PLAN-002 (done)  
**Last Updated**: 2026-04-17  

_Drafted during Claude helper-claim; finalized by Codex after ownership moved back on 2026-04-13._

---

## Purpose

This document closes Blueprint Gap GAP-02 from `Pantheon_Blueprint_Gap_Review_v1.md`.

GAP-02 identified that the Research Plane has a large number of integrations and contracts, but lacked:

1. An explicit maturity matrix showing where each backend stands
2. A clear production-path vs. deferred-path distinction
3. Evidence that each research problem type has a designated primary backend
4. Cross-backend consistency assessment for the research → artifact → registry → promotion chain

This document provides all four.

---

## Evaluation Framework

Each backend is assessed against the integration status codes established in `OSS_INTEGRATION_CHECKLIST.md`:

| Code | Meaning |
|---|---|
| `not-started` | Named in architecture; no integration work begun |
| `source-selected` | Upstream project confirmed; no pin or adapter yet |
| `version-pinned` | Dependency version locked; no adapter yet |
| `adapter-started` | Local adapter and governance boundary in progress |
| `criteria-defined` | Deferred framework with explicit activation gate documented |
| `smoke-tested` | Dependency pinned + adapter + governed I/O + smoke test passes |
| `governed` | Full integration with governance artifacts complete |

For production-path classification, three tiers apply:

| Tier | Definition |
|---|---|
| **Production Research Path** | Smoke-tested or governed; primary backend for at least one active research problem type |
| **Activation-Ready** | Criteria-defined or version-pinned; entry gates locked; not yet smoke-tested |
| **Not Integrated** | Not in OSS checklist; no version pin, no adapter, no governance artifact |

---

## Research Backend Maturity Matrix

| Framework | Role in Research Plane | Integration Status | Production-Path Tier | Current Owner | Example Strategy Family | Missing Proof to Advance |
|---|---|---|---|---|---|---|
| **MLflow** | Primary experiment registry backend; stores run metadata, artifact references, and model lineage | `governed` | **Production Research Path** | Codex | All research families (cross-cutting) | Follow-on backend-generalization step before enabling W&B parity |
| **DSPy** | Persona policy optimization; prompt/weight optimization for persona decision modules | `governed` | **Production Research Path** | Codex | Persona policy optimization | Keep smoke and deny-regression evidence refreshed when the pin or backend changes |
| **imitation** | Behavior cloning; supervised imitation learning from trader trajectory data | `governed` | **Production Research Path** | Codex | Trader behavior cloning | Keep BC-only scope and smoke evidence refreshed when the pin or backend changes |
| **OpenClaw** | Experiment orchestration / runtime coordination; upstream runtime wrapping local workflows | `adapter-started` | **Activation-Ready** | Codex | All research families (orchestration layer) | Upstream repo dependency path; `openclaw-gateway-adapter` implementation; pinned-image smoke test |
| **Qlib** | Supervised alpha research; cross-sectional feature engineering, LightGBM/LSTM alpha signal discovery | `criteria-defined` | **Activation-Ready** | Qwen | Cross-sectional equity alpha | Version pin; data pipeline adapter; single-model smoke test (see `services/learning/qlib/ACTIVATION_CRITERIA.md`) |
| **TRL** | Preference learning; DPO/RLHF training from governed feedback preference pairs | `criteria-defined` | **Activation-Ready** | Qwen | Persona preference alignment | ≥200 FB-002 events + ≥100 preference pairs; active imitation baseline; version pin; smoke test (see `services/learning/trl/ACTIVATION_CRITERIA.md`) |
| **FinRL** | Simplified single-agent RL portfolio management; pre-configured trading environments | `criteria-defined` | **Activation-Ready** | Copilot | Single-agent RL trading | Current wave explicitly deferred on 2026-04-17; reopen only after Qlib alpha is approved and stable for **3 months**; then prove the first governed single-agent smoke path (see `services/learning/rl/PATH_DEFINITION.md`) |
| **RLlib** | Multi-agent / scalable RL policy training; PPO/SAC via Ray | `criteria-defined` | **Activation-Ready** | Copilot | Multi-agent portfolio optimization | Current wave explicitly deferred on 2026-04-17; stays behind the RL gate and the FinRL first-lane proof; then add governed training/eval loop + smoke test |
| **Ray Tune** | Hyperparameter search over RL/learning experiments; PBT/grid/Bayesian search | `version-pinned` | **Activation-Ready** | Copilot | RL hyperparameter optimization | Current wave explicitly deferred on 2026-04-17; remains coupled to the RLlib follow-on lane after the FinRL first-lane proof; governed search-output adapter still missing |
| **W&B** | Optional alternative experiment registry backend to MLflow; SaaS metrics visualization | `criteria-defined` | **Activation-Ready** | Qwen | All research families (optional) | **OSS-NEXT-004 (2026-04-17): formally deferred**; re-entry gate in `WANDB_ACTIVATION.md §7`; requires MLflow ≥30-day history (earliest 2026-05-15), documented operator preference, adapter generalization, canonical state migration, SDK pin, and infrastructure readiness |
| **vectorbt** | Backtesting and portfolio optimization prototyping; fast vectorized backtest engine | `governed` | **Production Research Path** | Codex2 | Rapid strategy prototyping | Keep stub smoke, worker entrypoint, and governance evidence refreshed when the pin or adapter changes |
| **statsmodels** | Econometrics and regime analysis; cointegration, VAR, ARIMA, regime-switching models | `governed` | **Production Research Path** | Codex2 | Regime inference / macro research | Keep smoke evidence, worker entrypoint, and governance docs refreshed when the pin or adapter changes |
| **QuantLib** | Derivatives pricing and risk; options pricing, fixed income analytics, Greeks | `version-pinned` | **Activation-Ready** | Claude | Derivatives strategy research | Governed input adapter; stub/real backend split; smoke test; evidence pack |

---

## Production-Path Mapping

### Current Production Research Path

The following backends form the active production research path as of this document:

```
Research Intake
  └─ RS-001: Ingestion (GitHub/OpenAlex adapter — smoke-tested)
  └─ RS-002: StrategySpec normalization (smoke-tested)
  └─ RS-003: Replication gate (done)
       │
       ├─ Persona Policy Optimization
       │     └─ DSPy (governed) ──→ MLflow registry
       │
       ├─ Behavior Cloning
       │     └─ imitation (governed) ──→ MLflow registry
       │
       └─ Experiment Registry
             └─ MLflow (governed, primary backend)
```

All three governed backends (DSPy, imitation, MLflow) feed into the canonical artifact/registry path via:

- `artifact_state`: `draft` → `candidate` → `approved`
- `deployment_summary.current_stage`: `none` → `paper` → `canary` → `live`
- Governed by: `REG-001`, `REG-003`, `EX-001`

### Next Activation Order

The ordered activation queue (from `OSS_INTEGRATION_CHECKLIST.md` §Immediate Priorities, updated with OSS-003 criteria):

```
1. OpenClaw    — orchestration semantics affect all paths
2. Qlib        — first learning framework to activate (supervised alpha path)
3. TRL         — preference learning after imitation baseline established
4. FinRL first, then RLlib/Ray Tune — deferred until Qlib plateaus for **3 months** and the RL gate is reopened
5. QuantLib    — derivatives path (adapter and smoke path needed)
6. W&B         — optional after MLflow generalization
```

### Research Problem Type → Primary Backend Mapping

| Research Problem Type | Primary Backend | Fallback / Alternative | Status |
|---|---|---|---|
| Persona policy optimization | DSPy | — | Production path |
| Behavior cloning from trajectories | imitation | — | Production path |
| Experiment lifecycle and registry | MLflow | W&B (future) | Production path |
| Supervised alpha signal discovery | Qlib (LightGBM-first) | vectorbt (prototyping) | Activation-ready |
| Preference learning / RLHF | TRL | — | Activation-ready |
| Sequential RL policy | FinRL (first executable lane after RL re-entry) | RLlib + Ray Tune (follow-on scalable lane) | Activation-ready |
| Rapid strategy backtesting | vectorbt | — | Production research path |
| Econometrics / regime analysis | statsmodels | — | Production research path |
| Derivatives pricing / risk | QuantLib | — | Activation-ready |
| Experiment orchestration | OpenClaw | — | Activation-ready |

---

## Cross-Backend Consistency Assessment

### Research → Artifact → Registry → Promotion Consistency

For production-path backends (DSPy, imitation, MLflow):

| Gate | DSPy | imitation | MLflow |
|---|---|---|---|
| Upstream version pinned | v2.4.5 ✓ | v1.0.1 ✓ | v3.10.1 ✓ |
| Local adapter defined | ✓ | ✓ | ✓ |
| Governed I/O boundaries | ✓ | ✓ | ✓ |
| Smoke test passes | ✓ | ✓ | ✓ |
| `integration.md` artifact | ✓ | ✓ | ✓ |
| `governance.md` artifact | ✓ | ✓ | ✓ |
| `artifact_state` vocabulary | ✓ | ✓ | ✓ |
| `deployment_summary.current_stage` vocabulary | ✓ | ✓ | ✓ |

**Finding**: All three production-path backends are now governed and use canonical registry vocabulary. Their checklist evidence is anchored by `integrations/dspy/`, `integrations/imitation/`, and `integrations/mlflow/`, each with `integration.md`, `governance.md`, and executable smoke-test references.

For activation-ready backends (Qlib, TRL, RL stack):

| Gate | Qlib | TRL | RLlib/FinRL/Tune |
|---|---|---|---|
| Activation criteria documented | ✓ | ✓ | ✓ |
| `artifact_state` vocabulary defined | ✓ | ✓ | ✓ |
| `deployment_summary.current_stage` vocabulary defined | ✓ | ✓ | ✓ |
| Upstream version pinned | Pending | Pending | Ray Tune only |
| Data pipeline adapter | Pending | Pending | Pending |
| Smoke test | Pending | Pending | Pending |

**Finding**: All three deferred learning paths have explicit activation gates with canonical registry vocabulary. The consistency risk is low because the entry criteria were written after the `artifact_state` / `deployment_stage` vocabulary was formalized (OSS-002 / OSS-003 cleanup).

### Inconsistency Risks

1. **QuantLib remains materialized but still not executable**: The upstream source, version pin, and use-case binding exist repo-locally, but no governed adapter, smoke path, or governance evidence has been implemented yet.

2. **OpenClaw adapter is in progress but not smoke-tested end-to-end**: OpenClaw affects orchestration semantics for all backends. Its baseline is pinned, but the real gateway adapter and full workflow execution path still need closure.

3. **RL stack is activation-ready on paper but explicitly deferred in the accepted session**: the 2026-04-17 human gate keeps RL closed for the current wave. FinRL is the chosen first executable lane once re-entry criteria are met, while RLlib/Ray Tune remain a follow-on path. Without recording that decision in canonical summaries, the matrix overstates near-term readiness.

---

## GAP-02 Response (Blueprint Gap Format)

### Current Status

Research Plane has five governed backends on the production path (DSPy, imitation, MLflow, vectorbt, statsmodels), plus multiple activation-ready backends with explicit entry gates or pinned execution baselines (OpenClaw, Qlib, TRL, FinRL/RLlib/Tune, W&B, QuantLib). The remaining research gap is no longer missing task cuts; it is the absence of runnable adapters and smoke evidence for the still-deferred backends.

### Existing Evidence

- `OSS_INTEGRATION_CHECKLIST.md`: Integration status per component
- `OSS_INTEGRATION_AUDIT.md`: Audit correcting conceptual vs. real integration
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib entry gate (OSS-003)
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL entry gate (OSS-003)
- `services/learning/rl/PATH_DEFINITION.md`: RL path definition (LP-005)
- `services/registry/experiments/WANDB_ACTIVATION.md`: W&B activation criteria (OSS-003)
- `services/research/vectorbt/ACTIVATION_CRITERIA.md`: vectorbt governed baseline gate and closeout
- `services/research/vectorbt/requirements.txt`: vectorbt version pin
- `services/research/vectorbt/worker.py`: vectorbt container entrypoint
- `services/research/vectorbt/examples/strategy_dataset_sample.json`: governed sample dataset
- `integrations/openclaw/`: OpenClaw adapter work in progress
- `integrations/statsmodels/`: statsmodels governed evidence pack
- `integrations/vectorbt/integration.md`: vectorbt source selection and governed adapter baseline
- `integrations/vectorbt/governance.md`: vectorbt governance boundary
- `integrations/vectorbt/smoke_test.md`: vectorbt smoke evidence
- `integrations/oss-002/regrade_report.md`: DSPy/imitation/MLflow regrade evidence
- `integrations/dspy/`: DSPy canonical evidence pack
- `integrations/imitation/`: imitation canonical evidence pack
- `integrations/mlflow/`: MLflow canonical evidence pack
- `services/learning/dspy/`: DSPy runnable adapter implementation
- `services/learning/imitation/`: imitation runnable adapter implementation
- `services/registry/experiments/`: MLflow runnable adapter implementation

### Why It Is a Real Gap

The gap is real but scoped: the platform has a valid production research path for behavior cloning, persona optimization, rapid strategy backtesting, and econometrics/regime analysis, but has no production research path for supervised alpha discovery (Qlib), preference learning (TRL), sequential RL, or derivatives pricing/risk (QuantLib). This directly limits the Research Plane's coverage of the complete Blueprint's research problem types.

### Proposed Owner

- Production-path backends: Codex (registry) + Copilot (research ingestion)
- Qlib activation: Qwen
- TRL / RL stack activation: Copilot
- vectorbt: Codex2 owns the governed baseline closeout and smoke path refresh
- statsmodels: Codex2 owns the governed baseline and regression refresh
- QuantLib: Claude owns the materialized baseline (OSS-NEXT-007); follow-on implementation owner still needed

### Source of Truth

- Primary: `OSS_INTEGRATION_CHECKLIST.md` (per-component status)
- Supplementary: `services/learning/*/ACTIVATION_CRITERIA.md`, `services/learning/rl/PATH_DEFINITION.md`

### Planned Closure Work

| Gap Item | Action Required | Target |
|---|---|---|
| OpenClaw gateway adapter + smoke test | Implement and test | Near-term |
| Qlib version pin + data adapter + smoke test | Activate per OSS-003 criteria | Mid-term |
| TRL version pin + pair-construction pipeline + smoke test | Activate after imitation baseline | Mid-term |
| vectorbt governed baseline closeout | Adapter, smoke path, worker entrypoint, and evidence pack committed | Regression refresh when pin or adapter changes |
| statsmodels governed baseline | Worker entrypoint, sample dataset, smoke path, and evidence pack committed | Regression refresh when pin or adapter changes |
| QuantLib task materialization | OSS-NEXT-007 materialized baseline complete | Follow-on implementation wave |
| RL stack approval gate | After Qlib reaches approved status and stays stable for **3 months**; FinRL is the first executable lane when reopened | Long-term |

### Acceptance Evidence

- [x] Maturity matrix with per-backend status and missing proof
- [x] Production-path mapping with explicit tiers
- [x] Research problem type → primary backend mapping
- [x] Cross-backend consistency assessment
- [x] Activation order queue
- [x] GAP-02 response in blueprint format
- [x] `integration.md` + `governance.md` for DSPy, imitation, MLflow
- [ ] Qlib smoke test (activation-gated on OSS-003 entry criteria)
- [x] vectorbt governed smoke path and evidence pack
- [x] statsmodels governed smoke path and evidence pack
- [x] QuantLib task materialization

### Target Wave / Date

This document: Blueprint Gap P1 (current wave).  
Follow-on activation work: P1 continuation and P2 wave.

### Production Sign-off Impact

**Medium.** The production research path for persona optimization, behavior cloning, rapid strategy backtesting, and econometrics/regime analysis is now governed. However, the supervised alpha path (Qlib) and the derivatives path (QuantLib) are still not smoke-tested. This still limits the Research Plane's ability to cover all complete-blueprint research problem types before production sign-off.

---

## References

- `Pantheon_Blueprint_Gap_Review_v1.md`: GAP-02 definition and acceptance criteria
- `OSS_INTEGRATION_CHECKLIST.md`: Per-component integration status
- `OSS_INTEGRATION_AUDIT.md`: Integration audit and correction
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib activation gate
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL activation gate
- `services/learning/rl/PATH_DEFINITION.md`: RL path and FinRL/RLlib/Tune criteria
- `integrations/openclaw/integration.md`: OpenClaw integration evidence
- `integrations/oss-002/regrade_report.md`: DSPy, imitation, MLflow regrade evidence
- `CANONICAL_DOCUMENT_MAP.md`: Canonical document registry

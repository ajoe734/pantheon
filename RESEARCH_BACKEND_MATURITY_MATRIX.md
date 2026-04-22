# Research Backend Maturity Matrix and Production-Path Mapping

**Task**: BG-002  
**Owner**: Codex  
**Reviewer**: Qwen  
**Phase**: Blueprint Gap P1  
**Depends On**: PLAN-002 (done)  
**Last Updated**: 2026-04-22

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
| **Production Research Path** | Governed or smoke-tested and already authorized as the active backend for at least one governed research problem type |
| **Activation-Ready** | Criteria-defined, version-pinned, smoke-tested, or governed baseline exists, but runtime adoption or production activation is still gated |
| **Not Integrated** | Not in OSS checklist; no version pin, no adapter, no governance artifact |

---

## Research Backend Maturity Matrix

| Framework | Role in Research Plane | Integration Status | Production-Path Tier | Current Owner | Example Strategy Family | Missing Proof to Advance |
|---|---|---|---|---|---|---|
| **MLflow** | Primary experiment registry backend; stores run metadata, artifact references, and model lineage | `governed` | **Production Research Path** | Codex | All research families (cross-cutting) | Follow-on backend-generalization step before enabling W&B parity |
| **DSPy** | Persona policy optimization; prompt/weight optimization for persona decision modules | `governed` | **Production Research Path** | Codex | Persona policy optimization | Keep smoke and deny-regression evidence refreshed when the pin or backend changes |
| **imitation** | Behavior cloning; supervised imitation learning from trader trajectory data | `governed` | **Production Research Path** | Codex | Trader behavior cloning | Keep BC-only scope and smoke evidence refreshed when the pin or backend changes |
| **OpenClaw** | Experiment orchestration / runtime coordination; upstream runtime wrapping local workflows | `governed` | **Activation-Ready** | Codex | All research families (orchestration layer) | Adopt the governed gateway path in the repo-authoritative runtime surfaces and keep live smoke evidence refreshed when the pin changes |
| **Qlib** | Supervised alpha research; cross-sectional feature engineering, LightGBM/LSTM alpha signal discovery | `smoke-tested` | **Activation-Ready** | Qwen | Cross-sectional equity alpha | RS-003 candidate + governed dataset gates (>=50 instruments, 2+ years data) must clear before the first promotion-ready LightGBM alpha run |
| **TRL** | Preference learning; DPO/RLHF training from governed feedback preference pairs | `smoke-tested` | **Activation-Ready** | Qwen | Persona preference alignment | >=200 FB-002 events, >=100 valid preference pairs, approved LP-002 artifacts, and one downstream consumer must exist before the first governed production DPO run |
| **FinRL** | Simplified single-agent RL portfolio management; pre-configured trading environments | `criteria-defined` | **Activation-Ready** | Copilot | Single-agent RL trading | Current wave explicitly deferred on 2026-04-17; reopen only after Qlib alpha is approved and stable for **3 months**; then prove the first governed single-agent smoke path (see `services/learning/rl/PATH_DEFINITION.md`) |
| **RLlib** | Multi-agent / scalable RL policy training; PPO/SAC via Ray | `version-pinned` | **Activation-Ready** | Copilot | Multi-agent portfolio optimization | Current wave explicitly deferred on 2026-04-17; stays behind the RL gate and the FinRL first-lane proof; then add governed training/eval loop + smoke test |
| **Ray Tune** | Hyperparameter search over RL/learning experiments; PBT/grid/Bayesian search | `version-pinned` | **Activation-Ready** | Copilot | RL hyperparameter optimization | Current wave explicitly deferred on 2026-04-17; remains coupled to the RLlib follow-on lane after the FinRL first-lane proof; governed search-output adapter still missing |
| **W&B** | Optional alternative experiment registry backend to MLflow; SaaS metrics visualization | `criteria-defined` | **Activation-Ready** | Qwen | All research families (optional) | **OSS-NEXT-004 (2026-04-17): formally deferred**; re-entry gate in `WANDB_ACTIVATION.md §7`; requires MLflow ≥30-day history (earliest 2026-05-15), documented operator preference, adapter generalization, canonical state migration, SDK pin, and infrastructure readiness |
| **vectorbt** | Backtesting and portfolio optimization prototyping; fast vectorized backtest engine | `governed` | **Production Research Path** | Codex2 | Rapid strategy prototyping | Keep stub smoke, worker entrypoint, and governance evidence refreshed when the pin or adapter changes |
| **statsmodels** | Econometrics and regime analysis; cointegration, VAR, ARIMA, regime-switching models | `governed` | **Production Research Path** | Codex2 | Regime inference / macro research | Keep smoke evidence, worker entrypoint, and governance docs refreshed when the pin or adapter changes |
| **QuantLib** | Derivatives pricing and risk; options pricing, fixed income analytics, Greeks | `governed` | **Production Research Path** | Codex | Derivatives strategy research | Keep smoke, worker entrypoint, and evidence pack refreshed when the pin or backend changes |

---

## Production-Path Mapping

### Current Active Production Research Path

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

The always-on governed backbone is anchored by DSPy, imitation, and MLflow, which feed into the canonical artifact/registry path via:

- `artifact_state`: `draft` → `candidate` → `approved`
- `deployment_summary.current_stage`: `none` → `paper` → `canary` → `live`
- Governed by: `REG-001`, `REG-003`, `EX-001`

Additional governed production-path backends already available today:

- `vectorbt` for rapid strategy backtesting
- `statsmodels` for econometrics and regime analysis
- `QuantLib` for derivatives pricing and risk

Runnable baselines that are **not yet** active production lanes:

- `OpenClaw`: `governed` baseline and live gateway smoke are landed, but repo-authoritative runtime adoption is tracked separately in `PER-001-RUNTIME-INTEGRATION-001`
- `Qlib`: `smoke-tested` LightGBM baseline is landed, but supervised-alpha activation still depends on RS-003 candidate readiness and governed market-data gates
- `TRL`: `smoke-tested` DPO baseline is landed, but production use still depends on runtime feedback volume, approved imitation artifacts, and one downstream consumer

### Next Activation Order

The ordered activation queue (from `OSS_INTEGRATION_CHECKLIST.md` §Immediate Priorities, updated with OSS-003 criteria):

```
1. OpenClaw    — governed gateway baseline is ready; adopt it in the repo-authoritative runtime surfaces
2. Qlib        — smoke-tested baseline is ready; clear data gates and run the first governed alpha activation
3. TRL         — smoke-tested baseline is ready; clear runtime-data gates and run the first governed production DPO activation
4. FinRL first, then RLlib/Ray Tune — deferred until Qlib plateaus for **3 months** and the RL gate is reopened
5. W&B         — optional after MLflow generalization
```

### Research Problem Type → Primary Backend Mapping

| Research Problem Type | Primary Backend | Fallback / Alternative | Status |
|---|---|---|---|
| Persona policy optimization | DSPy | — | Production path |
| Behavior cloning from trajectories | imitation | — | Production path |
| Experiment lifecycle and registry | MLflow | W&B (future) | Production path |
| Supervised alpha signal discovery | Qlib (LightGBM-first) | vectorbt (prototyping) | Smoke-tested baseline; activation gated |
| Preference learning / RLHF | TRL | — | Smoke-tested baseline; runtime-data gated |
| Sequential RL policy | FinRL (first executable lane after RL re-entry) | RLlib + Ray Tune (follow-on scalable lane) | Activation-ready |
| Rapid strategy backtesting | vectorbt | — | Production research path |
| Econometrics / regime analysis | statsmodels | — | Production research path |
| Derivatives pricing / risk | QuantLib | — | Production research path |
| Experiment orchestration | OpenClaw | — | Governed baseline; runtime adoption gated |

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

For activation-gated baselines (OpenClaw, Qlib, TRL, RL stack):

| Gate | OpenClaw | Qlib | TRL | RLlib/FinRL/Tune |
|---|---|---|---|---|
| Activation criteria / contract documented | `OPENCLAW_RUNTIME_CONTRACT.md` + governed evidence pack ✓ | ✓ | ✓ | ✓ |
| Upstream version/runtime pinned | `v2026.4.7` + `ghcr.io/openclaw/openclaw:2026.4.7` ✓ | `pyqlib==0.9.6` ✓ | `trl>=0.8.0,<0.10.0` ✓ | FinRL / RLlib / Ray Tune pins landed ✓ |
| Local adapter defined | ✓ | ✓ | ✓ | Pending |
| Governed I/O boundaries defined | ✓ | ✓ | ✓ | Criteria/contract only |
| Smoke test passes | Live gateway smoke ✓ | LightGBM smoke ✓ | DPO smoke ✓ | Pending |
| Remaining gate | Repo-authoritative runtime adoption | RS-003 candidate + governed dataset gates | Feedback-volume + imitation + downstream-consumer gates | RL approval gate remains closed |

**Finding**: OpenClaw, Qlib, and TRL no longer lack pins, adapters, or smoke evidence. Their remaining gaps are runtime-adoption or production-activation gates. The only backends in this activation-gated group that still lack runnable baselines are the RL stack rows.

### Inconsistency Risks

1. **Baseline maturity and production activation are easy to conflate**: OpenClaw is `governed`, while Qlib and TRL are `smoke-tested`; none of those facts mean the lane is already the default active production path. Summary docs must preserve that distinction.

2. **RL stack is activation-ready on paper but explicitly deferred in the accepted session**: the 2026-04-17 human gate keeps RL closed for the current wave. FinRL is the chosen first executable lane once re-entry criteria are met, while RLlib/Ray Tune remain a follow-on path. Without recording that decision in canonical summaries, the matrix overstates near-term readiness.

---

## GAP-02 Response (Blueprint Gap Format)

### Current Status

Research Plane has six governed backends on the production path (DSPy, imitation, MLflow, vectorbt, statsmodels, QuantLib), plus one governed activation-gated runtime substrate (OpenClaw) and two smoke-tested but activation-gated learning baselines (Qlib, TRL). The remaining research gap is no longer missing runnable baselines for those three rows; it is the lack of production activation for Qlib/TRL, the still-separate runtime-adoption closeout for OpenClaw, and the broader deferred RL/W&B paths.

### Existing Evidence

- `OSS_INTEGRATION_CHECKLIST.md`: Integration status per component
- `OSS_INTEGRATION_AUDIT.md`: Audit correcting conceptual vs. real integration
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib entry gate (OSS-003)
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL entry gate (OSS-003)
- `services/learning/rl/PATH_DEFINITION.md`: RL path definition (LP-005)
- `services/registry/experiments/WANDB_ACTIVATION.md`: W&B activation criteria (OSS-003)
- `integrations/openclaw/{integration,governance,smoke_test}.md`: OpenClaw governed runtime baseline and live gateway smoke evidence
- `integrations/qlib/{integration,governance,smoke_test}.md`: Qlib smoke-tested LightGBM adapter baseline
- `integrations/trl/{integration,governance,smoke_test}.md`: TRL smoke-tested DPO adapter baseline
- `services/research/vectorbt/ACTIVATION_CRITERIA.md`: vectorbt governed baseline gate and closeout
- `services/research/vectorbt/requirements.txt`: vectorbt version pin
- `services/research/vectorbt/worker.py`: vectorbt container entrypoint
- `services/research/vectorbt/examples/strategy_dataset_sample.json`: governed sample dataset
- `services/research/quantlib/ACTIVATION_CRITERIA.md`: QuantLib governed baseline gate and closeout
- `services/research/quantlib/requirements.txt`: QuantLib version pin
- `services/research/quantlib/worker.py`: QuantLib container entrypoint
- `services/research/quantlib/examples/pricing_dataset_sample.json`: governed sample dataset
- `integrations/statsmodels/`: statsmodels governed evidence pack
- `integrations/quantlib/`: QuantLib governed evidence pack
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

The gap is real but scoped: the platform has a valid production research path for behavior cloning, persona optimization, rapid strategy backtesting, econometrics/regime analysis, and derivatives pricing/risk, and it now has runnable governed/smoke-tested baselines for OpenClaw, Qlib, and TRL. However, it still has no production-activated path for supervised alpha discovery (Qlib), preference learning (TRL), or sequential RL. This directly limits the Research Plane's coverage of the complete Blueprint's research problem types.

### Proposed Owner

- Production-path backends: Codex (registry) + Copilot (research ingestion)
- Qlib activation: Qwen
- TRL activation: Qwen
- RL stack activation: Copilot
- vectorbt: Codex2 owns the governed baseline closeout and smoke path refresh
- statsmodels: Codex2 owns the governed baseline and regression refresh
- QuantLib: Codex owns the governed baseline closeout and regression refresh

### Source of Truth

- Primary: `OSS_INTEGRATION_CHECKLIST.md` (per-component status)
- Supplementary: `services/learning/*/ACTIVATION_CRITERIA.md`, `services/learning/rl/PATH_DEFINITION.md`

### Planned Closure Work

| Gap Item | Action Required | Target |
|---|---|---|
| OpenClaw runtime adoption | Switch the repo-authoritative persona/router/web runtime surfaces onto the governed gateway path and keep live smoke refreshed | Near-term |
| Qlib first production activation | Clear RS-003 + governed market-data gates and run the first governed LightGBM alpha activation packet | Mid-term |
| TRL first production activation | Clear feedback-volume / imitation / downstream-consumer gates and run the first governed production DPO packet | Mid-term |
| vectorbt governed baseline closeout | Adapter, smoke path, worker entrypoint, and evidence pack committed | Regression refresh when pin or adapter changes |
| statsmodels governed baseline | Worker entrypoint, sample dataset, smoke path, and evidence pack committed | Regression refresh when pin or adapter changes |
| QuantLib governed baseline | Worker entrypoint, sample dataset, smoke path, and evidence pack committed | Regression refresh when pin or adapter changes |
| RL stack approval gate | After Qlib reaches approved status and stays stable for **3 months**; FinRL is the first executable lane when reopened | Long-term |

### Acceptance Evidence

- [x] Maturity matrix with per-backend status and missing proof
- [x] Production-path mapping with explicit tiers
- [x] Research problem type → primary backend mapping
- [x] Cross-backend consistency assessment
- [x] Activation order queue
- [x] GAP-02 response in blueprint format
- [x] `integration.md` + `governance.md` for DSPy, imitation, MLflow
- [x] OpenClaw governed gateway adapter + live smoke evidence pack
- [x] Qlib smoke-tested baseline and evidence pack
- [x] TRL smoke-tested baseline and evidence pack
- [x] vectorbt governed smoke path and evidence pack
- [x] statsmodels governed smoke path and evidence pack
- [x] QuantLib governed smoke path and evidence pack

### Target Wave / Date

This document: Blueprint Gap P1 (current wave).  
Follow-on activation work: P1 continuation and P2 wave.

### Production Sign-off Impact

**Medium.** The production research path for persona optimization, behavior cloning, rapid strategy backtesting, econometrics/regime analysis, and derivatives pricing/risk is now governed. OpenClaw now has a governed runtime baseline, and Qlib/TRL now have smoke-tested baselines, but the supervised alpha path and the preference / RL paths are still not production-activated. This still limits the Research Plane's ability to cover all complete-blueprint research problem types before production sign-off.

---

## References

- `Pantheon_Blueprint_Gap_Review_v1.md`: GAP-02 definition and acceptance criteria
- `OSS_INTEGRATION_CHECKLIST.md`: Per-component integration status
- `OSS_INTEGRATION_AUDIT.md`: Integration audit and correction
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib activation gate
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL activation gate
- `services/learning/rl/PATH_DEFINITION.md`: RL path and FinRL/RLlib/Tune criteria
- `integrations/openclaw/integration.md`: OpenClaw integration evidence
- `integrations/qlib/integration.md`: Qlib smoke-tested integration evidence
- `integrations/trl/integration.md`: TRL smoke-tested integration evidence
- `integrations/oss-002/regrade_report.md`: DSPy, imitation, MLflow regrade evidence
- `CANONICAL_DOCUMENT_MAP.md`: Canonical document registry

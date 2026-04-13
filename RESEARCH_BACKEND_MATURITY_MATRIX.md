# Research Backend Maturity Matrix and Production-Path Mapping

**Task**: BG-002  
**Owner**: Codex  
**Reviewer**: Qwen  
**Phase**: Blueprint Gap P1  
**Depends On**: PLAN-002 (done)  
**Last Updated**: 2026-04-13  

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
| **MLflow** | Primary experiment registry backend; stores run metadata, artifact references, and model lineage | `smoke-tested` | **Production Research Path** | Codex | All research families (cross-cutting) | `integration.md` and `governance.md` per canonical checklist format; follow-on backend-generalization step |
| **DSPy** | Persona policy optimization; prompt/weight optimization for persona decision modules | `smoke-tested` | **Production Research Path** | Copilot | Persona policy optimization | `integration.md` and `governance.md` per canonical checklist format (see `integrations/oss-002/regrade_report.md`) |
| **imitation** | Behavior cloning; supervised imitation learning from trader trajectory data | `smoke-tested` | **Production Research Path** | Copilot | Trader behavior cloning | `integration.md` and `governance.md` per canonical checklist format (see `integrations/oss-002/regrade_report.md`) |
| **OpenClaw** | Experiment orchestration / runtime coordination; upstream runtime wrapping local workflows | `adapter-started` | **Activation-Ready** | Codex | All research families (orchestration layer) | Upstream repo dependency path; `openclaw-gateway-adapter` implementation; pinned-image smoke test |
| **Qlib** | Supervised alpha research; cross-sectional feature engineering, LightGBM/LSTM alpha signal discovery | `criteria-defined` | **Activation-Ready** | Qwen | Cross-sectional equity alpha | Version pin; data pipeline adapter; single-model smoke test (see `services/learning/qlib/ACTIVATION_CRITERIA.md`) |
| **TRL** | Preference learning; DPO/RLHF training from governed feedback preference pairs | `criteria-defined` | **Activation-Ready** | Qwen | Persona preference alignment | ≥200 FB-002 events + ≥100 preference pairs; active imitation baseline; version pin; smoke test (see `services/learning/trl/ACTIVATION_CRITERIA.md`) |
| **FinRL** | Simplified single-agent RL portfolio management; pre-configured trading environments | `criteria-defined` | **Activation-Ready** | Copilot | Single-agent RL trading | Qlib alpha exhausted; sequential decision dependency proven; version pin; smoke test (see `services/learning/rl/PATH_DEFINITION.md`) |
| **RLlib** | Multi-agent / scalable RL policy training; PPO/SAC via Ray | `criteria-defined` | **Activation-Ready** | Copilot | Multi-agent portfolio optimization | RL path approved; version pin; governed training/eval loop; smoke test |
| **Ray Tune** | Hyperparameter search over RL/learning experiments; PBT/grid/Bayesian search | `version-pinned` | **Activation-Ready** | Copilot | RL hyperparameter optimization | Governed search output adapter; smoke test with selected learning path; `DockerfileLeanFoundationARM` already pins `ray[tune]` |
| **W&B** | Optional alternative experiment registry backend to MLflow; SaaS metrics visualization | `criteria-defined` | **Activation-Ready** | Codex | All research families (optional) | Stable MLflow integration first; adapter generalization; explicit operator need; version pin (see `services/registry/experiments/WANDB_ACTIVATION.md`) |
| **vectorbt** | Backtesting and portfolio optimization prototyping; fast vectorized backtest engine | `not-started` | **Not Integrated** | Unassigned | Rapid strategy prototyping | Source selection; version pin; adapter; governed I/O; smoke test |
| **statsmodels** | Econometrics and regime analysis; cointegration, VAR, ARIMA, regime-switching models | `not-started` | **Not Integrated** | Unassigned | Regime inference / macro research | Source selection; version pin; adapter; governed I/O; smoke test |
| **QuantLib** | Derivatives pricing and risk; options pricing, fixed income analytics, Greeks | `not-started` | **Not Integrated** | Unassigned | Derivatives strategy research | Source selection; version pin; adapter; governed I/O; smoke test |

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
       │     └─ DSPy (smoke-tested) ──→ MLflow registry
       │
       ├─ Behavior Cloning
       │     └─ imitation (smoke-tested) ──→ MLflow registry
       │
       └─ Experiment Registry
             └─ MLflow (smoke-tested, primary backend)
```

All three smoke-tested backends (DSPy, imitation, MLflow) feed into the canonical artifact/registry path via:

- `artifact_state`: `draft` → `candidate` → `approved`
- `deployment_summary.current_stage`: `none` → `paper` → `canary` → `live`
- Governed by: `REG-001`, `REG-003`, `EX-001`

### Next Activation Order

The ordered activation queue (from `OSS_INTEGRATION_CHECKLIST.md` §Immediate Priorities, updated with OSS-003 criteria):

```
1. OpenClaw    — orchestration semantics affect all paths
2. Qlib        — first learning framework to activate (supervised alpha path)
3. TRL         — preference learning after imitation baseline established
4. FinRL/RLlib/Ray Tune — deferred until Qlib plateaus and RL criteria met
5. vectorbt    — rapid prototyping path (task materialization needed)
6. statsmodels — regime research path (task materialization needed)
7. QuantLib    — derivatives path (task materialization needed)
8. W&B         — optional after MLflow generalization
```

### Research Problem Type → Primary Backend Mapping

| Research Problem Type | Primary Backend | Fallback / Alternative | Status |
|---|---|---|---|
| Persona policy optimization | DSPy | — | Production path |
| Behavior cloning from trajectories | imitation | — | Production path |
| Experiment lifecycle and registry | MLflow | W&B (future) | Production path |
| Supervised alpha signal discovery | Qlib (LightGBM-first) | vectorbt (prototyping) | Activation-ready |
| Preference learning / RLHF | TRL | — | Activation-ready |
| Sequential RL policy | RLlib + Ray Tune | FinRL (single-agent) | Activation-ready |
| Rapid strategy backtesting | vectorbt | — | Not integrated |
| Econometrics / regime analysis | statsmodels | — | Not integrated |
| Derivatives pricing / risk | QuantLib | — | Not integrated |
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
| `integration.md` artifact | Pending | Pending | Pending |
| `governance.md` artifact | Pending | Pending | Pending |
| `artifact_state` vocabulary | ✓ | ✓ | ✓ |
| `deployment_summary.current_stage` vocabulary | ✓ | ✓ | ✓ |

**Finding**: All three production-path backends are smoke-tested and use canonical registry vocabulary. The outstanding gap is the formal `integration.md` and `governance.md` checklist artifacts, which are required by `OSS_INTEGRATION_CHECKLIST.md` but not yet written.

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

1. **Missing checklist artifacts for smoke-tested backends**: DSPy, imitation, and MLflow lack `integration.md` and `governance.md`. Until these are written, the integration evidence is incomplete per `OSS_INTEGRATION_CHECKLIST.md` §Required Evidence Per Component.

2. **vectorbt / statsmodels / QuantLib have no tasks**: These backends are named in the complete Blueprint but have no tasks materialized. They represent a planning gap, not a delivery gap — they were not in scope for the current sprint but need task materialization before activation-ready status can be claimed.

3. **OpenClaw adapter is in progress but not smoke-tested**: OpenClaw affects orchestration semantics for all backends. Its `adapter-started` status means experiment orchestration across backends is not yet provably consistent.

4. **Ray Tune version pinned but no governed adapter**: `DockerfileLeanFoundationARM` already pins `ray[tune]`, but no adapter exists. This creates a false sense of integration readiness.

---

## GAP-02 Response (Blueprint Gap Format)

### Current Status

Research Plane has three smoke-tested backends on the production path (DSPy, imitation, MLflow), plus five activation-ready backends with explicit entry gates (OpenClaw, Qlib, TRL, FinRL/RLlib/Tune, W&B). Three blueprint-required backends (vectorbt, statsmodels, QuantLib) have no integration work started.

### Existing Evidence

- `OSS_INTEGRATION_CHECKLIST.md`: Integration status per component
- `OSS_INTEGRATION_AUDIT.md`: Audit correcting conceptual vs. real integration
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib entry gate (OSS-003)
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL entry gate (OSS-003)
- `services/learning/rl/PATH_DEFINITION.md`: RL path definition (LP-005)
- `services/registry/experiments/WANDB_ACTIVATION.md`: W&B activation criteria (OSS-003)
- `integrations/openclaw/`: OpenClaw adapter work in progress
- `integrations/oss-002/regrade_report.md`: DSPy/imitation/MLflow regrade evidence
- `services/research/dspy/`: DSPy research integration
- `services/research/imitation/`: imitation research integration
- `services/research/mlflow/`: MLflow research integration
- `services/research/qlib/`: Qlib research integration scaffold

### Why It Is a Real Gap

The gap is real but scoped: the platform has a valid production research path for behavior cloning and persona optimization, but has no production research path for supervised alpha discovery (Qlib), preference learning (TRL), sequential RL, or quantitative research tools (vectorbt, statsmodels, QuantLib). This directly limits the Research Plane's coverage of the complete Blueprint's research problem types.

### Proposed Owner

- Production-path backends: Codex (registry) + Copilot (research ingestion)
- Qlib activation: Qwen
- TRL / RL stack activation: Copilot
- vectorbt / statsmodels / QuantLib: Task materialization needed; owner to be assigned

### Source of Truth

- Primary: `OSS_INTEGRATION_CHECKLIST.md` (per-component status)
- Supplementary: `services/learning/*/ACTIVATION_CRITERIA.md`, `services/learning/rl/PATH_DEFINITION.md`

### Planned Closure Work

| Gap Item | Action Required | Target |
|---|---|---|
| Missing `integration.md` / `governance.md` for DSPy, imitation, MLflow | Write per-component docs | Near-term |
| OpenClaw gateway adapter + smoke test | Implement and test | Near-term |
| Qlib version pin + data adapter + smoke test | Activate per OSS-003 criteria | Mid-term |
| TRL version pin + pair-construction pipeline + smoke test | Activate after imitation baseline | Mid-term |
| vectorbt task materialization | New BG task required | Next planning wave |
| statsmodels task materialization | New BG task required | Next planning wave |
| QuantLib task materialization | New BG task required | Next planning wave |
| RL stack approval gate | After Qlib plateaus | Long-term |

### Acceptance Evidence

- [x] Maturity matrix with per-backend status and missing proof
- [x] Production-path mapping with explicit tiers
- [x] Research problem type → primary backend mapping
- [x] Cross-backend consistency assessment
- [x] Activation order queue
- [x] GAP-02 response in blueprint format
- [ ] `integration.md` + `governance.md` for DSPy, imitation, MLflow (follow-on work)
- [ ] Qlib smoke test (activation-gated on OSS-003 entry criteria)
- [ ] vectorbt / statsmodels / QuantLib task materialization

### Target Wave / Date

This document: Blueprint Gap P1 (current wave).  
Follow-on activation work: P1 continuation and P2 wave.

### Production Sign-off Impact

**Medium-high.** The production research path for persona optimization and behavior cloning is smoke-tested. However, the supervised alpha path (Qlib) and the quantitative research tool set (vectorbt, statsmodels, QuantLib) are not yet available, which limits the Research Plane's ability to cover all complete-blueprint research problem types before production sign-off.

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

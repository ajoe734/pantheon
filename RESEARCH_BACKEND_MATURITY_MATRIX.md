# Research Backend Maturity Matrix and Production-Path Mapping

**Task**: BG-002
**Owner**: Codex
**Reviewer**: Qwen
**Phase**: Blueprint Gap P1
**Depends On**: PLAN-002 (done)
**Last Updated**: 2026-06-10

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
| `version-pinned` | Dependency version locked; production adapter/smoke evidence not yet counted, even if a fail-closed dormant scaffold exists |
| `adapter-started` | Local adapter and governance boundary in progress |
| `criteria-defined` | Deferred framework with explicit activation gate documented |
| `smoke-tested` | Dependency pinned + adapter + governed I/O + smoke test passes |
| `activation-gated` | Adapter/backend path exists and is tested in bounded form, but explicit runtime, network, credential, or operator gate remains closed |
| `governed` | Full integration with governance artifacts complete |

For production-path classification, three tiers apply:

| Tier | Definition |
|---|---|
| **Production Research Path** | Governed or smoke-tested and already authorized as the active backend for at least one governed research problem type |
| **Activation-Ready** | Criteria-defined, version-pinned, smoke-tested, governed baseline, or dormant implementation exists, but runtime adoption or production activation is still gated |
| **Not Integrated** | Not in OSS checklist; no version pin, no adapter, no governance artifact |

Activation-ready does **not** mean "do not develop." It means repo-local dormant implementation
may be built and verified behind fail-closed flags, while production activation, paper/canary/live
runtime paths, canonical writes, and networked backend operation remain gated.

---

## Research Backend Maturity Matrix

| Framework | Role in Research Plane | Integration Status | Production-Path Tier | Current Owner | Example Strategy Family | Missing Proof to Advance |
|---|---|---|---|---|---|---|
| **MLflow** | Primary experiment registry backend; stores run metadata, artifact references, and model lineage | `governed` | **Production Research Path** | Codex | All research families (cross-cutting) | Keep canonical `artifact_state` / `deployment_stage` bridge behavior refreshed while W&B stays deferred |
| **DSPy** | Persona policy optimization; prompt/weight optimization for persona decision modules | `governed` | **Production Research Path** | Codex | Persona policy optimization | Keep smoke and deny-regression evidence refreshed when the pin or backend changes |
| **imitation** | Behavior cloning; supervised imitation learning from trader trajectory data | `governed` | **Production Research Path** | Codex | Trader behavior cloning | Keep BC-only scope and smoke evidence refreshed when the pin or backend changes |
| **OpenClaw** | Experiment orchestration / runtime coordination; upstream runtime wrapping local workflows | `governed` | **Activation-Ready** | Codex | All research families (orchestration layer) | Fail-closed repo-authoritative runtime-adoption scaffold is landed; broker sessions, paper/canary/live routes, and capital binding remain closed while live smoke evidence stays refreshed when the pin changes |
| **Qlib** | Supervised alpha research; cross-sectional feature engineering, LightGBM/LSTM alpha signal discovery | `smoke-tested` | **Activation-Ready** | Qwen | Cross-sectional equity alpha | Activation packet is prepared; RS-003 candidate + governed dataset gates (>=50 instruments, 2+ years data) + target StrategySpec binding must clear before the first promotion-ready LightGBM alpha run |
| **TRL** | Preference learning; DPO/RLHF training from governed feedback preference pairs | `smoke-tested` | **Activation-Ready** | Qwen | Persona preference alignment | >=200 FB-002 events, >=100 valid preference pairs, approved LP-002 artifacts, and one downstream consumer must exist before the first governed production DPO run |
| **FinRL** | Simplified single-agent RL portfolio management; pre-configured trading environments | `smoke-tested` | **Activation-Ready** | Copilot | Single-agent RL trading | Bounded activation-ready smoke evidence exists under `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`; production RL remains closed until the RL approval gate reopens and FinRL proves the first governed single-agent production path |
| **RLlib** | Multi-agent / scalable RL policy training; PPO/SAC via Ray | `smoke-tested` | **Activation-Ready** | Copilot | Multi-agent portfolio optimization | Bounded activation-ready smoke evidence exists under `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`; governed train/eval activation stays behind the RL gate and the FinRL first-lane proof |
| **Ray Tune** | Hyperparameter search over RL/learning experiments; PBT/grid/Bayesian search | `smoke-tested` | **Activation-Ready** | Copilot | RL hyperparameter optimization | Bounded activation-ready smoke evidence exists under `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`; governed activation remains coupled to the RLlib follow-on lane after the FinRL first-lane proof |
| **W&B** | Optional alternative experiment registry backend to MLflow; SaaS metrics visualization | `activation-gated` | **Activation-Ready** | Qwen | All research families (optional) | Offline local-store and SDK-backed online-sync paths are fail-closed; credentialed W&B project/API-key rerun remains required before networked activation |
| **vectorbt** | Backtesting and portfolio optimization prototyping; fast vectorized backtest engine | `governed` | **Production Research Path** | Codex2 | Rapid strategy prototyping | Keep stub smoke, worker entrypoint, and governance evidence refreshed when the pin or adapter changes |
| **statsmodels** | Econometrics and regime analysis; cointegration, VAR, ARIMA, regime-switching models | `governed` | **Production Research Path** | Codex2 | Regime inference / macro research | Keep smoke evidence, worker entrypoint, and governance docs refreshed when the pin or adapter changes |
| **QuantLib** | Derivatives pricing and risk; options pricing, fixed income analytics, Greeks | `governed` | **Production Research Path** | Codex | Derivatives strategy research | Keep smoke, worker entrypoint, and evidence pack refreshed when the pin or backend changes |

---

## MPOS Observe Backend Matrix (G6)

This MPOS-specific read narrows the general Research Plane matrix to the
Observe phase used before persona proposal synthesis. The dispatcher contract is
`services/research/experiment_orchestrator/parallel_dispatch.py`: its default
backend registry includes `vectorbt`, `qlib`, and `statsmodels` backend ids.
`QuantLib` is intentionally kept outside that default fanout because derivatives
pricing and risk analytics need a separate governed request path.

| Backend | MPOS Observe role | Default dispatcher posture | Maturity posture | No-order-route guarantee | Proof tests and evidence |
|---|---|---|---|---|---|
| `vectorbt` | Rapid backtesting and strategy-prototype scoring for StrategySpec evidence | Default dispatcher ids: `vectorbt`, `vectorbt_portfolio` | `governed`; Production Research Path for rapid strategy backtesting | Emits `artifact_state=draft`, `deployment_summary.current_stage=none`, `governance.direct_live_influence=false`, and `lean_consumption=scoring_only_not_direct_action`; may not write SignalStore, LEAN, broker, or live execution state | `python3 services/research/vectorbt/smoke_test.py`; `python3 -m pytest services/research/vectorbt/test_adapter.py -q`; `integrations/vectorbt/{integration,governance,smoke_test}.md` |
| `Qlib` | Supervised alpha / rolling OOS evidence for cross-sectional signal discovery | Dispatcher ids exist: `qlib`, `qlib_rolling_oos`; use only for activation-ready research evidence with explicit inputs and gates | `smoke-tested`; Activation-Ready, not production-active | Keeps output at `artifact_state=draft` and `deployment_stage=none`; production packet requests review-only `draft_to_candidate`; requires `no_order_route=true`, no broker session, no capital binding, and `order_route=none` | `python3 services/research/qlib/smoke_test.py`; `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`; `python3 -m pytest tests/governance/test_qlib_proof_artifacts.py -q`; `integrations/qlib/{governance,smoke_test,activation_packet,rolling_oos_admission_packet}.md` |
| `statsmodels` | Econometrics, cointegration, factor diagnostics, and regime evidence for Observe/Orient | Default dispatcher id: `statsmodels` | `governed`; Production Research Path for regime and econometrics research | Emits non-executable research artifacts at `artifact_state=draft`, `deployment_summary.current_stage=none`, `governance.direct_live_influence=false`, and `lean_consumption=research_only_not_direct_action`; admission packets keep `order_route=none` | `python3 services/research/statsmodels/smoke_test.py`; `python3 -m pytest services/research/statsmodels/test_adapter.py -q`; `python3 -m pytest tests/governance/test_statsmodels_proof_artifacts.py -q`; `integrations/statsmodels/{governance,smoke_test,admission_proof,cointegration_production_evidence}.md` |
| `QuantLib` | Derivatives pricing, Greeks, option-chain snapshots, and fixed-income risk evidence | Separate governed research path, not default-dispatch, not deferred | `governed`; Production Research Path for derivatives pricing and risk research | Emits non-executable `pricing_report` / `pricing_snapshot` evidence at `artifact_state=draft`, `deployment_summary.current_stage=none`, `governance.direct_live_influence=false`, and `lean_consumption=research_only_not_direct_action`; admission packets keep `registry_write_performed=false`, `order_route=none`, and `no_order_route=true` | `python3 services/research/quantlib/smoke_test.py`; `python3 -m pytest services/research/quantlib/test_adapter.py -q`; `python3 -m pytest tests/governance/test_quantlib_proof_artifacts.py -q`; `integrations/quantlib/{governance,smoke_test,admission_proof,pricing_evidence_retention}.md` |

MPOS interpretation:

- `Qlib` remains Activation-Ready, not production-active. It can contribute
  review/OOS evidence only through explicit activation-ready inputs, production
  data proof, and review-only candidate packets; it does not open a production
  LightGBM lane or any order-capable route.
- `QuantLib` is not deferred. It is governed and production-path for derivatives
  pricing/risk research, but it is not part of the generic default MPOS Observe
  dispatcher fanout. Use it when the task explicitly needs derivative pricing,
  Greeks, option-chain, or fixed-income evidence.
- For all four backends, Observe outputs are research evidence only. Any later
  movement toward candidate, approval, deployment, RuntimeBinding, LEAN, paper,
  canary, live, broker, or capital authority must pass the downstream registry
  and governance gates; backend smoke or admission evidence alone is not
  execution authority.

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
- `deployment_summary.current_stage`: `none` → `paper` → `canary` → `frozen` → `live`
- Governed by: `REG-001`, `REG-003`, `EX-001`

Additional governed production-path backends already available today:

- `vectorbt` for rapid strategy backtesting
- `statsmodels` for econometrics and regime analysis
- `QuantLib` for derivatives pricing and risk

Runnable baselines and scaffolds that are **not yet** active production lanes:

- `OpenClaw`: `governed` baseline, live gateway smoke, and fail-closed repo-authoritative runtime-adoption scaffold are landed, but broker sessions, paper/canary/live routes, and capital binding remain denied
- `Qlib`: `smoke-tested` LightGBM baseline and activation packet are landed, but supervised-alpha activation still depends on RS-003 candidate readiness, governed market-data proof, and target StrategySpec binding
- `TRL`: `smoke-tested` DPO baseline is landed, but production use still depends on runtime feedback volume, approved imitation artifacts, and one downstream consumer
- `FinRL`, `RLlib`, and `Ray Tune`: `smoke-tested` activation-ready RL evidence is landed, but the RL production gate remains closed and all outputs stay research-only / draft / non-ordering
- `W&B`: `activation-gated` experiment backend path is landed, but online sync requires explicit credentialed activation and remains non-ordering

### Next Activation Order

The ordered activation queue (from `OSS_INTEGRATION_CHECKLIST.md` §Immediate Priorities, updated to the 2026-05-01 remaining-activation truth):

```
1. OpenClaw    — governed gateway baseline and fail-closed runtime-adoption scaffold are ready; keep production/broker/live/capital activation closed until a future explicit activation gate opens it
2. Qlib        — smoke-tested baseline is ready; clear data gates and run the first governed alpha activation
3. TRL         — smoke-tested baseline is ready; clear runtime-data gates and run the first governed production DPO activation
4. FinRL, RLlib, Ray Tune — all three are smoke-tested but still RL-gated; FinRL is the first future production RL lane after explicit re-entry approval
5. W&B         — activation-gated optional backend path; credentialed online sync still requires explicit test project/API-key evidence
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
| Upstream version/runtime pinned | `v2026.6.8` + `ghcr.io/openclaw/openclaw:2026.6.8` ✓ | `pyqlib==0.9.6` ✓ | `trl>=0.8.0,<0.10.0` ✓ | FinRL / RLlib / Ray Tune pins landed ✓ |
| Local adapter defined | ✓ | ✓ | ✓ | FinRL/RLlib/Ray Tune prep-only scaffolds present |
| Governed I/O boundaries defined | ✓ | ✓ | ✓ | FinRL/RLlib/Ray Tune draft/none prep boundaries present |
| Smoke test passes | Live gateway smoke ✓ | LightGBM smoke ✓ | DPO smoke ✓ | FinRL/RLlib/Ray Tune prep smoke paths pass behind explicit CLI gate |
| Remaining gate | Production/broker/live/capital activation remains closed by contract | RS-003 candidate + governed dataset gates | Feedback-volume + imitation + downstream-consumer gates | RL approval gate remains closed |

**Finding**: OpenClaw, Qlib, and TRL no longer lack pins, adapters, or smoke evidence. FinRL,
RLlib, and Ray Tune now have runnable dormant baselines as well, but they are explicit-gate,
offline-only, non-writing, draft/none, and not production activation evidence.

### Inconsistency Risks

1. **Baseline maturity and production activation are easy to conflate**: OpenClaw is `governed`, while Qlib and TRL are `smoke-tested`; none of those facts mean the lane is already the default active production path. Summary docs must preserve that distinction.

2. **RL stack is activation-ready on paper but explicitly deferred in the accepted session**: the 2026-04-17 human gate keeps RL activation closed for the current wave. FinRL is the chosen first active lane once re-entry criteria are met, while RLlib/Ray Tune remain a follow-on path. The dormant RLlib/Ray Tune scaffold must not be read as permission for production train/eval, registry writes, or paper/live execution.

---

## GAP-02 Response (Blueprint Gap Format)

### Current Status

Research Plane has six governed backends on the production path (DSPy, imitation, MLflow, vectorbt, statsmodels, QuantLib), plus one governed activation-gated runtime substrate (OpenClaw), two smoke-tested but activation-gated learning baselines (Qlib, TRL), smoke-tested explicit-gate FinRL/RLlib/Ray Tune baselines, and one activation-gated W&B experiment backend. The remaining research gap is no longer missing runnable baselines for OpenClaw/Qlib/TRL/FinRL/RLlib/Tune; it is the lack of production activation for Qlib/TRL, the still-closed production/broker/live/capital activation path for OpenClaw, the FinRL first active-lane proof after RL approval, and the broader gated RL/W&B activation paths.

The research service boundary now exposes a fail-closed dormant capability inventory for OpenClaw, Qlib, TRL, FinRL, RLlib, Ray Tune, and W&B from the research-worker gateway, research orchestrator, policy-learning, and BFF aggregate surfaces. That inventory is read-only operator metadata (`gate_state=fail_closed`, `allowed_scope=capability_metadata_read_only`) and does not authorize training dispatch, paper/canary/live execution, registry writes, governance writes, broker sessions, or capital binding.

### Existing Evidence

- `OSS_INTEGRATION_CHECKLIST.md`: Integration status per component
- `OSS_INTEGRATION_AUDIT.md`: Audit correcting conceptual vs. real integration
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib entry gate (OSS-003)
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL entry gate (OSS-003)
- `services/learning/rl/PATH_DEFINITION.md`: RL path definition (LP-005)
- `services/registry/experiments/WANDB_ACTIVATION.md`: W&B activation criteria (OSS-003)
- `integrations/openclaw/{integration,governance,smoke_test}.md`: OpenClaw governed runtime baseline and live gateway smoke evidence
- `integrations/qlib/{integration,governance,smoke_test,activation_packet}.md`: Qlib smoke-tested LightGBM adapter baseline plus activation packet
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
- `services/research-worker-gateway/main.py`: fail-closed dormant backend inventory and dispatch denial policy
- `services/research/main.py`: research-orchestrator dormant backend inventory and write-path denial policy
- `services/policy-learning/main.py`: policy-learning dormant backend inventory and write-path denial policy
- `services/control-plane/bff/main.py`: read-only `/api/v1/operator/research/oss-preactivation` aggregate for dormant capability and rejection evidence across the research orchestrator, policy-learning, worker gateway, and OpenClaw adapter surfaces
- `scripts/smoke_oss_activation_ready_matrix.py`: E2E smoke matrix covering default fail-closed Qlib/TRL/RL/W&B paths, explicit offline test gates, paper/canary/live rejection, local artifact evidence, and no registry/governance/broker/live writes

### Why It Is a Real Gap

The gap is real but scoped: the platform has a valid production research path for behavior cloning, persona optimization, rapid strategy backtesting, econometrics/regime analysis, and derivatives pricing/risk, and it now has runnable governed/smoke-tested/activation-gated baselines for OpenClaw, Qlib, TRL, FinRL, RLlib, Ray Tune, and W&B. However, it still has no production-activated path for supervised alpha discovery (Qlib), preference learning (TRL), or sequential RL. This directly limits the Research Plane's coverage of the complete Blueprint's research problem types.

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
| OpenClaw runtime activation | Keep the fail-closed runtime-adoption scaffold refreshed; open broker sessions, paper/canary/live routes, or capital binding only through a future explicit activation gate | Near-term |
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
- [x] FinRL/RLlib/Ray Tune bounded activation-ready smoke evidence remains offline-only and RL-gated
- [x] W&B offline-local smoke plus explicit-gated online backend remains non-broker and non-capital-bound by default
- [x] OSS activation-ready E2E smoke matrix passes with no registry, governance, broker, or live writes
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
- `scripts/smoke_oss_activation_ready_matrix.py`: Qlib/TRL/RL/W&B activation-ready E2E smoke matrix
- `CANONICAL_DOCUMENT_MAP.md`: Canonical document registry

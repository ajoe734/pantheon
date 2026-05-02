# Deferred OSS Activation Map

**Task**: BP5-OSS-004
**Owner**: Codex2
**Reviewer**: Claude
**Scope**: Define the dormant implementation and executable activation path for deferred Qlib, TRL, FinRL, RLlib, and W&B rows
**Status**: Done — review approved by Claude 2026-04-16
**Last Updated**: 2026-05-01

---

## Purpose

This document is the canonical activation map for the five deferred OSS rows in the Pantheon
platform. It consolidates the distributed per-row gate documents into one place, reconciles
already-landed package pins and runnable baselines with checklist text that predates them,
resolves activation ownership, and makes each row's executable next step explicit.

The key boundary is **development allowed, activation gated**. Deferred rows may accumulate
repo-local dormant implementation work such as interfaces, schema, feature flags, offline/mock
smoke tests, Dockerfiles, and fail-closed adapters. They may not become active production,
paper, canary, live, registry-writing, governance-writing, or networked backend paths until the
named activation gate is satisfied.

2026-05-01 correction: the old blanket avoidance of live/production behavior does not apply to
non-ordering external integrations. The only hard fail-closed boundary is production-live
real-capital side effects such as broker order placement, cancel/replace, position/capital
mutation, and order-capable routing. Adjustable deferred/offline rows now have active follow-up
execution tasks to finish development and runtime smoke where appropriate:

| Component | Follow-up task | Intent |
|---|---|---|
| `W&B` | `P2-WANDB-ONLINE-SYNC-001` | Add SDK-backed online sync using a test project/API key and readback evidence while keeping broker/order/capital paths out of scope. |
| `Qlib` | `P2-QLIB-PROD-DATA-ACTIVATION-001` | Produce governed production-data proof and real/stub-selectable backend smoke for a reviewable candidate handoff. |
| `TRL` | `P2-TRL-RUNTIME-DATA-ACTIVATION-001` | Connect FB-002 preference-pair runtime data and run real TRL DPO smoke or explicit dependency/config evidence. |
| `FinRL / RLlib / Ray Tune` | `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` | Move dormant/deferred prep to bounded governed train/search runtime smoke with research-only artifacts. |

It does **not** replace the per-row gate documents. Those remain authoritative for detailed criteria
and workflow design. This map adds:

- one unambiguous status per row (what is true right now)
- reconciled package-pin facts
- explicit owner for dormant implementation work and later activation work
- the one concrete thing that must happen next per row

---

## Row Status at BP5-OSS-004 Execution

| Row | Checklist Status | Package/Container Pinned? | Governed Adapter Present? | Smoke Path Present? | Phase-5 Activation Owner |
|---|---|---|---|---|---|
| `Qlib` | `smoke-tested` | Yes — `pyqlib==0.9.6` in `services/research/qlib/requirements.txt`; Qlib Dockerfile exists | Yes — `GovernedQlibDataAdapter` + `StubLightGBMBackend` + `QlibLightGBMBackend` + `run_qlib_workflow` | Yes — smoke passes (14 unit tests + smoke assertions OK, revalidated 2026-04-24); activation packet now in `integrations/qlib/activation_packet.md` | Qwen (Qlib gate owner) — production blocked on RS-003 candidate readiness, governed market-data proof, and target StrategySpec binding |
| `TRL` | `smoke-tested` | Yes — `trl>=0.8.0,<0.10.0` pinned in `services/learning/trl/requirements.txt` | Yes — `GovernedPreferencePairAdapter` + `StubDPOBackend` + `TRLDPOBackend` + `run_trl_dpo_workflow` | Yes — smoke passes (29 unit tests + assertions OK, revalidated 2026-04-29); evidence in `integrations/trl/` | Claude (OSS-NEXT-002 owner) — production blocked on runtime data gates |
| `FinRL` | `smoke-tested` | Yes — `finrl==0.3.6` in `services/research/finrl/requirements.txt`; FinRL Dockerfile carries the deferred-prep scaffold | Yes — governed input adapter, stub backend, and FinRL import-path backend; activation evidence harness (`activation_smoke.py`) with `--enable-activation-ready` flag | Yes — `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` done; activation smoke produced explicit `ModuleNotFoundError` with `silent_stub_fallback=false`, checksum-bearing artifact bundle, evaluator packet, registry entry, and candidate packet in `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/` | task done; no broker route or capital binding; RL gate remains closed |
| `RLlib` | `smoke-tested` | Yes — `ray[rllib]>=2.9.0,<3.0.0` and `ray[tune]>=2.9.0,<3.0.0` in `services/research/rllib/requirements.txt`; RLlib Dockerfile carries the deferred-prep scaffold | Yes — RLlib train/eval and Ray Tune search adapters with activation evidence harnesses | Yes — `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` done; bounded train/search smoke produced explicit `ModuleNotFoundError` with `silent_stub_fallback=false`, research-only artifact output, evidence in `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/` | task done; no broker route or capital binding; RL gate remains closed |
| `W&B` | `activation-gated` | Yes — `wandb>=0.16.0,<1.0` in `services/registry/experiments/requirements.txt` for the experiments container | Offline local-store exists; SDK-backed `WandbOnlineBackend` exists behind `PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1` plus test project/API key | Offline local-store yes; online upload/readback harness present. Local credentialed smoke skipped on 2026-05-01 because W&B SDK and `WANDB_API_KEY` were not present in this workspace | Codex → hand off `P2-WANDB-ONLINE-SYNC-001` for review; no broker route or capital binding |

---

## Row-by-Row Activation Detail

### 1. Qlib

**Gate doc**: `services/learning/qlib/ACTIVATION_CRITERIA.md` (approved, OSS-003)

**Current repo truth**:

- `pyqlib==0.9.6` is pinned in `services/research/qlib/requirements.txt`.
- A Qlib research Dockerfile exists at `services/research/qlib/Dockerfile`.
- Governed adapter baseline is implemented in `services/research/qlib/adapter/`:
  `GovernedQlibDataAdapter`, `StubLightGBMBackend`, `QlibLightGBMBackend`,
  and `run_qlib_workflow()`.
- A fail-closed, offline pre-activation preflight scaffold exists at
  `services/research/qlib/preflight.py`; it reports readiness only and does not
  run LightGBM or write registry/governance state.
- Smoke path and unit coverage are already present:
  `services/research/qlib/smoke_test.py`, `services/research/qlib/test_adapter.py`,
  and evidence in `integrations/qlib/{integration,governance,smoke_test}.md`.
- The first governed LightGBM activation packet is now prepared at
  `integrations/qlib/activation_packet.md`; it truthfully records the remaining
  RS-003 / dataset / strategy-binding gates instead of overstating production readiness.
- The gate doc's §7 "Next Steps" predates the runnable baseline. It should now be read as
  "version pin, adapter boundary, and smoke baseline are done; production activation gates remain."
- `QlibTool` is already named in `services/control-plane/skills/skills.yaml` and the router/
  permission contracts, confirming downstream consumer expectations exist.

**What Qlib still needs before production activation**:

| Evidence required | Status |
|---|---|
| Version pin | Done — `pyqlib==0.9.6` |
| Governed data-handler adapter (`services/research/qlib/adapter/`) | Done — runnable adapter baseline landed |
| One LightGBM smoke run emitting canonical `artifact_state` + `deployment_summary.current_stage` output | Done — smoke baseline landed on 2026-04-17 |
| RS-003 candidate strategy exists in registry | Activation gate — required by `ACTIVATION_CRITERIA.md §1` |
| Governed dataset of >=50 instruments with >=2 years OHLCV history | Activation gate — not proven by the smoke baseline |
| First governed alpha activation packet against the target universe | Prepared — `integrations/qlib/activation_packet.md` now defines the evidence bundle and preserves the remaining blockers |

**Activation prerequisite chain**:
1. RS-003 baseline StrategySpec candidate must exist in registry (`artifact_state=candidate`)
2. Governed dataset of ≥2 years daily OHLCV for ≥50 instruments must be accessible
3. Supervised-learning problem fit must be documented in the candidate strategy spec
4. `pyqlib==0.9.6` package compatibility with the governed research stack remains verified

**Executable next step**: When the RS-003 candidate, governed market-data, and target StrategySpec
binding evidence is assembled, run the offline preflight first via
`services/research/qlib/preflight.py`. Only if that report opens all required gates should the
first target-universe LightGBM activation run through `QlibLightGBMBackend` and submit the
resulting registry artifact envelope using canonical `artifact_state=draft` per
`ACTIVATION_CRITERIA.md §3.1`.

**Activation owner for follow-on work**: Qwen (Qlib gate owner). The adapter + smoke baseline is
already landed; the remaining follow-on is the first governed production activation once the data
and strategy gates in `ACTIVATION_CRITERIA.md §1` are satisfied.

---

### 2. TRL

**Gate doc**: `services/learning/trl/ACTIVATION_CRITERIA.md` (approved, OSS-003)

**Current repo truth** (updated 2026-04-17 — OSS-NEXT-002):

- `trl>=0.8.0,<0.10.0` pinned in `services/learning/trl/requirements.txt`;
  compatibility verified against DSPy v2.4.5, imitation v1.0.1, MLflow 3.10.1, pyqlib 0.9.6.
- Governed pair-construction adapter: `GovernedPreferencePairAdapter` in
  `services/learning/trl/adapter/trl_adapter.py`. Handles approve/reject/edit events from FB-002
  with actor_role/promotion_state/artifact_id governance filters.
- `StubDPOBackend` (CI/smoke, no ML deps) and `TRLDPOBackend` (upstream TRL DPO,
  distilbert-base-uncased) both implemented.
- `run_trl_dpo_workflow()` entrypoint emits canonical `artifact_state=draft` registry entries.
- A fail-closed, non-writing pre-activation preflight scaffold exists at
  `services/learning/trl/preflight.py`; it reports FB-002 event volume,
  preference-pair volume, imitation-artifact readiness, and downstream-consumer readiness
  without running active DPO.
- `services/learning/trl/activation_smoke.py` is the explicit-gated runtime-data evidence harness.
  On 2026-05-01 it produced a bounded FB-002 evidence packet with 240 governed events,
  240 preference pairs, 3 strategy families, all approve/edit/reject actions, evaluator packet,
  registry entry, candidate packet, and checksum-bearing artifact bundle under
  `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/`.
- The 2026-05-01 real backend attempt failed explicitly because the local environment does not
  have the upstream `trl` module installed (`ModuleNotFoundError: No module named 'trl'`);
  the evidence records `silent_stub_fallback=false`.
- Smoke test passes: 29 unit tests + assertions OK (revalidated 2026-04-29). Evidence in `integrations/trl/`.
- `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md` and `WORKFLOW_DEFINITION.md` remain
  authoritative for pair-construction contract and workflow design.
- The imitation integration (LP-002, `governed`) is complete and is TRL's declared prerequisite.
- The FB-002 feedback volume threshold (≥200 events, ≥100 preference pairs) is a runtime-data
  gate, not a code gate — cannot be pre-staged.

**What TRL still needs before production activation**:

| Evidence required | Status |
|---|---|
| Version pin (`trl>=0.8.0`) in `services/learning/trl/requirements.txt` | Done — `trl>=0.8.0,<0.10.0` |
| Pair-construction pipeline feeding FB-002 events | Done — `GovernedPreferencePairAdapter` |
| Minimal DPO smoke test on synthetic preference pairs | Done — 29 unit tests + smoke assertions OK |
| Dependency compatibility confirmed | Done — verified in `requirements.txt` header comment |
| FB-002 event volume at runtime (≥200 events, ≥100 pairs) | Bounded evidence produced — 240 governed fixture events and 240 pairs in `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/`; production store volume still remains a runtime gate |
| LP-002 imitation baseline active with approved artifacts | Bounded readiness probe recorded; production registry proof remains a runtime gate |
| Downstream consumer ready (EV-001, LP-005, or LP-001) | EV-001 contract-readiness probe recorded; production consumer enablement remains a runtime gate |

**Activation prerequisite chain**:
1. LP-002 imitation baseline must be active and producing governed `artifact_state=approved` artifacts
2. FB-002 feedback store must accumulate ≥200 governed events spanning ≥2 strategy families
3. ≥100 valid preference pairs must be constructable from those events per `PREFERENCE_LEARNING_CONTRACT.md §4`
4. At least one downstream consumer (EV-001, LP-005, or LP-001) must be ready to accept preference models

**Executable next step**: All code gates are now closed. The remaining gates are runtime-data gates.
When FB-002 accumulates sufficient event volume, run the non-writing preflight at
`services/learning/trl/preflight.py` against the runtime evidence first. Only if the required
FB-002 and preference-pair gates open should the owner execute a production DPO training run using
`TRLDPOBackend` and submit the resulting artifact for registry admission per `ACTIVATION_CRITERIA §3.2`.

**Activation owner for follow-on work**: Claude (OSS-NEXT-002 task owner) for smoke/baseline evidence;
Qwen (gate doc owner) for production DPO run when runtime data gates clear.

---

### 3. FinRL

**Gate doc**: `services/learning/rl/RL_PATH_APPROVAL_GATE.md` (accepted decision packet, BP5-OSS-004)

**Current repo truth**:

- `finrl==0.3.6` is already pinned in `services/research/finrl/requirements.txt`.
- The FinRL Dockerfile copies the repo-local deferred-prep adapter, worker, examples, and smoke
  path while keeping execution behind an explicit env gate.
- Repo-local dormant adapter pieces exist:
  `GovernedFinRLPolicyAdapter`, `StubFinRLBackend`, `FinRLPPOBackend`, and
  `run_finrl_workflow()`.
- The worker requires `PANTHEON_FINRL_PREP_ENABLED=1`; the smoke path requires
  `--enable-deferred-prep`.
- The dormant output is an in-memory registry-ready envelope only: `artifact_state=draft`,
  `deployment_summary.current_stage=none`, `gate_state=closed`, and no registry/governance write.
- `FinRLTool` is already named in `services/control-plane/skills/skills.yaml` and the router/
  permission contracts.
- FinRL activation is blocked on the RL path gate (all five criteria in `PATH_DEFINITION.md §1`
  must be met before any RL training begins). The most critical: Qlib supervised alpha must be
  exhausted first.

**What FinRL still needs before it leaves `criteria-defined`**:

| Evidence required | Status |
|---|---|
| Version pin | Done — `finrl==0.3.6` |
| RL path approval (all five `PATH_DEFINITION.md §1` criteria met) | Not met — Qlib not yet active |
| Governed policy-output mapping (single-agent, canonical `artifact_state`) | Prep-only done — canonical `rl_policy` draft envelope exists; production adapter activation remains gated |
| One smoke path proving artifact production (not just pinned container) | Prep-only done — explicit CLI gate required; not production activation evidence |
| Integration test confirming `FinRLTool` control-plane path resolves to governed adapter | Missing |

**Activation prerequisite chain**:
1. Qlib supervised alpha must reach `artifact_state=approved` and show stable Sharpe for ≥3 months
2. Sequential decision-making dependency must be formally justified (not just signal scoring)
3. ≥2 years intraday OHLCV + order fills must be available for the target universe
4. Explicit RL path approval decision must be recorded (human gate or governance review)

**Executable next step**: Production RL activation remains blocked until the RL path approval gate
is passed. Dormant pre-activation work is allowed if it stays fail-closed: repo-local interfaces,
artifact schema, feature flags defaulting off, offline/mock smoke tests, and no production
dispatch, registry write, paper/canary/live, or capital-bound execution path. The prerequisite
activation checkpoint is formalized in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`.

The next activation action is to assemble the re-entry evidence packet and obtain approval to open
the governed **FinRL-first** adapter lane. That first active lane is intentionally limited to
single-agent policy-output mapping plus one smoke path that proves a canonical `rl_policy`
artifact envelope.

**Activation owner for follow-on work**: Copilot (LP-005/RL path owner). Dormant scaffold work may
be prepared before the gate; governed adapter activation and smoke evidence remain Copilot's lane
once the RL path gate is passed.

---

### 4. RLlib + Ray Tune

**Gate doc**: `services/learning/rl/PATH_DEFINITION.md` (accepted, LP-005)

**Current repo truth**:

- `ray[rllib]>=2.9.0,<3.0.0` and `ray[tune]>=2.9.0,<3.0.0` are now pinned in
  `services/research/rllib/requirements.txt`. The Dockerfile now copies the repo-local
  deferred-prep adapters, workers, examples, and smoke paths while keeping the default command
  inert.
- Repo-local dormant adapters exist:
  `GovernedRLlibTrainEvalAdapter`, `StubRLlibBackend`, `RLlibPPOBackend`,
  `GovernedRayTuneSearchAdapter`, `StubRayTuneBackend`, `RayTuneImportBackend`,
  `run_rllib_workflow()`, and `run_ray_tune_workflow()`.
- The prep workers require explicit env gates:
  `PANTHEON_RLLIB_PREP_ENABLED=1` for RLlib and `PANTHEON_RAYTUNE_PREP_ENABLED=1` for Ray Tune.
- The prep smoke paths require explicit CLI gates:
  `--enable-deferred-prep` for both `smoke_test.py` and `ray_tune_smoke_test.py`.
- The dormant outputs are in-memory registry envelopes only: `artifact_state=draft`,
  `deployment_summary.current_stage=none`, `gate_state=closed`, and no registry/governance write.
- This scaffold closes the "where is the pin and prep boundary?" ambiguity that previously left
  RLlib only implied inside LP-005 prose.
- The LP-005 workflow in `PATH_DEFINITION.md §2` describes the RLlib + Ray Tune runtime boundary
  in detail (PPO algorithm, hyperparameter sweep, environment state/action shapes).
- `services/evaluation/optimizers/contract.md` already models RLlib/FinRL outputs as governed
  artifacts, confirming downstream consumer expectations exist.

**What RLlib still needs before it leaves `version-pinned`**:

| Evidence required | Status |
|---|---|
| RLlib version pin (`ray[rllib]>=2.9.0`) in `services/research/rllib/requirements.txt` | Done — landed as part of BP5-OSS-004 |
| Ray Tune version pin (reconcile `version-pinned` checklist entry with actual file) | Done — `ray[tune]>=2.9.0,<3.0.0` in same `requirements.txt` |
| RLlib environment contract instantiation (state/action shape, episode config) | Prep-only done — repo-local adapter schema and sample dataset exist; production environment activation remains gated |
| One governed train/eval loop with explicit RLlib/Tune runtime boundary | Prep-only done — offline stub/import-path scaffold exists; governed production train/eval remains gated |
| RL path approval gate (same as FinRL) | Not met |

**Activation prerequisite chain** (same RL gate as FinRL, plus):
1. Production environment contract must be approved from the dormant repo-local scaffold
2. All five `PATH_DEFINITION.md §1` RL entry criteria must be met

**Executable next step**: The version-pin and dormant-prep scaffold gaps are now closed. The
blocking gate is the RL path approval decision (same as FinRL). The scaffold may be kept current
with offline, explicit-gate tests only; no governed production train/eval loop, registry-writing
adapter, paper/canary/live path, or active RLlib/Tune dispatch may proceed until all five entry
criteria in `PATH_DEFINITION.md §1` are met and formally recorded (including Qlib supervised alpha
exhaustion). That checkpoint now lives in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`; after
it reopens, Pantheon should materialize the **FinRL-first** lane before opening any governed
RLlib/Tune activation lane.

**Activation owner for follow-on work**: Copilot (LP-005/RL path owner). Same RL path gate applies.

---

### 5. W&B

**Gate doc**: `services/registry/experiments/WANDB_ACTIVATION.md` (approved, OSS-003)

**Current repo truth**:

- `services/registry/experiments/requirements.txt` pins `wandb>=0.16.0,<1.0` for the W&B
  online sync smoke container.
- `services/registry/experiments/config.py` now defines `EXPERIMENT_BACKEND` as an env-var
  selector (default `"mlflow"`). `"wandb"` offline/dryrun mode is accepted only when
  `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` is set (legacy
  `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1` remains a compatibility alias). Online mode requires
  `PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1` plus a test W&B project and `WANDB_API_KEY`.
- `services/registry/experiments/adapter.py` exposes `RegistryExperimentAdapter` with an
  `ExperimentBackend` protocol, backend factory wiring, an offline `OfflineWandbLocalBackend`,
  and an SDK-backed `WandbOnlineBackend` that uploads metrics/artifact bundles and reads back
  W&B run/artifact refs when the online gate and credentials are present.
- `services/registry/experiments/README.md` records the offline default, online env gate,
  credentialed smoke command, and missing-config skip behavior.
- MLflow is now at `governed` status (`mlflow==3.10.1` pinned, runnable adapter present, smoke
  tested on 2026-04-15 per OSS-003). The 30-day operational history criterion is not yet met.
- The adapter now mirrors canonical `artifact_state` + derived `deployment_stage`; legacy
  `lifecycle_state` input is accepted only as a compatibility projection.

**Concrete blocking conditions (all must be resolved before W&B activation begins)**:

| Blocking condition | Status |
|---|---|
| MLflow ≥30 days operational history | Not met — MLflow governed as of 2026-04-15 |
| Explicit operator preference documented | No documented operator request |
| `EXPERIMENT_BACKEND` selector in `services/registry/experiments/config.py` | Done — default `"mlflow"`; W&B selectable only behind explicit offline-store or online-sync flags |
| `RegistryExperimentAdapter` generalized to accept non-MLflow backends | Done — backend protocol/factory supports MLflow, offline W&B local store, and explicit-gated SDK-backed W&B online backend |
| Canonical `artifact_state` / `deployment_stage` migration landed in experiment bridge | Prep-only done — canonical fields are primary; legacy lifecycle is compatibility-only |
| W&B SDK pin (`wandb>=0.16.0`) | Done — `wandb>=0.16.0,<1.0` in `services/registry/experiments/requirements.txt` |
| Network/infrastructure readiness for `api.wandb.ai` | Not verified in this workspace; local smoke reports explicit skipped config when gate/project/key/SDK are absent |

**Executable next step**: `P2-WANDB-ONLINE-SYNC-001` now has an SDK-backed online sync path and
smoke harness. The credentialed smoke command is:
`PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1 PANTHEON_WANDB_PROJECT=<test-project> WANDB_API_KEY=<test-api-key> python3 services/registry/experiments/smoke_test.py --backend wandb-online`.
Without those env vars and SDK install, the harness returns a structured skip naming missing
config without persisting secrets. This is not a broker, paper/canary/live, order-routing,
registry-promotion, or capital-binding task.

**Activation owner for follow-on work**: Codex owns `P2-WANDB-ONLINE-SYNC-001`; Claude reviews.

---

## Activation Readiness Summary

| Row | Overall Readiness | Single Blocking Gate | First Executable Proof |
|---|---|---|---|
| `Qlib` | Smoke-tested baseline landed — package pin, governed adapter, LightGBM smoke path, and activation packet are present | RS-003 candidate readiness + governed dataset availability (>=50 instruments, >=2 years data) + target StrategySpec binding | Run the first governed alpha activation through `QlibLightGBMBackend` when the gates clear |
| `TRL` | Smoke-tested baseline landed — blocked on runtime data gates; non-writing preflight scaffold present | ≥200 FB-002 events, ≥100 pairs, active LP-002, and a ready downstream consumer | Accumulate FB-002 volume, run the TRL preflight, then run the first governed production DPO activation with `TRLDPOBackend` |
| `FinRL` | Dormant adapter, worker, Dockerfile, examples, and explicit-gate smoke path are landed; follow-up runtime smoke is active | Bounded governed runtime smoke not yet completed | `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` |
| `RLlib` | Version pin and dormant RLlib/Ray Tune prep scaffold landed; follow-up runtime smoke is active | Bounded governed runtime smoke not yet completed | `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` |
| `W&B` | Offline local run store landed; SDK-backed online sync backend, SDK pin, BFF/evaluator ref preservation, and smoke harness are implemented | Credentialed W&B project/API-key smoke still requires external env in the target deployment | `P2-WANDB-ONLINE-SYNC-001` review |

---

## What This Document Does Not Do

- It does not approve dormant adapters, smoke tests, or package pins as production activation
  evidence unless the row explicitly says so.
- It does not approve or open the RL path. That remains a governance checkpoint.
- It does not by itself promote any activation-gated framework into active production use.
  Some rows now have runnable baselines, but the map only records the remaining gates and next
  executable proofs. Note: BP5-OSS-004 advanced the RLlib checklist row to `version-pinned`;
  this later dormant scaffold is still prep-only and does not change the activation gate.
- It does not forbid dormant implementation. It forbids activation before the named gate: no
  production dispatch, paper/canary/live, canonical registry/governance writes, or
  broker/capital-bound runtime path may be inferred from prep-only work. The W&B online backend is
  limited to explicit-gated experiment metadata sync and readback.

---

## References

- `OSS_INTEGRATION_CHECKLIST.md`: per-row checklist status and evidence requirements
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib gate (OSS-003, approved)
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL gate (OSS-003, approved)
- `integrations/qlib/integration.md`: Qlib smoke-tested adapter baseline
- `integrations/trl/integration.md`: TRL smoke-tested adapter baseline
- `services/learning/rl/PATH_DEFINITION.md`: FinRL / RLlib / Ray Tune gate (LP-005, accepted)
- `services/registry/experiments/WANDB_ACTIVATION.md`: W&B gate (OSS-003, approved)
- `support/sidecars/BP5-OSS-004/BP5-OSS-004-SIDECAR-ACCEPTANCE.md`: Codex acceptance packet
- `TARGET_ARCHITECTURE.md`: learning-plane north star and OSS framework policy
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`: evolution governance thresholds relevant to learning artifacts

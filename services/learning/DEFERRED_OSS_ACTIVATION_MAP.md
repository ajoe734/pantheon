# Deferred OSS Activation Map

**Task**: BP5-OSS-004
**Owner**: Codex2
**Reviewer**: Claude
**Scope**: Define the dormant implementation and executable activation path for deferred Qlib, TRL, FinRL, RLlib, and W&B rows
**Status**: Done — review approved by Claude 2026-04-16
**Last Updated**: 2026-04-29

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
| `FinRL` | `criteria-defined` | Yes — `finrl==0.3.6` in `services/research/finrl/requirements.txt`; FinRL Dockerfile carries the deferred-prep scaffold | Prep-only yes — governed input adapter, stub backend, and FinRL import-path backend exist, but production activation remains closed | Prep-only yes — smoke requires `--enable-deferred-prep` and emits draft/none only | Copilot (RL path owner) → dormant contracts/scaffolds may proceed fail-closed; governed training activation requires explicit RL-path approval first |
| `RLlib` | `version-pinned` | Yes — `ray[rllib]>=2.9.0,<3.0.0` and `ray[tune]>=2.9.0,<3.0.0` in `services/research/rllib/requirements.txt`; RLlib Dockerfile carries the deferred-prep scaffold | Prep-only yes — RLlib train/eval and Ray Tune search adapters exist, but production activation remains closed | Prep-only yes — both smoke paths require `--enable-deferred-prep` and emit draft/none only | Copilot (RL path owner) → dormant environment contracts/offline harnesses may proceed fail-closed; governed train/eval activation requires the RL path approval gate |
| `W&B` | `criteria-defined` | No SDK pin landed yet | Offline local-store only — `EXPERIMENT_BACKEND=wandb` is selectable only behind `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` (legacy `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1` still accepted); `OfflineWandbLocalBackend` writes local JSON run/artifact refs with no SDK import or network activation | Offline local-store yes — smoke path exists behind the explicit flag only | Qwen (gate doc owner) → offline adapter upkeep may proceed; SDK-backed or networked backend activation waits for all re-entry conditions |

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
| FB-002 event volume at runtime (≥200 events, ≥100 pairs) | Runtime gate — cannot be pre-staged |
| LP-002 imitation baseline active with approved artifacts | Runtime gate — LP-002 is governed |
| Downstream consumer ready (EV-001, LP-005, or LP-001) | Runtime gate — requires downstream activation |

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

- No `wandb` SDK pin exists in any `requirements.txt`.
- `services/registry/experiments/config.py` now defines `EXPERIMENT_BACKEND` as an env-var
  selector (default `"mlflow"`). `"wandb"` is accepted only when
  `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` is set (legacy
  `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1` remains a compatibility alias), and
  `PANTHEON_WANDB_MODE` is restricted to `offline` or `dryrun`.
- `services/registry/experiments/adapter.py` exposes `RegistryExperimentAdapter` with an
  `ExperimentBackend` protocol, backend factory wiring, and an offline
  `OfflineWandbLocalBackend` that writes W&B-compatible local run/artifact refs without importing
  the SDK or connecting to the network.
- `services/registry/experiments/README.md` explicitly states W&B remains deferred.
- MLflow is now at `governed` status (`mlflow==3.10.1` pinned, runnable adapter present, smoke
  tested on 2026-04-15 per OSS-003). The 30-day operational history criterion is not yet met.
- The adapter now mirrors canonical `artifact_state` + derived `deployment_stage`; legacy
  `lifecycle_state` input is accepted only as a compatibility projection.

**Concrete blocking conditions (all must be resolved before W&B activation begins)**:

| Blocking condition | Status |
|---|---|
| MLflow ≥30 days operational history | Not met — MLflow governed as of 2026-04-15 |
| Explicit operator preference documented | No documented operator request |
| `EXPERIMENT_BACKEND` selector in `services/registry/experiments/config.py` | Done — default `"mlflow"`; W&B selectable only behind explicit offline-store flag |
| `RegistryExperimentAdapter` generalized to accept non-MLflow backends | Offline local-store done — backend protocol/factory exists, but no SDK-backed W&B backend is active |
| Canonical `artifact_state` / `deployment_stage` migration landed in experiment bridge | Prep-only done — canonical fields are primary; legacy lifecycle is compatibility-only |
| W&B SDK pin (`wandb>=0.16.0`) | Missing |
| Network/infrastructure readiness for `api.wandb.ai` | Not verified |

**Executable next step**: No SDK-backed, online, or production-supporting W&B activation task
should open yet. The `EXPERIMENT_BACKEND` env-var selector and offline local-store adapter are now
in place, and offline adapter upkeep may continue if it remains non-networked, feature-flagged,
and incapable of becoming the active online backend. W&B remains formally deferred because the six re-entry conditions
in `WANDB_ACTIVATION.md §7.3` are still unmet. The next activation action is to prepare a reopen
packet once those six conditions are simultaneously satisfied; only then should Pantheon
materialize separate execution tasks for SDK-backed W&B backend implementation.

`EXEC-OSS-WANDB-001` closes the execution-slice ambiguity here: the first reviewable follow-up is
the **reopen packet itself**, not a backend implementation slice.

**OSS-NEXT-004 decision (2026-04-17)**: W&B is **formally deferred** for the current wave. All
six entry criteria remain unmet. Detailed re-entry gate now in `WANDB_ACTIVATION.md §7`. Earliest
eligible reopen: 2026-05-15 (MLflow 30-day history gate).

**Activation owner for follow-on work**: Qwen (gate doc owner). W&B follow-on should be a
separate execution task once all six re-entry conditions in `WANDB_ACTIVATION.md §7.3` are met.

---

## Activation Readiness Summary

| Row | Overall Readiness | Single Blocking Gate | First Executable Proof |
|---|---|---|---|
| `Qlib` | Smoke-tested baseline landed — package pin, governed adapter, LightGBM smoke path, and activation packet are present | RS-003 candidate readiness + governed dataset availability (>=50 instruments, >=2 years data) + target StrategySpec binding | Run the first governed alpha activation through `QlibLightGBMBackend` when the gates clear |
| `TRL` | Smoke-tested baseline landed — blocked on runtime data gates; non-writing preflight scaffold present | ≥200 FB-002 events, ≥100 pairs, active LP-002, and a ready downstream consumer | Accumulate FB-002 volume, run the TRL preflight, then run the first governed production DPO activation with `TRLDPOBackend` |
| `FinRL` | Dormant adapter, worker, Dockerfile, examples, and explicit-gate smoke path are landed; outputs remain draft/none and non-writing | RL path approval gate not met (Qlib must plateau first) | Approval packet against `services/learning/rl/RL_PATH_APPROVAL_GATE.md`; then governed single-agent policy-output mapping |
| `RLlib` | Version pin and dormant RLlib/Ray Tune prep scaffold landed; workers/smokes require explicit gates and output draft/none only | Same RL path approval gate as FinRL; production train/eval and registry-writing adapters remain closed | Approval packet against `services/learning/rl/RL_PATH_APPROVAL_GATE.md`; then governed activation lane after FinRL proof |
| `W&B` | Deferred honestly; `EXPERIMENT_BACKEND` selector and offline local run store now landed | SDK-backed backend activation blocked; MLflow 30-day operational history not yet met | Keep offline local adapter fail-closed; after all re-entry gates clear, implement SDK-backed backend |

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
  production dispatch, paper/canary/live, canonical registry/governance writes, networked W&B
  backend, or broker/capital-bound runtime path may be inferred from prep-only work.

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

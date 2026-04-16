# Deferred OSS Activation Map

**Task**: BP5-OSS-004
**Owner**: Claude
**Reviewer**: Codex
**Scope**: Define the executable activation path for deferred Qlib, TRL, FinRL, RLlib, and W&B rows
**Status**: Done — review approved by Codex 2026-04-16
**Last Updated**: 2026-04-16

---

## Purpose

This document is the canonical activation map for the five deferred OSS rows in the Pantheon
platform. It consolidates the distributed per-row gate documents into one place, reconciles
already-landed package pins with checklist text that predates them, resolves activation ownership,
and makes each row's executable next step explicit.

It does **not** replace the per-row gate documents. Those remain authoritative for detailed criteria
and workflow design. This map adds:

- one unambiguous status per row (what is true right now)
- reconciled package-pin facts
- explicit activation owner for the follow-on adapter + smoke work
- the one concrete thing that must happen next per row

---

## Row Status at BP5-OSS-004 Execution

| Row | Checklist Status | Package/Container Pinned? | Governed Adapter Present? | Smoke Path Present? | Phase-5 Activation Owner |
|---|---|---|---|---|---|
| `Qlib` | `criteria-defined` | Yes — `pyqlib==0.9.1` in `services/research/qlib/requirements.txt`; Qlib Dockerfile exists | No | No | Claude (BP5-OSS-004 owner) → hand off to Qwen for adapter + smoke implementation |
| `TRL` | `criteria-defined` | No package pin; no worker image present | No | No | Qwen (gate doc owner) — activated only after FB-002/imitation prerequisites are met |
| `FinRL` | `criteria-defined` | Yes — `finrl==0.3.6` in `services/research/finrl/requirements.txt`; FinRL Dockerfile exists | No | No | Copilot (RL path owner) → governed adapter path requires explicit RL-path approval first |
| `RLlib` | `version-pinned` | Yes — `ray[rllib]>=2.9.0,<3.0.0` in `services/research/rllib/requirements.txt`; RLlib Dockerfile stub exists | No | No | Copilot (RL path owner) → RL path approval gate required before adapter work |
| `W&B` | `criteria-defined` | No SDK pin landed yet | Partial — blocking design exists against real MLflow-first adapter; `EXPERIMENT_BACKEND` selector now in `services/registry/experiments/config.py` (default `"mlflow"`, no W&B backend wired) | No | Qwen (gate doc owner) → blocked on adapter generalization; no implementation work may begin until MLflow-first stabilizes |

---

## Row-by-Row Activation Detail

### 1. Qlib

**Gate doc**: `services/learning/qlib/ACTIVATION_CRITERIA.md` (approved, OSS-003)

**Current repo truth**:

- `pyqlib==0.9.1` is already pinned in `services/research/qlib/requirements.txt`.
- A Qlib research Dockerfile exists at `services/research/qlib/Dockerfile`.
- The gate doc's §7 "Next Steps" still says "Pin Qlib version and define adapter boundary." That text
  predates the pin landing; it should be read as "version pin is done; build data-handler adapter
  and smoke test remain."
- `QlibTool` is already named in `services/control-plane/skills/skills.yaml` and the router/
  permission contracts, confirming downstream consumer expectations exist.

**What Qlib still needs before it leaves `criteria-defined`**:

| Evidence required | Status |
|---|---|
| Version pin | Done — `pyqlib==0.9.1` |
| Governed data-handler adapter (`services/research/qlib/adapter/`) | Missing |
| One LightGBM smoke run emitting canonical `artifact_state` + `deployment_stage` output | Missing |
| Integration test confirming `QlibTool` control-plane path resolves to governed adapter | Missing |

**Activation prerequisite chain**:
1. RS-003 baseline StrategySpec candidate must exist in registry (`artifact_state=candidate`)
2. Governed dataset of ≥2 years daily OHLCV for ≥50 instruments must be accessible
3. Supervised-learning problem fit must be documented in the candidate strategy spec
4. `pyqlib==0.9.1` package compatibility with DSPy/imitation/MLflow confirmed (no conflicts)

**Executable next step**: Implement the Qlib data-handler adapter in `services/research/qlib/adapter/`
and run a single LightGBM model on a 10-ticker, 1-year stub dataset to validate the pipeline end-to-end.
Emit a registry artifact envelope using canonical `artifact_state=draft` per the shape in
`ACTIVATION_CRITERIA.md §3.1`.

**Activation owner for follow-on work**: Qwen (Qlib capability lane); Claude is accountable for
this activation map; Codex reviews. The adapter + smoke task should be materialized as a separate
execution task once this BP5-OSS-004 map is approved.

---

### 2. TRL

**Gate doc**: `services/learning/trl/ACTIVATION_CRITERIA.md` (approved, OSS-003)

**Current repo truth**:

- No `trl` package pin exists in any `requirements.txt` or Dockerfile in this slice.
- `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md` and `WORKFLOW_DEFINITION.md` provide
  detailed pair-construction and governance workflow.
- The gate doc specifies `trl>=0.8.0` as the expected version but this has not been pinned.
- The imitation integration (LP-002, `governed`) is complete and is TRL's declared prerequisite.
- The FB-002 feedback volume threshold (≥200 events, ≥100 preference pairs) is a runtime-data
  gate, not a code gate.

**What TRL still needs before it leaves `criteria-defined`**:

| Evidence required | Status |
|---|---|
| Version pin (`trl>=0.8.0`) in `services/learning/trl/requirements.txt` | Missing |
| Pair-construction pipeline feeding FB-002 events | Missing |
| Minimal DPO smoke test on 50–100 synthetic preference pairs | Missing |
| Confirmation that `trl>=0.8.0` does not conflict with DSPy/imitation/MLflow/Qlib | Missing |
| FB-002 event volume at runtime (≥200 events, ≥100 pairs) | Runtime gate — cannot be pre-staged |

**Activation prerequisite chain**:
1. LP-002 imitation baseline must be active and producing governed `artifact_state=approved` artifacts
2. FB-002 feedback store must accumulate ≥200 governed events spanning ≥2 strategy families
3. ≥100 valid preference pairs must be constructable from those events per `PREFERENCE_LEARNING_CONTRACT.md §4`
4. At least one downstream consumer (EV-001, LP-005, or LP-001) must be ready to accept preference models

**Executable next step**: Pin `trl>=0.8.0` in `services/learning/trl/requirements.txt`, verify
dependency compatibility, and implement a minimal pair-construction pipeline that can be smoke-tested
against synthetic preference pairs. The smoke test should produce a DPO training artifact with the
metadata envelope from `ACTIVATION_CRITERIA.md §3.1` (using `artifact_state=draft`).

**Activation owner for follow-on work**: Qwen (gate doc owner; TRL is explicitly Qwen's learning
lane). TRL follow-on implementation should not begin until the FB-002/imitation prerequisites are met.

---

### 3. FinRL

**Gate doc**: `services/learning/rl/PATH_DEFINITION.md §1` (accepted, LP-005)

**Current repo truth**:

- `finrl==0.3.6` is already pinned in `services/research/finrl/requirements.txt`.
- A FinRL research Dockerfile exists at `services/research/finrl/Dockerfile`.
- `FinRLTool` is already named in `services/control-plane/skills/skills.yaml` and the router/
  permission contracts.
- The gate doc's §6 "Next Steps" references RS-003, REG-001, and artifact materialization as
  future work; it does not acknowledge the already-landed package/container stub.
- FinRL activation is blocked on the RL path gate (all five criteria in `PATH_DEFINITION.md §1`
  must be met before any RL training begins). The most critical: Qlib supervised alpha must be
  exhausted first.

**What FinRL still needs before it leaves `criteria-defined`**:

| Evidence required | Status |
|---|---|
| Version pin | Done — `finrl==0.3.6` |
| RL path approval (all five `PATH_DEFINITION.md §1` criteria met) | Not met — Qlib not yet active |
| Governed policy-output mapping (single-agent, canonical `artifact_state`) | Missing |
| One smoke path proving artifact production (not just pinned container) | Missing |
| Integration test confirming `FinRLTool` control-plane path resolves to governed adapter | Missing |

**Activation prerequisite chain**:
1. Qlib supervised alpha must reach `artifact_state=approved` and show stable Sharpe for ≥3 months
2. Sequential decision-making dependency must be formally justified (not just signal scoring)
3. ≥2 years intraday OHLCV + order fills must be available for the target universe
4. Explicit RL path approval decision must be recorded (human gate or governance review)

**Executable next step**: FinRL implementation work is blocked until the RL path approval gate is
passed. The immediate executable action is to document the RL path approval decision point as a
formal governance checkpoint — i.e., create a `services/learning/rl/RL_PATH_APPROVAL_GATE.md`
that names the exact evidence and approver required to open the FinRL/RLlib implementation lane.

**Activation owner for follow-on work**: Copilot (LP-005/RL path owner). The governed adapter
and smoke path for FinRL are Copilot's lane once the RL path gate is passed.

---

### 4. RLlib + Ray Tune

**Gate doc**: `services/learning/rl/PATH_DEFINITION.md` (accepted, LP-005)

**Current repo truth**:

- `ray[rllib]>=2.9.0,<3.0.0` and `ray[tune]>=2.9.0,<3.0.0` are now pinned in
  `services/research/rllib/requirements.txt`. A Dockerfile stub exists at
  `services/research/rllib/Dockerfile` with a clear comment that it is a stub and no adapter
  is wired yet.
- The container stub closes the "where is the pin?" ambiguity that previously left RLlib only
  implied inside LP-005 prose.
- The LP-005 workflow in `PATH_DEFINITION.md §2` describes the RLlib + Ray Tune runtime boundary
  in detail (PPO algorithm, hyperparameter sweep, environment state/action shapes).
- `services/evaluation/optimizers/contract.md` already models RLlib/FinRL outputs as governed
  artifacts, confirming downstream consumer expectations exist.

**What RLlib still needs before it leaves `version-pinned`**:

| Evidence required | Status |
|---|---|
| RLlib version pin (`ray[rllib]>=2.9.0`) in `services/research/rllib/requirements.txt` | Done — landed as part of BP5-OSS-004 |
| Ray Tune version pin (reconcile `version-pinned` checklist entry with actual file) | Done — `ray[tune]>=2.9.0,<3.0.0` in same `requirements.txt` |
| RLlib environment contract instantiation (state/action shape, episode config) | Missing as repo-local artifact |
| One governed train/eval loop with explicit RLlib/Tune runtime boundary | Missing |
| RL path approval gate (same as FinRL) | Not met |

**Activation prerequisite chain** (same RL gate as FinRL, plus):
1. Environment contract must be instantiated as a repo-local file, not only described in prose
2. All five `PATH_DEFINITION.md §1` RL entry criteria must be met

**Executable next step**: The version-pin gap is now closed. The blocking gate is the RL path
approval decision (same as FinRL). No adapter or training work may proceed until all five entry
criteria in `PATH_DEFINITION.md §1` are met and formally recorded (including Qlib supervised
alpha exhaustion). The immediate executable action is to formalize the RL path approval gate in
`services/learning/rl/RL_PATH_APPROVAL_GATE.md`, naming the exact evidence and approver required.

**Activation owner for follow-on work**: Copilot (LP-005/RL path owner). Same RL path gate applies.

---

### 5. W&B

**Gate doc**: `services/registry/experiments/WANDB_ACTIVATION.md` (approved, OSS-003)

**Current repo truth**:

- No `wandb` SDK pin exists in any `requirements.txt`.
- `services/registry/experiments/config.py` now defines `EXPERIMENT_BACKEND` as an env-var stub
  (default `"mlflow"`). `"wandb"` is explicitly not in `_SUPPORTED_BACKENDS` — the config raises
  `EnvironmentError` if an unsupported backend is selected. This closes the "where is the
  backend selector?" ambiguity without wiring any W&B code.
- `services/registry/experiments/adapter.py` exposes `RegistryExperimentAdapter` with
  `PRIMARY_BACKEND = "mlflow"` and has not been generalized for non-MLflow backends.
- `services/registry/experiments/README.md` explicitly states W&B remains deferred.
- MLflow is now at `governed` status (`mlflow==3.10.1` pinned, runnable adapter present, smoke
  tested on 2026-04-15 per OSS-003). The 30-day operational history criterion is not yet met.
- The adapter still uses `lifecycle_state` / `paper` / `live` aliases rather than the canonical
  `artifact_state` + derived `deployment_stage` split required by the gate doc's §1.4.

**Concrete blocking conditions (all must be resolved before W&B activation begins)**:

| Blocking condition | Status |
|---|---|
| MLflow ≥30 days operational history | Not met — MLflow governed as of 2026-04-15 |
| Explicit operator preference documented | No documented operator request |
| `EXPERIMENT_BACKEND` selector in `services/registry/experiments/config.py` | Done — stub landed as part of BP5-OSS-004 (default `"mlflow"`, W&B not wired) |
| `RegistryExperimentAdapter` generalized to accept non-MLflow backends | Not done |
| Canonical `artifact_state` / `deployment_stage` migration landed in experiment bridge | Not done |
| W&B SDK pin (`wandb>=0.16.0`) | Missing |
| Network/infrastructure readiness for `api.wandb.ai` | Not verified |

**Executable next step**: W&B implementation work remains blocked on adapter generalization and
canonical-state migration tasks. The `EXPERIMENT_BACKEND` env-var stub is now in place. The next
concrete action is for Qwen (gate doc owner) to generalize `RegistryExperimentAdapter` so it
accepts configurable backends, and migrate canonical `artifact_state` / `deployment_stage`
support into the experiment bridge — but not before the MLflow 30-day operational history gate
is met.

**Activation owner for follow-on work**: Qwen (gate doc owner). W&B follow-on should be a
separate execution task once MLflow 30-day history is met and an operator preference is documented.

---

## Activation Readiness Summary

| Row | Overall Readiness | Single Blocking Gate | First Executable Proof |
|---|---|---|---|
| `Qlib` | Most ready of the five — package pinned, control-plane consumer exists | Governed data-handler adapter and smoke test missing | Adapter + one LightGBM smoke run in `services/research/qlib/adapter/` |
| `TRL` | Document-complete but execution-empty | No package pin; FB-002/imitation prerequisites not yet met | Pin `trl>=0.8.0`, build pair-construction pipeline, run minimal DPO smoke |
| `FinRL` | Package/container stub exists; control-plane consumer exists | RL path approval gate not met (Qlib must plateau first) | Formal RL path approval gate doc; then governed single-agent policy-output mapping |
| `RLlib` | Version pin now landed — package/container stub closed | Same RL path approval gate as FinRL; environment contract not yet a repo-local artifact | Formal RL path approval gate doc (`services/learning/rl/RL_PATH_APPROVAL_GATE.md`); then governed adapter |
| `W&B` | Deferred honestly; `EXPERIMENT_BACKEND` selector now landed | Adapter not generalized; MLflow 30-day operational history not yet met | Generalize `RegistryExperimentAdapter` for configurable backends (Qwen lane), after MLflow history gate clears |

---

## What This Document Does Not Do

- It does not implement adapters, smoke tests, or package pins (except the RLlib and W&B stubs
  called out as immediate executable next steps above).
- It does not approve or open the RL path. That remains a governance checkpoint.
- It does not make any deferred framework operational. The map only makes each row's executable
  next steps unambiguous. Note: BP5-OSS-004 did advance the RLlib checklist row from
  `criteria-defined` to `version-pinned` by landing the package pin and Dockerfile stub.

---

## References

- `OSS_INTEGRATION_CHECKLIST.md`: per-row checklist status and evidence requirements
- `services/learning/qlib/ACTIVATION_CRITERIA.md`: Qlib gate (OSS-003, approved)
- `services/learning/trl/ACTIVATION_CRITERIA.md`: TRL gate (OSS-003, approved)
- `services/learning/rl/PATH_DEFINITION.md`: FinRL / RLlib / Ray Tune gate (LP-005, accepted)
- `services/registry/experiments/WANDB_ACTIVATION.md`: W&B gate (OSS-003, approved)
- `support/sidecars/BP5-OSS-004/BP5-OSS-004-SIDECAR-ACCEPTANCE.md`: Codex acceptance packet
- `TARGET_ARCHITECTURE.md`: learning-plane north star and OSS framework policy
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`: evolution governance thresholds relevant to learning artifacts

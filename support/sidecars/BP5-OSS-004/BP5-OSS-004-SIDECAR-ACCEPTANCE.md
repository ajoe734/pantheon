# BP5-OSS-004 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `BP5-OSS-004-SIDECAR-ACCEPTANCE`
**Helper parent:** `BP5-OSS-004` - Define the executable activation path for deferred Qlib, TRL, and RL stack rows
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Codex`
**Reviewer:** `Claude`
**Date:** `2026-04-16`
**Status:** `done` — review_approved by Claude (2026-04-16); closed by Codex (2026-04-16)

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, registry semantics, or OSS checklist truth. It records the live repo evidence
> and the remaining normalization gaps for the BP5-OSS-004 sidecar slice.

---

## 1. Purpose

This packet gives `Claude` a compact review surface for `BP5-OSS-004`:

1. a criterion-by-criterion acceptance checklist against the phase-5 planning task
2. a row-by-row evidence snapshot for `Qlib`, `TRL`, `FinRL`, `RLlib`, and `W&B`
3. a dependency map showing what is already satisfied and what each deferred row still needs
4. a short list of normalization gaps the parent owner must close before calling the parent task done

The current repo truth is more mature than a pure "criteria-only" backlog note, but it is still
distributed across several documents and partial runtime stubs. The key review question for the
parent task is therefore:

**Has BP5-OSS-004 turned these distributed deferred-path documents into one explicit activation map
with named evidence requirements, unambiguous next steps, and clear ownership per row, without
pretending the frameworks are already runtime-integrated?**

---

## 2. Acceptance Checklist

Formal acceptance criteria from the phase-5 planning session:

- **AC-1:** `deferred OSS rows no longer sit in ambiguous criteria-only state without an executable next step`
- **AC-2:** `each deferred row names its entry criteria, evidence requirements, and activation owner`

### AC-1: Deferred rows have executable next steps

| Row | Current checklist state | Repo-local evidence of a concrete next step | What is still missing for parent closeout | Status |
|---|---|---|---|---|
| `Qlib` | `criteria-defined` in `OSS_INTEGRATION_CHECKLIST.md` | `services/learning/qlib/ACTIVATION_CRITERIA.md` defines entry criteria and workflow; `services/research/qlib/requirements.txt` pins `pyqlib==0.9.1`; `services/research/qlib/Dockerfile` exists | parent task should consolidate the executable next step into one canonical activation map, reconcile the already-pinned package with the doc text that still says "version selection" is next, and name the activation owner for adapter + smoke work | PARTIAL |
| `TRL` | `criteria-defined` in `OSS_INTEGRATION_CHECKLIST.md` | `services/learning/trl/ACTIVATION_CRITERIA.md` defines thresholds, workflow, registry constraints, and downstream consumers; `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md` and `WORKFLOW_DEFINITION.md` provide the gate details | no repo-local package pin, worker image, pair-construction pipeline, or smoke path is present in this slice; parent task should turn the document-only gate into an explicit activation sequence with owner and evidence refs | PARTIAL |
| `FinRL` | `criteria-defined` in `OSS_INTEGRATION_CHECKLIST.md` | RL entry criteria live in `services/learning/rl/PATH_DEFINITION.md`; `services/research/finrl/requirements.txt` pins `finrl==0.3.6`; `services/research/finrl/Dockerfile` exists; control-plane contracts already name `FinRLTool` | parent task should specify when the existing package/container stub becomes a governed adapter path, what evidence proves governed policy-output mapping, and who owns the activation | PARTIAL |
| `RLlib` | `criteria-defined` in `OSS_INTEGRATION_CHECKLIST.md`; `Ray Tune` is a separate `version-pinned` row | `services/learning/rl/PATH_DEFINITION.md` defines the RLlib + Ray Tune workflow and artifact model; `services/evaluation/optimizers/contract.md` already models RLlib / FinRL outputs | this slice did not surface a repo-local RLlib or Ray Tune worker package, requirements file, Dockerfile, or smoke entrypoint; parent task should make the missing pin/source/evidence path explicit instead of leaving RLlib implied inside the LP-005 prose | PARTIAL |
| `W&B` | `criteria-defined` in `OSS_INTEGRATION_CHECKLIST.md` | `services/registry/experiments/WANDB_ACTIVATION.md` defines entry criteria, target adapter design, output equivalence, and next steps; `services/registry/experiments/README.md` explicitly says W&B remains deferred | parent task should centralize the blocking conditions: no `EXPERIMENT_BACKEND` selector exists yet, and the live adapter still exposes an MLflow-first surface that uses `lifecycle_state` / `paper` / `live` aliases rather than the future canonical split | PARTIAL |

**AC-1 assessment:** `PENDING`. The repo no longer has a purely ambiguous "someday maybe" state for
these rows, because each row now has a documented path or blocking condition. The parent task still
needs to normalize those paths into one place and remove the remaining ambiguity around activation
owner, pin/source provenance, and the exact evidence that will move a row out of `criteria-defined`.

### AC-2: Entry criteria, evidence requirements, and activation owner are named

| Row | Entry criteria named today? | Evidence requirements named today? | Activation owner explicitly named today? | Reviewer note |
|---|---|---|---|---|
| `Qlib` | Yes — baseline StrategySpec, data depth, supervised fit, and no package conflicts are documented | Yes — adapter, smoke, and canonical registry projection are named in `ACTIVATION_CRITERIA.md` / checklist | Partial — the gate doc has `Owner: Qwen`, but the checklist row does not give a durable activation owner for the follow-on runtime work | PARTIAL |
| `TRL` | Yes — feedback volume, imitation baseline, preference pairs, baseline performance, and downstream consumer readiness are documented | Yes — pair-construction implementation, smoke, REG-001 alignment, and EV-001 integration are named | Partial — the gate doc has `Owner: Qwen`, but the activation owner for the executable follow-on remains implicit | PARTIAL |
| `FinRL` | Yes — inherited through `services/learning/rl/PATH_DEFINITION.md` entry criteria | Partial — package pin/container exist, but governed output mapping and smoke evidence are still only described at a higher-level RL path | No explicit row-level activation owner is surfaced in the checklist or RL path doc for the current BP5-OSS-004 follow-on | PARTIAL |
| `RLlib` | Yes — inherited through `services/learning/rl/PATH_DEFINITION.md` entry criteria and workflow | Partial — workflow/eval requirements are documented, but this slice did not surface a concrete repo-local runtime/package evidence path for RLlib/Tune | No row-level activation owner surfaced in the current checklist or RL path doc | PARTIAL |
| `W&B` | Yes — MLflow stability, operator preference, adapter generalization, canonical state migration, SDK compatibility, and network readiness are documented | Yes — backend selector, adapter implementation, metadata-equivalence smoke, and rollback enforcement are all named | Partial — the gate doc has `Owner: Qwen`, but the current parent execution owner is `Claude`, and the checklist row still lacks a normalized activation-owner field | PARTIAL |

**AC-2 assessment:** `PENDING`. Entry criteria and evidence requirements are already mostly present,
but activation ownership is still distributed between historical document owners and the current
phase-5 execution owner. `BP5-OSS-004` should explicitly resolve that gap rather than relying on
readers to infer ownership from older gate-doc headers.

---

## 3. Evidence Snapshot

### 3.1 Row readiness matrix

| Row | Gate/spec docs present | Repo-local pin or container evidence | Adapter/runtime surface present in this slice | Smoke path present | Net assessment |
|---|---|---|---|---|---|
| `Qlib` | Yes | Yes — `pyqlib==0.9.1`, Qlib Dockerfile | No governed adapter surfaced yet | No | strongest of the deferred rows after W&B; packaging has started, but activation still needs an adapter + smoke owner |
| `TRL` | Yes | No package pin surfaced in this slice | No | No | rich gate design, but still document-first rather than execution-ready |
| `FinRL` | Yes via RL path doc | Yes — `finrl==0.3.6`, FinRL Dockerfile | No governed adapter surfaced yet | No | package/container stub exists, but the executable activation path is still not normalized |
| `RLlib` | Yes via RL path doc | No repo-local runtime pin surfaced in this slice | No | No | still the thinnest executable path; parent task should not let RLlib hide behind generic RL prose |
| `W&B` | Yes | No SDK pin landed yet | Partial — blocking design exists against a real MLflow-first adapter | No | deferred honestly, but still blocked on backend generalization and canonical-state migration |

### 3.2 Useful repo signals for the reviewer

1. `OSS_INTEGRATION_CHECKLIST.md` already distinguishes `criteria-defined` rows from `governed`
   rows and gives next-step prose for all five deferred rows.
2. `services/research/qlib/requirements.txt` and `services/research/finrl/requirements.txt` show
   that package pinning/containerization has started for two rows even though the checklist text
   still speaks as if version selection is entirely future work.
3. `services/control-plane/skills/skills.yaml`, `services/control-plane/permissions/contract.md`,
   and `services/control-plane/router/contract.md` already name `QlibTool` and `FinRLTool`, which
   means downstream consumer expectations exist even though governed runtime adapters do not.
4. `services/registry/experiments/adapter.py` still exposes a `RegistryExperimentAdapter` with
   `PRIMARY_BACKEND = "mlflow"` and no backend selector, which makes the `W&B` blocking conditions
   concrete rather than hypothetical.
5. `services/registry/experiments/README.md` explicitly says `W&B` remains deferred, so the parent
   task should preserve that honesty while defining the activation path.

---

## 4. Dependency Map

### 4.1 Durable task dependencies already satisfied

| Dependency | Status | Why BP5-OSS-004 needs it |
|---|---|---|
| `BP5-OSS-003` | done per task brief | provides the honest baseline that `DSPy`, `imitation`, and `MLflow` are already runnable/governed, so BP5-OSS-004 can focus only on the truly deferred rows |
| `BP5-SVC-012` | done per task brief | provides the governed evolution-decision / review path that future activation work can cite instead of inventing ad hoc promotion semantics |

### 4.2 Row-level activation dependencies

| Row | Immediate prerequisites | First honest executable proof expected next |
|---|---|---|
| `Qlib` | RS-003 baseline strategy candidate, governed datasets, supervised-learning fit, package compatibility | a governed data-handler/adapter plus one LightGBM smoke run that emits canonical `artifact_state` + deployment-stage projection |
| `TRL` | sufficient FB-002 event volume, valid preference-pair volume, active imitation baseline, downstream consumer readiness | a pair-construction pipeline and minimal DPO smoke/eval path tied to REG-001-compatible metadata |
| `FinRL` | RL path approved, Qlib plateau demonstrated, sequential decision need justified, intraday data ready | a governed single-agent policy-output mapping and one smoke path that proves artifact production rather than only a pinned container |
| `RLlib` | same RL gate as `FinRL`, plus a concrete RLlib/Ray Tune runtime pin and environment contract instantiation | one governed train/eval loop and explicit evidence of the RLlib/Tune runtime boundary |
| `W&B` | stable MLflow reference path, operator need, adapter generalization, canonical state migration, network readiness | backend-neutral experiment adapter selection plus a metadata-equivalence smoke test against one registry entry |

### 4.3 Adjacent downstream consumers the parent task should keep in view

| Consumer | Dependency on BP5-OSS-004 output |
|---|---|
| `services/control-plane/skills/skills.yaml` and router/permission contracts | already name `QlibTool` / `FinRLTool`; they need an honest activation boundary so the tool names do not outpace the real worker/runtime path |
| `services/evaluation/optimizers/contract.md` | already models TRL and RL outputs as governed artifacts, so BP5-OSS-004 should align row-level evidence with those artifact expectations |
| `services/registry/experiments/` | `W&B` cannot activate until the experiment bridge becomes backend-neutral and canonical-state-safe |

---

## 5. Coordination Notes

There are two state-shape details worth preserving for the reviewer:

1. The original phase-5 planning session proposed `BP5-OSS-004` under `Gemini` ownership, but the
   current durable `ai-status.json` entry is owned by `Claude` with `Codex` as reviewer. This
   packet follows the durable state, not the historical planning proposal.
2. The row-level gate docs (`services/learning/qlib/ACTIVATION_CRITERIA.md`,
   `services/learning/trl/ACTIVATION_CRITERIA.md`, `services/registry/experiments/WANDB_ACTIVATION.md`,
   and `services/learning/rl/PATH_DEFINITION.md`) carry older document owners/reviewers that do not
   by themselves answer the current parent acceptance requirement of "activation owner." The parent
   task should resolve that explicitly.

---

## 6. Reviewer Handoff

Recommended reviewer focus for `Claude`:

1. Confirm this packet stays inside sidecar scope and does not over-claim runtime integration.
2. Confirm the row matrix accurately distinguishes "criteria locked" from "execution path still
   missing" for each deferred framework.
3. Decide whether the existing Qlib/FinRL package pins should be absorbed into the parent closeout
   narrative or treated as incidental pre-work until adapters/smokes exist.
4. Decide where the parent task should materialize activation ownership: checklist row, support
   matrix, or a dedicated per-row activation table.
5. Confirm the W&B blocking note is accurate given the current MLflow-first adapter implementation.

If approved, this sidecar packet can be handed back to the parent owner for absorption into the
main `BP5-OSS-004` execution lane at their discretion.

---

## 7. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified
- No runtime, registry, or governance implementation was changed
- No checklist row status was edited by this sidecar
- The only artifact created by this slice is this acceptance packet

# BP5-OSS-004 Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `BP5-OSS-004-SIDECAR-REVIEW`
**Helper parent:** `BP5-OSS-004` — Define the executable activation path for deferred Qlib, TRL, and RL stack rows
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Reviewer:** `Codex`
**Date:** `2026-04-16`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, registry semantics, or OSS checklist truth. It provides a compact review surface
> for `BP5-OSS-004` — the parent task is already `done` per `ai-task-archive/tasks/BP5-OSS-004.json`.

---

## 1. Purpose

This packet gives `Codex` (and any future reviewer) a structured, single-document review surface
for `BP5-OSS-004`. Since the parent task has already been closed and archived, this packet serves
as a post-closeout evidence record that:

1. confirms the parent acceptance criteria were met at the time of closure
2. verifies the artifacts that were produced are present and internally consistent
3. summarizes the checklist-state changes that resulted from the task
4. enumerates residual follow-on items and their owners

---

## 2. Parent Task Status

| Field | Value |
|---|---|
| Task ID | `BP5-OSS-004` |
| Title | Define the executable activation path for deferred Qlib, TRL, and RL stack rows |
| Terminal status | `done` |
| Archived at | `2026-04-16T01:25:45Z` |
| Delivery commit | `5166c581c1a35496cca6a764043dfd273cdcf4e9` |
| Commit subject | `BP5-OSS-004: mark activation map done after Codex review approval` |
| Commit author | Codex |
| Dependencies satisfied | `BP5-SVC-012` (done), `BP5-OSS-003` (done) |

The parent task went through two rounds of Codex review before reaching `review_approved`:

- **Round 1:** Codex requested reconciliation of repo truth (RLlib stub and EXPERIMENT_BACKEND config stub vs. doc text that still described them as future work).
- **Round 2:** Codex requested correction of stale RLlib status language (`criteria-defined` → `version-pinned`) in three locations.
- **Round 3 (final):** Codex approved: "DEFERRED_OSS_ACTIVATION_MAP.md now matches the landed RLlib package/container stub and W&B backend-selector stub, the checklist rows are reconciled to repo truth, and each deferred row names entry criteria, evidence requirements, activation owner, and an executable next step."

---

## 3. Artifact Verification

### 3.1 Primary artifact

| Artifact | Path | Status |
|---|---|---|
| Deferred OSS Activation Map | `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Present, `Status: Done — review approved by Codex 2026-04-16` |

The map covers all five deferred rows (Qlib, TRL, FinRL, RLlib, W&B) with:
- one unambiguous current status per row
- reconciled package-pin facts vs. gate-doc text
- explicit activation owner for follow-on adapter + smoke work
- the one concrete executable next step per row

### 3.2 Secondary artifacts landed by BP5-OSS-004

| Artifact | Path | Purpose |
|---|---|---|
| RLlib/Tune requirements | `services/research/rllib/requirements.txt` | Closes "where is the RLlib pin?" ambiguity; pins `ray[rllib]>=2.9.0,<3.0.0` and `ray[tune]>=2.9.0,<3.0.0` |
| RLlib Dockerfile stub | `services/research/rllib/Dockerfile` | Stub with explicit comment that no adapter is wired; matches Qlib/FinRL container pattern |
| W&B backend selector stub | `services/registry/experiments/config.py` | Adds `EXPERIMENT_BACKEND` env-var (default `"mlflow"`); `"wandb"` not wired; makes blocking condition concrete rather than hypothetical |

### 3.3 Supporting sidecar artifact

| Artifact | Path | Notes |
|---|---|---|
| Codex acceptance packet | `support/sidecars/BP5-OSS-004/BP5-OSS-004-SIDECAR-ACCEPTANCE.md` | Prepared by Codex as a parallel sidecar (`BP5-OSS-004-SIDECAR-ACCEPTANCE`); provides row-level evidence snapshot, AC checklist, and dependency map; `Status: done — review_approved by Claude (2026-04-16)` |

---

## 4. Acceptance Criteria Verification

### AC-1: Deferred rows no longer sit in ambiguous criteria-only state without an executable next step

| Row | Ambiguity before BP5-OSS-004 | Resolution in DEFERRED_OSS_ACTIVATION_MAP.md | AC-1 status |
|---|---|---|---|
| `Qlib` | Gate doc `§7 Next Steps` still said "Pin Qlib version" after pin had already landed; no adapter owner was explicit | Pin reconciled as done; governed data-handler adapter + LightGBM smoke explicitly the next step; Qwen named as adapter/smoke owner | RESOLVED |
| `TRL` | No package pin; no pair-construction path; document-first only | Executable next steps defined: pin `trl>=0.8.0`, build pair-construction pipeline, run minimal DPO smoke; activation owner is Qwen, blocked on FB-002/imitation prerequisites | RESOLVED |
| `FinRL` | Package pin existed but gate doc `§6` had not acknowledged it; no governed output mapping or smoke path named | Pin reconciled as done; RL-path approval gate made concrete; Copilot named as activation owner once RL gate is passed | RESOLVED |
| `RLlib` | No repo-local pin; only implied inside LP-005 prose | Pin now landed; Dockerfile stub present; RL path approval gate same as FinRL; `RL_PATH_APPROVAL_GATE.md` named as immediate executable action | RESOLVED |
| `W&B` | `EXPERIMENT_BACKEND` selector described as future work but then landed; gate doc and adapter code were inconsistent | Selector stub confirmed in `config.py`; `"wandb"` explicitly not wired; blocking conditions itemized concretely; MLflow 30-day history gate and adapter-generalization next steps named | RESOLVED |

**AC-1 verdict: MET**

### AC-2: Each deferred row names its entry criteria, evidence requirements, and activation owner

| Row | Entry criteria named | Evidence requirements named | Activation owner explicit | AC-2 status |
|---|---|---|---|---|
| `Qlib` | Yes — `ACTIVATION_CRITERIA.md` + activation map §1 | Yes — adapter, smoke, registry projection, integration test enumerated | Yes — Qwen (adapter + smoke); Claude accountable for this map | MET |
| `TRL` | Yes — `ACTIVATION_CRITERIA.md` + activation map §2 | Yes — package pin, pair-construction pipeline, DPO smoke, dependency check, runtime data gate | Yes — Qwen (gate doc owner; blocked on FB-002/imitation prerequisites) | MET |
| `FinRL` | Yes — `PATH_DEFINITION.md §1` + activation map §3 | Yes — RL path approval gate items enumerated; package pin reconciled; governed adapter and smoke missing | Yes — Copilot (LP-005/RL path owner) | MET |
| `RLlib` | Yes — `PATH_DEFINITION.md` + activation map §4 | Yes — version pin done; environment contract, governed train/eval loop, RL path approval gate items listed | Yes — Copilot (same RL path lane as FinRL) | MET |
| `W&B` | Yes — `WANDB_ACTIVATION.md` + activation map §5 | Yes — seven concrete blocking conditions enumerated with individual status | Yes — Qwen (gate doc owner); no implementation until MLflow 30-day history met | MET |

**AC-2 verdict: MET**

---

## 5. Checklist State Changes

`BP5-OSS-004` changed one checklist row status:

| Row | Before BP5-OSS-004 | After BP5-OSS-004 |
|---|---|---|
| `RLlib` | `criteria-defined` | `version-pinned` |

All other rows remain `criteria-defined` (Qlib, TRL, FinRL, W&B). This is correct and honest: packaging exists for some rows, but no governed adapter or smoke path has been built.

---

## 6. Residual Follow-On Items

These items are explicitly out of scope for BP5-OSS-004 but have been made unambiguous by it.
Each has a named owner and a concrete next step.

| Row | Immediate next action | Owner | Blocker / gate |
|---|---|---|---|
| `Qlib` | Build governed data-handler adapter in `services/research/qlib/adapter/`; run one LightGBM smoke emitting canonical `artifact_state=draft` | Qwen | No hard blocker; requires RS-003 baseline StrategySpec candidate in registry |
| `TRL` | Pin `trl>=0.8.0` in `services/learning/trl/requirements.txt`; build minimal pair-construction pipeline; run DPO smoke against synthetic pairs | Qwen | Runtime data gate: ≥200 FB-002 events, ≥100 preference pairs |
| `FinRL` | Create `services/learning/rl/RL_PATH_APPROVAL_GATE.md` naming exact evidence and approver | Copilot | Qlib supervised alpha must reach `artifact_state=approved` and show stable Sharpe for ≥3 months |
| `RLlib` | Same `RL_PATH_APPROVAL_GATE.md` as FinRL; add environment contract as repo-local artifact | Copilot | Same RL path approval gate as FinRL |
| `W&B` | Generalize `RegistryExperimentAdapter` for configurable backends; migrate canonical `artifact_state` / `deployment_stage` into the experiment bridge | Qwen | MLflow ≥30 days operational history; no documented operator preference yet |

---

## 7. Consistency Checks

The following cross-file consistency items were verified by reading the three relevant files
(`OSS_INTEGRATION_CHECKLIST.md`, `DEFERRED_OSS_ACTIVATION_MAP.md`, `BP5-OSS-004-SIDECAR-ACCEPTANCE.md`):

| Check | Result |
|---|---|
| RLlib status is `version-pinned` in OSS_INTEGRATION_CHECKLIST.md | Yes — line 41 reflects `version-pinned` |
| RLlib status is `version-pinned` in DEFERRED_OSS_ACTIVATION_MAP.md summary table | Yes — row status column and section heading both say `version-pinned` |
| DEFERRED_OSS_ACTIVATION_MAP.md closing meta-claim does not falsely say "all five rows remain criteria-defined" | Yes — §7 says "BP5-OSS-004 did advance the RLlib checklist row from `criteria-defined` to `version-pinned`" |
| `EXPERIMENT_BACKEND` selector in `services/registry/experiments/config.py` is acknowledged accurately | Yes — map §5 confirms the stub is landed and `"wandb"` is not wired |
| All five rows have both an activation owner and a concrete next step | Yes — confirmed per AC-2 table above |
| No canonical L1 document was modified by the sidecar acceptance packet | Yes — SIDECAR-ACCEPTANCE.md §7 confirms this |

---

## 8. Reviewer Handoff

Recommended Codex review focus:

1. Confirm this packet correctly reflects the terminal state of BP5-OSS-004 as archived in
   `ai-task-archive/tasks/BP5-OSS-004.json`.
2. Confirm the AC-1 and AC-2 verdicts match the Codex review-approval message from
   `2026-04-16T01:24:37Z`.
3. Confirm the checklist-state change (RLlib `criteria-defined` → `version-pinned`) is accurately
   reported and not over- or under-claimed.
4. Confirm the residual follow-on table does not introduce new scope or ownership claims beyond
   what the activation map already states.
5. Note whether any of the residual follow-on items require a new execution task to be materialized
   in `ai-status.json` (e.g., a Qlib adapter task for Qwen, or an RL path approval gate task for Copilot).

If approved, this review packet closes the sidecar slice. The parent owner (Claude) should then
finalize `BP5-OSS-004-SIDECAR-REVIEW` to `done`.

---

## 9. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified
- No runtime, registry, or governance implementation was changed
- No checklist row status was edited by this sidecar
- No new execution tasks were materialized by this sidecar (that decision is left to the reviewer)
- The only artifact created by this slice is this review packet

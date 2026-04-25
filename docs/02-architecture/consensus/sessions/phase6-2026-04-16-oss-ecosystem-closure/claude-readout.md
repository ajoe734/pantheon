# Claude Readout

Session: `phase6-2026-04-16-oss-ecosystem-closure`
Round: 0
Date: 2026-04-16

---

## Lane

- Agent: Claude
- Capability focus: Facilitate consensus, synthesize cited disagreements, prepare the human gate packet.

---

## Canonical Sources Read

- L0: `OSS_INTEGRATION_CHECKLIST.md` (2026-04-16) — per-row status and evidence requirements
- L0: `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` (BP5-OSS-004, approved 2026-04-16) — row-by-row executable next steps and blocking conditions
- L1: `OSS_INTEGRATION_AUDIT.md` (2026-04-02) — upstream-vs-build classification rules
- L1: `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/full-blueprint-gap-inventory.md` — wave 5 scoping and OSS gap bucket definition
- L1: `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/execution-materialization.md` — Phase 5 task wave definitions (BP5-OSS-001 through BP5-OSS-004)
- L2: Git log 2026-04-14 → 2026-04-16 — empirical evidence of which BP5 tasks actually closed

---

## Working Interpretation

### Architecture summary

Phase 5 closed all four BP5-OSS tasks and all BP5-CICD + BP5-GCP tasks. The current repo state per `OSS_INTEGRATION_CHECKLIST.md` is:

| Row | Current Status |
|---|---|
| OpenClaw | `governed` — source pinned, adapter in `integrations/openclaw/adapter/`, smoke passed 2026-04-16 |
| DSPy | `governed` — v2.4.5 pinned, adapter in `services/learning/dspy/`, smoke passed 2026-04-15 |
| imitation | `governed` — v1.0.1 pinned, adapter in `services/learning/imitation/`, smoke passed 2026-04-15 |
| MLflow | `governed` — v3.10.1 pinned, adapter in `services/registry/experiments/`, smoke passed 2026-04-15 |
| Qlib | `criteria-defined` — `pyqlib==0.9.1` pinned, Dockerfile exists, **no adapter, no smoke** |
| TRL | `criteria-defined` — **no package pin**, no adapter, no smoke; volume gate unmet |
| FinRL | `criteria-defined` — `finrl==0.3.6` pinned, Dockerfile exists, **RL path gate not met** |
| RLlib | `version-pinned` — `ray[rllib]>=2.9.0` pinned, Dockerfile stub, **RL path gate not met** |
| W&B | `criteria-defined` — no SDK pin, `EXPERIMENT_BACKEND` env stub only, **30-day MLflow history gate not met until ~2026-05-15** |

Phase 6 scope is therefore: convert the five remaining `criteria-defined` / `version-pinned` rows into honest execution work without violating their documented gate sequences.

### Delivery order

The gate dependencies create an enforced serial structure:

1. **Qlib first** — most ready row; package pinned, control-plane consumer exists (`QlibTool` in `skills.yaml`), blocking condition is only the adapter + smoke test. Qlib plateau is also the *prerequisite* for the RL path gate, making it the root dependency for FinRL and RLlib.
2. **TRL prep in parallel with Qlib** — TRL can start with package pinning + pair-construction pipeline stubbing now even though the runtime volume gate (≥200 FB-002 events, ≥100 pairs) cannot be pre-staged.
3. **W&B adapter generalization** — blocked until ~2026-05-15 (30-day MLflow operational history from 2026-04-15 governed date). `RegistryExperimentAdapter` generalization + `artifact_state`/`deployment_stage` migration should be scheduled for that window, not now.
4. **RL path approval packet** (Copilot) — can begin assembly in parallel with Qlib execution, but cannot pass until Qlib supervised alpha plateaus and all five criteria in `PATH_DEFINITION.md §1` are met.
5. **FinRL + RLlib governed adapters** — blocked behind the RL path approval gate; earliest realistic date is well after Qlib reaches `artifact_state=approved` and stable Sharpe.

### Ownership boundaries

Per `DEFERRED_OSS_ACTIVATION_MAP.md §Activation Readiness Summary`:

- **Qwen** owns Qlib adapter + smoke, TRL pin + pair-construction pipeline, W&B adapter generalization
- **Copilot** owns RL path approval packet, FinRL governed adapter, RLlib/Tune governed adapter
- **Claude** facilitates, reviews, and prepares the consensus packet
- **Codex** may provide repo-grounding support but is not the primary activation owner for any of the five rows

---

## Risks / Contradictions

### Risk 1 — Qlib is an undeclared critical path root

`DEFERRED_OSS_ACTIVATION_MAP.md §FinRL` explicitly states: "Qlib supervised alpha must reach `artifact_state=approved` and show stable Sharpe for ≥3 months" before the RL path gate opens. If Phase 6 treats Qlib as one parallel item among five, FinRL and RLlib will remain blocked indefinitely. Qlib must be dispatched first and tracked as the dependency root.

*Cited evidence*: `DEFERRED_OSS_ACTIVATION_MAP.md §3 FinRL`, "Activation prerequisite chain", item 1; `services/learning/rl/PATH_DEFINITION.md §1` (referenced in gate doc).

### Risk 2 — TRL volume gate is a live-system constraint, not a planning artifact

The ≥200 FB-002 events / ≥100 preference pairs threshold cannot be pre-staged in code. Planning that treats TRL as "blocked until code is ready" will miss that even after TRL code is complete, activation cannot proceed until runtime accumulation occurs. A monitoring / tracking path for the FB-002 event counter should be part of the TRL task scope.

*Cited evidence*: `DEFERRED_OSS_ACTIVATION_MAP.md §2 TRL`, "Activation prerequisite chain", item 2; `services/learning/trl/ACTIVATION_CRITERIA.md §4` (referenced).

### Risk 3 — W&B adapter generalization requires `artifact_state` migration before W&B itself

`DEFERRED_OSS_ACTIVATION_MAP.md §5 W&B` notes that `RegistryExperimentAdapter` still uses `lifecycle_state`/`paper`/`live` aliases rather than the canonical `artifact_state` + derived `deployment_stage` split. This migration is a prerequisite for W&B but also creates risk of drift in the MLflow-first adapter path. The migration task should be spec'd independently so it does not accidentally break the governed MLflow path.

*Cited evidence*: `DEFERRED_OSS_ACTIVATION_MAP.md §5 W&B`, "Blocking condition: `RegistryExperimentAdapter` generalized to accept non-MLflow backends — Not done"; same §5 "Concrete blocking conditions", `artifact_state`/`deployment_stage` migration row.

### Risk 4 — BP5-LUV-010 sidecar is still in review (residual)

Git log as of 2026-04-16 shows `BP5-LUV-010-SIDECAR-REVIEW` as the latest commit, which adds the review packet evidence but the corresponding acceptance commit is not yet present. This is a residual Phase 5 closure item that Phase 6 should formally close or carry forward as a known open item.

*Cited evidence*: Git commit `440c8f8 BP5-LUV-010-SIDECAR-REVIEW: finalize review packet and close sidecar`; no corresponding `BP5-LUV-010-SIDECAR-ACCEPTANCE` commit in log.

---

## Suggested Task Slices

These are facilitator-lane suggestions; Codex holds the baton and seeds `starter-draft.md`.

- **P6-OSS-001**: Qlib data-handler adapter + LightGBM smoke test
  - Owner: Qwen
  - Scope: implement `services/research/qlib/adapter/`, run single LightGBM model on 10-ticker 1-year stub, emit `artifact_state=draft` registry envelope
  - Unblocked immediately; dispatch first

- **P6-OSS-002**: TRL package pin + pair-construction pipeline stub
  - Owner: Qwen
  - Scope: pin `trl>=0.8.0` in `services/learning/trl/requirements.txt`, stub pair-construction pipeline, add FB-002 event-counter monitoring path; defer DPO smoke until volume gate met
  - Can start in parallel with P6-OSS-001

- **P6-OSS-003**: W&B adapter generalization + `artifact_state`/`deployment_stage` migration
  - Owner: Qwen
  - Scope: generalize `RegistryExperimentAdapter` for configurable backends, migrate canonical state split, then pin `wandb>=0.16.0` and implement W&B backend
  - **Schedule after 2026-05-15** (MLflow 30-day history gate)

- **P6-OSS-004**: RL path approval packet assembly
  - Owner: Copilot
  - Scope: assemble evidence package against `services/learning/rl/RL_PATH_APPROVAL_GATE.md`, request human or governance approval checkpoint
  - Start parallel with P6-OSS-001; cannot pass gate until Qlib plateaus

- **P6-OSS-005**: FinRL + RLlib/Tune governed adapters
  - Owner: Copilot
  - Scope: governed single-agent policy-output adapter for FinRL, governed train/eval loop + environment contract for RLlib/Tune
  - **Gated**: dispatch only after P6-OSS-004 approval gate passes

- **P6-LUV-CLOSURE**: Close BP5-LUV-010 residual sidecar
  - Scope: confirm acceptance packet or note open state; carry as explicit closure item in consensus packet

---

## Citations

- [OSS_INTEGRATION_CHECKLIST.md §Component Inventory] current per-row status values for all nine components
- [DEFERRED_OSS_ACTIVATION_MAP.md §Row Status at BP5-OSS-004 Execution] table of governed-adapter and smoke-path present/missing
- [DEFERRED_OSS_ACTIVATION_MAP.md §Activation Readiness Summary] single-blocking-gate column and first-executable-proof column
- [DEFERRED_OSS_ACTIVATION_MAP.md §3 FinRL, activation prerequisite chain item 1] Qlib plateau as RL gate prerequisite
- [DEFERRED_OSS_ACTIVATION_MAP.md §5 W&B, Concrete blocking conditions] MLflow 30-day history and adapter generalization
- [phase5 execution-materialization.md §Wave 4] original BP5-OSS-* task definitions that this phase extends
- [Git log 2026-04-16] commit `440c8f8` as most recent, confirming BP5-LUV-010-SIDECAR-REVIEW open; no ACCEPTANCE commit present

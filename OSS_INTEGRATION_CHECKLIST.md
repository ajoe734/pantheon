# OSS Integration Checklist

Last updated: 2026-04-15
Status: execution checklist for upstream OSS components referenced by the OpenClaw target architecture

## Purpose

This file turns named upstream OSS components into a concrete integration checklist.

For each component, we track whether we have:

1. selected the upstream project
2. pinned a version or commit
3. added the dependency or repo integration path
4. built a local adapter
5. defined governed I/O boundaries
6. run a smoke test

Do not treat a component as integrated just because we wrote contracts around it.

## Checklist Status Codes

- `not-started`
- `source-selected`
- `version-pinned`
- `dependency-added`
- `adapter-started`
- `criteria-defined` (deferred framework with explicit entry criteria documented)
- `smoke-tested`
- `governed`

## Component Inventory

| Component | Upstream Type | Current Status | What still needs to happen |
|---|---|---|---|
| `OpenClaw` | upstream repo/runtime | `governed` | Source pin is locked to `openclaw/openclaw` tag `v2026.4.7` / commit `5050017543011b61df67744ebc6368d889c25a95`, the runtime artifact is pinned to `ghcr.io/openclaw/openclaw:2026.4.7`, the governed adapter seam is locked under `integrations/openclaw/`, and `scripts/openclaw-smoke-test.sh` now executes a real baseline against the pinned tag / image / normalization fixture. Next: implement the `openclaw-gateway-adapter`, add the real runtime dependency path, and prove end-to-end workflow execution in `BP5-OSS-002`. |
| `DSPy` | Python package/framework | `smoke-tested` | v2.4.5 pinned; full adapter with governed I/O; smoke test passes; add `integration.md` and `governance.md` per canonical checklist format (see integrations/oss-002/regrade_report.md) |
| `TRL` | Python package/framework | `criteria-defined` | activation criteria documented in `services/learning/trl/ACTIVATION_CRITERIA.md`; entry criteria require ≥200 FB-002 events, ≥100 preference pairs, active imitation baseline, and downstream consumer ready; TRL artifacts remain non-executable governed models (`draft` → `candidate` → `approved`), not `paper/live` execution states; next: pin version, build pair-construction pipeline, smoke test DPO training |
| `Qlib` | Python package/framework | `criteria-defined` | activation criteria documented in `services/learning/qlib/ACTIVATION_CRITERIA.md`; entry criteria require baseline StrategySpec, 2+ years data, supervised-learning-appropriate problem; LightGBM-first workflow defined; registry target shape now uses canonical `artifact_state` plus deployment staging; next: pin version, build data pipeline adapter, smoke test single model |
| `FinRL` | upstream repo/package | `criteria-defined` | deferred until RL path is justified; entry criteria documented in `services/learning/rl/PATH_DEFINITION.md` §1 (supervised alpha exhausted, sequential decision dependency, 2+ years intraday data); next: verify RL entry criteria met, then package and map governed policy outputs |
| `RLlib` | Python package/framework | `criteria-defined` | deferred until RL path is approved; entry criteria and full workflow documented in `services/learning/rl/PATH_DEFINITION.md`; next: approve RL path, pin version, prove governed training/eval loop |
| `Ray Tune` | Python package/framework | `version-pinned` | adapter path still missing; define governed search outputs and smoke test integration with selected learning path |
| `imitation` | Python package/framework | `smoke-tested` | v1.0.1 pinned; full BC adapter with governed trajectory filtering; smoke test passes; add `integration.md` and `governance.md` per canonical checklist format (see integrations/oss-002/regrade_report.md) |
| `MLflow` | backend/service/package | `smoke-tested` | v3.10.1 pinned (updated from 2.11.0); full registry↔experiment adapter; smoke test passes; add `integration.md` and `governance.md` per canonical checklist format (see integrations/oss-002/regrade_report.md) |
| `W&B` | backend/service/package | `criteria-defined` | activation criteria documented in `services/registry/experiments/WANDB_ACTIVATION.md`; W&B is an optional alternative backend to MLflow requiring stable MLflow integration first, explicit operator need, adapter generalization beyond the current MLflow-first `RegistryExperimentAdapter`, and canonical `artifact_state` / `deployment_stage` support; next: pin SDK version, generalize adapter surface, implement W&B backend, and prove metadata equivalence |

## Required Evidence Per Component

Each upstream integration should eventually produce these repo-local artifacts:

1. `integration.md`
   - selected upstream project
   - pinned version/commit
   - packaging/runtime notes

2. `adapter/`
   - local code that maps upstream inputs/outputs into governed repo contracts

3. `smoke_test.md` or executable smoke test
   - minimal proof the integration path works

4. `governance.md`
   - how promotion, permissions, and rollback apply to that upstream component

## Immediate Priorities

Priority order for real upstream integration work:

1. `OpenClaw`
2. `DSPy`
3. `MLflow or W&B`
4. `Qlib`
5. `imitation`
6. `TRL`
7. `FinRL / RLlib / Tune`

Why this order:

- `OpenClaw` affects orchestration semantics everywhere
- `DSPy` is the first intended persona optimization path already on the active board
- experiment/registry backend should exist before learning integrations fan out
- RL stack should stay last until governance and registry paths are stable

## Working Rule

When adding a new task for any named OSS component, include these acceptance points unless there is a good reason not to:

- upstream source selected
- version pinned
- dependency or repo path added
- local adapter boundary defined
- smoke test described or implemented

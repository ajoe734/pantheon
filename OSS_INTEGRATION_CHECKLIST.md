# OSS Integration Checklist

Last updated: 2026-04-02
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
- `smoke-tested`
- `governed`

## Component Inventory

| Component | Upstream Type | Current Status | What still needs to happen |
|---|---|---|---|
| `OpenClaw` | upstream repo/runtime | `not-started` | pin the upstream repo reference, decide integration mode, map its workflow/runtime outputs into local `StrategySpec` and permission contracts |
| `DSPy` | Python package/framework | `not-started` | add dependency, pin version, build persona optimization adapter, define prompt/artifact handoff to registry, run smoke test |
| `TRL` | Python package/framework | `not-started` | defer until governed preference loop exists, then pin version, wrap preference-learning I/O, and smoke test a non-live path |
| `Qlib` | Python package/framework | `not-started` | add package or worker image, pin version, define alpha-output adapter, run experiment smoke test, link outputs to registry |
| `FinRL` | upstream repo/package | `not-started` | defer until RL path is justified, then package it separately and map governed policy outputs into registry artifacts |
| `RLlib` | Python package/framework | `not-started` | only after RL path is approved; pin version and prove governed training/eval loop works |
| `Ray Tune` | Python package/framework | `version-pinned` | adapter path still missing; define governed search outputs and smoke test integration with selected learning path |
| `imitation` | Python package/framework | `not-started` | pin version, map `FB-001` trajectory schema into imitation dataset inputs, run BC smoke test, route outputs into registry |
| `MLflow` | backend/service/package | `source-selected` | Selected MLflow 2.11.0; Next: select deployment mode (GCP Managed vs GKE), define registry-mapping rules |
| `W&B` | backend/service/package | `not-started` | optional alternative to MLflow; define registry metadata mapping, alias strategy, and smoke test artifact promotion metadata |

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

# 2026-04-25 Deferred Prep Execution Packet

## Purpose

This packet materializes the four explicitly deferred lanes into a truthful
"deferred-prep" execution wave.

These tasks are intentionally **not** production-activation tasks.
They authorize repo-local engineering work only:

- scaffolding,
- adapter boundaries,
- config/schema plumbing,
- offline smoke tests,
- feature-flagged backend selection,
- and activation packets / runbooks.

They do **not** authorize:

- reopening the RL gate,
- switching any default production backend,
- claiming runtime verification or execution proof,
- or declaring canary/live activation complete.

## Materialized Tasks

### `APP-003-FINRL-DEFERRED-PREP-001`

Goal:
- build the governed single-agent RL prep lane for FinRL behind a deferred gate

Allowed work:
- adapter scaffold
- policy I/O contract
- offline toy workflow
- artifact draft/candidate pipeline
- unit + smoke tests
- feature-flagged config path

Not allowed:
- production activation
- canary/live lane claim
- RL gate reopen

### `APP-003-RLLIB-DEFERRED-PREP-001`

Goal:
- scaffold the governed RLlib train/eval backend behind the deferred RL gate

Allowed work:
- train/eval abstraction
- rollout/result schema
- local smoke scaffold
- adapter boundary
- non-default backend wiring

Not allowed:
- governed production train loop
- reopening the RL gate
- claiming RLlib as an active production backend

### `APP-003-RAYTUNE-DEFERRED-PREP-001`

Goal:
- scaffold the deferred Ray Tune search-output path behind the RL gate

Allowed work:
- search-space schema
- result adapter
- offline tuning artifact format
- local smoke fixture path

Not allowed:
- production optimization path
- activated tuning lane claim

### `APP-003-WANDB-DEFERRED-PREP-001`

Goal:
- scaffold the optional W&B backend behind the explicit deferred gate

Allowed work:
- backend adapter scaffold
- MLflow/W&B abstraction layer
- feature-flagged optional backend selector
- local/offline dry-run support
- non-default CI / smoke coverage

Not allowed:
- supported production backend claim
- default experiment-backend switch
- activation closeout

## Canonical Boundary

The deferred-prep tasks must preserve the following truth:

- `FinRL` remains `criteria-defined`
- `RLlib` remains `version-pinned`
- `Ray Tune` remains `version-pinned`
- `W&B` remains `criteria-defined`

See:

- `OSS_INTEGRATION_CHECKLIST.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`

## Reviewer Rule

Review approval for these tasks may confirm only:

1. repo-local prep/scaffold work landed,
2. activation boundaries remain truthful,
3. no default production path was silently changed.

Review approval must **not** be interpreted as production activation.

## 2026-04-25 Execution Notes

### `APP-003-FINRL-DEFERRED-PREP-001`

Repo-local deferred-prep scaffold now lands in `services/research/finrl/` with:

- governed input adapter and deferred-prep gate
- offline `rl_policy` draft workflow plus candidate packet scaffold
- worker entrypoint, sample dataset, smoke test, and unit coverage

This evidence is intentionally prep-only:

- `artifact_state` remains `draft`
- `deployment_summary.current_stage` remains `none`
- activation boundary remains `does_not_reopen_rl_gate`

### `APP-003-RLLIB-DEFERRED-PREP-001`

Repo-local deferred-prep scaffold now lands in `services/research/rllib/` with:

- governed train/eval adapter and deferred-prep gate
- rollout/result schema plus offline `rl_policy` draft workflow
- worker entrypoint, sample dataset, smoke test, and unit coverage

This evidence is intentionally prep-only:

- `artifact_state` remains `draft`
- `deployment_summary.current_stage` remains `none`
- `RLlib` remains `version-pinned`
- activation boundary remains `does_not_reopen_rl_gate`

### `APP-003-RAYTUNE-DEFERRED-PREP-001`

Repo-local deferred-prep scaffold now lands in `services/research/rllib/` with:

- governed Ray Tune search-space schema and result adapter
- offline `optimizer_result` draft workflow plus projected candidate outputs
- worker entrypoint, smoke test, and unit coverage for the search-output path

This evidence is intentionally prep-only:

- `artifact_state` remains `draft`
- `deployment_summary.current_stage` remains `none`
- `Ray Tune` remains `version-pinned`
- activation boundary remains `does_not_reopen_rl_gate`

### `APP-003-WANDB-DEFERRED-PREP-001`

Repo-local deferred-prep scaffold now lands in `services/registry/experiments/` with:

- generalized `ExperimentBackend` / `RegistryExperimentAdapter` wiring for non-default backends
- feature-flagged `EXPERIMENT_BACKEND=wandb` selector path guarded by `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`
- offline `OfflineWandbPrepBackend` dry-run scaffold plus local smoke and unit coverage

This evidence is intentionally prep-only:

- `EXPERIMENT_BACKEND` default remains `"mlflow"`
- no `wandb` SDK pin is claimed
- no network/infrastructure readiness is claimed
- `W&B` remains `criteria-defined`
- activation boundary remains `does_not_activate_wandb_backend`

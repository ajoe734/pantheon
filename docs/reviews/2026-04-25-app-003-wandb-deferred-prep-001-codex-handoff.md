# APP-003-WANDB-DEFERRED-PREP-001 Review Handoff

**Task**: `APP-003-WANDB-DEFERRED-PREP-001`
**Owner**: `Codex`
**Reviewer**: `Codex2`
**Date**: `2026-04-25`

## Scope

This slice lands the repo-local **deferred-prep** W&B lane only. It does:

- generalize the experiment bridge to a pluggable backend surface
- add a feature-flagged `EXPERIMENT_BACKEND=wandb` prep path
- add an offline `OfflineWandbPrepBackend` scaffold plus unit and smoke coverage
- keep `MLflow` as the default backend and preserve W&B's deferred truth

It does **not**:

- pin the real `wandb` SDK
- claim network or infrastructure readiness for `api.wandb.ai`
- change the default experiment backend away from `mlflow`
- claim W&B production support or activation

## Code Surface

- `services/registry/experiments/adapter.py`
- `services/registry/experiments/config.py`
- `services/registry/experiments/smoke_test.py`
- `services/registry/experiments/test_adapter.py`
- `services/registry/experiments/README.md`

## Canonical Truth Updates

- `services/registry/experiments/WANDB_ACTIVATION.md`

This task keeps W&B:

- prep scaffold landed repo-locally
- still `criteria-defined`
- still non-default
- still blocked on MLflow history, operator preference, canonical state migration, SDK pin, and infrastructure readiness

## Verification

Executed locally:

1. `python3 -m pytest services/registry/experiments/test_adapter.py -q`
   - Result: `7 passed`
2. `python3 services/registry/experiments/smoke_test.py`
   - Result: MLflow in-memory smoke passed
3. `python3 services/registry/experiments/smoke_test.py --backend wandb`
   - Result: offline W&B prep smoke passed; `promoted_metadata` shape preserved and backend ref switched to `wandb`

## Reviewer Focus

Please verify:

1. `EXPERIMENT_BACKEND` still defaults to `mlflow`
2. `wandb` remains gated behind `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1` and offline-only mode
3. the W&B path preserves registry-facing `ExperimentSyncResult` / `promoted_metadata` shape
4. docs keep W&B explicitly deferred and do not imply SDK, network, or activation readiness

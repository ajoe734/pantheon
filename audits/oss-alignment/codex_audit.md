# Codex OSS Alignment Audit

Scope:

- `REG-001`
- `FB-001`
- `LP-001`
- `LP-002`
- `REG-003`

## Summary

The local contract work remains useful, but none of these items should be mistaken for full upstream integration.

## Findings

### `REG-001`

- Classification: valid but only local wrapper/contract
- Why:
  - the registry contract is still useful as the governed local lifecycle boundary
  - but it is not yet connected to an upstream experiment backend such as `MLflow` or `W&B`
  - no integration smoke test exists yet

### `FB-001`

- Classification: valid but only local wrapper/contract
- Why:
  - feedback schemas are needed regardless of framework choice
  - but no actual storage/runtime path has been integrated yet
  - downstream adapters into `DSPy`, `imitation`, or `TRL` are still missing

### `LP-001`

- Classification: missing upstream integration step
- Why:
  - the current draft is a useful contract for how `DSPy` should be governed
  - but upstream `DSPy` source selection, version pinning, dependency addition, adapter code, and smoke test are still missing

### `LP-002`

- Classification: missing upstream integration step
- Why:
  - the task now correctly points at upstream `imitation`
  - but no package pin, adapter from `FB-001` trajectories, or BC smoke test exists yet

### `REG-003`

- Classification: valid local follow-up task
- Why:
  - rollback and lineage metadata are still local governance requirements
  - but when this task is implemented, it should also ensure compatibility with whichever experiment backend we actually choose

## Required Follow-up

1. `LP-001` needs an explicit upstream selection task or spike
2. `LP-002` needs an explicit upstream selection task or spike
3. `LP-003` should be treated as the first real experiment-backend integration milestone
4. `REG-001` and `FB-001` should continue to be described as governed local contracts until upstream backends are connected

## Recommended Task/Status Corrections

- do not mark `LP-001` or `LP-002` complete based on contracts alone
- treat `REG-001` as local-governance complete, but not backend-integrated
- tie future status updates to `OSS_INTEGRATION_CHECKLIST.md`


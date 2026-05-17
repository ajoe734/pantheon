# IMT-005 Behavior Cloning Baseline Contract

Status: IMT-005 research-plane contract

## Scope

`bc_trainer.py` provides a standalone CPU-only behavior cloning baseline for
the governed imitation dataset emitted by `dataset_builder.py` (IMT-003).

The trainer:

- accepts an IMT-003 dataset payload mapping, or a JSON file path containing
  that payload
- trains a deterministic linear softmax policy with batch gradient descent
- returns an in-memory `behavior_policy` artifact dict with a stable checksum
- records lineage back to the source dataset ref(s)
- does not write registry state, promote artifacts, deploy runtimes, or require
  GPU/torch/imitation/numpy

## Entry Point

```python
from bc_trainer import train

artifact = train(dataset_ref, model_config)
```

`dataset_ref` is the IMT-003 dataset payload or a JSON path. Required payload
fields:

- `dataset_id`
- `strategy_id`
- `source_dataset_refs`
- `sessions[].trajectory_id`
- `sessions[].actor_role`
- `sessions[].decision`
- `sessions[].target.strategy_id`
- `sessions[].target.promotion_state`
- `sessions[].steps[].observation`
- `sessions[].steps[].action`

`model_config` is optional. Supported fields:

- `version`
- `requested_by`
- `epochs`
- `learning_rate`
- `l2`
- `seed`
- `artifact_state` (`draft` or `candidate`)
- `storage_backend`
- `storage_path_template`

## Governance Boundary

The trainer repeats the IMT-003 governed training filters before training:

- actor role must be `operator` or `approver`
- decision must be `approve` or `edit`
- target strategy must match `dataset.strategy_id`
- target promotion state must be `candidate` or `paper`

If no governed transitions remain, training fails. The artifact keeps
`governance.research_only=true` and `governance.direct_live_influence=false`.

## Artifact Shape

The returned artifact uses `artifact_type="behavior_policy"` and includes:

- `checksum`: `sha256:<hex>` over the artifact excluding the checksum field
- `lineage.source_dataset_ref`: the IMT-003 `dataset_id`
- `lineage.source_dataset_refs`: the dataset id plus upstream
  `source_dataset_refs`
- `policy`: linear softmax weights, bias, observation dimension, and action
  labels
- `training`: CPU device marker, loss history, final loss, accuracy, and
  config parameters
- `registry_hints`: behavior policy metadata for later governed writeback

The artifact is registry-ready data, not a registry write. Downstream
evaluation, approval, deployment planning, and runtime binding gates still own
promotion and execution.

## Verification

Focused verification:

```bash
pytest -q services/research/imitation/test_bc_trainer.py
```

The deterministic synthetic test builds a 50-step, 4-action IMT-003 dataset,
trains the BC baseline, asserts loss decreases over epochs, verifies the
checksum, and confirms lineage references the source dataset refs.

## Closeout Evidence

Reviewer approval: Claude, 2026-05-17.

Owner finalization verification:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q services/research/imitation/test_bc_trainer.py
```

Result: 3 passed.

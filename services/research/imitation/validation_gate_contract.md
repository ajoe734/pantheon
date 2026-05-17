# IMT-007 Policy Validation Gate Contract

Status: IMT-007 research-plane contract

## Scope

`policy_validation_gate.py` is a standalone validation gate that checks a
`behavior_policy` artifact before it is submitted to the registry or governance
pipeline.  It has no registry, runtime, trainer, or eval-metrics dependencies.

## Entry Point

```python
from policy_validation_gate import validate

result = validate(behavior_policy_ref, threshold=0.6)
# result = {"passed": bool, "rejection_reasons": [str, ...]}
```

`behavior_policy_ref` is the artifact dict produced by `bc_trainer.train()`,
optionally augmented with an `eval_result` sub-dict from
`eval_metrics.evaluate()`.  All checks are applied together; all failures are
returned in a single `ValidationResult`.

`threshold` is the minimum `action_match_rate` required (default 0.6).

## Validation Checks

The gate applies four checks in order.  Failures from all checks are collected
before returning.

### 1. Metadata completeness

Three metadata fields are required:

| Field | Resolved from |
|---|---|
| `producer_run_id` | direct key or `training.run_id` |
| `dataset_ref` | direct key or `lineage.source_dataset_ref` |
| `training_config` | direct key or non-empty `training` mapping |

A single rejection reason lists all missing fields together.

### 2. Checksum integrity

The gate recomputes `sha256:<hex>` over the artifact (excluding the `checksum`
field itself, using stable JSON: `sort_keys=True`, no extra whitespace) and
compares it to the stored `checksum`.  A mismatch or absent checksum rejects
the artifact.

The checksum algorithm is compatible with `bc_trainer.artifact_checksum`.

### 3. IMT-006 eval-metric threshold

`action_match_rate` is resolved from the first of:

1. `eval_result.action_match_rate`
2. `evaluation.action_match_rate`
3. `metrics.action_match_rate`
4. top-level `action_match_rate`

If absent the artifact is rejected with a message directing the caller to run
`eval_metrics.evaluate()` first.  If present but below `threshold` the artifact
is rejected with the observed rate and threshold in the message.

### 4. Forbidden trigger words

All string values in the artifact are scanned recursively (keys are not
scanned).  The gate rejects if any string value contains one of the words
`deploy`, `canary`, or `live` as a whole word (case-insensitive).  This
prevents research-plane artifacts from embedding production-stage intent.

A valid `bc_trainer` artifact never triggers this check: `artifact_state` is
`draft` or `candidate`, governance booleans are non-string, and promotion
states are `candidate` or `paper`.

## Return Shape

```json
{
  "passed": true,
  "rejection_reasons": []
}
```

```json
{
  "passed": false,
  "rejection_reasons": [
    "metadata missing required field(s): producer_run_id, dataset_ref",
    "action_match_rate=0.4500 is below threshold=0.6000"
  ]
}
```

`PolicyValidationError` (subclass of `ValueError`) is raised only when
`behavior_policy_ref` is not a mapping at all.

## Governance Boundary

The gate is read-only.  It does not write registry state, promote artifacts,
trigger deployments, or mutate the artifact.  It is the caller's
responsibility to pass a properly assembled artifact (including an attached
`eval_result`) and to act on the returned `passed` flag.

## Verification

Focused verification:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q services/research/imitation/test_policy_validation_gate.py
```

The test suite covers:

- 1 pass scenario: valid artifact with eval_result.action_match_rate=0.75
- 3 required fail scenarios:
  1. `action_match_rate` below threshold
  2. checksum mismatch after artifact tampering
  3. all three required metadata fields missing simultaneously
- additional coverage: trigger word rejection, absent eval_result, custom
  threshold boundary, structural guard on non-mapping input

## Closeout Evidence

Owner: Claude  
Task-ID: IMT-007  
Reviewer: Claude2

# EXP-001 Review: ExperimentTask / ExperimentRun schema

Reviewer: Claude
Owner: Codex
Date: 2026-05-16
Outcome: **APPROVED**

## Scope Verified

- `services/research/experiments/experiment_task.schema.json`
- `services/research/experiments/experiment_run.schema.json`
- `services/research/experiments/models.py`
- `services/research/experiments/__init__.py`
- `services/research/experiments/test_models.py`
- `services/research/experiments/README.md`
- `support/evidence/EXP-001/README.md`
- Commit: `1dc686c6` (7 files, 1168 insertions)

## Verification

```
py_compile: PASSED
pytest services/research/experiments/test_models.py -q: 12 passed
```

## Findings

### JSON Schemas

Both schemas are well-formed Draft-7 and validated via `Draft7Validator.check_schema`. Key design points confirmed:

- `ExperimentTask`: `backend_id` is required in schema (nullable), with an `allOf` conditional that promotes it to non-null string once status leaves `queued`/`selecting_backend`. `additionalProperties: false` is set.
- `ExperimentRun`: `runtime_env` enum is `dev|sandbox|research` only — execution-plane values absent. Conditional `allOf` rules enforce `started_at` for running, `started_at + finished_at + output_manifest_ref` for completed, and `failure_reason` for failed. `additionalProperties: false` is set.

### Python Models

Frozen dataclasses with dual validation: Python-level field checks + JSON schema validation in `__post_init__`. No mutation after construction.

Invariants enforced:
- `backend_id` required once task exits backend selection (`ready`, `running`, `completed`, `failed`, `cancelled`)
- Completed run requires `started_at`, `finished_at`, `output_manifest_ref`
- Failed run requires `failure_reason`
- Running run requires `started_at`
- Duplicate `artifact_refs` rejected
- Unknown top-level fields rejected via schema
- Runtime env restricted to research-scoped values

### Lineage Cross-Validation

`validate_experiment_run_against_task` enforces that a run preserves `task_id`, `strategy_id`, `strategy_spec_version`, `dataset_version_id`, `code_version`, and `backend_id` from its parent task. This is the correct mechanism for downstream CandidateArtifact lineage verification.

### Scope Boundary

No adapter launch, no registry writeback, no promotion approval, no execution routing found anywhere in the module. The scope correctly stops at the research plane.

## No Blocking Findings

Implementation is correct, well-tested, and scoped appropriately. Returning to Codex for finalization.

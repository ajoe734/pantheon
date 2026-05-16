# EXP-001 Evidence: ExperimentTask / ExperimentRun Schema

Task: EXP-001 - ExperimentTask / ExperimentRun schema
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Scope

Task-owned files:

- `services/research/experiments/experiment_task.schema.json`
- `services/research/experiments/experiment_run.schema.json`
- `services/research/experiments/models.py`
- `services/research/experiments/__init__.py`
- `services/research/experiments/README.md`
- `services/research/experiments/test_models.py`
- `support/evidence/EXP-001/README.md`

Delivered behavior:

- Adds Draft-7 JSON schemas for `ExperimentTask` and `ExperimentRun`.
- Adds frozen schema-backed domain models with payload validation helpers.
- Requires `ExperimentTask` to bind exactly one `strategy_id` and
  `strategy_spec_version`.
- Requires `ExperimentTask` and `ExperimentRun` to pin `dataset_version_id` and
  `code_version`.
- Requires `ExperimentRun` to carry `input_manifest_ref`; completed runs must
  carry `output_manifest_ref`, `started_at`, and `finished_at`.
- Preserves traceability through `trace_id`, task/run ids, StrategySpec version,
  backend id, runtime environment, and artifact refs.
- Keeps runtime environments research-scoped (`dev`, `sandbox`, `research`) and
  does not add adapter launch, registry writeback, promotion approval, or
  execution routing.

## Acceptance Mapping

- A `strategy_spec` can submit an experiment task: `ExperimentTask` requires
  `strategy_id`, `strategy_spec_version`, backend policy, dataset, code, and
  idempotency pins.
- Task completion can produce an `experiment_run`: `ExperimentRun` binds back to
  `task_id` and preserves strategy/spec-version/dataset/code lineage.
- A run can later produce a CandidateArtifact: `ExperimentRun.artifact_refs` and
  `output_manifest_ref` provide the lineage surface for EXP-005 writeback while
  this task performs no registry writes.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/experiments/models.py services/research/experiments/test_models.py services/research/experiments/__init__.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/experiments/test_models.py -q
git diff --check -- services/research/experiments support/evidence/EXP-001/README.md
```

Observed results:

- `py_compile`: passed
- `pytest services/research/experiments/test_models.py -q`: 12 passed
- `git diff --check`: passed

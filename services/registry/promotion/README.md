# Promotion Gate (REG-002)

This directory is the canonical home for the promotion gate implementation.

Current contents:

- `gate.py`: lifecycle transition and metadata checks
- `cli.py`: CLI entrypoint for promoting a registry entry JSON document
- `test_gate.py`: service-local unit coverage for transition and projection behavior
- `smoke_test_gate.py`: end-to-end local smoke test for candidate/paper/live flow

Legacy compatibility files remain at repo root:

- `gate.py`
- `cli.py`

Those wrappers exist to avoid breaking older commands while task-board artifacts converge on the
service-local path.

## Scope

The promotion gate enforces:

- allowed lifecycle transitions
- candidate requirements
- paper requirements
- live requirements
- execution projection materialization for `EX-001`

## Lifecycle Rules

Allowed transitions follow the governed registry contract from `REG-001`:

- `draft -> candidate`
- `candidate -> paper`
- `paper -> live`
- `draft|candidate|paper|live -> retired` where allowed by `gate.py`

Direct skips such as `candidate -> live` are rejected.

## Promotion Requirements

`gate.py` enforces different metadata requirements per target state:

### Candidate

- `replication_success` must be truthy
- `lineage` must include at least one governed source reference:
  `source_run_ids`, `source_strategy_spec_id`, or `source_dataset_refs`
- legacy `lineage.source_run_id` is normalized to `lineage.source_run_ids`

### Paper

- `evaluation_summary` must be an object
- `evaluation_summary.risk_review_passed` must be truthy
- `evaluation_summary.sharpe_ratio` must be present
- `lineage` must still contain at least one source reference

### Live

- `approver` must be present
- `lineage` must still contain at least one source reference
- rollback metadata must exist in one of these forms:
  - canonical: `metadata.rollback.target_registry_id` + `metadata.rollback.target_version`
  - temporary compatibility fallback: `metadata.rollback_target_registry_id` + top-level `rollback_target`
- rollback targets cannot point back to the same `registry_id` or `version`

## Execution Projection

`build_execution_projection()` materializes the `EX-001` loader-facing metadata envelope after
promotion succeeds. The canonical Object Store keys are:

- `openclaw/registry/{strategy_id}/{version}/metadata.json`
- `openclaw/registry/{strategy_id}/{version}/artifact.bin`

Projected metadata includes:

- `registry_id`
- `strategy_id`
- `version`
- `artifact_type`
- `promotion_state`
- `checksum`
- `lineage`
- `created_at`
- `approved_at` when the entry has been promoted
- `approver` when present
- `rollback` for promoted live artifacts

`live` execution projections are rejected unless rollback metadata is explicit, so cron deploy and
the `EX-001` artifact loader receive the same governed contract.

## CLI

Promote an entry without overwriting the source file:

```bash
python3 services/registry/promotion/cli.py \
  --entry-file /tmp/registry-entry.json \
  --to paper \
  --approver "risk-committee"
```

Overwrite the registry entry in place:

```bash
python3 services/registry/promotion/cli.py \
  --entry-file /tmp/registry-entry.json \
  --to live \
  --approver "risk-committee" \
  --inplace
```

It does not replace:

- registry storage
- execution loader checks
- experiment backend lineage

Those are defined in:

- `services/registry/contract.md`
- `services/registry/lineage/contract.md`
- `services/execution/artifact-loader/contract.md`

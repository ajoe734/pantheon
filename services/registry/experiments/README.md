# Experiment Registry Metadata Bridge

**Task:** LP-003  
**Owner:** Codex  
**Reviewer:** Grok  
**Status:** IMPLEMENTING

## Purpose

This directory is the governed bridge between Pantheon's local registry (`REG-001`) and the
first experiment tracking backend selected in `SPIKE-EXP-001`.

The first backend is:

- `MLflow`
- pinned to `mlflow==3.10.1`
- self-hosted first, not SaaS-first

The experiment backend is not authoritative for promotion. It mirrors metadata so operators,
research workers, and downstream evaluation tooling can inspect run lineage without bypassing
the local registry or promotion gate.

## Files

- `adapter.py` — MLflow-first mapping layer plus in-memory test backend
- `test_adapter.py` — unit tests for registry-to-experiment mapping
- `smoke_test.py` — local smoke path that proves one governed entry can round-trip into an
  experiment record and back into promoted metadata

## Registry -> MLflow Mapping

`RegistryExperimentAdapter` maps one governed registry entry into a single MLflow run.

### Experiment name

```text
pantheon/{artifact_type}/{strategy_id}
```

This keeps runs grouped by artifact class and strategy family.

### Run name

```text
{version}:{lifecycle_state}
```

This makes lifecycle progression visible without letting MLflow become the promotion source of
truth.

### Required mirrored tags

The adapter writes these core tags into MLflow:

- `pantheon.registry_id`
- `pantheon.strategy_id`
- `pantheon.version`
- `pantheon.artifact_type`
- `pantheon.lifecycle_state`
- `pantheon.checksum`
- `pantheon.storage_backend`
- `pantheon.storage_path`
- `pantheon.lineage`
- `pantheon.aliases`
- `pantheon.mlflow.version_pin`

Optional tags include:

- `pantheon.producer_run_id`
- `pantheon.promoted_at`
- `pantheon.approver`
- `pantheon.rollback_target`
- `pantheon.evaluation_summary`

Lineage subfields are also broken out into dedicated tags when present:

- `pantheon.lineage.parent_registry_ids`
- `pantheon.lineage.source_run_ids`
- `pantheon.lineage.source_dataset_refs`
- `pantheon.lineage.source_strategy_spec_id`

## Promotion Metadata and Aliases

MLflow does not decide Pantheon promotion state. Instead, the adapter mirrors that state in two
places:

- run tag `pantheon.lifecycle_state`
- descriptive aliases in the resulting `experiment_ref`

Alias policy in v1:

- `candidate` -> `["candidate"]`
- `paper` -> `["paper"]`
- `live` -> `["live"]`
- `retired` -> `["retired"]`

`draft` entries can still be logged as experiments, but they do not produce promoted metadata or
aliases.

The returned `promoted_metadata` payload includes `experiment_refs`, so the registry can write the
final execution-facing metadata envelope back into Object Store without trusting MLflow as the
governed source.

## Artifact Version Handoff

The adapter emits `artifact_handoff.json` into the run artifacts. This handoff is the bridge from
MLflow back to governed loading:

- it preserves the canonical `storage_ref`
- it preserves `checksum`
- it includes the required execution projection paths:
  - `openclaw/registry/{strategy_id}/{version}/metadata.json`
  - `openclaw/registry/{strategy_id}/{version}/artifact.bin`
- it carries descriptive aliases only

This ensures `EX-001` can keep loading from the governed Object Store projection rather than from
MLflow artifacts directly.

## Smoke Commands

Fast local check without external dependencies:

```bash
python3 services/registry/experiments/smoke_test.py
```

Real MLflow check against the local tracking server:

```bash
python3 services/registry/experiments/smoke_test.py --backend mlflow --tracking-uri http://localhost:5000
```

## Live Rollback Rule

`live` entries need richer rollback metadata than the earlier `REG-001` string-only sketch.

Accepted forms:

1. `metadata.rollback` with:
   - `target_registry_id`
   - `target_version`
2. `metadata.rollback_target_registry_id` plus top-level `rollback_target`

If neither is present, the adapter rejects syncing a `live` entry because the resulting
`REG-003` metadata would be incomplete.

## W&B Status

`W&B` remains deferred. Nothing here makes W&B impossible later, but LP-003 intentionally ships
MLflow first so Pantheon can keep lineage, rollback metadata, and storage control local while the
promotion path is still stabilizing.

# Experiment Registry Metadata Bridge

**Task:** LP-003  
**Owner:** Codex  
**Reviewer:** Grok  
**Status:** IMPLEMENTING

## Purpose

This directory is the governed bridge between Pantheon's local registry (`REG-001`) and the
experiment tracking backend selected for a given run.

The default backend is:

- `MLflow`
- pinned to `mlflow==3.10.1`
- self-hosted first, not SaaS-first

The repo also now includes a prep-only offline `W&B` scaffold for the 2026-04-25 deferred-prep
exception. That scaffold is feature-flagged, non-default, and not an activation claim.

The experiment backend is not authoritative for promotion. It mirrors metadata so operators,
research workers, and downstream evaluation tooling can inspect run lineage without bypassing
the local registry or promotion gate.

## Files

- `adapter.py` — experiment-backend adapter surface, MLflow backend, and offline W&B prep scaffold
- `config.py` — feature-flagged backend selector (`mlflow` default; `wandb` prep-only)
- `test_adapter.py` — unit tests for registry-to-experiment mapping
- `smoke_test.py` — local smoke path that proves one governed entry can round-trip into an
  experiment record and back into promoted metadata

## Registry -> Experiment Mapping

`RegistryExperimentAdapter` maps one governed registry entry into a single experiment-backend run.

### Experiment name

```text
pantheon/{artifact_type}/{strategy_id}
```

This keeps runs grouped by artifact class and strategy family.

### Run name

```text
{version}:{artifact_state}:{deployment_stage}
```

This makes artifact promotion and deployment projection visible without letting the experiment
backend become the source of truth for either field.

### Required mirrored tags

The adapter writes these core tags into the selected experiment backend:

- `pantheon.registry_id`
- `pantheon.strategy_id`
- `pantheon.version`
- `pantheon.artifact_type`
- `pantheon.artifact_state`
- `pantheon.deployment_stage`
- `pantheon.checksum`
- `pantheon.storage_backend`
- `pantheon.storage_path`
- `pantheon.lineage`
- `pantheon.aliases`
- `pantheon.experiment_backend`
- `pantheon.experiment_backend_version`

The MLflow path also emits `pantheon.mlflow.version_pin` as a backend-specific compatibility tag.

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

Legacy `lifecycle_state` input is accepted only as a compatibility projection during migration.
The adapter maps it into canonical `artifact_state` and `deployment_stage` fields before building
records, and emits `pantheon.compat.lifecycle_state` instead of treating the legacy value as
canonical state.

## Promotion Metadata and Aliases

The experiment backend does not decide Pantheon promotion state. Instead, the adapter mirrors that
state in two places:

- run tag `pantheon.artifact_state`
- run tag `pantheon.deployment_stage`
- descriptive aliases in the resulting `experiment_ref`

Alias policy in v1:

- `candidate` -> `["candidate"]`
- `approved` -> `["approved"]`
- `retired` -> `["retired"]`

`draft` entries can still be logged as experiments, but they do not produce promoted metadata or
aliases.

`deployment_stage` values (`none`, `paper`, `canary`, `frozen`, `live`) are mirrored as metadata
only. They are not promotion aliases and are never used as a replacement for `artifact_state`.
Any non-`none` deployment stage is valid only for `artifact_state=approved`; invalid combinations
are rejected before the selected backend records a run.

The returned `promoted_metadata` payload includes `experiment_refs`, so the registry can write the
final execution-facing metadata envelope back into Object Store without trusting the experiment
backend as the governed source.

## Artifact Version Handoff

The adapter emits `artifact_handoff.json` into the run artifacts. This handoff is the bridge from
the experiment backend back to governed loading:

- it preserves the canonical `storage_ref`
- it preserves `checksum`
- it includes the required execution projection paths:
  - `openclaw/registry/{strategy_id}/{version}/metadata.json`
  - `openclaw/registry/{strategy_id}/{version}/artifact.bin`
- it carries descriptive aliases only

This ensures `EX-001` can keep loading from the governed Object Store projection rather than from
backend-owned artifacts directly.

## Smoke Commands

Fast local check without external dependencies:

```bash
python3 services/registry/experiments/smoke_test.py
```

Real MLflow check against the local tracking server:

```bash
python3 services/registry/experiments/smoke_test.py --backend mlflow --tracking-uri http://localhost:5000
```

Offline W&B deferred-prep smoke:

```bash
python3 services/registry/experiments/smoke_test.py --backend wandb
```

## Live Rollback Rule

Entries with `deployment_stage=live` need richer rollback metadata than the earlier `REG-001`
string-only sketch.

Accepted forms:

1. `metadata.rollback` with:
   - `target_registry_id`
   - `target_version`
2. `metadata.rollback_target_registry_id` plus top-level `rollback_target`

If neither is present, the adapter rejects syncing a `live` entry because the resulting
`REG-003` metadata would be incomplete.

## W&B Status

`W&B` remains `criteria-defined` and deferred. The repo now includes a feature-flagged,
offline-only prep scaffold so reviewers can verify metadata-shape parity and selector wiring
without changing the default backend. This does not imply SDK readiness, network readiness,
or activation approval; `MLflow` remains the default backend and the only governed production path.

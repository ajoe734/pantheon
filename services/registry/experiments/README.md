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

The repo also includes two explicit-gated `W&B` lanes:

- an offline local run store that records W&B-compatible run and artifact refs into repo-local
  JSON files without importing the W&B SDK or connecting to the network
- an SDK-backed online sync backend that activates only when `PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1`
  plus a test W&B project and `WANDB_API_KEY` are present

The experiment backend is not authoritative for promotion. It mirrors metadata so operators,
research workers, and downstream evaluation tooling can inspect run lineage without bypassing
the local registry or promotion gate.

## Files

- `adapter.py` — experiment-backend adapter surface, MLflow backend, offline W&B local store, and
  explicit-gated SDK-backed W&B online backend
- `config.py` — feature-flagged backend selector (`mlflow` default; `wandb` requires offline or
  online env gates)
- `requirements.txt` — W&B SDK dependency for the online sync smoke container
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
The W&B paths emit `pantheon.wandb.offline_local`, `pantheon.wandb.mode`, and
`pantheon.wandb.online_sync_gate` as backend-specific tags. The online path also records
`pantheon.wandb.project` and, when configured, `pantheon.wandb.entity`; it never mirrors
`WANDB_API_KEY` into tags, params, artifacts, promoted metadata, or smoke output.

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

Offline W&B local-store smoke:

```bash
python3 services/registry/experiments/smoke_test.py --backend wandb
```

SDK-backed W&B online sync smoke:

```bash
PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1 \
PANTHEON_WANDB_PROJECT=<test-project> \
WANDB_API_KEY=<test-api-key> \
python3 services/registry/experiments/smoke_test.py --backend wandb-online
```

If the explicit gate, project, API key, or SDK install is missing, the online smoke exits
successfully with a structured `skipped` payload that names the missing config without printing
or persisting secrets.

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

`W&B` online sync is implemented only as an explicit-gated experiment-backend smoke path.
`MLflow` remains the default backend and the only governed production path. The W&B online backend
uses the SDK to upload metrics and an artifact bundle, then reads back run/artifact references via
the W&B API; missing local credentials produce a structured skip instead of a silent pass.

The W&B path remains non-ordering: it cannot write canonical registry truth, approve governance,
route to broker/order/capital systems, or promote paper/canary/live deployment state.

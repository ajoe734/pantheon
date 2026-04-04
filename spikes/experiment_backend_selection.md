# Experiment Backend Selection Spike

## Goal

Choose the first experiment lifecycle backend: `MLflow` or `W&B`.

## Decision Summary

- selected first backend: `MLflow`
- upstream package source: `https://pypi.org/project/mlflow/`
- recommended first pin: `mlflow==3.10.1`
- selected deployment mode: self-hosted local service for dev/staging
- deferred alternative: `W&B` as optional future backend
- decision reason: stronger local control over governance, storage, and rollback metadata

## Why MLflow Wins First

Both `MLflow` and `W&B` can support experiment lineage and artifact tracking, but they fit
different operational models.

`MLflow` is the better first backend for this repo because:

- it is easier to self-host as part of a governed local stack
- it maps naturally to registry lineage and experiment-run identifiers
- it avoids introducing a hosted SaaS dependency before the promotion path is stable

`W&B` remains valuable as a later option, especially if richer artifact aliases or hosted
collaboration become more important than local control.

## Required Decisions

- selected backend: `MLflow`
- deployment mode: self-hosted local service for initial integration
- version pin: `3.10.1`
- registry metadata mapping strategy
- smoke-test path for one registry-to-experiment round-trip

## Registry Mapping Strategy

The experiment backend must not become a second source of truth for promotion.

Rules:

1. `REG-001` remains the governance source of truth
2. the experiment backend mirrors lineage and run/artifact metadata
3. promotion state recorded in the backend is descriptive, not authoritative

Minimum mapped fields:

- `registry_id`
- `strategy_id`
- `version`
- `artifact_type`
- `source_run_ids`
- `checksum`
- `promotion_state`
- `rollback_target`

## Why W&B Is Deferred

`W&B` is still a legitimate later path, but it is deferred because:

- the first milestone needs local operational control
- alias-driven promotion semantics would otherwise overlap with `REG-001` too early
- we want the local registry contract locked before adding a second lifecycle UI

This is a sequencing decision, not a rejection of `W&B`.

## Minimal Smoke Test

The first smoke test should prove:

1. `mlflow==3.10.1` starts in a local environment
2. one governed run can log metrics and an artifact reference
3. the run can store lineage fields that map back to `REG-001`
4. one registry entry can round-trip into experiment metadata without changing promotion truth
5. rollback target metadata remains visible in the experiment record

Suggested scope:

- one local MLflow tracking server
- one fake or test registry entry
- one logged artifact and evaluation summary
- one validation step that checks registry and MLflow metadata alignment

## Follow-up Deliverables

When implementation begins, create:

- `integrations/mlflow/integration.md`
- `integrations/mlflow/smoke_test.md`
- `services/learning/experiments/`

## Remaining Open Questions

- Do we want the first MLflow deployment to use a local file backend or a local SQL-backed server?
- Which fields should be duplicated into MLflow tags versus stored inside the artifact metadata payload itself?

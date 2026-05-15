# MLflow Integration — Governance Overlay

Last updated: 2026-04-15
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: governed runtime boundary documented
Related task: `LP-003`

## 1. Governance Principle

> MLflow may mirror governed registry metadata. It may not define promotion truth.

Pantheon remains authoritative for artifact lifecycle, deployment stage, rollback linkage, and
execution routing. MLflow is a descriptive mirror and inspection backend.

## 2. Registry-First Authority

The adapter enforces registry-first semantics:

- input is a governed registry entry
- output is promoted experiment metadata returned to Pantheon
- lifecycle aliases are descriptive only
- `draft` entries may be logged, but they do not produce promoted metadata aliases

This keeps MLflow behind Pantheon's registry instead of beside it as a competing control plane.

## 3. Mandatory Validation

The adapter validates core governance requirements before sync:

- supported lifecycle state must be one of the governed LP-003 states
- registry entry must include a lineage object
- non-draft promoted entries must have lineage that points to a run, dataset, or strategy spec
- `live` entries must carry rollback metadata

Accepted rollback forms:

1. `metadata.rollback` with target registry id and target version
2. `metadata.rollback_target_registry_id` plus top-level `rollback_target`

If those constraints fail, sync is rejected.

## 4. Storage and Execution Boundary

MLflow is not the canonical artifact store.

The adapter emits `artifact_handoff.json` so downstream loading keeps using governed
storage projections such as:

- `openclaw/registry/{strategy_id}/{version}/metadata.json`
- `openclaw/registry/{strategy_id}/{version}/artifact.bin`

That preserves Pantheon's object-store and execution authority boundary.

## 5. Deferred Backend Rule

`W&B` remains a deferred alternative backend.

This governed baseline assumes:

- MLflow is the only active backend
- backend-generalization work has not happened yet
- no document should imply a second experiment backend already exists

## 6. Upgrade Rules

When changing the MLflow pin or adapter behavior:

1. update `MLFLOW_VERSION_PIN` and any deployment/runtime notes
2. rerun `python3 services/registry/experiments/smoke_test.py`
3. rerun `python3 -m unittest discover -s services/registry/experiments -p 'test_*.py'`
4. update `integration.md`, this governance file, and `OSS_INTEGRATION_CHECKLIST.md`

Any future second backend must preserve the same registry-first authority, lineage validation,
and rollback requirements before it can claim parity.

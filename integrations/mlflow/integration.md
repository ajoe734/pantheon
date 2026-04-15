# MLflow Integration — Runnable Adapter Baseline

Last updated: 2026-04-15
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: governed runnable adapter verified
Implementation home: `services/registry/experiments/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `mlflow/mlflow` |
| Package source | `https://pypi.org/project/mlflow/` |
| Selected package | `mlflow==3.10.1` |
| Version pin source | `services/registry/experiments/adapter.py` (`MLFLOW_VERSION_PIN`) |
| First deployment mode | self-hosted tracking backend |
| Alternate backend status | `W&B` remains deferred |

Pantheon treats MLflow as the first experiment-tracking backend, but not as a second source of truth
for registry lifecycle or promotion.

## 2. Adapter Mode

Pantheon uses MLflow through a registry-first adapter for `LP-003`.

Accepted mode:

- one governed registry entry maps to one experiment record
- lifecycle state is mirrored into MLflow metadata, not delegated to MLflow
- the adapter returns promoted experiment metadata back to Pantheon's registry layer
- local smoke can run against an in-memory backend, while real deployment can point at a self-hosted tracking server

Rejected mode:

- allowing MLflow aliases or model stages to define Pantheon promotion truth
- loading production artifacts directly from MLflow instead of governed storage refs
- syncing live entries without required rollback metadata

## 3. Verified Local Adapter Surface

The runnable adapter path is already implemented and exercised:

- `services/registry/experiments/adapter.py`
  - `RegistryExperimentAdapter`
  - `ExperimentBackend`
  - `InMemoryMlflowBackend`
- `services/registry/experiments/smoke_test.py`
- `services/registry/experiments/test_adapter.py`
- `services/registry/experiments/WANDB_ACTIVATION.md`

The adapter:

1. accepts a governed registry entry
2. mirrors core registry metadata into an experiment record
3. writes an `artifact_handoff.json` projection for governed loading
4. returns `promoted_metadata` with `experiment_refs` for registry persistence

## 4. Runtime and Packaging Notes

- Local smoke requires only Python and the in-memory backend path
- Real backend execution uses `RegistryExperimentAdapter.from_tracking_uri(...)`
- Self-hosted MLflow is the intended first runtime mode
- `W&B` is explicitly deferred behind backend-generalization work

The adapter supports two execution modes:

1. `memory` backend for deterministic local smoke and tests
2. real `mlflow` backend targeting a local tracking server

## 5. Why This Counts As Runnable

This row is already executable because:

- the registry-to-experiment bridge is real code
- the repo ships an executable smoke path
- unit tests cover lifecycle validation and metadata mapping
- the adapter returns governed metadata rather than stopping at a narrative README

## 6. Evidence References

- implementation overview: `services/registry/experiments/README.md`
- governance overlay: `integrations/mlflow/governance.md`
- smoke procedure: `integrations/mlflow/smoke_test.md`

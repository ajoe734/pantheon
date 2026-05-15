# MLflow Integration — Smoke Test

Last updated: 2026-04-17
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: executable smoke path verified
Primary entrypoint: `python3 services/registry/experiments/smoke_test.py`

## 1. Objective

Prove that the MLflow row is backed by a runnable registry-to-experiment adapter rather than a
backend-selection note.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/registry/experiments/`

Optional real-backend prerequisites:

- `mlflow` installed in the runtime environment
- a reachable MLflow tracking URI

## 3. Canonical Commands

Deterministic local smoke:

```bash
python3 services/registry/experiments/smoke_test.py
```

Real MLflow smoke against a local tracking server:

```bash
python3 services/registry/experiments/smoke_test.py --backend mlflow --tracking-uri http://localhost:5000
```

Unit coverage:

```bash
python3 -m unittest discover -s services/registry/experiments -p 'test_*.py'
```

## 4. What the Smoke Path Verifies

The smoke script constructs one governed registry entry and proves that:

1. the adapter can sync it into an experiment backend
2. mirrored tags preserve registry id and lifecycle state
3. `live` aliases are descriptive only
4. `artifact_handoff.json` preserves the governed storage path
5. rollback metadata is returned in promoted experiment metadata

## 5. Verified Result

Verified on 2026-04-17 with the default in-memory backend:

- command: `python3 services/registry/experiments/smoke_test.py`
- result: `LP-003 smoke test passed with backend=memory: registry metadata mapped into experiment metadata.`

Unit coverage result on 2026-04-17:

- `python3 -m unittest discover -s services/registry/experiments -p 'test_*.py'`
- `Ran 4 tests`
- `OK`

## 6. Acceptance

Treat the MLflow row as smoke-proven when:

- the smoke command exits `0`
- the adapter mirrors lifecycle metadata into the experiment backend
- the promoted metadata includes rollback linkage
- unit coverage still passes

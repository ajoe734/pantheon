# imitation Integration — Smoke Test

Last updated: 2026-04-17
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: executable smoke path verified
Primary entrypoint: `python3 services/learning/imitation/smoke_test.py`

## 1. Objective

Prove that the imitation row is backed by a runnable governed adapter path rather than a
placeholder learning plan.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/learning/imitation/`

Optional upstream smoke prerequisites:

- `pip install -r services/learning/imitation/requirements.txt`

## 3. Canonical Commands

Deterministic local smoke:

```bash
python3 services/learning/imitation/smoke_test.py
```

Optional upstream imitation smoke:

```bash
python3 services/learning/imitation/smoke_test.py --backend imitation
```

Unit coverage:

```bash
python3 -m unittest discover -s services/learning/imitation -p 'test_*.py'
```

## 4. What the Smoke Path Verifies

The smoke script loads `examples/trajectory_dataset_sample.json` and proves that:

1. the governed trajectory dataset parses successfully
2. ineligible trajectories are not required for the happy path
3. `run_imitation_workflow()` emits a registry-ready `model_artifact`
4. the artifact writes a stable storage path under `learning/imitation/{strategy_id}/{version}/`
5. training metrics are available from the backend result

## 5. Verified Result

Verified on 2026-04-17 with the default stub backend:

- backend: `stub_bc`
- trajectories: `2`
- transitions: `4`
- registry id: `reg-alpha-mean-reversion-imitation-0.1.0`
- storage path: `learning/imitation/alpha-mean-reversion/0.1.0/artifact_bundle.json`
- checksum: `sha256:02d757f6a00ef711a34ce21dbc9b90dbb0f6b4e32ee80d8600cff2578cb4ced9`
- `training_accuracy = 1.0`
- `action_coverage_ratio = 1.0`
- `num_trajectories = 2`
- `num_transitions = 4`
- `epochs = 1`

Unit coverage result on 2026-04-17:

- `python3 -m unittest discover -s services/learning/imitation -p 'test_*.py'`
- `Ran 3 tests`
- `OK`

## 6. Acceptance

Treat the imitation row as smoke-proven when:

- the smoke command exits `0`
- the workflow emits a registry id and governed storage path
- the result contains training metrics and lineage metadata
- unit coverage still passes

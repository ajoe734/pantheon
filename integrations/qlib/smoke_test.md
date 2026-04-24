# Qlib Integration — Smoke Test

Last updated: 2026-04-24
Owner: APP-003-QLIB-ACTIVATION-001 (Codex2)
Reviewer: Codex
Status: executable smoke path revalidated
Primary entrypoint: `python3 services/research/qlib/smoke_test.py`

## 1. Objective

Prove that the Qlib row is backed by a runnable governed adapter path rather than a
placeholder activation criteria document.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/research/qlib/`

Optional upstream smoke prerequisites:

- `pip install -r services/research/qlib/requirements.txt`

## 3. Canonical Commands

Deterministic local smoke (no Qlib install required):

```bash
python3 services/research/qlib/smoke_test.py
```

Optional upstream Qlib smoke:

```bash
python3 services/research/qlib/smoke_test.py --backend qlib
```

Unit coverage:

```bash
python3 -m unittest discover -s services/research/qlib -p 'test_*.py'
```

## 4. What the Smoke Path Verifies

The smoke script loads `examples/equity_dataset_sample.json` and proves that:

1. the governed OHLCV dataset parses and validates successfully
2. `GovernedQlibDataAdapter` produces a feature matrix and labels from raw OHLCV
3. `run_qlib_workflow()` emits a registry-ready `model_artifact`
4. `artifact_state` is `draft` and `deployment_summary.current_stage` is `none`
5. the artifact carries a `sha256:` checksum and a governed storage path
6. `governance.direct_live_influence` is `false`
7. `governance.lean_consumption` is `scoring_only_not_direct_action`

## 5. Verified Result

Revalidated on 2026-04-24 with the default stub backend:

- backend: `stub_lgbm`
- instruments: `3`
- samples: `12`
- features: `4`
- registry_id: `qlib-alpha-equity-cross-sectional-alpha-1.0.0`
- artifact_state: `draft`
- deployment_stage: `none`
- storage_path: `research/qlib/equity-cross-sectional-alpha/1.0.0/artifact.bin`
- checksum: `sha256:*` (run-specific; changes with `created_at` / `run_id`)
- artifact_family: `qlib_alpha`
- `ic = 0.0` (expected for stub mean predictor)
- `mean_label = 0.00715282`
- `mse = 3.5745e-05`
- assertions: OK

Unit coverage result on 2026-04-24:

- `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
- `Ran 14 tests`
- `OK`

## 6. Acceptance

Treat the Qlib row as smoke-proven when:

- the smoke command exits `0` with `assertions: OK`
- the workflow emits a registry_id and governed storage path
- `artifact_state=draft` and `deployment_stage=none` are confirmed
- `direct_live_influence=false` is confirmed
- unit coverage still passes (14 tests)

# Qlib Integration — Smoke Test

Last updated: 2026-05-16
Task: QLIB-001
Owner: Gemini
Reviewer: Claude
Status: smoke, activation-ready offline path, and production packet path revalidated
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
python3 services/research/qlib/smoke_test.py --backend real
```

Unit coverage:

```bash
python3 -m unittest discover -s services/research/qlib -p 'test_*.py'
```

Gateway activation-ready offline path:

```bash
pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py
```

Production-data activation packet smoke:

```bash
python3 services/research/qlib/production_activation_smoke.py \
  --dataset /path/to/governed_ohlcv_dataset.json \
  --proof /path/to/production_dataset_proof.json \
  --backend stub \
  --output-dir /tmp/pantheon/research/qlib/prod-activation
```

Use `--backend real` for the upstream Qlib LightGBM path. Missing upstream
dependencies return an explicit install/config error instead of falling back to
the stub.

## 4. What the Smoke Path Verifies

The smoke script loads `examples/equity_dataset_sample.json` and proves that:

1. the governed OHLCV dataset parses and validates successfully
2. `GovernedQlibDataAdapter` produces a feature matrix and labels from raw OHLCV
3. `run_qlib_workflow()` emits a registry-ready `model_artifact`
4. `artifact_state` is `draft` and `deployment_summary.current_stage` is `none`
5. the artifact carries a `sha256:` checksum and a governed storage path
6. `governance.direct_live_influence` is `false`
7. `governance.lean_consumption` is `scoring_only_not_direct_action`
8. `candidate_packet` requests only `draft -> candidate` and declares
   `registry_service_only` as write authority
9. activation-ready data floors fail closed for insufficient datasets
10. worker execution requires `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1`
11. production activation packet smoke requires provider, entitlement,
    freshness, PIT, durable storage, rate-limit/audit, and no-order-route proof

## 5. Verified Result

Revalidated on 2026-05-16 with the default stub backend:

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

Unit coverage result on 2026-05-16:

- `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
- `Ran 33 tests`
- `OK`

Production packet result on 2026-05-01:

- covered by `services/research/qlib/test_production_activation.py`
- confirms `production_activation_smoke.py --backend stub` writes
  `production_activation_packet.json` plus the existing artifact, registry,
  candidate, and manifest handoff files
- confirms complete proof fields are attached and order routing remains `none`

Gateway path result on 2026-04-30:

- `pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py`
- `2 passed`
- closed gate rejects Qlib offline dispatch
- open offline gate plus Qlib env gate executes the worker and persists handoff artifacts
- combined activation/dispatch verification also passed:
  `python3 -m pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py services/research-worker-gateway/tests/test_research_worker_gateway_gate_dispatch.py`
  (`11 passed`)
- rejection/http regression verification also passed:
  `python3 -m pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py`
  (`9 passed`)

## 6. Acceptance

Treat the Qlib row as smoke-proven when:

- the smoke command exits `0` with `assertions: OK`
- the workflow emits a registry_id and governed storage path
- `artifact_state=draft` and `deployment_stage=none` are confirmed
- `direct_live_influence=false` is confirmed
- candidate packet and artifact manifest are emitted without registry writes
- production activation packet is emitted only with governed data proof and
  without registry/order writes
- unit coverage still passes (33 tests)
- gateway Qlib activation test still proves closed-gate rejection and explicit-gate offline success

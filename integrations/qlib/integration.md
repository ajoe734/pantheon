# Qlib Integration — Governed LightGBM Alpha Adapter

Last updated: 2026-04-24
Owner: APP-003-QLIB-ACTIVATION-001 (Codex2)
Reviewer: Codex
Status: smoke-tested governed adapter verified; activation packet prepared
Implementation home: `services/research/qlib/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `microsoft/qlib` |
| Package source | `https://pypi.org/project/pyqlib/` |
| Selected package | `pyqlib==0.9.6` |
| Version pin source | `services/research/qlib/adapter/qlib_adapter.py` (`QLIB_VERSION_PIN`) |
| Service dependency file | `services/research/qlib/requirements.txt` |
| Worker image | `services/research/qlib/Dockerfile` |

This integration consumes Qlib as a pinned Python package. Pantheon does not vendor
Qlib source and does not treat Qlib as an authority for registry lifecycle,
promotion, or execution routing.

## 2. Adapter Mode

Pantheon uses Qlib only for governed supervised alpha signal research under `LP-003`.

Accepted mode:

- governed OHLCV data is validated and feature-engineered before Qlib sees it
- Qlib LightGBM training runs inside `services/research/qlib/`
- the adapter emits a governed `qlib_alpha` artifact bundle and a registry-ready entry
- the default CI path uses a deterministic stub backend (no Qlib install required)
- worker/runtime paths can switch to the real Qlib `LGBModel` backend

Rejected mode:

- direct Qlib writes into registry truth
- direct Qlib influence on LEAN, SignalStore, or live deployment paths
- using Qlib artifacts as live execution authority without registry promotion
- Qlib consuming raw market data that bypasses governance lineage

## 3. Verified Local Adapter Surface

The runnable adapter path is implemented and exercised:

- `services/research/qlib/adapter/qlib_adapter.py`
  - `GovernedQlibDataAdapter`
  - `StubLightGBMBackend`
  - `QlibLightGBMBackend`
  - `run_qlib_workflow()`
- `services/research/qlib/smoke_test.py`
- `services/research/qlib/test_adapter.py`
- `services/research/qlib/examples/equity_dataset_sample.json`
- `services/research/qlib/worker.py`

The adapter takes governed OHLCV records and emits:

1. `artifact_bundle`
   - `artifact_family=qlib_alpha`
   - `model_family=lightgbm`
   - `framework=qlib`
   - governance block with `direct_live_influence=false` and `lean_consumption=scoring_only_not_direct_action`
2. `registry_entry`
   - `artifact_type=model_artifact`
   - `artifact_state=draft`
   - `deployment_summary.current_stage=none`
   - lineage points back to source dataset refs and training run

## 4. Feature Engineering

The governed data handler computes 4 features per (instrument, period) sample:

| Feature | Description |
|---|---|
| `momentum_1d` | 1-day price return |
| `momentum_2d` | 2-day cumulative return |
| `volume_change_1d` | 1-day volume change ratio |
| `volatility_1d` | absolute 1-day price change ratio |

Forward 1-day return is computed as the label. This matches the `LP-003` supervised-prediction
framing (predict next-day returns, not sequential decisions).

## 5. Runtime and Packaging Notes

- Base worker image: `python:3.11-slim`
- Default command: `python worker.py` (Dockerfile CMD)
- Smoke test requires no external dependencies (stub backend)
- Optional Qlib backend requires `pip install -r services/research/qlib/requirements.txt`

Two execution modes are intentionally supported:

1. stub backend for deterministic CI and local smoke tests
2. real Qlib `LGBModel` backend for dedicated research workers

## 6. Why This Counts As Runnable

`OSS-NEXT-001` requires a governed runnable adapter, not just a criteria document.

That proof exists because:

- the adapter path is real code, not a placeholder contract
- the service has a dedicated dependency file and Docker worker image
- the repo ships an executable smoke test with a governed sample dataset
- unit tests (14 tests) validate governed filtering and packaging logic
- registry output uses canonical `artifact_state` + `deployment_summary.current_stage`

## 7. Evidence References

- governance overlay: `integrations/qlib/governance.md`
- smoke procedure: `integrations/qlib/smoke_test.md`
- activation packet: `integrations/qlib/activation_packet.md`
- activation criteria: `services/learning/qlib/ACTIVATION_CRITERIA.md`

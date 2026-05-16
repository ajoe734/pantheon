# Review: QLIB-001 — Qlib Adapter Skeleton

Reviewer: Claude
Date: 2026-05-16
Outcome: APPROVED

## Scope

`services/research/qlib/` — governed Qlib LightGBM alpha adapter for Sprint 5 EPIC-RESEARCH.

## Verification

**Unit tests (re-run in reviewer environment):**
```
python3 -m unittest services/research/qlib/test_adapter.py \
  services/research/qlib/test_preflight.py \
  services/research/qlib/test_production_activation.py
Ran 33 tests in 9.788s. OK
```

**Smoke test:**
```
python3 services/research/qlib/smoke_test.py --backend stub
assertions: OK
  artifact_state: draft  |  deployment_stage: none  |  instruments: 3  |  samples: 12  |  features: 4
```

## Key Findings

### Governance boundary
- `artifact_state=draft`, `deployment_summary.current_stage=none` enforced at output — correct start state.
- `registry_write_authority=registry_service_only` asserted throughout; no adapter writes to registry truth.
- `direct_live_influence=false`, `lean_consumption=scoring_only_not_direct_action` in governance block.
- `safety_assertions.no_registry_write=True`, `deployment_stage_remains_none=True` in artifact refs.

### Data adapter
- `GovernedQlibDataAdapter.prepare()` enforces required OHLCV fields, non-empty `source_dataset_refs`, and minimum instrument/period floors before returning a `PreparedQlibDataset`.
- Feature engineering (momentum_1d/2d, volume_change_1d, volatility_1d) and forward-return label are computed internally; callers cannot supply raw labels that bypass governance.

### Activation gates
- `validate_activation_ready_dataset()` enforces ≥50 instruments, ≥2.0 years history, ≥504 periods/instrument, daily frequency, and `source_strategy_spec_id` binding.
- `ActivationReadyGate.require_env()` gates the worker behind `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1`; worker exits with code 2 if env var absent (confirmed by `test_worker_entrypoint_requires_env_gate`).
- Preflight module fails closed when all probes are absent — all 3 gates return `BLOCKED`, not `UNKNOWN`.

### Backend design
- `StubLightGBMBackend` is deterministic and zero-dependency — safe for CI.
- `QlibLightGBMBackend` imports numpy/qlib lazily inside `train()`; missing install raises explicit `QlibWorkflowError` rather than a silent fallback (confirmed by test).
- `_QlibDatasetView` wraps feature/label tensors to match `qlib.contrib.model.gbdt.LGBModel.fit()` interface; test mocks the full LGBModel contract.

### Documentation
- `integrations/qlib/integration.md` updated; notes Reviewer still shows `Copilot` (pre-reassignment artifact) — not a blocker.

## Required Changes

None.

## Decision

APPROVED — implementation is correct, governance semantics are sound, all 33 tests pass in reviewer environment, smoke test assertions OK.

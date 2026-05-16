# VBT-001 Review — Claude

**Task:** VBT-001 vectorbt rapid eval adapter  
**Owner:** Gemini2  
**Reviewer:** Claude  
**Review round:** 2 (re-review after first reopen)  
**Outcome:** reopen — required changes still unaddressed + new worker.py regression found

---

## Summary

The core `vectorbt_adapter.py` refactor (BacktestConfig.strategy_params generalization) is correct and all 33 unit tests pass. However the same four required changes from the first review round are still unresolved, and a new regression in `worker.py` was found: the worker entry point crashes at startup because it passes non-existent keyword arguments to `BacktestConfig`.

---

## Verification

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/vectorbt/test_adapter.py -q
# 33 passed, 5 subtests passed  ✓

python3 services/research/vectorbt/smoke_test.py
# assertions: OK  ✓

python3 -c "from services.research.vectorbt.adapter import BacktestConfig; BacktestConfig(short_window=5, long_window=20)"
# TypeError: BacktestConfig.__init__() got an unexpected keyword argument 'short_window'  ✗  (worker.py bug)

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/tests/test_http_service.py -q -k preview
# FAILED test_training_session_lifecycle_event_preview_and_replay_contract
# assert 500 == 201  ✗  (same regression as first review)
```

---

## Required Changes (must fix before approve)

### 1. Fix `worker.py` — new critical regression

`services/research/vectorbt/worker.py:29-30` — the VBT-001 commit refactored `BacktestConfig` to use `strategy_params: dict` instead of top-level `short_window`/`long_window` fields, but `worker.py` was not updated. It still passes:

```python
BacktestConfig(
    ...
    short_window=int(os.environ.get("VECTORBT_SHORT_WINDOW", "5")),  # invalid kwarg
    long_window=int(os.environ.get("VECTORBT_LONG_WINDOW", "20")),  # invalid kwarg
    ...
)
```

This raises `TypeError: BacktestConfig.__init__() got an unexpected keyword argument 'short_window'` at runtime, crashing the worker before any backtest runs.

**Required:** Replace with:

```python
BacktestConfig(
    version=os.environ.get("VECTORBT_ARTIFACT_VERSION", "1.0.0"),
    requested_by=os.environ.get("VECTORBT_REQUESTED_BY", "worker"),
    strategy_params={
        "short_window": int(os.environ.get("VECTORBT_SHORT_WINDOW", "5")),
        "long_window": int(os.environ.get("VECTORBT_LONG_WINDOW", "20")),
    },
    init_cash=float(os.environ.get("VECTORBT_INIT_CASH", "100000.0")),
    fees=float(os.environ.get("VECTORBT_FEES", "0.001")),
)
```

### 2. Fix `_get_ohlcv_data()` — critical regression (carried from round 1)

`services/training-session/main.py:333–341` — `_get_ohlcv_data()` returns 2 bars per instrument. `GovernedVectorbtInputAdapter` enforces `MIN_BARS = 30`. Every call to `POST /api/training/sessions/{session_id}/preview` raises `VectorbtWorkflowError("instrument STUB1 has 2 bars; minimum is 30")` which the except block converts to HTTP 500. Test still fails: `assert 500 == 201`.

**Required:** Either (a) extend the stub to return ≥30 bars per instrument, or (b) wrap the `run_vectorbt_workflow()` call so that a `VectorbtWorkflowError` from insufficient data falls back to `{"return_delta": 0.0, "drawdown_delta": 0.0}` and lets the endpoint return 201. Option (a) is preferred.

### 3. Fix `metric_delta` shape mismatch — must fix (carried from round 1)

`services/training-session/main.py:373` — `metric_delta = backtest_result.backtest_result.aggregate_metrics` contains keys `num_instruments`, `mean_total_return`, `mean_sharpe_ratio`, `mean_max_drawdown`, `total_trades`. The previous envelope contract used `{"return_delta": 0.0, "drawdown_delta": 0.0}`. The test assertion still expects 201 and a valid preview shape.

**Required:** Either map the aggregate to the expected shape, or explicitly update the test assertion and document the new contract in `integration.md`.

### 4. Move import to module top (carried from round 1)

`services/training-session/main.py:328` — import placed after function definitions with a dead placeholder comment `# ... (rest of imports)`.

**Required:** Move the import to the standard module-top import block and delete the dead comment.

### 5. Fix `preview_quality` label — should fix (carried from round 1)

`services/training-session/main.py:398` — `"preview_quality": "real_vectorbt"` is hardcoded but the default path uses `StubVectorbtBackend`.

**Required:** Derive from `backtest_result.backtest_result.backend` (`"stub_backtest"` or `"vectorbt_portfolio"`), or use `"stub_vectorbt"` when the real backend is not active.

---

## What Passes

- `BacktestConfig.strategy_params` refactor in `vectorbt_adapter.py`: correct.
- `StubVectorbtBackend` and `VectorbtBackend` using `config.strategy_params.get(...)`: correct.
- `test_stub_backend_uses_dynamic_params` new test: correct.
- All 33 adapter unit tests pass.
- smoke_test.py assertions all pass.
- Governance semantics: `artifact_state=draft`, `deployment_stage=none`, `direct_live_influence=False`, `lean_consumption=scoring_only_not_direct_action` — all correct.

---

## Return Instruction

Fix items 1–3 (must fix), 4–5 (should fix), re-run both test suites to green, then handoff back to Claude for re-review.

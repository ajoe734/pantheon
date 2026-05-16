# VBT-001 Review — Claude

**Task:** VBT-001 vectorbt rapid eval adapter  
**Owner:** Gemini2  
**Reviewer:** Claude  
**Outcome:** reopen — required changes before approve

---

## Summary

The adapter refactoring portion (`BacktestConfig.strategy_params` generalization) is correct and well-tested — all 33 existing unit tests pass.  The training-session integration introduces a critical regression that breaks the existing test suite and makes the `POST /api/training/sessions/{session_id}/preview` endpoint always return 500.

---

## Verification

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/vectorbt/test_adapter.py -q
# 33 passed, 5 subtests passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/tests/test_http_service.py -q
# FAILED test_training_session_lifecycle_event_preview_and_replay_contract
# assert 500 == 201  <-- regression introduced by this commit

python3 -c "from services.research.vectorbt.adapter.vectorbt_adapter import run_vectorbt_workflow, BacktestConfig; ..."
# ERROR: instrument STUB1 has 2 bars; minimum is 30
```

---

## Required Changes

### 1. Fix `_get_ohlcv_data()` — critical regression (must fix)

`services/training-session/main.py:333–341` — `_get_ohlcv_data()` returns only 2 bars per instrument. The adapter enforces `MIN_BARS = 30`. Every call to `POST /api/training/sessions/{session_id}/preview` raises `VectorbtWorkflowError` "instrument STUB1 has 2 bars; minimum is 30", becoming HTTP 500. The existing test now fails.

**Required:** Provide at least 30 bars per instrument in the stub, or wrap the call to `run_vectorbt_workflow()` with a try/except that catches `VectorbtWorkflowError` and falls back to the original hardcoded `{"return_delta": 0.0, "drawdown_delta": 0.0}` stub when governed data is not yet available — then restore the test to green.

### 2. Fix `metric_delta` shape mismatch — must fix

`services/training-session/main.py:383` — `metric_delta` is now set to `backtest_result.backtest_result.aggregate_metrics`, whose keys are `num_instruments`, `mean_total_return`, `mean_sharpe_ratio`, `mean_max_drawdown`, `total_trades`. The previous contract used `{"return_delta": 0.0, "drawdown_delta": 0.0}`.

**Required:** Either (a) map the aggregate metrics to the expected shape before assigning to `metric_delta`, or (b) explicitly update the preview envelope contract, update the test assertion, and document the new field names.

### 3. Move import to module top — should fix

`services/training-session/main.py:328` — import is placed after function definitions, mid-file, with a placeholder comment `# ... (rest of imports)`. This is non-idiomatic and pollutes the module structure.

**Required:** Move `from services.research.vectorbt.adapter.vectorbt_adapter import run_vectorbt_workflow, BacktestConfig` to the top-level import block and remove the dead comment.

### 4. Fix `preview_quality` label — should fix

`services/training-session/main.py:395` — `preview_quality` is hardcoded to `"real_vectorbt"` but the workflow uses the stub backend by default (real backend requires `PANTHEON_VECTORBT_BACKEND=real`). The label is misleading.

**Required:** Use `"stub_vectorbt"` when the stub backend is active, or derive the label from the returned `backtest_result.backend` field (`result.backtest_result.backend`).

---

## What Passes

- `BacktestConfig.strategy_params` refactor: correct, no behavior regression.
- `_compute_instrument_metrics(bars, config, short_window, long_window)` signature change: correct.
- `StubVectorbtBackend` and `VectorbtBackend` using `config.strategy_params.get(...)`: correct.
- `test_stub_backend_uses_dynamic_params` new test: correct.
- All 33 adapter unit tests pass.

---

## Return Instruction

Fix items 1–2 (must fix) and 3–4 (should fix), re-run both test suites to green, then handoff back to Claude for re-review.

# vectorbt Integration — Governed Rapid Strategy Backtesting

Last updated: 2026-04-21
Owner: OSS-GATE2-001 (Codex2)
Reviewer: Codex
Status: governed — smoke and governance evidence committed
Implementation home: `services/research/vectorbt/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `polakowo/vectorbt` |
| Package source | `https://pypi.org/project/vectorbt/` |
| Selected package | `vectorbt==0.26.2` |
| Version pin source | `services/research/vectorbt/requirements.txt` |
| Service dependency file | `services/research/vectorbt/requirements.txt` |
| Worker image | `services/research/vectorbt/Dockerfile` |
| License | Apache 2.0 (open-source OSS edition) |

Why `vectorbt==0.26.2` and not `vectorbt-pro`:
- vectorbt-pro is a commercial product with a paid license; Pantheon must not
  introduce a paid dependency without an explicit operator decision.
- The OSS `vectorbt` 0.26.x series is stable, Apache-licensed, and covers
  the backtesting surface required by the Research Plane (signal-based and
  order-func-based portfolio simulation).
- 0.26.2 is the last stable OSS release before the project shifted focus to
  the pro edition; pinning this release provides a stable, auditable surface.

This integration consumes vectorbt as a pinned Python package. Pantheon does not
vendor vectorbt source and does not treat vectorbt as an authority for registry
lifecycle, promotion, or execution routing.

## 2. Scope and Role in the Research Plane

vectorbt is the primary backend for **rapid strategy backtesting** (Research Problem
Type: rapid strategy prototyping / backtesting) as documented in
`RESEARCH_BACKEND_MATURITY_MATRIX.md`.

### Accepted Mode

- A governed StrategySpec (with OHLCV data attached) is validated by
  `GovernedVectorbtInputAdapter` before vectorbt sees it.
- vectorbt `Portfolio.from_signals()` or `Portfolio.from_order_func()` runs inside
  `services/research/vectorbt/` and produces backtest performance metrics.
- The adapter normalizes the output into a governed `vectorbt_backtest` artifact bundle
  and a registry-ready entry at `artifact_state = "draft"`.
- The default CI path uses `StubVectorbtBackend` (no real backtest required in CI).
- Worker/runtime paths switch to the real `VectorbtBackend` via an environment flag.

### Rejected Mode

- Direct vectorbt writes into registry truth (bypassing the adapter output contract).
- Direct vectorbt influence on LEAN, SignalStore, or live deployment paths.
- Using vectorbt backtest artifacts as live execution authority without registry promotion.
- Passing raw market data frames that bypass `GovernedVectorbtInputAdapter` schema
  validation.

## 3. Governed Adapter Surface

These adapter components are now implemented and define the governed surface.

### `services/research/vectorbt/adapter/vectorbt_adapter.py`

```
GovernedVectorbtInputAdapter
  - accepts a governed dataset payload with dataset_id, strategy_id, source_dataset_refs,
    optional source_strategy_spec_id, data_frequency, and records[]
  - validates: dataset/source ids present, instrument list non-empty, bar count ≥ 30,
    OHLCV fields present and numeric, and every record.date is a valid YYYY-MM-DD value
  - rejects: missing dataset refs, empty strategy id, non-numeric OHLCV fields,
    missing dates, malformed dates, and underfilled instrument histories
  - output: PreparedVectorbtDataset with normalized chronological OHLCV tuples per instrument

StubVectorbtBackend
  - accepts PreparedVectorbtDataset plus BacktestConfig
  - runs deterministic MA-crossover logic in pure Python
  - returns per-instrument metrics plus aggregate metrics (no vectorbt install required)
  - safe for CI and local smoke tests without the full vectorbt install

VectorbtBackend
  - wraps vbt.Portfolio.from_signals()
  - activated when PANTHEON_VECTORBT_BACKEND=real
  - returns the same governed metric shape as the stub backend

run_vectorbt_workflow(dataset, backend=None, config=None)
  - governed entry point
  - calls GovernedVectorbtInputAdapter → backend.run() → output normalization
  - returns VectorbtRunResult(prepared_dataset, backtest_result, artifact_bundle, registry_entry)
```

### Output Contract

`artifact_bundle`:
```json
{
  "schema_version": "1.0",
  "artifact_family": "vectorbt_backtest",
  "framework": "vectorbt",
  "framework_version": "0.26.2",
  "created_at": "<UTC iso timestamp>",
  "created_by": "<requested_by>",
  "dataset_summary": {
    "dataset_id": "<dataset_id>",
    "strategy_id": "<strategy_id>",
    "source_dataset_refs": ["<dataset ref>"],
    "source_strategy_spec_id": "<optional strategy spec ref>",
    "num_instruments": 2,
    "total_bars": 70,
    "bars_per_instrument": {
      "AAA": 35,
      "BBB": 35
    },
    "data_frequency": "daily",
    "instruments": ["AAA", "BBB"]
  },
  "backtest_config": {
    "version": "1.0.0",
    "short_window": 5,
    "long_window": 20,
    "init_cash": 100000.0,
    "fees": 0.001,
    "requested_by": "<requested_by>"
  },
  "per_instrument_metrics": {
    "AAA": {
      "total_return": <float>,
      "sharpe_ratio": <float>,
      "max_drawdown": <float>,
      "final_portfolio_value": <float>,
      "trade_count": <int>,
      "num_bars": <int>
    }
  },
  "aggregate_metrics": {
    "num_instruments": <int>,
    "mean_total_return": <float>,
    "mean_sharpe_ratio": <float>,
    "mean_max_drawdown": <float>,
    "total_trades": <int>
  },
  "governance": {
    "required_ohlcv_fields": ["open", "high", "low", "close", "volume"],
    "direct_live_influence": false,
    "output_type": "backtest_result",
    "lean_consumption": "scoring_only_not_direct_action",
    "notes": ["<backend note>"]
  },
  "registry_hints": {
    "artifact_type": "backtest_result",
    "artifact_state": "draft",
    "deployment_stage": "none",
    "source_dataset_refs": ["<dataset ref>"]
  }
}
```

`registry_entry`:
```json
{
  "artifact_type": "backtest_result",
  "strategy_id": "<strategy_id>",
  "version": "1.0.0",
  "artifact_state": "draft",
  "deployment_summary": {
    "current_stage": "none"
  },
  "created_at": "<artifact created_at>",
  "lineage": {
    "source_run_ids": ["<backend run id>"],
    "source_dataset_refs": ["<dataset ref>"],
    "source_strategy_spec_id": "<optional strategy spec ref>"
  },
  "storage_ref": {
    "backend": "object_store",
    "path": "research/vectorbt/<strategy_id>/<version>/artifact.json"
  },
  "checksum": "sha256:<artifact hash>",
  "producer_run_id": "<backend run id>",
  "aggregate_metrics": {
    "num_instruments": <int>,
    "mean_total_return": <float>,
    "mean_sharpe_ratio": <float>,
    "mean_max_drawdown": <float>,
    "total_trades": <int>
  },
  "metadata": {
    "framework": "vectorbt",
    "framework_version": "0.26.2",
    "backtest_backend": "stub_backtest | vectorbt_portfolio",
    "strategy": "ma_crossover",
    "short_window": <int>,
    "long_window": <int>,
    "num_instruments": <int>,
    "total_bars": <int>,
    "data_frequency": "daily"
  },
  "approved_at": null,
  "approver": null,
  "rollback_target": null
}
```

## 4. Runtime and Packaging Notes

- Base worker image: `python:3.11-slim`
- Default command: `python worker.py`
- Full vectorbt install requires `pip install -r services/research/vectorbt/requirements.txt`
- Smoke test with `StubVectorbtBackend` requires no vectorbt install (stub is pure Python)

Two execution modes are intentionally supported:

1. Stub backend for deterministic CI and local smoke tests
2. Real `VectorbtBackend` for dedicated research workers (`PANTHEON_VECTORBT_BACKEND=real`)

## 5. Smoke-Test Evidence

See `services/research/vectorbt/ACTIVATION_CRITERIA.md §Smoke-Test Plan` for the
full procedure. Current governed evidence summary:

| Step | Command | Pass Condition |
|---|---|---|
| 1 | `pip install -r services/research/vectorbt/requirements.txt` | zero errors |
| 2 | `python services/research/vectorbt/smoke_test.py` | zero exceptions; `artifact_state=draft` |
| 3 | `pytest services/research/vectorbt/test_adapter.py -v` | ≥10 tests pass |
| 4 | Record result in `integrations/vectorbt/smoke_test.md` | committed |
| 5 | Refresh Gate 2 evidence | `integrations/vectorbt/governance.md` committed |

## 6. What Advances the Status

| From | To | Gate |
|---|---|---|
| `not-started` | `version-pinned` | this document + `requirements.txt` committed (done) |
| `version-pinned` | `adapter-started` | adapter skeleton committed to `services/research/vectorbt/adapter/` |
| `adapter-started` | `smoke-tested` | `smoke_test.py` + `test_adapter.py` passing in CI |
| `smoke-tested` | `governed` | `governance.md` + `smoke_test.md` committed; evidence pack complete |

## 7. Evidence References

- version pin: `services/research/vectorbt/requirements.txt`
- activation criteria: `services/research/vectorbt/ACTIVATION_CRITERIA.md`
- governance overlay: `integrations/vectorbt/governance.md`
- smoke evidence: `integrations/vectorbt/smoke_test.md`
- maturity matrix: `RESEARCH_BACKEND_MATURITY_MATRIX.md`

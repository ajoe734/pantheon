# vectorbt Integration — Source Selection and Adapter Design

Last updated: 2026-04-17
Owner: OSS-NEXT-005 (Codex)
Reviewer: Claude
Status: version-pinned — execution-ready task family materialized
Implementation home (planned): `services/research/vectorbt/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `polakowo/vectorbt` |
| Package source | `https://pypi.org/project/vectorbt/` |
| Selected package | `vectorbt==0.26.2` |
| Version pin source | `services/research/vectorbt/requirements.txt` |
| Service dependency file | `services/research/vectorbt/requirements.txt` |
| Worker image (planned) | `services/research/vectorbt/Dockerfile` |
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

## 3. Planned Adapter Surface

The following adapter components are to be implemented in the follow-on task.
This section serves as the canonical design target for the implementation owner.

### `services/research/vectorbt/adapter/vectorbt_adapter.py`

```
GovernedVectorbtInputAdapter
  - accepts a StrategySpec + OHLCV data block
  - validates: instrument list non-empty, bar count ≥ 30, OHLCV fields present
  - rejects: NaN-heavy frames, missing close column, invalid date index
  - output: normalized signals dict ready for vbt.Portfolio.from_signals()

StubVectorbtBackend
  - accepts normalized signals dict
  - returns a deterministic dict of performance metrics (no real computation)
  - safe for CI and local smoke tests without the full vectorbt install

VectorbtBackend
  - wraps vbt.Portfolio.from_signals() or from_order_func()
  - activated when PANTHEON_VECTORBT_BACKEND=real
  - returns dict of performance metrics: total_return, sharpe_ratio, max_drawdown,
    win_rate, num_trades

run_vectorbt_workflow(strategy_spec, ohlcv_data, backend=None)
  - governed entry point
  - calls GovernedVectorbtInputAdapter → backend.run() → output normalization
  - returns (artifact_bundle, registry_entry)
```

### Output Contract

`artifact_bundle`:
```json
{
  "artifact_family": "vectorbt_backtest",
  "framework": "vectorbt",
  "framework_version": "0.26.2",
  "strategy_id": "<from StrategySpec>",
  "metrics": {
    "total_return": <float>,
    "sharpe_ratio": <float>,
    "max_drawdown": <float>,
    "win_rate": <float>,
    "num_trades": <int>
  },
  "governance": {
    "direct_live_influence": false,
    "lean_consumption": "scoring_only_not_direct_action"
  }
}
```

`registry_entry`:
```json
{
  "artifact_type": "backtest_result",
  "artifact_state": "draft",
  "deployment_summary": {
    "current_stage": "none"
  },
  "lineage": {
    "strategy_spec_ref": "<strategy_id>",
    "framework": "vectorbt",
    "framework_version": "0.26.2"
  }
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

## 5. Smoke-Test Plan

See `services/research/vectorbt/ACTIVATION_CRITERIA.md §Smoke-Test Plan` for the
full procedure. Summary:

| Step | Command | Pass Condition |
|---|---|---|
| 1 | `pip install -r services/research/vectorbt/requirements.txt` | zero errors |
| 2 | `python services/research/vectorbt/smoke_test.py` | zero exceptions; `artifact_state=draft` |
| 3 | `pytest services/research/vectorbt/test_adapter.py -v` | ≥10 tests pass |
| 4 | Record result in `integrations/vectorbt/smoke_test.md` | committed |

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
- governance overlay (planned): `integrations/vectorbt/governance.md`
- smoke procedure (planned): `integrations/vectorbt/smoke_test.md`
- maturity matrix: `RESEARCH_BACKEND_MATURITY_MATRIX.md`

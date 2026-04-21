# vectorbt Integration — Smoke Test

Last updated: 2026-04-18
Owner: OSS-GATE2-001 (Codex2)
Reviewer: Codex
Status: executable smoke path verified
Primary entrypoint: `python3 services/research/vectorbt/smoke_test.py`

## 1. Objective

Prove that the vectorbt row is backed by a runnable governed adapter path rather than a
design-only adapter proposal.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/research/vectorbt/`

Optional upstream smoke prerequisites:

- `pip install -r services/research/vectorbt/requirements.txt`
- `PANTHEON_VECTORBT_BACKEND=real` environment support when exercising the real backend

## 3. Canonical Commands

Deterministic local smoke:

```bash
python3 services/research/vectorbt/smoke_test.py
```

Optional upstream vectorbt smoke:

```bash
python3 services/research/vectorbt/smoke_test.py --backend real
```

Unit coverage:

```bash
python3 -m pytest services/research/vectorbt/test_adapter.py -q
```

## 4. What the Smoke Path Verifies

The smoke script builds a minimal governed OHLCV dataset and proves that:

1. two instruments with thirty-five bars each validate successfully
2. `run_vectorbt_workflow()` emits a governed `vectorbt_backtest`
3. the registry entry starts at `artifact_state = draft`
4. `deployment_summary.current_stage` remains `none`
5. `governance.direct_live_influence` is `false`
6. `governance.lean_consumption` is `scoring_only_not_direct_action`
7. the registry entry carries a `sha256:` checksum
8. aggregate performance metrics are produced for the backtest

## 5. Verified Result

Verified on 2026-04-18 with the default stub backend:

- command: `python3 services/research/vectorbt/smoke_test.py`
- backend: `stub_backtest`
- instruments: `2`
- total_bars: `70`
- registry_id: `vbt-backtest-smoke-strategy-1.0.0`
- artifact_type: `backtest_result`
- artifact_state: `draft`
- deployment_stage: `none`
- artifact_family: `vectorbt_backtest`
- framework: `vectorbt`
- direct_live_influence: `False`
- lean_consumption: `scoring_only_not_direct_action`
- checksum prefix: `sha256:`
- aggregate metrics:
  - `mean_max_drawdown = 0.007993`
  - `mean_sharpe_ratio = 1.14319`
  - `mean_total_return = 0.008743`
  - `num_instruments = 2`
  - `total_trades = 2`
- result: `assertions: OK`

Unit coverage result on 2026-04-18:

- command: `python3 -m pytest services/research/vectorbt/test_adapter.py -q`
- result: `28 passed, 5 subtests passed in 0.22s`

## 6. Acceptance

Treat the vectorbt row as smoke-proven when:

- the smoke command exits `0` with `assertions: OK`
- the workflow emits a governed `vectorbt_backtest`
- `artifact_state=draft` and `deployment_stage=none` are confirmed
- the registry entry carries a `sha256:` checksum
- unit coverage still passes

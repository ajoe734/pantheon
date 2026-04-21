# vectorbt Activation Criteria

Last updated: 2026-04-20
Owner: EXEC-OSS-VECTORBT-001 (Codex2)
Reviewer: Codex
Task: OSS-NEXT-005 baseline + EXEC-OSS-VECTORBT-001 closeout
Status: governed baseline implemented; smoke path and worker dispatch present

## Purpose

This file documents the entry gates for vectorbt and records the current
baseline now that the governed adapter, smoke path, and evidence pack exist.

vectorbt is the primary Pantheon backend for rapid strategy backtesting
and vectorized portfolio simulation. It does not replace LEAN as the live
execution engine; it is used exclusively inside the Research Plane for fast
iteration on strategy prototypes before a StrategySpec enters the formal
replication gate.

---

## Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `polakowo/vectorbt` |
| Package source | `https://pypi.org/project/vectorbt/` |
| Selected package | `vectorbt==0.26.2` |
| License | Apache 2.0 (open-source edition) |
| Rationale | vectorbt-pro is a commercial fork; use the Apache-licensed OSS edition |

Version pin source: `services/research/vectorbt/requirements.txt`

---

## Activation Gates

### Gate 1: Adapter Implementation (implemented)

The following components now exist and define the governed smoke-tested path:

1. `services/research/vectorbt/adapter/vectorbt_adapter.py`
   - `GovernedVectorbtInputAdapter` — validates and normalizes OHLCV input against
     the canonical StrategySpec schema before it reaches vectorbt
   - `StubVectorbtBackend` — deterministic stub for CI; runs no actual backtest
   - `VectorbtBackend` — real upstream runner using `vbt.Portfolio.from_signals()`
     or `vbt.Portfolio.from_order_func()`
   - `run_vectorbt_workflow()` — governed entry point that wires input adapter →
     backend → output normalization → artifact emission

2. `services/research/vectorbt/adapter/__init__.py`

3. `services/research/vectorbt/smoke_test.py`
   - imports `StubVectorbtBackend` only (no live network required)
   - feeds a minimal OHLCV stub dataset (≥2 instruments, ≥30 bars each)
   - asserts that `artifact_bundle` is emitted with:
     - `artifact_family = "vectorbt_backtest"`
     - `framework = "vectorbt"`
     - `governance.direct_live_influence = false`
     - `governance.lean_consumption = "scoring_only_not_direct_action"`
   - asserts that `registry_entry` contains:
     - `artifact_type = "backtest_result"`
     - `artifact_state = "draft"`
     - `deployment_summary.current_stage = "none"`

4. `services/research/vectorbt/test_adapter.py`
   - unit tests for `GovernedVectorbtInputAdapter` schema rejection
   - unit tests for output envelope correctness

5. `services/research/vectorbt/worker.py`
   - wraps `run_vectorbt_workflow()` for container dispatch
   - defaults to the sample governed dataset when `VECTORBT_DATASET_PATH` is not set

6. `services/research/vectorbt/examples/strategy_dataset_sample.json`
   - minimal governed OHLCV sample matching the StrategySpec input contract

### Gate 2: Evidence Pack (implemented)

1. `integrations/vectorbt/integration.md` — upstream source and packaging notes
2. `integrations/vectorbt/governance.md` — promotion, rollback, and governance policy
3. `integrations/vectorbt/smoke_test.md` — procedure and last-known-good result

---

## Governance Boundary (Invariants)

- vectorbt is a Research Plane tool only. It must not write to SignalStore, LEAN, or
  any live execution path directly.
- All OHLCV input must arrive via the `GovernedVectorbtInputAdapter`; raw market data
  frames must not bypass schema validation.
- All output artifacts enter the registry at `artifact_state = "draft"` and
  `deployment_summary.current_stage = "none"`. Promotion to `candidate` or beyond
  requires the same registry promotion gate as all other research artifacts.
- The `StubVectorbtBackend` is the only CI-safe backend. The real `VectorbtBackend`
  must be gated behind an environment flag (`PANTHEON_VECTORBT_BACKEND=real`).

---

## Smoke-Test Plan

The smoke test for vectorbt follows the same structure as Qlib and TRL:

| Step | Description |
|---|---|
| 1 | Install `services/research/vectorbt/requirements.txt` in an isolated environment when exercising the real backend |
| 2 | Run `python3 services/research/vectorbt/smoke_test.py` with `StubVectorbtBackend` |
| 3 | Assert zero exceptions; assert `artifact_state = "draft"` in emitted registry_entry |
| 4 | Run `python3 -m pytest services/research/vectorbt/test_adapter.py -v` |
| 5 | Assert all unit tests pass (target: ≥10 tests) |
| 6 | Record last-known-good result and commit to `integrations/vectorbt/smoke_test.md` |
| 7 | Run `python3 services/research/vectorbt/worker.py` to verify container entrypoint and sample dataset path |

CI integration: `.github/workflows/syntax-tests.yml` now contains a `vectorbt-smoke`
job that runs the stub smoke script and adapter tests on Python 3.11.

---

## Activation Owner

- Materialization owner: Codex
- Governed baseline closeout owner: Codex2
- Reviewer: Codex
- Task family: OSS-NEXT-005 baseline + EXEC-OSS-VECTORBT-001 closeout

---

## What This Document Does Not Cover

This document covers the governed baseline gate (source selection, version pin,
adapter, smoke path, worker entrypoint, and evidence pack). It does not cover:

- Production activation criteria (no equivalent to Qlib's 50-instrument / 2-year gate,
  since vectorbt is a prototyping tool, not a production data pipeline)
- Multi-asset portfolio optimization workflows (deferred until the adapter baseline exists)
- Integration with OpenClaw orchestration (deferred until the adapter is smoke-tested)

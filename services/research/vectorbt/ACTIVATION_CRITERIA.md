# vectorbt Activation Criteria

Last updated: 2026-04-17
Owner: OSS-NEXT-005 (Codex)
Reviewer: Claude
Task: OSS-NEXT-005 — vectorbt task materialization
Status: source-selected / version-pinned

## Purpose

This file documents the entry gates that must be satisfied before vectorbt
transitions from `version-pinned` to `smoke-tested` and then to `governed`.

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

### Gate 1: Adapter Implementation (blocks `adapter-started` → `smoke-tested`)

The following components must exist and pass CI before vectorbt is considered `smoke-tested`:

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

6. `services/research/vectorbt/examples/strategy_dataset_sample.json`
   - minimal governed OHLCV sample matching the StrategySpec input contract

### Gate 2: Evidence Pack (blocks `smoke-tested` → `governed`)

1. `integrations/vectorbt/integration.md` — upstream source and packaging notes (exists)
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
| 1 | Install `services/research/vectorbt/requirements.txt` in an isolated environment |
| 2 | Run `python services/research/vectorbt/smoke_test.py` with `StubVectorbtBackend` |
| 3 | Assert zero exceptions; assert `artifact_state = "draft"` in emitted registry_entry |
| 4 | Run `python -m pytest services/research/vectorbt/test_adapter.py -v` |
| 5 | Assert all unit tests pass (target: ≥10 tests) |
| 6 | Record last-known-good result and commit to `integrations/vectorbt/smoke_test.md` |

CI integration: add to `.github/workflows/syntax-tests.yml` or a new `vectorbt-smoke`
workflow job under the OSS research matrix.

---

## Activation Owner

- Materialization owner: Codex
- Follow-on implementation owner: to be assigned
- Reviewer: Claude
- Task family: OSS-NEXT-005 (this document) + follow-on implementation task

---

## What This Document Does Not Cover

This document covers the materialization gate (source selection, version pin, adapter
design, smoke-test plan). It does not cover:

- Production activation criteria (no equivalent to Qlib's 50-instrument / 2-year gate,
  since vectorbt is a prototyping tool, not a production data pipeline)
- Multi-asset portfolio optimization workflows (deferred until the adapter baseline exists)
- Integration with OpenClaw orchestration (deferred until the adapter is smoke-tested)

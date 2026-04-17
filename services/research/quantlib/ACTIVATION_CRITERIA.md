# QuantLib Activation Criteria

Last updated: 2026-04-17
Owner: OSS-NEXT-007 (Claude)
Reviewer: Codex2
Task: OSS-NEXT-007 — QuantLib task materialization
Status: source-selected / version-pinned

## Purpose

This file documents the entry gates that must be satisfied before QuantLib
transitions from `version-pinned` to `smoke-tested` and then to `governed`.

QuantLib is the primary Pantheon backend for derivatives pricing and risk
analytics inside the Research Plane. It is used to compute options pricing
(Black-Scholes, Heston), fixed income analytics (yield curve construction,
duration, convexity), and Greeks calculation before any resulting artifact
enters the canonical registry path.

---

## Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `lballabio/QuantLib` (Python bindings: `QuantLib-Python`) |
| Package source | `https://pypi.org/project/QuantLib-Python/` |
| Selected package | `QuantLib-Python==1.18` |
| License | BSD License |
| Rationale | Industry-standard open-source quantitative finance library with stable support for options pricing, fixed income analytics, yield curve construction, and Greeks computation |

Version pin source: `services/research/quantlib/requirements.txt`

---

## Pantheon Use-Case Binding

QuantLib is scoped to Research Plane analytics only. The first Pantheon
execution-ready family is:

1. **Derivatives pricing and risk analytics**
   - compute option prices (European, American) using Black-Scholes or Heston models
   - calculate Greeks (delta, gamma, vega, theta, rho) for governed option positions
   - construct yield curves and compute fixed income analytics (duration, convexity,
     DV01) from governed rate datasets

Accepted role:

- produces governed research artifacts such as `pricing_report`,
  `greeks_bundle`, or `yield_curve_snapshot`
- consumes only governed market data snapshots that already passed the research
  ingestion and replication gates
- can inform downstream scoring or review workflows after registry promotion

Rejected role:

- direct writes into live execution, LEAN, or SignalStore
- direct use of raw market data outside governed lineage tracking
- direct authority over deployment stages or promotion truth
- real-time streaming pricing outside governed research artifact boundaries

---

## Activation Gates

### Gate 1: Adapter Implementation (blocks `version-pinned` → `smoke-tested`)

The following components must exist and pass CI before QuantLib is
considered `smoke-tested`:

1. `services/research/quantlib/adapter/quantlib_adapter.py`
   - `GovernedQuantLibInputAdapter` — validates governed market data snapshots
     (spot price, vol surface, rate curve) before they reach QuantLib
   - `StubQuantLibBackend` — deterministic CI-safe backend with no external
     market data dependency; returns fixed prices and Greeks
   - `QuantLibBackend` — real backend wrapping the first approved model set
     (Black-Scholes European pricing, yield curve construction)
   - `run_quantlib_workflow()` — governed entry point that wires input
     adapter → backend → normalized artifact emission

2. `services/research/quantlib/adapter/__init__.py`

3. `services/research/quantlib/smoke_test.py`
   - uses `StubQuantLibBackend` by default
   - feeds a minimal governed market data snapshot with:
     - ≥1 equity option spec (spot, strike, maturity, vol, rate)
     - ≥1 fixed income instrument (bond spec with coupon, maturity, rate)
   - asserts that `artifact_bundle` is emitted with:
     - `artifact_family = "pricing_report"`
     - `framework = "quantlib"`
     - `governance.direct_live_influence = false`
     - `governance.lean_consumption = "research_only_not_direct_action"`
   - asserts that `registry_entry` contains:
     - `artifact_type = "research_report"`
     - `artifact_state = "draft"`
     - `deployment_summary.current_stage = "none"`

4. `services/research/quantlib/test_adapter.py`
   - unit tests for schema rejection and canonical output envelope
   - unit tests for deterministic price/Greeks packaging and diagnostics fields

5. `services/research/quantlib/worker.py`
   - wraps `run_quantlib_workflow()` for container dispatch

6. `services/research/quantlib/examples/pricing_dataset_sample.json`
   - minimal governed sample covering options pricing and fixed income inputs

### Gate 2: Evidence Pack (blocks `smoke-tested` → `governed`)

1. `integrations/quantlib/integration.md` — upstream source and packaging
   notes (exists)
2. `integrations/quantlib/governance.md` — promotion, rollback, and research
   governance policy
3. `integrations/quantlib/smoke_test.md` — procedure and last-known-good
   result

---

## Governance Boundary (Invariants)

- QuantLib is a Research Plane tool only. It must never write directly to
  execution-plane systems.
- Every market data snapshot must arrive through `GovernedQuantLibInputAdapter`;
  raw pandas frames or unvalidated vol surfaces are not a governed interface.
- The first implementation lane must emit non-executable research artifacts at
  `artifact_state = "draft"` and `deployment_summary.current_stage = "none"`.
- CI and default local verification must use `StubQuantLibBackend`; the real
  backend must be gated behind `PANTHEON_QUANTLIB_BACKEND=real`.

---

## Smoke-Test Plan

| Step | Description |
|---|---|
| 1 | Install `services/research/quantlib/requirements.txt` in an isolated environment |
| 2 | Run `python services/research/quantlib/smoke_test.py` with `StubQuantLibBackend` |
| 3 | Assert zero exceptions and canonical registry output (`artifact_state = "draft"`) |
| 4 | Run `python -m pytest services/research/quantlib/test_adapter.py -v` |
| 5 | Assert all unit tests pass (target: ≥10 tests) |
| 6 | Record last-known-good result in `integrations/quantlib/smoke_test.md` |

CI integration: add a `quantlib-smoke` job to the OSS research matrix after
the adapter baseline exists.

---

## Activation Owner

- Implementation owner: to be assigned
- Reviewer: Codex2
- Task family: OSS-NEXT-007 (this document) + follow-on implementation task

---

## What This Document Does Not Cover

This document covers the materialization gate (use-case binding, source
selection, version pin, adapter design, smoke-test plan). It does not cover:

- production approval criteria for any derivatives-driven artifact consumer
- real-time streaming pricing or intraday Greeks computation
- OpenClaw orchestration for QuantLib jobs before the adapter baseline exists

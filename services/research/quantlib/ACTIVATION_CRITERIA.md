# QuantLib Activation Criteria

Last updated: 2026-04-21
Owner: EXEC-OSS-QUANTLIB-001 (Codex)
Reviewer: Claude
Task: EXEC-OSS-QUANTLIB-001 — QuantLib execution readiness closeout
Status: governed / evidence-complete

## Purpose

This file documents the entry gates that were required to move QuantLib from
`version-pinned` to `smoke-tested` and then to `governed`, plus the concrete
surface that now satisfies those gates.

QuantLib is the primary Pantheon backend for derivatives pricing and risk
analytics inside the Research Plane. It is used to compute options pricing
(Black-Scholes / binomial CRR proxy for American options), fixed-income
analytics (yield-curve summaries, duration, convexity, DV01), and Greeks before
any resulting artifact enters the canonical registry path.

---

## Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `lballabio/QuantLib` (Python bindings: `QuantLib-Python`) |
| Package source | `https://pypi.org/project/QuantLib-Python/` |
| Selected package | `QuantLib-Python==1.18` |
| License | BSD License |
| Rationale | Mature OSS derivatives and fixed-income analytics library with stable support for governed pricing, Greeks, and bond analytics |

Version pin source: `services/research/quantlib/requirements.txt`

---

## Pantheon Use-Case Binding

QuantLib is scoped to Research Plane analytics only. The first Pantheon
execution-ready family is:

1. **Derivatives pricing and risk analytics**
   - compute European and American option prices from governed instrument specs
   - calculate Greeks (delta, gamma, vega, theta, rho) for governed option positions
   - compute fixed-income analytics (duration, convexity, DV01) and summarize curve points from governed bond inputs

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

### Gate 1: Adapter Implementation (completed: `version-pinned` → `smoke-tested`)

The following components now exist repo-locally and support the smoke-tested
baseline:

1. `services/research/quantlib/adapter/quantlib_adapter.py`
   - `GovernedQuantLibInputAdapter` — validates governed market data snapshots
     (spot, vol surface proxy, rate curve proxy, lineage refs) before they reach
     any backend
   - `StubQuantLibBackend` — deterministic CI-safe backend with no external
     market data dependency; returns fixed option pricing / Greeks and
     fixed-income diagnostics
   - `QuantLibBackend` — real backend wrapping the first approved model set
     (analytic European pricing, binomial CRR American pricing, and fixed-rate
     bond analytics)
   - `run_quantlib_workflow()` — governed entry point wiring the adapter →
     backend → normalized artifact emission

2. `services/research/quantlib/adapter/__init__.py`

3. `services/research/quantlib/smoke_test.py`
   - uses `StubQuantLibBackend` by default
   - feeds a minimal governed market snapshot with:
     - ≥1 option spec
     - ≥1 bond spec
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
   - unit tests for deterministic stub packaging and real-backend pricing / Greek parity when `QuantLib` bindings are present

5. `services/research/quantlib/worker.py`
   - wraps `run_quantlib_workflow()` for container dispatch
   - defaults to the governed sample dataset when `QUANTLIB_DATASET_PATH` is not set

6. `services/research/quantlib/examples/pricing_dataset_sample.json`
   - governed sample covering options pricing and fixed-income inputs for smoke
     mode and worker fallback execution

### Gate 2: Evidence Pack (completed: `smoke-tested` → `governed`)

1. `integrations/quantlib/integration.md` — upstream source and packaging notes
2. `integrations/quantlib/governance.md` — promotion, rollback, and research governance policy
3. `integrations/quantlib/smoke_test.md` — procedure and last-known-good result

---

## Governance Boundary (Invariants)

- QuantLib is a Research Plane tool only. It must never write directly to
  execution-plane systems.
- Every market data snapshot must arrive through `GovernedQuantLibInputAdapter`;
  raw pandas frames or ad-hoc pricing inputs are not a governed interface.
- The governed baseline emits non-executable research artifacts at
  `artifact_state = "draft"` and `deployment_summary.current_stage = "none"`.
- CI and default local verification use `StubQuantLibBackend`; the real backend
  remains gated behind `PANTHEON_QUANTLIB_BACKEND=real`.

---

## Smoke-Test Plan

| Step | Description |
|---|---|
| 1 | Install `services/research/quantlib/requirements.txt` in an isolated environment when exercising the real backend |
| 2 | Run `python3 services/research/quantlib/smoke_test.py` with `StubQuantLibBackend` |
| 3 | Assert zero exceptions and canonical registry output (`artifact_state = "draft"`) |
| 4 | Run `python3 -m pytest services/research/quantlib/test_adapter.py -v` |
| 5 | Assert unit coverage still passes (current default-workspace baseline: `17 passed, 1 skipped`) |
| 6 | Record last-known-good result in `integrations/quantlib/smoke_test.md` |
| 7 | Run `python3 services/research/quantlib/worker.py` to verify container entrypoint and sample dataset path |

CI integration next step: add a `quantlib-smoke` job to the shared OSS research
matrix when the matrix refresh runs. The local governed baseline and evidence
pack already exist.

---

## Activation Owner

- Implementation owner: Codex
- Reviewer: Claude
- Task family: EXEC-OSS-QUANTLIB-001

---

## What This Document Does Not Cover

This document covers the governed baseline gate (source selection, version pin,
adapter, smoke path, worker entrypoint, sample dataset, and evidence pack). It
does not cover:

- production approval criteria for any derivatives-driven artifact consumer
- real-time streaming pricing or intraday Greeks computation
- OpenClaw orchestration for QuantLib jobs before a dedicated runtime contract
  is added

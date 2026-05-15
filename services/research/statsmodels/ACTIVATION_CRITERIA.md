# statsmodels Activation Criteria

Last updated: 2026-04-21
Owner: EXEC-OSS-STATSMODELS-001 (Codex2)
Reviewer: Codex
Task: EXEC-OSS-STATSMODELS-001 — statsmodels execution readiness closeout
Status: governed / evidence-complete

## Purpose

This file documents the entry gates that were required to move statsmodels from
`version-pinned` to `smoke-tested` and then to `governed`, plus the concrete
surface that now satisfies those gates.

statsmodels is the primary Pantheon backend for econometrics and regime
research inside the Research Plane. It is used to test market structure
hypotheses such as cointegration, VAR-based macro propagation, and
regime-switching inference before any resulting artifact is allowed to enter
the canonical registry path.

---

## Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `statsmodels/statsmodels` |
| Package source | `https://pypi.org/project/statsmodels/` |
| Selected package | `statsmodels==0.14.2` |
| License | BSD License |
| Rationale | Mature OSS econometrics package with stable support for OLS/GLM, ARIMA/SARIMAX, VAR/VECM, cointegration tests, and Markov-switching models |

Version pin source: `services/research/statsmodels/requirements.txt`

---

## Pantheon Use-Case Binding

statsmodels is scoped to Research Plane analytics only. The first Pantheon
execution-ready family is:

1. **Regime and market-structure inference**
   - detect cointegration candidates across governed instrument baskets
   - fit VAR/VECM relationships between macro proxies and strategy factors
   - estimate Markov-switching or state-space regime labels for downstream
     research artifacts

Accepted role:

- produces governed research artifacts such as `regime_report`,
  `cointegration_candidate_set`, or `factor_diagnostics`
- consumes only governed historical datasets that already passed the research
  ingestion and replication gates
- can inform downstream scoring or review workflows after registry promotion

Rejected role:

- direct writes into live execution, LEAN, or SignalStore
- direct use of raw market data outside governed lineage tracking
- direct authority over deployment stages or promotion truth

---

## Activation Gates

### Gate 1: Adapter Implementation (completed: `version-pinned` → `smoke-tested`)

The following components now exist repo-locally and support the smoke-tested
baseline:

1. `services/research/statsmodels/adapter/statsmodels_adapter.py`
   - `GovernedStatsmodelsInputAdapter` — validates governed time-series and
     factor datasets before they reach statsmodels, including numeric-only
     observations, equal-length alignment, non-finite-value rejection, and
     `metadata.governed=True`
   - `StubStatsmodelsBackend` — deterministic CI-safe backend with no external
     data dependency
   - `StatsmodelsBackend` — real backend wrapping the first approved model set
     (`coint`, `VECM`, `VAR`, or `MarkovRegression`)
   - `run_statsmodels_workflow()` — governed entry point that wires input
     adapter → backend → normalized artifact emission

2. `services/research/statsmodels/adapter/__init__.py`

3. `services/research/statsmodels/smoke_test.py`
   - uses `StubStatsmodelsBackend` by default
   - feeds a minimal governed dataset with:
     - ≥2 price series for cointegration
     - ≥1 macro/factor series for regime diagnostics
   - asserts that `artifact_bundle` is emitted with:
     - `artifact_family = "regime_report"`
     - `framework = "statsmodels"`
     - `governance.direct_live_influence = false`
     - `governance.lean_consumption = "research_only_not_direct_action"`
   - asserts that `registry_entry` contains:
     - `artifact_type = "research_report"`
     - `artifact_state = "draft"`
     - `deployment_summary.current_stage = "none"`

4. `services/research/statsmodels/test_adapter.py`
   - unit tests for schema rejection and canonical output envelope
   - unit tests for deterministic regime-label packaging and diagnostics fields

5. `services/research/statsmodels/worker.py`
   - wraps `run_statsmodels_workflow()` for container dispatch

6. `services/research/statsmodels/examples/regime_dataset_sample.json`
   - minimal governed sample covering cointegration and regime-analysis inputs

### Gate 2: Evidence Pack (completed: `smoke-tested` → `governed`)

1. `integrations/statsmodels/integration.md` — upstream source and packaging
   notes (exists)
2. `integrations/statsmodels/governance.md` — promotion, rollback, and research
   governance policy
3. `integrations/statsmodels/smoke_test.md` — procedure and last-known-good
   result

---

## Governance Boundary (Invariants)

- statsmodels is a Research Plane tool only. It must never write directly to
  execution-plane systems.
- Every dataset must arrive through `GovernedStatsmodelsInputAdapter`; raw
  pandas frames are not a governed interface.
- The first implementation lane must emit non-executable research artifacts at
  `artifact_state = "draft"` and `deployment_summary.current_stage = "none"`.
- CI and default local verification must use `StubStatsmodelsBackend`; the real
  backend must be gated behind `PANTHEON_STATSMODELS_BACKEND=real`.

---

## Smoke-Test Plan

| Step | Description |
|---|---|
| 1 | Install `services/research/statsmodels/requirements.txt` in an isolated environment |
| 2 | Run `python services/research/statsmodels/smoke_test.py` with `StubStatsmodelsBackend` |
| 3 | Assert zero exceptions and canonical registry output (`artifact_state = "draft"`) |
| 4 | Run `python -m pytest services/research/statsmodels/test_adapter.py -v` |
| 5 | Assert all unit tests pass (target: ≥10 tests) |
| 6 | Record last-known-good result in `integrations/statsmodels/smoke_test.md` |

CI integration next step: add a `statsmodels-smoke` job to the OSS research
matrix when the shared OSS research matrix refresh runs. The local governed
baseline and evidence pack already exist.

---

## Activation Owner

- Implementation owner: Codex2
- Reviewer: Codex
- Task family: EXEC-OSS-STATSMODELS-001

---

## What This Document Does Not Cover

This document covers the materialization gate (use-case binding, source
selection, version pin, adapter design, smoke-test plan). It does not cover:

- production approval criteria for any regime-driven artifact consumer
- real-time econometric execution or online state estimation
- OpenClaw orchestration for statsmodels jobs before a dedicated runtime
  contract is added

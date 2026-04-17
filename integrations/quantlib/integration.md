# QuantLib Integration — Derivatives Pricing and Risk Analytics Baseline

Last updated: 2026-04-17
Owner: OSS-NEXT-007 (Claude)
Reviewer: Codex2
Status: version-pinned — execution-ready task family materialized
Implementation home: `services/research/quantlib/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `lballabio/QuantLib` (Python bindings: `QuantLib-Python`) |
| Package source | `https://pypi.org/project/QuantLib-Python/` |
| Version pin | `QuantLib-Python==1.18` |
| Version pin source | `services/research/quantlib/requirements.txt` |
| Service dependency file | `services/research/quantlib/requirements.txt` |

Pantheon consumes QuantLib as a pinned Python package. It does not vendor
QuantLib source and does not treat QuantLib as an authority for registry
lifecycle, deployment, or execution routing.

## 2. Pantheon Adapter Mode

QuantLib is scoped to governed derivatives pricing and risk analytics only.

Accepted mode:

- governed market data snapshots (spot, vol surface, rate curve) are validated
  before QuantLib sees them
- QuantLib runs inside `services/research/quantlib/`
- the adapter emits non-executable research artifacts and registry-ready entries
- the default smoke path will use a deterministic stub backend

Rejected mode:

- direct writes into live execution or signal-routing systems
- raw upstream market data bypassing lineage and schema validation
- direct deployment authority based on QuantLib outputs alone

## 3. First Approved Use Cases

The first implementation lane is limited to research artifacts for:

1. European and American options pricing via Black-Scholes and Heston models
2. Greeks computation (delta, gamma, vega, theta, rho) for governed option instruments
3. Yield curve construction and fixed income analytics (duration, convexity, DV01)

These outputs are research evidence, not live actions.

## 4. Materialized Local Surface

The repo now contains the baseline needed to stop treating QuantLib as
planning debt only:

- `services/research/quantlib/ACTIVATION_CRITERIA.md`
- `services/research/quantlib/requirements.txt`
- `integrations/quantlib/integration.md`

The adapter, worker, smoke test, and governance overlay are intentionally
deferred to the follow-on implementation slice.

## 5. Next Implementation Gate

Before QuantLib can move from `version-pinned` to `smoke-tested`, Pantheon
must add:

- `GovernedQuantLibInputAdapter`
- `StubQuantLibBackend`
- `QuantLibBackend`
- `run_quantlib_workflow()`
- smoke and unit tests
- governance and smoke evidence docs

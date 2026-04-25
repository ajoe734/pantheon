# QuantLib Integration — Governed Derivatives Pricing and Risk Analytics

Last updated: 2026-04-21
Owner: EXEC-OSS-QUANTLIB-001 (Codex)
Reviewer: Claude
Status: governed — smoke and governance evidence committed
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

The repo now contains the governed surface required for Pantheon to treat
QuantLib as an active OSS integration:

- `services/research/quantlib/ACTIVATION_CRITERIA.md`
- `services/research/quantlib/requirements.txt`
- `services/research/quantlib/adapter/`
- `services/research/quantlib/smoke_test.py`
- `services/research/quantlib/test_adapter.py`
- `services/research/quantlib/worker.py`
- `services/research/quantlib/examples/pricing_dataset_sample.json`
- `integrations/quantlib/integration.md`
- `integrations/quantlib/governance.md`
- `integrations/quantlib/smoke_test.md`

Implemented governed workflow surface:

- `GovernedQuantLibInputAdapter`
- `StubQuantLibBackend`
- `QuantLibBackend`
- `run_quantlib_workflow()`

## 5. Verified Smoke and Test Status

The current governed claim is backed by reproducible local evidence:

- smoke command: `python3 services/research/quantlib/smoke_test.py`
- latest local verification: `2026-04-21`
- smoke result: passed with governed `pricing_report` output
- unit coverage: `python3 -m pytest services/research/quantlib/test_adapter.py -q`
- latest default-workspace result: `17 passed, 1 skipped`
- worker entrypoint: `python3 services/research/quantlib/worker.py`
- latest worker verification: `2026-04-21` (sample dataset fallback path)
- recorded real-backend rerun: `2026-04-17`, passing with `18 passed`

## 6. What Advances the Status

| From | To | Gate |
|---|---|---|
| `version-pinned` | `smoke-tested` | adapter + smoke path + unit coverage landed |
| `smoke-tested` | `governed` | `governance.md` + `smoke_test.md` committed; evidence pack complete |

## 7. Evidence References

- version pin: `services/research/quantlib/requirements.txt`
- activation criteria: `services/research/quantlib/ACTIVATION_CRITERIA.md`
- governance overlay: `integrations/quantlib/governance.md`
- smoke evidence: `integrations/quantlib/smoke_test.md`

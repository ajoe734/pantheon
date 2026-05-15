# statsmodels Integration — Governed Econometrics and Regime Research

Last updated: 2026-04-21
Owner: OSS-GATE2-001 (Codex2)
Reviewer: Codex
Status: governed — smoke and governance evidence committed
Implementation home: `services/research/statsmodels/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `statsmodels/statsmodels` |
| Package source | `https://pypi.org/project/statsmodels/` |
| Version pin | `statsmodels==0.14.2` |
| Version pin source | `services/research/statsmodels/requirements.txt` |
| Service dependency file | `services/research/statsmodels/requirements.txt` |

Pantheon consumes statsmodels as a pinned Python package. It does not vendor
statsmodels source and does not treat statsmodels as an authority for registry
lifecycle, deployment, or execution routing.

## 2. Pantheon Adapter Mode

statsmodels is scoped to governed econometrics and regime analysis only.

Accepted mode:

- governed historical datasets are validated before statsmodels sees them
- statsmodels runs inside `services/research/statsmodels/`
- the adapter emits non-executable research artifacts and registry-ready entries
- the default smoke path will use a deterministic stub backend

Rejected mode:

- direct writes into live execution or signal-routing systems
- raw upstream data bypassing lineage and schema validation
- direct deployment authority based on statsmodels outputs alone

## 3. First Approved Use Cases

The first implementation lane is limited to research artifacts for:

1. cointegration screening over governed instrument baskets
2. VAR/VECM diagnostics over macro and factor series
3. regime classification or transition diagnostics using Markov-switching models

These outputs are research evidence, not live actions.

## 4. Materialized Local Surface

The repo now contains the governed surface required for Pantheon to treat
statsmodels as an active OSS integration rather than planning debt:

- `services/research/statsmodels/ACTIVATION_CRITERIA.md`
- `services/research/statsmodels/requirements.txt`
- `services/research/statsmodels/adapter/statsmodels_adapter.py`
- `services/research/statsmodels/smoke_test.py`
- `services/research/statsmodels/test_adapter.py`
- `services/research/statsmodels/worker.py`
- `services/research/statsmodels/examples/regime_dataset_sample.json`
- `integrations/statsmodels/integration.md`
- `integrations/statsmodels/governance.md`
- `integrations/statsmodels/smoke_test.md`

Implemented governed workflow surface:

- `GovernedStatsmodelsInputAdapter`
- `StubStatsmodelsBackend`
- `StatsmodelsBackend`
- `run_statsmodels_workflow()`

## 5. Verified Smoke and Test Status

The current governed claim is backed by reproducible local evidence:

- smoke command: `python3 services/research/statsmodels/smoke_test.py`
- worker command: `python3 services/research/statsmodels/worker.py`
- latest local verification: `2026-04-21`
- smoke result: passed; all three analysis paths present
- unit coverage: `python3 -m pytest services/research/statsmodels/test_adapter.py -q`
- latest unit result: `20 passed`

## 6. What Advances the Status

| From | To | Gate |
|---|---|---|
| `version-pinned` | `smoke-tested` | adapter + smoke path + unit coverage landed |
| `smoke-tested` | `governed` | `governance.md` + `smoke_test.md` committed; evidence pack complete |

## 7. Evidence References

- version pin: `services/research/statsmodels/requirements.txt`
- activation criteria: `services/research/statsmodels/ACTIVATION_CRITERIA.md`
- governance overlay: `integrations/statsmodels/governance.md`
- smoke evidence: `integrations/statsmodels/smoke_test.md`

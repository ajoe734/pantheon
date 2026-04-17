# statsmodels Integration — Econometrics and Regime Research Baseline

Last updated: 2026-04-17
Owner: OSS-NEXT-006 (Codex2)
Reviewer: Codex
Status: version-pinned — execution-ready task family materialized
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

The repo now contains the baseline needed to stop treating statsmodels as
planning debt only:

- `services/research/statsmodels/ACTIVATION_CRITERIA.md`
- `services/research/statsmodels/requirements.txt`
- `integrations/statsmodels/integration.md`

The adapter, worker, smoke test, and governance overlay are intentionally
deferred to the follow-on implementation slice.

## 5. Next Implementation Gate

Before statsmodels can move from `version-pinned` to `smoke-tested`, Pantheon
must add:

- `GovernedStatsmodelsInputAdapter`
- `StubStatsmodelsBackend`
- `StatsmodelsBackend`
- `run_statsmodels_workflow()`
- smoke and unit tests
- governance and smoke evidence docs

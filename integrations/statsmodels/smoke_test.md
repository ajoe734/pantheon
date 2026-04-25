# statsmodels Integration — Smoke Test

Last updated: 2026-04-21
Owner: OSS-GATE2-001 (Codex2)
Reviewer: Codex
Status: executable smoke path verified
Primary entrypoint: `python3 services/research/statsmodels/smoke_test.py`

## 1. Objective

Prove that the statsmodels row is backed by a runnable governed adapter path rather than a
status-only checklist claim.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/research/statsmodels/`

Optional upstream smoke prerequisites:

- `pip install -r services/research/statsmodels/requirements.txt`

## 3. Canonical Commands

Deterministic local smoke:

```bash
python3 services/research/statsmodels/smoke_test.py
```

Unit coverage:

```bash
python3 -m pytest services/research/statsmodels/test_adapter.py -q
```

## 4. What the Smoke Path Verifies

The smoke script builds a minimal governed dataset and proves that:

1. two price series plus one factor series parse into `GovernedDataset`
2. `run_statsmodels_workflow()` emits a governed `regime_report`
3. the registry entry starts at `artifact_state = draft`
4. `deployment_summary.current_stage` remains `none`
5. `governance.direct_live_influence` is `false`
6. `governance.lean_consumption` is `research_only_not_direct_action`
7. all three baseline analysis paths are present: `cointegration`, `var_vecm`, `markov_switching`

## 5. Verified Result

Verified on 2026-04-18 with the default stub backend:

- command: `python3 services/research/statsmodels/smoke_test.py`
- dataset shape: `2` price series, `1` factor series
- artifact_family: `regime_report`
- artifact_state: `draft`
- deployment_stage: `none`
- direct_live_influence: `False`
- lean_consumption: `research_only_not_direct_action`
- analysis paths: `cointegration`, `var_vecm`, `markov_switching`
- result: `SMOKE TEST PASSED`

Revalidated on 2026-04-21 with the default stub backend and worker entrypoint:

- command: `python3 services/research/statsmodels/smoke_test.py`
- worker command: `python3 services/research/statsmodels/worker.py`
- result: smoke passed and worker emitted governed draft artifact metadata

Unit coverage result on 2026-04-21:

- command: `python3 -m pytest services/research/statsmodels/test_adapter.py -q`
- result: `20 passed in 0.92s`
- note: deprecation warning from `datetime.utcnow()` was removed during revalidation; no known contract warnings remain in the governed baseline

## 6. Acceptance

Treat the statsmodels row as smoke-proven when:

- the smoke command exits `0`
- the workflow emits a governed `regime_report`
- `artifact_state=draft` and `deployment_stage=none` are confirmed
- all three analysis paths are present
- unit coverage still passes

# QuantLib Integration — Smoke Test

Last updated: 2026-04-21
Owner: EXEC-OSS-QUANTLIB-001 (Codex)
Reviewer: Claude
Status: executable smoke path verified
Primary entrypoint: `python3 services/research/quantlib/smoke_test.py`

## 1. Objective

Prove that the QuantLib row is backed by a runnable governed adapter path rather than a
planning-only pricing note.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/research/quantlib/`

Optional upstream smoke prerequisites:

- `pip install -r services/research/quantlib/requirements.txt`
- `QuantLib` Python bindings available when using `--backend real`

## 3. Canonical Commands

Deterministic local smoke:

```bash
python3 services/research/quantlib/smoke_test.py
```

Optional upstream QuantLib smoke:

```bash
python3 services/research/quantlib/smoke_test.py --backend real
```

Unit coverage:

```bash
python3 -m pytest services/research/quantlib/test_adapter.py -q
```

Worker entrypoint:

```bash
python3 services/research/quantlib/worker.py
```

## 4. What the Smoke Path Verifies

The smoke script builds a governed market snapshot and proves that:

1. one governed option spec and one governed bond spec validate successfully
2. `run_quantlib_workflow()` emits a governed `pricing_report`
3. the registry entry starts at `artifact_state = draft`
4. `deployment_summary.current_stage` remains `none`
5. `governance.direct_live_influence` is `false`
6. `governance.lean_consumption` is `research_only_not_direct_action`
7. both pricing branches are populated: `options_pricing` and `fixed_income`

## 5. Verified Result

Verified on 2026-04-21 with the default stub backend:

- command: `python3 services/research/quantlib/smoke_test.py`
- artifact_family: `pricing_report`
- framework: `quantlib`
- artifact_state: `draft`
- deployment_stage: `none`
- direct_live_influence: `False`
- lean_consumption: `research_only_not_direct_action`
- option_count: `1`
- bond_count: `1`
- result: `assertions: OK`

Unit coverage result on 2026-04-21:

- command: `python3 -m pytest services/research/quantlib/test_adapter.py -q`
- result: `17 passed, 1 skipped in 0.16s`
- note: the skipped test is the real-backend path when local `QuantLib` bindings are unavailable in the default workspace

Worker verification result on 2026-04-21:

- command: `python3 services/research/quantlib/worker.py`
- dataset source: sample fallback (`services/research/quantlib/examples/pricing_dataset_sample.json`)
- artifact_family: `pricing_report`
- artifact_state: `draft`
- deployment_stage: `none`
- result_keys: `["fixed_income", "options_pricing"]`

Previously recorded real-backend evidence from 2026-04-17 remains valid:

- `PYTHONPATH=/tmp/oss-impl-002-site python3 -m pytest services/research/quantlib/test_adapter.py -q` => `18 passed`
- `PYTHONPATH=/tmp/oss-impl-002-site python3 services/research/quantlib/smoke_test.py --backend real` => passed

## 6. Acceptance

Treat the QuantLib row as smoke-proven when:

- the smoke command exits `0` with `assertions: OK`
- the workflow emits a governed `pricing_report`
- `artifact_state=draft` and `deployment_stage=none` are confirmed
- both option and bond branches are populated
- unit coverage still passes

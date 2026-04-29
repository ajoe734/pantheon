# services/research/finrl

Deferred-prep FinRL lane for Pantheon.

## Scope

This directory now contains a **prep-only** FinRL workflow:

- governed input adapter
- explicit non-default deferred-prep gate
- offline policy-output workflow
- draft artifact envelope plus candidate packet scaffold
- local unit and smoke coverage

It does **not** reopen the RL gate in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`.

## Current Truth

- checklist status remains `criteria-defined`
- maturity remains activation-ready, not activated
- outputs are repo-local `artifact_state=draft` only
- `deployment_summary.current_stage` remains `none`

## Files

- `adapter/finrl_adapter.py`: governed adapter, deferred-prep gate, stub and import-validating backends
- `worker.py`: container entrypoint
- `smoke_test.py`: explicit-gate smoke path
- `test_adapter.py`: unit coverage
- `examples/policy_input_sample.json`: governed smoke input dataset
- `examples/policy_dataset_sample.json`: smaller governed sample dataset

## Usage

Run unit tests:

```bash
python3 -m pytest services/research/finrl/test_adapter.py -q
```

Run smoke test:

```bash
python3 services/research/finrl/smoke_test.py --enable-deferred-prep
```

Run worker:

```bash
PANTHEON_FINRL_PREP_ENABLED=1 python3 services/research/finrl/worker.py
```

# services/research/finrl

Deferred-prep FinRL lane for Pantheon.

## Scope

This directory now contains a **prep-only** FinRL workflow:

- governed input adapter
- explicit non-default deferred-prep gate
- offline policy-output workflow with artifact packet persistence
- draft artifact envelope plus candidate packet scaffold
- local unit and smoke coverage

It does **not** reopen the RL gate in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`.

## Current Truth

- checklist status remains `criteria-defined`
- maturity remains activation-ready, not activated
- outputs are repo-local `artifact_state=draft` only
- `deployment_summary.current_stage` remains `none`
- `PANTHEON_FINRL_BACKEND=finrl_ppo` or `finrl_dqn` uses the pinned upstream package metadata and then runs a bounded offline policy fit
- the default backend remains offline-safe `stub`

## Files

- `adapter.py`: public `train(strategy_spec_ref, backend)` entrypoint
- `engine/finrl_adapter.py`: governed adapter, deferred-prep gate, stub backend, and bounded PPO/DQN backends
- `worker.py`: container entrypoint that writes artifact, registry, and candidate JSON packets
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
python3 services/research/finrl/smoke_test.py
```

Run worker:

```bash
PANTHEON_FINRL_PREP_ENABLED=1 python3 services/research/finrl/worker.py
PANTHEON_FINRL_PREP_ENABLED=1 PANTHEON_FINRL_BACKEND=finrl_dqn python3 services/research/finrl/worker.py
```

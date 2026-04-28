# services/research/rllib

Deferred-prep RLlib + Ray Tune lane for Pantheon.

## Scope

This directory now contains **prep-only** RLlib + Ray Tune workflows:

- governed train/eval input adapter
- governed Ray Tune search-space and result adapter
- explicit non-default deferred-prep gate
- rollout/result schema scaffold
- offline train/eval workflow plus candidate packet scaffold
- offline tuning artifact format plus projected candidate outputs
- local unit and smoke coverage

It does **not** reopen the RL gate in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`.

## Current Truth

- checklist status remains `version-pinned`
- maturity remains activation-ready, not activated
- outputs are repo-local `artifact_state=draft` only
- `deployment_summary.current_stage` remains `none`
- default backends remain offline-safe `stub`

## Files

- `adapter/rllib_adapter.py`: governed RLlib adapter, rollout/result schema, stub and import-validating backends
- `adapter/ray_tune_adapter.py`: governed Ray Tune search-space/result adapter, offline tuning artifact format
- `worker.py`: RLlib container entrypoint
- `ray_tune_worker.py`: Ray Tune container entrypoint
- `smoke_test.py`: RLlib explicit-gate smoke path
- `ray_tune_smoke_test.py`: Ray Tune explicit-gate smoke path
- `test_adapter.py`: RLlib unit coverage
- `test_ray_tune_adapter.py`: Ray Tune unit coverage
- `examples/train_eval_input_sample.json`: governed sample dataset

## Usage

Run RLlib unit tests:

```bash
python3 -m pytest services/research/rllib/test_adapter.py -q
```

Run Ray Tune unit tests:

```bash
python3 -m pytest services/research/rllib/test_ray_tune_adapter.py -q
```

Run RLlib smoke test:

```bash
python3 services/research/rllib/smoke_test.py --enable-deferred-prep
```

Run Ray Tune smoke test:

```bash
python3 services/research/rllib/ray_tune_smoke_test.py --enable-deferred-prep
```

Run RLlib worker:

```bash
PANTHEON_RLLIB_PREP_ENABLED=1 python3 services/research/rllib/worker.py
```

Run Ray Tune worker:

```bash
PANTHEON_RAYTUNE_PREP_ENABLED=1 python3 services/research/rllib/ray_tune_worker.py
```

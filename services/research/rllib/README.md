# services/research/rllib

Deferred-prep RLlib + Ray Tune lane for Pantheon, plus the
OSS-RLLIB-001 bounded CartPole PPO adapter surface.

## Scope

This directory now contains **prep-only** RLlib + Ray Tune workflows:

- governed train/eval input adapter
- governed Ray Tune search-space and result adapter
- CPU-only CartPole PPO `train_ppo()` public adapter returning a draft
  `model_artifact`
- explicit non-default deferred-prep gate
- rollout/result schema enforcement
- offline train/eval workflow plus candidate packet scaffold
- offline tuning artifact format plus projected candidate outputs
- worker artifact packet persistence
- local unit and smoke coverage

It does **not** reopen the RL gate in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`.

## Current Truth

- checklist status remains `version-pinned`
- maturity remains activation-ready, not activated
- outputs are repo-local `artifact_state=draft` only
- `deployment_summary.current_stage` remains `none`
- default backends remain offline-safe `stub`
- `PANTHEON_RLLIB_BACKEND=rllib` requires the pinned upstream Ray/RLlib package and then runs bounded offline train/eval without stub delegation
- `PANTHEON_RAYTUNE_BACKEND=tune` requires the pinned upstream Ray Tune package and then runs bounded offline search without stub delegation
- `train_ppo(require_rllib=True)` requires upstream Ray/RLlib; the default
  `train_ppo()` path records an explicit local fallback if Ray/RLlib is absent

## Files

- `adapter.py`: public OSS-RLLIB-001 shim exposing `train_ppo(env_id, num_iters)`
- `cartpole_ppo.py`: bounded CPU-only CartPole PPO mini-loop and model_artifact envelope
- `adapter/rllib_adapter.py`: governed RLlib adapter, rollout/result schema, stub backend, and bounded upstream-gated backend
- `adapter/ray_tune_adapter.py`: governed Ray Tune search-space/result adapter, offline tuning artifact format, and bounded upstream-gated search backend
- `contract.md`: OSS-RLLIB-001 public interface and smoke acceptance contract
- `worker.py`: RLlib container entrypoint that writes artifact, registry, and candidate JSON packets
- `ray_tune_worker.py`: Ray Tune container entrypoint that writes artifact, registry, and candidate JSON packets
- `smoke_test.py`: CartPole PPO model_artifact smoke path, with `--enable-deferred-prep` for the older governed prep smoke
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

Run RLlib CartPole PPO smoke test:

```bash
python3 services/research/rllib/smoke_test.py --num-iters 5
```

Run the older RLlib deferred-prep smoke test:

```bash
python3 services/research/rllib/smoke_test.py --enable-deferred-prep --deferred-backend stub
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

# OSS-RLLIB-001 Contract: RLlib PPO Adapter

Status: implemented
Task: OSS-RLLIB-001
Reviewer: Claude

## Purpose

Provides a bounded CPU-only CartPole PPO adapter surface for the research
plane. The output is an ExperimentRun-shaped dict that references a draft
`model_artifact`; it has no direct live-trading influence.

## Public Interface

### `adapter.train_ppo(env_id="CartPole-v1", num_iters=5, ...)`

```python
def train_ppo(
    env_id: str = "CartPole-v1",
    num_iters: int = 5,
    *,
    output_dir: str | Path | None = None,
    seed: int = 42,
    eval_episodes: int = 5,
    require_rllib: bool = False,
) -> dict:
    ...
```

Inputs:

| Parameter | Type | Constraint |
| --- | --- | --- |
| `env_id` | string | Defaults to `CartPole-v1`; local fallback supports only CartPole |
| `num_iters` | integer | `1 <= num_iters <= 20` |
| `output_dir` | path | Defaults to `/tmp/pantheon/research/rllib/cartpole` |
| `seed` | integer | Used for deterministic baseline and evaluation |
| `eval_episodes` | integer | Must be >= 1 |
| `require_rllib` | boolean | If true, fail instead of using local fallback when Ray/RLlib is unavailable |

Output dict:

| Key | Description |
| --- | --- |
| `schema_version` | `experiment_run.v1` |
| `run_id` | Unique run id |
| `status` | `completed` on success |
| `adapter` | `rllib_ppo` |
| `framework` | `ray.rllib` |
| `algorithm` | `PPO` |
| `artifact_type` | `model_artifact` |
| `model_artifact_ref` | Registry-style artifact reference |
| `model_artifact_path` | Local JSON artifact path |
| `registry_entry.artifact_type` | `model_artifact` |
| `metrics.random_baseline_mean_reward` | Random-policy CartPole baseline |
| `metrics.mean_reward` | Trained policy mean reward |
| `metrics.improved_vs_random_baseline` | Must be true for smoke acceptance |

## Backend Behavior

- Preferred backend: upstream Ray RLlib `PPOConfig` with `num_gpus=0`, one local
  worker, and at most 20 training iterations.
- Dependency-light fallback: deterministic local CartPole policy-improvement
  skeleton. The fallback is explicit: returned runs include
  `backend_kind=dependency_light_fallback` and a `backend_failure` record for
  the failed upstream attempt.
- `require_rllib=True` disables fallback and raises on upstream import/runtime
  failure.

## Smoke Acceptance

`services/research/rllib/smoke_test.py` runs `train_ppo("CartPole-v1",
num_iters<=20)`, asserts CPU-only execution, asserts the trained policy mean
reward is greater than the random baseline, and asserts the generated registry
entry and run both expose `artifact_type=model_artifact`.

## Governance

- Output artifact type: `model_artifact`
- `artifact_state: draft`
- `deployment_summary.current_stage: none`
- `direct_live_influence: false`
- No broker, registry-service, or execution-plane writes.
- Docker base image remains `python:3.11-slim`; no NVIDIA/GPU image is used.

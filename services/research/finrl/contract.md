# FinRL Adapter Contract

## Overview
This adapter provides a governed entrypoint for FinRL-based RL agent training for financial datasets.

## Interface
- `train(strategy_spec_ref: Mapping[str, Any], backend: str = "finrl_ppo") -> Mapping[str, Any]`
- Supported backends:
  - `finrl_ppo` / `ppo` / `finrl`
  - `finrl_dqn` / `dqn`
  - `stub`

The adapter consumes governed OHLCV records from `strategy_spec_ref["records"]`,
runs a bounded offline mini-training pass, and returns an ExperimentRun-shaped
dict with `run_id`, `backend`, `status`, `metrics`, `artifact_type`, and
`model_artifact_ref`.

The FinRL package version is pinned in this service's local `requirements.txt`.
The smoke path is CPU-only and must not require CUDA/NVIDIA base images or
GPU-specific wheels.

## Artifacts
- Produced: `model_artifact` (registered with type `model_artifact`)
- Lineage: References `strategy_spec_ref` and `dataset_ref`

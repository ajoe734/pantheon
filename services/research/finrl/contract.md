# FinRL Adapter Contract

## Overview
This adapter provides a governed entrypoint for FinRL-based RL agent training for financial datasets.

## Interface
- `train(strategy_spec_ref: Mapping[str, Any], backend: str = "finrl_ppo") -> Mapping[str, Any]`

## Artifacts
- Produced: `model_artifact` (registered with type `model_artifact`)
- Lineage: References `strategy_spec_ref` and `dataset_ref`

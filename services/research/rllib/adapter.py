"""Public RLlib PPO adapter shim for OSS-RLLIB-001.

Python resolves `services.research.rllib.adapter` to the existing adapter
package when both `adapter.py` and `adapter/` are present. The implementation
therefore lives in `cartpole_ppo.py` and is re-exported from both surfaces.
"""
from __future__ import annotations

try:  # Package import from repo root.
    from .cartpole_ppo import (
        CartPolePPOConfig,
        DEFAULT_ENV_ID,
        MAX_NUM_ITERS,
        RLlibPPOAdapterError,
        evaluate_policy,
        random_policy_baseline,
        train_ppo,
    )
except ImportError:  # Service-local execution from services/research/rllib.
    from cartpole_ppo import (  # type: ignore
        CartPolePPOConfig,
        DEFAULT_ENV_ID,
        MAX_NUM_ITERS,
        RLlibPPOAdapterError,
        evaluate_policy,
        random_policy_baseline,
        train_ppo,
    )

__all__ = [
    "CartPolePPOConfig",
    "DEFAULT_ENV_ID",
    "MAX_NUM_ITERS",
    "RLlibPPOAdapterError",
    "evaluate_policy",
    "random_policy_baseline",
    "train_ppo",
]

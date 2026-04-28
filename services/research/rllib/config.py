"""RLlib deferred-prep config selectors.

This selector is repo-local prep wiring only. It does not reopen the RL gate or
authorize production RLlib execution.
"""
from __future__ import annotations

import os

_SUPPORTED_BACKENDS = ("stub", "rllib")
_SUPPORTED_TUNE_BACKENDS = ("stub", "tune")


def selected_backend(default: str = "stub") -> str:
    backend = os.getenv("PANTHEON_RLLIB_BACKEND", default).strip().lower() or default
    if backend not in _SUPPORTED_BACKENDS:
        raise EnvironmentError(
            f"PANTHEON_RLLIB_BACKEND={backend!r} is not supported. "
            f"Supported backends: {_SUPPORTED_BACKENDS}. "
            "RLlib deferred prep stays offline and non-default."
        )
    return backend


def selected_tune_backend(default: str = "stub") -> str:
    backend = os.getenv("PANTHEON_RAYTUNE_BACKEND", default).strip().lower() or default
    if backend not in _SUPPORTED_TUNE_BACKENDS:
        raise EnvironmentError(
            f"PANTHEON_RAYTUNE_BACKEND={backend!r} is not supported. "
            f"Supported backends: {_SUPPORTED_TUNE_BACKENDS}. "
            "Ray Tune deferred prep stays offline and non-default."
        )
    return backend

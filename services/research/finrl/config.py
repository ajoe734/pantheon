"""FinRL deferred-prep config selectors.

This selector is repo-local prep wiring only. It does not reopen the RL gate or
authorize production FinRL execution.
"""
from __future__ import annotations

import os

_SUPPORTED_BACKENDS = ("stub", "finrl")


def selected_backend(default: str = "stub") -> str:
    backend = os.getenv("PANTHEON_FINRL_BACKEND", default).strip().lower() or default
    if backend not in _SUPPORTED_BACKENDS:
        raise EnvironmentError(
            f"PANTHEON_FINRL_BACKEND={backend!r} is not supported. "
            f"Supported backends: {_SUPPORTED_BACKENDS}. "
            "FinRL deferred prep stays offline and non-default."
        )
    return backend

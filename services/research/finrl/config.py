"""FinRL deferred-prep config selectors.

This selector is repo-local prep wiring only. It does not reopen the RL gate or
authorize production FinRL execution.
"""
from __future__ import annotations

import os

_BACKEND_ALIASES = {
    "stub": "stub",
    "stub_finrl": "stub",
    "finrl": "finrl_ppo",
    "ppo": "finrl_ppo",
    "finrl_ppo": "finrl_ppo",
    "dqn": "finrl_dqn",
    "finrl_dqn": "finrl_dqn",
}
_SUPPORTED_BACKENDS = tuple(_BACKEND_ALIASES)


def selected_backend(default: str = "stub") -> str:
    backend = os.getenv("PANTHEON_FINRL_BACKEND", default).strip().lower() or default
    canonical = _BACKEND_ALIASES.get(backend)
    if canonical is None:
        raise EnvironmentError(
            f"PANTHEON_FINRL_BACKEND={backend!r} is not supported. "
            f"Supported backends: {_SUPPORTED_BACKENDS}. "
            "FinRL deferred prep stays offline and non-default."
        )
    return canonical

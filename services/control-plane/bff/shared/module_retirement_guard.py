"""Module retirement guard enforcing OVERLAY-RETIRE-001 invariant.

Under OVERLAY-RETIRE-001, process-local product state overlays are retired.
Accessing or attempting to reinstate any retired overlay symbol raises an
explicit AttributeError with normative rationale.
"""
from __future__ import annotations

import types
from typing import Any, FrozenSet

DEFAULT_RETIRED_PROCESS_OVERLAYS: FrozenSet[str] = frozenset({
    "_PERSONA_BFF_OVERLAY",
    "_STRATEGY_BFF_OVERLAY",
    "_GOV_BFF_INCIDENT_OVERLAY",
    "_GOV_BFF_JOB_OVERLAY",
})

GETATTR_ERROR_MESSAGE = (
    "{name} has been retired and deleted under OVERLAY-RETIRE-001; "
    "process-local overlays are forbidden and canonical domain stores must be used directly."
)

SETATTR_ERROR_MESSAGE = (
    "{name} has been retired and deleted under OVERLAY-RETIRE-001; "
    "process-local overlays are forbidden and cannot be reinstated."
)


def check_retired_overlay_getattr(
    name: str,
    retired_symbols: FrozenSet[str] = DEFAULT_RETIRED_PROCESS_OVERLAYS,
) -> None:
    if name in retired_symbols:
        raise AttributeError(GETATTR_ERROR_MESSAGE.format(name=name))


def check_retired_overlay_setattr(
    name: str,
    retired_symbols: FrozenSet[str] = DEFAULT_RETIRED_PROCESS_OVERLAYS,
) -> None:
    if name in retired_symbols:
        raise AttributeError(SETATTR_ERROR_MESSAGE.format(name=name))


class ModuleRetirementGuard(types.ModuleType):
    """ModuleType subclass guarding against reading or writing retired overlays."""

    _retired_symbols: FrozenSet[str] = DEFAULT_RETIRED_PROCESS_OVERLAYS

    def __init__(self, name: str, doc: str | None = None, *, retired_symbols: FrozenSet[str] | None = None) -> None:
        super().__init__(name, doc)
        if retired_symbols is not None:
            self._retired_symbols = retired_symbols

    def __getattr__(self, name: str) -> Any:
        if name in self._retired_symbols:
            raise AttributeError(GETATTR_ERROR_MESSAGE.format(name=name))
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._retired_symbols:
            raise AttributeError(SETATTR_ERROR_MESSAGE.format(name=name))
        super().__setattr__(name, value)
        self._on_setattr(name, value)

    def _on_setattr(self, name: str, value: Any) -> None:
        """Hook for subclasses to react to attribute modifications."""
        pass

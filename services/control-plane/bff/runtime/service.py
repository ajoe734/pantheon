"""Runtime router composition support.

The router intentionally receives BFF ports instead of importing ``main``.
This keeps RuntimeBinding write ownership in Runtime Manager while allowing the
BFF to assemble its read, command, and streaming surfaces.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Mapping, Optional


class _MissingRuntimeDependency:
    """Deferred error so route-discovery tests need no full BFF composition."""

    def __init__(self, name: str) -> None:
        self._name = name

    def _raise(self) -> None:
        raise RuntimeError(
            f"Runtime router dependency {self._name!r} was not supplied by the BFF composition root."
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._raise()

    def __getattr__(self, _name: str) -> Any:
        self._raise()

    def __getitem__(self, _key: Any) -> Any:
        self._raise()


class _LazyReadStore:
    def __init__(self, get_read_store: Optional[Callable[[], Any]]) -> None:
        self._get_read_store = get_read_store

    def __getattr__(self, name: str) -> Any:
        if self._get_read_store is None:
            return getattr(_MissingRuntimeDependency("get_read_store"), name)
        return getattr(self._get_read_store(), name)


class RuntimeRouterService:
    """Resolve composition ports while preserving a late-bound BFF read store."""

    def __init__(
        self,
        *,
        read_surface: Optional[Any] = None,
        get_read_store: Optional[Callable[[], Any]] = None,
        dependencies: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if read_surface is not None:
            self._get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
        else:
            self._get_read_store = get_read_store
        self._dependencies = dependencies or {}

    @property
    def read_store(self) -> _LazyReadStore:
        return _LazyReadStore(self._get_read_store)

    def dependency(self, name: str) -> Any:
        return self._dependencies.get(name, _MissingRuntimeDependency(name))

    def runtime_event_stream(self) -> tuple[Any, Any]:
        buffers = self.dependency("_sse_buffers")
        subscribers = self.dependency("_sse_subscribers")
        if isinstance(buffers, _MissingRuntimeDependency) or isinstance(
            subscribers, _MissingRuntimeDependency
        ):
            return deque(maxlen=100), []
        return buffers["runtime"], subscribers["runtime"]

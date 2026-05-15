"""Concrete pending-signal store adapters for execution runtimes.

The canonical signal-store contract intentionally stops at stable write/query
semantics. Execution runtimes need one extra capability: pull the next batch of
signals waiting to be consumed. This module provides runtime-local adapters for
that pending queue without changing the base store contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Protocol

_SIGNAL_STORE_DIR = Path(__file__).resolve().parents[2] / "signal-store"
if str(_SIGNAL_STORE_DIR) not in sys.path:
    sys.path.insert(0, str(_SIGNAL_STORE_DIR))

from client import validate_signal_payload_minimal


class PendingSignalStore(Protocol):
    """Execution-side queue interface used by the paper runtime service."""

    def get_pending(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return and remove the next batch of pending signals."""

    def queue_depth(self) -> int:
        """Return the current pending queue depth."""


class InMemoryPendingSignalStore:
    """Small in-memory queue for tests and local dry runs."""

    kind = "memory_pending_signal_store"

    def __init__(self, pending_signals: list[dict[str, Any]] | None = None) -> None:
        self._pending: list[dict[str, Any]] = []
        for payload in pending_signals or []:
            self.enqueue(payload)

    def enqueue(self, payload: dict[str, Any]) -> None:
        validate_signal_payload_minimal(payload)
        self._pending.append(json.loads(json.dumps(payload)))

    def get_pending(self, limit: int | None = None) -> list[dict[str, Any]]:
        batch_limit = max(int(limit or len(self._pending) or 1), 0)
        if batch_limit == 0:
            return []
        drained = self._pending[:batch_limit]
        self._pending = self._pending[batch_limit:]
        return drained

    def queue_depth(self) -> int:
        return len(self._pending)


class RedisPendingSignalStore:
    """Redis-backed pending queue for VM-2 execution containers.

    Signals are stored as exact JSON payloads in a Redis list. `get_pending()`
    removes items eagerly when the runtime claims them. This keeps the adapter
    simple and matches the current EP4 goal: prove a truthful runtime package
    and concrete signal-consumer path without overclaiming delivery receipts.
    """

    kind = "redis_pending_signal_store"

    def __init__(
        self,
        redis_url: str,
        *,
        queue_key: str = "pantheon:signals:pending",
        default_batch_size: int = 100,
    ) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - exercised in container
            raise RuntimeError(
                "RedisPendingSignalStore requires the 'redis' package in the runtime image."
            ) from exc

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._queue_key = queue_key
        self._default_batch_size = max(int(default_batch_size), 1)

    def enqueue(self, payload: dict[str, Any]) -> None:
        validate_signal_payload_minimal(payload)
        self._client.rpush(self._queue_key, json.dumps(payload))

    def get_pending(self, limit: int | None = None) -> list[dict[str, Any]]:
        batch_limit = max(int(limit or self._default_batch_size), 1)
        drained: list[dict[str, Any]] = []
        for _ in range(batch_limit):
            raw = self._client.lpop(self._queue_key)
            if raw is None:
                break
            drained.append(json.loads(raw))
        return drained

    def queue_depth(self) -> int:
        return int(self._client.llen(self._queue_key))


def build_pending_signal_store(
    signal_store_url: str,
    *,
    queue_key: str = "pantheon:signals:pending",
    default_batch_size: int = 100,
) -> PendingSignalStore:
    """Build the default pending store adapter from the configured URL."""

    normalized = str(signal_store_url or "").strip()
    if not normalized:
        return InMemoryPendingSignalStore()
    if normalized.startswith("redis://") or normalized.startswith("rediss://"):
        return RedisPendingSignalStore(
            normalized,
            queue_key=queue_key,
            default_batch_size=default_batch_size,
        )
    raise RuntimeError(
        f"Unsupported SIGNAL_STORE_URL {normalized!r}; expected redis://, rediss://, or empty."
    )

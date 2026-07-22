"""Concrete pending-signal store adapters for execution runtimes.

The canonical signal-store contract intentionally stops at stable write/query
semantics. Execution runtimes need one extra capability: pull the next batch of
signals waiting to be consumed. This module provides runtime-local adapters for
that pending queue without changing the base store contract.

Queue key isolation
-------------------
Each runtime binding should consume from its own Redis key to prevent the 15+
paper runtimes in the fleet from racing on a shared queue.  The canonical
key format is ``pantheon:signals:pending:<binding_id>``.

Use :func:`binding_queue_key` to construct a binding-scoped key.
:func:`build_pending_signal_store` auto-derives the scoped key from env vars
when the caller has not supplied an explicit override:
  1. ``PANTHEON_SIGNAL_QUEUE_KEY`` (set by the fleet reconciler per worker)
  2. ``PANTHEON_RUNTIME_BINDING_ID``  (set in the worker's env by the reconciler)
  3. bare default ``pantheon:signals:pending`` (standalone / test runs)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

#: Shared namespace prefix; all signal queue keys start with this value.
BINDING_QUEUE_KEY_PREFIX = "pantheon:signals:pending"

#: Dead-letter queue prefix; misrouted or isolation-rejected signals land here.
BINDING_DLQ_KEY_PREFIX = "pantheon:signals:dlq"

_SIGNAL_STORE_DIR = Path(__file__).resolve().parents[2] / "signal-store"
if str(_SIGNAL_STORE_DIR) not in sys.path:
    sys.path.insert(0, str(_SIGNAL_STORE_DIR))

from client import validate_signal_payload_minimal


def binding_queue_key(binding_id: str) -> str:
    """Return the binding-scoped Redis queue key for *binding_id*."""
    return f"{BINDING_QUEUE_KEY_PREFIX}:{binding_id}"


def binding_dlq_key(binding_id: str) -> str:
    """Return the binding-scoped DLQ key for *binding_id*.

    Signals rejected by isolation checks (runtime_id or capital_pool_id
    mismatch) are enqueued here so operators can inspect or replay them
    without losing the payload.
    """
    return f"{BINDING_DLQ_KEY_PREFIX}:{binding_id}"


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
        self._dlq: list[dict[str, Any]] = []
        for payload in pending_signals or []:
            self.enqueue(payload)
        self._processed: set[str] = set()

    def mark_processed(self, signal_id: str) -> None:
        self._processed.add(str(signal_id))

    def is_processed(self, signal_id: str) -> bool:
        return str(signal_id) in self._processed

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

    def enqueue_dlq(self, payload: dict[str, Any]) -> None:
        """Route an isolation-rejected signal to the in-memory DLQ."""
        self._dlq.append(json.loads(json.dumps(payload)))

    def dlq_depth(self) -> int:
        return len(self._dlq)

    def get_dlq(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Drain up to *limit* items from the DLQ (for testing/replay)."""
        batch_limit = max(int(limit or len(self._dlq) or 1), 0)
        drained = self._dlq[:batch_limit]
        self._dlq = self._dlq[batch_limit:]
        return drained


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
        # Persistent idempotency window: processed signal_ids survive a worker
        # restart so a replayed signal is not double-filled. Bounded by a TTL that
        # matches the consumer's 24h staleness window (older duplicates are
        # discarded as stale anyway), keeping redis growth bounded.
        self._processed_ttl_seconds = 24 * 60 * 60
        self._processed_prefix = f"{queue_key}:processed:"
        # DLQ key: derive from queue_key by replacing the pending prefix with dlq prefix.
        # e.g. pantheon:signals:pending:b-001 → pantheon:signals:dlq:b-001
        self._dlq_key = queue_key.replace(BINDING_QUEUE_KEY_PREFIX, BINDING_DLQ_KEY_PREFIX, 1)

    def mark_processed(self, signal_id: str) -> None:
        try:
            self._client.setex(self._processed_prefix + str(signal_id), self._processed_ttl_seconds, "1")
        except Exception:  # noqa: BLE001 - dedup is best-effort; never break execution
            pass

    def is_processed(self, signal_id: str) -> bool:
        try:
            return bool(self._client.exists(self._processed_prefix + str(signal_id)))
        except Exception:  # noqa: BLE001
            return False

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

    def enqueue_dlq(self, payload: dict[str, Any]) -> None:
        """Route an isolation-rejected signal to the Redis DLQ (best-effort)."""
        try:
            self._client.rpush(self._dlq_key, json.dumps(payload))
        except Exception:  # noqa: BLE001 - DLQ write is best-effort; never break signal path
            pass

    def dlq_depth(self) -> int:
        try:
            return int(self._client.llen(self._dlq_key))
        except Exception:  # noqa: BLE001
            return -1


def build_pending_signal_store(
    signal_store_url: str,
    *,
    queue_key: str = BINDING_QUEUE_KEY_PREFIX,
    default_batch_size: int = 100,
) -> PendingSignalStore:
    """Build the default pending store adapter from the configured URL.

    Queue-key resolution (when *queue_key* equals the bare default):
    1. ``PANTHEON_SIGNAL_QUEUE_KEY`` env var (set by fleet reconciler)
    2. Binding-scoped key derived from ``PANTHEON_RUNTIME_BINDING_ID``
    3. Bare default ``pantheon:signals:pending``
    """
    if queue_key == BINDING_QUEUE_KEY_PREFIX:
        env_explicit = os.getenv("PANTHEON_SIGNAL_QUEUE_KEY", "").strip()
        env_binding = os.getenv("PANTHEON_RUNTIME_BINDING_ID", "").strip()
        if env_explicit:
            queue_key = env_explicit
        elif env_binding:
            queue_key = binding_queue_key(env_binding)

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

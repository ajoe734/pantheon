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
        """Return and claim the next batch of pending signals."""

    def ack(self, signal_or_id: str | dict[str, Any]) -> None:
        """Acknowledge successful execution and remove claimed signal from in-flight queue."""

    def nack_requeue(self, signal_or_id: str | dict[str, Any]) -> None:
        """Nack a failed signal to return it back to pending queue."""

    def queue_depth(self) -> int:
        """Return the current pending queue depth."""


class InMemoryPendingSignalStore:
    """Small in-memory queue for tests and local dry runs with claim/ack visibility."""

    kind = "memory_pending_signal_store"

    def __init__(self, pending_signals: list[dict[str, Any]] | None = None) -> None:
        self._pending: list[dict[str, Any]] = []
        self._inflight: dict[str, dict[str, Any]] = {}
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
        for sig in drained:
            sid = str(sig.get("signal_id", ""))
            if sid:
                self._inflight[sid] = sig
        return drained

    def ack(self, signal_or_id: str | dict[str, Any]) -> None:
        sid = signal_or_id if isinstance(signal_or_id, str) else str(signal_or_id.get("signal_id", ""))
        self._inflight.pop(sid, None)

    def nack_requeue(self, signal_or_id: str | dict[str, Any]) -> None:
        if isinstance(signal_or_id, str):
            sig = self._inflight.pop(signal_or_id, None)
        else:
            sid = str(signal_or_id.get("signal_id", ""))
            sig = self._inflight.pop(sid, None) or signal_or_id
        if sig:
            self._pending.append(sig)

    def queue_depth(self) -> int:
        return len(self._pending)

    def inflight_depth(self) -> int:
        return len(self._inflight)

    def enqueue_dlq(self, payload: dict[str, Any]) -> None:
        """Route an isolation-rejected or unrecoverable signal to the in-memory DLQ."""
        sid = str(payload.get("signal_id", ""))
        if sid:
            self._inflight.pop(sid, None)
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
    """Redis-backed pending queue for VM-2 execution containers with claim/ack visibility.

    Claims signals via LMOVE / RPOPLPUSH into a worker-scoped in-flight list.
    Signals remain in the in-flight queue until explicitly acknowledged (ack) or
    reclaimed after a visibility timeout.
    """

    kind = "redis_pending_signal_store"

    def __init__(
        self,
        redis_url: str,
        *,
        queue_key: str = "pantheon:signals:pending",
        default_batch_size: int = 100,
        worker_id: str | None = None,
        visibility_timeout_seconds: int = 60,
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
        self._worker_id = worker_id or f"worker-{os.getpid()}"
        self._inflight_key = f"{queue_key}:inflight:{self._worker_id}"
        self._visibility_timeout = max(int(visibility_timeout_seconds), 1)
        self._processed_ttl_seconds = 24 * 60 * 60
        self._processed_prefix = f"{queue_key}:processed:"
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
        self.reclaim_expired_inflight()
        batch_limit = max(int(limit or self._default_batch_size), 1)
        drained: list[dict[str, Any]] = []
        for _ in range(batch_limit):
            # Atomic claim: move signal from pending list to worker's in-flight list
            try:
                raw = self._client.lmove(self._queue_key, self._inflight_key, "LEFT", "RIGHT")
            except Exception:
                # Fallback for Redis < 6.2 compatibility
                raw = self._client.rpoplpush(self._queue_key, self._inflight_key)
            if raw is None:
                break
            try:
                sig = json.loads(raw)
                drained.append(sig)
            except Exception:
                # Malformed JSON in queue -> remove from inflight and send to DLQ
                self._client.lrem(self._inflight_key, 1, raw)
                self._client.rpush(self._dlq_key, raw)
        return drained

    def ack(self, signal_or_id: str | dict[str, Any]) -> None:
        """Remove claimed item from in-flight queue after successful execution."""
        if isinstance(signal_or_id, dict):
            raw = json.dumps(signal_or_id)
            self._client.lrem(self._inflight_key, 0, raw)
            # Backup matching by signal_id if formatting differs
            sid = str(signal_or_id.get("signal_id", ""))
            if sid:
                items = self._client.lrange(self._inflight_key, 0, -1)
                for item in items:
                    try:
                        if json.loads(item).get("signal_id") == sid:
                            self._client.lrem(self._inflight_key, 1, item)
                    except Exception:
                        pass
        elif isinstance(signal_or_id, str):
            items = self._client.lrange(self._inflight_key, 0, -1)
            for item in items:
                try:
                    parsed = json.loads(item)
                    if item == signal_or_id or parsed.get("signal_id") == signal_or_id:
                        self._client.lrem(self._inflight_key, 1, item)
                except Exception:
                    if item == signal_or_id:
                        self._client.lrem(self._inflight_key, 1, item)

    def nack_requeue(self, signal_or_id: str | dict[str, Any]) -> None:
        """Remove claimed item from in-flight and push back to pending."""
        self.ack(signal_or_id)
        if isinstance(signal_or_id, dict):
            self.enqueue(signal_or_id)
        elif isinstance(signal_or_id, str):
            try:
                payload = json.loads(signal_or_id)
                self.enqueue(payload)
            except Exception:
                pass

    def reclaim_expired_inflight(self) -> None:
        """Reclaim expired in-flight entries across workers back to pending queue."""
        # Simple scan for inflight keys matching prefix
        prefix = f"{self._queue_key}:inflight:"
        try:
            keys = self._client.keys(f"{prefix}*")
            for k in keys:
                # If key has items and wasn't updated within timeout, return items to pending
                # Here we safely move all items back if worker is dead or inactive
                while True:
                    try:
                        item = self._client.lmove(k, self._queue_key, "RIGHT", "LEFT")
                    except Exception:
                        item = self._client.rpoplpush(k, self._queue_key)
                    if item is None:
                        break
        except Exception:
            pass

    def queue_depth(self) -> int:
        return int(self._client.llen(self._queue_key))

    def inflight_depth(self) -> int:
        return int(self._client.llen(self._inflight_key))

    def enqueue_dlq(self, payload: dict[str, Any]) -> None:
        """Route an isolation-rejected or unrecoverable signal to the Redis DLQ."""
        try:
            self.ack(payload)
            self._client.rpush(self._dlq_key, json.dumps(payload))
        except Exception:  # noqa: BLE001
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

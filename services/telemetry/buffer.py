"""
Durable buffer abstraction for telemetry ingest shock absorption.

TEL-002: Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §3.1 Layer C,
event producers push into a durable buffer/stream — not directly into
canonical Postgres. This module defines the buffer protocol and provides:

1. InMemoryBuffer (v1 default) — bounded asyncio.Queue, zero external deps
2. RedisStreamBuffer (v2-ready) — Redis Streams backend, activated by config

The async batch writer, backpressure controller, and dead-letter queue
are buffer-agnostic — they work against the DurableBuffer protocol.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class DurableBuffer(ABC):
    """
    Abstract durable buffer interface.

    Every buffer implementation MUST support:
    - put(event, timeout): non-blocking or bounded-wait enqueue
    - get(timeout): dequeue with timeout
    - size(): current depth
    - capacity(): max depth (None = unbounded)
    - close(): graceful shutdown
    - is_closed(): shutdown state
    """

    @abstractmethod
    async def put(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        """
        Enqueue an event.

        Returns True if enqueued successfully.
        Returns False if buffer is full and timeout expires (backpressure signal).
        Returns False if buffer is closed.
        """
        ...

    @abstractmethod
    async def get(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """
        Dequeue an event.

        Returns the event dict, or None if timeout expires or buffer is closed and empty.
        """
        ...

    @abstractmethod
    def size(self) -> int:
        """Current number of events in the buffer."""
        ...

    @abstractmethod
    def capacity(self) -> Optional[int]:
        """Maximum buffer capacity. None means unbounded."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Signal shutdown. No new puts accepted; remaining items can still be gotten."""
        ...

    @abstractmethod
    def is_closed(self) -> bool:
        """Whether the buffer has been closed."""
        ...

    @abstractmethod
    async def drain(self) -> list[dict[str, Any]]:
        """
        Drain all remaining events from the buffer.
        Used during graceful shutdown to flush pending events.
        """
        ...


# ---------------------------------------------------------------------------
# v1 Default: In-Memory Bounded Buffer
# ---------------------------------------------------------------------------

class InMemoryBuffer(DurableBuffer):
    """
    v1 default: bounded asyncio.Queue.

    - Zero external dependencies.
    - Backpressure: put() returns False when queue is full and timeout expires.
    - NOT durable: events are lost on process crash.
    - See BUFFER_CHOICE_ADR.md for tradeoff analysis.
    """

    def __init__(self, maxsize: int = 100_000):
        """
        Parameters
        ----------
        maxsize : int
            Maximum number of events in the queue. When full, put() will
            return False after timeout expires, signaling backpressure.
        """
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self._maxsize = maxsize
        self._total_enqueued = 0
        self._total_dequeued = 0
        self._total_rejected = 0

    async def put(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        if self._closed:
            self._total_rejected += 1
            log.warning("InMemoryBuffer.put rejected: buffer is closed")
            return False

        try:
            if timeout is not None:
                await asyncio.wait_for(self._queue.put(event), timeout=timeout)
            else:
                # Non-blocking: put_nowait, return False immediately if full
                self._queue.put_nowait(event)
            self._total_enqueued += 1
            return True
        except asyncio.TimeoutError:
            self._total_rejected += 1
            log.warning(
                f"InMemoryBuffer.put rejected: buffer full "
                f"(size={self._queue.qsize()}/{self._maxsize}, timeout={timeout}s)"
            )
            return False
        except asyncio.QueueFull:
            self._total_rejected += 1
            log.warning(
                f"InMemoryBuffer.put rejected: buffer full "
                f"(size={self._queue.qsize()}/{self._maxsize})"
            )
            return False

    async def get(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        try:
            if timeout is not None:
                result = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                result = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            self._total_dequeued += 1
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None

    def size(self) -> int:
        return self._queue.qsize()

    def capacity(self) -> Optional[int]:
        return self._maxsize

    async def close(self) -> None:
        self._closed = True
        log.info(f"InMemoryBuffer closed. Stats: enqueued={self._total_enqueued}, "
                 f"dequeued={self._total_dequeued}, rejected={self._total_rejected}")

    def is_closed(self) -> bool:
        return self._closed

    async def drain(self) -> list[dict[str, Any]]:
        """Drain all remaining events without blocking."""
        events = []
        while not self._queue.empty():
            try:
                events.append(self._queue.get_nowait())
                self._total_dequeued += 1
            except asyncio.QueueEmpty:
                break
        return events

    # -- Diagnostics --

    def stats(self) -> dict[str, Any]:
        """Return buffer statistics for monitoring."""
        return {
            "type": "in_memory",
            "size": self.size(),
            "capacity": self.capacity(),
            "total_enqueued": self._total_enqueued,
            "total_dequeued": self._total_dequeued,
            "total_rejected": self._total_rejected,
            "is_closed": self.is_closed(),
            "utilization_pct": round(self.size() / self._maxsize * 100, 2) if self._maxsize else None,
        }


# ---------------------------------------------------------------------------
# v2-Ready: Redis Streams Adapter (requires redis-py)
# ---------------------------------------------------------------------------

class RedisStreamBuffer(DurableBuffer):
    """
    v2-ready: Redis Streams backend.

    Activated when Pantheon deploys to multi-node or production environments.
    See BUFFER_CHOICE_ADR.md for activation criteria.

    Requires: redis-py (redis >= 4.0)
    """

    def __init__(
        self,
        stream_name: str = "pantheon:telemetry:ingest",
        redis_url: str = "redis://localhost:6379/0",
        maxsize: int = 1_000_000,
        maxlen: Optional[int] = 500_000,
    ):
        """
        Parameters
        ----------
        stream_name : str
            Redis stream key name.
        redis_url : str
            Redis connection URL.
        maxsize : int
            Application-side max pending events before backpressure.
        maxlen : int, optional
            Redis stream MAXLEN trim limit. None = no trim.
        """
        self._stream_name = stream_name
        self._redis_url = redis_url
        self._maxsize = maxsize
        self._maxlen = maxlen
        self._closed = False
        self._total_enqueued = 0
        self._total_dequeued = 0
        self._total_rejected = 0
        self._consumer_group: Optional[str] = None
        self._consumer_name: Optional[str] = None
        self._last_id: str = "0"  # For replay

        # Lazy-initialized
        self._redis_client = None

    async def _ensure_client(self):
        """Lazily initialize the Redis client."""
        if self._redis_client is not None:
            return self._redis_client

        try:
            import redis.asyncio as aioredis
        except ImportError:
            log.error("RedisStreamBuffer requires redis-py (redis >= 4.0) with async support")
            raise ImportError("redis package is required for RedisStreamBuffer")

        self._redis_client = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
        )
        return self._redis_client

    async def put(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        if self._closed:
            self._total_rejected += 1
            return False

        # Application-side backpressure check
        if self._maxsize is not None:
            try:
                client = await self._ensure_client()
                current_len = await client.xlen(self._stream_name)
                if current_len >= self._maxsize:
                    self._total_rejected += 1
                    log.warning(
                        f"RedisStreamBuffer.put rejected: stream at capacity "
                        f"(len={current_len}/{self._maxsize})"
                    )
                    return False
            except Exception as e:
                log.error(f"RedisStreamBuffer.put Redis error during capacity check: {e}")
                self._total_rejected += 1
                return False

        try:
            client = await self._ensure_client()
            import json as _json
            # XADD with optional MAXLEN trim
            kwargs: dict[str, Any] = {"message_data": _json.dumps(event)}
            if self._maxlen is not None:
                kwargs["nomkstream"] = False
                result = await client.xadd(
                    self._stream_name,
                    fields=kwargs,
                    maxlen=self._maxlen,
                    approximate=True,
                )
            else:
                result = await client.xadd(self._stream_name, fields=kwargs)

            if result:
                self._total_enqueued += 1
                return True
            else:
                self._total_rejected += 1
                return False
        except Exception as e:
            log.error(f"RedisStreamBuffer.put error: {e}")
            self._total_rejected += 1
            return False

    async def get(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        try:
            client = await self._ensure_client()
            import json as _json

            read_timeout = int((timeout or 1.0) * 1000)  # Redis uses ms
            entries = await client.xread(
                {self._stream_name: self._last_id},
                count=1,
                block=read_timeout,
            )

            if not entries:
                return None

            # entries = [(stream_name, [(id, {field: value})])]
            for _, messages in entries:
                for msg_id, fields in messages:
                    self._last_id = msg_id
                    self._total_dequeued += 1
                    return _json.loads(fields.get("message_data", "{}"))
            return None
        except Exception as e:
            log.error(f"RedisStreamBuffer.get error: {e}")
            return None

    def size(self) -> int:
        # Synchronous approximation — real size requires async call
        return 0  # Will be updated by monitoring task

    async def stream_length(self) -> int:
        """Get actual Redis stream length."""
        try:
            client = await self._ensure_client()
            return await client.xlen(self._stream_name)
        except Exception:
            return 0

    def capacity(self) -> Optional[int]:
        return self._maxsize

    async def close(self) -> None:
        self._closed = True
        if self._redis_client:
            await self._redis_client.close()
        log.info(f"RedisStreamBuffer closed. Stats: enqueued={self._total_enqueued}, "
                 f"dequeued={self._total_dequeued}, rejected={self._total_rejected}")

    def is_closed(self) -> bool:
        return self._closed

    async def drain(self) -> list[dict[str, Any]]:
        """Drain remaining events from stream (reads from last_id onward)."""
        import json as _json
        events = []
        try:
            client = await self._ensure_client()
            while True:
                entries = await client.xread(
                    {self._stream_name: self._last_id},
                    count=100,
                    block=0,
                )
                if not entries:
                    break
                for _, messages in entries:
                    for msg_id, fields in messages:
                        self._last_id = msg_id
                        self._total_dequeued += 1
                        events.append(_json.loads(fields.get("message_data", "{}")))
        except Exception as e:
            log.error(f"RedisStreamBuffer.drain error: {e}")
        return events

    def stats(self) -> dict[str, Any]:
        return {
            "type": "redis_stream",
            "stream_name": self._stream_name,
            "total_enqueued": self._total_enqueued,
            "total_dequeued": self._total_dequeued,
            "total_rejected": self._total_rejected,
            "is_closed": self.is_closed(),
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_buffer(
    backend: str = "memory",
    **kwargs: Any,
) -> DurableBuffer:
    """
    Factory to create a DurableBuffer based on configuration.

    Parameters
    ----------
    backend : str
        One of "memory", "redis".
    **kwargs
        Passed through to the buffer constructor.

    Returns
    -------
    DurableBuffer
    """
    if backend == "memory":
        maxsize = kwargs.get("maxsize", 100_000)
        return InMemoryBuffer(maxsize=maxsize)
    elif backend == "redis":
        return RedisStreamBuffer(**kwargs)
    else:
        raise ValueError(f"Unknown buffer backend: {backend}. Use 'memory' or 'redis'.")

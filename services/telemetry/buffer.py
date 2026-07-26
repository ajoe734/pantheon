"""
Durable buffer abstraction for telemetry ingest shock absorption.

TEL-002: Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §3.1 Layer C,
event producers push into a durable buffer/stream — not directly into
canonical Postgres. This module defines the buffer protocol and provides:

1. InMemoryBuffer — bounded asyncio.Queue for explicit unit/dev use only
2. RedisStreamBuffer — Redis Streams backend, activated by config
3. NatsJetStreamBuffer — file-backed work queue with durable consumer ACKs

The async batch writer, backpressure controller, and dead-letter queue
are buffer-agnostic — they work against the DurableBuffer protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Any, Optional

log = logging.getLogger(__name__)


def _durable_receipt_id(event: dict[str, Any]) -> str:
    """Return a tenant- and content-bound broker idempotency key.

    ``event_id`` remains the canonical Postgres idempotency key. The broker
    receipt additionally binds the complete immutable payload so a conflicting
    retry after an ingest-process restart is retained for canonical conflict
    handling instead of being silently suppressed by JetStream deduplication.
    """

    payload = json.dumps(
        event,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class DurableBuffer(ABC):
    """
    Abstract durable buffer interface.

    Every buffer implementation MUST support:
    - put(event, timeout): non-blocking or bounded-wait enqueue
    - get(timeout): dequeue with timeout
    - ack(events): remove events only after the canonical writer succeeds
    - release(events): make fetched events available for retry
    - size(): current depth
    - capacity(): max depth (None = unbounded)
    - close(): graceful shutdown
    - is_closed(): shutdown state
    """

    @abstractmethod
    async def start(self) -> None:
        """Establish and validate any durable backend resources."""
        ...

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
    async def ack(self, events: list[dict[str, Any]]) -> None:
        """Acknowledge events after their canonical write succeeds."""
        ...

    @abstractmethod
    async def release(self, events: list[dict[str, Any]]) -> bool:
        """Release fetched events for retry; return False if any release fails."""
        ...

    @abstractmethod
    def is_durable(self) -> bool:
        """Whether a successful put survives process death."""
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
# Explicit Local/Test Backend: In-Memory Bounded Buffer
# ---------------------------------------------------------------------------

class InMemoryBuffer(DurableBuffer):
    """
    Explicit local-development/test backend: bounded asyncio.Queue.

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

    async def start(self) -> None:
        return None

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

    async def ack(self, events: list[dict[str, Any]]) -> None:
        # asyncio.Queue removes on get(); this backend is explicitly volatile.
        return None

    async def release(self, events: list[dict[str, Any]]) -> bool:
        released = True
        for event in events:
            released = await self.put(event) and released
        return released

    def is_durable(self) -> bool:
        return False

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
            "durable": False,
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
        self._pending: dict[str, deque[tuple[str, dict[str, Any]]]] = defaultdict(deque)
        self._released: deque[tuple[str, dict[str, Any]]] = deque()

        # Lazy-initialized
        self._redis_client = None

    async def start(self) -> None:
        client = await self._ensure_client()
        await client.ping()

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
            if self._released:
                msg_id, event = self._released.popleft()
                receipt_id = _durable_receipt_id(event)
                self._pending[receipt_id].append((msg_id, event))
                return event

            client = await self._ensure_client()

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
                    event = json.loads(fields.get("message_data", "{}"))
                    receipt_id = _durable_receipt_id(event)
                    self._pending[receipt_id].append((msg_id, event))
                    return event
            return None
        except Exception as e:
            log.error(f"RedisStreamBuffer.get error: {e}")
            return None

    async def ack(self, events: list[dict[str, Any]]) -> None:
        client = await self._ensure_client()
        message_ids: list[str] = []
        for event in events:
            receipt_id = _durable_receipt_id(event)
            pending = self._pending.get(receipt_id)
            if pending:
                msg_id, _ = pending.popleft()
                message_ids.append(msg_id)
                if not pending:
                    self._pending.pop(receipt_id, None)
        if message_ids:
            await client.xdel(self._stream_name, *message_ids)

    async def release(self, events: list[dict[str, Any]]) -> bool:
        for event in events:
            receipt_id = _durable_receipt_id(event)
            pending = self._pending.get(receipt_id)
            if not pending:
                continue
            item = pending.popleft()
            self._released.append(item)
            if not pending:
                self._pending.pop(receipt_id, None)
        return True

    def is_durable(self) -> bool:
        return True

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
        events = [event for _, event in self._released]
        self._released.clear()
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
                        event = json.loads(fields.get("message_data", "{}"))
                        receipt_id = _durable_receipt_id(event)
                        self._pending[receipt_id].append((msg_id, event))
                        events.append(event)
        except Exception as e:
            log.error(f"RedisStreamBuffer.drain error: {e}")
        return events

    def stats(self) -> dict[str, Any]:
        return {
            "type": "redis_stream",
            "durable": True,
            "stream_name": self._stream_name,
            "total_enqueued": self._total_enqueued,
            "total_dequeued": self._total_dequeued,
            "total_rejected": self._total_rejected,
            "is_closed": self.is_closed(),
        }


# ---------------------------------------------------------------------------
# v1 Production: NATS JetStream Work Queue
# ---------------------------------------------------------------------------

class NatsJetStreamBuffer(DurableBuffer):
    """File-backed JetStream work queue with explicit canonical-write ACKs.

    ``put`` returns only after the server's PubAck confirms persistence.
    ``get`` leaves the message pending on a durable pull consumer.  The batch
    writer calls ``ack`` only after Postgres accepts the event, so a process
    death between HTTP acknowledgement and batch flush causes redelivery
    rather than loss.
    """

    def __init__(
        self,
        nats_url: str = "nats://localhost:4222",
        stream_name: str = "PANTHEON_TELEMETRY_INGEST",
        subject: str = "pantheon.telemetry.ingest",
        durable_name: str = "telemetry-postgres-writer",
        maxsize: int = 1_000_000,
        ack_wait: float = 120.0,
        duplicate_window: float = 86_400.0,
    ) -> None:
        self._nats_url = nats_url
        self._stream_name = stream_name
        self._subject = subject
        self._durable_name = durable_name
        self._maxsize = max(int(maxsize), 1)
        self._ack_wait = max(float(ack_wait), 1.0)
        self._duplicate_window = max(float(duplicate_window), 1.0)
        self._closed = False
        self._nc = None
        self._js = None
        self._subscription = None
        self._pending: dict[str, deque[Any]] = defaultdict(deque)
        self._estimated_depth = 0
        self._total_enqueued = 0
        self._total_dequeued = 0
        self._total_acked = 0
        self._total_released = 0
        self._total_rejected = 0

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("NatsJetStreamBuffer is closed")
        if self._subscription is not None:
            return

        try:
            import nats
            from nats.js import api
            from nats.js.errors import NotFoundError
        except ImportError as exc:
            raise ImportError(
                "nats-py is required for the JetStream telemetry buffer"
            ) from exc

        self._nc = await nats.connect(
            servers=[self._nats_url],
            name="pantheon-telemetry-ingest",
            connect_timeout=5,
            max_reconnect_attempts=-1,
        )
        self._js = self._nc.jetstream(timeout=5)
        config = api.StreamConfig(
            name=self._stream_name,
            description="Pantheon telemetry durable ingest receipts",
            subjects=[self._subject],
            retention=api.RetentionPolicy.WORK_QUEUE,
            max_msgs=self._maxsize,
            discard=api.DiscardPolicy.NEW,
            storage=api.StorageType.FILE,
            duplicate_window=self._duplicate_window,
        )
        try:
            info = await self._js.stream_info(self._stream_name)
        except NotFoundError:
            info = await self._js.add_stream(config=config)
        self._validate_stream(info.config, api)

        consumer_config = api.ConsumerConfig(
            durable_name=self._durable_name,
            ack_policy=api.AckPolicy.EXPLICIT,
            ack_wait=self._ack_wait,
            max_deliver=-1,
            filter_subject=self._subject,
            replay_policy=api.ReplayPolicy.INSTANT,
            max_ack_pending=min(self._maxsize, 20_000),
        )
        self._subscription = await self._js.pull_subscribe(
            self._subject,
            durable=self._durable_name,
            stream=self._stream_name,
            config=consumer_config,
        )
        consumer_info = await self._subscription.consumer_info()
        self._validate_consumer(consumer_info.config, api)
        self._estimated_depth = int(
            getattr(consumer_info, "num_pending", 0)
            + getattr(consumer_info, "num_ack_pending", 0)
        )

    def _validate_stream(self, config: Any, api: Any) -> None:
        subjects = set(getattr(config, "subjects", None) or [])
        if (
            getattr(config, "retention", None) != api.RetentionPolicy.WORK_QUEUE
            or getattr(config, "storage", None) != api.StorageType.FILE
            or getattr(config, "discard", None) != api.DiscardPolicy.NEW
            or self._subject not in subjects
        ):
            raise RuntimeError(
                f"JetStream stream {self._stream_name!r} has unsafe durability config"
            )

    def _validate_consumer(self, config: Any, api: Any) -> None:
        if (
            getattr(config, "ack_policy", None) != api.AckPolicy.EXPLICIT
            or getattr(config, "filter_subject", None) != self._subject
        ):
            raise RuntimeError(
                f"JetStream consumer {self._durable_name!r} has unsafe ACK config"
            )

    async def _ensure_started(self) -> None:
        if self._subscription is None:
            await self.start()

    async def put(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        if self._closed:
            self._total_rejected += 1
            return False
        await self._ensure_started()
        event_id = str(event.get("event_id") or "").strip()
        tenant_id = str(event.get("tenant_id") or "").strip()
        if not event_id or not tenant_id:
            self._total_rejected += 1
            log.error("JetStream buffer requires event_id and tenant_id")
            return False
        try:
            payload = json.dumps(
                event,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            receipt_id = f"sha256:{hashlib.sha256(payload).hexdigest()}"
            publish_timeout = max(float(timeout), 0.001) if timeout is not None else 5.0
            await self._js.publish(
                self._subject,
                payload,
                timeout=publish_timeout,
                stream=self._stream_name,
                headers={
                    "Nats-Msg-Id": receipt_id,
                    "Pantheon-Tenant-Id": tenant_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._total_rejected += 1
            log.error("NatsJetStreamBuffer.put failed: %s", exc)
            return False
        self._total_enqueued += 1
        self._estimated_depth += 1
        return True

    async def get(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        await self._ensure_started()
        try:
            messages = await self._subscription.fetch(
                batch=1,
                timeout=max(float(timeout or 1.0), 0.001),
            )
        except Exception as exc:  # nats.errors.TimeoutError is an expected empty poll
            if type(exc).__name__ != "TimeoutError":
                log.error("NatsJetStreamBuffer.get failed: %s", exc)
            return None
        if not messages:
            return None
        message = messages[0]
        try:
            event = json.loads(message.data.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("JetStream payload is not a JSON object")
            event_id = str(event.get("event_id") or "").strip()
            if not event_id:
                raise ValueError("JetStream payload has no event_id")
        except Exception as exc:  # noqa: BLE001
            log.error("Terminating invalid JetStream telemetry record: %s", exc)
            await message.term()
            self._total_rejected += 1
            self._estimated_depth = max(0, self._estimated_depth - 1)
            return None
        receipt_id = _durable_receipt_id(event)
        self._pending[receipt_id].append(message)
        self._total_dequeued += 1
        return event

    async def ack(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            receipt_id = _durable_receipt_id(event)
            pending = self._pending.get(receipt_id)
            if not pending:
                continue
            message = pending[0]
            await message.ack_sync(timeout=5.0)
            pending.popleft()
            if not pending:
                self._pending.pop(receipt_id, None)
            self._total_acked += 1
            self._estimated_depth = max(0, self._estimated_depth - 1)

    async def release(self, events: list[dict[str, Any]]) -> bool:
        for event in events:
            receipt_id = _durable_receipt_id(event)
            pending = self._pending.get(receipt_id)
            if not pending:
                continue
            message = pending.popleft()
            await message.nak(delay=0.1)
            if not pending:
                self._pending.pop(receipt_id, None)
            self._total_released += 1
        return True

    def size(self) -> int:
        return self._estimated_depth

    def capacity(self) -> Optional[int]:
        return self._maxsize

    def is_durable(self) -> bool:
        return True

    async def close(self) -> None:
        self._closed = True
        if self._nc is not None and not self._nc.is_closed:
            await self._nc.close()
        self._subscription = None

    def is_closed(self) -> bool:
        return self._closed

    async def drain(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            event = await self.get(timeout=0.01)
            if event is None:
                break
            events.append(event)
        return events

    def stats(self) -> dict[str, Any]:
        return {
            "type": "nats_jetstream",
            "durable": True,
            "stream_name": self._stream_name,
            "subject": self._subject,
            "durable_consumer": self._durable_name,
            "size": self.size(),
            "capacity": self.capacity(),
            "total_enqueued": self._total_enqueued,
            "total_dequeued": self._total_dequeued,
            "total_acked": self._total_acked,
            "total_released": self._total_released,
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
        One of "memory", "redis", "jetstream".
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
    elif backend in {"jetstream", "nats", "nats_jetstream"}:
        return NatsJetStreamBuffer(**kwargs)
    else:
        raise ValueError(
            f"Unknown buffer backend: {backend}. "
            "Use 'memory', 'redis', or 'jetstream'."
        )

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

import hashlib
import json
import os
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ExecutionFence:
    """Result of atomically reserving one signal's downstream side effect.

    ``acquired`` grants the returned claim token the only execution authority.
    ``in_progress`` means another claim already crossed the effect boundary.
    ``completed`` means the durable idempotency commit already exists.
    ``lost_claim`` refuses a stale or reclaimed queue claim.
    """

    status: str
    token: str | None = None


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

    def renew_claim(self, signal_or_id: str | dict[str, Any]) -> bool:
        """Extend a live claim and return false when ownership already expired."""

    def begin_execution(self, signal_or_id: str | dict[str, Any]) -> ExecutionFence:
        """Atomically reserve the signal's downstream side-effect boundary."""

    def complete_execution(self, signal_id: str, fence_token: str) -> bool:
        """Atomically commit processed identity for the owning execution token."""

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
        self._execution_fences: dict[str, str] = {}

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

    def renew_claim(self, signal_or_id: str | dict[str, Any]) -> bool:
        sid = signal_or_id if isinstance(signal_or_id, str) else str(signal_or_id.get("signal_id", ""))
        return sid in self._inflight

    def begin_execution(self, signal_or_id: str | dict[str, Any]) -> ExecutionFence:
        sid = signal_or_id if isinstance(signal_or_id, str) else str(signal_or_id.get("signal_id", ""))
        if not sid or sid not in self._inflight:
            return ExecutionFence("lost_claim")
        existing = self._execution_fences.get(sid)
        if existing is None:
            token = f"memory:{sid}"
            self._execution_fences[sid] = f"started:{token}"
            return ExecutionFence("acquired", token)
        state, _, token = existing.partition(":")
        if state == "completed":
            return ExecutionFence("completed", token)
        if existing == f"started:memory:{sid}":
            return ExecutionFence("acquired", f"memory:{sid}")
        return ExecutionFence("in_progress", token)

    def complete_execution(self, signal_id: str, fence_token: str) -> bool:
        sid = str(signal_id)
        expected = f"started:{fence_token}"
        existing = self._execution_fences.get(sid)
        if existing not in {expected, f"completed:{fence_token}"}:
            return False
        self._processed.add(sid)
        self._execution_fences[sid] = f"completed:{fence_token}"
        return True

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

    Every state transition is one Redis Lua transaction.  A claim atomically
    removes one pending payload, assigns a unique claim token, stores the
    payload in a worker-scoped in-flight hash, and records its server-clock
    claim time in a sorted set.  Ack, nack/requeue, DLQ transfer, and expired
    reclaim are likewise atomic, so a client crash or response loss can cause
    a safe redelivery but cannot create a signal-loss window.
    """

    kind = "redis_pending_signal_store"

    _CLAIM_LUA = """
local raw = redis.call('LPOP', KEYS[1])
if not raw then
  return nil
end
local sequence = redis.call('INCR', KEYS[4])
local token = ARGV[1] .. ':' .. tostring(sequence)
local server_time = redis.call('TIME')
local claimed_at = tonumber(server_time[1]) + (tonumber(server_time[2]) / 1000000)
redis.call('HSET', KEYS[2], token, raw)
redis.call('ZADD', KEYS[3], claimed_at, token)
return {raw, token, tostring(claimed_at)}
"""

    _ACK_LUA = """
local removed = redis.call('HDEL', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return removed
"""

    _RENEW_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
local claimed_at = redis.call('ZSCORE', KEYS[2], ARGV[1])
if not raw or not claimed_at then
  return 0
end
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) + (tonumber(server_time[2]) / 1000000)
local cutoff = now - tonumber(ARGV[2])
if tonumber(claimed_at) <= cutoff then
  return 0
end
redis.call('ZADD', KEYS[2], now, ARGV[1])
return 1
"""

    _BEGIN_EXECUTION_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
local claimed_at = redis.call('ZSCORE', KEYS[2], ARGV[1])
if not raw or not claimed_at then
  return {0, ''}
end
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) + (tonumber(server_time[2]) / 1000000)
local cutoff = now - tonumber(ARGV[2])
if tonumber(claimed_at) <= cutoff then
  return {0, ''}
end
local started = 'started:' .. ARGV[1]
local completed = 'completed:' .. ARGV[1]
local existing = redis.call('GET', KEYS[3])
if not existing then
  redis.call('SET', KEYS[3], started)
  return {1, ARGV[1]}
end
if existing == started then
  return {1, ARGV[1]}
end
if existing == completed or string.sub(existing, 1, 10) == 'completed:' then
  return {3, existing}
end
return {2, existing}
"""

    _COMPLETE_EXECUTION_LUA = """
local started = 'started:' .. ARGV[1]
local completed = 'completed:' .. ARGV[1]
local existing = redis.call('GET', KEYS[1])
if existing ~= started and existing ~= completed then
  return 0
end
redis.call('SETEX', KEYS[2], tonumber(ARGV[2]), '1')
redis.call('SETEX', KEYS[1], tonumber(ARGV[2]), completed)
return 1
"""

    _TRANSFER_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then
  redis.call('ZREM', KEYS[2], ARGV[1])
  return 0
end
redis.call('HDEL', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
if ARGV[2] ~= '' then
  raw = ARGV[2]
end
redis.call('RPUSH', KEYS[3], raw)
return 1
"""

    _RECLAIM_LUA = """
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) + (tonumber(server_time[2]) / 1000000)
local cutoff = now - tonumber(ARGV[1])
local tokens = redis.call(
  'ZRANGEBYSCORE', KEYS[2], '-inf', cutoff, 'LIMIT', 0, tonumber(ARGV[2])
)
local moved = 0
for _, token in ipairs(tokens) do
  local raw = redis.call('HGET', KEYS[1], token)
  if raw then
    redis.call('HDEL', KEYS[1], token)
    redis.call('RPUSH', KEYS[3], raw)
    moved = moved + 1
  end
  redis.call('ZREM', KEYS[2], token)
end
return moved
"""

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
        self._inflight_key = f"{queue_key}:inflight:{self._worker_id}:claims"
        self._visibility_key = f"{queue_key}:inflight:{self._worker_id}:visibility"
        self._claim_sequence_key = f"{queue_key}:claim-sequence"
        self._visibility_timeout = max(float(visibility_timeout_seconds), 0.05)
        self._processed_ttl_seconds = 24 * 60 * 60
        self._processed_prefix = f"{queue_key}:processed:"
        self._execution_prefix = f"{queue_key}:execution:"
        self._dlq_key = queue_key.replace(BINDING_QUEUE_KEY_PREFIX, BINDING_DLQ_KEY_PREFIX, 1)
        self._claim_tokens_by_signal_id: dict[str, list[str]] = {}
        self._claim_tokens_by_raw: dict[str, list[str]] = {}

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
            result = self._client.eval(
                self._CLAIM_LUA,
                4,
                self._queue_key,
                self._inflight_key,
                self._visibility_key,
                self._claim_sequence_key,
                self._worker_id,
            )
            if not result:
                break
            raw = self._as_text(result[0])
            token = self._as_text(result[1])
            try:
                sig = json.loads(raw)
            except Exception:
                self._transfer_claim(token, self._dlq_key, raw)
                continue
            if not isinstance(sig, dict) or not str(sig.get("signal_id") or "").strip():
                invalid = json.dumps(
                    {
                        "raw_payload": sig,
                        "_dlq_reason": "invalid_claim_payload",
                    },
                    sort_keys=True,
                )
                self._transfer_claim(token, self._dlq_key, invalid)
                continue
            self._remember_claim(token, raw, sig)
            drained.append(sig)
        return drained

    def ack(self, signal_or_id: str | dict[str, Any]) -> None:
        """Remove claimed item from in-flight queue after successful execution."""
        token = self._claim_token_for(signal_or_id)
        if token is None:
            return
        self._client.eval(
            self._ACK_LUA,
            2,
            self._inflight_key,
            self._visibility_key,
            token,
        )
        self._forget_claim(token)

    def nack_requeue(self, signal_or_id: str | dict[str, Any]) -> None:
        """Atomically return a claimed item to the pending queue."""
        token = self._claim_token_for(signal_or_id)
        if token is None:
            return
        self._transfer_claim(token, self._queue_key)

    def renew_claim(self, signal_or_id: str | dict[str, Any]) -> bool:
        """Renew only an unexpired worker-scoped claim using Redis server time.

        An expired token cannot be revived, even when its payload has not yet
        been reclaimed.  This makes a false result a fencing decision: callers
        must not execute the stale local copy.
        """
        token = self._claim_token_for(signal_or_id)
        if token is None:
            return False
        renewed = bool(
            int(
                self._client.eval(
                    self._RENEW_LUA,
                    2,
                    self._inflight_key,
                    self._visibility_key,
                    token,
                    self._visibility_timeout,
                )
                or 0
            )
        )
        if not renewed:
            self._forget_claim(token)
        return renewed

    def claim_renewal_interval_seconds(self) -> float:
        """Return a safe heartbeat interval for the current visibility lease."""
        return max(min(self._visibility_timeout / 3.0, 5.0), 0.01)

    def begin_execution(self, signal_or_id: str | dict[str, Any]) -> ExecutionFence:
        """Acquire a durable idempotency fence before calling the executor.

        The Lua transaction validates the current claim using Redis server
        time and creates exactly one signal-scoped execution reservation.
        Losing the shorter queue visibility lease does not transfer this
        already-started side-effect authority to a reclaiming worker.
        """
        signal_id = (
            signal_or_id
            if isinstance(signal_or_id, str)
            else str(signal_or_id.get("signal_id", ""))
        )
        signal_id = str(signal_id).strip()
        token = self._claim_token_for(signal_or_id)
        if not signal_id or token is None:
            return ExecutionFence("lost_claim")
        result = self._client.eval(
            self._BEGIN_EXECUTION_LUA,
            3,
            self._inflight_key,
            self._visibility_key,
            self._execution_key(signal_id),
            token,
            self._visibility_timeout,
        )
        code = int(result[0]) if result else 0
        returned_token = self._as_text(result[1]) if result and len(result) > 1 else None
        status = {
            0: "lost_claim",
            1: "acquired",
            2: "in_progress",
            3: "completed",
        }.get(code, "lost_claim")
        return ExecutionFence(status, returned_token or None)

    def complete_execution(self, signal_id: str, fence_token: str) -> bool:
        """Commit processed identity only for the execution reservation owner."""
        sid = str(signal_id).strip()
        token = str(fence_token).strip()
        if not sid or not token:
            return False
        return bool(
            int(
                self._client.eval(
                    self._COMPLETE_EXECUTION_LUA,
                    2,
                    self._execution_key(sid),
                    self._processed_prefix + sid,
                    token,
                    self._processed_ttl_seconds,
                )
                or 0
            )
        )

    def reclaim_expired_inflight(self) -> int:
        """Reclaim expired in-flight entries across workers back to pending queue."""
        pattern = f"{self._queue_key}:inflight:*:claims"
        cursor = 0
        moved = 0
        while True:
            cursor, claim_keys = self._client.scan(
                cursor=cursor,
                match=pattern,
                count=100,
            )
            for claim_key_value in claim_keys:
                claim_key = self._as_text(claim_key_value)
                visibility_key = claim_key[: -len(":claims")] + ":visibility"
                moved += int(
                    self._client.eval(
                        self._RECLAIM_LUA,
                        3,
                        claim_key,
                        visibility_key,
                        self._queue_key,
                        self._visibility_timeout,
                        1000,
                    )
                    or 0
                )
            if int(cursor) == 0:
                break
        return moved

    def queue_depth(self) -> int:
        return int(self._client.llen(self._queue_key))

    def inflight_depth(self) -> int:
        return int(self._client.hlen(self._inflight_key))

    def enqueue_dlq(self, payload: dict[str, Any]) -> None:
        """Atomically move a claimed signal into the binding-scoped Redis DLQ."""
        token = self._claim_token_for(payload)
        if token is None:
            raise RuntimeError("cannot DLQ a Redis signal without an active claim")
        self._transfer_claim(
            token,
            self._dlq_key,
            json.dumps(payload, sort_keys=True),
        )

    def dlq_depth(self) -> int:
        try:
            return int(self._client.llen(self._dlq_key))
        except Exception:  # noqa: BLE001
            return -1

    @staticmethod
    def _as_text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def _remember_claim(self, token: str, raw: str, signal: dict[str, Any]) -> None:
        signal_id = str(signal.get("signal_id") or "").strip()
        self._claim_tokens_by_signal_id.setdefault(signal_id, []).append(token)
        self._claim_tokens_by_raw.setdefault(raw, []).append(token)

    def _execution_key(self, signal_id: str) -> str:
        digest = hashlib.sha256(str(signal_id).encode("utf-8")).hexdigest()
        return self._execution_prefix + digest

    def _claim_token_for(self, signal_or_id: str | dict[str, Any]) -> str | None:
        if isinstance(signal_or_id, dict):
            signal_id = str(signal_or_id.get("signal_id") or "").strip()
            raw = json.dumps(signal_or_id)
        else:
            signal_id = str(signal_or_id).strip()
            raw = str(signal_or_id)
        candidates = list(self._claim_tokens_by_signal_id.get(signal_id) or [])
        for token in candidates:
            if self._client.hexists(self._inflight_key, token):
                return token
            self._forget_claim(token)
        raw_candidates = list(self._claim_tokens_by_raw.get(raw) or [])
        for token in raw_candidates:
            if self._client.hexists(self._inflight_key, token):
                return token
            self._forget_claim(token)

        # A caller may acknowledge after rebuilding its local consumer object.
        # Resolve that claim from the worker-scoped durable hash, then perform
        # the state transition atomically by token.
        for token_value, raw_value in self._client.hscan_iter(self._inflight_key):
            token = self._as_text(token_value)
            stored_raw = self._as_text(raw_value)
            if stored_raw == raw:
                return token
            try:
                stored_signal_id = str(json.loads(stored_raw).get("signal_id") or "")
            except Exception:
                stored_signal_id = ""
            if signal_id and stored_signal_id == signal_id:
                return token
        return None

    def _transfer_claim(
        self,
        token: str,
        destination_key: str,
        replacement_payload: str = "",
    ) -> None:
        self._client.eval(
            self._TRANSFER_LUA,
            3,
            self._inflight_key,
            self._visibility_key,
            destination_key,
            token,
            replacement_payload,
        )
        self._forget_claim(token)

    def _forget_claim(self, token: str) -> None:
        for index in (self._claim_tokens_by_signal_id, self._claim_tokens_by_raw):
            for key, tokens in list(index.items()):
                if token in tokens:
                    tokens.remove(token)
                if not tokens:
                    index.pop(key, None)


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

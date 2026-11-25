"""Dead-letter replay across the JetStream duplicate window (RC2).

TELEMETRY-REPLAY-BROKER-DEDUP-20261125:
1. NatsJetStreamBuffer.put inspects PubAck.duplicate: duplicate=True does not
   increment depth or total_enqueued and returns a typed PutResult (truthy for
   idempotent producer retries, with duplicate=True / enqueued=False).
2. replay_dlq publishes each dead-letter entry with a replay-specific message ID
   (content hash + rejected_at) so JetStream does not drop it inside the
   original publish's duplicate window, while replaying the same entry twice
   inside the duplicate window is deduplicated. Returns the count of entries
   really enqueued and reports suppressed and refused counts separately.
3. End-to-end test over NatsJetStreamBuffer + AsyncBatchWriter +
   build_postgres_write_fn proves: victim event dead-lettered through the
   non-retryable path and replayed inside the duplicate window is stored once,
   its dead letter is settled per the existing rule, depth returns to its
   previous value, a conflicting event is dead-lettered again, and replaying
   twice stores once.
4. Depth accounting returns buffer_size to the broker's real pending count.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import patch

from services.telemetry.buffer import NatsJetStreamBuffer, _durable_receipt_id
from services.telemetry.dead_letter import (
    TAG_POISON_EVENT,
    TAG_SCHEMA_VIOLATION,
    TAG_WRITER_ERROR,
)
from services.telemetry.ingest_svc import (
    TelemetryIngestService,
    build_postgres_write_fn,
)
from services.telemetry.test_conflict_isolation import FakeDb, _driver


_BINDING_ID = "550e8400-e29b-41d4-a716-446655440001"
_TENANT_ID = "tenant-dev"


def _make_valid_event(
    event_id: str,
    *,
    event_type: str = "order_submitted",
    created_at: str = "2026-11-24T23:35:00Z",
    pnl: float = 100.0,
    tenant_id: str = _TENANT_ID,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "event_id": event_id,
        "event_type": event_type,
        "created_at": created_at,
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": _BINDING_ID,
        "runtime_id": "lean-worker-1",
        "capital_pool_id": "pool-alpha",
        "artifact_id": "artifact-123",
        "artifact_version": "1.0.0",
        "plan_id": "plan-456",
        "persona_capital_binding_id": "pcb-789",
        "target": {"strategy_id": "strategy-1"},
        "metrics": {"pnl": pnl},
    }


class _BrokerMessage:
    """In-memory JetStream work-queue message with explicit ACK/NAK/TERM."""

    def __init__(
        self,
        broker: "DuplicateWindowJetStreamBroker",
        seq: int,
        payload: bytes,
        headers: dict[str, str],
    ) -> None:
        self._broker = broker
        self.seq = seq
        self.data = payload
        self.headers = dict(headers)
        self.acked = False
        self.naked = False
        self.termed = False

    async def ack_sync(self, timeout: float = 5.0) -> None:
        self.acked = True
        self._broker._on_ack(self)

    async def nak(self, delay: float = 0.0) -> None:
        self.naked = True
        self._broker._on_nak(self)

    async def term(self) -> None:
        self.termed = True
        self._broker._on_term(self)


class DuplicateWindowJetStreamBroker:
    """Work-queue JetStream test harness implementing the Nats-Msg-Id duplicate window.

    In JetStream WORK_QUEUE streams, ACKing a message removes it from the queue
    of deliverable messages, but the stream retains ``Nats-Msg-Id`` in its
    deduplication table for ``duplicate_window`` seconds and answers any publish
    carrying the same ``Nats-Msg-Id`` with ``PubAck(duplicate=True)`` without
    storing or delivering the message again.
    """

    def __init__(self, *, duplicate_window: float = 86_400.0) -> None:
        self.duplicate_window = float(duplicate_window)
        self.now_fn = time.monotonic
        self._seq = 0
        self._seen_msg_ids: dict[str, tuple[int, float]] = {}
        self._pending: deque[_BrokerMessage] = deque()
        self._ack_pending: dict[int, _BrokerMessage] = {}
        self.publish_history: list[dict[str, Any]] = []
        self.acked_seqs: list[int] = []
        self.naked_seqs: list[int] = []
        self.termed_seqs: list[int] = []

    def advance_time(self, seconds: float) -> None:
        base = self.now_fn()
        self.now_fn = lambda: base + seconds

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout: float = 5.0,
        stream: str = "PANTHEON_TELEMETRY_INGEST",
        headers: dict[str, str] | None = None,
    ) -> Any:
        hdrs = dict(headers or {})
        msg_id = hdrs.get("Nats-Msg-Id", "")
        now = self.now_fn()
        if msg_id and msg_id in self._seen_msg_ids:
            orig_seq, first_seen = self._seen_msg_ids[msg_id]
            if (now - first_seen) < self.duplicate_window:
                self.publish_history.append(
                    {
                        "subject": subject,
                        "stream": stream,
                        "headers": hdrs,
                        "duplicate": True,
                        "seq": orig_seq,
                    }
                )
                return types.SimpleNamespace(
                    stream=stream,
                    seq=orig_seq,
                    duplicate=True,
                )

        self._seq += 1
        seq = self._seq
        if msg_id:
            self._seen_msg_ids[msg_id] = (seq, now)
        msg = _BrokerMessage(self, seq, payload, hdrs)
        self._pending.append(msg)
        self.publish_history.append(
            {
                "subject": subject,
                "stream": stream,
                "headers": hdrs,
                "duplicate": False,
                "seq": seq,
            }
        )
        return types.SimpleNamespace(
            stream=stream,
            seq=seq,
            duplicate=False,
        )

    async def fetch(self, *, batch: int = 1, timeout: float = 1.0) -> list[_BrokerMessage]:
        if not self._pending:
            await asyncio.sleep(min(float(timeout), 0.01))
            if not self._pending:
                raise TimeoutError("nats: timeout")
        msg = self._pending.popleft()
        self._ack_pending[msg.seq] = msg
        return [msg]

    async def consumer_info(self) -> Any:
        return types.SimpleNamespace(
            num_pending=len(self._pending),
            num_ack_pending=len(self._ack_pending),
        )

    def _on_ack(self, msg: _BrokerMessage) -> None:
        self._ack_pending.pop(msg.seq, None)
        self.acked_seqs.append(msg.seq)

    def _on_nak(self, msg: _BrokerMessage) -> None:
        if self._ack_pending.pop(msg.seq, None) is not None:
            self._pending.appendleft(msg)
        self.naked_seqs.append(msg.seq)

    def _on_term(self, msg: _BrokerMessage) -> None:
        self._ack_pending.pop(msg.seq, None)
        self.termed_seqs.append(msg.seq)

    @property
    def real_pending_count(self) -> int:
        return len(self._pending) + len(self._ack_pending)

    def attach_buffer(self, buffer: NatsJetStreamBuffer) -> NatsJetStreamBuffer:
        buffer._js = self
        buffer._subscription = self
        return buffer


class ReplayBrokerDedupTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = FakeDb()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spill_path = str(Path(self.tmp.name) / "dead_letter.jsonl")
        self.broker = DuplicateWindowJetStreamBroker(duplicate_window=86_400.0)
        self.buffer = self.broker.attach_buffer(NatsJetStreamBuffer())
        patcher = patch.dict(sys.modules, {"asyncpg": _driver(self.db)})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _drain_writer(self, svc: TelemetryIngestService) -> None:
        for _ in range(100):
            if self.broker.real_pending_count == 0 and self.buffer.size() == 0:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)

    async def test_put_inspects_puback_duplicate_and_preserves_depth_and_enqueued_count(self) -> None:
        event = _make_valid_event("evt-dup-put-001")

        first = await self.buffer.put(event)
        self.assertTrue(first)
        self.assertTrue(getattr(first, "enqueued", False))
        self.assertFalse(getattr(first, "duplicate", True))
        self.assertEqual(self.buffer.size(), 1)
        self.assertEqual(self.buffer.stats()["total_enqueued"], 1)
        self.assertEqual(self.buffer.stats()["total_duplicates"], 0)

        # Second publish of identical content inside the duplicate window gets
        # PubAck(duplicate=True): truthy for callers, but not enqueued and
        # depth must stay at 1.
        second = await self.buffer.put(dict(event))
        self.assertTrue(second)
        self.assertFalse(getattr(second, "enqueued", True))
        self.assertTrue(getattr(second, "duplicate", False))
        self.assertEqual(self.buffer.size(), 1)
        self.assertEqual(self.buffer.stats()["total_enqueued"], 1)
        self.assertEqual(self.buffer.stats()["total_duplicates"], 1)

        # Fetch and ACK the single stored message; depth returns to 0.
        fetched = await self.buffer.get(timeout=0.1)
        self.assertEqual(fetched, event)
        await self.buffer.ack([fetched])
        self.assertEqual(self.buffer.size(), 0)
        self.assertEqual(self.broker.real_pending_count, 0)

    async def test_normal_producer_exact_retry_after_restart_is_idempotent_and_counted_as_duplicate(self) -> None:
        pg_write = build_postgres_write_fn("postgresql://x/db")
        svc = TelemetryIngestService(
            buffer=self.buffer,
            write_fn=pg_write,
            dlq_spill_path=self.spill_path,
            batch_size=10,
            batch_interval=0.01,
        )
        await svc.start()
        try:
            event = _make_valid_event("evt-normal-retry-001")
            self.assertTrue(await svc.ingest(event))
            await self._drain_writer(svc)
            self.assertIn("evt-normal-retry-001", self.db.rows)
            self.assertEqual(self.buffer.size(), 0)

            # Simulate in-memory dedup eviction / process restart while the
            # broker duplicate window still holds the receipt ID.
            svc._seen_event_ids.clear()
            retry_ok = await svc.ingest(dict(event))
            self.assertTrue(retry_ok)
            self.assertEqual(svc.stats()["service"]["total_ingested"], 1)
            self.assertEqual(svc.stats()["service"]["total_duplicates"], 1)
            self.assertEqual(self.buffer.size(), 0)
            self.assertEqual(self.buffer.stats()["total_enqueued"], 1)
            self.assertEqual(self.buffer.stats()["total_duplicates"], 1)
        finally:
            await svc.stop(graceful=True)

    async def test_dead_letter_replay_inside_duplicate_window_stores_once_and_re_dead_letters_conflict(self) -> None:
        pg_write = build_postgres_write_fn("postgresql://x/db")
        fail_non_retryable = False

        async def switchable_write_fn(batch: list[dict[str, Any]]):
            if fail_non_retryable:
                from services.telemetry.batch_writer import WriteResult
                return WriteResult.fail(
                    "conflicting duplicate event_id=evt-self-conflict",
                    retryable=False,
                )
            return await pg_write(batch)

        svc = TelemetryIngestService(
            buffer=self.buffer,
            write_fn=switchable_write_fn,
            dlq_spill_path=self.spill_path,
            batch_size=10,
            batch_interval=0.01,
        )
        await svc.start()
        try:
            # 1. Commit the canonical immutable row for evt-db-conflict first.
            committed_original = _make_valid_event("evt-db-conflict", pnl=100.0)
            self.assertTrue(await svc.ingest(committed_original))
            await self._drain_writer(svc)
            self.assertIn("evt-db-conflict", self.db.rows)
            self.assertEqual(self.buffer.size(), 0)
            previous_depth = self.buffer.size()
            self.assertEqual(previous_depth, 0)

            # 2. Publish a victim event, a conflicting reuse of evt-db-conflict
            #    (after dedup set clear / restart), and evt-self-conflict while
            #    the writer fails through the non-retryable path.
            svc._seen_event_ids.clear()
            fail_non_retryable = True
            victim = _make_valid_event("evt-victim-001", pnl=250.0)
            conflicting = _make_valid_event("evt-db-conflict", pnl=999.0)
            self_conflict = _make_valid_event("evt-self-conflict", pnl=500.0)

            self.assertTrue(await svc.ingest(victim))
            self.assertTrue(await svc.ingest(conflicting))
            self.assertTrue(await svc.ingest(self_conflict))
            await self._drain_writer(svc)

            # All three were dead-lettered and their original broker receipts
            # were settled (ACKed) per the existing rule; victim is not stored.
            self.assertNotIn("evt-victim-001", self.db.rows)
            self.assertEqual(self.db.rows["evt-db-conflict"][2]["metrics"]["pnl"], 100.0)
            self.assertEqual(self.buffer.size(), previous_depth)
            self.assertEqual(self.broker.real_pending_count, 0)
            self.assertEqual(len(svc.get_dlq_entries(tag_filter=TAG_POISON_EVENT)), 3)

            # 3. Restore canonical Postgres writer and replay inside the
            #    broker's duplicate window.
            fail_non_retryable = False
            replay_1 = await svc.replay_dlq(tag_filter=TAG_POISON_EVENT)
            await self._drain_writer(svc)

            # Victim is now stored once in Postgres; depth returned to 0.
            # (On dev head before the fix, JetStream drops the replay as a
            # duplicate of the original publish, so evt-victim-001 is missing
            # from self.db.rows and buffer.size() leaks to 2.)
            self.assertIn("evt-victim-001", self.db.rows)
            self.assertEqual(self.db.rows["evt-victim-001"][2]["metrics"]["pnl"], 250.0)
            self.assertEqual(self.buffer.size(), previous_depth)
            self.assertEqual(self.broker.real_pending_count, 0)
            self.assertEqual(int(replay_1), 2)
            self.assertEqual(replay_1.to_dict(), {"replayed": 2, "suppressed": 0, "refused": 1})

            # Conflicting event reached Postgres conflict isolation and was
            # dead-lettered again naming its own event_id, while the committed
            # row in Postgres was not overwritten.
            self.assertEqual(self.db.rows["evt-db-conflict"][2]["metrics"]["pnl"], 100.0)
            poison_entries = svc.get_dlq_entries(tag_filter=TAG_POISON_EVENT)
            self.assertEqual(len(poison_entries), 4)
            self.assertEqual(
                poison_entries[-1]["event"]["event_id"],
                "evt-db-conflict",
            )
            self.assertIn(
                "conflicting duplicate event_id=evt-db-conflict",
                poison_entries[-1]["reason"],
            )

            # 4. Replaying a second time inside the duplicate window is
            #    deduplicated by the broker: 0 enqueued, 1 suppressed (victim),
            #    2 refused (evt-self-conflict and newly confirmed evt-db-conflict),
            #    victim remains stored once, and depth stays 0.
            seq_before_second_replay = self.db.rows["evt-victim-001"][3]
            notifications_before = len(self.db.notifications)
            replay_2 = await svc.replay_dlq(tag_filter=TAG_POISON_EVENT)
            self.assertEqual(int(replay_2), 0)
            self.assertEqual(getattr(replay_2, "replayed", None), 0)
            self.assertEqual(getattr(replay_2, "suppressed", None), 1)
            self.assertEqual(getattr(replay_2, "refused", None), 2)
            await self._drain_writer(svc)

            self.assertEqual(self.db.rows["evt-victim-001"][3], seq_before_second_replay)
            self.assertEqual(len(self.db.notifications), notifications_before)
            self.assertEqual(self.buffer.size(), previous_depth)
            self.assertEqual(self.broker.real_pending_count, 0)
        finally:
            await svc.stop(graceful=True)

    async def test_depth_resynchronises_to_broker_real_pending_count(self) -> None:
        # Simulate a drifted depth estimate (e.g. from pre-fix suppressed publishes)
        self.buffer._estimated_depth = 12
        self.assertEqual(self.broker.real_pending_count, 0)

        # An empty poll resynchronises buffer_size to the broker's real pending count (0).
        self.assertIsNone(await self.buffer.get(timeout=0.01))
        self.assertEqual(self.buffer.size(), 0)

        # And a suppressed duplicate publish does not re-inflate buffer_size.
        event = _make_valid_event("evt-depth-sync-001")
        self.assertTrue(await self.buffer.put(event))
        self.assertEqual(self.buffer.size(), 1)
        self.assertTrue(await self.buffer.put(dict(event)))
        self.assertEqual(self.buffer.size(), 1)
        fetched = await self.buffer.get(timeout=0.05)
        self.assertEqual(fetched, event)
        await self.buffer.ack([fetched])
        self.assertEqual(self.buffer.size(), 0)

    async def test_hosted_11_24_shape_replays_15_and_stores_9_missing_victims_with_zero_idle_depth(self) -> None:
        pg_write = build_postgres_write_fn("postgresql://x/db")
        svc = TelemetryIngestService(
            buffer=self.buffer,
            write_fn=pg_write,
            dlq_spill_path=self.spill_path,
            batch_size=20,
            batch_interval=0.01,
        )
        await svc.start()
        try:
            # 3 victims from 11/23 (>24h old) and 3 from 11/24 already in Postgres;
            # 9 victims from 11/24 not in Postgres (their original publish is still
            # in the broker's 24h duplicate window); 2 self-conflicts (4 entries).
            nov23_events = [
                _make_valid_event(f"evt-1123-{i}", created_at="2026-11-23T10:00:00Z", pnl=float(i))
                for i in range(3)
            ]
            nov24_already_stored = [
                _make_valid_event(f"evt-1124-stored-{i}", created_at="2026-11-24T23:35:00Z", pnl=float(10 + i))
                for i in range(3)
            ]
            nov24_victims = [
                _make_valid_event(f"evt-1124-victim-{i}", created_at="2026-11-24T23:35:00Z", pnl=float(100 + i))
                for i in range(9)
            ]
            self_conflicts = [
                _make_valid_event(f"evt-conflict-{i}", created_at="2026-11-24T23:35:00Z", pnl=float(900 + i))
                for i in range(2)
            ]

            for ev in nov23_events + nov24_already_stored:
                self.db.seq += 1
                self.db.rows[ev["event_id"]] = (ev["event_type"], ev["created_at"], ev, self.db.seq)

            # Simulate original publish + ACK of the 12 events from 11/24 inside
            # the 24h duplicate window (and the failed 11/25 bare-hash replay).
            for ev in nov24_already_stored + nov24_victims:
                self.assertTrue(await self.buffer.put(ev))
                fetched = await self.buffer.get(timeout=0.05)
                await self.buffer.ack([fetched])
            self.assertEqual(self.buffer.size(), 0)

            # Populate the 19 poison_event DLQ entries (17 distinct IDs: 15 victims + 2 self-conflicts x2).
            for ev in nov23_events:
                svc._dlq.reject(
                    ev,
                    tags=[TAG_POISON_EVENT],
                    reason="Non-retryable write failure: conflicting duplicate event_id=evt-conflict-0",
                )
            svc._dlq.reject(
                self_conflicts[0],
                tags=[TAG_POISON_EVENT],
                reason="Non-retryable write failure: conflicting duplicate event_id=evt-conflict-0",
            )
            for ev in nov24_already_stored + nov24_victims:
                svc._dlq.reject(
                    ev,
                    tags=[TAG_POISON_EVENT],
                    reason="Non-retryable write failure: conflicting duplicate event_id=evt-conflict-1",
                )
            for sc in self_conflicts:
                svc._dlq.reject(
                    sc,
                    tags=[TAG_POISON_EVENT],
                    reason=f"Non-retryable write failure: conflicting duplicate event_id={sc['event_id']}",
                )
            # 3 + 1 + 12 + 2 = 18; add one duplicate self-conflict entry to match 19 total / 17 distinct
            svc._dlq.reject(
                self_conflicts[1],
                tags=[TAG_POISON_EVENT],
                reason="Non-retryable write failure: conflicting duplicate event_id=evt-conflict-1",
            )
            self.assertEqual(len(svc.get_dlq_entries(tag_filter=TAG_POISON_EVENT)), 19)

            result = await svc.replay_dlq(tag_filter=TAG_POISON_EVENT)
            await self._drain_writer(svc)

            # All 9 missing victim events from 11/24 are now stored in telemetry_events,
            # total rows = 6 existing + 9 new = 15, and buffer_size is 0 when idle.
            for ev in nov24_victims:
                self.assertIn(ev["event_id"], self.db.rows)
            self.assertEqual(len(self.db.rows), 15)
            self.assertEqual(self.buffer.size(), 0)
            self.assertEqual(int(result), 15)
            self.assertEqual(result.to_dict(), {"replayed": 15, "suppressed": 0, "refused": 2})
        finally:
            await svc.stop(graceful=True)

    async def test_replay_twice_across_expired_broker_window_still_stores_once_in_postgres(self) -> None:
        pg_write = build_postgres_write_fn("postgresql://x/db")
        svc = TelemetryIngestService(
            buffer=self.buffer,
            write_fn=pg_write,
            dlq_spill_path=self.spill_path,
            batch_size=10,
            batch_interval=0.01,
        )
        await svc.start()
        try:
            victim = _make_valid_event("evt-victim-window-expiry", pnl=321.0)
            svc._dlq.reject(
                victim,
                tags=[TAG_WRITER_ERROR],
                reason="transient write failure",
            )
            bad_schema = {"event_id": "evt-invalid-schema", "tenant_id": _TENANT_ID}
            svc._dlq.reject(
                bad_schema,
                tags=[TAG_WRITER_ERROR],
                reason="corrupt spill entry",
            )

            first = await svc.replay_dlq()
            await self._drain_writer(svc)
            self.assertEqual(first.to_dict(), {"replayed": 1, "suppressed": 0, "refused": 1})
            self.assertIn("evt-victim-window-expiry", self.db.rows)
            first_seq = self.db.rows["evt-victim-window-expiry"][3]
            self.assertEqual(len(self.db.notifications), 1)

            # Advance past the 24h broker duplicate window and replay again:
            # broker allows the publish, and Postgres ON CONFLICT DO NOTHING
            # keeps the single immutable row without a second notification.
            self.broker.advance_time(86_401.0)
            second = await svc.replay_dlq()
            await self._drain_writer(svc)
            self.assertEqual(second.to_dict(), {"replayed": 1, "suppressed": 0, "refused": 1})
            self.assertEqual(self.db.rows["evt-victim-window-expiry"][3], first_seq)
            self.assertEqual(len(self.db.notifications), 1)
            self.assertEqual(self.buffer.size(), 0)
        finally:
            await svc.stop(graceful=True)

    def test_replay_http_route_reports_replayed_suppressed_and_refused_separately(self) -> None:
        import services.telemetry.main as _main
        from services.telemetry.ingest_svc import ReplayResult

        with patch.dict(
            os.environ,
            {
                "PANTHEON_TELEMETRY_AUTH_MODE": "permissive",
                "PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID,
            },
        ), patch.object(
            _main,
            "_get_service",
            return_value=types.SimpleNamespace(
                replay_dlq=lambda **kwargs: None,
            ),
        ), patch.object(
            _main,
            "_run_async",
            return_value=ReplayResult(3, suppressed=12, refused=2),
        ):
            client = _main.app.test_client()
            resp = client.post(
                "/api/telemetry/replay?tag=poison_event",
                headers={
                    "Authorization": "Bearer telemetry-test:operator",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(
                resp.get_json(),
                {"replayed": 3, "suppressed": 12, "refused": 2},
            )


@unittest.skipUnless(
    os.getenv("PANTHEON_TEST_NATS_URL"),
    "set PANTHEON_TEST_NATS_URL to run the live JetStream duplicate-window replay test",
)
class RealNatsReplayBrokerDedupTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_jetstream_replay_inside_duplicate_window(self) -> None:
        nats_url = os.environ["PANTHEON_TEST_NATS_URL"]
        suffix = uuid.uuid4().hex[:12]
        stream_name = f"TEL_REPLAY_{suffix.upper()}"
        subject = f"tel.replay.{suffix}.ingest"
        durable_name = f"tel-replay-{suffix}-writer"
        db = FakeDb()
        buffer = NatsJetStreamBuffer(
            nats_url=nats_url,
            stream_name=stream_name,
            subject=subject,
            durable_name=durable_name,
            maxsize=100,
            ack_wait=5.0,
            duplicate_window=120.0,
        )
        with tempfile.TemporaryDirectory() as td, patch.dict(
            sys.modules, {"asyncpg": _driver(db)}
        ):
            fail_once = True
            pg_write = build_postgres_write_fn("postgresql://x/db")

            async def write_fn(batch: list[dict[str, Any]]):
                if fail_once:
                    from services.telemetry.batch_writer import WriteResult
                    return WriteResult.fail("simulated non-retryable failure", retryable=False)
                return await pg_write(batch)

            svc = TelemetryIngestService(
                buffer=buffer,
                write_fn=write_fn,
                dlq_spill_path=str(Path(td) / "dlq.jsonl"),
                batch_size=10,
                batch_interval=0.02,
            )
            try:
                await svc.start()
                event = _make_valid_event(f"evt-live-nats-{suffix}")
                self.assertTrue(await svc.ingest(event))
                for _ in range(100):
                    if len(svc.get_dlq_entries(tag_filter=TAG_POISON_EVENT)) >= 1:
                        break
                    await asyncio.sleep(0.02)
                self.assertNotIn(event["event_id"], db.rows)
                self.assertEqual(buffer.size(), 0)

                fail_once = False
                replay_1 = await svc.replay_dlq(tag_filter=TAG_POISON_EVENT)
                self.assertEqual(replay_1.to_dict(), {"replayed": 1, "suppressed": 0, "refused": 0})
                for _ in range(100):
                    if event["event_id"] in db.rows and buffer.size() == 0:
                        break
                    await asyncio.sleep(0.02)
                self.assertIn(event["event_id"], db.rows)
                self.assertEqual(buffer.size(), 0)

                replay_2 = await svc.replay_dlq(tag_filter=TAG_POISON_EVENT)
                self.assertEqual(replay_2.to_dict(), {"replayed": 0, "suppressed": 1, "refused": 0})
                self.assertEqual(buffer.size(), 0)
            finally:
                await svc.stop(graceful=True)
                if buffer._js is not None:
                    try:
                        await buffer._js.delete_stream(stream_name)
                    except Exception:
                        pass


if __name__ == "__main__":
    unittest.main()

"""A conflicting duplicate isolates exactly one event (RC2).

Uses the real AsyncBatchWriter and the real build_postgres_write_fn; only the
asyncpg driver is replaced by a stateful in-memory connection.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from services.telemetry.batch_writer import AsyncBatchWriter
from services.telemetry.buffer import InMemoryBuffer
from services.telemetry.dead_letter import (
    DeadLetterQueue,
    TAG_POISON_EVENT,
    is_trading_event_type,
)
from services.telemetry.ingest_svc import (
    TelemetryIngestService,
    build_postgres_write_fn,
)


class FakeDb:
    def __init__(self):
        self.rows: dict[str, tuple] = {}
        self.seq = 0
        self.notifications: list[dict] = []
        self.statements: list[str] = []
        self.fail_on: str | None = None
        self.lock_held = False


def _driver(db: FakeDb):
    class Tx:
        async def __aenter__(self):
            self.snapshot = (dict(db.rows), db.seq, len(db.notifications))

        async def __aexit__(self, exc_type, exc, tb):
            db.lock_held = False
            if exc_type is not None:
                db.rows, db.seq = self.snapshot[0], self.snapshot[1]
                del db.notifications[self.snapshot[2]:]
            return False

    class Prepared:
        async def fetch(self, ids, types_, created, payloads):
            assert db.lock_held, "insert must run under the advisory lock"
            db.statements.append("insert")
            out = []
            for i, ev in enumerate(ids):
                if ev in db.rows:
                    continue
                db.seq += 1
                db.rows[ev] = (types_[i], created[i], json.loads(payloads[i]), db.seq)
                out.append({"event_id": ev, "ingested_seq": db.seq})
            return out

    class Conn:
        def transaction(self):
            return Tx()

        async def prepare(self, query):
            return Prepared()

        async def execute(self, query, *args):
            db.statements.append(query.split()[1] if "pg_" in query else query)
            if "advisory" in query:
                db.lock_held = True
            if "pg_notify" in query:
                db.notifications.append(json.loads(args[1]))
            if db.fail_on and db.fail_on in query:
                raise RuntimeError("db boom")

        async def fetch(self, query, ids, types_, created, payloads, ordinals):
            db.statements.append("conflict_check")
            if db.fail_on == "conflict_check":
                raise RuntimeError("db boom")
            out = []
            for i, ev in enumerate(ids):
                row = db.rows[ev]
                if row[0] != types_[i] or row[2] != json.loads(payloads[i]):
                    out.append({"event_id": ev, "ordinality": ordinals[i]})
            return out

        async def close(self):
            pass

    async def connect(dsn):
        return Conn()

    return types.SimpleNamespace(connect=connect)


def _ev(event_id, event_type="order_submitted", **extra):
    ev = {
        "event_id": event_id,
        "event_type": event_type,
        "created_at": "2026-11-24T10:00:00Z",
        "metrics": {"v": 1},
    }
    ev.update(extra)
    return ev


class RecordingBuffer(InMemoryBuffer):
    def __init__(self):
        super().__init__()
        self.acked: list[str] = []
        self.released: list[str] = []

    async def ack(self, events):
        self.acked += [e["event_id"] for e in events]

    async def release(self, events):
        self.released += [e["event_id"] for e in events]
        return True


class ConflictIsolationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = FakeDb()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dlq = DeadLetterQueue(spill_path=str(Path(self.tmp.name) / "dl.jsonl"))
        self.buffer = RecordingBuffer()
        self.writer = AsyncBatchWriter(
            buffer=self.buffer,
            write_fn=build_postgres_write_fn("postgresql://x/db"),
            dead_letter_queue=self.dlq,
            max_retries=1,
            base_retry_delay=0.001,
        )
        patcher = patch.dict(sys.modules, {"asyncpg": _driver(self.db)})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _flush(self, batch):
        await self.writer._write_with_retry(batch, "p")

    def _seed(self, *events):
        for ev in events:
            self.db.seq += 1
            self.db.rows[ev["event_id"]] = (
                ev["event_type"], ev["created_at"], ev, self.db.seq,
            )

    def _dead_ids(self):
        return [e.event["event_id"] for e in self.dlq.get_entries()]

    async def test_one_conflict_commits_the_rest(self):
        original = _ev("c1")
        self._seed(original)
        changed = _ev("c1", metrics={"v": 2})
        batch = [_ev("a"), changed, _ev("b")]
        await self._flush(batch)
        self.assertEqual(self._dead_ids(), ["c1"])
        entry = self.dlq.get_entries()[0]
        self.assertEqual(entry.tags, [TAG_POISON_EVENT])
        self.assertIn("event_id=c1", entry.reason)
        self.assertEqual(self.db.rows["c1"][2], original)  # never changed
        self.assertEqual([self.db.rows[k][3] for k in ("a", "b")], [2, 3])
        self.assertEqual(len(self.db.notifications), 1)
        self.assertEqual(self.db.notifications[0]["inserted_count"], 2)
        self.assertEqual(sorted(self.buffer.acked), ["a", "b", "c1"])
        self.assertEqual(self.buffer.released, [])

    async def test_several_conflicts(self):
        self._seed(_ev("c1"), _ev("c2"))
        await self._flush([_ev("c1", metrics={"v": 9}), _ev("a"), _ev("c2", event_type="fill_x")])
        self.assertEqual(self._dead_ids(), ["c1", "c2"])
        self.assertIn("a", self.db.rows)
        for e in self.dlq.get_entries():
            self.assertIn(f"event_id={e.event['event_id']}", e.reason)

    async def test_all_conflicting_no_notify(self):
        self._seed(_ev("c1"), _ev("c2"))
        await self._flush([_ev("c1", metrics={"v": 9}), _ev("c2", metrics={"v": 9})])
        self.assertEqual(self._dead_ids(), ["c1", "c2"])
        self.assertEqual(self.db.notifications, [])
        self.assertEqual(sorted(self.buffer.acked), ["c1", "c2"])

    async def test_conflict_inside_batch(self):
        first, second = _ev("d1"), _ev("d1", metrics={"v": 2})
        await self._flush([first, _ev("a"), second])
        self.assertEqual(self.db.rows["d1"][2], first)
        self.assertEqual(self._dead_ids(), ["d1"])
        self.assertEqual(self.dlq.get_entries()[0].event, second)
        self.assertIn("a", self.db.rows)

    async def test_exact_duplicate_is_not_dead_lettered(self):
        self._seed(_ev("x"))
        await self._flush([_ev("x"), _ev("y")])
        self.assertEqual(self._dead_ids(), [])
        self.assertEqual(self.buffer.acked, ["x", "y"])

    async def test_statements_bounded_independent_of_batch_size(self):
        async def run(n):
            self.db.statements.clear()
            await self._flush([_ev(f"n{n}-{i}") for i in range(n)])
            return len(self.db.statements)

        self.assertEqual(await run(1), await run(300))
        self.assertLessEqual(await run(300), 3)

    async def test_database_error_midway_retries_whole_batch(self):
        self.db.fail_on = "pg_notify"
        await self._flush([_ev("a"), _ev("b")])
        # retries exhausted -> writer_error dead letters, nothing committed
        self.assertEqual(self.db.rows, {})
        self.assertEqual(self.db.notifications, [])
        self.assertEqual(len(self.dlq.get_entries()), 2)
        self.db.fail_on = None
        await self._flush([_ev("a"), _ev("b")])
        self.assertEqual(sorted(self.db.rows), ["a", "b"])

    async def test_dead_letter_write_failure_releases_only_conflict(self):
        self._seed(_ev("c1"))
        # an unwritable spill makes the DLQ append non-durable
        self.dlq._spill_path = Path(self.tmp.name) / "missing" / "dl.jsonl"
        self.buffer.is_durable = lambda: True  # type: ignore[method-assign]
        batch = [_ev("a"), _ev("c1", metrics={"v": 2})]
        await self._flush(batch)
        self.assertEqual(self.buffer.released, ["c1"])
        self.assertEqual(self.buffer.acked, ["a"])
        # redelivery of the partly committed batch is idempotent
        before = dict(self.db.rows)
        notes = len(self.db.notifications)
        await self._flush(batch)
        self.assertEqual(self.db.rows, before)
        self.assertEqual(len(self.db.notifications), notes)

    async def test_trading_dead_letters_visible_in_stats(self):
        self._seed(_ev("c1"))
        await self._flush([_ev("c1", metrics={"v": 2})])
        trading = self.dlq.stats()["trading_dead_letters"]
        self.assertEqual(trading["order_submitted"]["count"], 1)
        self.assertIn("event_id=c1", trading["order_submitted"]["newest_reason"])
        self.assertFalse(is_trading_event_type("heartbeat"))
        self.assertTrue(is_trading_event_type("paper_fill_simulated"))

    async def test_replay_skips_conflicting_rows_only(self):
        svc = TelemetryIngestService.__new__(TelemetryIngestService)
        svc._dlq = self.dlq
        legacy = "Non-retryable write failure: conflicting duplicate event_id=c1"
        self.dlq.reject(_ev("a"), [TAG_POISON_EVENT], legacy)
        self.dlq.reject(_ev("c1"), [TAG_POISON_EVENT], legacy)
        self.dlq.reject(_ev("b"), [TAG_POISON_EVENT], legacy)
        ids = [e["event_id"] for e in svc._replay_candidates(TAG_POISON_EVENT)]
        self.assertEqual(ids, ["a", "b"])


if __name__ == "__main__":
    unittest.main()

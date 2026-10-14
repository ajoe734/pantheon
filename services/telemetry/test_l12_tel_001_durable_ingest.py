"""L12-TEL-001 durable receipt and canonical-write ACK regressions."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import multiprocessing
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from services.telemetry.batch_writer import AsyncBatchWriter, WriteResult
from services.telemetry.buffer import NatsJetStreamBuffer
from services.telemetry.dead_letter import DeadLetterQueue
from services.telemetry.ingest_svc import TelemetryIngestService, build_postgres_event_reader
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


class _Message:
    def __init__(self, event: dict, calls: list[str]) -> None:
        self.data = json.dumps(event).encode("utf-8")
        self.calls = calls

    async def ack_sync(self, timeout: float) -> None:
        self.calls.append("ack")

    async def nak(self, delay: float) -> None:
        self.calls.append("nak")

    async def term(self) -> None:
        self.calls.append("term")


class _Subscription:
    def __init__(self, message: _Message) -> None:
        self.message = message
        self.delivered = False

    async def fetch(self, *, batch: int, timeout: float):
        if self.delivered:
            raise TimeoutError()
        self.delivered = True
        return [self.message]


class _JetStream:
    def __init__(self, calls: list[str], *, fail_publish: bool = False) -> None:
        self.calls = calls
        self.fail_publish = fail_publish
        self.publish_kwargs: dict | None = None
        self.publish_history: list[dict] = []

    async def publish(self, subject: str, payload: bytes, **kwargs):
        self.calls.append("puback" if not self.fail_publish else "publish_failed")
        if self.fail_publish:
            raise TimeoutError("no JetStream persistence acknowledgement")
        self.publish_kwargs = {
            "subject": subject,
            "payload": json.loads(payload),
            **kwargs,
        }
        self.publish_history.append(self.publish_kwargs)
        return object()


def _event() -> dict:
    return {
        "event_id": "evt-l12-tel-001",
        "tenant_id": "tenant-alpha",
        "event_type": "heartbeat",
        "created_at": "2026-07-26T00:00:00Z",
        "deployment_stage": "paper",
    }


def _capital_runtime_event() -> dict:
    """A paper-only event with the identity emitted by the Capital runtime."""
    return {
        "event_id": "evt-l12-min-cap-runtime-001",
        "tenant_id": "tenant-l12",
        "event_type": "paper_fill_simulated",
        "created_at": "2026-08-10T03:15:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "binding-l12-min-cap",
        "runtime_id": "runtime-l12-min-cap",
        "capital_pool_id": "pool-l12-paper",
        "artifact_id": "artifact-l12-paper",
        "artifact_version": "1.0.0",
        "plan_id": "plan-l12-min-dep",
        "persona_capital_binding_id": "pcb-l12-paper",
        "trace_id": "trace-l12-min-cap",
        "target": {"strategy_id": "strategy-l12-paper"},
        "metrics": {"fill_quantity": 2.0, "fill_price": 100.0},
        "metadata": {
            "runtime_package": "paper_execution_runtime",
            "ledger_committed": True,
            "is_real_order": False,
            "is_real_capital": False,
        },
    }


def _expected_receipt_id(event: dict) -> str:
    payload = json.dumps(
        event,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _started_buffer(
    *,
    jetstream: _JetStream,
    subscription: _Subscription,
) -> NatsJetStreamBuffer:
    buffer = NatsJetStreamBuffer()
    buffer._js = jetstream
    buffer._subscription = subscription
    return buffer


def _fetch_then_crash(
    connection,
    *,
    nats_url: str,
    stream_name: str,
    subject: str,
    durable_name: str,
) -> None:
    """Fetch one durable receipt in a child process, then exit without ACK."""

    async def fetch() -> dict | None:
        buffer = NatsJetStreamBuffer(
            nats_url=nats_url,
            stream_name=stream_name,
            subject=subject,
            durable_name=durable_name,
            maxsize=100,
            ack_wait=1.0,
            duplicate_window=120.0,
        )
        await buffer.start()
        return await buffer.get(timeout=5.0)

    try:
        connection.send(asyncio.run(fetch()))
        connection.close()
    except BaseException as exc:  # pragma: no cover - child failure surfaced to parent
        try:
            connection.send({"child_error": repr(exc)})
            connection.close()
        finally:
            os._exit(24)
    os._exit(23)


class DurableIngestReceiptTest(unittest.IsolatedAsyncioTestCase):
    async def test_capital_runtime_event_is_accepted_for_reconciliation_readback(self):
        """The exact paper-runtime event remains available after ingest admission."""
        event = _capital_runtime_event()
        with tempfile.TemporaryDirectory() as td:
            projection = RuntimeSummaryProjectionStore(
                Path(td) / "runtime-summaries.json"
            )
            service = TelemetryIngestService(runtime_summary_store=projection)
            await service.start()
            try:
                self.assertTrue(await service.ingest(event))
                self.assertEqual(
                    service.get_accepted_event(event["event_id"], tenant_id="tenant-l12"),
                    event,
                )
                [summary] = service.list_runtime_summaries(tenant_id="tenant-l12")
                self.assertEqual(summary["last_event_id"], event["event_id"])
                self.assertEqual(summary["binding_id"], event["binding_id"])
                self.assertEqual(summary["runtime_id"], event["runtime_id"])
            finally:
                await service.stop(graceful=True)

    async def test_runtime_summary_enrichment_queries_only_reconciliation_evidence_without_unrelated_payloads(self):
        """Durable reader query restricts hydration to canonical reconciliation evidence."""
        import sys, types
        from unittest.mock import AsyncMock, patch

        snap_event = dict(
            _capital_runtime_event(),
            event_id="evt-durable-snap-001",
            event_type="position_snapshot",
            authority_refs={"persona_id": "persona-l12-paper"},
            metrics={"position_qty": 5.0, "price": 200.0, "cash": 10000.0},
        )
        second_snap = dict(
            _capital_runtime_event(),
            event_id="evt-durable-second-002",
            binding_id="binding-l12-second",
            runtime_id="rt-l12-second",
            event_type="position_snapshot",
            authority_refs={"persona_id": "persona-l12-paper"},
            metrics={"position_qty": 10.0, "price": 150.0, "cash": 25000.0},
        )
        older_snap = dict(
            _capital_runtime_event(),
            event_id="evt-durable-older-003",
            binding_id="binding-l12-third",
            runtime_id="rt-l12-third",
            event_type="position_snapshot",
            authority_refs={"persona_id": "persona-l12-paper"},
            metrics={"position_qty": 2.0, "price": 300.0, "cash": 5000.0},
        )
        fill_event = _capital_runtime_event()

        mock_db_rows: dict[str, dict[str, Any]] = {
            snap_event["event_id"]: dict(snap_event, ingested_seq=10),
            second_snap["event_id"]: dict(second_snap, ingested_seq=11),
            older_snap["event_id"]: dict(older_snap, ingested_seq=12),
            fill_event["event_id"]: dict(fill_event, ingested_seq=13),
        }

        # Bounded synthetic wide input reflecting observed shape (55 summaries / 18,458 IDs):
        # 54 summaries with 335 IDs each (18090) + 1 summary with 368 IDs = 18458 IDs total
        # (each ring bounded <= 512)
        def _make_summary(base_ev: dict[str, Any], ring: list[str]) -> dict[str, Any]:
            return {
                "tenant_id": base_ev["tenant_id"],
                "binding_id": base_ev["binding_id"],
                "runtime_id": base_ev["runtime_id"],
                "artifact_id": base_ev["artifact_id"],
                "artifact_version": base_ev["artifact_version"],
                "plan_id": base_ev["plan_id"],
                "capital_pool_id": base_ev["capital_pool_id"],
                "persona_capital_binding_id": base_ev.get("persona_capital_binding_id"),
                "persona_id": base_ev.get("authority_refs", {}).get("persona_id") or "persona-l12-paper",
                "recent_lifecycle_event_ids": ring,
            }

        wide_summaries: list[dict[str, Any]] = []
        cur_id = 0
        for s_idx in range(55):
            ring_size = 368 if s_idx == 0 else 335
            ring_ids: list[str] = []
            if s_idx == 0:
                base_ev = snap_event
                ring_ids.append("evt-durable-snap-001")
                ring_ids.append(fill_event["event_id"])
            elif s_idx == 1:
                base_ev = second_snap
                ring_ids.append("evt-durable-second-002")
            elif s_idx == 2:
                base_ev = older_snap
                # Older eligible position snapshot behind 129 newer lifecycle events
                ring_ids.extend([f"evt-unrelated-{cur_id + k}" for k in range(129)])
                cur_id += 129
                ring_ids.append("evt-durable-older-003")
            else:
                base_ev = dict(
                    snap_event,
                    binding_id=f"binding-l12-{s_idx}",
                    runtime_id=f"rt-l12-{s_idx}",
                )

            needed = ring_size - len(ring_ids)
            for _ in range(needed):
                unrelated_id = f"evt-unrelated-{cur_id}"
                cur_id += 1
                ring_ids.append(unrelated_id)
                mock_db_rows[unrelated_id] = {
                    "event_id": unrelated_id,
                    "event_type": "order_submitted",
                    "tenant_id": "tenant-l12",
                    "payload": {"tenant_id": "tenant-l12"},
                    "ingested_seq": 100 + cur_id,
                }
            wide_summaries.append(_make_summary(base_ev, ring_ids))

        total_collected_ids = sum(len(s["recent_lifecycle_event_ids"]) for s in wide_summaries)
        self.assertEqual(len(wide_summaries), 55)
        self.assertEqual(total_collected_ids, 18458)

        captured_queries: list[dict[str, Any]] = []

        class FakeConnection:
            async def fetch(self, sql: str, *args: Any, timeout: float = 5.0) -> list[dict[str, Any]]:
                captured_queries.append({"sql": sql, "args": args})
                if "event_id = ANY($1::text[])" in sql:
                    req_ids = args[0]
                    clean_types = args[1] if len(args) > 1 and isinstance(args[1], (list, tuple)) else None
                    tid = args[2] if len(args) > 2 else (args[1] if len(args) > 1 and isinstance(args[1], str) else None)
                    results = []
                    for eid in req_ids:
                        row = mock_db_rows.get(eid)
                        if not row:
                            continue
                        if clean_types and row.get("event_type") not in clean_types:
                            continue
                        if tid and row.get("tenant_id") != tid:
                            continue
                        results.append({
                            "event_id": row["event_id"],
                            "payload": json.dumps(row),
                            "ingested_seq": row.get("ingested_seq", 1),
                        })
                    return results
                if "event_id = $1" in sql:
                    eid = args[0]
                    tid = args[1] if len(args) > 1 else None
                    row = mock_db_rows.get(eid)
                    if row and (not tid or row.get("tenant_id") == tid):
                        return [{
                            "event_id": row["event_id"],
                            "payload": json.dumps(row),
                            "ingested_seq": row.get("ingested_seq", 1),
                        }]
                    return []
                return []

            async def close(self, timeout: float = 1.0) -> None:
                pass

            def terminate(self) -> None:
                pass

        fake_asyncpg = types.SimpleNamespace(connect=AsyncMock(return_value=FakeConnection()))

        with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
            durable_reader, _ = build_postgres_event_reader("postgresql://test-l12/db", table="telemetry_events")
            with tempfile.TemporaryDirectory() as td:
                projection = RuntimeSummaryProjectionStore(
                    Path(td) / "runtime-summaries.json"
                )
                service = TelemetryIngestService(
                    runtime_summary_store=projection,
                    event_reader=durable_reader,
                )
                await service.start()
                try:
                    # 1. Bounded synthetic wide input reflecting observed shape (55 summaries / 18,458 IDs)
                    enriched = service._enrich_with_committed_position_snapshots(
                        copy.deepcopy(wide_summaries), tenant_id="tenant-l12"
                    )
                    self.assertEqual(len(captured_queries), 1)
                    q = captured_queries[0]
                    self.assertIn("WHERE event_id = ANY($1::text[])", q["sql"])
                    self.assertIn("event_type = ANY($2::text[])", q["sql"])
                    self.assertIn("(payload->>'tenant_id' = $3 OR payload->'metadata'->>'tenant_id' = $3)", q["sql"])
                    self.assertEqual(len(q["args"][0]), 18458)
                    self.assertEqual(
                        q["args"][1],
                        ["position_snapshot", "reconciliation_completed", "reconciliation_failed"],
                    )
                    self.assertEqual(q["args"][2], "tenant-l12")

                    # Verify that unrelated payloads (~18,455 events) were not hydrated by the DB query
                    self.assertIn("committed_position_snapshot", enriched[0])
                    self.assertEqual(enriched[0]["committed_position_snapshot"]["event_id"], "evt-durable-snap-001")

                    # Second binding snapshot enriched
                    self.assertIn("committed_position_snapshot", enriched[1])
                    self.assertEqual(enriched[1]["committed_position_snapshot"]["event_id"], "evt-durable-second-002")

                    # Older eligible position snapshot in deep ring (129 newer events) enriched (not dropped)
                    self.assertIn("committed_position_snapshot", enriched[2])
                    self.assertEqual(enriched[2]["committed_position_snapshot"]["event_id"], "evt-durable-older-003")

                    # 2. Already-enriched summaries are skipped before collecting IDs and not queried
                    query_count_before = len(captured_queries)
                    already_enriched = [s for s in enriched if s.get("committed_position_snapshot")]
                    self.assertEqual(len(already_enriched), 3)
                    service._enrich_with_committed_position_snapshots(already_enriched, tenant_id="tenant-l12")
                    self.assertEqual(len(captured_queries), query_count_before)

                    # When all 55 summaries are already enriched, zero reader queries are made
                    all_enriched_55 = [dict(s, committed_position_snapshot={"event_id": "snap"}) for s in wide_summaries]
                    service._enrich_with_committed_position_snapshots(all_enriched_55, tenant_id="tenant-l12")
                    self.assertEqual(len(captured_queries), query_count_before)

                    # Mixed test: when some summaries are enriched and one is new, only unenriched IDs queried
                    not_yet_enriched = {
                        "tenant_id": "tenant-l12",
                        "binding_id": "binding-l12-new",
                        "runtime_id": "rt-l12-new",
                        "recent_lifecycle_event_ids": ["evt-durable-snap-001"],
                    }
                    service._enrich_with_committed_position_snapshots(
                        [enriched[0], not_yet_enriched], tenant_id="tenant-l12"
                    )
                    self.assertEqual(len(captured_queries), query_count_before + 1)
                    self.assertEqual(captured_queries[-1]["args"][0], ["evt-durable-snap-001"])

                    # 3. Generic batch and scalar reader behavior
                    # Scalar reader call executes original scalar query without event_types filter
                    scalar_res = durable_reader("evt-durable-snap-001", tenant_id="tenant-l12")
                    self.assertIsNotNone(scalar_res)
                    self.assertEqual(scalar_res["event_id"], "evt-durable-snap-001")
                    self.assertEqual(scalar_res["ingested_seq"], 10)
                    sq = captured_queries[-1]
                    self.assertIn("WHERE event_id = $1 AND (payload->>'tenant_id' = $2 OR payload->'metadata'->>'tenant_id' = $2) LIMIT 1", sq["sql"])
                    self.assertNotIn("event_type", sq["sql"])
                    self.assertEqual(sq["args"], ("evt-durable-snap-001", "tenant-l12"))

                    # Scalar without tenant
                    scalar_no_tid = durable_reader("evt-durable-snap-001")
                    self.assertIsNotNone(scalar_no_tid)
                    sq_no_tid = captured_queries[-1]
                    self.assertEqual(sq_no_tid["sql"], "SELECT event_id, payload, ingested_seq FROM telemetry_events WHERE event_id = $1 LIMIT 1")
                    self.assertEqual(sq_no_tid["args"], ("evt-durable-snap-001",))
                finally:
                    await service.stop(graceful=True)

    async def test_put_succeeds_only_after_jetstream_puback(self):
        calls: list[str] = []
        message = _Message(_event(), calls)
        jetstream = _JetStream(calls)
        buffer = _started_buffer(
            jetstream=jetstream,
            subscription=_Subscription(message),
        )

        accepted = await buffer.put(_event())

        self.assertTrue(accepted)
        self.assertEqual(calls, ["puback"])
        self.assertEqual(
            jetstream.publish_kwargs["headers"],
            {
                "Nats-Msg-Id": _expected_receipt_id(_event()),
                "Pantheon-Tenant-Id": "tenant-alpha",
            },
        )

    async def test_receipt_id_binds_tenant_and_immutable_payload(self):
        calls: list[str] = []
        event = _event()
        jetstream = _JetStream(calls)
        buffer = _started_buffer(
            jetstream=jetstream,
            subscription=_Subscription(_Message(event, calls)),
        )

        self.assertTrue(await buffer.put(event))
        self.assertTrue(await buffer.put(dict(event)))
        self.assertTrue(
            await buffer.put(
                {
                    **event,
                    "tenant_id": "tenant-beta",
                }
            )
        )
        self.assertTrue(
            await buffer.put(
                {
                    **event,
                    "created_at": "2026-07-26T00:00:01Z",
                }
            )
        )

        receipt_ids = [
            call["headers"]["Nats-Msg-Id"]
            for call in jetstream.publish_history
        ]
        self.assertEqual(receipt_ids[0], receipt_ids[1])
        self.assertNotEqual(receipt_ids[0], receipt_ids[2])
        self.assertNotEqual(receipt_ids[0], receipt_ids[3])

    async def test_put_fails_when_durable_puback_is_missing(self):
        calls: list[str] = []
        message = _Message(_event(), calls)
        buffer = _started_buffer(
            jetstream=_JetStream(calls, fail_publish=True),
            subscription=_Subscription(message),
        )

        self.assertFalse(await buffer.put(_event()))
        self.assertEqual(calls, ["publish_failed"])

    async def test_writer_acks_only_after_canonical_write(self):
        calls: list[str] = []
        event = _event()
        message = _Message(event, calls)
        buffer = _started_buffer(
            jetstream=_JetStream(calls),
            subscription=_Subscription(message),
        )
        fetched = await buffer.get(timeout=0.1)
        self.assertEqual(fetched, event)
        self.assertEqual(calls, [])

        async def write_fn(batch: list[dict]) -> WriteResult:
            self.assertEqual(batch, [event])
            calls.append("canonical_write")
            return WriteResult.ok(1)

        with tempfile.TemporaryDirectory() as td:
            writer = AsyncBatchWriter(
                buffer=buffer,
                write_fn=write_fn,
                dead_letter_queue=DeadLetterQueue(
                    spill_path=str(Path(td) / "dlq.jsonl")
                ),
                max_retries=0,
            )
            await writer._write_with_retry([event], "paper")

        self.assertEqual(calls, ["canonical_write", "ack"])
        self.assertEqual(buffer.stats()["total_acked"], 1)

    async def test_unacked_receipt_remains_releasable_for_redelivery(self):
        calls: list[str] = []
        event = _event()
        message = _Message(event, calls)
        buffer = _started_buffer(
            jetstream=_JetStream(calls),
            subscription=_Subscription(message),
        )

        fetched = await buffer.get(timeout=0.1)
        self.assertEqual(fetched, event)
        self.assertEqual(calls, [])
        self.assertTrue(await buffer.release([fetched]))
        self.assertEqual(calls, ["nak"])
        self.assertEqual(buffer.stats()["total_acked"], 0)


@unittest.skipUnless(
    os.getenv("PANTHEON_TEST_NATS_URL"),
    "set PANTHEON_TEST_NATS_URL to run the real JetStream crash probe",
)
class RealNatsCrashRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_process_death_before_flush_redelivers_exact_receipt(self):
        nats_url = os.environ["PANTHEON_TEST_NATS_URL"]
        suffix = uuid.uuid4().hex[:12]
        stream_name = f"L12_TEL_{suffix.upper()}"
        subject = f"l12.tel.{suffix}.ingest"
        durable_name = f"l12-tel-{suffix}-writer"
        event = {
            **_event(),
            "event_id": f"evt-crash-{suffix}",
        }
        publisher = NatsJetStreamBuffer(
            nats_url=nats_url,
            stream_name=stream_name,
            subject=subject,
            durable_name=durable_name,
            maxsize=100,
            ack_wait=1.0,
            duplicate_window=120.0,
        )
        recovery: NatsJetStreamBuffer | None = None
        process = None
        parent_connection = None
        try:
            await publisher.start()
            self.assertTrue(await publisher.put(event))
            await publisher.close()

            context = multiprocessing.get_context("spawn")
            parent_connection, child_connection = context.Pipe(duplex=False)
            process = context.Process(
                target=_fetch_then_crash,
                kwargs={
                    "connection": child_connection,
                    "nats_url": nats_url,
                    "stream_name": stream_name,
                    "subject": subject,
                    "durable_name": durable_name,
                },
            )
            process.start()
            child_connection.close()
            fetched_ready = await asyncio.to_thread(parent_connection.poll, 8.0)
            self.assertTrue(fetched_ready, "child did not fetch the durable receipt")
            fetched = parent_connection.recv()
            await asyncio.to_thread(process.join, 8.0)
            self.assertEqual(process.exitcode, 23)
            self.assertEqual(fetched, event)

            await asyncio.sleep(1.2)
            recovery = NatsJetStreamBuffer(
                nats_url=nats_url,
                stream_name=stream_name,
                subject=subject,
                durable_name=durable_name,
                maxsize=100,
                ack_wait=1.0,
                duplicate_window=120.0,
            )
            await recovery.start()
            recovered = await recovery.get(timeout=5.0)
            self.assertEqual(recovered, event)
            await recovery.ack([recovered])
            self.assertEqual(recovery.stats()["total_acked"], 1)
        finally:
            if parent_connection is not None:
                parent_connection.close()
            if process is not None and process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
            cleanup = recovery or publisher
            if cleanup._js is not None:
                try:
                    await cleanup._js.delete_stream(stream_name)
                except Exception:
                    pass
            if recovery is not None:
                await recovery.close()


if __name__ == "__main__":
    unittest.main()

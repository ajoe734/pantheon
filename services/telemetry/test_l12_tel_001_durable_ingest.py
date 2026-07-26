"""L12-TEL-001 durable receipt and canonical-write ACK regressions."""

from __future__ import annotations

import asyncio
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

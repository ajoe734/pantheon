"""L12-TEL-001 durable receipt and canonical-write ACK regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
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

    async def publish(self, subject: str, payload: bytes, **kwargs):
        self.calls.append("puback" if not self.fail_publish else "publish_failed")
        if self.fail_publish:
            raise TimeoutError("no JetStream persistence acknowledgement")
        self.publish_kwargs = {
            "subject": subject,
            "payload": json.loads(payload),
            **kwargs,
        }
        return object()


def _event() -> dict:
    return {
        "event_id": "evt-l12-tel-001",
        "tenant_id": "tenant-alpha",
        "event_type": "heartbeat",
        "created_at": "2026-07-26T00:00:00Z",
        "deployment_stage": "paper",
    }


def _started_buffer(
    *,
    jetstream: _JetStream,
    subscription: _Subscription,
) -> NatsJetStreamBuffer:
    buffer = NatsJetStreamBuffer()
    buffer._js = jetstream
    buffer._subscription = subscription
    return buffer


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
                "Nats-Msg-Id": "evt-l12-tel-001",
                "Pantheon-Tenant-Id": "tenant-alpha",
            },
        )

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


if __name__ == "__main__":
    unittest.main()

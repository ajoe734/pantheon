"""BFF-CONSOL-012: SSE backpressure and bounded-buffer regressions."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Callable

import pytest

BFF_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BFF_DIR))
sys.path.insert(0, str(REPO_ROOT))

from services.control_plane.bff import main as bff_main


@pytest.fixture(autouse=True)
def clean_sse_buffers():
    for buffer in bff_main._sse_buffers.values():
        buffer.clear()
    for subscribers in bff_main._sse_subscribers.values():
        subscribers.clear()
    bff_main._incident_events.clear()
    bff_main._incident_subscribers.clear()
    yield
    for buffer in bff_main._sse_buffers.values():
        buffer.clear()
    for subscribers in bff_main._sse_subscribers.values():
        subscribers.clear()
    bff_main._incident_events.clear()
    bff_main._incident_subscribers.clear()


async def _wait_until(predicate: Callable[[], bool], *, timeout_seconds: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for SSE condition")
        await asyncio.sleep(0)


def _event_id_from_chunk(chunk: str) -> str:
    for line in chunk.splitlines():
        if line.startswith("id: "):
            return line.removeprefix("id: ").strip()
    raise AssertionError(f"SSE chunk did not contain an event id: {chunk!r}")


def test_slow_consumer_queue_is_bounded_drops_newest_and_cleans_up_on_disconnect() -> None:
    async def scenario() -> dict[str, int | str]:
        channel = "approval"
        buffer = bff_main._sse_buffers[channel]
        subscribers = bff_main._sse_subscribers[channel]
        stream = bff_main._sse_stream(buffer, subscribers)
        first_chunk_task = asyncio.create_task(anext(stream))

        await _wait_until(lambda: len(subscribers) == 1)
        subscriber_queue = subscribers[0]
        max_queue_events = subscriber_queue.maxsize
        assert max_queue_events == 1000

        total_events = max_queue_events + 25
        published_ids: list[str] = []
        for index in range(total_events):
            published_ids.append(
                bff_main._publish_event(
                    buffer,
                    subscribers,
                    "approval.backpressure",
                    {
                        "approval_id": "appr-bff-consol-012",
                        "aggregate_type": "approval decision",
                        "aggregate_id": "appr-bff-consol-012",
                        "sequence_no": index + 1,
                    },
                )
            )

        assert len(buffer) == bff_main._MAX_EVENTS
        assert subscriber_queue.full()
        assert subscriber_queue.qsize() == max_queue_events

        queued_ids = [event["id"] for event in subscriber_queue._queue]
        assert queued_ids == published_ids[:max_queue_events]
        assert published_ids[-1] not in queued_ids

        first_chunk = await asyncio.wait_for(first_chunk_task, timeout=1.0)
        assert f"id: {published_ids[0]}" in first_chunk
        assert subscriber_queue.qsize() == max_queue_events - 1

        await stream.aclose()
        await _wait_until(lambda: len(subscribers) == 0)

        return {
            "published_events": total_events,
            "replay_buffer_high_water_mark": len(buffer),
            "subscriber_queue_high_water_mark": max_queue_events,
            "subscriber_count_after_disconnect": len(subscribers),
            "subscriber_drop_strategy": "newest",
        }

    measurements = asyncio.run(scenario())

    assert measurements == {
        "published_events": 1025,
        "replay_buffer_high_water_mark": 500,
        "subscriber_queue_high_water_mark": 1000,
        "subscriber_count_after_disconnect": 0,
        "subscriber_drop_strategy": "newest",
    }


def test_replay_window_is_bounded_drops_oldest_and_preserves_per_aggregate_ordering() -> None:
    channel = "approval"
    buffer = bff_main._sse_buffers[channel]
    subscribers = bff_main._sse_subscribers[channel]
    published_ids: list[str] = []
    causal_parent_id = None

    for sequence_no in range(1, bff_main._MAX_EVENTS + 6):
        event_id = bff_main._publish_event(
            buffer,
            subscribers,
            "approval.ordering",
            {
                "approval_id": "appr-bff-consol-012",
                "aggregate_type": "approval decision",
                "aggregate_id": "appr-bff-consol-012",
                "sequence_no": sequence_no,
                "causal_parent_id": causal_parent_id,
            },
        )
        published_ids.append(event_id)
        causal_parent_id = event_id

    window_ids = [event_id for event_id, _event in buffer]
    assert len(buffer) == bff_main._MAX_EVENTS
    assert window_ids == published_ids[-bff_main._MAX_EVENTS :]

    with pytest.raises(bff_main.SseReplayUnavailableError):
        bff_main._replay_from(buffer, published_ids[0])

    replayed = bff_main._replay_from(buffer, published_ids[-4])

    assert [event["id"] for event in replayed] == published_ids[-3:]
    assert [event["data"]["sequence_no"] for event in replayed] == [
        bff_main._MAX_EVENTS + 3,
        bff_main._MAX_EVENTS + 4,
        bff_main._MAX_EVENTS + 5,
    ]
    assert all(
        event["data"]["aggregate_id"] == "appr-bff-consol-012" for event in replayed
    )


def test_replay_headers_publish_window_policy_for_clients() -> None:
    headers = bff_main._sse_replay_headers("approval")

    assert headers["X-SSE-Replay-Supported"] == "true"
    assert headers["X-SSE-Replay-Window-Events"] == str(bff_main._MAX_EVENTS)
    assert headers["X-SSE-Buffer-Size"] == str(bff_main._MAX_EVENTS)
    assert headers["X-SSE-Replay-Store"] == "in-memory"
    assert headers["X-SSE-Resync-Routes"] == "/bff/approvals,/bff/v5/interventions"


def test_long_running_reconnect_heartbeat_and_duplicate_replay_contract(monkeypatch) -> None:
    async def scenario() -> dict[str, int | list[str] | str]:
        channel = "approval"
        buffer = bff_main._sse_buffers[channel]
        subscribers = bff_main._sse_subscribers[channel]
        original_wait_for = asyncio.wait_for

        first_id = bff_main._publish_event(
            buffer,
            subscribers,
            "approval.reconnect",
            {"approval_id": "appr-long-001", "sequence_no": 1},
        )
        initial_stream = bff_main._sse_stream(buffer, subscribers)
        first_chunk = await original_wait_for(anext(initial_stream), timeout=1.0)
        assert _event_id_from_chunk(first_chunk) == first_id
        await initial_stream.aclose()
        await _wait_until(lambda: len(subscribers) == 0)

        replay_ids = [
            bff_main._publish_event(
                buffer,
                subscribers,
                "approval.reconnect",
                {"approval_id": "appr-long-001", "sequence_no": sequence_no},
            )
            for sequence_no in (2, 3)
        ]
        reconnect_stream = bff_main._sse_stream(buffer, subscribers, last_event_id=first_id)
        replay_chunks = [
            await original_wait_for(anext(reconnect_stream), timeout=1.0),
            await original_wait_for(anext(reconnect_stream), timeout=1.0),
        ]
        replayed_ids = [_event_id_from_chunk(chunk) for chunk in replay_chunks]
        assert replayed_ids == replay_ids
        assert first_id not in replayed_ids
        assert len(replayed_ids) == len(set(replayed_ids))

        heartbeat_count = 0

        async def force_one_heartbeat(awaitable, timeout):
            nonlocal heartbeat_count
            if heartbeat_count == 0:
                heartbeat_count += 1
                close = getattr(awaitable, "close", None)
                if close is not None:
                    close()
                raise asyncio.TimeoutError
            return await original_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr(bff_main.asyncio, "wait_for", force_one_heartbeat)
        heartbeat_chunk = await original_wait_for(anext(reconnect_stream), timeout=1.0)
        assert heartbeat_chunk == ": heartbeat\n\n"
        await reconnect_stream.aclose()
        await _wait_until(lambda: len(subscribers) == 0)

        second_reconnect_stream = bff_main._sse_stream(buffer, subscribers, last_event_id=first_id)
        second_replay_chunks = [
            await original_wait_for(anext(second_reconnect_stream), timeout=1.0),
            await original_wait_for(anext(second_reconnect_stream), timeout=1.0),
        ]
        await second_reconnect_stream.aclose()
        await _wait_until(lambda: len(subscribers) == 0)

        return {
            "first_event_id": first_id,
            "first_reconnect_ids": replayed_ids,
            "second_reconnect_ids": [_event_id_from_chunk(chunk) for chunk in second_replay_chunks],
            "heartbeat_count": heartbeat_count,
            "subscriber_count_after_disconnect": len(subscribers),
        }

    measurements = asyncio.run(scenario())

    assert measurements["first_reconnect_ids"] == measurements["second_reconnect_ids"]
    assert measurements["heartbeat_count"] == 1
    assert measurements["subscriber_count_after_disconnect"] == 0

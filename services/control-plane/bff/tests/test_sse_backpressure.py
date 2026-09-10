"""BFF-CONSOL-012: SSE backpressure and bounded-buffer regressions."""
from __future__ import annotations

import asyncio
from typing import Callable

import pytest

import services.control_plane.bff.events.service as evt_service_module
from services.control_plane.bff.events.service import (
    DEFAULT_SSE_CHANNEL_CATALOG,
    EventStreamService,
    MAX_SSE_EVENTS,
    SseReplayUnavailableError,
)


@pytest.fixture
def sse_service() -> EventStreamService:
    return EventStreamService()


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


def test_slow_consumer_queue_is_bounded_drops_newest_and_cleans_up_on_disconnect(
    sse_service: EventStreamService,
) -> None:
    async def scenario() -> dict[str, int | str]:
        channel = "approval"
        buffer = sse_service.buffers[channel]
        subscribers = sse_service.subscribers[channel]
        stream = sse_service.stream(channel, buffer, subscribers, last_event_id=None)
        first_chunk_task = asyncio.create_task(anext(stream))

        await _wait_until(lambda: len(subscribers) == 1)
        subscriber_queue = subscribers[0]
        max_queue_events = subscriber_queue.maxsize
        assert max_queue_events == 1000

        total_events = max_queue_events + 25
        published_ids: list[str] = []
        for index in range(total_events):
            published_ids.append(
                sse_service.publish(
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

        assert len(buffer) == sse_service.max_events
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


def test_replay_window_is_bounded_drops_oldest_and_preserves_per_aggregate_ordering(
    sse_service: EventStreamService,
) -> None:
    channel = "approval"
    buffer = sse_service.buffers[channel]
    subscribers = sse_service.subscribers[channel]
    published_ids: list[str] = []
    causal_parent_id = None

    for sequence_no in range(1, sse_service.max_events + 6):
        event_id = sse_service.publish(
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
    assert len(buffer) == sse_service.max_events
    assert window_ids == published_ids[-sse_service.max_events :]

    with pytest.raises(SseReplayUnavailableError):
        sse_service.replay(channel, buffer, published_ids[0])

    replayed = sse_service.replay(channel, buffer, published_ids[-4])

    assert [event["id"] for event in replayed] == published_ids[-3:]
    assert [event["data"]["sequence_no"] for event in replayed] == [
        sse_service.max_events + 3,
        sse_service.max_events + 4,
        sse_service.max_events + 5,
    ]
    assert all(
        event["data"]["aggregate_id"] == "appr-bff-consol-012" for event in replayed
    )


def test_replay_headers_publish_window_policy_for_clients(
    sse_service: EventStreamService,
) -> None:
    headers = sse_service.replay_headers("approval")

    assert headers["X-SSE-Replay-Supported"] == "true"
    assert headers["X-SSE-Replay-Window-Events"] == str(sse_service.max_events)
    assert headers["X-SSE-Buffer-Size"] == str(sse_service.max_events)
    assert headers["X-SSE-Replay-Store"] == "in-memory"
    assert headers["X-SSE-Resync-Routes"] == "/bff/approvals,/bff/v5/interventions"


def test_long_running_reconnect_heartbeat_and_duplicate_replay_contract(
    sse_service: EventStreamService, monkeypatch
) -> None:
    async def scenario() -> dict[str, int | list[str] | str]:
        channel = "approval"
        buffer = sse_service.buffers[channel]
        subscribers = sse_service.subscribers[channel]
        original_wait_for = asyncio.wait_for

        first_id = sse_service.publish(
            buffer,
            subscribers,
            "approval.reconnect",
            {"approval_id": "appr-long-001", "sequence_no": 1},
        )
        initial_stream = sse_service.stream(channel, buffer, subscribers, last_event_id=None)
        first_chunk = await original_wait_for(anext(initial_stream), timeout=1.0)
        assert _event_id_from_chunk(first_chunk) == first_id
        await initial_stream.aclose()
        await _wait_until(lambda: len(subscribers) == 0)

        replay_ids = [
            sse_service.publish(
                buffer,
                subscribers,
                "approval.reconnect",
                {"approval_id": "appr-long-001", "sequence_no": sequence_no},
            )
            for sequence_no in (2, 3)
        ]
        reconnect_stream = sse_service.stream(channel, buffer, subscribers, last_event_id=first_id)
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

        monkeypatch.setattr(evt_service_module.asyncio, "wait_for", force_one_heartbeat)
        heartbeat_chunk = await original_wait_for(anext(reconnect_stream), timeout=1.0)
        assert heartbeat_chunk == ": heartbeat\n\n"
        await reconnect_stream.aclose()
        await _wait_until(lambda: len(subscribers) == 0)

        second_reconnect_stream = sse_service.stream(channel, buffer, subscribers, last_event_id=first_id)
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

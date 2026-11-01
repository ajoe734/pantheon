"""SSE streams end promptly when the BFF begins shutting down."""
from __future__ import annotations

import asyncio

import pytest

from services.control_plane.bff.events import router as events_router
from services.control_plane.bff.events.service import EventStreamService


@pytest.fixture(autouse=True)
def _reset():
    events_router.reset_sse_shutdown()
    yield
    events_router.reset_sse_shutdown()


def test_service_stream_ends_on_shutdown_and_unsubscribes() -> None:
    async def scenario() -> None:
        svc = EventStreamService()
        buf, subs = svc.buffers["approval"], svc.subscribers["approval"]
        stream = events_router._end_on_shutdown(
            svc.stream("approval", buf, subs, last_event_id=None)
        )
        nxt = asyncio.create_task(anext(stream))
        for _ in range(50):
            if subs:
                break
            await asyncio.sleep(0)
        assert len(subs) == 1
        events_router.begin_sse_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(nxt, timeout=1.0)
        assert subs == []

    asyncio.run(scenario())


def test_shutdown_from_other_thread_wakes_waiter() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        waiter = asyncio.create_task(events_router._race_shutdown(asyncio.sleep(30)))
        await asyncio.sleep(0.01)
        await loop.run_in_executor(None, events_router.begin_sse_shutdown)
        stopped, _ = await asyncio.wait_for(waiter, timeout=1.0)
        assert stopped

    asyncio.run(scenario())


def test_stream_passes_events_through_without_shutdown() -> None:
    async def scenario() -> None:
        async def src():
            yield 1
            yield 2

        assert [x async for x in events_router._end_on_shutdown(src())] == [1, 2]

    asyncio.run(scenario())


def test_frontend_liveness_stream_ends_on_shutdown() -> None:
    async def scenario() -> None:
        gen = events_router._end_on_shutdown(
            events_router._default_frontend_bff_event_stream(("system",))
        )
        await anext(gen)
        nxt = asyncio.create_task(anext(gen))
        await asyncio.sleep(0.01)
        events_router.begin_sse_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(nxt, timeout=1.0)

    asyncio.run(scenario())

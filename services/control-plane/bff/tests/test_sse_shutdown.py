"""SSE streams end promptly when the BFF begins shutting down."""
from __future__ import annotations

import asyncio

import pytest

from services.control_plane.bff.events import shutdown as sse_shutdown
from services.control_plane.bff.events.service import EventStreamService


@pytest.fixture(autouse=True)
def _reset():
    sse_shutdown.reset()
    yield
    sse_shutdown.reset()


def test_service_stream_ends_on_shutdown_and_unsubscribes() -> None:
    async def scenario() -> None:
        svc = EventStreamService()
        buf, subs = svc.buffers["approval"], svc.subscribers["approval"]
        stream = svc.stream("approval", buf, subs, last_event_id=None)
        nxt = asyncio.create_task(anext(stream))
        for _ in range(50):
            if subs:
                break
            await asyncio.sleep(0)
        assert len(subs) == 1
        sse_shutdown.begin_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(nxt, timeout=1.0)
        assert subs == []

    asyncio.run(scenario())


def test_shutdown_from_other_thread_wakes_waiter() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        waiter = asyncio.create_task(sse_shutdown.race(asyncio.sleep(30)))
        await asyncio.sleep(0.01)
        await loop.run_in_executor(None, sse_shutdown.begin_shutdown)
        stopped, _ = await asyncio.wait_for(waiter, timeout=1.0)
        assert stopped

    asyncio.run(scenario())


def test_race_returns_result_when_no_shutdown() -> None:
    async def scenario() -> None:
        async def val() -> int:
            return 7
        assert await sse_shutdown.race(val()) == (False, 7)

    asyncio.run(scenario())


def test_frontend_liveness_stream_ends_on_shutdown() -> None:
    from services.control_plane.bff.events.router import _default_frontend_bff_event_stream

    async def scenario() -> None:
        gen = _default_frontend_bff_event_stream(("system",))
        await anext(gen)
        nxt = asyncio.create_task(anext(gen))
        await asyncio.sleep(0.01)
        sse_shutdown.begin_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(nxt, timeout=1.0)

    asyncio.run(scenario())

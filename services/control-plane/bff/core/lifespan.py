"""ASGI lifespan helpers for non-blocking provider observability."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, Callable

from fastapi import FastAPI

from ..auth.service import ProviderReadinessCache


async def refresh_provider_readiness(
    cache: ProviderReadinessCache,
    *,
    interval_seconds: float,
) -> None:
    """Refresh forever; every individual probe is bounded by the cache."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    while True:
        await cache.refresh()
        await asyncio.sleep(interval_seconds)


def create_lifespan(
    cache: ProviderReadinessCache,
    *,
    interval_seconds: float = 30.0,
    task_factory: Callable[..., asyncio.Task] = asyncio.create_task,
):
    """Return a lifespan that schedules refresh without awaiting first probe."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.provider_readiness_cache = cache
        refresh_task = task_factory(
            refresh_provider_readiness(cache, interval_seconds=interval_seconds),
            name="bff-provider-readiness-refresh",
        )
        app.state.provider_readiness_refresh_task = refresh_task
        try:
            yield
        finally:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task

    return lifespan

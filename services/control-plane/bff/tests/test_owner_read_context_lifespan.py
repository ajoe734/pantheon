"""Owner read cache must not span lifespan background tasks.

Task: PERSONA-PROVISIONING-BACKGROUND-PROMOTE-R3-20261103
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware import Middleware

from services.control_plane.bff.auth.service import ProviderReadinessCache
from services.control_plane.bff.core import lifespan as lifespan_module
from services.control_plane.bff.core.lifespan import create_lifespan
from services.control_plane.bff.core.owner_reads import (
    OwnerReadContextMiddleware,
    cached_owner_call,
    request_read_cache,
)


def _cache() -> ProviderReadinessCache:
    async def probe():
        return {}

    return ProviderReadinessCache(probe=probe, stale_after_seconds=10.0)


def _app(lifespan) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.user_middleware.append(Middleware(OwnerReadContextMiddleware))
    app.middleware_stack = None
    return app


def test_lifespan_task_sees_no_request_read_cache(monkeypatch):
    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_RECONCILER_ENABLED", "false")
    seen: list = []

    async def probe_task():
        seen.append(request_read_cache.get())

    def factory(coro, name=None):
        coro.close()
        return asyncio.get_running_loop().create_task(probe_task())

    lp = create_lifespan(
        _cache(), prewarm_jwks=False, task_factory=factory, downstream_health_monitor=None
    )
    # TestClient drives the real ASGI lifespan scope through the middleware.
    with TestClient(_app(lp)):
        pass
    assert seen and all(v is None for v in seen)


def test_requests_keep_isolated_deduping_caches():
    app = FastAPI()
    app.user_middleware.append(Middleware(OwnerReadContextMiddleware))
    caches = []

    @app.get("/x")
    def x():
        calls = []
        cached_owner_call(("k",), lambda: calls.append(1) or 1)
        cached_owner_call(("k",), lambda: calls.append(1) or 1)
        caches.append(request_read_cache.get())
        return {"calls": len(calls)}

    with TestClient(app) as client:
        assert client.get("/x").json() == {"calls": 1}
        assert client.get("/x").json() == {"calls": 1}
    assert caches[0] is not caches[1]

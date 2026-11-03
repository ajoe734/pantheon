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
from services.control_plane.bff.personas import service as persona_service


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


def test_background_reconcile_pass_sees_fresh_owner_data_each_pass(monkeypatch):
    """The real loop, under middleware + lifespan, must not freeze owner reads."""
    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_RECONCILER_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_RECONCILE_SECONDS", "1")
    sessions: list = []
    observed: list = []

    def fake_once() -> int:
        observed.append(len(cached_owner_call(("rsp_paper_monitoring",), lambda: list(sessions))))
        # same-pass dedupe still holds
        again = cached_owner_call(("rsp_paper_monitoring",), lambda: ["never"])
        assert len(again) == observed[-1]
        return 0

    monkeypatch.setattr(persona_service, "_reconcile_persona_provisioning_once", fake_once)
    lp = create_lifespan(_cache(), prewarm_jwks=False, downstream_health_monitor=None)
    app = _app(lp)

    async def run():
        async with lp(app):
            while not observed:
                await asyncio.sleep(0.1)
            sessions.append({"runtime_id": "rt-new"})  # worker appears after first pass
            for _ in range(60):
                if len(observed) >= 2 and observed[-1] == 1:
                    break
                await asyncio.sleep(0.1)

    asyncio.run(run())
    assert observed[0] == 0
    assert observed[-1] == 1

"""OPGAP-BE-BFF-CORE-V2 async auth/core acceptance evidence.

The tests deliberately use ``httpx.AsyncClient(ASGITransport)``.  A slow or
failed provider probe runs concurrently while local auth and settings routes
must remain responsive.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from services.control_plane.bff.auth.service import AuthFacadeService, ProviderReadinessCache
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.core.app_factory import (
    ROUTE_ASSIGNMENTS,
    create_settings_router,
    create_assistant_management_router,
    create_core_router,
)
from services.control_plane.bff.core.lifespan import create_lifespan
from services.control_plane.bff.tests.conftest import asgi_request


TASK_DELIVERY_EVIDENCE = {
    "task_id": "OPGAP-BE-BFF-CORE-V2-20260830",
    "owned_layer": "prepared BFF auth/core routing",
    "not_changing": "services/control-plane/bff/main.py",
    "route_assignment_count": 30,
    "transport": "httpx.AsyncClient(ASGITransport)",
}

REPO_ROOT = Path(__file__).resolve().parents[4]
ROUTE_CATALOG = (
    REPO_ROOT
    / "docs/04/pantheon_full_product_operation_audit_2026-08-29"
    / "EXECUTION_TASK_CATALOG_2026-08-30.json"
)


class _MemorySettingsStore:
    def __init__(self) -> None:
        self.value = {"general": {"language": "zh-TW"}}

    def get(self) -> dict[str, Any]:
        return copy.deepcopy(self.value)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.value.update(copy.deepcopy(patch))
        return self.get()

    def export_json(self) -> str:
        return json.dumps(self.value)

    def import_json(self, value: str) -> dict[str, Any]:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("settings must be an object")
        self.value = parsed
        return self.get()


async def _local_ready(**_kwargs: Any) -> dict[str, Any]:
    return {
        "data": {
            "ready": True,
            "authReady": True,
            "auth": {
                "mode": "strict",
                "strict": True,
                "sessionKind": "bearer",
                "sessionReady": True,
            },
            "identity": {
                "operatorId": "operator-test",
                "roles": ["operator"],
                "tenantId": "tenant-test",
                "capabilities": ["agora.workshop.v1"],
            },
        }
    }


def _auth_service(cache: ProviderReadinessCache) -> AuthFacadeService:
    return AuthFacadeService(
        local_readiness=_local_ready,
        provider_readiness_cache=cache,
    )


def _app(cache: ProviderReadinessCache) -> FastAPI:
    app = FastAPI(title="Pantheon Operator BFF Core Test", version="0.2.0")
    app.include_router(create_auth_router(service=_auth_service(cache)))
    app.include_router(
        create_settings_router(
            settings_store=_MemorySettingsStore(),
            extract_identity=lambda _authorization, **_kwargs: object(),
            require_admin_mfa=lambda _identity, _action: None,
        )
    )
    app.include_router(create_assistant_management_router({}))
    app.include_router(create_core_router({}))
    return app


def test_core_factory_matches_exact_30_route_assignment() -> None:
    app = _app(ProviderReadinessCache())
    actual: set[tuple[str, str]] = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            pending.extend(included_router.routes)
            continue
        if isinstance(route, APIRoute):
            actual.update(
                (method, route.path)
                for method in route.methods or set()
                if method != "HEAD"
            )
    expected = {(method, path) for method, path, _handler in ROUTE_ASSIGNMENTS}

    assert len(ROUTE_ASSIGNMENTS) == 30
    assert actual == expected


def test_route_assignment_constant_matches_canonical_catalog() -> None:
    catalog = json.loads(ROUTE_CATALOG.read_text(encoding="utf-8"))
    catalog_rows = [
        (method, path, handler)
        for method, path, handler, _line, owner, _target in catalog[
            "route_migration_inventory"
        ]["assignments"]
        if owner == "OPGAP-BE-BFF-CORE-20260830"
    ]
    normalized_runtime_rows = [
        (method, re.sub(r"\{[^{}]+\}", "{param}", path), handler)
        for method, path, handler in ROUTE_ASSIGNMENTS
    ]

    assert normalized_runtime_rows == catalog_rows


def test_auth_readiness_stays_below_50ms_while_provider_probe_is_slow() -> None:
    calls = 0

    def slow_probe() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        time.sleep(0.20)
        return {"provider": "openclaw", "ready": True, "status": "ready"}

    cache = ProviderReadinessCache(
        slow_probe,
        timeout_seconds=0.5,
        stale_after_seconds=30.0,
    )
    app = _app(cache)

    async def scenario() -> tuple[httpx.Response, float]:
        refresh = asyncio.create_task(cache.refresh())
        await asyncio.sleep(0.01)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bff.test") as client:
            started = time.perf_counter()
            response = await asyncio.wait_for(
                client.get("/bff/auth/readiness"),
                timeout=0.05,
            )
            elapsed = time.perf_counter() - started
        await refresh
        return response, elapsed

    response, elapsed = asyncio.run(scenario())
    data = response.json()["data"]

    assert response.status_code == 200
    assert elapsed < 0.05
    assert data["ready"] is True
    assert data["authReady"] is True
    assert data["providerReady"] is False
    assert data["provider"]["cached"] is True
    assert calls == 1


def test_failed_provider_refresh_degrades_cache_without_flipping_auth(asgi_request) -> None:
    def failed_probe() -> dict[str, Any]:
        raise ConnectionError("provider unavailable")

    cache = ProviderReadinessCache(failed_probe, timeout_seconds=0.05)
    asyncio.run(cache.refresh())

    response = asgi_request(
        _app(cache),
        "GET",
        "/bff/auth/readiness",
        timeout_seconds=0.05,
    )
    data = response.json()["data"]

    assert response.status_code == 200
    assert data["authReady"] is True
    assert data["ready"] is True
    assert data["providerReady"] is False
    assert data["provider"]["status"] == "unavailable"
    assert data["provider"]["reason"] == "ConnectionError"


def test_settings_routes_do_not_consult_provider_cache(asgi_request) -> None:
    probe_calls = 0

    def forbidden_probe() -> dict[str, Any]:
        nonlocal probe_calls
        probe_calls += 1
        raise AssertionError("settings request called provider readiness")

    cache = ProviderReadinessCache(forbidden_probe)
    app = _app(cache)

    read_response = asgi_request(app, "GET", "/api/v1/settings")
    update_response = asgi_request(
        app,
        "POST",
        "/api/v1/settings",
        json={"featureFlags": {"demo": False}},
    )

    assert read_response.status_code == 200
    assert read_response.json()["general"]["language"] == "zh-TW"
    assert update_response.status_code == 200
    assert update_response.json()["settings"]["featureFlags"]["demo"] is False
    assert probe_calls == 0


def test_provider_timeout_is_bounded_and_published_as_degraded() -> None:
    def blocked_probe() -> dict[str, Any]:
        time.sleep(0.20)
        return {"ready": True}

    cache = ProviderReadinessCache(blocked_probe, timeout_seconds=0.02)

    async def scenario() -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        snapshot = await cache.refresh()
        return snapshot, time.perf_counter() - started

    snapshot, elapsed = asyncio.run(scenario())

    assert elapsed < 0.10
    assert snapshot["ready"] is False
    assert snapshot["status"] == "unavailable"
    assert snapshot["reason"] == "timeout"


def test_lifespan_schedules_first_probe_without_blocking_startup() -> None:
    def slow_probe() -> dict[str, Any]:
        time.sleep(0.20)
        return {"ready": True, "status": "ready"}

    cache = ProviderReadinessCache(slow_probe, timeout_seconds=0.5)
    app = _app(cache)
    lifespan = create_lifespan(cache, interval_seconds=30.0)

    async def scenario() -> float:
        started = time.perf_counter()
        async with lifespan(app):
            elapsed = time.perf_counter() - started
            assert app.state.provider_readiness_cache is cache
        return elapsed

    elapsed = asyncio.run(scenario())
    assert elapsed < 0.05


@pytest.mark.parametrize("usage_handler_async", [True, False])
def test_assistant_providers_and_usage_summary_offload_sync_probes_with_contextvars(
    monkeypatch: pytest.MonkeyPatch,
    usage_handler_async: bool,
) -> None:
    import contextvars
    import threading
    from services.control_plane.bff.assistant import management_service
    from services.control_plane.bff.assistant.routes import create_assistant_router

    req_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("test_req_ctx", default="unset")
    observed_ctx: list[str] = []
    release = threading.Event()
    started = {name: threading.Event() for name in ("providers", "usage")}

    def slow_provider_list(auth_probe: bool = False) -> dict[str, Any]:
        observed_ctx.append(f"providers:{req_ctx.get()}:{auth_probe}")
        started["providers"].set()
        assert release.wait(5), "provider callback was not released while auth ran"
        return {"status": "ok", "data": [{"provider": "openclaw", "ready": True}]}

    def slow_usage_summary(*, auth_probe: bool = False, limit: int = 50, window_hours: int = 24) -> dict[str, Any]:
        observed_ctx.append(f"usage:{req_ctx.get()}:{auth_probe}:{limit}:{window_hours}")
        started["usage"].set()
        assert release.wait(5), "usage callback was not released while auth ran"
        return {"status": "ok", "data": {"providers": []}}

    monkeypatch.setattr(management_service, "_extract_identity", lambda _auth, **_kw: object())
    monkeypatch.setattr(management_service, "_require_read_role", lambda _id: None)
    monkeypatch.setattr(management_service, "_assistant_provider_usage_summary", slow_usage_summary)

    cache = ProviderReadinessCache(lambda: {"ready": True, "status": "ready"})
    auth_service = AuthFacadeService(
        local_readiness=_local_ready,
        provider_readiness_cache=cache,
        handlers={
            "bff_logout": lambda **_kwargs: {"status": "ok"},
            "bff_me": lambda **_kwargs: {
                "data": {
                    "currentUser": {"id": "pantheon-dev-admin-test", "display_name": "admintest"},
                    "session": {"authenticated": True},
                }
            }
        },
    )
    app = FastAPI()

    @app.middleware("http")
    async def _set_ctx(request, call_next):
        tok = req_ctx.set(request.headers.get("x-test-ctx", "default-ctx"))
        try:
            return await call_next(request)
        finally:
            req_ctx.reset(tok)

    app.include_router(create_auth_router(service=auth_service))
    app.include_router(
        create_assistant_router(
            build_context_pack=lambda *_a, **_kw: None,
            extract_identity=lambda _auth, **_kw: object(),
            require_read_role=lambda _id: None,
            provider_list=slow_provider_list,
            provider_readiness=lambda: {"ready": True},
        )
    )
    # Cover both supported composition paths: the async owner endpoint and
    # the standalone router's synchronous handler fallback.
    app.include_router(create_assistant_management_router({
        "bff_assistant_provider_usage_summary": (
            management_service.bff_assistant_provider_usage_summary
            if usage_handler_async else slow_usage_summary
        ),
    }))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bff.test") as client:
            slow_providers = asyncio.create_task(
                client.get("/bff/assistant/providers?auth_probe=true", headers={"x-test-ctx": "ctx-p"})
            )
            slow_usage = asyncio.create_task(
                client.get(
                    "/bff/assistant/providers/usage-summary?auth_probe=true&limit=25&window_hours=12",
                    headers={"x-test-ctx": "ctx-u"},
                )
            )
            try:
                # Events prove both external callbacks are still held, so an
                # accidentally synchronous implementation cannot pass after its
                # slow work finishes. Deadlines only bound a hung test.
                for event in started.values():
                    assert await asyncio.to_thread(event.wait, 5)
                readiness_resp, me_resp, logout_resp = await asyncio.wait_for(
                    asyncio.gather(
                        client.get("/bff/auth/readiness"),
                        client.get("/bff/me"),
                        client.post("/bff/logout", json={}),
                    ),
                    timeout=5,
                )
                assert all(r.status_code == 200 for r in (readiness_resp, me_resp, logout_resp))
                assert not slow_providers.done()
                assert not slow_usage.done()
            finally:
                release.set()
                results = await asyncio.gather(slow_providers, slow_usage)
            assert all(r.status_code == 200 for r in results)

    asyncio.run(scenario())
    assert "providers:ctx-p:True" in observed_ctx
    assert "usage:ctx-u:True:25:12" in observed_ctx


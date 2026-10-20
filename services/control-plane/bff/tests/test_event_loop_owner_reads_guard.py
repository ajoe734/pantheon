"""BFF-EVENT-LOOP-OWNER-READS-20261020: Management page-load reads stay off the event loop.

Blocking owner reads (urllib owner transports, sync psycopg) executed on the
event-loop thread stall every other request, including /bff/me.  This guard
(1) patches the owner transports to record any call made on a thread that is
running an asyncio loop and drives every listed route through the ASGI app,
(2) pins the converted handlers to ``def`` so the threadpool serves them, and
(3) proves /bff/me and /bff/auth/readiness stay responsive while a
persona-league read is held inside a blocking owner read.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time

import pytest

from services.control_plane.bff.test_execute_plans_final_live_wiring_contract import (  # noqa: E402
    HEADERS,
    _isolated_final_read_models,
)

# Handlers that are plain ``def`` (served by the anyio threadpool).
SYNC_HANDLER_PATHS = [
    "/bff/management/persona-league",
    "/bff/management/persona-league/rankings",
    "/bff/management/persona-league/movers",
    "/bff/management/persona-league/tiers",
    "/bff/management/persona-league/heatmap",
    "/bff/management/promotion-reviews",
    "/bff/management/promotion-reviews/{review_id}",
    "/bff/management/quarterly-ranking/formula",
    "/bff/persona-league",
    "/bff/management/persona-league/{persona_id}",
    "/bff/personas",
    "/bff/personas/{persona_id}",
    "/bff/personas/{persona_id}/route-policy",
    "/bff/runtimes",
    "/bff/v5/execution/persona-health",
    "/bff/v5/loop-runs",
    "/bff/deployments",
    "/bff/management/shell-summary",
    "/bff/management/trading-pulse",
    "/bff/ranking-formulas",
    "/bff/research-experiments",
    "/bff/artifacts",
    "/bff/jobs",
    "/bff/evolution-programs",
    "/bff/incidents",
    "/bff/audit",
    "/bff/strategies",
    "/bff/tools",
    "/bff/skills",
    "/bff/mcp-servers",
    "/api/v1/knowledge/notes",
    "/api/v1/knowledge/memory",
    "/bff/agora/capabilities",
    "/bff/agora/journal",
]

# Handlers that stay ``async def`` because they await, but must offload
# their blocking reads (checked at runtime by the transport guard).
ASYNC_OFFLOADED_PATHS = [
    "/bff/search",
    "/bff/management/cockpit",
    "/bff/alerts",
    "/bff/approvals",
    "/bff/v5/loop-health",
    "/bff/assistant/providers/usage-summary",
    "/bff/assistant/providers",
]

RUNTIME_GET_PATHS = [
    "/bff/management/persona-league",
    "/bff/management/persona-league/rankings",
    "/bff/management/persona-league/movers",
    "/bff/management/persona-league/tiers",
    "/bff/management/persona-league/heatmap",
    "/bff/management/promotion-reviews",
    "/bff/management/quarterly-ranking/formula",
    "/bff/persona-league",
    "/bff/management/persona-fleet",
    "/bff/personas",
    "/bff/personas/persona_001",
    "/bff/personas/persona_001/route-policy",
    "/bff/runtimes",
    "/bff/v5/execution/persona-health",
    "/bff/v5/loop-health",
    "/bff/v5/loop-runs",
    "/bff/deployments",
    "/bff/management/shell-summary",
    "/bff/management/cockpit",
    "/bff/management/trading-pulse",
    "/bff/ranking-formulas",
    "/bff/search?q=a",
    "/api/v1/knowledge/notes",
    "/bff/research-experiments",
    "/bff/artifacts",
    "/bff/assistant/providers/usage-summary",
    "/bff/assistant/providers",
    "/bff/jobs",
    "/bff/evolution-programs",
    "/bff/alerts",
    "/bff/incidents",
    "/bff/audit",
    "/bff/approvals",
    "/bff/agora/capabilities",
    "/bff/agora/journal",
    "/bff/tools",
    "/bff/skills",
    "/bff/mcp-servers",
    "/bff/strategies",
]


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


def _on_event_loop_thread() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _iter_api_routes(routes):
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:  # FastAPI lazily-included router branch
            yield from _iter_api_routes(original.routes)
        else:
            yield route


def _route_endpoint(app, path: str):
    for route in _iter_api_routes(app.routes):
        if getattr(route, "path", None) == path and "GET" in (getattr(route, "methods", None) or ()):
            return route.endpoint
    return None


def test_converted_management_handlers_are_threadpool_sync() -> None:
    with _isolated_final_read_models() as client:
        offenders = []
        for path in SYNC_HANDLER_PATHS:
            endpoint = _route_endpoint(client.app, path)
            assert endpoint is not None, f"GET {path} is not registered"
            if inspect.iscoroutinefunction(endpoint):
                offenders.append(path)
    assert not offenders, f"async def handlers still run owner reads on the event loop: {offenders}"


def test_owner_transports_are_never_called_on_the_event_loop(monkeypatch) -> None:
    import urllib.request

    from services.control_plane.bff.command_adapters import base as adapters_base
    from services.control_plane.bff.core import owner_reads

    violations: list[str] = []

    def _guard(name: str):
        def _fail(*_args, **_kwargs):
            if _on_event_loop_thread():
                violations.append(name)
            raise ConnectionError(f"{name} blocked by event-loop guard")

        return _fail

    monkeypatch.setattr(urllib.request, "urlopen", _guard("urllib.request.urlopen"))
    monkeypatch.setattr(adapters_base, "http_request_json", _guard("command_adapters.base.http_request_json"))
    monkeypatch.setattr(owner_reads, "_evolution_http_get", _guard("core.owner_reads._evolution_http_get"))
    try:
        import psycopg

        monkeypatch.setattr(psycopg, "connect", _guard("psycopg.connect"))
    except ImportError:  # pragma: no cover - psycopg is optional in light envs
        pass

    with _isolated_final_read_models() as client:
        for path in RUNTIME_GET_PATHS:
            client.get(path, headers=HEADERS)

    assert not violations, f"blocking owner transport invoked on the event loop: {sorted(set(violations))}"


def test_me_and_readiness_stay_responsive_while_persona_league_blocks(monkeypatch) -> None:
    httpx = pytest.importorskip("httpx")
    from services.control_plane.bff.personas.service import PersonaService

    hold_seconds = 5.0
    entered = threading.Event()

    def _blocking_owner_read(self, *_args, **_kwargs):
        entered.set()
        time.sleep(hold_seconds)  # stands in for a blocking owner read
        return {"data": [], "items": [], "page_info": {}, "meta": {}}

    monkeypatch.setattr(PersonaService, "get_persona_league", _blocking_owner_read)

    async def _drive(app) -> dict[str, float]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bff.test") as client:
            league = asyncio.create_task(
                client.get("/bff/management/persona-league", headers=HEADERS)
            )
            # The league task is scheduled first; if its handler blocks the
            # event loop, the probes below cannot even start until it returns,
            # so latency is measured from the moment all three are created.
            started = time.perf_counter()

            async def _timed(path: str) -> float:
                response = await client.get(path, headers=HEADERS)
                assert response.status_code < 500, response.text
                return time.perf_counter() - started

            me_task = asyncio.create_task(_timed("/bff/me"))
            readiness_task = asyncio.create_task(_timed("/bff/auth/readiness"))
            me, readiness = await asyncio.gather(me_task, readiness_task)
            assert entered.wait(timeout=2), "persona-league never reached the stubbed owner read"
            await league
            return {"me": me, "readiness": readiness}

    with _isolated_final_read_models() as client:
        timings = asyncio.run(_drive(client.app))

    print(f"concurrency timings (seconds): {timings}")
    assert timings["me"] < 1.0, timings
    assert timings["readiness"] < 1.0, timings

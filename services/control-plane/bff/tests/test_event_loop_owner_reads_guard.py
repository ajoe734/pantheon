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
    "/bff/research-tasks",
    "/bff/research-tasks/{task_id}",
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
    "/bff/search",
    "/bff/agora/capabilities",
    "/bff/agora/journal",
    "/bff/alpha-factory",
]

# Handlers that stay ``async def`` because they await, but must offload
# their blocking reads (checked at runtime by the transport guard).
ASYNC_OFFLOADED_PATHS = [
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
    "/bff/research-tasks",
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
    "/bff/alpha-factory",
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


# GET routes under these prefixes are enumerated from the composed route table.
GUARDED_PREFIXES = ("/bff/management", "/bff/v5")

# Routes the table-driven guard cannot drive through a plain GET: path -> reason.
RUNTIME_GUARD_SKIP = {
    "/bff/management/chat/stream": "SSE stream never completes under TestClient",
    "/bff/v5/events/stream": "SSE stream never completes under TestClient",
}


def _enumerate_guarded_get_paths(app) -> list[str]:
    import re

    paths: list[str] = []
    for route in _iter_api_routes(app.routes):
        path = getattr(route, "path", None)
        if not path or "GET" not in (getattr(route, "methods", None) or ()):
            continue
        if not path.startswith(GUARDED_PREFIXES) or path in RUNTIME_GUARD_SKIP:
            continue
        paths.append(re.sub(r"\{[^}]+\}", "guard-id-001", path))
    return sorted(set(paths))


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
        table_paths = _enumerate_guarded_get_paths(client.app)
        assert len(table_paths) > 50, "route table enumeration looks empty"
        for path in [*RUNTIME_GET_PATHS, *table_paths]:
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


def test_performance_attribution_blocking_read_does_not_stall_me_or_livez(monkeypatch) -> None:
    httpx = pytest.importorskip("httpx")
    from services.control_plane.bff.agora.performance import service as agora_perf

    entered = threading.Event()
    hold_seconds = 4.0

    def _blocking_owner_read(*_args, **_kwargs):
        entered.set()
        time.sleep(hold_seconds)  # stands in for the ~12s hosted owner read
        return {"data": [], "items": [], "meta": {}}

    monkeypatch.setattr(agora_perf, "pm12_performance_attribution_response", _blocking_owner_read)
    assert not inspect.iscoroutinefunction(
        _route_endpoint_for_probe("/bff/management/performance-attribution")
    )

    async def _drive(app) -> dict[str, float]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bff.test") as client:
            attribution = asyncio.create_task(
                client.get("/bff/management/performance-attribution", headers=HEADERS)
            )
            for _ in range(200):  # let the handler reach the blocking read
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set(), "performance-attribution never reached the stubbed owner read"
            started = time.perf_counter()

            async def _timed(path: str) -> float:
                response = await client.get(path, headers=HEADERS)
                assert response.status_code < 500, response.text
                return time.perf_counter() - started

            me, livez = await asyncio.gather(_timed("/bff/me"), _timed("/livez"))
            await attribution
            return {"me": me, "livez": livez}

    with _isolated_final_read_models() as client:
        timings = asyncio.run(_drive(client.app))

    print(f"performance-attribution concurrency timings (seconds): {timings}")
    assert timings["me"] < 1.0, timings
    assert timings["livez"] < 1.0, timings


def _route_endpoint_for_probe(path: str):
    with _isolated_final_read_models() as client:
        return _route_endpoint(client.app, path)


# BFF-EVO-ALPHA-PAGE-TRUTH-20261107: the composed app wires /bff/alpha-factory
# to the strategies router's registry reader, on a plain ``def`` handler.
_WIRING_STATES = {
    "strat-src-dev-product-spy-anchor": "approved",
    "strat-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-60f9c8a78715": "approved",
    "strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8": "retired",
    "strat-hosted-all12-h12-claude-20261027-4923-src-106b4057d07a": "retired",
}


def _hosted_four_entries_for_wiring():
    return [
        {
            "admission_id": f"adm-wire-{idx}",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": f"spec-wire-{idx}",
            "strategy_id": strategy_id,
            "admission_status": "admitted",
            "status": "admitted",
            "queue_status": "completed",
            "attempt_count": 0,
            "last_error": None,
        }
        for idx, strategy_id in enumerate(_WIRING_STATES)
    ]


def test_real_app_alpha_factory_reads_state_through_registry_strategy_reader(monkeypatch) -> None:
    """The composed app wires the alpha factory to the strategies router service."""
    import inspect

    from services.control_plane.bff.console_gap import alpha_factory as alpha_module
    from services.control_plane.bff.strategies.service import StrategiesService

    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research-orchestrator.local")
    # The composed app captured _default_http_get at import; stub its network edge.
    monkeypatch.setattr(alpha_module, "bound_tenant", lambda payload, **kw: kw.get("tenant_id"))
    monkeypatch.setattr(
        alpha_module,
        "http_request_json_with_headers",
        lambda url, **kw: (200, {}, _hosted_four_entries_for_wiring()),
    )
    state_calls: List[str] = []

    def _state(self, strategy_id: str):
        state_calls.append(strategy_id)
        return _WIRING_STATES[strategy_id], "ok"

    monkeypatch.setattr(StrategiesService, "get_strategy_registry_state", _state)

    with _isolated_final_read_models() as client:
        endpoint = _route_endpoint(client.app, "/bff/alpha-factory")
        assert endpoint is not None and not inspect.iscoroutinefunction(endpoint)
        resp = client.get("/bff/alpha-factory?page_size=100", headers={**HEADERS, "X-Tenant-Id": "tenant-dev"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert sorted(state_calls) == sorted(_WIRING_STATES)
    assert {c["strategy_id"]: c["strategy_state"] for c in payload["data"]["items"]} == _WIRING_STATES
    assert payload["meta"]["surfaces"]["alpha_factory_strategy_state"]["status"] == "ok"

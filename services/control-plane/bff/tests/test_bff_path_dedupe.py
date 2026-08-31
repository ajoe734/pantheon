from __future__ import annotations

import os
import re
import sys
from typing import Iterable

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main

OPERATOR_HEADERS = {"Authorization": "Bearer op-path-dedupe:operator,admin"}


def _client() -> TestClient:
    return TestClient(bff_main.app)


def _assert_deprecated(response, replacement: str) -> None:
    assert response.status_code == 410, response.text
    assert response.headers["X-Deprecated"] == "true"
    assert response.headers["X-Deprecated-At"] == "2026-05-25T08:40:02Z"
    assert response.headers["Deprecation"] == "true"
    assert response.headers["X-Pantheon-Replacement-Route"] == replacement
    body = response.json()
    assert body["error"]["details"]["replacement"] == replacement
    assert body["meta"]["deprecation"]["replacement"] == replacement


def _iter_all_routes(routes) -> list:
    """Flatten APIRoute objects, expanding include_router()-mounted sub-routers.

    Newer FastAPI keeps a mounted APIRouter as a single ``_IncludedRouter``
    entry in ``app.routes`` (with ``path=None``) instead of flattening its
    routes in place, so callers must recurse into ``original_router.routes``
    to see the real endpoints.
    """
    flattened: list = []
    for route in routes:
        sub_router = getattr(route, "original_router", None)
        if sub_router is not None:
            flattened.extend(_iter_all_routes(sub_router.routes))
            continue
        flattened.append(route)
    return flattened


def _route_paths_for_method(method: str) -> list[str]:
    paths: list[str] = []
    for route in _iter_all_routes(bff_main.app.routes):
        methods = getattr(route, "methods", set()) or set()
        if method in methods:
            paths.append(getattr(route, "path", ""))
    return paths


def _normalized_route(path: str) -> str:
    return re.sub(r"\{[^}/]+\}", "{}", path)


def _route_count(method: str, normalized_path: str) -> int:
    return sum(
        1
        for path in _route_paths_for_method(method)
        if _normalized_route(path) == normalized_path
    )


def test_deprecated_alternate_url_families_return_410_with_headers() -> None:
    client = _client()

    get_cases = [
        ("/bff/mcp/servers", "/bff/mcp-servers"),
        ("/bff/mcp/servers/server-1", "/bff/mcp-servers/{server_id}"),
        ("/bff/ranking/formulas", "/bff/ranking-formulas"),
        ("/bff/ranking/formulas/formula-1", "/bff/ranking-formulas/{formula_id}"),
    ]
    for path, replacement in get_cases:
        _assert_deprecated(client.get(path, headers=OPERATOR_HEADERS), replacement)

    write_cases = [
        ("post", "/bff/ranking/formulas", "/bff/ranking-formulas"),
        ("patch", "/bff/ranking/formulas/formula-1", "/bff/ranking-formulas/{formula_id}"),
        (
            "post",
            "/bff/ranking/formulas/formula-1/actions/promote",
            "/bff/actions/rankingFormula/{formula_id}/{action_id}",
        ),
        (
            "post",
            "/bff/mcp/tools/tool-1/actions/enable",
            "/bff/mcp-tools/{tool_id}/{action_id}",
        ),
    ]
    for method, path, replacement in write_cases:
        response = getattr(client, method)(path, headers=OPERATOR_HEADERS)
        _assert_deprecated(response, replacement)


def test_deprecated_nested_action_families_return_410_with_headers() -> None:
    client = _client()

    cases = [
        ("/bff/strategies/strategy-1/actions/promote", "/bff/actions/strategy/{strategy_id}/{action_id}"),
        ("/bff/personas/persona-1/actions/promote", "/bff/actions/persona/{persona_id}/{action_id}"),
        ("/bff/capital-pools/pool-1/actions/freeze", "/bff/actions/capitalPool/{pool_id}/{action_id}"),
        ("/bff/rebalances/rebalance-1/actions/approve", "/bff/actions/rebalance/{rebalance_id}/{action_id}"),
        ("/bff/deployments/deployment-1/actions/promote", "/bff/actions/deployment/{deployment_id}/{action_id}"),
        ("/bff/runtimes/runtime-1/actions/pause", "/bff/actions/runtime/{runtime_id}/{action_id}"),
        ("/bff/skills/skill-1/actions/disable", "/bff/actions/skill/{skill_id}/{action_id}"),
        ("/bff/tools/tool-1/actions/disable", "/bff/actions/tool/{tool_id}/{action_id}"),
    ]
    for path, replacement in cases:
        _assert_deprecated(client.post(path, headers=OPERATOR_HEADERS), replacement)


def test_path_parameter_dedupe_keeps_only_snake_case_canonical_templates() -> None:
    routes = set()
    for route in _iter_all_routes(bff_main.app.routes):
        routes.add(getattr(route, "path", ""))

    canonical_templates = {
        "/bff/personas/{persona_id}",
        "/bff/capital-pools/{pool_id}",
        "/bff/deployments/{deployment_id}",
        "/bff/rebalances/{rebalance_id}",
        "/bff/incidents/{incident_id}",
        "/bff/runtimes/{runtime_id}",
        "/bff/skills/{skill_id}",
        "/bff/tools/{tool_id}",
        "/bff/strategies/{strategy_id}/actions/{action_id}",
        "/bff/mcp-servers/{server_id}/import-tools",
    }
    assert canonical_templates <= routes

    legacy_short_form_templates = {
        "/bff/personas/{id}",
        "/bff/capital-pools/{id}",
        "/bff/deployments/{id}",
        "/bff/rebalances/{id}",
        "/bff/incidents/{id}",
        "/bff/runtimes/{id}",
        "/bff/skills/{id}",
        "/bff/tools/{id}",
        "/bff/strategies/{id}/actions/{actionId}",
        "/bff/mcp-servers/{id}/import-tools",
    }
    assert not legacy_short_form_templates.intersection(routes)


def test_targeted_duplicate_route_registrations_are_removed() -> None:
    expected_singletons: Iterable[tuple[str, str]] = [
        ("POST", "/bff/personas"),
        ("POST", "/bff/strategies"),
        ("POST", "/bff/mcp-servers/{}/import-tools"),
        ("GET", "/bff/management/shell-summary"),
        ("GET", "/api/v1/operator/home"),
        ("GET", "/bff/management/cockpit"),
        ("GET", "/bff/management/trading-pulse"),
        ("GET", "/bff/management/trading-pulse/rankings"),
        ("GET", "/bff/management/sentinel-pulse"),
        ("GET", "/api/v1/operator/health-status"),
        ("GET", "/bff/management/loop-throughput"),
        ("GET", "/bff/management/risk-radar"),
        ("GET", "/bff/management/incident-timeline"),
        ("GET", "/bff/management/human-inbox"),
        ("GET", "/bff/management/human-inbox/{}"),
        ("GET", "/bff/management/hiq-backlog"),
        ("GET", "/bff/management/intervention-stream"),
        ("POST", "/bff/management/nl/ask"),
        ("POST", "/bff/management/nl/ask/stream"),
        ("GET", "/bff/management/evidence"),
        ("GET", "/bff/management/operations-read-model/{}"),
        ("GET", "/api/v1/operator/degraded-control-guidance"),
    ]
    for method, normalized_path in expected_singletons:
        assert _route_count(method, normalized_path) == 1, (method, normalized_path)

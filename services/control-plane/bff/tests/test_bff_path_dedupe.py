from __future__ import annotations

import os
import re
import sys
from typing import Iterable

from fastapi.testclient import TestClient

# RETAINED_COMPOSITION (architecture gap, not a seam-boundary gap): every test
# in this file exists to prove the *fully assembled* Operator BFF app has no
# duplicate/shadowed route registrations across its entire route table. That
# is inherently a whole-app property, not a single router's.
#
# `core.app_factory.build_bff_app()` is documented in its own module
# docstring as "Prepared BFF core composition for the 30-route core
# assignment... The later main-assembly task will inject the existing domain
# handlers", and indeed only wires FastAPI + middleware/CORS/security; it
# mounts zero routers on its own. main.py is still the *only* place in this
# tree that assembles the full app: it calls `app.include_router(...)` ~30
# times (personas, capital, incidents, strategies, jobs, governance,
# deployments, runtimes, skills, tools, mcp-servers, ranking-formulas, agora,
# trade journal/journeys, events, alpha-factory, auth, assistant-management,
# core, settings, ...) after building the base app via `build_bff_app()`.
# No other module in services/control-plane/bff assembles anywhere near the
# full route surface (grepped for `create_app`/`build_app`/`FastAPI(` across
# the whole BFF tree; only main.py and core/app_factory.py construct a
# `FastAPI()` at all, and app_factory's is the partial 30-route core only).
#
# Building a second, parallel "full app assembler" here, outside main.py,
# would either (a) duplicate main.py's ~30-router composition logic in a
# test file (exactly the kind of business-logic duplication this migration
# is meant to avoid), or (b) silently narrow this test's scope to whatever
# subset of routers a test-local composer happens to wire up, which would
# defeat the test's actual purpose: proving the real production app that
# actually serves traffic has zero duplicate paths. Until a real
# `create_app()`/`build_app()` composition root exists outside main.py that
# assembles every router main.py mounts, this file is retained pointed at
# the real composed app via the least-bad remaining import.
from services.control_plane.bff import main as bff_main

OPERATOR_HEADERS = {"Authorization": "Bearer op-path-dedupe:operator,admin"}


def _client() -> TestClient:
    return TestClient(bff_main.app)


def _assert_deprecated(response, replacement: str) -> None:
    assert response.status_code == 410, response.text
    assert response.headers["X-Deprecated"] == "true"
    assert response.headers["X-Deprecated-At"] in {"2026-05-25T08:40:02Z", "2026-06-01"}
    assert response.headers["Deprecation"] == "true"
    assert response.headers["X-Pantheon-Replacement-Route"] == replacement
    body = response.json()
    error_obj = body.get("error") or body.get("detail", {}).get("error", {})
    assert error_obj.get("details", {}).get("replacement") == replacement
    assert body.get("meta", {}).get("deprecation", {}).get("replacement") == replacement


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
            "/bff/v1/commands",
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
        ("/bff/strategies/strategy-1/actions/promote", "/bff/v1/commands"),
        ("/bff/personas/persona-1/actions/promote", "/bff/v1/commands"),
        ("/bff/deployments/deployment-1/actions/promote", "/bff/v1/commands"),
        ("/bff/runtimes/runtime-1/actions/pause", "/bff/v1/commands"),
        ("/bff/skills/skill-1/actions/disable", "/bff/v1/commands"),
        ("/bff/tools/tool-1/actions/disable", "/bff/v1/commands"),
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
    ]
    for method, normalized_path in expected_singletons:
        assert _route_count(method, normalized_path) == 1, (method, normalized_path)

from __future__ import annotations

import os
import re
import sys
from typing import Iterable

from fastapi.testclient import TestClient

# BFF-TEST-MIGRATION-REMAINING-IMPORTERS-001: every test in this file exists
# to prove the *fully assembled* Operator BFF app has no duplicate/shadowed
# route registrations across its entire route table -- inherently a
# whole-app property, not a single router's. `core.app_factory.compose_bff_app()`
# (BFF-MAIN-FINAL-SEAMS-CORRECTIVE-001) is now the real, standalone full-app
# composition root: main.py itself calls nothing but
# `compose_bff_app(app_deps=..., ...)` to build its own `app` and no longer
# calls `include_router` at all, and `test_compose_bff_app_matches_main_route_set`
# (tests/test_main_composition_seam_extraction_003.py) proves the standalone
# composer's route set is byte-identical to main.py's. This file therefore
# builds its own app via `compose_bff_app()` instead of importing main.py.
#
# `mount_bff_routers`'s `_dep` helper (`core/app_factory.py`) resolves
# `_deprecated_bff_path_response` from an explicit keyword argument before
# ever falling back to a loaded `main` module or the inert stub in
# `_resolve_default_dependency`. The real, already-extracted production owner
# of that response (`personas.service._deprecated_bff_path_response`) is
# passed directly here instead, so this file needs no main.py reference at
# all -- dynamic or static.
from services.control_plane.bff.core.app_factory import compose_bff_app
from services.control_plane.bff.personas.service import (
    _deprecated_bff_path_response,
)

OPERATOR_HEADERS = {"Authorization": "Bearer op-path-dedupe:operator,admin"}

_APP = compose_bff_app(_deprecated_bff_path_response=_deprecated_bff_path_response)


def _client() -> TestClient:
    return TestClient(_APP)


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
    for route in _iter_all_routes(_APP.routes):
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
    for route in _iter_all_routes(_APP.routes):
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

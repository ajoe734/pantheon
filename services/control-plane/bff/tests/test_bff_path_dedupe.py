from __future__ import annotations

import os
import re
import sys
from typing import Iterable
from unittest.mock import Mock

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
from services.control_plane.bff.tests.bff_compose_stand_ins import resolve_with_stand_ins
from services.control_plane.bff.core.app_factory import compose_bff_app

OPERATOR_HEADERS = {"Authorization": "Bearer op-path-dedupe:operator,admin"}

_APP = compose_bff_app(dependency_resolver=resolve_with_stand_ins)


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

    # The notification-only /bff/mcp/servers GET and /bff/mcp/tools action
    # routes were removed in BFF-OLD-NOTIFICATION-RETIRE-20261111.
    for method, path in (
        ("get", "/bff/mcp/servers"),
        ("get", "/bff/mcp/servers/server-1"),
        ("post", "/bff/mcp/tools/tool-1/actions/enable"),
    ):
        response = getattr(client, method)(path, headers=OPERATOR_HEADERS)
        assert response.status_code in {404, 405}, (path, response.text)

    # The four immediate /bff/ranking/formulas* deprecation notification
    # wrappers were removed in BFF-FINAL-COMPAT-20261107 after execute-plans
    # migrated exclusively to /bff/ranking-formulas*.
    for method, path in (
        ("get", "/bff/ranking/formulas"),
        ("post", "/bff/ranking/formulas"),
        ("get", "/bff/ranking/formulas/formula-1"),
        ("patch", "/bff/ranking/formulas/formula-1"),
    ):
        response = getattr(client, method)(path, headers=OPERATOR_HEADERS)
        assert response.status_code in {404, 405}, response.text

    # The nested ranking promote family is not a deprecated-path alias: the
    # RankingFormulaAction command has no executor and fails before admission
    # with ACTION_RETIRED (410) once.
    retired = client.post(
        "/bff/ranking/formulas/formula-1/actions/promote", headers=OPERATOR_HEADERS
    )
    assert retired.status_code == 410, retired.text
    error = retired.json()["error"]
    assert error["code"] == "ACTION_RETIRED"
    assert error["details"]["replacement"]


def test_notification_only_nested_action_families_are_removed(monkeypatch) -> None:
    client = _client()
    admission = Mock(spec=["submit_command_admission"])
    monkeypatch.setattr(_APP.state, "command_adapter_service", admission)

    for path in (
        "/bff/strategies/strategy-1/actions/promote",
        "/bff/deployments/deployment-1/actions/promote",
        "/bff/runtimes/runtime-1/actions/pause",
        "/bff/skills/skill-1/actions/disable",
        "/bff/tools/tool-1/actions/disable",
    ):
        response = client.post(path, headers=OPERATOR_HEADERS)
        assert response.status_code in {404, 405}, (path, response.text)

    # Persona lifecycle aliases are live; other persona actions are unknown (404).
    resp = client.post("/bff/personas/persona-1/actions/archive", headers=OPERATOR_HEADERS)
    assert resp.status_code == 404, resp.text
    error = resp.json()["error"]
    assert error["code"] == "RESOURCE_NOT_FOUND"
    assert "replacement" not in (error.get("details") or {})
    assert "deprecation" not in resp.json().get("meta", {})
    for header in ("X-Deprecated", "X-Deprecated-At", "Deprecation", "Sunset", "X-Pantheon-Replacement-Route"):
        assert header not in resp.headers
    assert admission.mock_calls == []


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

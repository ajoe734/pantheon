"""Ownership checks for the dedicated Runtime BFF router."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BFF_ROOT = Path(__file__).resolve().parents[1]

from services.control_plane.bff.runtime.router import create_runtime_router


class _BindingsStore:
    def __init__(self, binding_id: str) -> None:
        self._binding_id = binding_id

    def list_bindings(self, **_filters: object) -> list[dict[str, str]]:
        return [{"binding_id": self._binding_id}]


def test_runtime_router_owns_all_runtime_routes() -> None:
    router = create_runtime_router()

    expected = {
        ("GET", "/api/v1/bindings"),
        ("POST", "/api/v1/bindings"),
        ("GET", "/api/v1/runtime-bindings"),
        ("GET", "/api/v1/runtimes/{runtime_id}/status"),
        ("GET", "/api/v1/bindings/{binding_id}"),
        ("GET", "/api/v1/runtime-bindings/{binding_id}"),
        ("GET", "/api/v1/runtimes/{runtime_id}/rollbacks"),
        ("GET", "/api/v1/operator/runtime-state"),
        ("GET", "/api/v1/operator/paper-live-drift/{runtime_id}"),
        ("GET", "/bff/runtimes/{runtime_id}/ooda"),
        ("GET", "/api/v1/runtime/{runtime_id}/events/stream"),
        ("POST", "/bff/runtimes"),
        ("GET", "/bff/runtimes"),
        ("GET", "/bff/runtimes/{runtime_id}"),
        ("POST", "/bff/runtimes/{runtime_id}/actions/{action_id}"),
        ("GET", "/bff/v5/execution/persona-health"),
        ("GET", "/bff/v5/execution/strategy-health"),
    }
    actual = {(method, route.path) for route in router.routes for method in route.methods if method in {"GET", "POST"}}
    assert actual == expected


def test_main_composes_runtime_router_without_runtime_decorators() -> None:
    main_source = (BFF_ROOT / "main.py").read_text(encoding="utf-8")
    assert "from runtime.router import create_runtime_router" in main_source
    assert "app.routes.extend(_runtime_router.routes)" in main_source

    for _method, path in {
        ("GET", "/api/v1/bindings"),
        ("GET", "/api/v1/runtime-bindings"),
        ("GET", "/api/v1/runtimes/{runtime_id}/status"),
        ("GET", "/bff/runtimes"),
        ("GET", "/bff/v5/execution/persona-health"),
    }:
        escaped_path = re.escape(path)
        assert not re.search(
            rf'@app\.(?:get|post|put|patch|delete)\(\s*["\']{escaped_path}["\']',
            main_source,
        )


def test_runtime_router_resolves_the_bff_read_store_per_request() -> None:
    current_store = {"value": _BindingsStore("binding-first")}
    router = create_runtime_router(
        get_read_store=lambda: current_store["value"],
        dependencies={
            "_extract_identity": lambda _authorization: object(),
            "_require_read_role": lambda _identity: None,
            "_read_surface_meta": lambda *_args, **_kwargs: {},
            "utc_now": lambda: "2026-08-30T00:00:00Z",
        },
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/api/v1/bindings").json()["data"] == [
        {"binding_id": "binding-first"}
    ]

    current_store["value"] = _BindingsStore("binding-second")
    assert client.get("/api/v1/bindings").json()["data"] == [
        {"binding_id": "binding-second"}
    ]


def test_runtime_action_route_injects_request_without_a_query_parameter() -> None:
    router = create_runtime_router(
        dependencies={
            "_deprecated_bff_path_response": lambda **kwargs: {"deprecated": kwargs},
        },
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post("/bff/runtimes/runtime-1/actions/pause")

    assert response.status_code == 202
    assert response.json() == {
        "deprecated": {
            "route": "/bff/runtimes/{runtime_id}/actions/{action_id}",
            "replacement": "/bff/actions/runtime/{runtime_id}/{action_id}",
        }
    }

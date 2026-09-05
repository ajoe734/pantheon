from __future__ import annotations

import os
import sys
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.runtime.router import create_runtime_router


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt004_binding_list_route_honors_persona_id_filter() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "bindings": [
                    {
                        "id": "binding-042",
                        "binding_id": "binding-042",
                        "persona_id": "persona-alpha",
                        "capital_pool_id": "pool-main",
                        "role": "primary",
                        "validity": "active",
                    }
                ]
            }
        )
        router = create_runtime_router(
            read_surface=store,
            dependencies={
                "_extract_identity": lambda auth: OperatorIdentity(operator_id="op-2", roles=["operator"]),
                "_require_read_role": lambda identity: None,
                "utc_now": lambda: "2026-09-05T00:00:00Z",
                "_read_surface_meta": lambda surface, method, **kw: {"surface": surface, "method": method, **kw},
            },
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        headers = {"Authorization": OPERATOR_TOKEN}

        bindings = client.get(
            "/api/v1/bindings?persona_id=persona-alpha&capital_pool_id=pool-main&validity=active",
            headers=headers,
        )
        assert bindings.status_code == 200, bindings.text
        payload = bindings.json()
        assert payload["meta"]["total"] == 1
        assert payload["data"][0]["id"] == "binding-042"
        assert payload["data"][0]["persona_id"] == "persona-alpha"
        assert payload["data"][0]["validity"] == "active"

        no_bindings = client.get(
            "/api/v1/bindings?persona_id=persona-does-not-exist",
            headers=headers,
        )
        assert no_bindings.status_code == 200, no_bindings.text
        assert no_bindings.json()["meta"]["total"] == 0


def test_pkt004_binding_list_store_honors_persona_id_filter_before_returning_rows() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "bindings": [
                    {
                        "id": "binding-001",
                        "binding_id": "binding-001",
                    "persona_id": "persona-alpha",
                    "capital_pool_id": "pool-main",
                    "role": "primary",
                    "validity": "active",
                    "status": "active",
                    "allowed_deployment_scope": "paper",
                },
                    {
                        "id": "binding-002",
                        "binding_id": "binding-002",
                    "persona_id": "persona-beta",
                    "capital_pool_id": "pool-main",
                    "role": "backup",
                    "validity": "expired",
                    "status": "inactive",
                    "allowed_deployment_scope": "paper",
                },
                ]
            }
        )

        bindings = store.list_bindings(persona_id="persona-alpha", validity="active")
        assert [binding["id"] for binding in bindings] == ["binding-001"]
        assert bindings[0]["persona_id"] == "persona-alpha"
        assert bindings[0]["validity"] == "active"

        no_bindings = store.list_bindings(persona_id="persona-does-not-exist")
        assert no_bindings == []

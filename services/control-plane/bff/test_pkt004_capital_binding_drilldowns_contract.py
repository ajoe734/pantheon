from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt004_binding_list_route_honors_persona_id_filter() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
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
        finally:
            bff_main.read_store = original_store


def test_pkt004_binding_list_store_honors_persona_id_filter_before_returning_rows() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        store._canonical.list_records = lambda dataset: (
            True,
            [
                {
                    "binding_id": "binding-001",
                    "persona_id": "persona-alpha",
                    "capital_pool_id": "pool-main",
                    "role": "primary",
                    "validity": "active",
                    "status": "active",
                    "allowed_deployment_scope": "paper",
                },
                {
                    "binding_id": "binding-002",
                    "persona_id": "persona-beta",
                    "capital_pool_id": "pool-main",
                    "role": "backup",
                    "validity": "expired",
                    "status": "inactive",
                    "allowed_deployment_scope": "paper",
                },
            ],
        ) if dataset == "persona_bindings" else (False, [])

        bindings = store.list_bindings(persona_id="persona-alpha", validity="active")
        assert [binding["id"] for binding in bindings] == ["binding-001"]
        assert bindings[0]["persona_id"] == "persona-alpha"
        assert bindings[0]["validity"] == "active"

        no_bindings = store.list_bindings(persona_id="persona-does-not-exist")
        assert no_bindings == []

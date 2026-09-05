"""Two-instance real-router data and metadata isolation regression test.

Owned by BFF-ROUTER-STRUCT-001 within personas/** domain.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.personas.service import create_persona_registry_write_owner
from services.control_plane.bff.command_queue import CommandStore

AUTH_HEADERS = {"Authorization": "Bearer test-operator:operator,reviewer,admin"}


class FakeRankingWriteOwner:
    def __init__(self):
        self.snapshots = {}

    def put_ranking_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        sid = snapshot.get("snapshot_id") or "snap-1"
        self.snapshots[sid] = snapshot
        return {"status": "created", "snapshot_id": sid, "snapshot": snapshot}

    def get_ranking_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self.snapshots.get(snapshot_id)

    def list_ranking_snapshots(self) -> List[Dict[str, Any]]:
        return list(self.snapshots.values())


@pytest.fixture(autouse=True)
def enable_auth_stub(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


def test_persona_router_two_instance_isolation(tmp_path):
    """Verify two PersonaService and router instances remain strictly isolated in data and metadata.

    In the legacy implementation, instantiating a second PersonaService mutated
    module-level globals in personas/service.py, contaminating the metadata of
    requests made to the first router instance (changing status from ok/typed_store
    to unavailable/missing while data remained from the first instance).
    This test verifies that:
    1. Client 1 served from a seeded store returns data and status=ok, source=typed_store.
    2. Client 2 served from an unseeded/missing store returns status=unavailable, source=missing.
    3. Re-querying Client 1 after Client 2 yields clean, uncontaminated status=ok and first-instance data.
    """
    fake_persona = {"id": "persona-alpha", "name": "Persona Alpha", "lifecycle_state": "active"}

    class SeededStore:
        def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return [dict(fake_persona)]

        def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
            if persona_id == "persona-alpha":
                return dict(fake_persona)
            return None

        def dataset_source(self, dataset: str) -> str:
            return "typed_store"

    write_owner_1 = create_persona_registry_write_owner()
    read_store_1 = SeededStore()
    command_store_1 = CommandStore(str(tmp_path / "commands_1.jsonl"))
    service_1 = PersonaService(
        write_owner=write_owner_1,
        ranking_write_owner=FakeRankingWriteOwner(),
        read_store=read_store_1,
        command_store=command_store_1,
    )
    router_1 = create_personas_router(service=service_1)
    app_1 = FastAPI(title="Instance 1")
    app_1.include_router(router_1)
    client_1 = TestClient(app_1)

    # Initial query on Client 1
    resp_1_init = client_1.get("/api/v1/personas", headers=AUTH_HEADERS)
    assert resp_1_init.status_code == 200
    body_1_init = resp_1_init.json()
    assert body_1_init["data"] == [fake_persona]
    meta_1_init = body_1_init["meta"]["surfaces"]["persona_list"]
    assert meta_1_init["status"] == "ok"
    assert meta_1_init["source"] == "typed_store"

    # Instance 2: Unseeded with missing store
    class MissingStore:
        def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
            return []

        def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
            return None

        def dataset_source(self, dataset: str) -> str:
            return "missing"

    write_owner_2 = create_persona_registry_write_owner()
    read_store_2 = MissingStore()
    command_store_2 = CommandStore(str(tmp_path / "commands_2.jsonl"))
    service_2 = PersonaService(
        write_owner=write_owner_2,
        ranking_write_owner=FakeRankingWriteOwner(),
        read_store=read_store_2,
        command_store=command_store_2,
    )
    router_2 = create_personas_router(service=service_2)
    app_2 = FastAPI(title="Instance 2")
    app_2.include_router(router_2)
    client_2 = TestClient(app_2)

    # Query Client 2
    resp_2 = client_2.get("/api/v1/personas", headers=AUTH_HEADERS)
    assert resp_2.status_code == 200
    body_2 = resp_2.json()
    assert body_2["data"] == []
    meta_2 = body_2["meta"]["surfaces"]["persona_list"]
    assert meta_2["status"] == "unavailable"
    assert meta_2["source"] == "missing"

    # Query Client 1 again: metadata and data must remain completely unaffected by Instance 2
    resp_1_after = client_1.get("/api/v1/personas", headers=AUTH_HEADERS)
    assert resp_1_after.status_code == 200
    body_1_after = resp_1_after.json()
    assert body_1_after["data"] == [fake_persona]
    meta_1_after = body_1_after["meta"]["surfaces"]["persona_list"]
    assert meta_1_after["status"] == "ok", (
        f"Cross-instance contamination detected! Client 1 status changed to {meta_1_after['status']}"
    )
    assert meta_1_after["source"] == "typed_store", (
        f"Cross-instance contamination detected! Client 1 source changed to {meta_1_after['source']}"
    )


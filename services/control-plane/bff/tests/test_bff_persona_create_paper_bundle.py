from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _schedule_receipt,
)

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


@pytest.fixture(autouse=True)
def _isolate_persona_create_service_clients(monkeypatch):
    """Keep this BFF contract test local after persona creation moved its
    subresource writes to the canonical Capital, Deployment, and Runtime
    services."""
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-persona-provisioner")
    transport = FakeOwnerTransport()
    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(personas_service, "_PersonaOwnerHttpTransport", lambda *a, **kw: transport)
    monkeypatch.setattr(personas_service, "_register_persona_cron_required", _schedule_receipt)

    def _missing_deployment_plan(*_args, **_kwargs):
        raise RuntimeError("deployment plan not found")

    monkeypatch.setattr(personas_service, "_get_json", _missing_deployment_plan, raising=False)
    monkeypatch.setattr(personas_service, "_post_json", lambda *_args, **_kwargs: {"status": "created"}, raising=False)

    class _RuntimeManagerClient:
        def get(self, _binding_id):
            return None

        def deploy(self, request):
            return {"runtime_id": request["runtime_id"], "status": "accepted"}

        def list_all(self):
            return []

    monkeypatch.setattr(personas_service, "_runtime_manager_client", _RuntimeManagerClient, raising=False)
    try:
        from services.persona.runtime_profile import build_persona_runtime_profile
        monkeypatch.setattr(personas_service, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    except ImportError:
        monkeypatch.setattr(personas_service, "build_persona_runtime_profile", lambda *a, **kw: type("Profile", (), {"to_dict": lambda s: {}})(), raising=False)


_SHARED_PERSONAS: dict[str, dict[str, Any]] = {}


def _make_persona_test_store():
    store = create_in_memory_read_surface_ports()

    def _create_p(**kwargs):
        pid = kwargs.get("persona_id") or kwargs.get("id")
        name = kwargs.get("name") or pid
        archetype = kwargs.get("archetype") or "generalist"
        meta = dict(kwargs.get("metadata") or {})
        meta.setdefault("archetype", archetype)
        rec = {
            "id": pid,
            "persona_id": pid,
            "name": name,
            "archetype": archetype,
            "state": kwargs.get("state") or kwargs.get("lifecycle_state") or "active",
            "lifecycle_state": kwargs.get("lifecycle_state") or kwargs.get("state") or "active",
            **kwargs,
            "metadata": meta,
        }
        _SHARED_PERSONAS[pid] = rec
        return rec

    def _get_p(pid):
        return _SHARED_PERSONAS.get(pid)

    def _list_p(**kwargs):
        return list(_SHARED_PERSONAS.values())

    def _update_p(pid, **kwargs):
        if pid in _SHARED_PERSONAS:
            if "metadata" in kwargs:
                meta = dict(_SHARED_PERSONAS[pid].get("metadata") or {})
                meta.update(kwargs["metadata"])
                kwargs["metadata"] = meta
            _SHARED_PERSONAS[pid].update(kwargs)
            return _SHARED_PERSONAS[pid]
        return None

    store.create_persona = _create_p
    store.get_persona = _get_p
    store.list_personas = _list_p
    store.update_persona = _update_p
    return store


def _fresh_client(td: str) -> tuple[TestClient, Any]:
    _SHARED_PERSONAS.clear()
    store = _make_persona_test_store()
    cmd_store = CommandStore(os.path.join(td, "commands.jsonl"))
    if hasattr(personas_service, "_PERSONA_PROVISIONING_STORE"):
        prov_store = personas_service._PERSONA_PROVISIONING_STORE
        if prov_store is not None:
            getattr(prov_store, "_records", {}).clear()
            getattr(prov_store, "_leases", {}).clear()
    service = PersonaService(
        read_store=store,
        write_owner=store,
        ranking_write_owner=store,
        command_store=cmd_store,
    )
    app = FastAPI()
    app.include_router(create_personas_router(service=service))
    return TestClient(app), store


def test_bff_management_create_paper_bundle_validation() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _ = _fresh_client(td)

        # Missing Idempotency-Key header is rejected or name is missing
        missing_name = client.post(
            "/bff/management/personas/create-paper-bundle",
            json={},
            headers={**HEADERS, "Idempotency-Key": "bundle-001"},
        )
        assert missing_name.status_code == 422, missing_name.text

        # Empty name is rejected
        empty_name = client.post(
            "/bff/management/personas/create-paper-bundle",
            json={"name": "  "},
            headers={**HEADERS, "Idempotency-Key": "bundle-002"},
        )
        assert empty_name.status_code == 422, empty_name.text


def test_bff_management_create_paper_bundle_success() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, store = _fresh_client(td)

        payload = {
            "name": "Alpha Trader",
            "archetype": "mean_reversion",
            "risk": "low",
            "mandate": "Trade TW equities using daily pricing",
            "market": "TW",
        }

        resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**HEADERS, "Idempotency-Key": "bundle-create-123"},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body

        data = body["data"]
        meta = body["meta"]
        persona_id = data["id"]

        # Acceptance verification
        assert data["state"] == "provisioning"
        assert data["capitalMode"] == "paper"
        assert data["deploymentStage"] == "paper"
        assert data["paperLedgerId"].startswith("paper-ledger-")
        assert "runtimeId" not in data
        assert "runtimeBindingId" not in data
        assert "capitalPoolId" not in data

        assert meta["create_flow"] == "durable_owner_coordinated_provisioning"
        assert meta["runtime_id"] is None
        assert meta["runtime_binding_id"] is None
        assert meta["live_capital_side_effects"] is False
        assert meta["human_review_required_for_live"] is True

        # Idempotency check with the same key
        dup_resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**HEADERS, "Idempotency-Key": "bundle-create-123"},
        )
        assert dup_resp.status_code == 201
        assert dup_resp.json()["data"]["id"] == persona_id

        # Query the created persona detail to verify data sources and bindings
        detail_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
        assert detail_resp.status_code == 200, detail_resp.text
        detail = detail_resp.json()["data"]

        assert detail["state"] == "provisioning"
        assert detail["mandate"] == "Trade TW equities using daily pricing"
        assert detail["archetype"] == "mean_reversion"

        # Check TW required data sources are set correctly
        assert "sourceHealthBindings" in detail or "required_data_sources" in store.get_persona(persona_id)
        persona_raw = store.get_persona(persona_id)
        assert persona_raw is not None
        assert len(persona_raw.get("required_data_sources", [])) > 0

        # Ensure paper ledger is isolated
        ledger = persona_raw["metadata"].get("paper_ledger")
        assert ledger is not None
        assert ledger["persona_id"] == persona_id
        assert ledger["is_isolated"] is True

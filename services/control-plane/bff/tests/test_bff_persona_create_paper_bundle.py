from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


@pytest.fixture(autouse=True)
def _isolate_persona_create_service_clients(monkeypatch):
    """Keep this BFF contract test local after persona creation moved its
    subresource writes to the canonical Capital, Deployment, and Runtime
    services."""

    transport = FakeOwnerTransport()
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(bff_main, "_PersonaOwnerHttpTransport", lambda: transport)
    monkeypatch.setattr(bff_main, "_register_persona_cron_required", _schedule_receipt)

    def _missing_deployment_plan(*_args, **_kwargs):
        raise RuntimeError("deployment plan not found")

    monkeypatch.setattr(bff_main, "_get_json", _missing_deployment_plan)
    monkeypatch.setattr(bff_main, "_post_json", lambda *_args, **_kwargs: {"status": "created"})

    class _RuntimeManagerClient:
        def get(self, _binding_id):
            return None

        def deploy(self, request):
            return {"runtime_id": request["runtime_id"], "status": "accepted"}

        def list_all(self):
            return []

    monkeypatch.setattr(bff_main, "_runtime_manager_client", _RuntimeManagerClient)
    try:
        from services.persona.runtime_profile import build_persona_runtime_profile
        monkeypatch.setattr(bff_main, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    except ImportError:
        monkeypatch.setattr(bff_main, "build_persona_runtime_profile", lambda *a, **kw: type("Profile", (), {"to_dict": lambda s: {}})(), raising=False)


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


def _fresh_client(td: str) -> TestClient:
    _SHARED_PERSONAS.clear()
    bff_main.read_store = _make_persona_test_store()
    bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    bff_main._COMMAND_AUTH_CONTEXT.clear()
    if hasattr(bff_main, "_PERSONA_PROVISIONING_STORE"):
        getattr(bff_main._PERSONA_PROVISIONING_STORE, "_records", {}).clear()
        getattr(bff_main._PERSONA_PROVISIONING_STORE, "_leases", {}).clear()
    return TestClient(bff_main.app)


def test_bff_management_create_paper_bundle_validation() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)

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
        finally:
            bff_main.read_store = original


def test_bff_management_create_paper_bundle_success() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)

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
            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main.read_store = _make_persona_test_store()

            detail_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert detail_resp.status_code == 200, detail_resp.text
            detail = detail_resp.json()["data"]

            assert detail["state"] == "provisioning"
            assert detail["mandate"] == "Trade TW equities using daily pricing"
            assert detail["archetype"] == "mean_reversion"

            # Check TW required data sources are set correctly
            assert "sourceHealthBindings" in detail or "required_data_sources" in bff_main.read_store.get_persona(persona_id)
            persona_raw = bff_main.read_store.get_persona(persona_id)
            assert persona_raw is not None
            assert len(persona_raw.get("required_data_sources", [])) > 0

            # Ensure paper ledger is isolated
            ledger = persona_raw["metadata"].get("paper_ledger")
            assert ledger is not None
            assert ledger["persona_id"] == persona_id
            assert ledger["is_isolated"] is True

        finally:
            bff_main.read_store = original

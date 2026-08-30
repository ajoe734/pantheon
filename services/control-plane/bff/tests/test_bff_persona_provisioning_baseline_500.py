"""Deterministic tests for dev paper baseline provisioning.

Covers:
1. Reproduction proof: build_persona_runtime_profile imported and ReadSurfacePorts has create_persona/update_persona.
2. Dev paper baseline provisioning success (201 Created, provisioning/paper_running, succeeded).
3. Idempotent retry with exact same Idempotency-Key returns 201 with identical results.
4. Idempotency conflict with modified payload returns 409 IDEMPOTENCY_CONFLICT.
5. Downstream owner failure returns typed 502 UPSTREAM_ERROR with actionable diagnostics (not generic 500).
6. Authentication and RBAC enforcement (401 on missing auth, 403 on non-operator).
7. Detail and list readback consistency (/bff/personas/{id} and /bff/personas).
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from persona_provisioning import MemoryPersonaProvisioningStore
from ports import create_in_memory_read_surface_ports, create_read_surface_ports
from test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt

OPERATOR_TOKEN = "Bearer op-2:operator"
VIEWER_TOKEN = "Bearer viewer-1:viewer"
OPERATOR_HEADERS = {"Authorization": OPERATOR_TOKEN}
VIEWER_HEADERS = {"Authorization": VIEWER_TOKEN}


@pytest.fixture(autouse=True)
def _isolate_test_environment(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")


def _setup_mock_services(monkeypatch, transport=None):
    if transport is None:
        transport = FakeOwnerTransport()
    store = MemoryPersonaProvisioningStore()
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", store)
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
    return transport, store


def test_reproduction_imports_and_ports_contract():
    """Verify that build_persona_runtime_profile is imported and ReadSurfacePorts has persona write methods."""
    assert hasattr(bff_main, "build_persona_runtime_profile"), "build_persona_runtime_profile must be imported in main"
    assert callable(bff_main.build_persona_runtime_profile), "build_persona_runtime_profile must be callable"

    # Default production ReadSurfacePorts factory
    prod_ports = create_read_surface_ports()
    assert hasattr(prod_ports, "create_persona"), "ReadSurfacePorts must have create_persona"
    assert hasattr(prod_ports, "update_persona"), "ReadSurfacePorts must have update_persona"
    assert hasattr(prod_ports, "get_persona"), "ReadSurfacePorts must have get_persona"
    assert hasattr(prod_ports, "list_personas"), "ReadSurfacePorts must have list_personas"

    # In-memory test ReadSurfacePorts factory
    in_mem_ports = create_in_memory_read_surface_ports()
    assert hasattr(in_mem_ports, "create_persona"), "in-memory ReadSurfacePorts must have create_persona"
    assert hasattr(in_mem_ports, "update_persona"), "in-memory ReadSurfacePorts must have update_persona"

    # Verify native in-memory persona store operations
    created = in_mem_ports.create_persona(persona_id="p-test-1", name="Test Persona", archetype="momentum")
    assert created["id"] == "p-test-1"
    assert created["name"] == "Test Persona"
    found = in_mem_ports.get_persona("p-test-1")
    assert found is not None
    assert found["persona_id"] == "p-test-1"
    updated = in_mem_ports.update_persona("p-test-1", lifecycle_state="paper_running")
    assert updated is not None
    assert updated["lifecycle_state"] == "paper_running"


def test_dev_paper_baseline_provisioning_success(monkeypatch):
    """Verify clean bootstrap of dev paper baseline returning 201 Created and paper metadata."""
    _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        idempotency_key = "dev-paper-bootstrap-20260830-op-a-v1"
        payload = {
            "name": "Pantheon Dev Paper Baseline",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Paper-only lifecycle verification in dev",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }

        resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json().get("data", {})
        meta = resp.json().get("meta", {})

        assert data.get("name") == "Pantheon Dev Paper Baseline"
        assert data.get("state") in {"provisioning", "paper_running"}
        assert data.get("capitalMode") == "paper"
        assert data.get("deploymentStage") == "paper"
        assert data.get("paperLedgerId", "").startswith("paper-ledger-")

        assert meta.get("create_flow") == "durable_owner_coordinated_provisioning"
        assert meta.get("capital_mode") == "paper"
        assert meta.get("deployment_plan_id") is not None
        assert meta.get("persona_capital_binding_id") is not None
        assert meta.get("live_capital_side_effects") is False
        assert meta.get("human_review_required_for_live") is True


def test_dev_paper_baseline_provisioning_idempotent_retry(monkeypatch):
    """Verify that retrying with the same Idempotency-Key is deterministic and returns identical result."""
    _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        idempotency_key = "dev-paper-bootstrap-20260830-idempotent-retry-v1"
        payload = {
            "name": "Pantheon Dev Paper Baseline Retry",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Paper-only lifecycle verification in dev",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }

        resp1 = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert resp2.status_code == 201
        assert resp1.json()["data"]["id"] == resp2.json()["data"]["id"]
        assert resp1.json()["meta"]["deployment_plan_id"] == resp2.json()["meta"]["deployment_plan_id"]
        assert resp1.json()["meta"]["persona_capital_binding_id"] == resp2.json()["meta"]["persona_capital_binding_id"]


def test_dev_paper_baseline_provisioning_idempotency_conflict(monkeypatch):
    """Verify that using the same Idempotency-Key with different payload returns 409 conflict."""
    _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        idempotency_key = "dev-paper-bootstrap-conflict-key-v1"
        payload1 = {
            "name": "Pantheon Dev Paper Baseline Initial",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Initial mandate",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }
        payload2 = {
            "name": "Pantheon Dev Paper Baseline Conflict",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Conflicting different mandate",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }

        resp1 = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload1,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload2,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert resp2.status_code == 409
        err = resp2.json().get("error", {})
        assert err.get("code") == "IDEMPOTENCY_CONFLICT"
        assert err.get("details", {}).get("precondition_failed") in {
            "idempotency_conflict",
            "idempotency_or_tenant_name",
        }


def test_dev_paper_baseline_downstream_failure_returns_typed_502(monkeypatch):
    """Verify that downstream coordinator failure returns typed 502 UPSTREAM_ERROR, not generic 500."""
    failing_transport = FakeOwnerTransport(mutation_failure={"/api/capital-pools"})
    _setup_mock_services(monkeypatch, transport=failing_transport)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app, raise_server_exceptions=False)
        resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json={
                "name": "Pantheon Dev Paper Baseline Failing",
                "archetype": "momentum",
                "risk": "low",
                "mandate": "Testing downstream failure",
                "market": "US",
                "strategy_family": "dev_paper_baseline",
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "failing-transport-test-key"},
        )

        assert resp.status_code == 502, f"Expected 502 Bad Gateway, got {resp.status_code}: {resp.text}"
        err = resp.json().get("error", {})
        assert err.get("code") == "UPSTREAM_ERROR"
        assert err.get("details") is not None
        assert "owner rejected" in err["details"]["reason"] or "capital_pool" in str(err["details"])
        assert err["details"]["provisioningState"] in {"failed", "compensated"}


def test_dev_paper_baseline_unauthenticated_and_rbac(monkeypatch):
    """Verify authentication and authorization gates on persona bundle creation."""
    _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(bff_main.app)

        payload = {
            "name": "Pantheon Dev Paper Baseline Auth Test",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Testing auth",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }

        # 1. Unauthenticated
        resp_unauth = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={"Idempotency-Key": "auth-test-key-1"},
        )
        assert resp_unauth.status_code == 401

        # 2. Insufficient role (viewer cannot create personas)
        resp_viewer = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**VIEWER_HEADERS, "Idempotency-Key": "auth-test-key-2"},
        )
        assert resp_viewer.status_code == 403


def test_dev_paper_baseline_readback_consistency(monkeypatch):
    """Verify detail and list readback of provisioned persona."""
    _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        payload = {
            "name": "Pantheon Dev Paper Baseline Readback",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Testing readback consistency",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }

        create_resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "readback-test-key-v1"},
        )
        assert create_resp.status_code == 201
        persona_id = create_resp.json()["data"]["id"]

        # Detail readback
        detail_resp = client.get(
            f"/bff/personas/{persona_id}",
            headers=OPERATOR_HEADERS,
        )
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()["data"]
        assert detail_data["id"] == persona_id
        assert detail_data["name"] == "Pantheon Dev Paper Baseline Readback"
        assert detail_data["state"] in {"provisioning", "paper_running"}

        # List readback
        list_resp = client.get(
            "/bff/personas",
            headers=OPERATOR_HEADERS,
        )
        assert list_resp.status_code == 200
        list_data = list_resp.json()["data"]
        found = [p for p in list_data if p.get("id") == persona_id or p.get("persona_id") == persona_id]
        assert len(found) >= 1

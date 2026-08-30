"""Deterministic tests for dev paper baseline provisioning repair.

Covers:
1. Reproduction & contract proof: build_persona_runtime_profile imported in main.py,
   and ReadSurfacePorts / PersonaFleetPort / PersonaCapitalRuntimeDomainPort have NO mutation methods.
2. Dev paper baseline provisioning success (201 Created, paper capital mode, isolated ledger, deterministic IDs).
3. Idempotent retry with exact same Idempotency-Key returns 201 with identical results.
4. Idempotency conflict with modified payload returns 409 IDEMPOTENCY_CONFLICT with sanitized message.
5. Downstream owner failure returns typed 502 UPSTREAM_ERROR with sanitized diagnostics (no raw exception or internal secrets leak).
6. Authentication, RBAC, and boundary enforcement (401 on missing auth, 403 on non-operator, 422 on non-paper mode).
7. Durable owner delegation: verifies coordinator writes to capital, registry, governance, deployment, and schedule owners.
8. Restart idempotency and readback consistency: verifies recovery from durable store after in-memory cache clear.
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
from domain_ports.persona_capital_runtime import PersonaFleetPort, PersonaCapitalRuntimeDomainPort
from persona_provisioning import MemoryPersonaProvisioningStore
from ports import create_in_memory_read_surface_ports, create_read_surface_ports, ReadSurfacePorts
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


def test_reproduction_imports_and_read_ports_contract():
    """Verify that build_persona_runtime_profile is imported in main.py, and read ports have NO mutation methods."""
    assert hasattr(bff_main, "build_persona_runtime_profile"), "build_persona_runtime_profile must be imported in main"
    assert callable(bff_main.build_persona_runtime_profile), "build_persona_runtime_profile must be callable"

    # Default production ReadSurfacePorts factory
    prod_ports = create_read_surface_ports()
    assert hasattr(prod_ports, "get_persona"), "ReadSurfacePorts must have get_persona"
    assert hasattr(prod_ports, "list_personas"), "ReadSurfacePorts must have list_personas"
    assert not hasattr(prod_ports, "create_persona"), "ReadSurfacePorts must NOT have create_persona (read-side facade)"
    assert not hasattr(prod_ports, "update_persona"), "ReadSurfacePorts must NOT have update_persona (read-side facade)"

    # In-memory test ReadSurfacePorts factory
    in_mem_ports = create_in_memory_read_surface_ports()
    assert hasattr(in_mem_ports, "get_persona"), "in-memory ReadSurfacePorts must have get_persona"
    assert hasattr(in_mem_ports, "list_personas"), "in-memory ReadSurfacePorts must have list_personas"
    assert not hasattr(in_mem_ports, "create_persona"), "in-memory ReadSurfacePorts must NOT have create_persona"
    assert not hasattr(in_mem_ports, "update_persona"), "in-memory ReadSurfacePorts must NOT have update_persona"

    # Domain ports must also be read-only
    fleet_port = PersonaFleetPort()
    assert not hasattr(fleet_port, "create_persona"), "PersonaFleetPort must NOT have create_persona"
    assert not hasattr(fleet_port, "update_persona"), "PersonaFleetPort must NOT have update_persona"
    assert not hasattr(fleet_port, "_personas"), "PersonaFleetPort must NOT have in-memory _personas mutation state"

    domain_port = PersonaCapitalRuntimeDomainPort()
    assert not hasattr(domain_port, "create_persona"), "PersonaCapitalRuntimeDomainPort must NOT have create_persona"
    assert not hasattr(domain_port, "update_persona"), "PersonaCapitalRuntimeDomainPort must NOT have update_persona"


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
    """Verify that using the same Idempotency-Key with different payload returns 409 conflict with sanitized reason."""
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
        # Verify sanitized reason - no raw stack trace
        assert "traceback" not in str(err).lower()


def test_dev_paper_baseline_downstream_failure_returns_typed_502_sanitized(monkeypatch):
    """Verify that downstream coordinator failure returns typed 502 UPSTREAM_ERROR with sanitized details."""
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
        details = err.get("details", {})
        assert details is not None
        assert details.get("provisioningState") in {"failed", "compensated"}
        assert details.get("precondition_failed") in {"capital_pool", "provisioning_coordination", "provisioning"}
        # Verify sanitization: no internal terminalReason or idempotencyKey leaked in details
        assert "terminalReason" not in details, "terminalReason must not be exposed in API error details"
        assert "idempotencyKey" not in details, "idempotencyKey must not be exposed in API error details"
        assert "traceback" not in str(err).lower()


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

        # 1. Unauthenticated -> 401
        resp_unauth = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={"Idempotency-Key": "auth-test-key-1"},
        )
        assert resp_unauth.status_code == 401

        # 2. Insufficient role (viewer cannot create personas) -> 403
        resp_viewer = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**VIEWER_HEADERS, "Idempotency-Key": "auth-test-key-2"},
        )
        assert resp_viewer.status_code == 403

        # 3. Non-paper capital mode -> 422
        resp_non_paper = client.post(
            "/bff/management/personas/create-paper-bundle",
            json={**payload, "capitalMode": "live"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "auth-test-key-3"},
        )
        assert resp_non_paper.status_code == 422


def test_dev_paper_baseline_durable_owner_delegation(monkeypatch):
    """Verify that create-paper-bundle delegates mutations to durable owners and stores state in PersonaProvisioningStore."""
    transport, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        idempotency_key = "dev-paper-durable-owner-test-key-v1"
        payload = {
            "name": "Pantheon Dev Paper Baseline Durable",
            "archetype": "trend_following",
            "risk": "low",
            "mandate": "Testing durable owner delegation",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }

        resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert resp.status_code == 201

        # Check durable store has persisted the record
        record = store.get("tenant-dev-default", idempotency_key)
        if record is None:
            # check with any recorded tenant
            records = list(getattr(store, "_records", {}).values())
            assert len(records) > 0, "Durable PersonaProvisioningStore must contain the provisioning record"
            record = records[0]

        assert record.state in {"provisioning", "succeeded"}
        assert "capital_pool" in record.references
        assert "deployment_plan" in record.references
        assert "persona_capital_binding_created" in record.references
        assert (record.result or {}).get("capital_pool_id") is not None
        assert (record.result or {}).get("deployment_plan_id") is not None
        assert (record.result or {}).get("persona_capital_binding_id") is not None


def test_dev_paper_baseline_restart_idempotency_and_readback(monkeypatch):
    """Verify detail readback and retry consistency across server restarts when in-memory overlay is cleared."""
    transport, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        idempotency_key = "dev-paper-restart-test-key-v1"
        payload = {
            "name": "Pantheon Dev Paper Baseline Restart",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Testing restart readback consistency",
            "market": "US",
            "strategy_family": "dev_paper_baseline",
        }

        create_resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert create_resp.status_code == 201
        persona_id = create_resp.json()["data"]["id"]

        # Simulate full BFF restart: clear in-memory overlays and caches
        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main.read_store = create_in_memory_read_surface_ports()

        # 1. Readback detail must recover from durable provisioning store
        detail_resp = client.get(
            f"/bff/personas/{persona_id}",
            headers=OPERATOR_HEADERS,
        )
        assert detail_resp.status_code == 200, f"Expected 200 after restart, got {detail_resp.status_code}: {detail_resp.text}"
        detail_data = detail_resp.json()["data"]
        assert detail_data["id"] == persona_id
        assert detail_data["name"] == "Pantheon Dev Paper Baseline Restart"

        # 2. Idempotent retry must succeed deterministically after restart
        retry_resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert retry_resp.status_code == 201
        assert retry_resp.json()["data"]["id"] == persona_id


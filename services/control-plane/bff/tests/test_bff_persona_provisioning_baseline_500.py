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
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports.persona_capital_runtime import PersonaFleetPort, PersonaCapitalRuntimeDomainPort
from persona_provisioning import MemoryPersonaProvisioningStore, MemoryProvisioningBackend
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
            # check with any recorded tenant via the store's public list surface
            records = store.list_all()
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

        # Simulate full BFF restart: fresh store instance initialized over persistent backing,
        # clear in-memory overlays, caches, and read_store
        # A fresh, identity-distinct store instance reading the same shared
        # backend proves durable readback through the public protocol only
        # (no private-state copy) -- the in-process analogue of two BFF
        # replicas reading one shared Postgres table.
        fresh_store = MemoryPersonaProvisioningStore(backend=store.backend)
        assert fresh_store is not store
        assert fresh_store.backend is store.backend
        monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", fresh_store)
        monkeypatch.setattr(bff_main, "_persona_provisioning_store", lambda: fresh_store)

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

        # 2. Readback list must also recover from durable provisioning store after restart
        list_resp = client.get(
            "/bff/personas",
            headers=OPERATOR_HEADERS,
        )
        assert list_resp.status_code == 200, f"Expected 200 list after restart, got {list_resp.status_code}: {list_resp.text}"
        list_items = list_resp.json().get("data", [])
        matched = [p for p in list_items if p.get("id") == persona_id]
        assert len(matched) == 1, f"Newly provisioned persona {persona_id} must appear in list after restart: {list_items}"
        matched_item = matched[0]
        assert matched_item["name"] == "Pantheon Dev Paper Baseline Restart"
        assert matched_item["state"] in {"provisioning", "paper_running"}
        assert matched_item["capitalMode"] == "paper"
        assert matched_item["deploymentStage"] == "paper"
        assert matched_item.get("paperLedgerId", "").startswith("paper-ledger-")
        assert isinstance(matched_item.get("paperLedger"), dict)
        assert matched_item.get("legacyPaperCapitalPoolId") is not None
        assert matched_item.get("deploymentPlanId") is not None

        # 3. Idempotent retry must succeed deterministically after restart
        retry_resp = client.post(
            "/bff/management/personas/create-paper-bundle",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": idempotency_key},
        )
        assert retry_resp.status_code == 201
        assert retry_resp.json()["data"]["id"] == persona_id


def test_dev_paper_baseline_restart_durable_list_readback(monkeypatch):
    """Verify newly provisioned Persona is returned in GET /bff/personas after BFF restart with fresh store instance and no required fields sourced from process-local overlay."""
    _, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        idempotency_key = "dev-paper-list-readback-restart-test-key"
        payload = {
            "name": "Pantheon Dev Paper List Readback",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Testing durable list readback",
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

        # Simulate full BFF restart: fresh store instance initialized over persistent backing,
        # clear in-memory overlays, caches, and read_store
        # A fresh, identity-distinct store instance reading the same shared
        # backend proves durable readback through the public protocol only
        # (no private-state copy) -- the in-process analogue of two BFF
        # replicas reading one shared Postgres table.
        fresh_store = MemoryPersonaProvisioningStore(backend=store.backend)
        assert fresh_store is not store
        assert fresh_store.backend is store.backend
        monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", fresh_store)
        monkeypatch.setattr(bff_main, "_persona_provisioning_store", lambda: fresh_store)

        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main.read_store = create_in_memory_read_surface_ports()

        # Call list endpoint
        list_resp = client.get(
            "/bff/personas",
            headers=OPERATOR_HEADERS,
        )
        assert list_resp.status_code == 200, f"Expected 200 list after restart, got {list_resp.status_code}: {list_resp.text}"
        data = list_resp.json().get("data", [])
        page_info = list_resp.json().get("page_info", {})
        assert page_info.get("canonical_total", 0) >= 1
        assert page_info.get("total", 0) >= 1

        matched = [item for item in data if item.get("id") == persona_id]
        assert len(matched) == 1, f"Persona {persona_id} must be present in list after restart: {data}"
        dto = matched[0]

        # Verify no required fields missing from durable projection
        assert dto.get("id") == persona_id
        assert dto.get("name") == "Pantheon Dev Paper List Readback"
        assert dto.get("state") in {"provisioning", "paper_running"}
        assert dto.get("capitalMode") == "paper"
        assert dto.get("deploymentStage") == "paper"
        assert dto.get("archetype") == "momentum"
        assert dto.get("risk") == "low"
        assert dto.get("paperLedgerId", "").startswith("paper-ledger-")
        assert isinstance(dto.get("paperLedger"), dict)
        assert dto.get("legacyPaperCapitalPoolId") is not None
        assert dto.get("deploymentPlanId") is not None
        assert dto.get("tenantId") == create_resp.json()["data"]["tenantId"]


def test_dev_paper_baseline_store_outage_fails_closed_with_typed_503_diagnostics(monkeypatch):
    """Verify that authoritative provisioning store outage during list or detail fails closed with typed 503 DEPENDENCY_UNAVAILABLE, never false 404 or false 200 empty."""
    class _FailingStore:
        def reserve(self, **_kwargs):
            raise RuntimeError("authoritative provisioning database connection lost")

        def get(self, *_args, **_kwargs):
            raise RuntimeError("authoritative provisioning database connection lost")

        def get_by_persona(self, *_args, **_kwargs):
            raise RuntimeError("authoritative provisioning database connection lost")

        def list_by_tenant(self, *_args, **_kwargs):
            raise RuntimeError("authoritative provisioning database connection lost")

        def list_all(self, *_args, **_kwargs):
            raise RuntimeError("authoritative provisioning database connection lost")

    failing_store = _FailingStore()
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", failing_store)
    monkeypatch.setattr(bff_main, "_persona_provisioning_store", lambda: failing_store)

    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()

        client = TestClient(bff_main.app, raise_server_exceptions=False)

        # 1. List request must fail closed with 503 DEPENDENCY_UNAVAILABLE
        list_resp = client.get(
            "/bff/personas",
            headers=OPERATOR_HEADERS,
        )
        assert list_resp.status_code == 503, f"Expected 503 on list during store outage, got {list_resp.status_code}: {list_resp.text}"
        list_err = list_resp.json().get("error", {})
        assert list_err.get("code") == "DEPENDENCY_UNAVAILABLE"
        assert list_err.get("retryable") is True
        assert list_err.get("details", {}).get("precondition_failed") == "persona_provisioning_store"

        # 2. Detail request must fail closed with 503 DEPENDENCY_UNAVAILABLE, NOT false 404
        detail_resp = client.get(
            "/bff/personas/persona-unknown-id",
            headers=OPERATOR_HEADERS,
        )
        assert detail_resp.status_code == 503, f"Expected 503 on detail during store outage, got {detail_resp.status_code}: {detail_resp.text}"
        detail_err = detail_resp.json().get("error", {})
        assert detail_err.get("code") == "DEPENDENCY_UNAVAILABLE"
        assert detail_err.get("retryable") is True
        assert detail_err.get("details", {}).get("precondition_failed") == "persona_provisioning_store"


def test_dev_paper_baseline_list_ordering_filtering_and_pagination(monkeypatch):
    """Verify deterministic deduplication, ordering, filtering, and pagination across restart."""
    _, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)

        personas_to_create = [
            {"name": "Persona Alpha", "archetype": "momentum", "key": "key-alpha"},
            {"name": "Persona Beta", "archetype": "trend_following", "key": "key-beta"},
            {"name": "Persona Gamma", "archetype": "momentum", "key": "key-gamma"},
        ]
        created_ids = []
        for p in personas_to_create:
            resp = client.post(
                "/bff/management/personas/create-paper-bundle",
                json={
                    "name": p["name"],
                    "archetype": p["archetype"],
                    "risk": "low",
                    "mandate": f"Mandate for {p['name']}",
                    "market": "US",
                    "strategy_family": "dev_paper_baseline",
                },
                headers={**OPERATOR_HEADERS, "Idempotency-Key": p["key"]},
            )
            assert resp.status_code == 201
            created_ids.append(resp.json()["data"]["id"])

        # Simulate BFF restart: fresh store instance initialized over persistent records
        # A fresh, identity-distinct store instance reading the same shared
        # backend proves durable readback through the public protocol only
        # (no private-state copy) -- the in-process analogue of two BFF
        # replicas reading one shared Postgres table.
        fresh_store = MemoryPersonaProvisioningStore(backend=store.backend)
        assert fresh_store is not store
        assert fresh_store.backend is store.backend
        monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", fresh_store)
        monkeypatch.setattr(bff_main, "_persona_provisioning_store", lambda: fresh_store)

        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main.read_store = create_in_memory_read_surface_ports()

        # 1. Total list has all 3
        resp_all = client.get("/bff/personas", headers=OPERATOR_HEADERS)
        assert resp_all.status_code == 200
        all_items = resp_all.json()["data"]
        all_ids = [item["id"] for item in all_items]
        for cid in created_ids:
            assert cid in all_ids

        # 2. Filtering by archetype=momentum returns Alpha and Gamma only
        resp_momentum = client.get("/bff/personas?archetype=momentum", headers=OPERATOR_HEADERS)
        assert resp_momentum.status_code == 200
        mom_items = resp_momentum.json()["data"]
        mom_names = [item["name"] for item in mom_items]
        assert "Persona Alpha" in mom_names
        assert "Persona Gamma" in mom_names
        assert "Persona Beta" not in mom_names
        assert resp_momentum.json()["page_info"]["filtered_total"] == 2

        # 3. Pagination with page_size=2
        resp_page1 = client.get("/bff/personas?page_size=2", headers=OPERATOR_HEADERS)
        assert resp_page1.status_code == 200
        page1_data = resp_page1.json()["data"]
        assert len(page1_data) == 2
        next_token = resp_page1.json()["page_info"]["next_page_token"]
        assert next_token is not None

        resp_page2 = client.get(f"/bff/personas?page_size=2&page_token={next_token}", headers=OPERATOR_HEADERS)
        assert resp_page2.status_code == 200
        page2_data = resp_page2.json()["data"]
        assert len(page2_data) >= 1
        page1_ids = {p["id"] for p in page1_data}
        page2_ids = {p["id"] for p in page2_data}
        assert page1_ids.isdisjoint(page2_ids), "Page 1 and Page 2 must not overlap"


def test_dev_paper_baseline_tenant_isolation_on_list_and_detail(monkeypatch):
    """Verify strict tenant isolation across list and detail readbacks with multiple tenants."""
    _, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)

        # 1. Create persona under default tenant (pantheon-dev)
        resp1 = client.post(
            "/bff/management/personas/create-paper-bundle",
            json={
                "name": "Tenant A Persona",
                "archetype": "momentum",
                "risk": "low",
                "mandate": "Tenant A mandate",
                "market": "US",
                "strategy_family": "dev_paper_baseline",
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "tenant-a-key"},
        )
        assert resp1.status_code == 201
        tenant_a_persona_id = resp1.json()["data"]["id"]

        # 2. Reserve a second persona under a different tenant (tenant-other) directly in durable store
        store_rec2, _ = store.reserve(
            tenant_id="tenant-other",
            idempotency_key="tenant-b-key",
            request_hash="sha256:tenant-b",
            normalized_name="tenant b persona",
            persona_id="persona-tenant-b-id",
            request_payload={
                "name": "Tenant B Persona",
                "archetype": "trend_following",
                "risk": "low",
                "mandate": "Tenant B mandate",
                "requested_by": "op-other",
                "capitalMode": "paper",
            },
        )
        store.acquire("tenant-other", "tenant-b-key", lease_owner="test-init", lease_seconds=60)
        store_rec2.state = "succeeded"
        store.checkpoint(store_rec2, lease_owner="test-init", lease_seconds=60)
        store.release(store_rec2, lease_owner="test-init")
        tenant_b_persona_id = "persona-tenant-b-id"

        # Clear overlay to test durable isolation
        bff_main._PERSONA_BFF_OVERLAY.clear()

        # Query list as Tenant A (pantheon-dev) -> contains Tenant A persona, excludes Tenant B persona
        list_a = client.get("/bff/personas", headers=OPERATOR_HEADERS)
        assert list_a.status_code == 200
        a_ids = [item["id"] for item in list_a.json()["data"]]
        assert tenant_a_persona_id in a_ids
        assert tenant_b_persona_id not in a_ids

        # Query detail of Tenant A persona as Tenant A -> 200
        detail_a = client.get(f"/bff/personas/{tenant_a_persona_id}", headers=OPERATOR_HEADERS)
        assert detail_a.status_code == 200
        assert detail_a.json()["data"]["id"] == tenant_a_persona_id

        # Query detail of Tenant B persona as Tenant A -> 404 (does not leak other tenant's persona)
        detail_b_as_a = client.get(f"/bff/personas/{tenant_b_persona_id}", headers=OPERATOR_HEADERS)
        assert detail_b_as_a.status_code == 404
        assert detail_b_as_a.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
def _reserve_cross_tenant_persona(store, *, persona_id="persona-cross-tenant-canary"):
    """Directly reserve a durable Persona under tenant-other, bypassing any
    tenant-dev caller context, so a leak shows up as an unexpected hit rather
    than requiring a second authenticated identity."""
    record, _ = store.reserve(
        tenant_id="tenant-other",
        idempotency_key="cross-tenant-canary-key",
        request_hash="sha256:cross-tenant-canary",
        normalized_name="cross tenant canary persona",
        persona_id=persona_id,
        request_payload={
            "name": "Cross Tenant Canary Persona",
            "archetype": "trend_following",
            "risk": "low",
            "mandate": "Cross tenant leak canary",
            "requested_by": "op-other",
            "capitalMode": "paper",
        },
    )
    store.acquire("tenant-other", "cross-tenant-canary-key", lease_owner="test-init", lease_seconds=60)
    record.state = "succeeded"
    store.checkpoint(record, lease_owner="test-init", lease_seconds=60)
    store.release(record, lease_owner="test-init")
    return persona_id


def test_dev_paper_baseline_search_does_not_leak_cross_tenant_persona(monkeypatch):
    """GET /bff/search must not surface a Persona reserved under another tenant."""
    _, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        bff_main.read_store = create_in_memory_read_surface_ports()
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        canary_id = _reserve_cross_tenant_persona(store)

        resp = client.get(
            "/bff/search?q=cross tenant canary&types=persona",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200
        result_ids = [item.get("id") for item in resp.json().get("results", [])]
        assert canary_id not in result_ids, "search must not leak a Persona reserved under another tenant"


def test_dev_paper_baseline_persona_league_does_not_leak_cross_tenant_persona(monkeypatch):
    """GET /bff/management/persona-league must not surface a Persona reserved under another tenant."""
    _, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        bff_main.read_store = create_in_memory_read_surface_ports()
        monkeypatch.setattr(bff_main.read_store, "put_ranking_snapshot", lambda *_a, **_kw: None, raising=False)
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        canary_id = _reserve_cross_tenant_persona(store)

        resp = client.get("/bff/management/persona-league", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200
        assert canary_id not in resp.text, "persona-league must not leak a Persona reserved under another tenant"


def test_dev_paper_baseline_persona_intent_does_not_leak_cross_tenant_persona(monkeypatch):
    """GET /bff/management/persona-intent must not surface sessions for a Persona reserved under another tenant."""
    _, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        bff_main.read_store = create_in_memory_read_surface_ports()
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        canary_id = _reserve_cross_tenant_persona(store)

        resp = client.get(
            f"/bff/management/persona-intent?persona_id={canary_id}",
            headers=OPERATOR_HEADERS,
        )
        assert resp.status_code == 200
        items = resp.json().get("items") or []
        assert not items, "persona-intent must not resolve sessions for a Persona reserved under another tenant"


def test_dev_paper_baseline_sentinel_findings_does_not_leak_cross_tenant_persona(monkeypatch):
    """GET /bff/v5/sentinel/findings must not derive a persona_health finding from another tenant's Persona."""
    _, store = _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        bff_main.read_store = create_in_memory_read_surface_ports()
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()

        client = TestClient(bff_main.app)
        canary_id = _reserve_cross_tenant_persona(store)

        resp = client.get("/bff/v5/sentinel/findings", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200
        items = resp.json().get("data") or resp.json().get("items") or []
        derived_ids = [item.get("derived_from_persona_id") or item.get("persona_id") for item in items]
        assert canary_id not in derived_ids, "sentinel findings must not derive from a Persona reserved under another tenant"


def test_dev_paper_baseline_tenantless_overlay_is_never_admitted_to_tenant_readbacks(monkeypatch):
    """A tenantless, non-canonical Persona record is not a tenant wildcard."""
    _setup_mock_services(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        read_store = create_in_memory_read_surface_ports()
        tenantless = {
            "id": "persona-tenantless-canary",
            "persona_id": "persona-tenantless-canary",
            "name": "Tenantless Canary",
            "state": "paper_running",
            "archetype": "momentum",
        }
        bff_main.read_store = read_store
        bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._PERSONA_BFF_OVERLAY.clear()
        monkeypatch.setitem(bff_main._PERSONA_BFF_OVERLAY, "persona-tenantless-canary", tenantless)

        client = TestClient(bff_main.app)
        listed = client.get("/bff/personas", headers=OPERATOR_HEADERS)
        assert listed.status_code == 200
        assert "persona-tenantless-canary" not in [item["id"] for item in listed.json()["data"]]

        detail = client.get("/bff/personas/persona-tenantless-canary", headers=OPERATOR_HEADERS)
        assert detail.status_code == 404
        assert detail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_dev_paper_baseline_tenantless_registry_row_is_never_admitted(monkeypatch):
    """A tenantless registry row has no tenant ownership and fails closed."""
    _setup_mock_services(monkeypatch)
    read_store = create_in_memory_read_surface_ports()
    monkeypatch.setattr(
        read_store,
        "list_personas",
        lambda **_kwargs: [{
            "id": "persona-legacy-tenantless-canary",
            "persona_id": "persona-legacy-tenantless-canary",
            "name": "Legacy Tenantless Canary",
            "lifecycle_state": "paper_running",
            "metadata": {"archetype": "momentum"},
        }],
    )
    bff_main.read_store = read_store
    bff_main._PERSONA_BFF_OVERLAY.clear()

    tenant_dev_ids = {
        item["persona_id"]
        for item in bff_main._list_persona_records("tenant-dev")
    }
    tenant_other_ids = {
        item["persona_id"]
        for item in bff_main._list_persona_records("tenant-other")
    }
    assert "persona-legacy-tenantless-canary" not in tenant_dev_ids
    assert "persona-legacy-tenantless-canary" not in tenant_other_ids

    client = TestClient(bff_main.app)
    listed = client.get("/bff/personas", headers=OPERATOR_HEADERS)
    assert listed.status_code == 200
    assert "persona-legacy-tenantless-canary" not in [item["id"] for item in listed.json()["data"]]

    detail = client.get("/bff/personas/persona-legacy-tenantless-canary", headers=OPERATOR_HEADERS)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_dev_paper_baseline_strategy_seed_route_excludes_other_tenant_persona(monkeypatch):
    """GET /bff/management/strategy-seeds cannot suggest a foreign Persona."""
    _, store = _setup_mock_services(monkeypatch)
    bff_main.read_store = create_in_memory_read_surface_ports()
    canary_id = _reserve_cross_tenant_persona(store)

    seed = SimpleNamespace(
        seed_id="seed-cross-tenant",
        source_id="source-cross-tenant",
        source_ids=["source-cross-tenant"],
        evidence_bundle_id="bundle-cross-tenant",
        hypothesis="Cross-tenant regression canary.",
        asset_class=["equity"],
        market_scope=["US"],
        holding_period="swing",
        required_data=["ohlcv"],
        confidence=0.9,
        status="draft",
        metadata={"strategy_family": "momentum"},
        lineage={},
    )

    class _SeedStore:
        path = "strategy-seeds-cross-tenant-test"

        def list_all(self):
            return [seed]

    class _Match:
        def to_dict(self):
            return {
                "matched_object_id": "seed-cross-tenant",
                "match_id": "match-cross-tenant",
                "score": 1.0,
                "metadata": {"blockers": []},
                "recommended_action": {"type": "promote_seed_candidate"},
            }

    class _Discovery:
        def match_candidates(self, *_args, **_kwargs):
            return [_Match()]

    monkeypatch.setattr(bff_main, "StrategySpecSeedStore", _SeedStore)
    monkeypatch.setattr(bff_main, "PersonaStrategyDiscoveryService", _Discovery)
    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/management/strategy-seeds",
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    cards = response.json()["data"]["items"]
    assert len(cards) == 1
    assert cards[0]["suggested_actions"] == []
    assert canary_id not in response.text


def test_dev_paper_baseline_pm12_route_excludes_other_tenant_runtime(monkeypatch):
    """GET PM12 attribution excludes runtime facts without a caller-tenant Persona."""
    _, store = _setup_mock_services(monkeypatch)
    read_store = create_in_memory_read_surface_ports()
    bff_main.read_store = read_store
    canary_id = _reserve_cross_tenant_persona(store)
    runtime = {
        "runtime_id": "runtime-cross-tenant",
        "runtime_binding_id": "binding-cross-tenant",
        "persona_id": canary_id,
        "strategy_id": "strategy-cross-tenant",
        "capital_pool_id": "pool-cross-tenant",
        "status": "active",
        "deployment_stage": "paper",
    }
    binding = {
        "persona_capital_binding_id": "binding-cross-tenant",
        "persona_id": canary_id,
        "strategy_id": "strategy-cross-tenant",
        "capital_pool_id": "pool-cross-tenant",
    }
    monkeypatch.setattr(read_store, "list_runtime_bindings", lambda **_kwargs: [runtime])
    monkeypatch.setattr(read_store, "list_bindings", lambda **_kwargs: [binding])
    monkeypatch.setattr(read_store, "list_deployment_plans", lambda: [])
    monkeypatch.setattr(read_store, "list_capital_pools", lambda **_kwargs: [])
    monkeypatch.setattr(read_store, "list_telemetry_summaries", lambda: [])
    monkeypatch.setattr(read_store, "get_telemetry_summary", lambda _runtime_id: {})

    sources = bff_main._pm12_performance_attribution_sources("tenant-dev")
    assert bff_main._pm12_performance_attribution_facts(sources, "latest") == []
    assert bff_main._management_strategy_allocation_runtime_facts(sources) == []

    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/management/performance-attribution/by-strategy",
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert canary_id not in response.text


def test_dev_paper_baseline_agora_persona_intent_route_excludes_other_tenant_context(monkeypatch):
    """GET Persona Intent excludes an Agora session tied to a foreign Persona."""
    _, store = _setup_mock_services(monkeypatch)
    read_store = create_in_memory_read_surface_ports()
    bff_main.read_store = read_store
    canary_id = _reserve_cross_tenant_persona(store)
    monkeypatch.setattr(
        read_store,
        "list_agora_sessions",
        lambda **_kwargs: [{
            "sessionId": "agora-cross-tenant-session",
            "status": "active",
            "mode": "committee",
            "title": "Foreign Persona Agora Context",
            "createdAt": "2026-08-30T00:00:00Z",
            "updatedAt": "2026-08-30T00:00:00Z",
            "contextRefs": [{"type": "persona", "id": canary_id}],
        }],
    )

    client = TestClient(bff_main.app)
    response = client.get(
        "/bff/management/persona-intent?source_type=agora_session",
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"] == []
    assert "agora-cross-tenant-session" not in response.text
    assert canary_id not in response.text


def test_dev_paper_baseline_fleet_reports_catalog_defaults_without_ghost_rows(monkeypatch):
    """Catalog defaults affect only the summary, never fleet identities."""
    _setup_mock_services(monkeypatch)
    read_store = create_in_memory_read_surface_ports()
    for method_name in (
        "list_persona_league",
        "list_bindings",
        "list_runtime_bindings",
        "list_capital_pools",
        "list_incidents",
        "list_evolution_decisions",
        "list_telemetry_summaries",
    ):
        monkeypatch.setattr(read_store, method_name, lambda **_kwargs: [])
    bff_main.read_store = read_store
    snapshot = bff_main.PersonaDirectorySnapshot(
        tenant_id="tenant-dev",
        snapshot_at="2026-08-30T00:00:00Z",
        records_by_id={},
        catalog_defaults_by_id={
            "persona-catalog-canary": {
                "id": "persona-catalog-canary",
                "name": "Catalog Canary",
                "record_kind": "catalog_default",
                "detail_available": False,
                "admission_state": "not_admitted",
            }
        },
    )
    monkeypatch.setattr(bff_main, "_get_persona_directory_snapshot", lambda *_args, **_kwargs: snapshot)

    payload = bff_main._persona_fleet_slim_list_payload(
        tenant_id="tenant-dev",
        snapshot_at="2026-08-30T00:00:00Z",
        state=None,
        health=None,
        deployment_stage=None,
        market_scope=None,
        q=None,
        page_token=None,
        page_size=20,
    )
    data = payload["data"]
    assert data["items"] == []
    assert set(data) == {"items", "summary"}
    assert data["summary"]["catalog_default_total"] == 1

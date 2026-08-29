"""
Tests for LOOP-PROD-PER-001: Persona provisioning, readback, duplicate safety, and terminal failure states.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from persona_provisioning import MemoryPersonaProvisioningStore
from ports import create_read_surface_ports
from test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {
    "Authorization": OPERATOR_TOKEN,
    "Idempotency-Key": "test-provisioning-idempotency",
}


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch):
    transport = FakeOwnerTransport()
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(bff_main, "_PersonaOwnerHttpTransport", lambda: transport)
    monkeypatch.setattr(bff_main, "_register_persona_cron_required", _schedule_receipt)
    monkeypatch.setattr(
        bff_main,
        "_remove_persona_cron_required",
        lambda persona_id: {
            "persona_id": persona_id,
            "registered": False,
            "removed_ids": [],
        },
    )
    # Mock create_capital_binding
    monkeypatch.setattr(bff_main, "create_capital_binding", lambda payload: {"status": "created"})
    from services.persona.runtime_profile import build_persona_runtime_profile
    monkeypatch.setattr(bff_main, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    
    # Mock _post_json to do nothing and return empty dict
    monkeypatch.setattr(bff_main, "_post_json", lambda *args, **kwargs: {})
    
    # Mock _get_json to raise urllib.error.HTTPError for 404 (not found) by default
    import urllib.error
    from io import BytesIO
    fp = BytesIO(b"")
    mock_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, fp)
    monkeypatch.setattr(bff_main, "_get_json", lambda *args, **kwargs: (_ for _ in ()).throw(mock_404))
    
    # Mock _runtime_manager_client
    class MockRuntimeManagerClient:
        def deploy(self, request):
            binding_id = (
                request.get("persona_capital_binding_id")
                or request.get("binding_id")
                or request.get("runtime_binding_id")
                or "test-binding"
            )
            bff_main.read_store.create_runtime_binding(
                runtime_id=request.get("runtime_id", "test-runtime"),
                name=request.get("metadata", {}).get("name", "test"),
                persona_id=request.get("metadata", {}).get("persona_id", "test"),
                binding_id=binding_id,
                deployment_plan_id=request.get("plan_id", "test-plan"),
                runtime_kind="paper",
                actor_id="test",
                created_at=bff_main.utc_now(),
                params=request.get("metadata", {}),
                state=request.get("state") or "running",
            )
            return bff_main.read_store.get_runtime_binding(binding_id)
            
        def get(self, binding_id):
            return bff_main.read_store.get_runtime_binding(binding_id)
            
        def list_all(self):
            return list((bff_main.read_store._ensure_local_overlay_records("runtime_bindings") or {}).values())

        def list_by_plan(self, plan_id):
            return [
                binding
                for binding in self.list_all()
                if binding.get("plan_id") == plan_id
            ]
            
    mock_client = MockRuntimeManagerClient()
    monkeypatch.setattr(bff_main, "_runtime_manager_client", lambda: mock_client)


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = create_read_surface_ports()
    bff_main.command_store = bff_main.CommandStore(os.path.join(td, "commands.jsonl"))
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    bff_main._COMMAND_AUTH_CONTEXT.clear()
    return TestClient(bff_main.app)


def _install_authoritative_readback(
    *,
    persona_id: str,
    plan_id: str,
    saga_id: str,
    persona_capital_binding_id: str,
    binding_state: str = "active",
) -> tuple[str, str]:
    runtime_binding_id = f"rb-{persona_id[-12:]}"
    runtime_id = f"runtime-{persona_id[-12:]}"
    persona = bff_main.read_store.get_persona(persona_id)
    assert persona is not None
    metadata = persona["metadata"]
    capital_pool_id = metadata["internal_paper_capital_pool_id"]
    tenant_id = metadata["tenant_id"]
    authoritative_binding = {
        "binding_id": runtime_binding_id,
        "runtime_id": runtime_id,
        "plan_id": plan_id,
        "capital_pool_id": capital_pool_id,
        "persona_capital_binding_id": persona_capital_binding_id,
        "deployment_mode": "paper",
        "status": binding_state,
        "metadata": {"persona_id": persona_id, "tenant_id": tenant_id},
    }
    projection = {
        "plan_id": plan_id,
        "deployment_saga_id": saga_id,
        "deployment_saga_status": "completed",
        "deployment_saga_progress": {"progress_status": "completed"},
        "runtime_binding_id": runtime_binding_id,
        "runtime_id": runtime_id,
        "runtime_binding": authoritative_binding,
    }
    bff_main._get_json = lambda *_args, **_kwargs: projection

    class ExactRuntimeManagerClient:
        def get(self, binding_id):
            return authoritative_binding if binding_id == runtime_binding_id else None

        def list_all(self):
            return [authoritative_binding]

        def list_by_plan(self, requested_plan_id):
            return [authoritative_binding] if requested_plan_id == plan_id else []

    bff_main._runtime_manager_client = lambda: ExactRuntimeManagerClient()
    bff_main.read_store.list_authoritative_paper_runtime_monitoring_sessions = lambda: [
        {
            "session_id": f"session-{persona_id}",
            "runtime_id": runtime_id,
            "binding_id": runtime_binding_id,
            "capital_pool_id": capital_pool_id,
            "status": "running",
            "active": True,
            "last_heartbeat_at": bff_main.utc_now(),
        }
    ]
    bff_main._register_persona_cron_required = lambda *_args, **_kwargs: {
        "authoritative_readback": {
            "persona_id": persona_id,
            "workflow_id": "pantheon.persona.first-evaluation",
            "registered": True,
            "runtime_id": runtime_id,
            "runtime_binding_id": runtime_binding_id,
            "capital_pool_id": capital_pool_id,
            "persona_capital_binding_id": persona_capital_binding_id,
            "job_id": f"job-{persona_id}",
            "job_name": f"pantheon-first-evaluation-{persona_id}",
            "request_id": (
                f"persona-provisioning:{persona_id}:"
                "pantheon.persona.first-evaluation"
            ),
            "schedule": {"kind": "cron", "expr": "*/15 * * * *"},
            "session_target": persona_id,
            "observed_at": bff_main.utc_now(),
        }
    }
    return runtime_binding_id, runtime_id


def test_persona_creation_initial_state_is_provisioning() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            # Create a persona
            resp = client.post(
                "/bff/personas",
                json={"name": "Trader A", "traits": {"risk_appetite": "low"}},
                headers={**HEADERS, "Idempotency-Key": "create-trader-a"},
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()["data"]
            assert data["name"] == "Trader A"
            assert data["state"] == "provisioning"  # Should initially be provisioning

            # Get the persona detail
            persona_id = data["id"]
            get_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["state"] == "provisioning"
        finally:
            bff_main.read_store = original


def test_persona_provisioning_completes_upon_readback_success() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            # Create a persona
            resp = client.post(
                "/bff/personas",
                json={"name": "Trader B"},
                headers={**HEADERS, "Idempotency-Key": "create-trader-b"},
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()["data"]
            persona_id = data["id"]
            assert "runtimeBindingId" not in data
            assert "runtimeId" not in data
            runtime_binding_id, runtime_id = _install_authoritative_readback(
                persona_id=persona_id,
                plan_id=data["deploymentPlanId"],
                saga_id=resp.json()["meta"]["deployment_saga_id"],
                persona_capital_binding_id=resp.json()["meta"][
                    "persona_capital_binding_id"
                ],
            )

            # GET is intentionally pure and cannot advance lifecycle.
            get_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["state"] == "provisioning"

            reconciled = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile",
                headers=HEADERS,
            )
            assert reconciled.status_code == 200, reconciled.text
            reconciled_body = reconciled.json()
            assert reconciled_body["data"]["state"] == "paper_running"
            authoritative = reconciled_body["meta"]["authoritative_readback"]
            assert authoritative["available"] is True
            schedule = authoritative["first_evaluation_schedule"]
            assert schedule["workflow_id"] == "pantheon.persona.first-evaluation"
            assert schedule["registered"] is True
            assert schedule["runtime_id"] == runtime_id
            assert schedule["runtime_binding_id"] == runtime_binding_id

            # Check store to verify the status is persisted (restart-safe)
            persisted = bff_main.read_store.get_persona(persona_id)
            assert persisted["lifecycle_state"] == "paper_running"
        finally:
            bff_main.read_store = original


def test_persona_provisioning_fails_on_downstream_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/personas",
                json={"name": "Trader C"},
                headers={**HEADERS, "Idempotency-Key": "create-trader-c"},
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()["data"]
            persona_id = data["id"]
            _install_authoritative_readback(
                persona_id=persona_id,
                plan_id=data["deploymentPlanId"],
                saga_id=resp.json()["meta"]["deployment_saga_id"],
                persona_capital_binding_id=resp.json()["meta"][
                    "persona_capital_binding_id"
                ],
                binding_state="failed",
            )

            # GET stays pure; the explicit controller pass publishes failure.
            get_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["state"] == "provisioning"
            reconciled = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile",
                headers=HEADERS,
            )
            assert reconciled.status_code == 200, reconciled.text
            assert reconciled.json()["data"]["state"] == "failed"

            # Check store to verify failure state is persisted (restart-safe)
            persisted = bff_main.read_store.get_persona(persona_id)
            assert persisted["lifecycle_state"] == "provisioning_failed"
        finally:
            bff_main.read_store = original


def test_persona_provisioning_fails_on_timeout() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/personas",
                json={"name": "Trader D"},
                headers={**HEADERS, "Idempotency-Key": "create-trader-d"},
            )
            data = resp.json()["data"]
            persona_id = data["id"]

            # Timeout starts at the durable post-schedule readback checkpoint,
            # not at the Persona's original creation timestamp.
            persona = bff_main.read_store.get_persona(persona_id)
            assert persona is not None
            bff_main.read_store.update_persona(
                persona_id,
                metadata={"provisioning_readback_started_at": "2026-07-15T00:00:00Z"},
            )

            # GET stays pure; the explicit controller pass applies timeout.
            get_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["state"] == "provisioning"
            reconciled = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile",
                headers=HEADERS,
            )
            assert reconciled.status_code == 200, reconciled.text
            assert reconciled.json()["data"]["state"] == "failed"
        finally:
            bff_main.read_store = original


def test_persona_duplicate_create_converges() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            
            # Create first time
            resp1 = client.post(
                "/bff/personas",
                json={"name": "Trader Unique"},
                headers={**HEADERS, "Idempotency-Key": "create-trader-unique-1"},
            )
            assert resp1.status_code == 201, resp1.text
            data1 = resp1.json()["data"]
            persona_id_1 = data1["id"]

            # Create second time with a different idempotency key but same name
            resp2 = client.post(
                "/bff/personas",
                json={"name": "Trader Unique"},
                headers={**HEADERS, "Idempotency-Key": "create-trader-unique-2"},
            )
            assert resp2.status_code == 201, resp2.text
            data2 = resp2.json()["data"]
            persona_id_2 = data2["id"]

            # They converge to one dynamic Persona and one deterministic owner
            # identity set; RuntimeBinding remains absent until Deployment owns it.
            assert persona_id_1 == persona_id_2
            assert "runtimeBindingId" not in data1
            assert "runtimeBindingId" not in data2
            assert resp1.json()["meta"]["persona_capital_binding_id"] == resp2.json()[
                "meta"
            ]["persona_capital_binding_id"]
            assert resp1.json()["meta"]["deployment_saga_id"] == resp2.json()["meta"][
                "deployment_saga_id"
            ]
        finally:
            bff_main.read_store = original


def test_persona_duplicate_create_rejects_registry_only_success_projection() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            
            # 1. Create the persona
            resp1 = client.post(
                "/bff/personas",
                json={"name": "Trader Safety"},
                headers={**HEADERS, "Idempotency-Key": "create-safety-1"},
            )
            assert resp1.status_code == 201, resp1.text
            data1 = resp1.json()["data"]
            persona_id = data1["id"]
            before = bff_main.read_store.get_persona(persona_id)
            assert before is not None
            original_created_at = before["created_at"]
            original_readback_started_at = before["metadata"][
                "provisioning_readback_started_at"
            ]

            # 2. Forge only the Persona registry projection.  The durable
            # provisioning ledger is still non-terminal, so this cannot be
            # accepted as paper-running authority.
            bff_main.read_store.update_persona(persona_id, lifecycle_state="paper_running")

            # 3. Request creation again with same name but new idempotency key
            resp2 = client.post(
                "/bff/personas",
                json={"name": "Trader Safety"},
                headers={**HEADERS, "Idempotency-Key": "create-safety-2"},
            )
            assert resp2.status_code == 201, resp2.text
            
            # 4. Duplicate materialization converges to durable ledger truth.
            persisted_persona = bff_main.read_store.get_persona(persona_id)
            assert persisted_persona is not None
            assert persisted_persona["lifecycle_state"] == "provisioning"
            assert persisted_persona["created_at"] == original_created_at
            assert persisted_persona["metadata"][
                "provisioning_readback_started_at"
            ] == original_readback_started_at
            
        finally:
            bff_main.read_store = original

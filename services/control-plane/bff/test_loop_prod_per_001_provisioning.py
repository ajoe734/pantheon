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
from read_store import ReadSurfaceStore
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
    # Mock create_capital_binding
    monkeypatch.setattr(bff_main, "create_capital_binding", lambda payload: {"status": "created"})
    
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
            
    mock_client = MockRuntimeManagerClient()
    monkeypatch.setattr(bff_main, "_runtime_manager_client", lambda: mock_client)


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
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
    projection = {
        "plan_id": plan_id,
        "deployment_saga_id": saga_id,
        "deployment_saga_status": "completed",
        "deployment_saga_progress": {"progress_status": "completed"},
        "runtime_binding_id": runtime_binding_id,
        "runtime_id": runtime_id,
        "runtime_binding": {
            "binding_id": runtime_binding_id,
            "runtime_id": runtime_id,
            "plan_id": plan_id,
            "persona_capital_binding_id": persona_capital_binding_id,
            "status": binding_state,
            "metadata": {"persona_id": persona_id},
        },
    }
    bff_main._get_json = lambda *_args, **_kwargs: projection
    bff_main.read_store.list_authoritative_paper_runtime_monitoring_sessions = lambda: [
        {
            "session_id": f"session-{persona_id}",
            "runtime_id": runtime_id,
            "binding_id": runtime_binding_id,
            "status": "running",
            "active": True,
            "last_heartbeat_at": bff_main.utc_now(),
        }
    ]
    bff_main._register_persona_cron_required = lambda *_args, **_kwargs: {
        "authoritative_readback": {
            "registered": True,
            "runtime_id": runtime_id,
            "runtime_binding_id": runtime_binding_id,
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
            _install_authoritative_readback(
                persona_id=persona_id,
                plan_id=data["deploymentPlanId"],
                saga_id=resp.json()["meta"]["deployment_saga_id"],
                persona_capital_binding_id=resp.json()["meta"][
                    "persona_capital_binding_id"
                ],
            )

            # Query the persona -> should transition to paper_running
            get_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["state"] == "paper_running"

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

            # Query the persona -> should transition to failed
            get_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["state"] == "failed"

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

            # Query the persona -> should transition to failed due to timeout
            get_resp = client.get(f"/bff/personas/{persona_id}", headers=HEADERS)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["data"]["state"] == "failed"
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


def test_persona_duplicate_create_does_not_clobber_or_reset_timeout() -> None:
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

            # 2. Simulate a later authoritative lifecycle transition.
            bff_main.read_store.update_persona(persona_id, lifecycle_state="paper_running")

            # 3. Request creation again with same name but new idempotency key
            resp2 = client.post(
                "/bff/personas",
                json={"name": "Trader Safety"},
                headers={**HEADERS, "Idempotency-Key": "create-safety-2"},
            )
            assert resp2.status_code == 201, resp2.text
            
            # 4. Check that state was not clobbered (is still paper_running in read_store)
            persisted_persona = bff_main.read_store.get_persona(persona_id)
            assert persisted_persona is not None
            assert persisted_persona["lifecycle_state"] == "paper_running"
            assert persisted_persona["created_at"] == original_created_at
            assert persisted_persona["metadata"][
                "provisioning_readback_started_at"
            ] == original_readback_started_at
            
        finally:
            bff_main.read_store = original

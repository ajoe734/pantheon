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
from read_store import ReadSurfaceStore

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {
    "Authorization": OPERATOR_TOKEN,
    "Idempotency-Key": "test-provisioning-idempotency",
}


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch):
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
            data = resp.json()["data"]
            persona_id = data["id"]
            binding_id = data["runtimeBindingId"]
            runtime_id = data["runtimeId"]

            # Mock RuntimeBinding to be active/running
            bff_main.read_store.create_runtime_binding(
                runtime_id=runtime_id,
                name="Trader B paper runtime",
                persona_id=persona_id,
                binding_id=binding_id,
                deployment_plan_id=data["deploymentPlanId"],
                runtime_kind="paper",
                actor_id="test",
                created_at=bff_main.utc_now(),
                params={},
                state="running",  # active/running state
            )

            # Mock monitoring session with heartbeat
            bff_main.read_store._ensure_local_overlay_records("paper_runtime_monitoring_sessions")[
                f"{runtime_id}:{binding_id}"
            ] = {
                "session_id": "session-1",
                "runtime_id": runtime_id,
                "binding_id": binding_id,
                "active": True,
                "last_heartbeat_at": bff_main.utc_now(),
            }

            # Mock evaluation schedule (cron job) - PersonaCronRegistrar readback mock
            from unittest.mock import MagicMock
            mock_registrar_instance = MagicMock()
            mock_registrar_instance._get_runtime.return_value = "dummy-runtime"
            mock_registrar_instance._existing_registrations.return_value = [(persona_id, "workflow-1")]
            
            class DummyModule:
                PersonaCronRegistrar = MagicMock(return_value=mock_registrar_instance)
                
            sys.modules["persona_cron_registrar"] = DummyModule

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
            data = resp.json()["data"]
            persona_id = data["id"]
            binding_id = data["runtimeBindingId"]
            runtime_id = data["runtimeId"]

            # 1. Mock RuntimeBinding state to be failed
            bff_main.read_store.create_runtime_binding(
                runtime_id=runtime_id,
                name="Trader C paper runtime",
                persona_id=persona_id,
                binding_id=binding_id,
                deployment_plan_id=data["deploymentPlanId"],
                runtime_kind="paper",
                actor_id="test",
                created_at=bff_main.utc_now(),
                params={},
                state="failed",  # failed state
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

            # Mock creation date to be 200 seconds ago
            persona = bff_main.read_store.get_persona(persona_id)
            persona["created_at"] = "2026-07-15T00:00:00Z"
            bff_main.read_store.update_persona(persona_id, updated_at="2026-07-15T00:00:00Z")
            # Force update raw dict field in the local mock too
            raw_personas = bff_main.read_store._ensure_local_overlay_records("personas")
            raw_personas[persona_id]["created_at"] = "2026-07-15T00:00:00Z"
            bff_main.read_store._save()

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
            data1 = resp1.json()["data"]
            persona_id_1 = data1["id"]
            binding_id_1 = data1["runtimeBindingId"]

            # Create second time with a different idempotency key but same name
            resp2 = client.post(
                "/bff/personas",
                json={"name": "Trader Unique"},
                headers={**HEADERS, "Idempotency-Key": "create-trader-unique-2"},
            )
            data2 = resp2.json()["data"]
            persona_id_2 = data2["id"]
            binding_id_2 = data2["runtimeBindingId"]

            # They must converge to the same persona and binding
            assert persona_id_1 == persona_id_2
            assert binding_id_1 == binding_id_2
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
            data1 = resp1.json()["data"]
            persona_id = data1["id"]
            
            # 2. Advance the state and manually update runtime binding's created_at to old time
            old_created_at = "2026-07-15T04:00:00Z"
            
            bff_main.read_store.update_persona(persona_id, lifecycle_state="paper_running")
            
            # update runtime binding
            rb = bff_main.read_store.get_runtime_binding(data1["runtimeBindingId"])
            rb["created_at"] = old_created_at
            rb["state"] = "running"
            bff_main.read_store._ensure_local_overlay_records("runtime_bindings")[data1["runtimeBindingId"]] = rb
            bff_main.read_store._save()

            # 3. Request creation again with same name but new idempotency key
            resp2 = client.post(
                "/bff/personas",
                json={"name": "Trader Safety"},
                headers={**HEADERS, "Idempotency-Key": "create-safety-2"},
            )
            
            # 4. Check that state was not clobbered (is still paper_running in read_store)
            persisted_persona = bff_main.read_store.get_persona(persona_id)
            assert persisted_persona["lifecycle_state"] == "paper_running"
            
            # 5. Check that runtime binding state and created_at were not clobbered/reset
            persisted_rb = bff_main.read_store.get_runtime_binding(data1["runtimeBindingId"])
            assert persisted_rb["created_at"] == old_created_at
            assert persisted_rb["state"] == "running"
            
        finally:
            bff_main.read_store = original

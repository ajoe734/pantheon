"""BFF contract tests for MGMT-OPS-006: governed operator actions and Human Review."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

import json
import main as bff_main
from fastapi.testclient import TestClient
from ports import ReadSurfacePorts
from models import CommandType, RiskLevel

OPERATOR_TOKEN = "Bearer op-mgmt-ops-006:operator"
ADMIN_TOKEN = "Bearer op-mgmt-ops-006:admin"
REVIEWER_TOKEN = "Bearer op-mgmt-ops-006:reviewer"
APPROVER_TOKEN = "Bearer op-mgmt-ops-006:approver"
READONLY_TOKEN = "Bearer op-mgmt-ops-006:reader"


def _load_fallback_data() -> dict[str, Any]:
    fallback_path = os.path.join(os.path.dirname(__file__), "data", "read_surfaces.json")
    if os.path.exists(fallback_path):
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class MgmtOps006TestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        if seed_data is not None:
            self._data: dict[str, Any] = seed_data
        elif allow_fallback:
            self._data = _load_fallback_data()
        else:
            self._data = {}
        self.allow_fallback = allow_fallback

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "source": "local_snapshot", "snapshot_at": snapshot_at}

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, [])

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        persona_id = kwargs.get("persona_id") or kwargs.get("id") or "p-new"
        persona = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": kwargs.get("name") or persona_id,
            "lifecycle_state": kwargs.get("lifecycle_state") or "active",
            "metadata": kwargs.get("metadata") or {},
        }
        ds = self._data.setdefault("personas", {})
        if isinstance(ds, dict):
            ds[persona_id] = persona
        elif isinstance(ds, list):
            ds.append(persona)
        return persona

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("personas", {})
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("personas", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_runtime_binding(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("runtime_bindings") or self._data.get("runtime_instances") or self._data.get("runtimes") or {}
        if isinstance(ds, dict):
            return ds.get(str(runtime_id or ""))
        return next((r for r in ds if r.get("id") == runtime_id or r.get("runtime_id") == runtime_id), None)

    def get_runtime_binding_by_runtime_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        return self.get_runtime_binding(runtime_id)


@contextmanager
def _client_with_store(store: MgmtOps006TestReadPorts) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    bff_main.read_store = store
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store


def _fresh_store() -> MgmtOps006TestReadPorts:
    return MgmtOps006TestReadPorts(allow_fallback=True)


def test_operator_roles_check() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-role",
        name="Test Role Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    with _client_with_store(store) as client:
        # PausePaperRuntime without operator/admin role should be forbidden
        response = client.post(
            "/bff/v1/commands",
            headers={"Authorization": READONLY_TOKEN, "Idempotency-Key": "test-role-1"},
            json={
                "command": "PausePaperRuntime",
                "target": {"type": "Runtime", "id": "runtime-test-role"},
                "params": {"runtime_id": "runtime-test-role"},
                "audit_context": {"reason": "Test role block"},
            }
        )
        assert response.status_code == 403
        assert "role_check" in response.text or "Forbidden" in response.text


def test_rejected_preconditions_unverifiable_source_confidence() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-unverifiable",
        name="Unverifiable Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    original_ops_model = bff_main._ops_read_model_entry_for_persona

    from operations_read_model import OperationsReadModelEntry, OperationsIdentity, DataConfidence as OpsDataConfidence, OperationsPerformance

    def mock_ops_model(persona_id, period="latest"):
        return OperationsReadModelEntry(
            identity=OperationsIdentity(persona_id=persona_id, period=period, as_of="2026-07-09T00:00:00Z"),
            data_confidence=OpsDataConfidence.UNAVAILABLE,
            performance=OperationsPerformance(),
            sources=[],
            diagnostics=[]
        )

    bff_main._ops_read_model_entry_for_persona = mock_ops_model

    try:
        with _client_with_store(store) as client:
            response = client.post(
                "/bff/v1/commands",
                headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "test-unverifiable-1"},
                json={
                    "command": "PausePaperRuntime",
                    "target": {"type": "Runtime", "id": "runtime-test"},
                    "params": {"persona_id": "persona-test-unverifiable", "runtime_id": "runtime-test"},
                    "audit_context": {"reason": "Test confidence block"},
                }
            )
            assert response.status_code == 422
            assert "source_confidence" in response.text or "unavailable" in response.text or "unverifiable" in response.text
    finally:
        bff_main._ops_read_model_entry_for_persona = original_ops_model


def test_emergency_containment_limit() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-containment",
        name="Containment Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    with _client_with_store(store) as client:
        # EmergencyContainment trying to increase allocation or promote should fail
        response = client.post(
            "/bff/v1/commands",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "test-containment-1"},
            json={
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": "persona-test-containment"},
                "params": {
                    "persona_id": "persona-test-containment",
                    "allocation_increase": True
                },
                "audit_context": {"reason": "Containment promotion test"},
            }
        )
        assert response.status_code == 422
        assert "Emergency containment cannot promote or increase allocation" in response.text


def test_command_idempotency() -> None:
    store = _fresh_store()
    store.create_persona(
        persona_id="persona-test-idempotency",
        name="Idempotency Persona",
        actor_id="test",
        lifecycle_state="deployed",
        metadata={},
    )

    runtimes = [
        {
            "id": "rb-test",
            "binding_id": "rb-test",
            "runtime_id": "runtime-test",
            "status": "active",
            "deployment_stage": "paper",
        }
    ]
    store.list_runtime_bindings = lambda **_: runtimes
    store.get_runtime_binding_by_runtime_id = lambda runtime_id: runtimes[0] if runtime_id == "runtime-test" else None

    original_ops_model = bff_main._ops_read_model_entry_for_persona

    from operations_read_model import OperationsReadModelEntry, OperationsIdentity, DataConfidence as OpsDataConfidence, OperationsPerformance

    def mock_ops_model(persona_id, period="latest"):
        return OperationsReadModelEntry(
            identity=OperationsIdentity(persona_id=persona_id, period=period, as_of="2026-07-09T00:00:00Z"),
            data_confidence=OpsDataConfidence.FORMAL,
            performance=OperationsPerformance(),
            sources=[],
            diagnostics=[]
        )

    bff_main._ops_read_model_entry_for_persona = mock_ops_model

    try:
        with _client_with_store(store) as client:
            body = {
                "command": "Observe",
                "target": {"type": "Persona", "id": "persona-test-idempotency"},
                "params": {"persona_id": "persona-test-idempotency"},
                "audit_context": {"reason": "Idempotency testing"},
            }
            headers = {
                "Authorization": OPERATOR_TOKEN,
                "Idempotency-Key": "key-idempotency-ops-006",
                "X-Correlation-Id": "corr-idempotency-ops-006",
            }

            first = client.post("/bff/v1/commands", headers=headers, json=body)
            assert first.status_code == 202, first.text
            assert first.json()["data"]["command_id"]

            second = client.post("/bff/v1/commands", headers=headers, json=body)
            assert second.status_code == 202
            assert second.json()["data"]["command_id"] == first.json()["data"]["command_id"]
    finally:
        bff_main._ops_read_model_entry_for_persona = original_ops_model

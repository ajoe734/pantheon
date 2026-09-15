"""
Tests for LOOP-PROD-PER-001: Persona provisioning, readback, duplicate safety, and terminal failure states.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.personas import service as _persona_service_module
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _schedule_receipt,
)

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {
    "Authorization": OPERATOR_TOKEN,
    "Idempotency-Key": "test-provisioning-idempotency",
}


class _State:
    read_store: Any = None
    persona_service: Any = None


_state = _State()


def _provisioning_read_surface_double():
    """Return typed read ports with the legacy write seams this route exercises.

    Persona provisioning still coordinates writes through ``main.read_store``.
    The production write-owner migration is outside this test-retirement task,
    so the fixture keeps those few write seams local instead of constructing the
    retired aggregate read-store fixture.
    """
    store = create_read_surface_ports()
    personas: dict[str, dict[str, Any]] = {}
    runtime_bindings: dict[str, dict[str, Any]] = {}

    def clone(value: Any) -> Any:
        return json.loads(json.dumps(value))

    def create_persona(
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        created_at: str | None = None,
        archetype: str = "generalist",
        lifecycle_state: str = "draft",
        risk_level: str = "low",
        mandate: str | None = None,
        strategy_family: str | None = None,
        traits: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        required_data_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        timestamp = created_at or utc_now()
        clean_metadata = clone(metadata or {})
        clean_metadata.update(
            {"owner": actor_id, "archetype": archetype, "risk_level": risk_level}
        )
        if traits:
            clean_metadata["traits"] = clone(traits)
        record = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": name,
            "mandate": mandate or archetype,
            "strategy_family": strategy_family or archetype,
            "lifecycle_state": lifecycle_state,
            "status": lifecycle_state,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "required_data_sources": clone(required_data_sources or []),
            "metadata": clean_metadata,
        }
        personas[persona_id] = record
        return clone(record)

    def update_persona(
        persona_id: str,
        *,
        name: str | None = None,
        actor_id: str | None = None,
        updated_at: str | None = None,
        archetype: str | None = None,
        lifecycle_state: str | None = None,
        risk_level: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        existing = personas.get(persona_id)
        if existing is None:
            return None
        record = clone(existing)
        if name is not None:
            record["name"] = name
        if lifecycle_state is not None:
            record["lifecycle_state"] = lifecycle_state
            record["status"] = lifecycle_state
        if archetype is not None:
            record["mandate"] = archetype
            record["strategy_family"] = archetype
        clean_metadata = dict(record.get("metadata") or {})
        clean_metadata.update(clone(metadata or {}))
        if actor_id is not None:
            clean_metadata["owner"] = actor_id
        if archetype is not None:
            clean_metadata["archetype"] = archetype
        if risk_level is not None:
            clean_metadata["risk_level"] = risk_level
        record["metadata"] = clean_metadata
        record["updated_at"] = updated_at or utc_now()
        personas[persona_id] = record
        return clone(record)

    def create_runtime_binding(**payload: Any) -> dict[str, Any]:
        binding_id = str(payload["binding_id"])
        runtime_id = str(payload["runtime_id"])
        params = clone(payload.get("params") or {})
        record = {
            "id": runtime_id,
            "runtime_id": runtime_id,
            "binding_id": binding_id,
            "runtime_binding_id": binding_id,
            "persona_capital_binding_id": binding_id,
            "persona_id": payload.get("persona_id"),
            "deployment_plan_id": payload.get("deployment_plan_id"),
            "plan_id": payload.get("deployment_plan_id"),
            "deployment_mode": payload.get("runtime_kind") or "paper",
            "deployment_stage": payload.get("runtime_kind") or "paper",
            "state": payload.get("state") or "running",
            "status": payload.get("state") or "running",
            "capital_pool_id": params.get("capital_pool_id"),
            "params": params,
        }
        runtime_bindings[binding_id] = record
        return clone(record)

    store.create_persona = create_persona
    store.update_persona = update_persona
    store.get_persona = lambda persona_id: clone(personas.get(str(persona_id))) if persona_id in personas else None
    store.list_personas = lambda **_kwargs: clone(list(personas.values()))
    store.create_runtime_binding = create_runtime_binding
    store.get_runtime_binding = lambda binding_id: clone(runtime_bindings.get(str(binding_id))) if binding_id in runtime_bindings else None
    store.list_runtime_bindings = lambda **_kwargs: clone(list(runtime_bindings.values()))
    store._ensure_local_overlay_records = lambda dataset: (
        runtime_bindings if dataset == "runtime_bindings" else personas if dataset == "personas" else {}
    )
    store.list_authoritative_paper_runtime_monitoring_sessions = lambda: []
    return store


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch):
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-persona-provisioner")
    transport = FakeOwnerTransport()
    _state.transport = transport
    monkeypatch.setattr(_persona_service_module, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(_persona_service_module, "_PersonaOwnerHttpTransport", lambda *args, **kwargs: transport)
    monkeypatch.setattr(_persona_service_module, "_register_persona_cron_required", _schedule_receipt)
    monkeypatch.setattr(
        _persona_service_module,
        "_remove_persona_cron_required",
        lambda persona_id: {
            "persona_id": persona_id,
            "registered": False,
            "removed_ids": [],
        },
    )
    # Mock create_capital_binding
    monkeypatch.setattr(_persona_service_module, "create_capital_binding", lambda payload: {"status": "created"}, raising=False)
    from services.persona.runtime_profile import build_persona_runtime_profile
    monkeypatch.setattr(_persona_service_module, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    
    # Mock _post_json to do nothing and return empty dict
    monkeypatch.setattr(_persona_service_module, "_post_json", lambda *args, **kwargs: {}, raising=False)
    
    # Mock _get_json to raise urllib.error.HTTPError for 404 (not found) by default
    import urllib.error
    from io import BytesIO
    fp = BytesIO(b"")
    mock_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, fp)
    monkeypatch.setattr(_persona_service_module, "_get_json", lambda *args, **kwargs: (_ for _ in ()).throw(mock_404), raising=False)
    
    # Mock _runtime_manager_client
    class MockRuntimeManagerClient:
        def deploy(self, request):
            binding_id = (
                request.get("persona_capital_binding_id")
                or request.get("binding_id")
                or request.get("runtime_binding_id")
                or "test-binding"
            )
            _state.read_store.create_runtime_binding(
                runtime_id=request.get("runtime_id", "test-runtime"),
                name=request.get("metadata", {}).get("name", "test"),
                persona_id=request.get("metadata", {}).get("persona_id", "test"),
                binding_id=binding_id,
                deployment_plan_id=request.get("plan_id", "test-plan"),
                runtime_kind="paper",
                actor_id="test",
                created_at=utc_now(),
                params=request.get("metadata", {}),
                state=request.get("state") or "running",
            )
            return _state.read_store.get_runtime_binding(binding_id)
            
        def get(self, binding_id):
            return _state.read_store.get_runtime_binding(binding_id)
            
        def list_all(self):
            return list((_state.read_store._ensure_local_overlay_records("runtime_bindings") or {}).values())

        def list_by_plan(self, plan_id):
            return [
                binding
                for binding in self.list_all()
                if binding.get("plan_id") == plan_id
            ]
            
    mock_client = MockRuntimeManagerClient()
    monkeypatch.setattr(_persona_service_module, "_runtime_manager_client", lambda: mock_client, raising=False)


def _fresh_client(td: str) -> TestClient:
    read_surface_double = _provisioning_read_surface_double()
    _state.read_store = read_surface_double
    _persona_service_module.persona_write_owner = read_surface_double
    cmd_store = CommandStore(os.path.join(td, "commands.jsonl"))

    persona_svc = PersonaService(
        write_owner=read_surface_double,
        read_store=read_surface_double,
        ranking_write_owner=object(),
        command_store=cmd_store,
    )
    persona_svc._write_owner = read_surface_double
    persona_svc._read_store = read_surface_double
    _state.persona_service = persona_svc

    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Any, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(
        create_personas_router(
            service=persona_svc,
            extract_identity_fn=lambda auth, **kw: extract_identity_stub(auth),
            require_operator_role_fn=require_operator_role,
            require_read_role_fn=require_read_role,
            bff_error_fn=bff_error,
            utc_now_fn=utc_now,
        )
    )
    return TestClient(app)


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
    persona = _state.read_store.get_persona(persona_id)
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
    _persona_service_module._get_json = lambda *_args, **_kwargs: projection
    if getattr(_state, "transport", None) is not None:
        from urllib.parse import quote
        _state.transport.objects[("deployment", f"/api/deployment/plans/{quote(plan_id, safe='')}/projection")] = projection

    class ExactRuntimeManagerClient:
        def get(self, binding_id):
            return authoritative_binding if binding_id == runtime_binding_id else None

        def list_all(self):
            return [authoritative_binding]

        def list_by_plan(self, requested_plan_id):
            return [authoritative_binding] if requested_plan_id == plan_id else []

    _persona_service_module._runtime_manager_client = lambda: ExactRuntimeManagerClient()
    _state.read_store.list_authoritative_paper_runtime_monitoring_sessions = lambda: [
        {
            "session_id": f"session-{persona_id}",
            "runtime_id": runtime_id,
            "binding_id": runtime_binding_id,
            "capital_pool_id": capital_pool_id,
            "status": "running",
            "active": True,
            "last_heartbeat_at": utc_now(),
        }
    ]
    _persona_service_module._register_persona_cron_required = lambda *_args, **_kwargs: {
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
            "observed_at": utc_now(),
        }
    }
    return runtime_binding_id, runtime_id


def test_persona_creation_initial_state_is_provisioning() -> None:
    with tempfile.TemporaryDirectory() as td:
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


def test_persona_provisioning_completes_upon_readback_success() -> None:
    with tempfile.TemporaryDirectory() as td:
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
        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "paper_running"
        assert persisted["metadata"].get("provisioning_reconciliation_state") == "paper_running"


def test_persona_provisioning_fails_on_downstream_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
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
        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert persisted["metadata"].get("provisioning_reconciliation_state") == "provisioning_failed"


def test_persona_provisioning_fails_on_timeout() -> None:
    with tempfile.TemporaryDirectory() as td:
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
        persona = _state.read_store.get_persona(persona_id)
        assert persona is not None
        _state.read_store.update_persona(
            persona_id,
            metadata={"provisioning_readback_started_at": "2026-07-15T00:00:00Z"},
        )
        store = _persona_service_module._PERSONA_PROVISIONING_STORE
        tenant_id = persona["metadata"].get("tenant_id", "pantheon-dev")
        idempotency_key = persona["metadata"].get("provisioning_idempotency_key", "create-trader-d")
        backend_rec = store._backend.records.get((tenant_id, idempotency_key))
        if backend_rec:
            backend_rec.references["provisioning_readback_started_at"] = "2026-07-15T00:00:00Z"

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
        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert persisted["metadata"].get("provisioning_reconciliation_state") == "provisioning_failed"


def test_persona_provisioning_reconcile_remains_provisioning_without_downstream_owners() -> None:
    """Reproduce the recorded failure: repeated reconcile calls stay in provisioning
    when downstream runtime binding and paper worker owners have not produced evidence.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Repeated Polling"},
            headers={**HEADERS, "Idempotency-Key": "create-trader-repeated-poll"},
        )
        assert resp.status_code == 201, resp.text
        persona_id = resp.json()["data"]["id"]

        for attempt in range(5):
            reconciled = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile",
                headers=HEADERS,
            )
            assert reconciled.status_code == 200, reconciled.text
            body = reconciled.json()
            assert body["data"]["state"] == "provisioning"
            assert body["meta"]["lifecycle_state"] == "provisioning"
            assert body["meta"]["status"] == "ok"
            assert body["meta"]["authoritative_readback"]["available"] is False

            persisted = _state.read_store.get_persona(persona_id)
            assert persisted["lifecycle_state"] == "provisioning"


def test_persona_duplicate_create_converges() -> None:
    with tempfile.TemporaryDirectory() as td:
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


def test_persona_duplicate_create_rejects_registry_only_success_projection() -> None:
    with tempfile.TemporaryDirectory() as td:
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
        before = _state.read_store.get_persona(persona_id)
        assert before is not None
        original_created_at = before["created_at"]
        original_readback_started_at = before["metadata"][
            "provisioning_readback_started_at"
        ]

        # 2. Forge only the Persona registry projection.  The durable
        # provisioning ledger is still non-terminal, so this cannot be
        # accepted as paper-running authority.
        _state.read_store.update_persona(persona_id, lifecycle_state="paper_running")

        # 3. Request creation again with same name but new idempotency key
        resp2 = client.post(
            "/bff/personas",
            json={"name": "Trader Safety"},
            headers={**HEADERS, "Idempotency-Key": "create-safety-2"},
        )
        assert resp2.status_code == 201, resp2.text
        
        # 4. Duplicate materialization converges to durable ledger truth.
        persisted_persona = _state.read_store.get_persona(persona_id)
        assert persisted_persona is not None
        assert persisted_persona["lifecycle_state"] == "provisioning"
        assert persisted_persona["created_at"] == original_created_at
        assert persisted_persona["metadata"][
            "provisioning_readback_started_at"
        ] == original_readback_started_at

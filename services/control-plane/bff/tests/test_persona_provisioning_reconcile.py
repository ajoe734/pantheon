"""Unit and regression tests for Persona provisioning reconciliation.

Task: LOOP-L08-PERSONA-PROVISIONING-RECONCILE-001
Acceptance criteria:
1. Reproduce recorded failure: polling reconcile route remains in provisioning_state=provisioning
   across repeated calls when downstream runtime binding and paper worker owners are absent.
2. Determine and verify root cause: state machine does not advance to terminal state because
   executable runtime binding owner (RuntimeManager) and paper execution worker (heartbeat)
   are genuinely outside this contract; reconcile must not fake terminal success.
3. When owners are present, reconcile drives provisioning to real terminal state backed by
   durable readback and persisted via PersonaProvisioningReconciliationMutationPort.
4. Negative paths: real reasons surfaced (timeout, binding failure, saga failure, worker stale)
   with accurate state without fabricated success or default identifiers.
5. Focused and full regression across changed modules.
"""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional
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
from services.control_plane.bff.personas.reconciliation import (
    PersonaProvisioningReconciliationMutationPort,
    PersonaReconciliationMutationError,
)
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
    "Idempotency-Key": "test-reconcile-idempotency",
}


class _TestHarnessState:
    read_store: Any = None
    mutation_port_updates: List[Dict[str, Any]] = []
    transport: Any = None


_state = _TestHarnessState()


class _RecordingPersonaMutationPort:
    def __init__(self, target_store: Any) -> None:
        self.target_store = target_store
        self.updates: List[Dict[str, Any]] = []

    def create_persona(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.target_store.create_persona(*args, **kwargs)

    def update_persona(
        self,
        persona_id: str,
        *,
        lifecycle_state: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        record = {
            "persona_id": persona_id,
            "lifecycle_state": lifecycle_state,
            "metadata": deepcopy(metadata or {}),
        }
        self.updates.append(record)
        _state.mutation_port_updates.append(record)
        return self.target_store.update_persona(
            persona_id,
            lifecycle_state=lifecycle_state,
            metadata=metadata,
            **kwargs,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.target_store, name)


def _provisioning_read_surface_double():
    store = create_read_surface_ports()
    personas: Dict[str, Dict[str, Any]] = {}
    runtime_bindings: Dict[str, Dict[str, Any]] = {}

    def clone(val: Any) -> Any:
        return json.loads(json.dumps(val))

    def create_persona(
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        created_at: Optional[str] = None,
        archetype: str = "generalist",
        lifecycle_state: str = "draft",
        risk_level: str = "low",
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
        traits: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        required_data_sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
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
        name: Optional[str] = None,
        actor_id: Optional[str] = None,
        updated_at: Optional[str] = None,
        archetype: Optional[str] = None,
        lifecycle_state: Optional[str] = None,
        risk_level: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
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

    store.create_persona = create_persona
    store.update_persona = update_persona
    store.get_persona = lambda pid: clone(personas.get(str(pid))) if pid in personas else None
    store.list_personas = lambda **_kw: clone(list(personas.values()))
    store._ensure_local_overlay_records = lambda dataset: (
        personas if dataset == "personas" else {}
    )
    store.list_authoritative_paper_runtime_monitoring_sessions = lambda: []
    return store


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-persona-provisioner")
    transport = FakeOwnerTransport()
    _state.transport = transport
    _state.mutation_port_updates.clear()
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
    monkeypatch.setattr(_persona_service_module, "create_capital_binding", lambda payload: {"status": "created"}, raising=False)
    from services.persona.runtime_profile import build_persona_runtime_profile
    monkeypatch.setattr(_persona_service_module, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    monkeypatch.setattr(_persona_service_module, "_post_json", lambda *args, **kwargs: {}, raising=False)

    import urllib.error
    from io import BytesIO
    mock_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, BytesIO(b""))
    monkeypatch.setattr(_persona_service_module, "_get_json", lambda *args, **kwargs: (_ for _ in ()).throw(mock_404), raising=False)

    class MockEmptyRuntimeManagerClient:
        def list_by_plan(self, plan_id: str):
            return []
        def list_all(self):
            return []
        def get(self, binding_id: str):
            return None

    monkeypatch.setattr(_persona_service_module, "_runtime_manager_client", lambda: MockEmptyRuntimeManagerClient(), raising=False)


def _fresh_client(td: str) -> TestClient:
    read_surface = _provisioning_read_surface_double()
    _state.read_store = read_surface
    mutation_port = _RecordingPersonaMutationPort(read_surface)
    _persona_service_module.persona_write_owner = mutation_port
    cmd_store = CommandStore(os.path.join(td, "commands.jsonl"))

    persona_svc = PersonaService(
        write_owner=mutation_port,
        read_store=read_surface,
        ranking_write_owner=object(),
        command_store=cmd_store,
    )
    persona_svc._write_owner = mutation_port
    persona_svc._read_store = read_surface

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
    worker_heartbeat_age_seconds: int = 5,
    saga_status: str = "completed",
    progress_status: str = "completed",
) -> tuple[str, str]:
    from datetime import datetime, timezone, timedelta
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
        "deployment_saga_status": saga_status,
        "deployment_saga_progress": {"progress_status": progress_status},
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

    now_dt = datetime.now(timezone.utc)
    heartbeat_dt = now_dt - timedelta(seconds=worker_heartbeat_age_seconds)
    heartbeat_iso = heartbeat_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    _state.read_store.list_authoritative_paper_runtime_monitoring_sessions = lambda: [
        {
            "session_id": f"session-{persona_id}",
            "runtime_id": runtime_id,
            "binding_id": runtime_binding_id,
            "capital_pool_id": capital_pool_id,
            "status": "running",
            "active": True,
            "last_heartbeat_at": heartbeat_iso,
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
            "request_id": f"persona-provisioning:{persona_id}:pantheon.persona.first-evaluation",
            "schedule": {"kind": "cron", "expr": "*/15 * * * *"},
            "session_target": persona_id,
            "observed_at": utc_now(),
        }
    }
    return runtime_binding_id, runtime_id


# =========================================================================
# Acceptance Criterion 1: Reproduce recorded failure
# =========================================================================

def test_reconcile_polling_remains_in_provisioning_without_downstream_owners() -> None:
    """Reproduce the failure: polling reconcile repeatedly remains in provisioning.

    In dev deployment, bootstrap_dev_paper_baseline.py polled reconcile 74 times
    over 420s. Because runtime manager had not materialized an active runtime binding
    and paper worker had not started, reconcile returned HTTP 200 with state='provisioning'
    each time without advancing to a terminal state or raising an error.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Reconcile Polling"},
            headers={**HEADERS, "Idempotency-Key": "create-trader-poll-001"},
        )
        assert resp.status_code == 201, resp.text
        persona_id = resp.json()["data"]["id"]

        # Downstream owners are absent: runtime manager client has 0 bindings,
        # paper runtime manager has 0 monitoring sessions.
        poll_count = 10
        observed_states: List[str] = []
        observed_lifecycles: List[str] = []

        for attempt in range(poll_count):
            reconcile_resp = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile",
                headers=HEADERS,
            )
            assert reconcile_resp.status_code == 200, reconcile_resp.text
            body = reconcile_resp.json()
            data_state = body["data"]["state"]
            meta_lifecycle = body["meta"]["lifecycle_state"]
            status_label = body["meta"]["status"]
            readback_avail = body["meta"]["authoritative_readback"]["available"]

            observed_states.append(data_state)
            observed_lifecycles.append(meta_lifecycle)

            # Every poll returns state='provisioning'
            assert data_state == "provisioning", f"Attempt {attempt}: expected provisioning, got {data_state}"
            assert meta_lifecycle == "provisioning", f"Attempt {attempt}: expected provisioning, got {meta_lifecycle}"
            assert status_label == "ok"
            assert readback_avail is False

            # Persona in underlying store is not falsely promoted
            persisted = _state.read_store.get_persona(persona_id)
            assert persisted["lifecycle_state"] == "provisioning"

        # Across all polls, state machine never advanced to terminal success
        assert all(s == "provisioning" for s in observed_states)
        assert all(l == "provisioning" for l in observed_lifecycles)
        assert len(observed_states) == poll_count


# =========================================================================
# Acceptance Criterion 2: Root cause & owner boundary verification
# =========================================================================

def test_reconcile_refuses_to_advance_without_executable_runtime_binding_owner() -> None:
    """Verify that reconcile rightly refuses to advance when RuntimeBinding is missing.

    The executable runtime binding owner (RuntimeManager) is outside this contract.
    Reconcile must NOT fake success or synthesize a runtime binding ID.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Missing Binding Owner"},
            headers={**HEADERS, "Idempotency-Key": "create-missing-binding-owner"},
        )
        assert resp.status_code == 201
        persona_id = resp.json()["data"]["id"]

        reconcile_resp = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconcile_resp.status_code == 200
        body = reconcile_resp.json()
        assert body["data"]["state"] == "provisioning"
        assert body["meta"]["lifecycle_state"] == "provisioning"
        # No fake runtimeBindingId is fabricated
        assert "runtimeBindingId" not in body["data"] or body["data"]["runtimeBindingId"] in (None, "")


def test_reconcile_refuses_to_advance_without_paper_worker_heartbeat_owner() -> None:
    """Verify that reconcile refuses to advance when paper worker heartbeat is missing.

    Even if a RuntimeBinding exists, the paper execution worker must prove liveness
    via an authoritative heartbeat. Without it, reconcile must remain non-terminal.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Missing Worker Owner"},
            headers={**HEADERS, "Idempotency-Key": "create-missing-worker-owner"},
        )
        data = resp.json()["data"]
        persona_id = data["id"]

        # Install authoritative binding, but NO paper worker session
        runtime_binding_id, runtime_id = _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=resp.json()["meta"]["deployment_saga_id"],
            persona_capital_binding_id=resp.json()["meta"]["persona_capital_binding_id"],
        )
        # Clear paper worker monitoring sessions
        _state.read_store.list_authoritative_paper_runtime_monitoring_sessions = lambda: []

        reconcile_resp = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconcile_resp.status_code == 200
        body = reconcile_resp.json()
        assert body["data"]["state"] == "provisioning"
        assert body["meta"]["lifecycle_state"] == "provisioning"


# =========================================================================
# Acceptance Criterion 3: Terminal success backed by durable readback
# =========================================================================

def test_reconcile_drives_terminal_success_when_all_owners_present() -> None:
    """When all downstream owners provide authoritative evidence, reconcile drives
    provisioning to paper_running and persists through the reconciliation mutation port.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Terminal Success"},
            headers={**HEADERS, "Idempotency-Key": "create-trader-terminal-success"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        persona_id = data["id"]

        runtime_binding_id, runtime_id = _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=resp.json()["meta"]["deployment_saga_id"],
            persona_capital_binding_id=resp.json()["meta"]["persona_capital_binding_id"],
        )

        reconciled = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconciled.status_code == 200, reconciled.text
        body = reconciled.json()
        assert body["data"]["state"] == "paper_running"
        assert body["meta"]["lifecycle_state"] == "paper_running"
        authoritative = body["meta"]["authoritative_readback"]
        assert authoritative["available"] is True
        assert authoritative["runtime_binding"]["runtime_binding_id"] == runtime_binding_id
        assert authoritative["paper_worker"]["runtime_id"] == runtime_id

        # Verify mutation port received the terminal transition with state tracking
        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "paper_running"
        assert persisted["metadata"]["provisioning_reconciliation_state"] == "paper_running"

        # Verify mutation port updates audit trail
        matching_updates = [
            u for u in _state.mutation_port_updates
            if u["persona_id"] == persona_id and u["lifecycle_state"] == "paper_running"
        ]
        assert len(matching_updates) >= 1
        assert matching_updates[-1]["metadata"]["provisioning_reconciliation_state"] == "paper_running"


# =========================================================================
# Acceptance Criterion 4: Negative paths with real reasons
# =========================================================================

def test_reconcile_negative_path_timeout() -> None:
    """When provisioning readback times out, reconcile transitions to provisioning_failed
    with reason 'provisioning_timeout', persisted through the mutation port.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Negative Timeout"},
            headers={**HEADERS, "Idempotency-Key": "create-negative-timeout"},
        )
        persona_id = resp.json()["data"]["id"]

        # Backdate provisioning_readback_started_at beyond 600s
        _state.read_store.update_persona(
            persona_id,
            metadata={"provisioning_readback_started_at": "2026-01-01T00:00:00Z"},
        )
        store = _persona_service_module._PERSONA_PROVISIONING_STORE
        tenant_id = _state.read_store.get_persona(persona_id)["metadata"].get("tenant_id", "pantheon-dev")
        rec = store._backend.records.get((tenant_id, "create-negative-timeout"))
        if rec:
            rec.references["provisioning_readback_started_at"] = "2026-01-01T00:00:00Z"

        reconciled = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconciled.status_code == 200, reconciled.text
        body = reconciled.json()
        assert body["data"]["state"] == "failed"
        assert body["meta"]["lifecycle_state"] == "provisioning_failed"

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert "provisioning_timeout" in persisted["metadata"]["provisioning_failure_reason"]
        assert persisted["metadata"]["provisioning_reconciliation_state"] == "provisioning_failed"


def test_reconcile_negative_path_runtime_binding_failed() -> None:
    """When runtime binding is in a failed state, reconcile transitions to
    provisioning_failed with reason 'runtime_binding_failed_or_mismatched'.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Binding Failed"},
            headers={**HEADERS, "Idempotency-Key": "create-binding-failed"},
        )
        data = resp.json()["data"]
        persona_id = data["id"]

        _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=resp.json()["meta"]["deployment_saga_id"],
            persona_capital_binding_id=resp.json()["meta"]["persona_capital_binding_id"],
            binding_state="failed",
        )

        reconciled = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconciled.status_code == 200, reconciled.text
        body = reconciled.json()
        assert body["data"]["state"] == "failed"
        assert body["meta"]["lifecycle_state"] == "provisioning_failed"

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert "runtime_binding_failed_or_mismatched" in persisted["metadata"]["provisioning_failure_reason"]
        assert persisted["metadata"]["provisioning_reconciliation_state"] == "provisioning_failed"


def test_reconcile_negative_path_deployment_saga_failed() -> None:
    """When deployment saga reports failure, reconcile transitions to
    provisioning_failed with reason 'deployment_saga_failed'.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Saga Failed"},
            headers={**HEADERS, "Idempotency-Key": "create-saga-failed"},
        )
        data = resp.json()["data"]
        persona_id = data["id"]

        _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=resp.json()["meta"]["deployment_saga_id"],
            persona_capital_binding_id=resp.json()["meta"]["persona_capital_binding_id"],
            saga_status="failed",
            progress_status="failed",
        )

        reconciled = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconciled.status_code == 200, reconciled.text
        body = reconciled.json()
        assert body["data"]["state"] == "failed"
        assert body["meta"]["lifecycle_state"] == "provisioning_failed"

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert "deployment_saga_failed" in persisted["metadata"]["provisioning_failure_reason"]
        assert persisted["metadata"]["provisioning_reconciliation_state"] == "provisioning_failed"


def test_reconcile_negative_path_stale_paper_worker_heartbeat() -> None:
    """When paper worker heartbeat is stale (> 90s), reconcile transitions to
    provisioning_failed with reason 'paper_worker_failed_stale_or_duplicated'.
    """
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Stale Worker"},
            headers={**HEADERS, "Idempotency-Key": "create-stale-worker"},
        )
        data = resp.json()["data"]
        persona_id = data["id"]

        _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=resp.json()["meta"]["deployment_saga_id"],
            persona_capital_binding_id=resp.json()["meta"]["persona_capital_binding_id"],
            worker_heartbeat_age_seconds=180,  # Stale: > 90s max age
        )

        reconciled = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconciled.status_code == 200, reconciled.text
        body = reconciled.json()
        assert body["data"]["state"] == "failed"
        assert body["meta"]["lifecycle_state"] == "provisioning_failed"

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert "paper_worker_failed_stale_or_duplicated" in persisted["metadata"]["provisioning_failure_reason"]
        assert persisted["metadata"]["provisioning_reconciliation_state"] == "provisioning_failed"


def test_reconcile_mutation_port_validates_required_persona_id_and_supported_state() -> None:
    """Unit validation of PersonaProvisioningReconciliationMutationPort."""
    class DummyMutationPort:
        def update_persona(self, persona_id: str, **kwargs: Any) -> Dict[str, Any]:
            return {"persona_id": persona_id, **kwargs}

    port = PersonaProvisioningReconciliationMutationPort(
        persona_mutation_port=DummyMutationPort(),
    )

    with pytest.raises(PersonaReconciliationMutationError, match="Persona id is required"):
        port.persist_terminal_transition("", lifecycle_state="paper_running", metadata={})

    with pytest.raises(PersonaReconciliationMutationError, match="Unsupported provisioning reconciliation state"):
        port.persist_terminal_transition("persona-1", lifecycle_state="unknown_state", metadata={})

    result = port.persist_terminal_transition(
        "persona-1",
        lifecycle_state="paper_running",
        metadata={"custom": "field"},
    )
    assert result["lifecycle_state"] == "paper_running"
    assert result["metadata"]["provisioning_reconciliation_state"] == "paper_running"
    assert result["metadata"]["custom"] == "field"

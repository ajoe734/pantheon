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
    http_owner: Any = None


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
        if _state.http_owner is not None:
            _state.http_owner.update_persona(
                persona_id, lifecycle_state=lifecycle_state, metadata=metadata
            )
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
    # Strategy-spec reads go to the Registry owner over HTTP (caller auth
    # required); this harness has no Registry, so the owner read is empty.
    store.list_strategy_specs = lambda **_kw: []
    store.get_strategy_spec_detail = lambda *_a, **_kw: None
    return store


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-persona-provisioner")
    transport = FakeOwnerTransport()
    _state.transport = transport
    _state.mutation_port_updates.clear()
    monkeypatch.setattr(_persona_service_module, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(_persona_service_module, "_PersonaOwnerHttpTransport", lambda *args, **kwargs: transport)
    monkeypatch.setattr(_persona_service_module, "_register_persona_cron_required",
        lambda persona_id, pool_id, binding_id, **_kwargs: _schedule_receipt(
            persona_id, pool_id, binding_id
        ),
    )
    monkeypatch.setattr(
        _persona_service_module,
        "_remove_persona_cron_required",
        lambda persona_id, **_kwargs: {
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
    _persona_service_module._current_persona_service.set(persona_svc)

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
        assert resp.status_code == 201, resp.text
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
        assert resp.status_code == 201, resp.text
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


# --- reconciler loop lifespan wiring ---
import asyncio

from fastapi import FastAPI

from services.control_plane.bff.auth.service import ProviderReadinessCache
from services.control_plane.bff.core.lifespan import create_lifespan


async def _ready_probe():
    return {"ready": True, "status": "ready"}


class _NoopMonitor:
    async def start(self):
        return None

    async def stop(self):
        return None


def _run_reconciler_lifespan(monkeypatch, enabled):
    from services.control_plane.bff.personas import service as personas_service

    started = {"n": 0, "cancelled": False}

    async def fake_loop():
        started["n"] += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            started["cancelled"] = True
            raise

    monkeypatch.setattr(personas_service, "_persona_provisioning_reconciler_loop", fake_loop)
    if enabled is None:
        monkeypatch.delenv("PANTHEON_PERSONA_PROVISIONING_RECONCILER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_RECONCILER_ENABLED", enabled)
    lifespan = create_lifespan(
        ProviderReadinessCache(_ready_probe),
        prewarm_jwks=False,
        downstream_health_monitor=_NoopMonitor(),
    )
    app = FastAPI()

    async def scenario():
        async with lifespan(app):
            await asyncio.sleep(0)
            task = app.state.persona_provisioning_reconciler_task
            return task

    task = asyncio.run(scenario())
    return task, started


@pytest.mark.parametrize("enabled", [None, "true"])
def test_lifespan_starts_and_stops_provisioning_reconciler(monkeypatch, enabled):
    task, started = _run_reconciler_lifespan(monkeypatch, enabled)
    assert task is not None
    assert started["n"] == 1
    assert started["cancelled"] is True


def test_lifespan_skips_provisioning_reconciler_when_disabled(monkeypatch):
    task, started = _run_reconciler_lifespan(monkeypatch, "false")
    assert task is None
    assert started["n"] == 0


# =========================================================================
# Background pass vs operator route (PERSONA-PROVISIONING-BACKGROUND-PROMOTE)
# =========================================================================

def _compose_background_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the app-scoped service the way app_factory does for the loop."""
    import types

    monkeypatch.setattr(
        _persona_service_module,
        "_composed_persona_service",
        types.SimpleNamespace(
            get_read_store=lambda: _state.read_store,
            get_write_owner=lambda: _persona_service_module.persona_write_owner,
            get_command_store=lambda: None,
        ),
    )


def _create_persona(client: TestClient, key: str) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    resp = client.post(
        "/bff/personas",
        json={"name": key},
        headers={**HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"], resp.json()["data"], resp.json()["meta"]


def _install_owner_cron_jobs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    persona_id: str,
    meta: Dict[str, Any],
    runtime_id: str,
    runtime_binding_id: str,
) -> None:
    """Serve the first-evaluation row from the cron gateway; no registration."""
    from services.control_plane.cron import persona_cron_registrar as registrar_module

    workflow = registrar_module.WORKFLOW_CATALOG[
        registrar_module.PERSONA_FIRST_EVALUATION_WORKFLOW_ID
    ]
    persona = _state.read_store.get_persona(persona_id)
    job = dict(
        registrar_module.PersonaCronRegistrar()._build_job_patch(
            workflow,
            persona_id,
            f"persona-provisioning:{persona_id}:{workflow.workflow_id}",
            runtime_id=runtime_id,
            runtime_binding_id=runtime_binding_id,
            capital_pool_id=persona["metadata"]["internal_paper_capital_pool_id"],
            persona_capital_binding_id=meta["persona_capital_binding_id"],
        ),
        id="job-first-evaluation",
    )
    monkeypatch.setattr(
        registrar_module.PersonaCronRegistrar, "_get_runtime", lambda self, **_kw: object()
    )
    monkeypatch.setattr(
        registrar_module.PersonaCronRegistrar, "_list_jobs", lambda self, _rt, **_kw: [job]
    )

    def _no_registration(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("batched schedule readback must not register")

    monkeypatch.setattr(_persona_service_module, "_register_persona_cron_required", _no_registration)


def _ready_persona(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, key: str, **readback: Any
) -> tuple[str, Dict[str, Any]]:
    persona_id, data, meta = _create_persona(client, key)
    _install_authoritative_readback(
        persona_id=persona_id,
        plan_id=data["deploymentPlanId"],
        saga_id=meta["deployment_saga_id"],
        persona_capital_binding_id=meta["persona_capital_binding_id"],
        **readback,
    )
    _compose_background_service(monkeypatch)
    return persona_id, meta


def test_background_pass_promotes_ready_persona_from_batched_readbacks(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, meta = _ready_persona(client, monkeypatch, "bg-batched")
        _install_owner_cron_jobs(
            monkeypatch,
            persona_id=persona_id,
            meta=meta,
            runtime_id=f"runtime-{persona_id[-12:]}",
            runtime_binding_id=f"rb-{persona_id[-12:]}",
        )

        bindings, jobs, sessions = _persona_service_module._persona_readback_snapshot()
        assert len(bindings) == 1 and len(jobs) == 1 and len(sessions) == 1

        assert _persona_service_module._reconcile_persona_provisioning_once() == 1
        assert _state.read_store.get_persona(persona_id)["lifecycle_state"] == "paper_running"


def test_background_pass_promotes_when_batch_owner_reads_fail(monkeypatch) -> None:
    """A failed pass-wide read is unknown, not an authoritative empty owner."""
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, _meta = _ready_persona(client, monkeypatch, "bg-batch-failed")
        inner_client = _persona_service_module._runtime_manager_client()

        class _ListAllUnavailable:
            def list_all(self) -> list:
                raise RuntimeError("runtime-manager list timed out")

            def list_by_plan(self, plan_id: str) -> list:
                return inner_client.list_by_plan(plan_id)

        monkeypatch.setattr(
            _persona_service_module, "_runtime_manager_client", lambda: _ListAllUnavailable()
        )
        sessions_read = _state.read_store.list_authoritative_paper_runtime_monitoring_sessions
        calls = {"n": 0}

        def _first_sessions_read_fails() -> list:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("fleet state timed out")
            return sessions_read()

        monkeypatch.setattr(
            _state.read_store,
            "list_authoritative_paper_runtime_monitoring_sessions",
            _first_sessions_read_fails,
        )

        assert _persona_service_module._persona_readback_snapshot()[0] is None
        calls["n"] = 0
        assert _persona_service_module._reconcile_persona_provisioning_once() == 1
        assert _state.read_store.get_persona(persona_id)["lifecycle_state"] == "paper_running"


@pytest.mark.parametrize("mode", ["route", "background"])
def test_background_pass_and_operator_route_agree_on_paper_running(monkeypatch, mode) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, _ = _ready_persona(client, monkeypatch, f"parity-running-{mode}")
        if mode == "route":
            client.post(f"/bff/personas/{persona_id}/provisioning/reconcile", headers=HEADERS)
        else:
            _persona_service_module._reconcile_persona_provisioning_once()
        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "paper_running"
        assert persisted["metadata"]["paper_runtime_state"] == "running"


@pytest.mark.parametrize("mode", ["route", "background"])
def test_background_pass_and_operator_route_agree_on_timeout(monkeypatch, mode) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, _data, _meta = _create_persona(client, f"parity-timeout-{mode}")
        _compose_background_service(monkeypatch)
        _state.read_store.update_persona(
            persona_id, metadata={"provisioning_readback_started_at": "2026-01-01T00:00:00Z"}
        )
        tenant_id = _state.read_store.get_persona(persona_id)["metadata"]["tenant_id"]
        record = _persona_service_module._PERSONA_PROVISIONING_STORE._backend.records[
            (tenant_id, f"parity-timeout-{mode}")
        ]
        record.references["provisioning_readback_started_at"] = "2026-01-01T00:00:00Z"
        if mode == "route":
            client.post(f"/bff/personas/{persona_id}/provisioning/reconcile", headers=HEADERS)
        else:
            _persona_service_module._reconcile_persona_provisioning_once()
        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert "provisioning_timeout" in persisted["metadata"]["provisioning_failure_reason"]


@pytest.mark.parametrize("fault", ["stale_heartbeat", "no_worker", "no_binding"])
def test_background_pass_stays_fail_closed(monkeypatch, fault) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, _ = _ready_persona(
            client,
            monkeypatch,
            f"bg-closed-{fault}",
            **({"worker_heartbeat_age_seconds": 3600} if fault == "stale_heartbeat" else {}),
        )
        if fault == "no_worker":
            monkeypatch.setattr(
                _state.read_store,
                "list_authoritative_paper_runtime_monitoring_sessions",
                lambda: [],
            )
        if fault == "no_binding":

            class _NoBindings:
                def list_all(self) -> list:
                    return []

                def list_by_plan(self, _plan_id: str) -> list:
                    return []

            monkeypatch.setattr(_persona_service_module, "_runtime_manager_client", lambda: _NoBindings())

        _persona_service_module._reconcile_persona_provisioning_once()
        state = _state.read_store.get_persona(persona_id)["lifecycle_state"]
        assert state == ("provisioning_failed" if fault == "stale_heartbeat" else "provisioning")



# =========================================================================
# Background controller pass parity with the operator reconcile route
# =========================================================================

def _create_persona(client: TestClient, key: str) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    resp = client.post(
        "/bff/personas",
        json={"name": f"Trader {key}"},
        headers={**HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"], resp.json()["data"], resp.json()["meta"]


def _background_pass_in_worker_thread(td: str) -> int:
    """Run the controller pass outside any request context, as the lifespan does."""
    import threading

    svc = PersonaService(
        write_owner=_persona_service_module.persona_write_owner,
        read_store=_state.read_store,
        ranking_write_owner=object(),
        command_store=CommandStore(os.path.join(td, "composed-commands.jsonl")),
    )
    previous = _persona_service_module._composed_persona_service
    _persona_service_module._composed_persona_service = svc
    out: Dict[str, Any] = {}

    def run() -> None:
        try:
            out["reconciled"] = _persona_service_module._reconcile_persona_provisioning_once()
        except BaseException as exc:  # noqa: BLE001
            out["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    worker.join()
    _persona_service_module._composed_persona_service = previous
    if "error" in out:
        raise out["error"]
    return out["reconciled"]


@pytest.mark.parametrize("controller", ["route", "background"])
def test_route_and_background_pass_persist_the_same_paper_running_state(controller: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, data, meta = _create_persona(client, f"parity-ok-{controller}")
        binding_id, runtime_id = _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=meta["deployment_saga_id"],
            persona_capital_binding_id=meta["persona_capital_binding_id"],
        )
        if controller == "route":
            reconciled = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile", headers=HEADERS
            )
            assert reconciled.json()["meta"]["lifecycle_state"] == "paper_running"
        else:
            assert _background_pass_in_worker_thread(td) >= 1

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "paper_running"
        metadata = persisted["metadata"]
        assert metadata["provisioning_reconciliation_state"] == "paper_running"
        assert metadata["paper_runtime_state"] == "running"
        assert metadata["runtime_binding_id"] == binding_id
        assert metadata["runtime_id"] == runtime_id
        assert metadata["provisioning_authoritative_readback"]["paper_worker"]["runtime_id"] == runtime_id
        observed = client.get(f"/bff/personas/{persona_id}", headers={"Authorization": OPERATOR_TOKEN})
        assert observed.status_code == 200, observed.text
        assert observed.json()["data"]["lifecycleStatus"] == "paper_running"


def test_background_pass_persists_provisioning_timeout_like_the_route() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, _data, _meta = _create_persona(client, "parity-bg-timeout")
        tenant_id = _state.read_store.get_persona(persona_id)["metadata"].get("tenant_id", "pantheon-dev")
        rec = _persona_service_module._PERSONA_PROVISIONING_STORE._backend.records.get(
            (tenant_id, "parity-bg-timeout")
        )
        rec.references["provisioning_readback_started_at"] = "2026-01-01T00:00:00Z"
        _state.read_store.update_persona(
            persona_id,
            metadata={"provisioning_readback_started_at": "2026-01-01T00:00:00Z"},
        )

        _background_pass_in_worker_thread(td)

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning_failed"
        assert "provisioning_timeout" in persisted["metadata"]["provisioning_failure_reason"]
        assert persisted["metadata"]["provisioning_reconciliation_state"] == "provisioning_failed"


def test_background_pass_reads_plan_directly_when_batch_lags_behind() -> None:
    """A non-empty list_all batch without an active binding must not decide."""
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, data, meta = _create_persona(client, "parity-bg-lagging-batch")
        binding_id, _runtime_id = _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=meta["deployment_saga_id"],
            persona_capital_binding_id=meta["persona_capital_binding_id"],
        )
        exact = _persona_service_module._runtime_manager_client()
        lagging = {**exact.list_all()[0], "status": "pending"}

        class LaggingBatchClient:
            def get(self, requested):
                return exact.get(requested)

            def list_all(self):
                return [lagging]

            def list_by_plan(self, plan):
                return exact.list_by_plan(plan)

        _persona_service_module._runtime_manager_client = lambda: LaggingBatchClient()

        _background_pass_in_worker_thread(td)

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "paper_running"
        assert persisted["metadata"]["runtime_binding_id"] == binding_id


def test_background_pass_records_bounded_structure_only_summary_while_worker_missing() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, data, meta = _create_persona(client, "parity-bg-stale-worker")
        _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=meta["deployment_saga_id"],
            persona_capital_binding_id=meta["persona_capital_binding_id"],
        )
        _state.read_store.list_authoritative_paper_runtime_monitoring_sessions = lambda: []

        _background_pass_in_worker_thread(td)

        persisted = _state.read_store.get_persona(persona_id)
        assert persisted["lifecycle_state"] == "provisioning"
        summary = persisted["metadata"]["provisioning_readback_summary"]
        assert "paper_worker_heartbeat" in summary["unmet"]
        assert "runtime_binding" not in summary["unmet"]
        assert set(summary) == {
            "unmet", "counts", "waiting_reason", "degraded_dependencies", "evaluated_at",
        }
        assert all(isinstance(v, int) for v in summary["counts"].values())
        assert len(json.dumps(summary)) < 600


def test_lifespan_background_loop_promotes_when_worker_session_appears_later(monkeypatch) -> None:
    """Real middleware + ASGI lifespan + real loop and pass; no manual reconcile.

    The paper worker session only appears after the first pass. With the
    process-lifetime lifespan cache the direct per-Persona session read stays
    frozen at the first pass's empty result and the Persona never promotes.
    """
    import asyncio

    from starlette.middleware import Middleware

    from services.control_plane.bff.auth.service import ProviderReadinessCache
    from services.control_plane.bff.core.lifespan import create_lifespan
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware

    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_RECONCILER_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_RECONCILE_SECONDS", "1")
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, meta = _ready_persona(client, monkeypatch, "bg-lifespan-late-worker")
        _install_owner_cron_jobs(
            monkeypatch,
            persona_id=persona_id,
            meta=meta,
            runtime_id=f"runtime-{persona_id[-12:]}",
            runtime_binding_id=f"rb-{persona_id[-12:]}",
        )
        ready_sessions = _state.read_store.list_authoritative_paper_runtime_monitoring_sessions()
        assert len(ready_sessions) == 1
        # Use the real read-surface method (it reads the fleet state through
        # cached_owner_call) fed by a provider that only has the worker later.
        del _state.read_store.list_authoritative_paper_runtime_monitoring_sessions
        appeared = {"on": False}
        _state.read_store._paper_runtime_monitoring_sessions_provider = (
            lambda: list(ready_sessions) if appeared["on"] else []
        )
        real_snapshot = _persona_service_module._persona_readback_snapshot
        passes = {"n": 0}

        def snapshot_counting_passes():
            passes["n"] += 1
            if passes["n"] == 2:
                appeared["on"] = True  # worker joins only after the first pass
            return real_snapshot()

        monkeypatch.setattr(
            _persona_service_module, "_persona_readback_snapshot", snapshot_counting_passes
        )

        async def probe():
            return {}

        lp = create_lifespan(
            ProviderReadinessCache(probe=probe, stale_after_seconds=10.0),
            prewarm_jwks=False,
            downstream_health_monitor=None,
        )
        app = FastAPI(lifespan=lp)
        app.user_middleware.append(Middleware(OwnerReadContextMiddleware))
        app.middleware_stack = None

        async def drive():
            # Drive the lifespan scope through the real middleware stack.
            queue: asyncio.Queue = asyncio.Queue()
            sent: list = []

            async def receive():
                return await queue.get()

            async def send(message):
                sent.append(message["type"])

            task = asyncio.create_task(app({"type": "lifespan", "asgi": {}}, receive, send))
            await queue.put({"type": "lifespan.startup"})
            for _ in range(100):
                if "lifespan.startup.complete" in sent:
                    break
                await asyncio.sleep(0.05)
            assert "lifespan.startup.complete" in sent
            for _ in range(120):
                if _state.read_store.get_persona(persona_id)["lifecycle_state"] == "paper_running":
                    break
                await asyncio.sleep(0.1)
            await queue.put({"type": "lifespan.shutdown"})
            await task

        asyncio.run(drive())

        assert passes["n"] >= 2
        assert _state.read_store.get_persona(persona_id)["lifecycle_state"] == "paper_running"


# --- terminal transition idempotency (PERSONA-PROVISIONING-TERMINAL-IDEMPOTENT-20261103) ---
import io
import threading
import urllib.error

from services.control_plane.bff.ports.persona_write_owner import (
    PersonaRegistryHttpWritePort,
    PersonaWriteConflict,
)


class _Resp:
    def __init__(self, payload: Any) -> None:
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakePersonaOwnerHttp:
    """Persona owner HTTP double with the real same-state/undeclared guard."""

    def __init__(self, persona_id: str, *, barrier: Optional[threading.Barrier] = None) -> None:
        self.persona_id = persona_id
        self.lock = threading.Lock()
        self.state = "draft"
        self.metadata: Dict[str, Any] = {}
        self.lifecycle_ok = 0
        self.lifecycle_rejected = 0
        self.barrier = barrier
        self.force_reject_with_state: Optional[str] = None

    def _persona(self) -> Dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "lifecycle_state": self.state,
            "metadata": dict(self.metadata),
            "tenant_id": "t",
        }

    def _error(self, request: Any, code: int, detail: str) -> None:
        raise urllib.error.HTTPError(
            request.full_url, code, "err", {}, io.BytesIO(json.dumps({"detail": detail}).encode())
        )

    def __call__(self, request: Any, timeout: float = 1.0) -> Any:
        path = request.full_url.split("?")[0]
        body = json.loads(request.data.decode()) if request.data else {}
        if request.get_method() == "GET":
            with self.lock:
                return _Resp(self._persona())
        if path.endswith("/lifecycle"):
            if self.barrier is not None:
                self.barrier.wait(timeout=10)
            with self.lock:
                target = body["target_state"]
                if self.force_reject_with_state is not None:
                    self.state = self.force_reject_with_state
                    self.lifecycle_rejected += 1
                    self._error(request, 422, f"invalid lifecycle transition {target!r}")
                if self.state == target or (self.state, target) != ("draft", "research_only"):
                    self.lifecycle_rejected += 1
                    self._error(
                        request, 422, f"invalid lifecycle transition '{self.state}' -> '{target}'"
                    )
                self.state = target
                self.lifecycle_ok += 1
                return _Resp(self._persona())
        with self.lock:
            self.metadata.update(body.get("metadata") or {})
            return _Resp(self._persona())


class _RaiseRecordingPort:
    """Records exceptions the owner port raises (the background pass swallows them)."""

    def __init__(self, port: Any) -> None:
        self._port = port
        self.errors: List[BaseException] = []

    def update_persona(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._port.update_persona(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            self.errors.append(exc)
            raise


def _http_port(owner: _FakePersonaOwnerHttp) -> PersonaRegistryHttpWritePort:
    return PersonaRegistryHttpWritePort(
        base_url="http://owner.invalid", service_token="tok", opener=owner
    )


def test_terminal_transition_converges_when_lifecycle_race_is_lost() -> None:
    owner = _FakePersonaOwnerHttp("p1")
    port = _http_port(owner)
    real_get = port.get_persona
    calls = {"n": 0}

    def stale_first_read(persona_id: str) -> Any:
        calls["n"] += 1
        value = real_get(persona_id)
        if calls["n"] == 1:
            return {**value, "lifecycle_state": "draft"}
        return value

    port.get_persona = stale_first_read  # type: ignore[method-assign]
    owner.state = "research_only"  # the other controller already promoted it
    result = port.update_persona("p1", lifecycle_state="paper_running", metadata={"k": "v"})
    assert result is not None and result["lifecycle_state"] == "research_only"
    assert owner.lifecycle_ok == 0 and owner.lifecycle_rejected == 1


def test_terminal_transition_propagates_rejection_when_not_the_race() -> None:
    owner = _FakePersonaOwnerHttp("p1")
    owner.force_reject_with_state = "draft"
    port = _http_port(owner)
    with pytest.raises(PersonaWriteConflict, match="invalid lifecycle transition"):
        port.update_persona("p1", lifecycle_state="paper_running", metadata={})


def test_route_returns_200_when_owner_rejects_same_state_transition() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, data, meta = _create_persona(client, "idem-route")
        _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=meta["deployment_saga_id"],
            persona_capital_binding_id=meta["persona_capital_binding_id"],
        )
        owner = _FakePersonaOwnerHttp(persona_id)
        owner.force_reject_with_state = "research_only"
        _state.http_owner = _http_port(owner)
        try:
            resp = client.post(
                f"/bff/personas/{persona_id}/provisioning/reconcile", headers=HEADERS
            )
        finally:
            _state.http_owner = None
        assert resp.status_code == 200, resp.text
        assert resp.json()["meta"]["lifecycle_state"] == "paper_running"
        assert _state.read_store.get_persona(persona_id)["lifecycle_state"] == "paper_running"


def test_route_and_background_pass_race_to_terminal_write_without_error() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        persona_id, data, meta = _create_persona(client, "idem-race")
        _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=meta["deployment_saga_id"],
            persona_capital_binding_id=meta["persona_capital_binding_id"],
        )
        owner = _FakePersonaOwnerHttp(persona_id, barrier=threading.Barrier(2))
        recorder = _RaiseRecordingPort(_http_port(owner))
        _state.http_owner = recorder
        out: Dict[str, Any] = {}

        def route() -> None:
            try:
                out["route"] = client.post(
                    f"/bff/personas/{persona_id}/provisioning/reconcile", headers=HEADERS
                )
            except BaseException as exc:  # noqa: BLE001
                out["route_error"] = exc

        def background() -> None:
            try:
                out["bg"] = _persona_service_module._reconcile_persona_provisioning_once()
            except BaseException as exc:  # noqa: BLE001
                out["bg_error"] = exc

        previous = _persona_service_module._composed_persona_service
        try:
            import types

            _persona_service_module._composed_persona_service = types.SimpleNamespace(
                get_read_store=lambda: _state.read_store,
                get_write_owner=lambda: _persona_service_module.persona_write_owner,
                get_command_store=lambda: None,
            )
            threads = [threading.Thread(target=route), threading.Thread(target=background)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        finally:
            _persona_service_module._composed_persona_service = previous
            _state.http_owner = None
        assert "route_error" not in out and "bg_error" not in out, out
        assert out["route"].status_code == 200, out["route"].text
        assert recorder.errors == []
        assert owner.state == "research_only"
        assert owner.lifecycle_ok == 1
        assert _state.read_store.get_persona(persona_id)["lifecycle_state"] == "paper_running"


# --- BFF-PERSONA-PAPER-ADVISORY-20261106 ---



from services.control_plane.bff.agora.interaction.router import _environment_allowed
from services.control_plane.bff.personas.service import (
    PersonaAdvisoryAdmissionError,
    PersonaAdvisoryAdmissionPort,
    paper_advisory_ineligibility,
    paper_advisory_snapshot_id,
)

class FakeOwner:
    def __init__(self, persona):
        self.persona = persona
        self.snapshots = {}
        self.updates = 0

    def upsert_persona_capability_snapshot(self, **kw):
        sem = {k: v for k, v in kw.items() if k != "generated_at"}
        cur = self.snapshots.get(kw["snapshot_id"])
        if cur is not None and cur["sem"] != sem:
            raise RuntimeError("conflict")
        self.snapshots.setdefault(kw["snapshot_id"], {"sem": sem, "capabilities": kw["capabilities"]})
        return {"snapshot_id": kw["snapshot_id"], "capabilities": kw["capabilities"]}

    def update_persona(self, pid, *, metadata=None, **_):
        self.updates += 1
        self.persona["metadata"] = {**self.persona.get("metadata", {}), **metadata}
        return self.persona


def _paper():
    return {"persona_id": "p1", "tenant_id": "t", "lifecycle_state": "research_only", "status": "active",
            "metadata": {"provisioning_reconciliation_state": "paper_running"}}


def test_missing_ceiling_reproduces_then_admission_fixes_and_is_stable():
    p = _paper()
    assert not _environment_allowed(p, "paper")  # reproduction: no ceiling
    owner = FakeOwner(p)
    port = PersonaAdvisoryAdmissionPort(persona_mutation_port=owner)
    first = port.ensure("p1", p, generated_at="t1")
    assert first["capabilities"] == ["persona_opinion"]
    assert first["capability_snapshot_id"] == paper_advisory_snapshot_id("p1")
    assert _environment_allowed(p, "paper") and not _environment_allowed(p, "canary")
    assert not _environment_allowed(p, "live")
    port.ensure("p1", p, generated_at="t2")  # repeat: stable, no extra write
    assert owner.updates == 1 and len(owner.snapshots) == 1


@pytest.mark.parametrize("patch", [
    {"lifecycle_state": "retired"},
    {"status": "suspended"},
    {"lifecycle_state": "draft"},
    {"lifecycle_state": "research_only", "metadata": {}},
])
def test_ineligible_personas_refused_without_writes(patch):
    p = {**_paper(), **patch}
    owner = FakeOwner(p)
    with pytest.raises(PersonaAdvisoryAdmissionError):
        PersonaAdvisoryAdmissionPort(persona_mutation_port=owner).ensure("p1", p, generated_at="t")
    assert not owner.snapshots and owner.updates == 0
    assert paper_advisory_ineligibility(p)


def test_higher_existing_ceiling_is_not_touched():
    p = _paper()
    p["metadata"]["environment_ceiling"] = "live"
    owner = FakeOwner(p)
    with pytest.raises(PersonaAdvisoryAdmissionError):
        PersonaAdvisoryAdmissionPort(persona_mutation_port=owner).ensure("p1", p, generated_at="t")
    assert owner.updates == 0


def test_reconcile_preserves_provisioned_expression_policy_and_pool_risk_policy() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.post(
            "/bff/personas",
            json={"name": "Trader Expression Policy Reconcile"},
            headers={**HEADERS, "Idempotency-Key": "create-trader-expression-policy-reconcile"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        meta = resp.json()["meta"]
        persona_id = data["id"]

        _install_authoritative_readback(
            persona_id=persona_id,
            plan_id=data["deploymentPlanId"],
            saga_id=meta["deployment_saga_id"],
            persona_capital_binding_id=meta["persona_capital_binding_id"],
        )

        reconciled = client.post(
            f"/bff/personas/{persona_id}/provisioning/reconcile",
            headers=HEADERS,
        )
        assert reconciled.status_code == 200, reconciled.text
        assert reconciled.json()["data"]["state"] == "paper_running"

        records = _persona_service_module._PERSONA_PROVISIONING_STORE.list_all()
        assert len(records) == 1
        refs = records[0].references
        forward_artifact = refs["strategy_artifact_approved"]["entry"]["metadata"]["strategy_artifact"]
        baseline_artifact = refs["baseline_strategy_artifact_approved"]["entry"]["metadata"]["strategy_artifact"]
        forward_spec = refs["strategy_spec_approved"]["entry"]["metadata"]["strategy_spec"]
        baseline_spec = refs["baseline_strategy_spec_approved"]["entry"]["metadata"]["strategy_spec"]
        pool_risk = refs["capital_pool"]["metadata"]["risk_policy"]

        assert forward_artifact["expression_policy"]["kind"] == "notional_target_v1"
        assert forward_artifact["expression_policy"]["version"] == "1.0"
        assert [c["parameter_key"] for c in forward_artifact["mutation_surface"]["controls"]] == [
            "lookback_bars",
            "momentum_threshold",
            "strength_scale_k",
            "strength_floor_momentum",
            "lever_min_strength",
            "notional_fraction_at_full_strength",
        ]
        assert "expression_policy" not in baseline_artifact
        assert forward_spec["market_scope"]["asset_classes"] == ["equity", "future"]
        assert baseline_spec["market_scope"]["asset_classes"] == ["equity"]
        assert pool_risk["max_leverage"] == 2.0
        assert pool_risk["gross_limit"] == 2.0
        assert pool_risk["allowed_asset_classes"] == ["equity", "future"]


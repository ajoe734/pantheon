"""Isolated HTTP-port/real-owner contracts, not hosted acceptance evidence."""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.ports.persona_write_owner import (
    PersonaRegistryHttpWritePort,
    PersonaWriteConflict,
    PersonaWriteOwnerUnavailable,
)
from services.persona.write_owner import (
    PersistentCapabilitySnapshotOwner,
    PersistentPersonaOwner,
    create_app,
)
from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    ProvisioningConflict,
)
from services.control_plane.bff.personas import service


@pytest.fixture
def owner_boundary(monkeypatch, tmp_path):
    token = "isolated-persona-contract-service-token"
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", token)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", "operator-bff")
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    path = tmp_path / "personas.json"
    app = create_app(
        owner=PersistentPersonaOwner.from_json_path(path),
        capability_owner=PersistentCapabilitySnapshotOwner.from_json_path(
            tmp_path / "capabilities.json"
        ),
    )
    calls = []
    with TestClient(app) as client:
        def opener(request, *, timeout):
            # Only replace the socket transport. Real routing, authentication,
            # request validation, lifecycle policy and durable storage run.
            payload = json.loads(request.data) if request.data else None
            response = client.request(
                request.get_method(), request.full_url,
                content=request.data, headers=dict(request.header_items()),
            )
            calls.append((request.get_method(), request.full_url, payload, response.status_code))
            if response.is_error:
                raise HTTPError(request.full_url, response.status_code, "owner rejected",
                                response.headers, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        port = PersonaRegistryHttpWritePort(
            base_url="http://testserver", service_token=token,
            service_actor_id="operator-bff", opener=opener,
        )
        yield port, client, path, calls


def create(port, *, state="provisioning", tenant="tenant-contract"):
    return port.create_persona(
        persona_id="persona-contract", name="Isolated paper contract",
        actor_id="operator-contract", lifecycle_state=state,
        metadata={
            "tenant_id": tenant, "agora_user_id": "operator-contract",
            "persona_class": "paper", "provisioning_state": state,
            "paper_runtime_state": "provisioning", "live_capital_enabled": False,
        },
    )


@pytest.mark.parametrize("state", ["draft", "provisioning", "provisioning_failed", "paper_running"])
def test_create_uses_real_owner_draft_and_preserves_provisioning_metadata(owner_boundary, state):
    port, client, path, calls = owner_boundary
    result = create(port, state=state)
    assert result["lifecycle_state"] == "draft"
    assert result["metadata"]["provisioning_state"] == state
    assert result["metadata"]["live_capital_enabled"] is False
    assert result["owner"] == "operator-contract"
    assert result["created_by"] == "operator-bff"
    assert calls[0][2]["lifecycle_state"] == "draft"
    assert calls[0][3] == 201
    reloaded = PersistentPersonaOwner.from_json_path(path).get("persona-contract")
    assert reloaded.lifecycle_state == "draft"
    assert reloaded.metadata["tenant_id"] == "tenant-contract"
    # Metadata preservation must not be mislabeled as new governed tenant
    # authority: that separate, pre-existing adapter gap is not this hotfix.
    assert reloaded.tenant_id is None


@pytest.mark.parametrize("state", ["research_only", "consultable", "paper_owner", "live_owner", "typo"])
def test_create_does_not_translate_governed_or_unknown_states(owner_boundary, state):
    port, _client, _path, calls = owner_boundary
    with pytest.raises(PersonaWriteConflict, match="must start in 'draft'"):
        create(port, state=state)
    assert calls[-1][3] == 422
    assert port.get_persona("persona-contract") is None


def test_real_owner_still_rejects_direct_provisioning_create(owner_boundary):
    port, client, _path, _calls = owner_boundary
    result = client.post("/api/personas", json={
        "actor_id": "operator-bff", "name": "Rejected",
        "mandate": "simulation", "lifecycle_state": "provisioning",
    }, headers={"Authorization": "Bearer isolated-persona-contract-service-token"})
    assert result.status_code == 422
    assert "must start in 'draft'" in result.json()["detail"]


def test_retry_reads_canonical_record_without_downgrade_and_reloads_in_new_process(owner_boundary):
    port, _client, path, calls = owner_boundary
    create(port)
    updated = port.update_persona("persona-contract", lifecycle_state="paper_running")
    assert updated["lifecycle_state"] == "research_only"
    replay = create(port)
    assert replay["lifecycle_state"] == "research_only"
    assert [row[3] for row in calls if row[0] == "POST"] == [201, 409]
    assert sum(row[1].endswith("/lifecycle") for row in calls) == 1
    readback = subprocess.run([
        sys.executable, "-c",
        "from services.persona.write_owner import PersistentPersonaOwner; "
        "import sys; p=PersistentPersonaOwner.from_json_path(sys.argv[1]).get('persona-contract'); "
        "assert p.lifecycle_state=='research_only'; "
        "assert p.metadata['tenant_id']=='tenant-contract'; "
        "assert p.metadata['provisioning_state']=='provisioning'",
        str(path),
    ], cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=30)
    assert readback.returncode == 0, readback.stderr


def test_conflicting_tenant_retry_is_rejected(owner_boundary):
    port, _client, path, _calls = owner_boundary
    create(port)
    with pytest.raises(PersonaWriteConflict, match="another owner scope"):
        create(port, tenant="foreign-tenant")
    assert PersistentPersonaOwner.from_json_path(path).get("persona-contract").metadata[
        "tenant_id"
    ] == "tenant-contract"


def test_create_preserves_service_authentication_and_actor_binding(owner_boundary):
    port, _client, path, _calls = owner_boundary
    port._service_actor_id = "forged-actor"
    with pytest.raises(PersonaWriteOwnerUnavailable, match="authenticated actor"):
        create(port, state="draft")
    port._service_actor_id = "operator-bff"
    port._service_token = "invalid-contract-token"
    with pytest.raises(PersonaWriteOwnerUnavailable):
        create(port, state="draft")
    assert PersistentPersonaOwner.from_json_path(path).list() == []


@pytest.fixture
def projection_boundary(owner_boundary, monkeypatch):
    """Real JSON owner plus the isolated in-memory ledger protocol (not Postgres evidence)."""
    port, _client, _path, _calls = owner_boundary
    store = MemoryPersonaProvisioningStore()
    record, _ = store.reserve(
        tenant_id="tenant-contract", idempotency_key="contract-create-v1",
        request_hash="contract-request-hash", normalized_name="isolated paper contract",
        persona_id="persona-contract",
        request_payload={"name": "Isolated paper contract", "requested_by": "operator-contract"},
    )
    monkeypatch.setattr(service, "read_store", SimpleNamespace(
        get_persona=port.get_persona, list_personas=port.list_personas,
    ))
    monkeypatch.setattr(service, "persona_write_owner", port)
    monkeypatch.setattr(service, "_PERSONA_PROVISIONING_STORE", store)
    return port, store, record


def project(record, *, mutate=False):
    return service._persona_record_for_provisioning(
        record, payload=record.request_payload, owner="operator-contract", mutate_store=mutate,
    )[0]


def checkpoint(store, record, state, *, complete_readback=False):
    leased = store.acquire(record.tenant_id, record.idempotency_key,
                           lease_owner="isolated-contract", lease_seconds=60)
    assert leased is not None
    leased.state = state
    leased.current_step = "readback_verified" if state == "succeeded" else "schedule_registered"
    if complete_readback:
        # Explicit simulation fixture: proves the projection contract only,
        # not execution of a real hosted RuntimeBinding or paper worker.
        leased.references = {
            "runtime_binding_id": "rb-contract", "runtime_id": "runtime-contract",
            "authoritative_readback": {"fixture": "simulation"},
        }
        leased.result = {"paper_running": True, "status": "paper_running"}
    return store.release(leased, lease_owner="isolated-contract")


def test_real_create_and_pending_reload_remain_controller_eligible(projection_boundary, owner_boundary, monkeypatch):
    port, store, record = projection_boundary
    _port, _client, path, _calls = owner_boundary
    projected = project(record, mutate=True)
    assert projected["lifecycle_state"] == "provisioning"
    assert projected["owner_lifecycle_state"] == "draft"
    assert PersistentPersonaOwner.from_json_path(path).get(record.persona_id).lifecycle_state == "draft"
    # A fresh ledger adapter reads the same backend. The owner independently
    # reloads actual JSON; this is not a claim of Postgres process restart.
    fresh = MemoryPersonaProvisioningStore(backend=store.backend)
    monkeypatch.setattr(service, "_PERSONA_PROVISIONING_STORE", fresh)
    observed = []
    monkeypatch.setattr(service, "_persona_readback_snapshot", lambda: ({}, None, []))
    monkeypatch.setattr(service, "_evaluate_persona_provisioning_status",
                        lambda pid, raw, **kwargs: observed.append((pid, raw)))
    assert service._reconcile_persona_provisioning_once() == 1
    assert observed[0][1]["lifecycle_state"] == "provisioning"
    assert observed[0][1]["owner_lifecycle_state"] == "draft"
    assert port.get_persona(record.persona_id)["lifecycle_state"] == "draft"


def test_actual_reconciler_keeps_draft_owner_pending_without_runtime_evidence(projection_boundary, monkeypatch):
    port, store, record = projection_boundary
    project(record, mutate=True)
    monkeypatch.setattr(service, "_persona_readback_snapshot", lambda: ({}, None, []))
    # Isolate the Deployment HTTP read. The actual controller/evaluator,
    # Persona port/owner and ledger protocol still run; no runtime is claimed.
    monkeypatch.setattr(service, "_get_json", lambda *args, **kwargs: {})
    assert service._reconcile_persona_provisioning_once() == 1
    assert port.get_persona(record.persona_id)["lifecycle_state"] == "draft"
    projected, = service._list_persona_records()
    assert projected["lifecycle_state"] == "provisioning"
    assert projected["metadata"]["paper_runtime_state"] == "provisioning"
    assert store.get(record.tenant_id, record.idempotency_key).state != "succeeded"


@pytest.mark.parametrize("state,complete,expected", [
    ("provisioning", False, "provisioning"),
    ("failed", False, "provisioning_failed"),
    ("compensated", False, "provisioning_failed"),
    ("succeeded", False, "provisioning"),
    ("succeeded", True, "paper_running"),
])
def test_reload_uses_ledger_progress_not_stale_owner_metadata(projection_boundary, monkeypatch, state, complete, expected):
    port, store, record = projection_boundary
    project(record, mutate=True)
    # Request/owner metadata is deliberately stale and overclaims success.
    port.update_persona(record.persona_id, metadata={
        "provisioning_state": "succeeded", "paper_runtime_state": "running",
        "unrelated_owner_field": "preserved",
    })
    checkpoint(store, record, state, complete_readback=complete)
    monkeypatch.setattr(service, "_PERSONA_PROVISIONING_STORE",
                        MemoryPersonaProvisioningStore(backend=store.backend))
    projected, = service._list_persona_records("tenant-contract")
    assert projected["lifecycle_state"] == expected
    assert projected["owner_lifecycle_state"] == "draft"
    assert projected["metadata"]["provisioning_state"] == state
    assert projected["metadata"]["unrelated_owner_field"] == "preserved"
    assert (projected["metadata"]["paper_runtime_state"] == "running") == (expected == "paper_running")
    assert port.get_persona(record.persona_id)["lifecycle_state"] == "draft"


def test_succeeded_replay_preserves_governed_transition(projection_boundary, owner_boundary):
    port, store, record = projection_boundary
    project(record, mutate=True)
    terminal = checkpoint(store, record, "succeeded", complete_readback=True)
    projected = project(terminal, mutate=True)
    assert projected["lifecycle_state"] == "paper_running"
    assert projected["owner_lifecycle_state"] == "research_only"
    assert project(terminal, mutate=True)["owner_lifecycle_state"] == "research_only"
    _port, _client, path, calls = owner_boundary
    assert sum(row[1].endswith("/lifecycle") for row in calls) == 1
    assert PersistentPersonaOwner.from_json_path(path).get(record.persona_id).lifecycle_state == "research_only"


@pytest.mark.parametrize("field,value", [
    ("tenant_id", "foreign-tenant"), ("tenant_id", None),
    ("name", "Foreign name"), ("persona_id", "foreign-id"),
])
def test_foreign_or_missing_scope_cannot_be_relabelled(projection_boundary, monkeypatch, field, value):
    port, _store, record = projection_boundary
    project(record, mutate=True)
    raw = port.get_persona(record.persona_id)
    raw[field] = value
    if field == "tenant_id":
        raw["metadata"]["tenant_id"] = value
        raw["tenantId"] = value
        raw["metadata"]["tenantId"] = value
    monkeypatch.setattr(service, "read_store", SimpleNamespace(
        get_persona=lambda pid: raw, list_personas=lambda: [raw],
    ))
    for mutate in (False, True):
        with pytest.raises(ProvisioningConflict):
            project(record, mutate=mutate)
    listed = service._list_persona_records()
    assert listed[0]["lifecycle_state"] == "draft"
    assert "owner_lifecycle_state" not in listed[0]


@pytest.mark.parametrize("state", ["frozen", "retired", "consultable", "paper_owner", "live_owner"])
def test_projection_does_not_reactivate_or_downgrade_governed_owner(projection_boundary, monkeypatch, state):
    port, store, record = projection_boundary
    project(record, mutate=True)
    canonical = {**port.get_persona(record.persona_id), "lifecycle_state": state}
    # Later governed states are read-only fixtures, not forged owner transitions.
    monkeypatch.setattr(service, "read_store", SimpleNamespace(
        get_persona=lambda pid: canonical, list_personas=lambda: [canonical],
    ))
    checkpoint(store, record, "succeeded", complete_readback=True)
    projected, = service._list_persona_records()
    assert projected["lifecycle_state"] == state
    monkeypatch.setattr(service, "_persona_readback_snapshot", lambda: ({}, None, []))
    assert service._reconcile_persona_provisioning_once() == 0


def test_ordinary_draft_without_ledger_is_not_reconciled(owner_boundary, monkeypatch):
    port, _client, _path, _calls = owner_boundary
    create(port, state="draft")
    monkeypatch.setattr(service, "read_store", SimpleNamespace(
        get_persona=port.get_persona, list_personas=port.list_personas,
    ))
    monkeypatch.setattr(service, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(service, "_persona_readback_snapshot", lambda: ({}, None, []))
    assert service._list_persona_records()[0]["lifecycle_state"] == "draft"
    assert service._reconcile_persona_provisioning_once() == 0

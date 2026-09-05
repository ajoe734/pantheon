"""Unit tests for AG-BE-000: Agora BFF router package skeleton.

Verifies:
- create_agora_router() mounts without errors and does not break existing routes
- GET /bff/agora/me returns the §18 envelope ({data, meta}) with capability scope
- GET /bff/agora/capabilities returns the filtered capability manifest
- POST /bff/agora/servant/ensure provisions a governed user-private servant
- Unauthenticated requests to new endpoints return HTTP 401
- Package imports are consistent (models, router, sub-module factories)
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

_OPERATOR_AUTH = "Bearer agora-test-user:operator"
_NO_AUTH = None


def _create_test_agora_store(*, allow_fallback: bool = True):
    personas_map: dict[str, dict] = {}
    snapshots_map: dict[str, dict] = {}

    store = create_in_memory_read_surface_ports()

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
        traits: dict | None = None,
        metadata: dict | None = None,
        required_data_sources: list | None = None,
    ) -> dict:
        timestamp = created_at or "2026-08-29T00:00:00Z"
        clean_metadata = dict(metadata or {})
        clean_metadata.update({
            "owner": actor_id,
            "archetype": archetype,
            "risk_level": risk_level,
        })
        if traits:
            clean_metadata["traits"] = dict(traits)
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
            "tenant_id": "pantheon-dev",
            "tenantId": "pantheon-dev",
            "required_data_sources": list(required_data_sources or []),
            "metadata": clean_metadata,
            "canonicalWriteAuthority": "persona_registry_service",
            "persistenceMode": "bff_local_dev_store",
        }
        personas_map[persona_id] = record
        return record

    def update_persona(
        persona_id: str,
        *,
        name: str | None = None,
        actor_id: str | None = None,
        updated_at: str | None = None,
        archetype: str | None = None,
        lifecycle_state: str | None = None,
        risk_level: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        if not persona_id or persona_id not in personas_map:
            return None
        record = dict(personas_map[persona_id])
        timestamp = updated_at or "2026-08-29T00:00:00Z"
        if name is not None:
            record["name"] = name
        if lifecycle_state is not None:
            record["lifecycle_state"] = lifecycle_state
            record["status"] = lifecycle_state
        if archetype is not None:
            record["mandate"] = archetype
            record["strategy_family"] = archetype
        record["updated_at"] = timestamp
        clean_metadata = dict(record.get("metadata") or {})
        if metadata:
            clean_metadata.update(metadata)
        if actor_id is not None:
            clean_metadata["owner"] = actor_id
        if archetype is not None:
            clean_metadata["archetype"] = archetype
        if risk_level is not None:
            clean_metadata["risk_level"] = risk_level
        record["metadata"] = clean_metadata
        personas_map[persona_id] = record
        return record

    def upsert_persona(record: dict) -> dict:
        pid = record.get("persona_id") or record.get("id")
        personas_map[pid] = dict(record)
        return personas_map[pid]

    def get_persona(pid: str) -> dict | None:
        return personas_map.get(pid)

    def list_personas(**kw) -> list[dict]:
        res = list(personas_map.values())
        if kw.get("lifecycle_state"):
            res = [p for p in res if p.get("lifecycle_state") == kw["lifecycle_state"]]
        return res

    def upsert_persona_capability_snapshot(
        snapshot_id: str,
        persona_id: str,
        capabilities: list[str],
        generated_at: str,
        source_refs: list[str] | None = None,
        metadata: dict | None = None,
        **kw,
    ) -> dict:
        snap = {
            "snapshot_id": snapshot_id,
            "persona_id": persona_id,
            "capabilities": list(capabilities),
            "allowed_capabilities": list(capabilities),
            "generated_at": generated_at,
            "source_refs": source_refs or [],
            "metadata": dict(metadata or {}),
        }
        snapshots_map[snapshot_id] = snap
        return snap

    def get_capability_snapshot(snap_id: str) -> dict | None:
        return snapshots_map.get(snap_id)

    def get_capability_snapshot_for_persona(pid: str) -> dict | None:
        for snap in snapshots_map.values():
            if snap.get("persona_id") == pid:
                return snap
        return None

    store.create_persona = create_persona
    store.update_persona = update_persona
    store.upsert_persona = upsert_persona
    store.get_persona = get_persona
    store.list_personas = list_personas
    store.upsert_persona_capability_snapshot = upsert_persona_capability_snapshot
    store.get_capability_snapshot = get_capability_snapshot
    store.get_capability_snapshot_for_persona = get_capability_snapshot_for_persona
    store._local_dataset = lambda name: snapshots_map if name == "capability_snapshots" else {}
    store.dataset_source = lambda d: "typed_store"
    store.list_agora_signals = lambda **kw: []
    store.list_agora_sessions = lambda **kw: []
    store.list_agora_watchlist = lambda **kw: []
    store.list_agora_training_examples = lambda **kw: []
    return store


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _install_agora_store(monkeypatch, store) -> None:
    monkeypatch.setattr(bff_main, "read_store", store)
    monkeypatch.setattr(bff_main, "persona_write_owner", store)


# --------------------------------------------------------------------------- #
# Import smoke tests
# --------------------------------------------------------------------------- #

def test_agora_models_importable():
    from services.control_plane.bff.agora.models import (
        AgoraReadPredicate,
        AgoraServantPolicy,
        AgoraCapabilityScope,
        AgoraEnvelope,
        AgoraListEnvelope,
        AgoraMeta,
        AgoraListMeta,
        AgoraErrorCode,
        AgoraError,
        AGORA_CAPABILITIES,
        AGORA_REQUIRED_ROLES,
    )
    assert AgoraReadPredicate(tenant_id="tenant-alpha", user_id="user-alpha").fail_closed is True
    assert AgoraServantPolicy().execution_authority == "none"
    assert AgoraCapabilityScope
    assert len(AGORA_CAPABILITIES) == 7
    assert "agora.identity.v1" in AGORA_CAPABILITIES
    assert "agora.session.v1" in AGORA_CAPABILITIES
    assert "agora.workshop.v1" in AGORA_CAPABILITIES
    assert "agora.research.v1" in AGORA_CAPABILITIES
    assert "agora.trading.v1" in AGORA_CAPABILITIES
    assert "agora.dashboard.v1" in AGORA_CAPABILITIES
    assert "agora.personalization.v1" in AGORA_CAPABILITIES
    assert "operator" in AGORA_REQUIRED_ROLES


def test_agora_error_code_typed():
    from services.control_plane.bff.agora.models import AgoraErrorCode, AgoraError
    err = AgoraError(AgoraErrorCode.NOT_IMPLEMENTED, "stub", status_code=501)
    assert err.code == AgoraErrorCode.NOT_IMPLEMENTED
    assert err.status_code == 501


def test_agora_router_factory_importable():
    from services.control_plane.bff.agora.router import create_agora_router
    assert callable(create_agora_router)


def test_agora_sub_router_factories_importable():
    from services.control_plane.bff.agora.identity.router import create_identity_router
    from services.control_plane.bff.agora.servant.router import create_servant_router
    from services.control_plane.bff.agora.strategy_workshop.router import create_strategy_workshop_router
    from services.control_plane.bff.agora.research.router import create_research_router
    from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
    from services.control_plane.bff.agora.dashboard.router import create_dashboard_router
    from services.control_plane.bff.agora.shadow.router import create_shadow_router
    from services.control_plane.bff.agora.personalization.router import create_personalization_router
    from services.control_plane.bff.agora.management_projection.router import create_management_projection_router
    for factory in (
        create_identity_router, create_servant_router, create_strategy_workshop_router,
        create_research_router, create_trading_room_router, create_dashboard_router,
        create_shadow_router, create_personalization_router, create_management_projection_router,
    ):
        assert callable(factory)


# --------------------------------------------------------------------------- #
# GET /bff/agora/me — §18 envelope + capability scope
# --------------------------------------------------------------------------- #

def test_agora_me_returns_envelope(monkeypatch):
    """New endpoint — must return {data, meta} envelope."""
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/me", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body, f"missing 'data' in {body}"
    assert "meta" in body, f"missing 'meta' in {body}"
    meta = body["meta"]
    assert "snapshot_at" in meta
    assert meta.get("capability") == "agora.identity.v1"


def test_agora_me_data_has_7_capabilities(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/me", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "capabilities" in data
    caps = data["capabilities"]
    assert isinstance(caps, list)
    assert len(caps) == 7, f"Expected 7 capabilities, got {len(caps)}: {caps}"
    assert "agora.identity.v1" in caps
    assert data["tenant_id"] == "pantheon-dev"
    assert data["user_id"] == "agora-test-user"
    assert data["operator_id"] == "agora-test-user"
    assert data["granted_capabilities"] == caps
    assert data["read_predicate"] == {
        "tenant_id": "pantheon-dev",
        "user_id": "agora-test-user",
        "required_fields": ["tenant_id", "user_id"],
        "fail_closed": True,
    }
    assert data["servant_policy"]["persona_class"] == "agora_servant"
    assert data["servant_policy"]["owner_scope"] == "user_private"
    assert data["servant_policy"]["execution_authority"] == "none"
    joined_caps = " ".join(caps)
    assert "runtime_binding" not in joined_caps
    assert "broker" not in joined_caps
    assert "capital" not in joined_caps


def test_agora_me_rejects_cross_tenant_scope(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha")
    client = _client(monkeypatch)
    resp = client.get(
        "/bff/agora/me",
        headers={"Authorization": _OPERATOR_AUTH, "X-Tenant-Id": "tenant-beta"},
    )
    assert resp.status_code == 403


def test_agora_capabilities_include_backend_scope(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    client = _client(monkeypatch)
    resp = client.get(
        "/bff/agora/capabilities",
        headers={"Authorization": _OPERATOR_AUTH, "X-Tenant-Id": "tenant-beta"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["scope"]["tenant_id"] == "tenant-beta"
    assert data["scope"]["user_id"] == "agora-test-user"
    assert data["scope"]["read_predicate"]["fail_closed"] is True


def test_agora_me_unauthenticated_returns_401(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/me")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# GET /bff/agora/capabilities — filtered capability manifest
# --------------------------------------------------------------------------- #

def test_agora_capabilities_returns_manifest(monkeypatch):
    """New endpoint — must return capability manifest."""
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/capabilities", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert "capabilities" in body["data"]
    assert isinstance(body["data"]["capabilities"], list)

    capabilities_list = body["data"]["capabilities"]
    assert len(capabilities_list) == 7, f"Expected 7 capabilities, got {len(capabilities_list)}"
    cap_names = {c["name"] for c in capabilities_list}
    assert "agora.identity.v1" in cap_names
    assert "agora.session.v1" in cap_names



def test_agora_capabilities_unauthenticated_returns_401(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/capabilities")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# POST /bff/agora/servant/ensure — private servant provisioning
# --------------------------------------------------------------------------- #

def test_agora_servant_ensure_provisions_profile(monkeypatch, tmp_path):
    """New route — creates the user-private Persona Registry object and syncs OpenClaw."""
    store = _create_test_agora_store(allow_fallback=True)
    calls = []

    def fake_sync(persona):
        calls.append(persona)
        persona_id = persona["persona_id"]
        return {
            "status": "created",
            "agent_id": persona_id,
            "model_id": f"openclaw/{persona_id}",
            "model": "anthropic/claude-opus-4-8",
            "workspace_ref": f"/home/node/.openclaw/workspaces/{persona_id}",
        }

    _install_agora_store(monkeypatch, store)
    monkeypatch.setattr(bff_main, "_ensure_agora_servant_openclaw_agent", fake_sync)
    client = _client(monkeypatch)
    resp = client.post(
        "/bff/agora/servant/ensure",
        headers={
            "Authorization": _OPERATOR_AUTH,
            "Idempotency-Key": "agora-servant-ensure-001",
            "X-Request-Id": "req-agora-servant-ensure-001",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["capability"] == "agora.servant.v1"
    data = body["data"]
    assert data["persona_class"] == "agora_servant"
    assert data["owner_scope"] == "user_private"
    assert data["tenant_id"] == "pantheon-dev"
    assert data["agora_user_id"] == "agora-test-user"
    assert data["policy"]["execution_authority"] == "none"
    assert set(data["policy"]["prohibited_authority"]) == {
        "runtime_binding",
        "broker_order",
        "capital_binding",
    }
    assert "runtime_binding" not in " ".join(data["capability_summary"]["allowed_agora_capabilities"])
    stored = store.get_persona(data["persona_id"])
    assert stored is not None
    assert stored["metadata"]["persona_class"] == "agora_servant"
    assert stored["metadata"]["openclaw_agent"]["agent_id"] == data["persona_id"]
    assert calls and calls[0]["metadata"]["persona_class"] == "agora_servant"
    assert calls[0]["_agent_sync_idempotency_key"] == "agora-servant-ensure-001"


def test_agora_servant_ensure_reconciles_existing_profile(monkeypatch, tmp_path):
    store = _create_test_agora_store(allow_fallback=True)
    _install_agora_store(monkeypatch, store)

    calls = []

    def fake_sync(persona):
        calls.append(persona)
        persona_id = persona["persona_id"]
        return {
            "status": "updated",
            "agent_id": persona_id,
            "model_id": f"openclaw/{persona_id}",
            "workspace_ref": f"/home/node/.openclaw/workspaces/{persona_id}",
        }

    monkeypatch.setattr(bff_main, "_ensure_agora_servant_openclaw_agent", fake_sync)
    client = _client(monkeypatch)
    headers = {
        "Authorization": _OPERATOR_AUTH,
        "Idempotency-Key": "agora-servant-ensure-replay",
        "X-Request-Id": "req-agora-servant-replay-001",
    }
    first = client.post("/bff/agora/servant/ensure", headers=headers)
    second = client.post(
        "/bff/agora/servant/ensure",
        headers={**headers, "X-Request-Id": "req-agora-servant-replay-002"},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["persona_id"] == second.json()["data"]["persona_id"]
    servants = [
        persona for persona in store.list_personas()
        if (persona.get("metadata") or {}).get("persona_class") == "agora_servant"
    ]
    assert len(servants) == 1
    assert len(calls) == 2
    assert {call["_agent_sync_idempotency_key"] for call in calls} == {
        "agora-servant-ensure-replay"
    }


def test_ensured_servant_is_exactly_eligible_for_paper_persona_opinion(monkeypatch, tmp_path):
    """The supported proof path uses ensure, not full trading Persona provisioning."""
    store = _create_test_agora_store(allow_fallback=False)
    _install_agora_store(monkeypatch, store)
    expected_persona_id = "agora-servant-" + hashlib.sha256(
        "pantheon-dev\0agora-test-user\0agora_servant".encode("utf-8")
    ).hexdigest()[:20]
    store.upsert_persona_capability_snapshot(
        snapshot_id="cap-older-without-opinion",
        persona_id=expected_persona_id,
        capabilities=["research_only"],
        generated_at="2026-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        bff_main,
        "_ensure_agora_servant_openclaw_agent",
        lambda persona: {
            "status": "created",
            "agent_id": persona["persona_id"],
            "model_id": f"openclaw/{persona['persona_id']}",
            "workspace_ref": f"/home/node/.openclaw/workspaces/{persona['persona_id']}",
        },
    )
    client = _client(monkeypatch)
    suffix = uuid.uuid4().hex
    ensure_headers = {
        "Authorization": _OPERATOR_AUTH,
        "Idempotency-Key": f"ensure-paper-opinion-{suffix}",
        "X-Request-Id": f"req-ensure-paper-opinion-{suffix}",
    }
    ensured = client.post("/bff/agora/servant/ensure", headers=ensure_headers)
    replayed = client.post(
        "/bff/agora/servant/ensure",
        headers={**ensure_headers, "X-Request-Id": f"req-ensure-paper-opinion-replay-{suffix}"},
    )
    assert ensured.status_code == replayed.status_code == 200, ensured.text
    persona_id = ensured.json()["data"]["persona_id"]
    assert persona_id == expected_persona_id
    assert replayed.json()["data"]["persona_id"] == persona_id
    assert ensured.json()["data"]["status"] == "paper_only"
    assert ensured.json()["data"]["policy"]["execution_authority"] == "none"

    stored = store.get_persona(persona_id)
    assert stored is not None
    assert stored["tenant_id"] == "pantheon-dev"
    assert stored["tenantId"] == "pantheon-dev"
    assert stored["lifecycle_state"] == "paper_only"
    assert stored["metadata"]["deployment_stage"] == "paper"
    assert stored["metadata"]["environment_ceiling"] == "paper"
    assert stored["metadata"]["execution_authority"] == "none"
    assert store.list_personas()[0]["tenant_id"] == "pantheon-dev"

    # An older snapshot for the same Persona exists first in storage.  The
    # explicit canonical pointer must select the ensured grant exactly.
    assert store.get_capability_snapshot_for_persona(persona_id)["snapshot_id"] == "cap-older-without-opinion"
    snapshot = store.get_capability_snapshot(stored["metadata"]["capability_snapshot_id"])
    assert snapshot is not None
    assert snapshot["capabilities"] == ["persona_opinion"]
    assert snapshot["allowed_capabilities"] == ["persona_opinion"]
    assert snapshot["metadata"]["execution_authority"] == "none"

    context = client.post(
        "/bff/agora/interactions/context:resolve",
        headers={
            "Authorization": _OPERATOR_AUTH,
            "Idempotency-Key": f"context-paper-opinion-{suffix}",
        },
        json={
            "environment": "paper",
            "context_refs": [
                {"type": "strategy", "id": "strategy-paper-opinion", "version_id": "v1"}
            ],
        },
    )
    assert context.status_code == 200, context.text
    eligibility = client.post(
        "/bff/agora/interactions/participants:eligible",
        headers={"Authorization": _OPERATOR_AUTH},
        json={
            "workshop_id": context.json()["data"]["workshop_id"],
            "mode": "consult",
            "environment": "paper",
            "required_capability": "persona_opinion",
        },
    )
    assert eligibility.status_code == 200, eligibility.text
    included = eligibility.json()["data"]["included"]
    assert [row["persona_id"] for row in included] == [persona_id]
    assert included[0]["capability_snapshot_id"] == snapshot["snapshot_id"]


def test_agora_servant_ensure_requires_idempotency_headers(monkeypatch, tmp_path):
    _install_agora_store(
        monkeypatch,
        _create_test_agora_store(allow_fallback=True),
    )
    monkeypatch.setattr(bff_main, "_ensure_agora_servant_openclaw_agent", lambda persona: {})
    client = _client(monkeypatch)
    resp = client.post("/bff/agora/servant/ensure", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["precondition_failed"] == "Idempotency-Key"


def test_agora_servant_ensure_viewer_cannot_create_persona_or_capability_snapshot(monkeypatch, tmp_path):
    store = _create_test_agora_store(allow_fallback=False)
    _install_agora_store(monkeypatch, store)
    sync_calls = []
    monkeypatch.setattr(
        bff_main,
        "_ensure_agora_servant_openclaw_agent",
        lambda persona: sync_calls.append(persona) or {},
    )
    client = _client(monkeypatch)
    before_personas = store.list_personas()
    response = client.post(
        "/bff/agora/servant/ensure",
        headers={
            "Authorization": "Bearer agora-test-viewer:viewer",
            "Idempotency-Key": "viewer-cannot-ensure-servant",
            "X-Request-Id": "req-viewer-cannot-ensure-servant",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["precondition_failed"] == "role_check"
    assert store.list_personas() == before_personas
    assert not any(
        snapshot.get("canonicalWriteAuthority") == "persona_capability_service"
        for snapshot in (store._local_dataset("capability_snapshots") or {}).values()
    )
    assert sync_calls == []


def test_agora_servant_sync_failure_leaves_new_persona_ineligible(monkeypatch, tmp_path):
    store = _create_test_agora_store(allow_fallback=False)
    _install_agora_store(monkeypatch, store)

    def fail_sync(_persona):
        raise RuntimeError("OpenClaw unavailable")

    monkeypatch.setattr(bff_main, "_ensure_agora_servant_openclaw_agent", fail_sync)
    client = _client(monkeypatch)
    response = client.post(
        "/bff/agora/servant/ensure",
        headers={
            "Authorization": _OPERATOR_AUTH,
            "Idempotency-Key": "failed-sync-cannot-admit-servant",
            "X-Request-Id": "req-failed-sync-cannot-admit-servant",
        },
    )
    assert response.status_code == 503, response.text
    servants = [
        persona
        for persona in store.list_personas()
        if (persona.get("metadata") or {}).get("persona_class") == "agora_servant"
    ]
    assert len(servants) == 1
    assert servants[0]["lifecycle_state"] == "draft"
    assert store.get_capability_snapshot_for_persona(servants[0]["persona_id"]) is None


def test_agora_servant_ensure_unauthenticated_returns_401(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/bff/agora/servant/ensure")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Existing BFF routes not broken by Agora package mount
# --------------------------------------------------------------------------- #

def test_existing_bff_health_not_broken(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code in (200, 503), f"Unexpected health status: {resp.status_code}"


def test_existing_agora_sessions_not_broken(monkeypatch):
    """Existing main.py route must still respond (not shadowed by package router)."""
    store = _create_test_agora_store()
    monkeypatch.setattr(bff_main, "read_store", store)
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/sessions", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, f"Existing /bff/agora/sessions broken: {resp.status_code}"


def test_existing_agora_signals_not_broken(monkeypatch):
    store = _create_test_agora_store()
    monkeypatch.setattr(bff_main, "read_store", store)
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/signals", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, f"Existing /bff/agora/signals broken: {resp.status_code}"


def test_servant_ensure_and_eligibility_with_read_surface_ports_and_explicit_write_owner(monkeypatch, tmp_path):
    """Verifies that ReadSurfacePorts resolves list_personas with include_market_persona_defaults=True

    and eligibility returns 200 with the freshly ensured servant when write owner is explicit.
    """
    from services.control_plane.bff.ports.read_surface_ports import create_read_surface_ports

    write_owner = _create_test_agora_store(allow_fallback=False)
    read_surface = create_read_surface_ports(persona_registry_store=write_owner)

    monkeypatch.setattr(bff_main, "read_store", read_surface)
    monkeypatch.setattr(bff_main, "persona_write_owner", write_owner)
    monkeypatch.setattr(
        bff_main,
        "_ensure_agora_servant_openclaw_agent",
        lambda persona: {
            "status": "created",
            "agent_id": persona["persona_id"],
            "model_id": f"openclaw/{persona['persona_id']}",
            "workspace_ref": f"/home/node/.openclaw/workspaces/{persona['persona_id']}",
        },
    )

    client = _client(monkeypatch)
    suffix = uuid.uuid4().hex
    ensure_headers = {
        "Authorization": _OPERATOR_AUTH,
        "Idempotency-Key": f"ensure-rsp-compat-{suffix}",
        "X-Request-Id": f"req-ensure-rsp-compat-{suffix}",
    }
    ensured = client.post("/bff/agora/servant/ensure", headers=ensure_headers)
    assert ensured.status_code == 200, ensured.text
    persona_id = ensured.json()["data"]["persona_id"]

    context = client.post(
        "/bff/agora/interactions/context:resolve",
        headers={
            "Authorization": _OPERATOR_AUTH,
            "Idempotency-Key": f"context-rsp-compat-{suffix}",
        },
        json={
            "environment": "paper",
            "context_refs": [
                {"type": "strategy", "id": "strategy-rsp-compat", "version_id": "v1"}
            ],
        },
    )
    assert context.status_code == 200, context.text

    eligibility = client.post(
        "/bff/agora/interactions/participants:eligible",
        headers={"Authorization": _OPERATOR_AUTH},
        json={
            "workshop_id": context.json()["data"]["workshop_id"],
            "mode": "consult",
            "environment": "paper",
            "required_capability": "persona_opinion",
        },
    )
    assert eligibility.status_code == 200, eligibility.text
    included = eligibility.json()["data"]["included"]
    assert [row["persona_id"] for row in included] == [persona_id]


def test_servant_ensure_fails_if_read_surface_ports_is_used_as_write_owner(monkeypatch):
    """Verifies that servant ensure requires an explicit command-capable write owner and never treats ReadSurfacePorts as a writer."""
    from services.control_plane.bff.ports.read_surface_ports import create_in_memory_read_surface_ports

    read_surface = create_in_memory_read_surface_ports()
    monkeypatch.setattr(bff_main, "read_store", read_surface)
    monkeypatch.setattr(bff_main, "persona_write_owner", read_surface)

    client = _client(monkeypatch)
    suffix = uuid.uuid4().hex
    resp = client.post(
        "/bff/agora/servant/ensure",
        headers={
            "Authorization": _OPERATOR_AUTH,
            "Idempotency-Key": f"ensure-fail-{suffix}",
            "X-Request-Id": f"req-ensure-fail-{suffix}",
        },
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_agora_routers_have_zero_reverse_imports_of_main():
    """Verify that agora identity and personalization routers do not import main.py."""
    from services.control_plane.bff.agora.identity import router as id_router
    from services.control_plane.bff.agora.personalization import router as pers_router
    from services.control_plane.bff.agora import service as agora_service
    import inspect

    id_src = inspect.getsource(id_router)
    pers_src = inspect.getsource(pers_router)
    svc_src = inspect.getsource(agora_service)

    assert "import main" not in id_src, "agora.identity.router still imports main"
    assert "from main import" not in id_src, "agora.identity.router still imports from main"
    assert "import main" not in pers_src, "agora.personalization.router still imports main"
    assert "from main import" not in pers_src, "agora.personalization.router still imports from main"
    assert "import main" not in svc_src, "agora.service still imports main"
    assert "from main import" not in svc_src, "agora.service still imports from main"


def test_default_allowlisted_adapter_emits_simulation_provenance_by_default():
    """OP-G01: Locally generated results cannot claim real execution."""
    from services.control_plane.bff.agora.research.dispatcher import DefaultAllowlistedAdapter

    adapter = DefaultAllowlistedAdapter("backtest", "vectorbt_runner")
    assert adapter.default_provenance == "simulation"

    result = adapter.execute(
        stage={"stage_id": "stg-1", "routing": {}},
        plan={"strategy_id": "strat-1"},
        context={},
        downstream_key="key-1",
    )
    assert result.provenance == "simulation"
    assert result.outcome == "succeeded"
    assert result.metrics[0]["provenance"] == "simulation"

    # When real mode is requested WITHOUT real receipt, must emit simulation
    result_unverified_real = adapter.execute(
        stage={"stage_id": "stg-2", "routing": {"backend_mode": "real"}},
        plan={"strategy_id": "strat-1"},
        context={},
        downstream_key="key-2",
    )
    assert result_unverified_real.provenance == "simulation"

    # A claimed receipt is not owner readback and cannot promote synthetic data.
    result_verified_real = adapter.execute(
        stage={"stage_id": "stg-3", "routing": {"backend_mode": "real"}, "real_backend_receipt_id": "rcpt-123"},
        plan={"strategy_id": "strat-1"},
        context={"has_real_receipt": True},
        downstream_key="key-3",
    )
    assert result_verified_real.provenance == "simulation"


def test_agora_service_session_and_insight_lifecycle():
    """Verify AgoraService session creation, message append, and insight creation."""
    from services.control_plane.bff.agora.service import AgoraService

    store = _create_test_agora_store()
    svc = AgoraService(get_read_store=lambda: store)

    # Session lifecycle
    sess = svc.create_session(
        session_id="sess-test-01",
        title="Test Session",
        actor_id="test-operator",
        payload={"mode": "quick_ask"},
        created_at="2026-08-30T00:00:00Z",
    )
    assert sess["sessionId"] == "sess-test-01"
    assert svc.get_session("sess-test-01") is not None

    msg = svc.append_session_message(
        "sess-test-01",
        message_id="msg-test-01",
        content="Hello Agora",
        actor_id="test-operator",
        payload={"role": "user"},
        created_at="2026-08-30T00:00:01Z",
    )
    assert msg["messageId"] == "msg-test-01"

    # Insight lifecycle
    ins = svc.create_insight(
        insight_id="ins-test-01",
        summary="Test Insight",
        actor_id="test-operator",
        payload={"scope": "global"},
        created_at="2026-08-30T00:00:00Z",
    )
    assert ins["insightId"] == "ins-test-01"
    assert svc.get_insight("ins-test-01") is not None


def test_agora_service_session_status_uses_canonical_consultation_port():
    """The HTTP singular status filter must not leak into the plural port API."""
    from services.control_plane.bff.agora.service import AgoraService
    from services.control_plane.bff.ports import ReadSurfacePorts

    class _ConsultationReads:
        def list_consult_requests(
            self, *, statuses=None, target_type=None, consultation_type=None
        ):
            del target_type, consultation_type
            records = [
                {"id": "sess-active", "status": "active"},
                {"id": "sess-done", "status": "completed"},
            ]
            if statuses:
                return [record for record in records if record["status"] in statuses]
            return records

    ports = ReadSurfacePorts(operations_consultation=_ConsultationReads())
    svc = AgoraService(get_read_store=lambda: ports)

    assert [item["id"] for item in svc.list_sessions(status="ACTIVE")] == ["sess-active"]
    assert [item["id"] for item in svc.list_sessions()] == ["sess-active", "sess-done"]


def test_main_py_has_zero_legacy_agora_route_decorators():
    """Acceptance: main.py must have 0 legacy @app Agora route decorators remaining."""
    import inspect
    import re
    from services.control_plane.bff import main as bff_main

    main_src = inspect.getsource(bff_main)
    pattern = re.compile(r'@app\.(get|post|put|patch|delete)\(\s*["\'](/bff/agora|/api/v1/agora|/bff/sse/agora|/bff/research/tasks)')
    matches = pattern.findall(main_src)
    assert len(matches) == 0, f"Found {len(matches)} legacy Agora decorators in main.py: {matches}"


def test_migrated_agora_routes_preserve_legacy_http_contracts():
    """The extracted routes retain the query, header, and optional-body API shapes."""
    schema = bff_main.app.openapi()

    signals = schema["paths"]["/bff/agora/signals"]["get"]
    signal_parameters = {parameter["name"]: parameter for parameter in signals["parameters"]}
    assert {"reviewStatus", "status", "page_token", "page_size"}.issubset(signal_parameters)
    assert signal_parameters["page_size"]["schema"]["minimum"] == 1
    assert signal_parameters["page_size"]["schema"]["maximum"] == 200

    for path in (
        "/bff/agora/watchlist",
        "/bff/agora/markets",
        "/bff/agora/notes",
        "/bff/agora/market-notes",
        "/bff/agora/journal",
        "/bff/agora/decision-journal",
        "/bff/agora/training-examples",
        "/bff/agora/research-tasks",
        "/bff/research/tasks",
    ):
        parameters = {
            parameter["name"]: parameter
            for parameter in schema["paths"][path]["get"]["parameters"]
        }
        assert {"page_token", "page_size"}.issubset(parameters), path
        assert "pageToken" not in parameters, path
        assert "pageSize" not in parameters, path
        assert parameters["page_size"]["schema"]["minimum"] == 1, path
        assert parameters["page_size"]["schema"]["maximum"] == 200, path

    journal_patch = schema["paths"]["/bff/agora/journal/{entry_id}"]["patch"]
    journal_headers = {
        parameter["name"].lower()
        for parameter in journal_patch["parameters"]
        if parameter["in"] == "header"
    }
    assert {"x-mfa-token", "x-trace-id"}.issubset(journal_headers)

    for path in (
        "/api/v1/agora/ask/stream",
        "/bff/sse/agora/signals",
        "/bff/sse/agora/sessions/{sessionId}",
    ):
        parameters = schema["paths"][path]["get"]["parameters"]
        assert any(
            parameter["name"] == "last_event_id" and parameter["in"] == "query"
            for parameter in parameters
        ), path

    for path in (
        "/bff/agora/committee/{sessionId}/evidence-pack",
        "/bff/agora/committee/sessions",
        "/bff/agora/committee/sessions/{sessionId}/memos",
    ):
        request_body = schema["paths"][path]["post"].get("requestBody", {})
        assert request_body.get("required", False) is False, path


def test_migrated_agora_signals_supports_legacy_status_and_pagination(monkeypatch):
    """Legacy snake-case pagination and status filters remain valid after extraction."""
    store = _create_test_agora_store()
    captured: dict[str, str | None] = {}

    def list_signals(*, review_status=None, **_kwargs):
        captured["review_status"] = review_status
        return []

    store.list_agora_signals = list_signals
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    response = client.get(
        "/bff/agora/signals",
        params={"status": "pending", "page_token": "signal-17", "page_size": 1},
        headers={"Authorization": _OPERATOR_AUTH},
    )
    assert response.status_code == 200, response.text
    assert captured == {"review_status": "pending"}

    for invalid_page_size in (0, 201):
        invalid = client.get(
            "/bff/agora/signals",
            params={"page_size": invalid_page_size},
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert invalid.status_code == 422, invalid.text


@pytest.mark.parametrize(
    "path",
    (
        "/bff/agora/watchlist",
        "/bff/agora/notes",
        "/bff/agora/journal",
        "/bff/agora/training-examples",
        "/bff/agora/research-tasks",
    ),
)
def test_migrated_agora_list_routes_preserve_legacy_pagination_bounds(monkeypatch, path):
    """Extracted list routes retain snake-case pagination and the 1..200 bounds."""
    store = _create_test_agora_store()
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    response = client.get(
        path,
        params={"page_token": "legacy-token", "page_size": 1},
        headers={"Authorization": _OPERATOR_AUTH},
    )
    assert response.status_code == 200, response.text

    for invalid_page_size in (0, 201):
        invalid = client.get(
            path,
            params={"page_size": invalid_page_size},
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert invalid.status_code == 422, invalid.text


def test_migrated_agora_committee_posts_accept_optional_bodies(monkeypatch):
    """The route migration must not turn legacy optional JSON bodies into 422s."""
    store = _create_test_agora_store()
    committee_session_id = "committee-optional-body"
    store.get_agora_session = lambda session_id: (
        {"id": committee_session_id, "sessionId": committee_session_id, "mode": "committee"}
        if session_id == committee_session_id else None
    )
    store.create_agora_committee_evidence_pack = lambda **kwargs: {
        "id": kwargs["pack_id"],
        "sessionId": kwargs["session_id"],
    }
    store.get_consult_memo = lambda _memo_id: None
    store.submit_committee_session_memo = lambda session_id, **kwargs: {
        "memoId": kwargs["memo_id"],
        "sessionId": session_id,
    }
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    session_response = client.post(
        "/bff/agora/committee/sessions",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert session_response.status_code == 201, session_response.text

    evidence_response = client.post(
        f"/bff/agora/committee/{committee_session_id}/evidence-pack",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert evidence_response.status_code == 201, evidence_response.text

    memo_response = client.post(
        f"/bff/agora/committee/sessions/{committee_session_id}/memos",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert memo_response.status_code == 201, memo_response.text


def test_agora_service_imports_ports_package_interfaces():
    """Acceptance: agora/service.py must import canonical ports interfaces from ports package."""
    from services.control_plane.bff.agora import service as agora_service
    from services.control_plane.bff.ports import ReadSurfacePorts

    assert hasattr(agora_service, "ReadSurfacePorts")
    assert agora_service.ReadSurfacePorts is ReadSurfacePorts


def test_agora_session_and_message_dry_run_non_mutating(monkeypatch):
    """Regression: X-Dry-Run on session and message create returns 200 without mutating read surfaces."""
    store = _create_test_agora_store()
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    sess_resp = client.post(
        "/bff/agora/sessions",
        json={"title": "Dry-run session marker 999"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4()), "X-Dry-Run": "1"},
    )
    assert sess_resp.status_code == 200, sess_resp.text
    sess_body = sess_resp.json()
    assert sess_body["meta"]["dryRun"] is True
    assert sess_body["meta"]["durable"] is False
    sess_id = sess_body["data"]["id"]

    # Verify session is not persisted
    get_sess = client.get(f"/bff/agora/sessions/{sess_id}", headers={"Authorization": _OPERATOR_AUTH})
    assert get_sess.status_code == 404

    # Create real session for message dry run test
    real_sess = client.post(
        "/bff/agora/sessions",
        json={"title": "Real session for message test"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert real_sess.status_code == 201
    real_sess_id = real_sess.json()["data"]["id"]

    msg_resp = client.post(
        f"/bff/agora/sessions/{real_sess_id}/messages",
        json={"content": "Dry-run message marker 999"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4()), "X-Dry-Run": "1"},
    )
    assert msg_resp.status_code == 200, msg_resp.text
    msg_body = msg_resp.json()
    assert msg_body["meta"]["dryRun"] is True
    msg_id = msg_body["data"]["id"]

    # Verify message is not listed in session
    list_msgs = client.get(f"/bff/agora/sessions/{real_sess_id}/messages", headers={"Authorization": _OPERATOR_AUTH})
    assert list_msgs.status_code == 200
    assert all(m.get("id") != msg_id for m in list_msgs.json().get("data", []))


def test_agora_insight_dry_run_non_mutating(monkeypatch):
    """Regression: X-Dry-Run on insight create returns 200 without mutating read surfaces."""
    store = _create_test_agora_store()
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    ins_resp = client.post(
        "/bff/agora/insights",
        json={"summary": "Dry-run insight marker 999"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4()), "X-Dry-Run": "1"},
    )
    assert ins_resp.status_code == 200, ins_resp.text
    ins_body = ins_resp.json()
    assert ins_body["meta"]["dryRun"] is True
    assert ins_body["meta"]["durable"] is False
    ins_id = ins_body["data"]["id"]

    # Verify insight is not in list
    list_ins = client.get("/bff/agora/insights", headers={"Authorization": _OPERATOR_AUTH})
    assert list_ins.status_code == 200
    assert all((it.get("id") or it.get("insight_id")) != ins_id for it in list_ins.json().get("items", []))


def test_agora_message_create_on_nonexistent_session_returns_404(monkeypatch):
    """Regression: POST /bff/agora/sessions/{sessionId}/messages returns 404 for nonexistent session."""
    store = _create_test_agora_store()
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    resp = client.post(
        "/bff/agora/sessions/nonexistent-session-xyz/messages",
        json={"content": "Hello on missing session"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 404, resp.text


def test_agora_feedback_returns_201(monkeypatch):
    """Regression: POST /bff/agora/feedback returns 201 per API contract."""
    store = _create_test_agora_store()
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    resp = client.post(
        "/bff/agora/feedback",
        json={"signal_id": "sig-test-001", "verdict": "useful", "memo": "great signal"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4()), "X-Dry-Run": "1"},
    )
    # Dry run returns 200 with dryRun: true
    assert resp.status_code == 200, resp.text
    assert resp.json()["meta"]["dryRun"] is True

    # Real signal feedback against existing signal
    sig_resp = client.post(
        "/bff/agora/signals",
        json={"title": "Feedback test signal", "body": "Testing feedback route"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert sig_resp.status_code == 201
    sig_id = sig_resp.json()["data"]["id"]

    real_fb = client.post(
        "/bff/agora/feedback",
        json={"signal_id": sig_id, "verdict": "useful", "memo": "great signal"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert real_fb.status_code == 201, real_fb.text


def test_agora_missing_idempotency_key_returns_400(monkeypatch):
    """Regression: Missing Idempotency-Key returns HTTP 400 on mutating Agora routes."""
    store = _create_test_agora_store()
    _install_agora_store(monkeypatch, store)
    client = _client(monkeypatch)

    routes = [
        ("POST", "/bff/agora/ask/sessions", {"title": "Test"}),
        ("POST", "/bff/agora/committee/sessions", {"title": "Test"}),
        ("POST", "/bff/agora/committee/sessions/comm-1/open", {}),
        ("POST", "/bff/agora/committee/sessions/comm-1/close", {}),
    ]
    for method, path, payload in routes:
        resp = client.request(method, path, json=payload, headers={"Authorization": _OPERATOR_AUTH})
        assert resp.status_code == 400, f"Expected 400 for {method} {path}, got {resp.status_code}: {resp.text}"

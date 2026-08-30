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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports import create_in_memory_read_surface_ports

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
    from agora.models import (
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
    from agora.models import AgoraErrorCode, AgoraError
    err = AgoraError(AgoraErrorCode.NOT_IMPLEMENTED, "stub", status_code=501)
    assert err.code == AgoraErrorCode.NOT_IMPLEMENTED
    assert err.status_code == 501


def test_agora_router_factory_importable():
    from agora.router import create_agora_router
    assert callable(create_agora_router)


def test_agora_sub_router_factories_importable():
    from agora.identity.router import create_identity_router
    from agora.servant.router import create_servant_router
    from agora.strategy_workshop.router import create_strategy_workshop_router
    from agora.research.router import create_research_router
    from agora.trading_room.router import create_trading_room_router
    from agora.dashboard.router import create_dashboard_router
    from agora.shadow.router import create_shadow_router
    from agora.personalization.router import create_personalization_router
    from agora.management_projection.router import create_management_projection_router
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
    from ports.read_surface_ports import create_read_surface_ports

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
    from ports.read_surface_ports import create_in_memory_read_surface_ports

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
    import agora.identity.router as id_router
    import agora.personalization.router as pers_router
    import agora.service as agora_service
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
    """OP-G01: DefaultAllowlistedAdapter must emit simulation provenance unless real backend receipt exists."""
    from agora.research.dispatcher import DefaultAllowlistedAdapter

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

    # When real mode is requested WITH real receipt, emits real
    result_verified_real = adapter.execute(
        stage={"stage_id": "stg-3", "routing": {"backend_mode": "real"}, "real_backend_receipt_id": "rcpt-123"},
        plan={"strategy_id": "strat-1"},
        context={"has_real_receipt": True},
        downstream_key="key-3",
    )
    assert result_verified_real.provenance == "real"


def test_agora_service_session_and_insight_lifecycle():
    """Verify AgoraService session creation, message append, and insight creation."""
    from agora.service import AgoraService

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


"""Unit tests for AG-BE-000: Agora BFF router package skeleton.

Verifies:
- create_agora_router() mounts without errors and does not break existing routes
- GET /bff/agora/me returns the §18 envelope ({data, meta}) with capability scope
- GET /bff/agora/capabilities returns the filtered capability manifest
- POST /bff/agora/servant/ensure returns HTTP 501 (genuinely new stub route)
- Unauthenticated requests to new endpoints return HTTP 401
- Package imports are consistent (models, router, sub-module factories)
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

_OPERATOR_AUTH = "Bearer agora-test-user:operator"
_NO_AUTH = None


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    return TestClient(bff_main.app, raise_server_exceptions=False)


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


def test_agora_capabilities_unauthenticated_returns_401(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/capabilities")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# POST /bff/agora/servant/ensure — private servant provisioning
# --------------------------------------------------------------------------- #

def test_agora_servant_ensure_provisions_profile(monkeypatch, tmp_path):
    """New route — creates the user-private Persona Registry object and syncs OpenClaw."""
    store = ReadSurfaceStore(str(tmp_path / "read_surfaces.json"), allow_local_snapshot_fallback=True)
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

    monkeypatch.setattr(bff_main, "read_store", store)
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


def test_agora_servant_ensure_reconciles_existing_profile(monkeypatch, tmp_path):
    store = ReadSurfaceStore(str(tmp_path / "read_surfaces.json"), allow_local_snapshot_fallback=True)
    monkeypatch.setattr(bff_main, "read_store", store)

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


def test_agora_servant_ensure_requires_idempotency_headers(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bff_main,
        "read_store",
        ReadSurfaceStore(str(tmp_path / "read_surfaces.json"), allow_local_snapshot_fallback=True),
    )
    monkeypatch.setattr(bff_main, "_ensure_agora_servant_openclaw_agent", lambda persona: {})
    client = _client(monkeypatch)
    resp = client.post("/bff/agora/servant/ensure", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["precondition_failed"] == "Idempotency-Key"


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
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/sessions", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, f"Existing /bff/agora/sessions broken: {resp.status_code}"


def test_existing_agora_signals_not_broken(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/bff/agora/signals", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, f"Existing /bff/agora/signals broken: {resp.status_code}"

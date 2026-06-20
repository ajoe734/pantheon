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
# POST /bff/agora/servant/ensure — genuinely new stub (not in main.py)
# --------------------------------------------------------------------------- #

def test_agora_servant_ensure_returns_501(monkeypatch):
    """New route — should return 501 NOT_IMPLEMENTED (canonical BFF code)."""
    client = _client(monkeypatch)
    resp = client.post("/bff/agora/servant/ensure", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 501, f"Expected 501, got {resp.status_code}: {resp.text}"
    body = resp.json()
    # BFF exception handler normalises to {error: {...}, meta: {...}} shape
    assert "error" in body, f"missing 'error' in {body}"
    assert body["error"].get("code") == "NOT_IMPLEMENTED"
    assert "not yet implemented" in body["error"].get("message", "").lower()


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

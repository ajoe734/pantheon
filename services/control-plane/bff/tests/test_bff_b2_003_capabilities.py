"""
BFF-B2-003: Integration tests for the B2.3 Capabilities facade.

Covers dedicated GET handlers for:
  - GET /bff/mcp-servers            list envelope + 401
  - GET /bff/mcp-servers/{id}       detail + 404 + 401
  - GET /bff/mcp-tools              list envelope + 401
  - GET /bff/mcp-tools/{id}         detail + 404 + 401
  - GET /bff/channels               list envelope (catalog) + 401
  - GET /bff/channels/{id}          detail + 404 + 401
  - GET /bff/ranking-formulas       list envelope + 401
  - GET /bff/ranking-formulas/{id}  detail + 404 + 401
  - /bff/tools and /bff/skills still served by their own dedicated handlers
"""
from __future__ import annotations

import tempfile
import uuid
from typing import Any, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.management_read_models.ranking_router import (
    create_ranking_formulas_router,
)
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.tools_integrations.router import create_integrations_router
from services.control_plane.bff.tools_integrations.service import (
    SSE_CHANNEL_CATALOG,
    IntegrationsService,
    default_bff_error,
)

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2-003:operator"}
NO_AUTH_HEADERS: dict = {}

_ACTIVE_APP: Optional[FastAPI] = None
_ACTIVE_SERVICE: Optional[IntegrationsService] = None


def _extract_identity(authorization: Optional[str] = None, **kwargs: Any) -> Any:
    class Identity:
        def __init__(self, op_id: str, roles: List[str]) -> None:
            self.operator_id = op_id
            self.id = op_id
            self.roles = roles
            self.is_authenticated = True

    if not authorization or not authorization.startswith("Bearer "):
        raise default_bff_error(
            status_code=401,
            code="AUTH_REQUIRED",
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
        )
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise default_bff_error(
            status_code=401,
            code="AUTH_REQUIRED",
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
        )
    if ":" in token:
        parts = token.split(":", 1)
        return Identity(parts[0], [r.strip() for r in parts[1].split(",")])
    return Identity(token, ["operator", "viewer", "admin"])


def _create_app(store: Any) -> tuple[FastAPI, IntegrationsService]:
    app = FastAPI()
    register_error_handlers(app)
    svc = IntegrationsService(
        read_store=store,
        openclaw_client=None,
        utc_now_fn=utc_now,
    )
    int_router = create_integrations_router(
        service=svc,
        read_surface=store,
        extract_identity=_extract_identity,
        utc_now_fn=utc_now,
    )
    rank_router = create_ranking_formulas_router(
        read_surface=store,
        extract_identity=_extract_identity,
        utc_now=utc_now,
    )
    app.include_router(int_router)
    app.include_router(rank_router)
    return app, svc


def _fresh_client(td: str = "") -> TestClient:
    global _ACTIVE_APP, _ACTIVE_SERVICE
    store = create_in_memory_read_surface_ports()
    _ACTIVE_APP, _ACTIVE_SERVICE = _create_app(store)
    return TestClient(_ACTIVE_APP)


def _register_mcp_server(client: TestClient, name: str = "Test MCP Server") -> str:
    key = f"b2-003-srv-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/mcp/servers",
        json={"name": name, "endpoint": "http://localhost:9000"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body.get("server_id") or body.get("id") or "")


# ---------------------------------------------------------------------------
# GET /bff/mcp-servers
# ---------------------------------------------------------------------------

def test_bff_mcp_servers_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        _register_mcp_server(client, "Alpha MCP")
        resp = client.get("/bff/mcp-servers", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body
        assert body["page_info"]["total"] >= 1


def test_bff_mcp_servers_list_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/mcp-servers", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


def test_bff_mcp_servers_list_status_filter() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        _register_mcp_server(client, "Filtered MCP")
        resp = client.get("/bff/mcp-servers?status=registered", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body.get("data"), list)


# ---------------------------------------------------------------------------
# GET /bff/mcp-servers/{id}
# ---------------------------------------------------------------------------

def test_bff_mcp_servers_detail_known() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        server_id = _register_mcp_server(client, "Detail MCP")
        assert server_id, "server_id should be non-empty"
        resp = client.get(f"/bff/mcp-servers/{server_id}", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        record = body["data"]
        assert str(record.get("server_id") or record.get("id") or "") == server_id


def test_bff_mcp_servers_detail_unknown_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/mcp-servers/nonexistent-srv-id", headers=OPERATOR_HEADERS)
        assert resp.status_code == 404


def test_bff_mcp_servers_detail_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/mcp-servers/any-id", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /bff/mcp-tools
# ---------------------------------------------------------------------------

def test_bff_mcp_tools_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/mcp-tools", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body


def test_bff_mcp_tools_list_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/mcp-tools", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


def test_bff_mcp_tools_detail_unknown_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/mcp-tools/nonexistent-tool", headers=OPERATOR_HEADERS)
        assert resp.status_code == 404


def test_bff_mcp_tools_detail_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/mcp-tools/any-tool", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /bff/channels
# ---------------------------------------------------------------------------

def test_bff_channels_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/channels", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body
        channels = body["data"]
        assert len(channels) > 0, "SSE_CHANNEL_CATALOG must be non-empty"
        first = channels[0]
        assert "id" in first
        assert "channel_id" in first
        assert "status" in first


def test_bff_channels_list_contains_catalog() -> None:
    """All SSE_CHANNEL_CATALOG entries must appear in the list."""
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/channels", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = {r["id"] for r in body["data"]}
        for ch in SSE_CHANNEL_CATALOG:
            assert ch in ids, f"Channel {ch!r} missing from /bff/channels list"


def test_bff_channels_list_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/channels", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /bff/channels/{id}
# ---------------------------------------------------------------------------

def test_bff_channels_detail_known() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        channel_id = SSE_CHANNEL_CATALOG[0]
        resp = client.get(f"/bff/channels/{channel_id}", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert body["data"]["id"] == channel_id
        assert body["data"]["channel_id"] == channel_id


def test_bff_channels_detail_unknown_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/channels/nonexistent-channel", headers=OPERATOR_HEADERS)
        assert resp.status_code == 404


def test_bff_channels_detail_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get(f"/bff/channels/{SSE_CHANNEL_CATALOG[0]}", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /bff/ranking-formulas
# ---------------------------------------------------------------------------

def _seed_ranking_formula(td: str) -> str:
    global _ACTIVE_APP, _ACTIVE_SERVICE
    formula_id = f"rf-{uuid.uuid4().hex[:8]}"
    record = {
        "formula_id": formula_id,
        "id": formula_id,
        "name": "Test Formula",
        "description": "b2-003 test formula",
        "actor_id": "op-b2-003",
    }
    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"ranking_formulas": [record]}
    )
    _ACTIVE_APP, _ACTIVE_SERVICE = _create_app(store)
    return formula_id


def test_bff_ranking_formulas_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        _seed_ranking_formula(td)
        assert _ACTIVE_APP is not None
        client = TestClient(_ACTIVE_APP)
        resp = client.get("/bff/ranking-formulas", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body
        assert body["page_info"]["total"] >= 1


def test_bff_ranking_formulas_list_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/ranking-formulas", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /bff/ranking-formulas/{id}
# ---------------------------------------------------------------------------

def test_bff_ranking_formulas_detail_known() -> None:
    with tempfile.TemporaryDirectory() as td:
        formula_id = _seed_ranking_formula(td)
        assert formula_id, "formula_id should be non-empty"
        assert _ACTIVE_APP is not None
        client = TestClient(_ACTIVE_APP)
        resp = client.get(f"/bff/ranking-formulas/{formula_id}", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        record = body["data"]
        assert str(record.get("formula_id") or record.get("id") or "") == formula_id


def test_bff_ranking_formulas_detail_unknown_404() -> None:
    # Seed at least one formula so the surface has records and detail returns 404
    with tempfile.TemporaryDirectory() as td:
        _seed_ranking_formula(td)
        assert _ACTIVE_APP is not None
        client = TestClient(_ACTIVE_APP)
        resp = client.get("/bff/ranking-formulas/nonexistent-rf-xyz", headers=OPERATOR_HEADERS)
        assert resp.status_code == 404


def test_bff_ranking_formulas_detail_401_unauthenticated() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/ranking-formulas/some-rf", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Existing /bff/tools and /bff/skills still served by dedicated handlers
# ---------------------------------------------------------------------------

def test_bff_tools_still_has_dedicated_handler() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/tools", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body or "items" in body or "page_info" in body


def test_bff_skills_still_has_dedicated_handler() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/skills", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body or "items" in body or "page_info" in body

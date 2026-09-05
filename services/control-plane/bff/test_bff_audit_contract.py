"""
Contract tests for P0-AUD-001: GET /bff/audit read endpoint.

Verifies: list envelope, filter params, pagination, RBAC, and read-surface meta.
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-aud:operator"}
ANON_HEADERS: dict = {}


class AuditTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        self._allow_fallback = allow_fallback
        if data is not None:
            self._data = data
        elif allow_fallback:
            self._data = {
                "governance_audit_events": {
                    "aud-001": {
                        "entry_id": "aud-001",
                        "auditId": "aud-001",
                        "id": "aud-001",
                        "actor": "fixture-governance-reviewer",
                        "target_type": "strategy",
                        "action_type": "route_policy_published",
                        "created_at": "2026-05-01T12:00:00Z",
                    },
                    "aud-002": {
                        "entry_id": "aud-002",
                        "auditId": "aud-002",
                        "id": "aud-002",
                        "actor": "fixture-governance-reviewer",
                        "target_type": "strategy",
                        "action_type": "route_policy_published",
                        "created_at": "2026-05-01T12:05:00Z",
                    },
                }
            }
        else:
            self._data = {}

    def list_governance_audit_events(
        self,
        *,
        actor: str | None = None,
        action_types: list[str] | None = None,
        target_type: str | None = None,
        from_ts: Any = None,
        to_ts: Any = None,
        include_fixture_pack: bool = True,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        events = list(self._data.get("governance_audit_events", {}).values())
        filtered = []
        for e in events:
            if actor and e.get("actor") != actor:
                continue
            if target_type and e.get("target_type") != target_type:
                continue
            if action_types and e.get("action_type") not in action_types:
                continue
            filtered.append(e)
        return filtered


def _fresh_client(td: str = "", *, allow_fallback: bool = True) -> TestClient:
    store = AuditTestReadPorts(allow_fallback=allow_fallback)
    app = FastAPI(title="Audit router contract")

    def _extract_identity(authorization: str | None) -> OperatorIdentity:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required")
        raw = authorization[len("Bearer "):].strip()
        parts = raw.split(":")
        operator_id = parts[0] if parts else "op"
        roles = parts[1].split(",") if len(parts) > 1 else []
        return OperatorIdentity(operator_id=operator_id, roles=roles, claims={})

    def _require_read_role(identity: OperatorIdentity) -> None:
        if not identity or not identity.roles:
            raise HTTPException(status_code=403, detail="Forbidden")

    router = create_incident_router(
        list_governance_audit_events=store.list_governance_audit_events,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        read_surface_meta=lambda surface, view, **kwargs: {
            "snapshot_at": "2026-05-01T12:00:00Z",
            "source": "local_store",
            "total": kwargs.get("total", 0),
        },
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Basic list contract
# ---------------------------------------------------------------------------

def test_bff_audit_returns_200_with_standard_envelope() -> None:
    client = _fresh_client()
    resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "items" in body
    assert "page_info" in body
    assert "meta" in body
    assert isinstance(body["data"], list)
    assert isinstance(body["items"], list)


def test_bff_audit_returns_fixture_events_from_pack_a() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # pack_a fixture ships two governance_audit_events
    assert len(body["data"]) >= 2


def test_bff_audit_page_info_has_total() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    page_info = resp.json()["page_info"]
    assert "total" in page_info
    assert isinstance(page_info["total"], int)


# ---------------------------------------------------------------------------
# Filter params
# ---------------------------------------------------------------------------

def test_bff_audit_filter_by_actor() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get(
        "/bff/audit",
        params={"actor": "fixture-governance-reviewer"},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for event in body["data"]:
        assert event.get("actor") == "fixture-governance-reviewer"


def test_bff_audit_filter_by_target_type() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get(
        "/bff/audit",
        params={"target_type": "strategy"},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for event in body["data"]:
        assert event.get("target_type") == "strategy"


def test_bff_audit_filter_by_action_type() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get(
        "/bff/audit",
        params={"action_type": "route_policy_published"},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for event in body["data"]:
        assert event.get("action_type") == "route_policy_published"


def test_bff_audit_filter_nonexistent_actor_returns_empty() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get(
        "/bff/audit",
        params={"actor": "no-such-actor-xyz"},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["page_info"]["total"] == 0


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_bff_audit_pagination_respects_page_size() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get(
        "/bff/audit",
        params={"page_size": 1},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["data"]) <= 1
    total = body["page_info"]["total"]
    if total > 1:
        assert body["page_info"]["next_page_token"] is not None


def test_bff_audit_pagination_page_token_advances() -> None:
    client = _fresh_client(allow_fallback=True)
    first = client.get(
        "/bff/audit",
        params={"page_size": 1},
        headers=OPERATOR_HEADERS,
    )
    assert first.status_code == 200
    first_body = first.json()
    if first_body["page_info"]["next_page_token"] is None:
        pytest.skip("Only one event in fixture; pagination not exercised")
    token = first_body["page_info"]["next_page_token"]
    second = client.get(
        "/bff/audit",
        params={"page_size": 1, "page_token": token},
        headers=OPERATOR_HEADERS,
    )
    assert second.status_code == 200
    second_body = second.json()
    first_ids = {e.get("entry_id") for e in first_body["data"]}
    second_ids = {e.get("entry_id") for e in second_body["data"]}
    assert first_ids.isdisjoint(second_ids), "Pages must not overlap"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_bff_audit_requires_auth() -> None:
    client = _fresh_client(allow_fallback=True)
    resp = client.get("/bff/audit", headers=ANON_HEADERS)
    assert resp.status_code in {401, 403}, resp.text


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def test_bff_audit_meta_has_snapshot_at() -> None:
    client = _fresh_client()
    resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json()["meta"]
    assert "snapshot_at" in meta

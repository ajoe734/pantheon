"""Contract tests for BFF-GOVRULES governance sub-rules read endpoints.

Covers:
  GET /bff/management/permissions         (BFF-GOVRULES-01)
  GET /bff/management/memory-governance   (BFF-GOVRULES-02)
  GET /bff/management/consult-rules       (BFF-GOVRULES-03)
  GET /bff/route-policies                 (BFF-GOVRULES-04)

For each endpoint:
  - 401 when no auth header
  - degraded envelope (status:unavailable, source:missing) when store is empty
  - ok envelope with items when store is seeded
  - pagination via page_token / page_size
"""
from __future__ import annotations

import os
import sys
import types

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.consult_rules import create_consult_rules_router
from services.control_plane.bff.console_gap.memory_governance import create_memory_governance_router
from services.control_plane.bff.console_gap.permissions import create_permissions_router
from services.control_plane.bff.console_gap.route_policies import create_route_policies_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_HEADERS = {"Authorization": "Bearer op-gov:operator,reviewer"}


def _extract_identity(authorization: str | None) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = authorization[len("Bearer "):].strip()
    parts = raw.split(":")
    operator_id = parts[0] if parts else "op"
    roles = parts[1].split(",") if len(parts) > 1 else []
    claims = {"mfa": True} if len(parts) > 2 and "mfa" in parts[2] else {}
    return OperatorIdentity(operator_id=operator_id, roles=roles, claims=claims)


def _require_read_role(identity: OperatorIdentity) -> None:
    if not identity or not identity.roles:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── helpers ──────────────────────────────────────────────────────────────────

_PERM_RECORD = {"permission_id": "perm-001", "subject": "persona", "action": "trade", "effect": "allow"}
_MEM_GOV_RECORD = {"rule_id": "mem-001", "scope": "persona_memory", "retention_days": 90}
_CONSULT_RULE_RECORD = {"rule_id": "cr-001", "trigger": "high_risk_allocation", "required": True}
_ROUTE_POLICY_RECORD = {"policy_id": "rp-001", "route": "/execution/*", "mode": "paper_only"}


class _SubrulesTestStore:
    def __init__(self, seeded: dict[str, list] | None = None) -> None:
        self.seeded = seeded or {}
        self.ports = create_in_memory_read_surface_ports()

    def list_governance_permissions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.seeded.get("governance_permissions", []))

    def list_memory_governance_rules(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.seeded.get("memory_governance_rules", []))

    def list_consult_rules(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.seeded.get("consult_rules", []))

    def list_route_policies(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.seeded.get("route_policies", []))

    def dataset_source(self, dataset: str) -> str:
        if dataset in self.seeded:
            return "local_snapshot"
        return "missing"

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ports, name)


def _empty_store() -> _SubrulesTestStore:
    """Store with no governance datasets configured — all sources return 'missing'."""
    return _SubrulesTestStore()


def _seeded_store(dataset: str, records: list) -> _SubrulesTestStore:
    """Store with a specific dataset seeded so dataset_source returns 'local_snapshot'."""
    return _SubrulesTestStore({dataset: records})


def _make_client(store: Any = None) -> TestClient:
    app = FastAPI(title="Governance Subrules Contract")
    store_obj = store if store is not None else _empty_store()
    kwargs = dict(
        read_surface=store_obj,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
    )
    app.include_router(create_permissions_router(**kwargs))
    app.include_router(create_memory_governance_router(**kwargs))
    app.include_router(create_consult_rules_router(**kwargs))
    app.include_router(create_route_policies_router(**kwargs))
    return TestClient(app, raise_server_exceptions=False)


# ── GET /bff/management/permissions ─────────────────────────────────────────

class TestPermissions:
    ROUTE = "/bff/management/permissions"

    def test_requires_auth(self) -> None:
        client = _make_client()
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        client = _make_client(_empty_store())
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" not in body
        assert body["data"]["items"] == []
        assert body["page_info"]["total"] == 0
        surface = body["meta"]["surfaces"]["governance_permissions"]
        assert surface["status"] == "unavailable"
        assert surface["source"] == "missing"

    def test_seeded_store_ok_envelope(self) -> None:
        client = _make_client(_seeded_store("governance_permissions", [_PERM_RECORD]))
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["total"] == 1
        assert "items" not in body
        assert body["data"]["items"][0]["permission_id"] == "perm-001"
        surface = body["meta"]["surfaces"]["governance_permissions"]
        assert surface["status"] == "ok"

    def test_pagination(self) -> None:
        records = [{"permission_id": f"perm-{i}", "action": "trade"} for i in range(5)]
        client = _make_client(_seeded_store("governance_permissions", records))
        resp = client.get(self.ROUTE + "?page_size=2", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["total"] == 5
        assert "items" not in body
        assert len(body["data"]["items"]) == 2
        assert body["page_info"]["next_page_token"] == "2"


# ── GET /bff/management/memory-governance ────────────────────────────────────

class TestMemoryGovernance:
    ROUTE = "/bff/management/memory-governance"

    def test_requires_auth(self) -> None:
        client = _make_client()
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        client = _make_client(_empty_store())
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" not in body
        assert body["data"]["items"] == []
        surface = body["meta"]["surfaces"]["memory_governance_rules"]
        assert surface["status"] == "unavailable"
        assert surface["source"] == "missing"

    def test_seeded_store_ok_envelope(self) -> None:
        client = _make_client(_seeded_store("memory_governance_rules", [_MEM_GOV_RECORD]))
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["total"] == 1
        assert "items" not in body
        assert body["data"]["items"][0]["rule_id"] == "mem-001"
        surface = body["meta"]["surfaces"]["memory_governance_rules"]
        assert surface["status"] == "ok"


# ── GET /bff/management/consult-rules ────────────────────────────────────────

class TestConsultRules:
    ROUTE = "/bff/management/consult-rules"

    def test_requires_auth(self) -> None:
        client = _make_client()
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        client = _make_client(_empty_store())
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" not in body
        assert body["data"]["items"] == []
        surface = body["meta"]["surfaces"]["consult_rules"]
        assert surface["status"] == "unavailable"
        assert surface["source"] == "missing"

    def test_seeded_store_ok_envelope(self) -> None:
        client = _make_client(_seeded_store("consult_rules", [_CONSULT_RULE_RECORD]))
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["total"] == 1
        assert "items" not in body
        assert body["data"]["items"][0]["rule_id"] == "cr-001"
        surface = body["meta"]["surfaces"]["consult_rules"]
        assert surface["status"] == "ok"


# ── GET /bff/route-policies ──────────────────────────────────────────────────

class TestRoutePolicies:
    ROUTE = "/bff/route-policies"

    def test_requires_auth(self) -> None:
        client = _make_client()
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        client = _make_client(_empty_store())
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" not in body
        assert body["data"]["items"] == []
        surface = body["meta"]["surfaces"]["route_policies"]
        assert surface["status"] == "unavailable"
        assert surface["source"] == "missing"

    def test_seeded_store_ok_envelope(self) -> None:
        client = _make_client(_seeded_store("route_policies", [_ROUTE_POLICY_RECORD]))
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["total"] == 1
        assert "items" not in body
        assert body["data"]["items"][0]["policy_id"] == "rp-001"
        surface = body["meta"]["surfaces"]["route_policies"]
        assert surface["status"] == "ok"

    def test_envelope_shape(self) -> None:
        client = _make_client(_empty_store())
        resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "items" not in body
        assert "page_info" in body
        assert "meta" in body
        pi = body["page_info"]
        assert "next_page_token" in pi
        assert "total" in pi
        assert "page_size" in pi
        assert "returned" in pi
        assert "snapshot_at" in body["meta"]
        assert "status" in body["meta"]
        assert "source" in body["meta"]
        assert "surfaces" in body["meta"]

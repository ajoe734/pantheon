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

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer op-gov:operator,reviewer"}

# ── helpers ──────────────────────────────────────────────────────────────────

_PERM_RECORD = {"permission_id": "perm-001", "subject": "persona", "action": "trade", "effect": "allow"}
_MEM_GOV_RECORD = {"rule_id": "mem-001", "scope": "persona_memory", "retention_days": 90}
_CONSULT_RULE_RECORD = {"rule_id": "cr-001", "trigger": "high_risk_allocation", "required": True}
_ROUTE_POLICY_RECORD = {"policy_id": "rp-001", "route": "/execution/*", "mode": "paper_only"}


def _empty_store() -> ReadSurfaceStore:
    """Store with no governance datasets configured — all sources return 'missing'."""
    import tempfile, os as _os
    path = _os.path.join(tempfile.mkdtemp(), "read_surfaces.json")
    return ReadSurfaceStore(path, allow_local_snapshot_fallback=False)


def _seeded_store(dataset: str, records: list) -> ReadSurfaceStore:
    """Store with a specific dataset seeded so dataset_source returns 'local_snapshot'."""
    import tempfile, os as _os, json
    td = tempfile.mkdtemp()
    path = _os.path.join(td, "read_surfaces.json")
    store = ReadSurfaceStore(path, allow_local_snapshot_fallback=True)
    # Inject data directly into the internal dict used by _local_dataset
    store._data[dataset] = {str(i): r for i, r in enumerate(records)}
    return store


# ── GET /bff/management/permissions ─────────────────────────────────────────

class TestPermissions:
    ROUTE = "/bff/management/permissions"

    def test_requires_auth(self) -> None:
        client = TestClient(bff_main.app, raise_server_exceptions=False)
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _empty_store()
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "items" not in body
            assert body["data"]["items"] == []
            assert body["page_info"]["total"] == 0
            surface = body["meta"]["surfaces"]["governance_permissions"]
            assert surface["status"] == "unavailable"
            assert surface["source"] == "missing"
        finally:
            bff_main.read_store = original

    def test_seeded_store_ok_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _seeded_store("governance_permissions", [_PERM_RECORD])
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 1
            assert "items" not in body
            assert body["data"]["items"][0]["permission_id"] == "perm-001"
            surface = body["meta"]["surfaces"]["governance_permissions"]
            assert surface["status"] == "ok"
        finally:
            bff_main.read_store = original

    def test_pagination(self) -> None:
        records = [{"permission_id": f"perm-{i}", "action": "trade"} for i in range(5)]
        original = bff_main.read_store
        try:
            bff_main.read_store = _seeded_store("governance_permissions", records)
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE + "?page_size=2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 5
            assert "items" not in body
            assert len(body["data"]["items"]) == 2
            assert body["page_info"]["next_page_token"] == "2"
        finally:
            bff_main.read_store = original


# ── GET /bff/management/memory-governance ────────────────────────────────────

class TestMemoryGovernance:
    ROUTE = "/bff/management/memory-governance"

    def test_requires_auth(self) -> None:
        client = TestClient(bff_main.app, raise_server_exceptions=False)
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _empty_store()
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "items" not in body
            assert body["data"]["items"] == []
            surface = body["meta"]["surfaces"]["memory_governance_rules"]
            assert surface["status"] == "unavailable"
            assert surface["source"] == "missing"
        finally:
            bff_main.read_store = original

    def test_seeded_store_ok_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _seeded_store("memory_governance_rules", [_MEM_GOV_RECORD])
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 1
            assert "items" not in body
            assert body["data"]["items"][0]["rule_id"] == "mem-001"
            surface = body["meta"]["surfaces"]["memory_governance_rules"]
            assert surface["status"] == "ok"
        finally:
            bff_main.read_store = original


# ── GET /bff/management/consult-rules ────────────────────────────────────────

class TestConsultRules:
    ROUTE = "/bff/management/consult-rules"

    def test_requires_auth(self) -> None:
        client = TestClient(bff_main.app, raise_server_exceptions=False)
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _empty_store()
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "items" not in body
            assert body["data"]["items"] == []
            surface = body["meta"]["surfaces"]["consult_rules"]
            assert surface["status"] == "unavailable"
            assert surface["source"] == "missing"
        finally:
            bff_main.read_store = original

    def test_seeded_store_ok_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _seeded_store("consult_rules", [_CONSULT_RULE_RECORD])
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 1
            assert "items" not in body
            assert body["data"]["items"][0]["rule_id"] == "cr-001"
            surface = body["meta"]["surfaces"]["consult_rules"]
            assert surface["status"] == "ok"
        finally:
            bff_main.read_store = original


# ── GET /bff/route-policies ──────────────────────────────────────────────────

class TestRoutePolicies:
    ROUTE = "/bff/route-policies"

    def test_requires_auth(self) -> None:
        client = TestClient(bff_main.app, raise_server_exceptions=False)
        assert client.get(self.ROUTE).status_code == 401

    def test_empty_store_degraded_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _empty_store()
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "items" not in body
            assert body["data"]["items"] == []
            surface = body["meta"]["surfaces"]["route_policies"]
            assert surface["status"] == "unavailable"
            assert surface["source"] == "missing"
        finally:
            bff_main.read_store = original

    def test_seeded_store_ok_envelope(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _seeded_store("route_policies", [_ROUTE_POLICY_RECORD])
            client = TestClient(bff_main.app)
            resp = client.get(self.ROUTE, headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["total"] == 1
            assert "items" not in body
            assert body["data"]["items"][0]["policy_id"] == "rp-001"
            surface = body["meta"]["surfaces"]["route_policies"]
            assert surface["status"] == "ok"
        finally:
            bff_main.read_store = original

    def test_envelope_shape(self) -> None:
        original = bff_main.read_store
        try:
            bff_main.read_store = _empty_store()
            client = TestClient(bff_main.app)
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
        finally:
            bff_main.read_store = original

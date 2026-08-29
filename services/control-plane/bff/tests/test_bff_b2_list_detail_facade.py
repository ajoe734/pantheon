"""
BFF-B2-001: Integration tests for the B2.1 Strategy / Persona / Capital /
Deployment list-detail facade (14 read endpoints).

Covers:
  - GET /bff/strategies           list + page_info + DTO shape
  - GET /bff/strategies/{id}      detail + 404 for unknown id
  - GET /bff/strategies/{id}/specs  sub-resource list
  - GET /bff/personas             list + page_info + DTO shape
  - GET /bff/personas/{id}        detail + 404 for unknown id
  - GET /bff/personas/{id}/route-policy
  - GET /bff/personas/{id}/evaluations
  - GET /bff/personas/{id}/memory
  - GET /bff/capital-pools        list + page_info
  - GET /bff/capital-pools/{id}   detail + 404 for unknown id
  - GET /bff/deployments          list + page_info
  - GET /bff/deployments/{id}     detail + 404 for unknown id
  - GET /bff/rebalances           list + page_info
  - GET /bff/rebalances/{id}      detail + 404 for unknown id
  - Unauthenticated requests return HTTP 401 for all 14 endpoints
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2:operator"}
NO_AUTH_HEADERS: dict = {}

_TS = "2026-05-23T00:00:00Z"


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = create_in_memory_read_surface_ports()
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    return TestClient(bff_main.app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_persona(client: TestClient, name: str = "Momentum Persona") -> str:
    """Create a persona via BFF and return its id."""
    import uuid
    key = f"b2-persona-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/personas",
        json={"name": name},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _seed_capital_pool(client: TestClient, name: str = "Main Pool") -> str:
    """Create a capital pool via BFF and return its id.

    bff_create_capital_pool returns the raw record (no data envelope).
    """
    import uuid
    key = f"b2-pool-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/capital-pools",
        json={"name": name},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body.get("id") or body.get("pool_id") or "")


def _seed_strategy(client: TestClient, name: str = "Alpha Strategy") -> str:
    """Create a strategy via BFF overlay and return its id."""
    import uuid
    key = f"b2-strategy-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/strategies",
        json={"name": name},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _seed_rebalance(client: TestClient, pool_id: str) -> str:
    """Create a rebalance via BFF and return its id.

    bff_create_rebalance returns a command response dict with rebalance_id at top level.
    """
    import uuid
    key = f"b2-rebalance-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/rebalances",
        json={"capital_pool_id": pool_id, "reason": "b2 test"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code in (201, 202), resp.text
    body = resp.json()
    return str(body.get("rebalance_id") or body.get("id") or "")


# ---------------------------------------------------------------------------
# 1. GET /bff/strategies
# ---------------------------------------------------------------------------

def test_bff_strategies_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            bff_main.read_store = original


def test_bff_strategies_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            _seed_strategy(client)
            resp = client.get("/bff/strategies", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
            item = items[0]
            assert "id" in item
            assert "name" in item
            assert "state" in item
            assert "risk" in item
            assert "personaIds" in item
            assert "capitalPoolId" in item
        finally:
            bff_main.read_store = original


def test_bff_strategies_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 2. GET /bff/strategies/{id}
# ---------------------------------------------------------------------------

def test_bff_strategy_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            sid = _seed_strategy(client, "Detail Test Strategy")
            resp = client.get(f"/bff/strategies/{sid}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            data = body["data"]
            assert data["id"] == sid
            assert "name" in data
            assert "state" in data
            assert "risk" in data
        finally:
            bff_main.read_store = original


def test_bff_strategy_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/nonexistent-strategy-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original


def test_bff_strategy_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 3. GET /bff/strategies/{id}/specs
# ---------------------------------------------------------------------------

def test_bff_strategy_specs_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            sid = _seed_strategy(client, "Specs Strategy")
            resp = client.get(f"/bff/strategies/{sid}/specs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_strategy_specs_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/any-id/specs", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 4. GET /bff/personas
# ---------------------------------------------------------------------------

def test_bff_personas_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            bff_main.read_store = original


def test_bff_personas_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            _seed_persona(client)
            resp = client.get("/bff/personas", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
            item = items[0]
            assert "id" in item
            assert "name" in item
            assert "state" in item
            assert "archetype" in item
        finally:
            bff_main.read_store = original


def test_bff_personas_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 5. GET /bff/personas/{id}
# ---------------------------------------------------------------------------

def test_bff_persona_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Detail Persona")
            resp = client.get(f"/bff/personas/{pid}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            data = body["data"]
            assert data["id"] == pid
            assert "name" in data
            assert "state" in data
            assert "archetype" in data
        finally:
            bff_main.read_store = original


def test_bff_persona_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/nonexistent-persona-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original


def test_bff_persona_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 6. GET /bff/personas/{id}/route-policy
# ---------------------------------------------------------------------------

def test_bff_persona_route_policy_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Route Policy Persona")
            resp = client.get(f"/bff/personas/{pid}/route-policy", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert body["data"]["personaId"] == pid
        finally:
            bff_main.read_store = original


def test_bff_persona_route_policy_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/ghost-persona/route-policy", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_persona_route_policy_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id/route-policy", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 7. GET /bff/personas/{id}/evaluations
# ---------------------------------------------------------------------------

def test_bff_persona_evaluations_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Eval Persona")
            resp = client.get(f"/bff/personas/{pid}/evaluations", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert isinstance(body["data"], list)
        finally:
            bff_main.read_store = original


def test_bff_persona_evaluations_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/ghost-persona/evaluations", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_persona_evaluations_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id/evaluations", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 8. GET /bff/personas/{id}/memory
# ---------------------------------------------------------------------------

def test_bff_persona_memory_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Memory Persona")
            resp = client.get(f"/bff/personas/{pid}/memory", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # memory endpoint returns a list of memory items under data
            assert "data" in body and "meta" in body and "page_info" in body
            assert isinstance(body["data"], list)
            assert body["meta"]["status"] == "degraded"
            assert body["meta"]["memory_source"]["reason"] == "memory_plane_unconfigured"
            assert body["meta"]["memory_source"]["fallback_used"] is False
        finally:
            bff_main.read_store = original


def test_bff_persona_memory_reads_canonical_memory_plane(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "hits": [
                        {
                            "type": "persona",
                            "relevance_score": 0.93,
                            "entry": {"memory_id": "pmem-1", "persona_id": captured["persona_id"]},
                        },
                        {"type": "institutional", "entry": {"entry_id": "inst-1"}},
                    ],
                    "authz": {"policy_version": "governance-authz.v1"},
                }
            ).encode()

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory:8080")
            monkeypatch.setattr(bff_main.urllib_request, "urlopen", fake_urlopen)
            client = _fresh_client(td)
            pid = _seed_persona(client, "Canonical Memory Persona")
            captured["persona_id"] = pid
            resp = client.get(f"/bff/personas/{pid}/memory", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == [
                {"memory_id": "pmem-1", "persona_id": pid, "relevance_score": 0.93}
            ]
            assert body["meta"]["status"] == "ok"
            source = body["meta"]["memory_source"]
            assert source["kind"] == "canonical_memory_plane"
            assert source["available"] is True
            assert source["workspace_is_source_of_truth"] is False
            assert "scope=persona" in captured["url"]
            assert f"persona_id={pid}" in captured["url"]
        finally:
            bff_main.read_store = original


def test_bff_persona_memory_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/ghost-persona/memory", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_persona_memory_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id/memory", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 9. GET /bff/capital-pools
# ---------------------------------------------------------------------------

def test_bff_capital_pools_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            bff_main.read_store = original


def test_bff_capital_pools_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            _seed_capital_pool(client)
            resp = client.get("/bff/capital-pools", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
            item = items[0]
            assert "id" in item or "pool_id" in item
        finally:
            bff_main.read_store = original


def test_bff_capital_pools_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 10. GET /bff/capital-pools/{id}
# ---------------------------------------------------------------------------

def test_bff_capital_pool_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            pool_id = _seed_capital_pool(client, "Detail Pool")
            resp = client.get(f"/bff/capital-pools/{pool_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_capital_pool_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools/nonexistent-pool-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original


def test_bff_capital_pool_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 11. GET /bff/deployments
# ---------------------------------------------------------------------------

def test_bff_deployments_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
            assert isinstance(body["data"], list)
        finally:
            bff_main.read_store = original


def test_bff_deployments_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 12. GET /bff/deployments/{id}
# ---------------------------------------------------------------------------

def test_bff_deployment_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments/nonexistent-deploy-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original


def test_bff_deployment_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 13. GET /bff/rebalances
# ---------------------------------------------------------------------------

def test_bff_rebalances_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            bff_main.read_store = original


def test_bff_rebalances_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            pool_id = _seed_capital_pool(client, "Rebalance Pool")
            _seed_rebalance(client, pool_id)
            resp = client.get("/bff/rebalances", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
        finally:
            bff_main.read_store = original


def test_bff_rebalances_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 14. GET /bff/rebalances/{id}
# ---------------------------------------------------------------------------

def test_bff_rebalance_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            pool_id = _seed_capital_pool(client, "Detail Rebalance Pool")
            rb_id = _seed_rebalance(client, pool_id)
            assert rb_id, "Expected a non-empty rebalance id"
            resp = client.get(f"/bff/rebalances/{rb_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_rebalance_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances/nonexistent-rb-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original


def test_bff_rebalance_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original

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
import uuid

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2:operator"}
NO_AUTH_HEADERS: dict = {}

_TS = "2026-05-23T00:00:00Z"


class _ListDetailFacadeTestStore:
    def __init__(self) -> None:
        self.ports = create_in_memory_read_surface_ports()
        self._personas: dict[str, dict[str, Any]] = {}
        self._pools: dict[str, dict[str, Any]] = {}
        self._rebalances: dict[str, dict[str, Any]] = {}
        self._route_policies: dict[str, dict[str, Any]] = {}
        self._evaluations: dict[str, list[dict[str, Any]]] = {}
        self._memories: dict[str, list[dict[str, Any]]] = {}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.ports, name, None)
        if attr is not None and callable(attr):
            def _safe_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return attr(*args, **kwargs)
                except TypeError:
                    return attr(*args)
            return _safe_wrapper
        if attr is not None:
            return attr
        raise AttributeError(f"'_ListDetailFacadeTestStore' has no attribute '{name}'")

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._personas.values())

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        persona_id = kwargs.get("persona_id") or kwargs.get("id") or f"persona-{len(self._personas) + 1}"
        name = kwargs.get("name") or persona_id
        archetype = kwargs.get("archetype") or "generalist"
        metadata = dict(kwargs.get("metadata") or {})
        metadata.setdefault("archetype", archetype)
        metadata.setdefault("owner", "op-b2")
        metadata.setdefault("risk_level", "low")
        metadata.setdefault("paper_ledger_id", f"ledger-{persona_id}")
        metadata.setdefault("capital_pool_id", "pool-main")
        metadata.setdefault("legacy_paper_capital_pool_id", "pool-main")
        metadata.setdefault("runtime_binding_id", f"runtime-{persona_id}")
        metadata.setdefault("deployment_stage", "paper")
        metadata.setdefault("capital_mode", "paper")
        item = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": name,
            "state": kwargs.get("state") or kwargs.get("lifecycle_state") or "active",
            "lifecycle_state": kwargs.get("lifecycle_state") or kwargs.get("state") or "active",
            "archetype": archetype,
            "created_at": kwargs.get("created_at") or "2026-05-23T00:00:00Z",
            "updated_at": kwargs.get("updated_at") or "2026-05-23T00:00:00Z",
            **kwargs,
            "metadata": metadata,
        }
        self._personas[persona_id] = item
        return item

    def get_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not persona_id:
            return None
        return self._personas.get(persona_id)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._pools.values())

    def create_capital_pool(self, **kwargs: Any) -> dict[str, Any]:
        pool_id = kwargs.get("pool_id") or kwargs.get("id") or f"pool-{len(self._pools) + 1}"
        item = {
            "id": pool_id,
            "pool_id": pool_id,
            "name": kwargs.get("name") or pool_id,
            "status": "active",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:00:00Z",
            **kwargs,
        }
        self._pools[pool_id] = item
        return item

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not pool_id:
            return None
        return self._pools.get(pool_id)

    def list_rebalances(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._rebalances.values())

    def create_rebalance(self, **kwargs: Any) -> dict[str, Any]:
        rb_id = kwargs.get("rebalance_id") or kwargs.get("id") or f"rb-{len(self._rebalances) + 1}"
        item = {
            "id": rb_id,
            "rebalance_id": rb_id,
            "status": "pending",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:00:00Z",
            **kwargs,
        }
        self._rebalances[rb_id] = item
        return item

    def get_rebalance(self, rebalance_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not rebalance_id:
            return None
        return self._rebalances.get(rebalance_id)

    def get_route_policy_for_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not persona_id:
            return None
        return self._route_policies.get(persona_id, {
            "persona_id": persona_id,
            "personaId": persona_id,
            "policy": "default",
            "route": "default",
            "version": "v1",
            "rules": [],
        })

    def get_persona_route_policy(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        return self.get_route_policy_for_persona(persona_id)

    def get_evaluations_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        if not persona_id:
            return []
        return self._evaluations.get(persona_id, [{"eval_id": f"eval-{persona_id}", "persona_id": persona_id, "score": 90.0, "status": "completed"}])

    def list_persona_evaluations(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        return self.get_evaluations_for_persona(persona_id)

    def get_allocation_evaluation(self, eval_id: Optional[str]) -> Optional[dict[str, Any]]:
        eval_id = eval_id or "eval-alloc-001"
        snapshot_id = "rk-snap-001"
        policy_version = "v1"
        line = {
            "ranking_snapshot_id": snapshot_id,
            "allocation_evaluation_id": eval_id,
            "allocation_policy_version": policy_version,
            "persona_id": "persona-alpha",
            "stage": "paper",
            "capital_scope": "pool",
            "capital_pool_id": "pool-main",
            "capital_sleeve_id": None,
            "current_weight": 0.0,
            "target_weight": 0.5,
            "delta": 0.5,
            "cap_reasons": [],
            "evidence_refs": [],
            "status": "admitted",
            "amount": 1000,
        }
        line["allocation_line_digest"] = bff_main._pm12_allocation_line_digest(line)
        content_digest = bff_main._stable_json_hash({
            "ranking_snapshot_id": snapshot_id,
            "allocation_evaluation_id": eval_id,
            "allocation_policy_version": policy_version,
            "lines": [line],
        })
        return {
            "id": eval_id,
            "allocation_evaluation_id": eval_id,
            "capital_pool_id": "pool-main",
            "status": "completed",
            "created_at": "2026-05-23T00:00:00Z",
            "ranking_snapshot_id": snapshot_id,
            "allocation_policy_version": policy_version,
            "content_digest": content_digest,
            "lines": [line],
            "admitted_lines": [line],
        }

    def list_persona_memories(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        if not persona_id:
            return []
        return self._memories.get(persona_id, [{"memory_id": f"mem-{persona_id}", "persona_id": persona_id, "content": "test memory", "created_at": "2026-05-23T00:00:00Z"}])

    def get_teaching_sessions_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        return [{"session_id": f"session-{persona_id}", "persona_id": persona_id}]

    def list_rankings(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": "rk-snap-001", "ranking_snapshot_id": "rk-snap-001", "snapshot_id": "rk-snap-001", "quarter": "2026Q2", "status": "admitted", "rankings": []}]

    def get_ranking(self, ranking_id: Optional[str]) -> Optional[dict[str, Any]]:
        return {"id": "rk-snap-001", "ranking_snapshot_id": "rk-snap-001", "snapshot_id": "rk-snap-001", "quarter": "2026Q2", "status": "admitted", "rankings": []}

    def get_quarterly_ranking_snapshot(self, snapshot_id: Optional[str]) -> Optional[dict[str, Any]]:
        return self.get_ranking_snapshot(snapshot_id)

    def get_ranking_snapshot(self, snapshot_id: Optional[str]) -> Optional[dict[str, Any]]:
        formula_version = getattr(bff_main, "_PM12_LEAGUE_FORMULA_VERSION", "v1")
        payload = {
            "surface": "quarterly",
            "period": "2026Q2",
            "formula_version": formula_version,
            "items": [],
        }
        digest = bff_main._stable_json_hash(payload)
        return {
            "id": snapshot_id or "rk-snap-001",
            "snapshot_id": snapshot_id or "rk-snap-001",
            "surface": "quarterly",
            "period": "2026Q2",
            "formula_version": formula_version,
            "items": [],
            "content_digest": digest,
            "status": "admitted",
        }


def _mock_create_capital_pool(payload: dict) -> dict:
    pool_id = payload.get("pool_id") or f"pool-{uuid.uuid4().hex[:8]}"
    pool = {
        "id": pool_id,
        "pool_id": pool_id,
        "name": payload.get("name", "Main Pool"),
        "status": payload.get("status", "active"),
        "owner_id": payload.get("owner_id", "op-b2"),
        "owner_type": payload.get("owner_type", "operator"),
        "currency": payload.get("currency", "USD"),
        "budget": payload.get("budget", 100000),
        "created_at": "2026-05-23T00:00:00Z",
        "updated_at": "2026-05-23T00:00:00Z",
    }
    if hasattr(bff_main.read_store, "_pools"):
        bff_main.read_store._pools[pool_id] = pool
    return pool


def _mock_create_rebalance(payload: dict) -> dict:
    reb_id = f"reb-{uuid.uuid4().hex[:8]}"
    item = {
        "id": reb_id,
        "rebalance_id": reb_id,
        "capital_pool_id": payload.get("capital_pool_id"),
        "status": "pending",
        "reason": payload.get("reason", "b2 test"),
        "created_at": "2026-05-23T00:00:00Z",
    }
    if hasattr(bff_main.read_store, "_rebalances"):
        bff_main.read_store._rebalances[reb_id] = item
    return item


def _mock_coordinate_persona_create(record: Any, payload: dict, owner: str) -> tuple:
    persona_id = getattr(record, "persona_id", None) or f"persona-{uuid.uuid4().hex[:8]}"
    tenant_id = str(getattr(record, "tenant_id", "") or "")
    archetype = payload.get("archetype") or "generalist"
    meta = {
        "archetype": archetype,
        "owner": owner,
        # Tenant-scoped persona reads fail closed for tenantless fixtures, so
        # preserve the canonical provisioning record's admitted tenant.
        "tenant_id": tenant_id,
        "risk_level": "low",
        "paper_ledger_id": f"ledger-{persona_id}",
        "paper_ledger": {
            "id": f"ledger-{persona_id}",
            "mode": "paper",
            "persona_id": persona_id,
            "is_isolated": True,
        },
        "evidence_refs": [],
        "capital_pool_id": "pool-main",
        "legacy_paper_capital_pool_id": "pool-main",
        "runtime_binding_id": f"runtime-{persona_id}",
        "deployment_stage": "paper",
        "capital_mode": "paper",
    }
    persona = {
        "id": persona_id,
        "persona_id": persona_id,
        "tenant_id": tenant_id,
        "name": payload.get("name", "Persona"),
        "state": "active",
        "lifecycle_state": "active",
        "archetype": archetype,
        "created_at": "2026-05-23T00:00:00Z",
        "updated_at": "2026-05-23T00:00:00Z",
        "metadata": meta,
    }
    if hasattr(bff_main.read_store, "_personas"):
        bff_main.read_store._personas[persona_id] = persona
    bff_main._PERSONA_BFF_OVERLAY[persona_id] = {
        "id": persona_id,
        "persona_id": persona_id,
        "name": persona["name"],
        "state": "active",
        "updatedAt": "2026-05-23T00:00:00Z",
        "archetype": archetype,
        "owner": owner,
        "risk": "low",
        "tenantId": tenant_id,
    }
    return record, persona, meta, None


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = _ListDetailFacadeTestStore()
    bff_main.create_capital_pool = _mock_create_capital_pool
    bff_main.create_rebalance = _mock_create_rebalance
    bff_main.create_capital_rebalance_proposal = _mock_create_rebalance
    bff_main._coordinate_persona_create = _mock_coordinate_persona_create
    bff_main.build_persona_runtime_profile = lambda *a, **kw: type("Profile", (), {"to_dict": lambda s: {}})()
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
    eval_rec = bff_main.read_store.get_allocation_evaluation("eval-alloc-001") or {}
    lines = eval_rec.get("lines") or [{"pool_id": pool_id, "amount": 1000}]
    resp = client.post(
        "/bff/rebalances",
        json={
            "capital_pool_id": pool_id,
            "reason": "b2 test",
            "ranking_snapshot_id": "rk-snap-001",
            "allocation_evaluation_id": "eval-alloc-001",
            "allocation_policy_version": "v1",
            "simulation": {"passed": True},
            "constraints": {"max_drawdown": 0.1},
            "rollback_target": "rb-target-001",
            "lines": lines,
        },
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

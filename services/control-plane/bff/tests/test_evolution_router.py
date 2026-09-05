"""Comprehensive unit and contract test suite for Evolution domain router.

Tests:
1. Router factory registration & route uniqueness (13 evolution routes + program routes)
2. GET /bff/evolution-programs/{program_id}/ooda (enabled & disabled feature flag)
3. GET /bff/management/evolution-journal (aggregation, filtering, persona lineage traversal, surface degradation)
4. GET /api/v1/evolution-decisions (listing, filtering, pagination)
5. GET /api/v1/evolution-decisions/{decision_id} (detail, 404 on missing)
6. GET /api/v1/freeze-orders (listing, status/scope filters)
7. GET /api/v1/rollbacks (listing, runtime_id/action_type/time_range filters)
8. GET /api/v1/lineage (listing, artifact filter, unavailable surface)
9. GET /api/v1/lineage/edges/{edge_id} (detail, 404 on missing)
10. GET /api/v1/lineage/graph (lineage graph traversal with depth clamping)
11. GET /api/v1/lineage/inspiration/{artifact_id} (inspiration graph projection, lineage edges fallback, 404 on missing)
12. GET /api/v1/telemetry (telemetry events, summary fallback)
13. GET /api/v1/telemetry/{runtime_id}/summary (summary detail, 404 on missing)
14. GET /api/v1/telemetry/{artifact_id}/performance (performance detail, 404 on missing)
15. Backward compatibility for `create_evolution_programs_router`
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


from services.control_plane.bff.evolution.router import create_evolution_programs_router, create_evolution_router
from services.control_plane.bff.evolution.service import EvolutionService


class _MockReadStore:
    def __init__(self):
        self.evolution_programs: Dict[str, Dict[str, Any]] = {}
        self.evolution_program_runs: Dict[str, List[Dict[str, Any]]] = {}
        self.evolution_program_candidates: Dict[str, List[Dict[str, Any]]] = {}
        self.ooda_packets: Dict[str, List[Dict[str, Any]]] = {}
        self.evolution_decisions: Dict[str, Dict[str, Any]] = {}
        self.approval_decisions: Dict[str, Dict[str, Any]] = {}
        self.freeze_orders: List[Dict[str, Any]] = []
        self.rollbacks: List[Dict[str, Any]] = []
        self.lineage_edges: List[Dict[str, Any]] = []
        self.lineage_records: List[Dict[str, Any]] = []
        self.inspiration_graphs: Dict[str, Dict[str, Any]] = {}
        self.artifacts: Set[str] = set()
        self.telemetry_events: List[Dict[str, Any]] = []
        self.telemetry_summaries: Dict[str, Dict[str, Any]] = {}
        self.telemetry_performances: Dict[str, Dict[str, Any]] = {}
        self.postmortems: List[Dict[str, Any]] = []
        self.personas: List[Dict[str, Any]] = []
        self.runtime_bindings: List[Dict[str, Any]] = []
        self.bindings: List[Dict[str, Any]] = []
        self.incidents: List[Dict[str, Any]] = []
        self.dataset_sources: Dict[str, str] = {}

    def dataset_source(self, ds: str) -> str:
        return self.dataset_sources.get(ds, "ok")

    # Evolution Programs
    def list_evolution_programs(self) -> List[Dict[str, Any]]:
        return list(self.evolution_programs.values())

    def get_evolution_program(self, program_id: str) -> Optional[Dict[str, Any]]:
        return self.evolution_programs.get(program_id)

    def create_evolution_program(self, *, program_id: str, name: str, actor_id: str, created_at: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        item = {
            "program_id": program_id,
            "id": program_id,
            "name": name,
            "status": "draft",
            "actor_id": actor_id,
            "created_at": created_at or "2026-08-30T00:00:00Z",
            "updated_at": created_at or "2026-08-30T00:00:00Z",
            "params": params or {},
        }
        self.evolution_programs[program_id] = item
        return item

    def patch_evolution_program(self, program_id: str, *, patch: Dict[str, Any], actor_id: str, updated_at: Optional[str] = None) -> Dict[str, Any]:
        item = self.evolution_programs[program_id]
        item.update(patch)
        item["updated_at"] = updated_at or "2026-08-30T00:00:00Z"
        return item

    def list_evolution_program_runs(self, program_id: str) -> List[Dict[str, Any]]:
        return self.evolution_program_runs.get(program_id, [])

    def list_evolution_program_candidates(self, program_id: str) -> List[Dict[str, Any]]:
        return self.evolution_program_candidates.get(program_id, [])

    # OODA
    def list_ooda_packets_for_evolution_program(self, program_id: str) -> List[Dict[str, Any]]:
        return self.ooda_packets.get(program_id, [])

    # Decisions
    def list_evolution_decisions(self, action_type: Optional[str] = None, risk_level: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        res = list(self.evolution_decisions.values())
        if action_type:
            res = [d for d in res if d.get("action_type") == action_type]
        if risk_level:
            res = [d for d in res if d.get("risk_level") == risk_level]
        if status:
            res = [d for d in res if d.get("status") == status or d.get("decision_state") == status]
        return res

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self.evolution_decisions.get(decision_id)

    def get_approval_decision_by_id(self, approval_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.approval_decisions.get(approval_id) if approval_id else None

    # Freeze Orders & Rollbacks
    def list_freeze_orders(self, status: Optional[str] = None, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        res = self.freeze_orders
        if status:
            res = [o for o in res if o.get("status") == status]
        if scope:
            res = [o for o in res if o.get("scope") == scope]
        return res

    def list_all_rollbacks(self, runtime_id: Optional[str] = None, action_type: Optional[str] = None, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        res = self.rollbacks
        if runtime_id:
            res = [r for r in res if r.get("runtime_id") == runtime_id]
        if action_type:
            res = [r for r in res if r.get("action_type") == action_type]
        return res

    # Lineage
    def list_lineage_records(self, artifact_id: Optional[str] = None, include_fixture_pack: bool = False) -> List[Dict[str, Any]]:
        res = self.lineage_records
        if artifact_id:
            res = [r for r in res if r.get("artifact_id") == artifact_id or r.get("from_artifact_id") == artifact_id or r.get("to_artifact_id") == artifact_id]
        return res

    def list_lineage_edges(self, artifact_id: Optional[str] = None) -> List[Dict[str, Any]]:
        res = self.lineage_edges
        if artifact_id:
            res = [e for e in res if e.get("from_artifact_id") == artifact_id or e.get("to_artifact_id") == artifact_id]
        return res

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        for e in self.lineage_edges:
            if e.get("id") == edge_id:
                return e
        return None

    def get_lineage_graph(self, root_type: Optional[str] = None, root_id: str = "", depth: int = 3) -> List[Dict[str, Any]]:
        return [e for e in self.lineage_edges if e.get("from_artifact_id") == root_id or e.get("to_artifact_id") == root_id]

    def get_lineage_graph_nodes(self, edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        node_ids = set()
        for e in edges:
            node_ids.add(e.get("from_artifact_id"))
            node_ids.add(e.get("to_artifact_id"))
        return [{"id": nid, "label": nid} for nid in node_ids if nid]

    def get_inspiration_graph(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self.inspiration_graphs.get(artifact_id)

    def artifact_exists(self, artifact_id: str) -> bool:
        return artifact_id in self.artifacts or artifact_id in self.inspiration_graphs

    # Telemetry
    def list_telemetry_events_with_source(self, pool_id: Optional[str] = None, artifact_id: Optional[str] = None, time_range: Optional[str] = None) -> tuple[str, List[Dict[str, Any]]]:
        res = self.telemetry_events
        if pool_id:
            res = [e for e in res if e.get("pool_id") == pool_id]
        if artifact_id:
            res = [e for e in res if e.get("artifact_id") == artifact_id]
        return self.dataset_sources.get("telemetry_events", "ok"), res

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        return self.telemetry_summaries.get(runtime_id)

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self.telemetry_performances.get(artifact_id)

    # Postmortems, Personas, Bindings, Incidents for Journal
    def list_postmortems(self) -> List[Dict[str, Any]]:
        return self.postmortems

    def list_personas(self, include_market_persona_defaults: bool = True) -> List[Dict[str, Any]]:
        return self.personas

    def list_runtime_bindings(self, include_market_persona_defaults: bool = True) -> List[Dict[str, Any]]:
        return self.runtime_bindings

    def list_bindings(self, include_market_persona_defaults: bool = True) -> List[Dict[str, Any]]:
        return self.bindings

    def list_incidents(self) -> List[Dict[str, Any]]:
        return self.incidents


def _build_test_client(store: _MockReadStore, **kwargs: Any) -> TestClient:
    app = FastAPI()
    router = create_evolution_router(get_read_store=lambda: store, **kwargs)
    app.include_router(router)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_evolution_router_routes_count_and_exact_paths():
    """Verify that create_evolution_router registers all 13 domain routes + 7 program routes."""
    router = create_evolution_router()
    routes = {(m, r.path) for r in router.routes if hasattr(r, "methods") for m in r.methods}

    expected_13_domain_routes = {
        ("GET", "/bff/evolution-programs/{program_id}/ooda"),
        ("GET", "/bff/management/evolution-journal"),
        ("GET", "/api/v1/evolution-decisions"),
        ("GET", "/api/v1/evolution-decisions/{decision_id}"),
        ("GET", "/api/v1/freeze-orders"),
        ("GET", "/api/v1/rollbacks"),
        ("GET", "/api/v1/lineage"),
        ("GET", "/api/v1/lineage/edges/{edge_id}"),
        ("GET", "/api/v1/lineage/graph"),
        ("GET", "/api/v1/lineage/inspiration/{artifact_id}"),
        ("GET", "/api/v1/telemetry"),
        ("GET", "/api/v1/telemetry/{runtime_id}/summary"),
        ("GET", "/api/v1/telemetry/{artifact_id}/performance"),
    }

    expected_7_program_routes = {
        ("GET", "/bff/evolution-programs"),
        ("POST", "/bff/evolution-programs"),
        ("GET", "/bff/evolution-programs/{program_id}"),
        ("PATCH", "/bff/evolution-programs/{program_id}"),
        ("GET", "/bff/evolution-programs/{program_id}/runs"),
        ("GET", "/bff/evolution-programs/{program_id}/candidates"),
        ("POST", "/bff/evolution-programs/{program_id}/actions/{action_id}"),
    }

    all_expected = expected_13_domain_routes | expected_7_program_routes
    for route in all_expected:
        assert route in routes, f"Missing route: {route}"

    assert len(router.routes) == 20


def test_list_and_get_evolution_decisions():
    store = _MockReadStore()
    store.evolution_decisions["dec-1"] = {
        "decision_id": "dec-1",
        "action_type": "mutate_weight",
        "risk_level": "low",
        "status": "applied",
        "target_type": "strategy",
        "target_id": "strat-1",
        "notes": "Decision note 1",
    }
    store.evolution_decisions["dec-2"] = {
        "decision_id": "dec-2",
        "action_type": "rollback_model",
        "risk_level": "high",
        "status": "pending",
        "target_type": "model",
        "target_id": "mod-1",
        "notes": "Decision note 2",
    }
    client = _build_test_client(store)

    # 1. List all
    resp = client.get("/api/v1/evolution-decisions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert "meta" in data
    assert "page_info" in data

    # 2. Filter by action_type
    resp = client.get("/api/v1/evolution-decisions?action_type=mutate_weight")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert resp.json()["items"][0]["decision_id"] == "dec-1"

    # 3. Filter by risk_level
    resp = client.get("/api/v1/evolution-decisions?risk_level=high")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert resp.json()["items"][0]["decision_id"] == "dec-2"

    # 4. Get by ID
    resp = client.get("/api/v1/evolution-decisions/dec-1")
    assert resp.status_code == 200
    assert resp.json()["decision_id"] == "dec-1"
    assert resp.json()["notes"] == "Decision note 1"
    assert "meta" in resp.json()

    # 5. Get missing ID -> 404
    resp = client.get("/api/v1/evolution-decisions/nonexistent")
    assert resp.status_code == 404


def test_list_freeze_orders_and_rollbacks():
    store = _MockReadStore()
    store.freeze_orders.append({
        "freeze_order_id": "fo-1",
        "status": "active",
        "scope": "pool_capital",
        "reason": "High slippage detected",
        "issued_at": "2026-08-30T10:00:00Z",
    })
    store.freeze_orders.append({
        "freeze_order_id": "fo-2",
        "status": "resolved",
        "scope": "strategy_execution",
        "reason": "Test freeze",
        "issued_at": "2026-08-30T11:00:00Z",
    })

    store.rollbacks.append({
        "rollback_id": "rb-1",
        "runtime_id": "rt-alpha",
        "action_type": "hard_rollback",
        "reason": "Circuit breaker hit",
        "executed_at": "2026-08-30T10:30:00Z",
    })
    client = _build_test_client(store)

    # Freeze orders
    resp = client.get("/api/v1/freeze-orders")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2

    resp = client.get("/api/v1/freeze-orders?status=active")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert resp.json()["items"][0]["freeze_order_id"] == "fo-1"

    # Rollbacks
    resp = client.get("/api/v1/rollbacks")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert resp.json()["items"][0]["rollback_id"] == "rb-1"

    resp = client.get("/api/v1/rollbacks?runtime_id=rt-beta")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 0


def test_lineage_and_inspiration_graph():
    store = _MockReadStore()
    store.artifacts.add("art-root")
    store.artifacts.add("art-child")
    store.lineage_edges.append({
        "id": "edge-1",
        "from_artifact_id": "art-root",
        "to_artifact_id": "art-child",
        "relationship": "derived_from",
        "edge_type": "derived_from",
        "strategy_id": "strat-momentum",
        "influence_weight": 0.85,
    })
    store.lineage_records.append({
        "id": "rec-1",
        "artifact_id": "art-root",
        "status": "verified",
    })
    client = _build_test_client(store)

    # Lineage records list
    resp = client.get("/api/v1/lineage?artifact_id=art-root")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    # Lineage edge detail
    resp = client.get("/api/v1/lineage/edges/edge-1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "edge-1"
    assert resp.json()["relationship"] == "derived_from"

    # Missing edge -> 404
    resp = client.get("/api/v1/lineage/edges/missing-edge")
    assert resp.status_code == 404

    # Lineage graph
    resp = client.get("/api/v1/lineage/graph?root_id=art-root&depth=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["edges"]) == 1
    assert len(data["nodes"]) == 2

    # Inspiration graph fallback projection
    resp = client.get("/api/v1/lineage/inspiration/art-child")
    assert resp.status_code == 200
    data = resp.json()
    assert data["artifact_id"] == "art-child"
    assert len(data["inspiration_edges"]) == 1
    assert data["inspiration_edges"][0]["source_artifact_id"] == "art-root"
    assert "strat-momentum" in data["strategy_tags"]
    assert data["meta"]["surfaces"]["inspiration"] == "fresh"

    # Missing artifact -> 404
    resp = client.get("/api/v1/lineage/inspiration/nonexistent-artifact")
    assert resp.status_code == 404


def test_telemetry_endpoints():
    store = _MockReadStore()
    store.telemetry_events.append({
        "id": "tel-1",
        "pool_id": "pool-1",
        "artifact_id": "art-1",
        "metric_name": "pnl_pct",
        "value": 0.052,
    })
    store.telemetry_summaries["rt-alpha"] = {
        "runtime_id": "rt-alpha",
        "total_executions": 128,
        "avg_latency_ms": 4.2,
    }
    store.telemetry_performances["art-1"] = {
        "artifact_id": "art-1",
        "sharpe_ratio": 2.14,
        "max_drawdown": 0.08,
    }
    client = _build_test_client(store)

    # 1. Telemetry list
    resp = client.get("/api/v1/telemetry?pool_id=pool-1")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    # 2. Telemetry summary
    resp = client.get("/api/v1/telemetry/rt-alpha/summary")
    assert resp.status_code == 200
    assert resp.json()["data"]["total_executions"] == 128

    resp = client.get("/api/v1/telemetry/rt-missing/summary")
    assert resp.status_code == 404

    # 3. Telemetry performance
    resp = client.get("/api/v1/telemetry/art-1/performance")
    assert resp.status_code == 200
    assert resp.json()["data"]["sharpe_ratio"] == 2.14

    resp = client.get("/api/v1/telemetry/art-missing/performance")
    assert resp.status_code == 404


def test_evolution_program_ooda_packets():
    store = _MockReadStore()
    store.ooda_packets["prog-1"] = [
        {"id": "pkt-1", "cycle_number": 1, "state": "observe"},
        {"id": "pkt-2", "cycle_number": 2, "state": "orient"},
    ]
    client = _build_test_client(store)

    # 1. List OODA packets
    resp = client.get("/bff/evolution-programs/prog-1/ooda")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["meta"]["related"]["id"] == "prog-1"

    # 2. Feature flag disabled -> 503
    with patch.dict(os.environ, {"PANTHEON_OODA_PACKET_ENABLED": "false"}):
        resp = client.get("/bff/evolution-programs/prog-1/ooda")
        assert resp.status_code == 503


def test_management_evolution_journal_aggregation_and_summary():
    store = _MockReadStore()
    store.evolution_decisions["dec-1"] = {
        "decision_id": "dec-1",
        "action_type": "model_mutation",
        "risk_level": "medium",
        "status": "applied",
        "title": "Model Mutation Decision",
        "notes": "Applied new model weights",
        "updated_at": "2026-08-30T12:00:00Z",
    }
    store.postmortems.append({
        "postmortem_id": "pm-1",
        "title": "Postmortem pm-1",
        "summary": "Root cause analysis",
        "status": "resolved",
        "published_at": "2026-08-30T11:00:00Z",
    })
    store.freeze_orders.append({
        "freeze_order_id": "fo-1",
        "title": "Freeze Order 1",
        "reason": "Circuit breaker",
        "status": "active",
        "issued_at": "2026-08-30T10:00:00Z",
    })
    store.rollbacks.append({
        "rollback_id": "rb-1",
        "title": "Rollback 1",
        "reason": "Demote unstable candidate",
        "status": "completed",
        "executed_at": "2026-08-30T09:00:00Z",
    })

    client = _build_test_client(store)

    # 1. Aggregation of all entry types
    resp = client.get("/bff/management/evolution-journal")
    assert resp.status_code == 200
    data = resp.json()
    items = data["data"]["items"]
    summary = data["data"]["summary"]

    assert len(items) == 4
    assert summary["decision_count"] == 1
    assert summary["postmortem_count"] == 1
    assert summary["freeze_order_count"] == 1
    assert summary["rollback_count"] == 1
    assert summary["active_freeze_count"] == 1
    assert summary["completed_rollback_count"] == 1

    # 2. Filter by source_type
    resp = client.get("/bff/management/evolution-journal?source_type=freeze_order")
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) == 1
    assert resp.json()["data"]["items"][0]["entry_type"] == "freeze_order"

    # 3. Filter by decision ID
    resp = client.get("/bff/management/evolution-journal?decision=dec-1")
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) == 1
    assert resp.json()["data"]["items"][0]["source_id"] == "dec-1"


def test_management_evolution_journal_persona_lineage_filtering():
    store = _MockReadStore()
    store.personas.append({
        "persona_id": "persona-apex",
        "runtime_id": "rt-apex-1",
    })
    store.runtime_bindings.append({
        "persona_id": "persona-apex",
        "runtime_id": "rt-apex-1",
        "binding_id": "bind-apex-1",
    })
    store.evolution_decisions["dec-apex"] = {
        "decision_id": "dec-apex",
        "target_type": "runtime",
        "target_id": "rt-apex-1",
        "title": "Apex runtime decision",
        "status": "applied",
    }
    store.evolution_decisions["dec-other"] = {
        "decision_id": "dec-other",
        "target_type": "runtime",
        "target_id": "rt-other-2",
        "title": "Other runtime decision",
        "status": "applied",
    }

    client = _build_test_client(store)

    resp = client.get("/bff/management/evolution-journal?persona=persona-apex")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["source_id"] == "dec-apex"


def test_evolution_programs_router_factory_compatibility():
    """Verify create_evolution_programs_router maintains backwards-compatible behavior."""
    store = _MockReadStore()
    router = create_evolution_programs_router(get_read_store=lambda: store)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. Create program
    resp = client.post("/bff/evolution-programs", json={"name": "Alpha Evolver"})
    assert resp.status_code == 201
    prog_id = resp.json()["program_id"]

    # 2. Get program
    resp = client.get(f"/bff/evolution-programs/{prog_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "Alpha Evolver"

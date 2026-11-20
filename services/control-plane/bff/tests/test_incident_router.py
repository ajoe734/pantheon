"""Comprehensive tests for BFF Incident domain router (OPGAP-BE-INCIDENT-ROUTER-V2-20260830).

Tests:
1. Exact 27 route decorators/endpoints mounted on the router
2. Operator alerts and risk alerts listing / retrieval
3. Incident listing, detail, filtering, and streaming
4. Composed incident response (PKT-002) and post-incident review views
5. Kill switch status and role verification (admin required)
6. Incident creation, overlays, idempotency, and action commands
7. Alert acknowledgment and suppression
8. Audit trail querying, entity audit, and export
9. Fast-path semantic commands and dry-run execution
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.incidents.service import IncidentService
from services.control_plane.bff.models import ErrorCode, OperatorIdentity


class MockReadStore:
    """Mock read store covering incident, alert, audit, kill switch, and telemetry reads."""

    def __init__(
        self,
        incidents: Optional[Dict[str, Dict[str, Any]]] = None,
        postmortems: Optional[Dict[str, Dict[str, Any]]] = None,
        kill_switch: Optional[Dict[str, Any]] = None,
        audit_events: Optional[List[Dict[str, Any]]] = None,
        bindings: Optional[Dict[str, Dict[str, Any]]] = None,
        runtime_bindings: Optional[Dict[str, Dict[str, Any]]] = None,
        telemetry_summaries: Optional[Dict[str, Dict[str, Any]]] = None,
        evolution_decisions: Optional[List[Dict[str, Any]]] = None,
        lineage_edges: Optional[List[Dict[str, Any]]] = None,
        telemetry_performance: Optional[Dict[str, Any]] = None,
        status: str = "ok",
    ) -> None:
        self.incidents = dict(incidents or {})
        self.postmortems = dict(postmortems or {})
        self.kill_switch = dict(
            kill_switch
            or {
                "status": "armed",
                "safe_mode_status": "off",
                "active": False,
                "last_triggered_at": "2026-08-30T12:00:00Z",
                "last_confirmed_at": "2026-08-30T12:05:00Z",
                "active_commands": ["cmd-ks-1"],
            }
        )
        self.audit_events = list(audit_events or [])
        self.bindings = dict(bindings or {})
        self.runtime_bindings = dict(runtime_bindings or {})
        self.telemetry_summaries = dict(telemetry_summaries or {})
        self.evolution_decisions = list(evolution_decisions or [])
        self.lineage_edges = list(lineage_edges or [])
        self.telemetry_performance = dict(telemetry_performance or {})
        self.status = status

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        res = list(self.incidents.values())
        if status:
            statuses = {s.strip().lower() for s in status.split(",") if s.strip()}
            res = [i for i in res if str(i.get("status") or "").lower() in statuses]
        if severity:
            res = [i for i in res if str(i.get("severity") or "").lower() == severity.lower()]
        if affected_pool_id:
            res = [
                i
                for i in res
                if (i.get("capital_pool_id") or i.get("affected_pool_id")) == affected_pool_id
            ]
        return res

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.incidents.get(incident_id)

    def create_incident(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        inc_id = payload.get("incident_id") or payload.get("id") or "inc-test"
        if inc_id in self.incidents:  # the incident owner rejects a duplicate id with 409
            raise HTTPException(status_code=409, detail=f"IncidentCase '{inc_id}' already exists")
        self.incidents[inc_id] = payload
        return payload

    def update_incident_status(self, incident_id: str, status: str, resolved_at: Optional[str] = None) -> Dict[str, Any]:
        clean_id = incident_id.strip()
        if clean_id in self.incidents:
            self.incidents[clean_id]["status"] = status
            if resolved_at:
                self.incidents[clean_id]["resolved_at"] = resolved_at
            return self.incidents[clean_id]
        return {"incident_id": clean_id, "status": status}

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        for pm in self.postmortems.values():
            if pm.get("incident_id") == incident_id:
                return pm
        return None

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self.postmortems.get(report_id)

    def get_kill_switch_status(self) -> Dict[str, Any]:
        return self.kill_switch

    def list_governance_review_queue_items(self) -> List[Dict[str, Any]]:
        return [
            {
                "item_id": "gq-1",
                "status": "pending",
                "risk_level": "high",
                "item_type": "StrategyReview",
                "submitted_at": "2026-08-30T10:00:00Z",
            }
        ]

    def list_approval_queue_items(self) -> List[Dict[str, Any]]:
        return [
            {
                "decision_id": "dec-1",
                "decision_state": "pending",
                "risk_level": "medium",
                "decision_type": "DeploymentApproval",
                "submitted_at": "2026-08-30T10:30:00Z",
            }
        ]

    def list_runtime_bindings(self) -> List[Dict[str, Any]]:
        return list(self.runtime_bindings.values())

    def get_binding(self, binding_id: str) -> Optional[Dict[str, Any]]:
        return self.bindings.get(binding_id)

    def get_runtime_binding(self, binding_id: str) -> Optional[Dict[str, Any]]:
        return self.runtime_bindings.get(binding_id)

    def get_runtime_binding_by_runtime_id(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not runtime_id:
            return None
        for b in self.runtime_bindings.values():
            if b.get("runtime_id") == runtime_id:
                return b
        return None

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        return self.telemetry_summaries.get(runtime_id)

    def get_evolution_decisions_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.evolution_decisions if e.get("incident_id") == incident_id]

    def list_lineage_edges(self, artifact_id: Optional[str] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        if artifact_id:
            return [e for e in self.lineage_edges if e.get("artifact_id") == artifact_id]
        return list(self.lineage_edges)

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self.telemetry_performance.get(artifact_id)

    def list_governance_audit_events(
        self,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[Any] = None,
        to_ts: Optional[Any] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        res = self.audit_events
        if actor:
            res = [e for e in res if e.get("actor") == actor]
        if action_types:
            res = [e for e in res if e.get("action_type") in action_types or e.get("action") in action_types]
        if target_type:
            res = [e for e in res if e.get("target_type") == target_type]
        return res

    def dataset_source(self, dataset: str) -> str:
        return self.status


class MockCommandStore:
    def __init__(self) -> None:
        self.commands: List[Dict[str, Any]] = []

    def submit_terminal_command(self, **kwargs: Any) -> Dict[str, Any]:
        self.commands.append(kwargs)
        return {"command_id": kwargs.get("command_id"), "status": "executed"}

    def get_command_by_idempotency_key(self, idempotency_key: str, *, operator_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for cmd in self.commands:
            record = (cmd.get("foundation_context") or {}).get("idempotency_record") or {}
            if record.get("idempotency_key") == idempotency_key and (cmd.get("audit_context") or {}).get("operator_id") == operator_id:
                return {"command_id": cmd["command_id"], "foundation": cmd["foundation_context"], "audit": cmd["audit_context"]}
        return None


def _build_test_client(
    store: Optional[MockReadStore] = None,
    cmd_store: Optional[MockCommandStore] = None,
    identity_roles: Optional[List[str]] = None,
) -> TestClient:
    read_store = store or MockReadStore()
    command_store = cmd_store or MockCommandStore()

    def extract_identity(auth: Optional[str] = None, **kwargs: Any) -> OperatorIdentity:
        if auth and "admin" in auth:
            return OperatorIdentity(operator_id="admin-user", roles=["admin", "operator", "viewer"])
        roles = identity_roles or ["operator", "viewer"]
        return OperatorIdentity(operator_id="test-op", roles=roles)

    service = IncidentService(
        get_read_store=lambda: read_store,
        get_command_store=lambda: command_store,
    )

    router = create_incident_router(
        service=service,
        get_read_store=lambda: read_store,
        get_command_store=lambda: command_store,
        extract_identity=extract_identity,
    )

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_incident_router_registers_all_27_decorators() -> None:
    """Verify that create_incident_router defines exactly the expected 27 routes."""
    router = create_incident_router()
    routes = [(list(getattr(r, "methods", set())), getattr(r, "path", "")) for r in router.routes]

    # Verify route count
    assert len(router.routes) == 27, f"Expected 27 route decorators, got {len(router.routes)}"

    # Check key paths
    paths = [r[1] for r in routes]
    expected_paths = [
        "/api/v1/operator/alerts",
        "/api/v1/incidents",
        "/api/v1/incidents/stream",
        "/api/v1/incidents/{incident_id}",
        "/api/v1/kill-switch/status",
        "/api/v1/operator/incident-response/{incident_id}",
        "/api/v1/operator/post-incident-review/{incident_id}",
        "/bff/risk/alerts",
        "/bff/risk/alerts/{alert_id}",
        "/bff/risk/alerts/{alert_id}/actions/{action_id}",
        "/bff/incidents",
        "/bff/incidents",
        "/bff/incidents/{incident_id}",
        "/bff/incidents/{incident_id}/actions/{action_id}",
        "/bff/alerts",
        "/bff/alerts/{alert_id}",
        "/bff/alerts/{alert_id}/acknowledge",
        "/bff/audit",
        "/bff/audit/events",
        "/bff/audit/entities/{entity_type}/{entity_id}",
        "/bff/audit/export",
        "/bff/audit/export",
        "/bff/alerts/{id}/escalate-incident",
        "/bff/incidents/{id}/append-postmortem",
        "/bff/incidents/{id}/resolve",
        "/bff/incidents/{id}/rollback-deployment",
        "/bff/incidents/{id}/start-mitigation",
    ]

    for p in expected_paths:
        assert p in paths, f"Missing path: {p}"

    # Verify stream is registered BEFORE {incident_id}
    stream_idx = next(i for i, r in enumerate(routes) if r[1] == "/api/v1/incidents/stream")
    detail_idx = next(i for i, r in enumerate(routes) if r[1] == "/api/v1/incidents/{incident_id}")
    assert stream_idx < detail_idx, "SSE stream route must precede parameterized incident_id route"


def test_operator_alerts_aggregation() -> None:
    """Test GET /api/v1/operator/alerts and GET /bff/risk/alerts aggregation."""
    incidents = {
        "inc-1": {
            "incident_id": "inc-1",
            "title": "Database CPU High",
            "severity": "high",
            "status": "open",
            "opened_at": "2026-08-30T11:00:00Z",
        }
    }
    runtime_bindings = {
        "rb-1": {
            "id": "rb-1",
            "runtime_id": "rt-1",
            "status": "degraded",
        }
    }
    telemetry_summaries = {
        "rt-1": {
            "runtime_id": "rt-1",
            "drawdown": 0.12,
            "fill_rate": 0.85,
        }
    }
    store = MockReadStore(
        incidents=incidents,
        runtime_bindings=runtime_bindings,
        telemetry_summaries=telemetry_summaries,
    )
    client = _build_test_client(store)

    resp = client.get("/api/v1/operator/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert "alerts" in data
    assert "summary" in data
    assert data["summary"]["total_active"] >= 1

    # Check /bff/risk/alerts
    resp_risk = client.get("/bff/risk/alerts")
    assert resp_risk.status_code == 200
    assert resp_risk.json()["summary"]["total_active"] == data["summary"]["total_active"]


def test_incident_list_and_detail() -> None:
    """Test GET /api/v1/incidents and GET /api/v1/incidents/{incident_id}."""
    incidents = {
        "inc-1": {
            "incident_id": "inc-1",
            "title": "Paper drawdown warning",
            "severity": "sev1",
            "status": "open",
            "capital_pool_id": "pool-a",
            "created_at": "2026-08-30T10:00:00Z",
        },
        "inc-2": {
            "incident_id": "inc-2",
            "title": "Minor latency",
            "severity": "sev3",
            "status": "resolved",
            "capital_pool_id": "pool-b",
            "created_at": "2026-08-30T09:00:00Z",
        },
    }
    store = MockReadStore(incidents=incidents)
    client = _build_test_client(store)

    # 1. List all
    resp = client.get("/api/v1/incidents")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert "meta" in body

    # 2. Filter by status
    resp_open = client.get("/api/v1/incidents?status=open")
    assert resp_open.status_code == 200
    assert len(resp_open.json()["items"]) == 1
    assert resp_open.json()["items"][0]["incident_id"] == "inc-1"

    # 3. Get detail
    resp_detail = client.get("/api/v1/incidents/inc-1")
    assert resp_detail.status_code == 200
    assert resp_detail.json()["data"]["incident_id"] == "inc-1"

    # 4. Missing detail -> 404
    resp_missing = client.get("/api/v1/incidents/inc-nonexistent")
    assert resp_missing.status_code == 404


def test_kill_switch_status_admin_gate() -> None:
    """Test GET /api/v1/kill-switch/status enforces admin role."""
    client_operator = _build_test_client(identity_roles=["operator", "viewer"])
    resp_forbidden = client_operator.get("/api/v1/kill-switch/status")
    assert resp_forbidden.status_code == 403

    client_admin = _build_test_client(identity_roles=["admin", "operator"])
    resp_ok = client_admin.get("/api/v1/kill-switch/status", headers={"Authorization": "Bearer admin-user:admin"})
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert "kill_switch" in data
    assert "allowedActions" in data
    assert data["kill_switch"]["status"] == "armed"


def test_composed_incident_response_view() -> None:
    """Test GET /api/v1/operator/incident-response/{incident_id}."""
    incidents = {
        "inc-1": {
            "incident_id": "inc-1",
            "title": "High Latency Incident",
            "severity": "sev1",
            "status": "open",
            "runtime_id": "rt-1",
            "binding_id": "rb-1",
            "persona_capital_binding_id": "pcb-1",
        }
    }
    bindings = {
        "pcb-1": {
            "id": "pcb-1",
            "persona_id": "p-1",
            "capital_pool_id": "pool-1",
            "stage": "paper",
            "status": "active",
        }
    }
    runtime_bindings = {
        "rb-1": {
            "id": "rb-1",
            "runtime_id": "rt-1",
            "persona_capital_binding_id": "pcb-1",
            "deployment_stage": "paper",
        }
    }
    store = MockReadStore(
        incidents=incidents,
        bindings=bindings,
        runtime_bindings=runtime_bindings,
    )
    client = _build_test_client(store, identity_roles=["operator", "admin"])

    resp = client.get("/api/v1/operator/incident-response/inc-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["incident"]["incident_id"] == "inc-1"
    assert len(data["data"]["affected_bindings"]) == 1
    assert data["data"]["affected_bindings"][0]["binding_id"] == "pcb-1"
    assert data["allowedActions"]["canPause"] is True


def test_composed_post_incident_review_view() -> None:
    """Test GET /api/v1/operator/post-incident-review/{incident_id}."""
    incidents = {
        "inc-1": {
            "incident_id": "inc-1",
            "title": "Resolved Issue",
            "artifact_id": "art-1",
        }
    }
    postmortems = {
        "pm-1": {
            "id": "pm-1",
            "incident_id": "inc-1",
            "summary": "Root cause identified.",
        }
    }
    evolution_decisions = [
        {"decision_id": "ed-1", "incident_id": "inc-1", "action": "retrain"}
    ]
    lineage_edges = [
        {"artifact_id": "art-1", "parent_id": "art-0"}
    ]
    store = MockReadStore(
        incidents=incidents,
        postmortems=postmortems,
        evolution_decisions=evolution_decisions,
        lineage_edges=lineage_edges,
    )
    client = _build_test_client(store)

    resp = client.get("/api/v1/operator/post-incident-review/inc-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["incident"]["incident_id"] == "inc-1"
    assert data["data"]["postmortem"]["id"] == "pm-1"
    assert len(data["data"]["evolution_decisions"]) == 1
    assert len(data["data"]["lineage_edges"]) == 1


def test_bff_incidents_crud_and_idempotency() -> None:
    """Test POST /bff/incidents and GET /bff/incidents."""
    store = MockReadStore()
    client = _build_test_client(store)

    payload = {
        "incident_id": "inc-created-1",
        "title": "New Alert",
        "severity": "critical",
        "status": "open",
    }

    # 1. Create incident
    resp = client.post(
        "/bff/incidents",
        json=payload,
        headers={"Idempotency-Key": "idem-key-1"},
    )
    assert resp.status_code == 201
    assert resp.json()["incident_id"] == "inc-created-1"

    # 2. Replay with identical key -> 201 returns same result
    replay = client.post(
        "/bff/incidents",
        json=payload,
        headers={"Idempotency-Key": "idem-key-1"},
    )
    assert replay.status_code == 201
    assert replay.json()["incident_id"] == "inc-created-1"

    # 3. A key-derived incident id makes the owner's durable id uniqueness the idempotency record
    keyed = {"title": "Keyed Alert", "severity": "high", "status": "open"}
    first_keyed = client.post("/bff/incidents", json=keyed, headers={"Idempotency-Key": "idem-key-2"})
    assert first_keyed.status_code == 201
    replay_keyed = client.post("/bff/incidents", json=keyed, headers={"Idempotency-Key": "idem-key-2"})
    assert replay_keyed.status_code == 201
    assert replay_keyed.json()["incident_id"] == first_keyed.json()["incident_id"]

    # 4. Replay with same key but different payload -> 409 conflict
    conflicting = client.post(
        "/bff/incidents",
        json={"title": "Different Title"},
        headers={"Idempotency-Key": "idem-key-2"},
    )
    assert conflicting.status_code == 409

    # 5. List incidents holds exactly one owner record per key / stable id
    listed = client.get("/bff/incidents")
    assert listed.status_code == 200
    assert {i["incident_id"] for i in listed.json()["items"]} == {
        "inc-created-1",
        first_keyed.json()["incident_id"],
    }


def test_bff_alert_acknowledge() -> None:
    """Test POST /bff/alerts/{alert_id}/acknowledge."""
    incidents = {
        "inc-1": {
            "incident_id": "inc-1",
            "title": "Critical DB Alert",
            "severity": "critical",
            "status": "open",
        }
    }
    cmd_store = MockCommandStore()
    store = MockReadStore(incidents=incidents)
    client = _build_test_client(store, cmd_store=cmd_store)

    resp = client.post(
        "/bff/alerts/alert-incident-inc-1/acknowledge",
        json={"note": "Investigating now"},
        headers={"Idempotency-Key": "ack-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"
    assert resp.json()["data"]["incident_status"] == "investigating"
    assert store.incidents["inc-1"]["status"] == "investigating"
    assert len(cmd_store.commands) == 1
    assert cmd_store.commands[0]["params"]["alert_id"] == "alert-incident-inc-1"


def test_bff_audit_endpoints() -> None:
    """Test GET /bff/audit, GET /bff/audit/events, GET /bff/audit/entities/{type}/{id}, GET /bff/audit/export."""
    events = [
        {
            "id": "aud-1",
            "actor": "op-1",
            "action_type": "CreateIncident",
            "target_type": "Incident",
            "target_id": "inc-1",
            "timestamp": "2026-08-30T10:00:00Z",
        },
        {
            "id": "aud-2",
            "actor": "op-2",
            "action_type": "AcknowledgeAlert",
            "target_type": "RiskAlert",
            "target_id": "alert-1",
            "timestamp": "2026-08-30T11:00:00Z",
        },
    ]
    store = MockReadStore(audit_events=events)
    client = _build_test_client(store)

    # 1. /bff/audit
    resp1 = client.get("/bff/audit?actor=op-1")
    assert resp1.status_code == 200
    assert len(resp1.json()["items"]) == 1
    assert resp1.json()["items"][0]["id"] == "aud-1"

    # 2. /bff/audit/events
    resp2 = client.get("/bff/audit/events?target_type=Incident")
    assert resp2.status_code == 200
    assert len(resp2.json()["events"]) == 1
    assert resp2.json()["events"][0]["id"] == "aud-1"

    # 3. /bff/audit/entities/Incident/inc-1
    resp3 = client.get("/bff/audit/entities/Incident/inc-1")
    assert resp3.status_code == 200
    assert len(resp3.json()["events"]) == 1
    assert resp3.json()["entity_type"] == "Incident"

    # 4. /bff/audit/export
    resp4 = client.get("/bff/audit/export")
    assert resp4.status_code == 200
    assert resp4.json()["total"] == 2

    # 5. POST /bff/audit/export
    resp5 = client.post("/bff/audit/export", json={"scope": "all"})
    assert resp5.status_code == 410


def test_fast_path_semantic_commands() -> None:
    """Test POST /bff/incidents/{id}/resolve and related semantic aliases."""
    client = _build_test_client()

    routes = [
        "/bff/alerts/a1/escalate-incident",
        "/bff/incidents/inc-1/resolve",
        "/bff/incidents/inc-1/start-mitigation",
    ]

    for r in routes:
        # Standard execution
        resp = client.post(r, json={"reason": "test action"})
        assert resp.status_code == 200, f"Failed for {r}: {resp.text}"
        expected_status = "resolved" if "resolve" in r else "investigating"
        assert resp.json()["status"] == expected_status

        # Dry run preview
        resp_dry = client.post(r, json={"reason": "test dry run"}, headers={"X-Dry-Run": "true"})
        assert resp_dry.status_code == 200
        assert resp_dry.json()["status"] == "preview"

    # Non-status incident actions fail unavailable without mutating incident status
    for r in [
        "/bff/incidents/inc-1/append-postmortem",
        "/bff/incidents/inc-1/rollback-deployment",
    ]:
        resp = client.post(r, json={"reason": "test action"})
        assert resp.status_code == 503, f"Expected 503 for {r}: {resp.text}"
        err = resp.json().get("error") or resp.json().get("detail", {}).get("error", {})
        assert err.get("code") == "DEPENDENCY_UNAVAILABLE"


def test_production_app_incident_routes_wiring() -> None:
    """Verify that all 27 incident/alert routes are wired into the incident router."""
    app = FastAPI()
    app.include_router(create_incident_router(read_surface=MockReadStore()))

    def _iter_routes(routes):
        for r in routes:
            if hasattr(r, "original_router"):
                yield from _iter_routes(r.original_router.routes)
            elif hasattr(r, "routes"):
                yield from _iter_routes(r.routes)
            else:
                yield r

    flat_routes = list(_iter_routes(app.routes))
    route_map = {(getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or []))): getattr(r, "endpoint", None).__name__ for r in flat_routes if getattr(r, "path", None)}

    expected_endpoints = [
        ("/api/v1/operator/alerts", ("GET",), "list_operator_alerts"),
        ("/api/v1/incidents", ("GET",), "list_incidents"),
        ("/api/v1/incidents/stream", ("GET",), "stream_incident_events"),
        ("/api/v1/incidents/{incident_id}", ("GET",), "get_incident"),
        ("/api/v1/kill-switch/status", ("GET",), "get_kill_switch_status"),
        ("/api/v1/operator/incident-response/{incident_id}", ("GET",), "get_incident_response"),
        ("/api/v1/operator/post-incident-review/{incident_id}", ("GET",), "get_post_incident_review"),
        ("/bff/risk/alerts", ("GET",), "bff_list_risk_alerts"),
        ("/bff/risk/alerts/{alert_id}", ("GET",), "bff_get_risk_alert"),
        ("/bff/risk/alerts/{alert_id}/actions/{action_id}", ("POST",), "bff_risk_alert_action"),
        ("/bff/incidents", ("GET",), "bff_list_incidents"),
        ("/bff/incidents", ("POST",), "bff_create_incident"),
        ("/bff/incidents/{incident_id}", ("GET",), "bff_get_incident"),
        ("/bff/incidents/{incident_id}/actions/{action_id}", ("POST",), "bff_incident_action"),
        ("/bff/alerts", ("GET",), "bff_list_alerts"),
        ("/bff/alerts/{alert_id}", ("GET",), "bff_get_alert"),
        ("/bff/alerts/{alert_id}/acknowledge", ("POST",), "bff_alert_acknowledge"),
        ("/bff/audit", ("GET",), "bff_list_audit"),
        ("/bff/audit/events", ("GET",), "bff_list_audit_events"),
        ("/bff/audit/entities/{entity_type}/{entity_id}", ("GET",), "bff_get_entity_audit"),
        ("/bff/audit/export", ("GET",), "bff_audit_export"),
        ("/bff/audit/export", ("POST",), "sem_audit_export_command"),
        ("/bff/incidents/{id}/start-mitigation", ("POST",), "sem_final_generic_id_command_alias"),
        ("/bff/incidents/{id}/rollback-deployment", ("POST",), "bff_incident_non_status_action"),
        ("/bff/incidents/{id}/resolve", ("POST",), "sem_final_generic_id_command_alias"),
        ("/bff/incidents/{id}/append-postmortem", ("POST",), "bff_incident_non_status_action"),
        ("/bff/alerts/{id}/escalate-incident", ("POST",), "sem_final_generic_id_command_alias"),
    ]

    for path, methods, ep_name in expected_endpoints:
        # Check matching route
        matching = [
            (p, m, ep)
            for (p, m), ep in route_map.items()
            if p == path and set(methods).issubset(set(m))
        ]
        assert matching, f"Missing route in incident app: {methods} {path} (expected {ep_name})"
        assert any(ep == ep_name for _, _, ep in matching), (
            f"Route {path} endpoint mismatch in incident app: expected {ep_name}, found {[ep for _, _, ep in matching]}"
        )

    # Test client against incident router app instance
    client = TestClient(app)
    prod_headers = {"Authorization": "Bearer test:operator:ops"}

    resp = client.get("/bff/incidents", headers=prod_headers)
    assert resp.status_code == 200
    assert "items" in resp.json() or "data" in resp.json()

    resp_alerts = client.get("/bff/risk/alerts", headers=prod_headers)
    assert resp_alerts.status_code == 200
    assert "alerts" in resp_alerts.json()

    resp_audit = client.get("/bff/audit", headers=prod_headers)
    assert resp_audit.status_code == 200


def test_bff_incident_detail_returns_localizable_summary_and_timeline_for_four_drift_reports() -> None:
    """INCIDENT-HUMAN-SUMMARY-AND-OCCURRENCES-20261120 Acceptance 4, 5, 6:
    GET /bff/incidents/{id} returns a localizable summary (summary_key +
    summary_params + plain summary), never uses evidence_summary as the summary
    or description body, and returns the timeline that the inbox card no longer
    carries.
    """
    import pytest
    from services.control_plane.bff.governance.human_inbox import _human_inbox_incident_item
    from services.incident.incident import IncidentStore
    from services.incidents.consumer import DriftReportIncidentConsumer

    event_ids = [
        "76586033-ad6a-4f09-ae0d-9a2e756617a5",
        "0b7a8a43-5c02-403e-970f-1a637517a78a",
        "94f2d4bb-75e5-4f50-ae40-53124d5872e7",
        "bf92e54f-13a9-4a76-b7a2-875427646c5c",
    ]
    timestamps = [
        "2026-11-18T11:36:14Z",
        "2026-11-18T12:36:14Z",
        "2026-11-18T13:36:14Z",
        "2026-11-18T14:36:14Z",
    ]
    inc_store = IncidentStore(path=None)
    consumer = DriftReportIncidentConsumer(incident_store=inc_store)
    for eid, ts in zip(event_ids, timestamps):
        consumer.consume(
            {
                "drift_report_id": f"drift-{eid}-drawdown",
                "recon_run_id": f"recon-{eid}",
                "evaluation_id": f"eval-{eid}",
                "incident_cluster_id": "drift:drawdown",
                "drift_type": "pnl",
                "status": "open",
                "severity": "medium",
                "binding_id": "rb-3823397e125e4579ba5f4174067087ed",
                "runtime_id": "runtime-3823397e125e4579ba5f4174067087ed",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-3823397e125e4579ba5f4174067087ed",
                "capital_pool_id": "pool-3823397e125e4579ba5f4174067087ed",
                "persona_capital_binding_id": "pcb-3823397e125e4579ba5f4174067087ed",
                "artifact_id": "artifact-3823397e125e4579ba5f4174067087ed",
                "artifact_version": "1.0.0",
                "trace_id": f"trace-{eid}",
                "telemetry_event_ids": [eid],
                "worst_metric": "drawdown",
                "threshold_metric": "rolling_drawdown_multiple",
                "observed": 9.609309450237793,
                "comparator": "gt",
                "threshold_value": 1.25,
                "threshold_window": "paper-daily-sweep",
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
                "generated_at": ts,
            }
        )

    stored_inc = inc_store.list_incidents()[0]
    inc_id = stored_inc.incident_id
    read_store = MockReadStore(incidents={inc_id: stored_inc.to_dict()})
    client = _build_test_client(read_store)

    resp = client.get(f"/bff/incidents/{inc_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["incident_id"] == inc_id
    assert data["kind"] == "drift"
    assert data["metric"] == "rolling_drawdown_multiple"
    assert data["observed"] == pytest.approx(9.609309450237793)
    assert data["threshold"] == pytest.approx(1.25)
    assert data["comparator"] == "gt"
    assert data["occurrence_count"] == 4
    assert data["first_seen_at"] == "2026-11-18T11:36:14Z"
    assert data["last_seen_at"] == "2026-11-18T14:36:14Z"
    assert data["summary_key"] == "incident.summary.drift_threshold_breach"
    assert data["summary_params"]["occurrence_count"] == 4
    assert data["summary"] == "Observed rolling drawdown multiple 9.61 (> 1.25) across 4 occurrences."
    assert data["description"] == data["summary"]
    assert data["summary"] != data["evidence_summary"]
    assert len(data["timeline"]) == 4
    assert [entry["source_ref"] for entry in data["timeline"]] == [
        f"drift-{eid}-drawdown" for eid in event_ids
    ]

    # Compare against inbox projection: inbox item carries summary_key/summary_params/summary
    # and omits timeline
    inbox_item = _human_inbox_incident_item(stored_inc.to_dict())
    assert inbox_item is not None
    assert inbox_item["summary"] == data["summary"]
    assert inbox_item["summary_key"] == data["summary_key"]
    assert inbox_item["occurrence_count"] == 4
    assert "timeline" not in inbox_item
    assert "timeline" not in inbox_item["details"]


def test_bff_incident_detail_derives_summary_and_timeline_for_legacy_appended_row() -> None:
    """Acceptance 6: Existing stored incidents with an appended evidence_summary
    get a summary and timeline derived from their stored fields at read time on
    GET /bff/incidents/{id} without mutating the stored row.
    """
    import json
    import pytest

    legacy_row = {
        "incident_id": "inc-drift-5f668ec4c8d9",
        "title": "Drift threshold breach: drift:drawdown for rb-3823397e125e4579ba5f4174067087ed",
        "status": "open",
        "severity": "medium",
        "created_at": "2026-11-18T11:36:14Z",
        "binding_id": "rb-3823397e125e4579ba5f4174067087ed",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-3823397e125e4579ba5f4174067087ed",
        "capital_pool_id": "pool-3823397e125e4579ba5f4174067087ed",
        "persona_capital_binding_id": "pcb-3823397e125e4579ba5f4174067087ed",
        "artifact_id": "artifact-3823397e125e4579ba5f4174067087ed",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-3823397e125e4579ba5f4174067087ed",
        "trace_id": "trace-76586033-ad6a-4f09-ae0d-9a2e756617a5",
        "incident_cluster_id": "drift:drawdown",
        "telemetry_event_ids": [
            "76586033-ad6a-4f09-ae0d-9a2e756617a5",
            "0b7a8a43-5c02-403e-970f-1a637517a78a",
            "94f2d4bb-75e5-4f50-ae40-53124d5872e7",
            "bf92e54f-13a9-4a76-b7a2-875427646c5c",
        ],
        "reconciliation_ids": [
            "drift-76586033-ad6a-4f09-ae0d-9a2e756617a5-drawdown",
            "drift-0b7a8a43-5c02-403e-970f-1a637517a78a-drawdown",
            "drift-94f2d4bb-75e5-4f50-ae40-53124d5872e7-drawdown",
            "drift-bf92e54f-13a9-4a76-b7a2-875427646c5c-drawdown",
        ],
        "evidence_summary": (
            "[older entries dropped] DriftReport drift-76586033-ad6a-4f09-ae0d-9a2e756617a5-drawdown; "
            "cluster=drift:drawdown; worst_metric=drawdown; threshold_metric=rolling_drawdown_multiple; "
            "observed=9.609309450237793; comparator=gt; threshold_value=1.25; threshold_window=paper-daily-sweep"
        ),
    }
    before = json.dumps(legacy_row, sort_keys=True)
    read_store = MockReadStore(incidents={"inc-drift-5f668ec4c8d9": legacy_row})
    client = _build_test_client(read_store)

    resp = client.get("/bff/incidents/inc-drift-5f668ec4c8d9")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["kind"] == "drift"
    assert data["metric"] == "rolling_drawdown_multiple"
    assert data["observed"] == pytest.approx(9.609309450237793)
    assert data["threshold"] == pytest.approx(1.25)
    assert data["comparator"] == "gt"
    assert data["occurrence_count"] == 4
    assert data["summary_key"] == "incident.summary.drift_threshold_breach"
    assert data["summary"] == "Observed rolling drawdown multiple 9.61 (> 1.25) across 4 occurrences."
    assert len(data["timeline"]) == 4
    assert json.dumps(read_store.incidents["inc-drift-5f668ec4c8d9"], sort_keys=True) == before

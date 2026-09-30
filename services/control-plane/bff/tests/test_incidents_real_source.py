"""Comprehensive mounted tests for BFF Incidents real service integration.

Task: BFF-INCIDENTS-REAL-SOURCE-001
Acceptance criteria:
1. Incident list, detail, and risk alert builder read from the incidents service and never from an in-memory overlay.
2. Resolve, start-mitigation, escalate, and acknowledge on an incident reach POST /api/incidents/{id}/status
   on the incidents service through every existing entry point (REST routes, IncidentAction, RiskAlertAction commands),
   and BFF returns the status read back from it.
3. Alert acknowledgement is persisted by an existing durable owner (incidents service) and survives BFF restart;
   alerts without a durable owner report acknowledgement as unavailable (HTTP 422).
4. Hard-coded 202 accepted responses, in-memory incident overlay, _ACKNOWLEDGED_ALERTS, and fabricated incident adapter results are deleted.
5. When the incidents service is unavailable, BFF reports unavailable instead of an empty or fabricated list.
6. Mounted tests cover every listed entry point against a stubbed incidents service.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
import urllib.parse
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.command_adapters.registry import dispatch_domain_command
from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.incidents.service import IncidentService
from services.control_plane.bff.models import CommandType, ObjectType
from services.control_plane.bff.ports.lifecycle_telemetry_governance import DomainIncidentPort


class StubIncidentsServer:
    """Threaded in-process HTTP server stubbing services/incidents/main.py."""

    def __init__(self) -> None:
        self.incidents: Dict[str, Dict[str, Any]] = {
            "inc-real-001": {
                "incident_id": "inc-real-001",
                "title": "Production latency spike",
                "severity": "critical",
                "status": "open",
                "created_at": "2026-09-30T00:00:00Z",
            }
        }
        self.status_calls: List[Dict[str, Any]] = []
        self.is_healthy: bool = True
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port: int = 0

    def start(self) -> str:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass  # suppress stderr noise

            def do_GET(self) -> None:
                if not outer.is_healthy:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error": "service unavailable"}')
                    return

                parsed = urllib.parse.urlparse(self.path)
                parts = parsed.path.strip("/").split("/")

                if parsed.path.startswith("/api/incidents"):
                    if len(parts) == 2:  # /api/incidents
                        items = list(outer.incidents.values())
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(items).encode("utf-8"))
                        return
                    elif len(parts) == 3:  # /api/incidents/{id}
                        inc_id = parts[2]
                        if inc_id in outer.incidents:
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps(outer.incidents[inc_id]).encode("utf-8"))
                            return
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b'{"error": "not found"}')
                        return

                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:
                if not outer.is_healthy:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error": "service unavailable"}')
                    return

                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length) if length > 0 else b"{}"
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}

                parsed = urllib.parse.urlparse(self.path)
                parts = parsed.path.strip("/").split("/")

                if parsed.path.startswith("/api/incidents"):
                    if len(parts) == 2:  # POST /api/incidents
                        inc_id = body.get("incident_id") or f"inc-{uuid.uuid4().hex[:6]}"
                        body["incident_id"] = inc_id
                        outer.incidents[inc_id] = body
                        self.send_response(201)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(body).encode("utf-8"))
                        return
                    elif len(parts) == 4 and parts[3] == "status":  # POST /api/incidents/{id}/status
                        inc_id = parts[2]
                        outer.status_calls.append({"incident_id": inc_id, "body": body})
                        inc = outer.incidents.get(inc_id, {"incident_id": inc_id})
                        inc["status"] = body.get("status", "investigating")
                        if "resolved_at" in body:
                            inc["resolved_at"] = body["resolved_at"]
                        outer.incidents[inc_id] = inc
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(inc).encode("utf-8"))
                        return

                self.send_response(404)
                self.end_headers()

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class StubReadStoreWithIncidentPort:
    """Read store that mounts DomainIncidentPort against the stub server."""

    def __init__(self, incidents_url: str, runtime_bindings: Optional[list] = None) -> None:
        self.incident_port = DomainIncidentPort(incidents_api_url=incidents_url)
        self._incidents_port = self.incident_port
        self._runtime_bindings = runtime_bindings or []

    def list_incidents(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return self.incident_port.list_incidents(**kwargs)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.incident_port.get_incident(incident_id)

    def create_incident(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.incident_port.create_incident(payload)

    def update_incident_status(self, incident_id: str, status: str, resolved_at: Optional[str] = None) -> Dict[str, Any]:
        return self.incident_port.update_incident_status(incident_id, status=status, resolved_at=resolved_at)

    def dataset_source(self, dataset: str = "incidents") -> str:
        if dataset == "incidents":
            return self.incident_port.dataset_source()
        return "typed_store"

    def get_kill_switch_status(self) -> Dict[str, Any]:
        return {"active": False, "status": "armed", "safe_mode_status": "off"}

    def list_governance_review_queue_items(self) -> list[Dict[str, Any]]:
        return []

    def list_approval_queue_items(self) -> list[Dict[str, Any]]:
        return []

    def list_runtime_bindings(self) -> list[Dict[str, Any]]:
        return self._runtime_bindings

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_postmortems(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return []

    def get_postmortem_by_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_evolution_decisions_by_incident(self, incident_id: str) -> list[Dict[str, Any]]:
        return []

    def list_lineage_edges(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return []

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return None


@pytest.fixture()
def incident_service_env(monkeypatch):
    stub_srv = StubIncidentsServer()
    base_url = stub_srv.start()
    monkeypatch.setenv("PANTHEON_INCIDENTS_API_URL", base_url)
    monkeypatch.setenv("PANTHEON_INCIDENTS_URL", base_url)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    yield stub_srv, base_url
    stub_srv.stop()


def _build_app(stub_store: StubReadStoreWithIncidentPort, tmp_path) -> FastAPI:
    app = FastAPI()
    cmd_store = CommandStore(str(tmp_path / f"cmd-{uuid.uuid4().hex[:8]}.jsonl"))
    service = IncidentService(get_read_store=lambda: stub_store)
    router = create_incident_router(
        service=service,
        read_surface=stub_store,
        command_store=cmd_store,
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        require_operator_role=auth_policy.require_operator_role,
        bff_error=auth_policy.bff_error,
    )
    app.include_router(router)
    register_error_handlers(app)
    return app


_AUTH = {"Authorization": "Bearer tester:operator"}


def test_incident_reads_come_from_incidents_service(incident_service_env, tmp_path) -> None:
    """Acceptance 1: Incident list and detail read from incidents service."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))

    # 1. List incidents reflects stub service content
    resp = client.get("/bff/incidents", headers=_AUTH)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["incident_id"] == "inc-real-001"
    assert items[0]["title"] == "Production latency spike"
    assert items[0]["status"] == "open"

    # 2. Detail incident reflects stub service content
    resp_detail = client.get("/bff/incidents/inc-real-001", headers=_AUTH)
    assert resp_detail.status_code == 200, resp_detail.text
    assert resp_detail.json()["data"]["incident_id"] == "inc-real-001"
    assert resp_detail.json()["data"]["status"] == "open"


def test_rest_routes_reach_status_endpoint_and_return_200(incident_service_env, tmp_path) -> None:
    """Acceptance 2: resolve, start-mitigation, escalate-incident reach POST /api/incidents/{id}/status

    and BFF returns the read-back status with HTTP 200 (hardcoded 202 is deleted).
    """
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))

    # 1. Start mitigation -> status becomes investigating
    resp_mit = client.post("/bff/incidents/inc-real-001/start-mitigation", headers=_AUTH, json={"reason": "mitigating"})
    assert resp_mit.status_code == 200, resp_mit.text
    assert resp_mit.json()["status"] == "investigating"
    assert stub_srv.incidents["inc-real-001"]["status"] == "investigating"
    assert any(c["incident_id"] == "inc-real-001" and c["body"]["status"] == "investigating" for c in stub_srv.status_calls)

    # 2. Escalate alert -> status investigating on incident
    resp_esc = client.post("/bff/alerts/alert-incident-inc-real-001/escalate-incident", headers=_AUTH, json={})
    assert resp_esc.status_code == 200, resp_esc.text
    assert resp_esc.json()["status"] == "investigating"

    # 3. Resolve incident -> status becomes resolved
    resp_res = client.post("/bff/incidents/inc-real-001/resolve", headers=_AUTH, json={"reason": "fixed"})
    assert resp_res.status_code == 200, resp_res.text
    assert resp_res.json()["status"] == "resolved"
    assert stub_srv.incidents["inc-real-001"]["status"] == "resolved"
    assert any(c["incident_id"] == "inc-real-001" and c["body"]["status"] == "resolved" for c in stub_srv.status_calls)


def test_alert_acknowledge_persists_in_incidents_service_and_survives_restart(incident_service_env, tmp_path) -> None:
    """Acceptance 2 & 3: Alert acknowledge reaches /status, persists in durable owner, survives restart."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))

    # Check alert feed before acknowledge: incident is open, so it's in the alert feed
    resp_alerts = client.get("/bff/risk/alerts", headers=_AUTH)
    assert resp_alerts.status_code == 200
    alert_ids = [a["alert_id"] for a in resp_alerts.json()["alerts"]]
    assert "alert-incident-inc-real-001" in alert_ids

    # Acknowledge the incident alert -> transitions incident to investigating
    resp_ack = client.post(
        "/bff/alerts/alert-incident-inc-real-001/acknowledge",
        headers={**_AUTH, "Idempotency-Key": "ack-real-1"},
        json={"note": "Acking now"},
    )
    assert resp_ack.status_code == 200, resp_ack.text
    assert resp_ack.json()["status"] == "acknowledged"
    assert resp_ack.json()["data"]["incident_status"] == "investigating"
    assert stub_srv.incidents["inc-real-001"]["status"] == "investigating"

    # Verify that in-memory alert list now has the alert suppressed
    resp_alerts_post = client.get("/bff/risk/alerts", headers=_AUTH)
    assert resp_alerts_post.status_code == 200
    alert_ids_post = [a["alert_id"] for a in resp_alerts_post.json()["alerts"]]
    assert "alert-incident-inc-real-001" not in alert_ids_post

    # Simulate restart: mount a brand-new BFF app & router pointing at the same incidents service
    fresh_store = StubReadStoreWithIncidentPort(base_url)
    new_client = TestClient(_build_app(fresh_store, tmp_path))

    # Incident detail reflects investigating status
    fresh_detail = new_client.get("/bff/incidents/inc-real-001", headers=_AUTH)
    assert fresh_detail.status_code == 200
    assert fresh_detail.json()["data"]["status"] == "investigating"

    # Alert feed on fresh BFF still does not show the alert because status in incidents service is investigating
    fresh_alerts = new_client.get("/bff/risk/alerts", headers=_AUTH)
    assert fresh_alerts.status_code == 200
    fresh_alert_ids = [a["alert_id"] for a in fresh_alerts.json()["alerts"]]
    assert "alert-incident-inc-real-001" not in fresh_alert_ids


def test_alert_without_durable_owner_fails_closed(incident_service_env, tmp_path) -> None:
    """Acceptance 3: Alert without durable owner reports acknowledgement as unavailable (HTTP 422)."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url, runtime_bindings=[{"runtime_id": "worker-down", "status": "failed"}])
    client = TestClient(_build_app(store, tmp_path))

    resp = client.post(
        "/bff/alerts/alert-runtime-worker-down/acknowledge",
        headers={**_AUTH, "Idempotency-Key": "orphan-ack-1"},
    )
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["code"] == "OPERATION_NOT_ALLOWED"
    assert err["details"]["precondition_failed"] == "durable_owner_unavailable"


def test_command_adapters_incident_and_alert_actions(incident_service_env) -> None:
    """Acceptance 2 & 4: IncidentAction and RiskAlertAction reach /status via IncidentCommandAdapter."""
    stub_srv, base_url = incident_service_env

    # 1. IncidentAction resolve
    res_resolve = dispatch_domain_command(
        command_id="cmd-inc-res-01",
        command_type=CommandType.INCIDENT_ACTION,
        params={"incident_id": "inc-real-001", "action_id": "resolve", "resolved_at": "2026-09-30T01:00:00Z"},
    )
    assert res_resolve["status"] == "resolved"
    assert res_resolve["authoritative_readback"]["status"] == "resolved"
    assert stub_srv.incidents["inc-real-001"]["status"] == "resolved"

    # 2. IncidentAction start-mitigation
    res_mit = dispatch_domain_command(
        command_id="cmd-inc-mit-01",
        command_type=CommandType.INCIDENT_ACTION,
        params={"incident_id": "inc-real-001", "action_id": "start-mitigation"},
    )
    assert res_mit["status"] == "investigating"
    assert stub_srv.incidents["inc-real-001"]["status"] == "investigating"

    # 3. RiskAlertAction acknowledge on incident alert
    res_ack = dispatch_domain_command(
        command_id="cmd-alert-ack-01",
        command_type=CommandType.RISK_ALERT_ACTION,
        params={"alert_id": "alert-incident-inc-real-001", "action_id": "acknowledge"},
    )
    assert res_ack["status"] == "acknowledged"
    assert res_ack["authoritative_readback"]["status"] == "investigating"

    # 4. RiskAlertAction on orphan alert raises ActionUnavailableError
    with pytest.raises(ActionUnavailableError) as exc_info:
        dispatch_domain_command(
            command_id="cmd-alert-orphan",
            command_type=CommandType.RISK_ALERT_ACTION,
            params={"alert_id": "alert-runtime-memory-99", "action_id": "acknowledge"},
        )
    assert "durable owner" in str(exc_info.value)


def test_incidents_service_unavailable_reports_unavailable(incident_service_env, tmp_path) -> None:
    """Acceptance 5: When the incidents service is unavailable, BFF reports unavailable."""
    stub_srv, base_url = incident_service_env
    # Make stub service return 503
    stub_srv.is_healthy = False

    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))

    # 1. Detail route fails with 503
    detail_resp = client.get("/bff/incidents/inc-real-001", headers=_AUTH)
    assert detail_resp.status_code == 503, detail_resp.text
    assert detail_resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    # 2. List route reports surface status unavailable
    list_resp = client.get("/bff/incidents", headers=_AUTH)
    assert list_resp.status_code == 200, list_resp.text
    surfaces = list_resp.json()["meta"]["surfaces"]
    assert surfaces["incidents"]["status"] == "unavailable"

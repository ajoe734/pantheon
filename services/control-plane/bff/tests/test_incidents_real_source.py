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
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import os
os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.command_adapters.registry import dispatch_domain_command
from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.command_executor import execute_command_with_status
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.incidents.service import IncidentService
from services.control_plane.bff.models import CommandStatus, CommandType, ObjectType, TargetObject
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
    CompositeLifecycleTelemetryGovernancePort,
    DomainIncidentPort,
)


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
        self.status_rejection: Optional[int] = None
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
                        qs = urllib.parse.parse_qs(parsed.query)
                        if "status" in qs:
                            st = qs["status"][0]
                            items = [i for i in items if i.get("status") == st]
                        if "severity" in qs:
                            sev = qs["severity"][0]
                            items = [i for i in items if i.get("severity") == sev]
                        if "capital_pool_id" in qs:
                            pool = qs["capital_pool_id"][0]
                            items = [i for i in items if (i.get("capital_pool_id") or i.get("affected_pool_id")) == pool]
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
                        if outer.status_rejection:
                            self.send_response(outer.status_rejection)
                            self.end_headers()
                            self.wfile.write(b'{"error": "status change rejected"}')
                            return
                        inc_id = urllib.parse.unquote(parts[2])
                        outer.status_calls.append({"incident_id": inc_id, "body": body, "headers": dict(self.headers)})
                        if inc_id not in outer.incidents:
                            self.send_response(404)
                            self.end_headers()
                            self.wfile.write(b'{"error": "not found"}')
                            return
                        inc = outer.incidents[inc_id]
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


def _build_app(stub_store: StubReadStoreWithIncidentPort, tmp_path, submit_action_command=None) -> FastAPI:
    app = FastAPI()
    cmd_store = CommandStore(str(tmp_path / f"cmd-{uuid.uuid4().hex[:8]}.jsonl"))
    service = IncidentService(get_read_store=lambda: stub_store)
    router = create_incident_router(
        service=service,
        read_surface=stub_store,
        command_store=cmd_store,
        submit_action_command=submit_action_command,
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        require_operator_role=auth_policy.require_operator_role,
        bff_error=auth_policy.bff_error,
    )
    app.include_router(router)
    register_error_handlers(app)
    return app


_AUTH = {"Authorization": "Bearer tester:operator"}


@pytest.fixture()
def status_writer_calls(monkeypatch):
    """Observe the shared writer while keeping the real owner HTTP call."""
    calls = []
    original = DomainIncidentPort.update_incident_status

    def record(self, incident_id, status, *args, **kwargs):
        calls.append((incident_id, status))
        return original(self, incident_id, status, *args, **kwargs)

    monkeypatch.setattr(DomainIncidentPort, "update_incident_status", record)
    return calls


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


def test_rest_routes_reach_status_endpoint_and_return_200(incident_service_env, tmp_path, status_writer_calls) -> None:
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
    assert status_writer_calls == [
        ("inc-real-001", "investigating"), ("inc-real-001", "investigating"), ("inc-real-001", "resolved"),
    ]


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


def test_command_adapters_incident_and_alert_actions(incident_service_env, status_writer_calls) -> None:
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
    assert res_ack["authoritative_readback"]["status"] == "acknowledged"
    assert res_ack["authoritative_readback"]["incident_status"] == "investigating"

    # 4. RiskAlertAction on orphan alert raises ActionUnavailableError
    with pytest.raises(ActionUnavailableError) as exc_info:
        dispatch_domain_command(
            command_id="cmd-alert-orphan",
            command_type=CommandType.RISK_ALERT_ACTION,
            params={"alert_id": "alert-runtime-memory-99", "action_id": "acknowledge"},
        )
    assert "durable owner" in str(exc_info.value)
    assert status_writer_calls == [
        ("inc-real-001", "resolved"), ("inc-real-001", "investigating"), ("inc-real-001", "investigating"),
    ]


@pytest.mark.parametrize("token", ["tester:operator", "Bearer tester:operator"])
@pytest.mark.parametrize("command_type", [CommandType.INCIDENT_ACTION, CommandType.RISK_ALERT_ACTION, CommandType.ALERT_ACKNOWLEDGE])
def test_shared_writer_preserves_command_credentials_and_encoded_id(incident_service_env, token, command_type):
    server, _ = incident_service_env
    incident_id = "inc-special/with space"
    server.incidents[incident_id] = {"incident_id": incident_id, "status": "open", "title": "Owner data"}
    is_incident = command_type == CommandType.INCIDENT_ACTION
    params = (
        {"incident_id": incident_id, "action_id": "resolve", "resolved_at": "2026-09-30T01:00:00Z"}
        if is_incident else {"alert_id": f"alert-incident-{incident_id}", "action_id": "acknowledge"}
    )
    result = dispatch_domain_command(
        command_id="cmd-encoded", command_type=command_type, params=params,
        auth_token=token, mfa_token="existing-test-token",
    )
    assert len(server.status_calls) == 1
    call = server.status_calls[0]
    assert call["incident_id"] == incident_id
    assert call["body"] == (
        {"status": "resolved", "resolved_at": "2026-09-30T01:00:00Z"} if is_incident else {"status": "investigating"}
    )
    assert call["headers"]["Authorization"] == "Bearer tester:operator"
    assert call["headers"]["X-Mfa-Token"] == "existing-test-token"
    assert result["domain_receipt"]["title"] == "Owner data"


@pytest.mark.parametrize("command_type, params", [
    (CommandType.INCIDENT_ACTION, {"incident_id": "inc-real-001", "action_id": "resolve"}),
    (CommandType.RISK_ALERT_ACTION, {"alert_id": "alert-incident-inc-real-001", "action_id": "acknowledge"}),
    (CommandType.ALERT_ACKNOWLEDGE, {"alert_id": "alert-incident-inc-real-001"}),
])
@pytest.mark.parametrize("failure, expected_status, expected_code", [
    ("missing", 404, "RESOURCE_NOT_FOUND"),
    ("outage", 503, "DEPENDENCY_UNAVAILABLE"),
    ("unconfigured", 503, "DEPENDENCY_UNAVAILABLE"),
    ("rejected", 400, "DOWNSTREAM_ERROR"),
    ("conflict", 409, "DOWNSTREAM_ERROR"),
])
def test_shared_writer_failures_remain_meaningful_in_command_queue(
    incident_service_env, monkeypatch, command_type, params, failure, expected_status, expected_code,
):
    server, _ = incident_service_env
    if failure == "missing":
        server.incidents.clear()
    elif failure == "outage":
        server.is_healthy = False
    elif failure in {"rejected", "conflict"}:
        server.status_rejection = expected_status
    else:
        monkeypatch.delenv("PANTHEON_INCIDENTS_API_URL")
        monkeypatch.delenv("PANTHEON_INCIDENTS_URL")
    status, result, error = execute_command_with_status("cmd-owner-failure", command_type, params)
    assert status == CommandStatus.FAILED and result is None
    assert error["code"] == expected_code
    assert error["downstream_status"] == expected_status
    assert error["retryable"] is (expected_status == 503)


@pytest.mark.parametrize("owner_status", [400, 409])
@pytest.mark.parametrize("path", [
    "/bff/incidents/inc-real-001/resolve",
    "/bff/incidents/inc-real-001/actions/resolve",
    "/bff/risk/alerts/alert-incident-inc-real-001/actions/acknowledge",
])
def test_owner_rejection_is_preserved_across_routes(incident_service_env, tmp_path, owner_status, path):
    server, base_url = incident_service_env
    server.status_rejection = owner_status
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))
    response = client.post(path, headers={**_AUTH, "Idempotency-Key": str(uuid.uuid4())}, json={})
    assert response.status_code == owner_status, response.text
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"
    assert server.incidents["inc-real-001"]["status"] == "open"
    assert store.incident_port.is_available()


@pytest.mark.parametrize("detail", ["Owner unavailable", {"error": "Owner unavailable"}])
def test_command_http_error_without_structured_details_does_not_escape(monkeypatch, detail):
    def reject(*args, **kwargs):
        raise HTTPException(status_code=503, detail=detail)

    monkeypatch.setattr("services.control_plane.bff.command_executor.execute_command", reject)
    status, result, error = execute_command_with_status("cmd-http-failure", CommandType.INCIDENT_ACTION, {})
    assert status == CommandStatus.FAILED and result is None
    assert error["code"] == "DOWNSTREAM_ERROR" and error["downstream_status"] == 503


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


def test_production_read_surface_ports_composition(incident_service_env, tmp_path) -> None:
    """Acceptance 1, 2, 3: Verify with real ReadSurfacePorts + CompositeLifecycleTelemetryGovernancePort."""
    stub_srv, base_url = incident_service_env
    domain_incident_port = DomainIncidentPort(incidents_api_url=base_url)
    lifecycle_port = CompositeLifecycleTelemetryGovernancePort(incident_port=domain_incident_port)
    read_surface = ReadSurfacePorts(lifecycle_telemetry_governance=lifecycle_port)

    app = FastAPI()
    cmd_store = CommandStore(str(tmp_path / f"cmd-{uuid.uuid4().hex[:8]}.jsonl"))
    service = IncidentService(get_read_store=lambda: read_surface, durable_writer=lifecycle_port)
    router = create_incident_router(
        service=service,
        read_surface=read_surface,
        command_store=cmd_store,
        durable_writer=lifecycle_port,
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        require_operator_role=auth_policy.require_operator_role,
        bff_error=auth_policy.bff_error,
    )
    app.include_router(router)
    register_error_handlers(app)
    client = TestClient(app)

    # 1. Resolve reaches POST /api/incidents/inc-real-001/status
    resp = client.post("/bff/incidents/inc-real-001/resolve", headers=_AUTH, json={"reason": "fixed"})
    assert resp.status_code == 200
    assert stub_srv.incidents["inc-real-001"]["status"] == "resolved"
    assert any(c["incident_id"] == "inc-real-001" and c["body"]["status"] == "resolved" for c in stub_srv.status_calls)

    # 2. Acknowledge reaches POST /api/incidents/inc-real-001/status
    resp = client.post("/bff/alerts/alert-incident-inc-real-001/acknowledge", headers={**_AUTH, "Idempotency-Key": "ack-p-1"}, json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"
    assert stub_srv.incidents["inc-real-001"]["status"] == "investigating"

    # 3. Fresh instance restart preserves investigating status
    fresh_domain_port = DomainIncidentPort(incidents_api_url=base_url)
    fresh_lifecycle = CompositeLifecycleTelemetryGovernancePort(incident_port=fresh_domain_port)
    fresh_read_surface = ReadSurfacePorts(lifecycle_telemetry_governance=fresh_lifecycle)
    fresh_service = IncidentService(get_read_store=lambda: fresh_read_surface, durable_writer=fresh_lifecycle)
    fresh_app = FastAPI()
    fresh_app.include_router(create_incident_router(
        service=fresh_service,
        read_surface=fresh_read_surface,
        command_store=cmd_store,
        durable_writer=fresh_lifecycle,
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        require_operator_role=auth_policy.require_operator_role,
        bff_error=auth_policy.bff_error,
    ))
    register_error_handlers(fresh_app)
    fresh_client = TestClient(fresh_app)
    detail_resp = fresh_client.get("/bff/incidents/inc-real-001", headers=_AUTH)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["data"]["status"] == "investigating"


def test_mounted_action_submission_helper_routes(incident_service_env, tmp_path) -> None:
    """Acceptance 2, 3: Mounted /bff/risk/alerts/{id}/actions/{action} and /bff/incidents/{id}/actions/{action}."""
    stub_srv, base_url = incident_service_env

    domain_incident_port = DomainIncidentPort(incidents_api_url=base_url)
    lifecycle_port = CompositeLifecycleTelemetryGovernancePort(incident_port=domain_incident_port)
    read_surface = ReadSurfacePorts(lifecycle_telemetry_governance=lifecycle_port)

    app = FastAPI()
    cmd_store = CommandStore(str(tmp_path / f"cmd-{uuid.uuid4().hex[:8]}.jsonl"))
    service = IncidentService(get_read_store=lambda: read_surface, durable_writer=lifecycle_port)
    router = create_incident_router(
        service=service,
        read_surface=read_surface,
        command_store=cmd_store,
        durable_writer=lifecycle_port,
        extract_identity=auth_policy.extract_identity,
        require_read_role=auth_policy.require_read_role,
        require_operator_role=auth_policy.require_operator_role,
        bff_error=auth_policy.bff_error,
    )
    app.include_router(router)
    register_error_handlers(app)
    client = TestClient(app)

    # 1. IncidentAction via /bff/incidents/{id}/actions/resolve
    resp_inc = client.post("/bff/incidents/inc-real-001/actions/resolve", headers=_AUTH, json={"reason": "resolved by operator"})
    assert resp_inc.status_code == 202
    assert stub_srv.incidents["inc-real-001"]["status"] == "resolved"
    assert resp_inc.json()["data"]["status"] == "resolved"
    assert resp_inc.json()["data"]["incident_status"] == "resolved"

    # 2. RiskAlertAction with durable owner via /bff/risk/alerts/alert-incident-inc-real-001/actions/acknowledge
    resp_alert = client.post(
        "/bff/risk/alerts/alert-incident-inc-real-001/actions/acknowledge",
        headers={**_AUTH, "Idempotency-Key": f"ack-{uuid.uuid4().hex[:6]}"},
        json={},
    )
    assert resp_alert.status_code == 202
    assert resp_alert.json()["data"]["status"] == "acknowledged"
    assert stub_srv.incidents["inc-real-001"]["status"] == "investigating"
    assert any(c["incident_id"] == "inc-real-001" for c in stub_srv.status_calls)
    assert not any(c["incident_id"].startswith("alert-") for c in stub_srv.status_calls)

    # 3. RiskAlertAction without durable owner via /bff/risk/alerts/alert-runtime-worker-1/actions/acknowledge
    resp_orphan = client.post(
        "/bff/risk/alerts/alert-runtime-worker-1/actions/acknowledge",
        headers={**_AUTH, "Idempotency-Key": f"ack-orphan-{uuid.uuid4().hex[:6]}"},
        json={},
    )
    assert resp_orphan.status_code == 422
    assert resp_orphan.json()["error"]["code"] == "OPERATION_NOT_ALLOWED"
    assert resp_orphan.json()["error"]["details"]["precondition_failed"] == "durable_owner_unavailable"


def test_outage_health_propagation_and_recovery(incident_service_env) -> None:
    """Acceptance 5: Test health propagation and recovery on outage."""
    stub_srv, base_url = incident_service_env

    port = DomainIncidentPort(incidents_api_url=base_url)
    store = StubReadStoreWithIncidentPort(base_url)
    store.incident_port = port
    service = IncidentService(get_read_store=lambda: store, durable_writer=port)

    # Initially healthy
    alerts, surface = service.build_incident_alerts(snapshot_at="2026-09-30T00:00:00Z")
    assert surface["status"] == "ok"
    status_payload = service.get_surface_status("incidents")
    assert status_payload["status"] == "ok"
    assert status_payload["source"] == "service_client"

    # Upstream fails (503)
    stub_srv.is_healthy = False
    alerts_outage, surface_outage = service.build_incident_alerts(snapshot_at="2026-09-30T00:01:00Z")
    assert surface_outage["status"] == "unavailable"
    assert alerts_outage == []
    status_outage = service.get_surface_status("incidents")
    assert status_outage["status"] == "unavailable"
    assert status_outage["source"] == "unavailable"

    # Upstream recovers
    stub_srv.is_healthy = True
    alerts_rec, surface_rec = service.build_incident_alerts(snapshot_at="2026-09-30T00:02:00Z")
    assert surface_rec["status"] == "ok"
    assert len(alerts_rec) >= 1
    status_rec = service.get_surface_status("incidents")
    assert status_rec["status"] == "ok"
    assert status_rec["source"] == "service_client"


def test_detail_404_preserves_not_found_without_service_error(incident_service_env, tmp_path) -> None:
    """Acceptance 1, 5: 404 on missing incident preserves not-found without marking service down."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))

    resp_404 = client.get("/bff/incidents/inc-does-not-exist", headers=_AUTH)
    assert resp_404.status_code == 404
    assert resp_404.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert not store.incident_port._last_error

    resp_200 = client.get("/bff/incidents/inc-real-001", headers=_AUTH)
    assert resp_200.status_code == 200


def test_create_incident_generates_uuid_and_persists(incident_service_env, tmp_path) -> None:
    """Acceptance 1, 4: POST /bff/incidents generates UUID and persists without invented defaults."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))

    resp = client.post(
        "/bff/incidents",
        headers={**_AUTH, "Idempotency-Key": f"create-{uuid.uuid4().hex[:6]}"},
        json={"title": "Spike in errors", "severity": "high"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    inc_id = data.get("id") or data.get("incident_id")
    assert inc_id and inc_id.strip()
    assert inc_id in stub_srv.incidents
    assert "rb-default" not in str(data)
    assert "plan-default" not in str(data)


@pytest.mark.parametrize("path", [
    "/bff/incidents/inc-real-001/actions/resolve",
    "/bff/risk/alerts/alert-incident-inc-real-001/actions/acknowledge",
])
def test_action_outage_reports_dependency_unavailable(incident_service_env, tmp_path, path) -> None:
    """Action endpoints report 503 DEPENDENCY_UNAVAILABLE on incidents outage."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))
    stub_srv.is_healthy = False
    response = client.post(path, headers={**_AUTH, "Idempotency-Key": str(uuid.uuid4())}, json={})
    assert response.status_code == 503, (response.status_code, response.text)
    err = response.json().get("error") or response.json().get("detail", {}).get("error", {})
    assert err["code"] == "DEPENDENCY_UNAVAILABLE"


def test_incident_outage_preserves_healthy_runtime_alerts(incident_service_env, tmp_path) -> None:
    """Runtime alerts remain visible when incidents service is unavailable."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url, runtime_bindings=[{"runtime_id": "worker-down", "status": "failed"}])
    client = TestClient(_build_app(store, tmp_path))
    before = client.get("/bff/risk/alerts", headers=_AUTH).json()
    assert any(a["alert_id"] == "alert-runtime-worker-down" for a in before["alerts"])
    stub_srv.is_healthy = False
    after = client.get("/bff/risk/alerts", headers=_AUTH).json()
    assert any(a["alert_id"] == "alert-runtime-worker-down" for a in after["alerts"]), after


@pytest.mark.parametrize("action", ["append-postmortem", "rollback-deployment"])
def test_unrelated_action_does_not_investigate_incident(incident_service_env, tmp_path, action) -> None:
    """Non-status actions do not mutate incident status or call incident service status endpoint."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))
    response = client.post(f"/bff/incidents/inc-real-001/{action}", headers=_AUTH, json={})
    assert stub_srv.status_calls == [], (response.status_code, response.text, stub_srv.status_calls)


@pytest.mark.parametrize("action", ["append-postmortem", "rollback-deployment"])
def test_command_action_does_not_mutate_status_for_unsupported_operation(incident_service_env, tmp_path, action: str) -> None:
    """Non-status actions on command route reject without status mutation or upstream calls."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))
    response = client.post(f"/bff/incidents/inc-real-001/actions/{action}", headers=_AUTH, json={})
    assert stub_srv.status_calls == [], {"http": response.status_code, "calls": stub_srv.status_calls, "body": response.json()}
    assert response.status_code == 422
    err = response.json().get("error") or {}
    assert err.get("code") == "OPERATION_NOT_ALLOWED"


@pytest.mark.parametrize("path", ["incident-response", "post-incident-review"])
def test_composed_details_report_outage_as_dependency_unavailable(incident_service_env, tmp_path, path: str) -> None:
    """Composed details endpoints report 503 on owner outage while retaining genuine 404."""
    stub_srv, base_url = incident_service_env
    store = StubReadStoreWithIncidentPort(base_url)
    client = TestClient(_build_app(store, tmp_path))
    stub_srv.is_healthy = False
    response = client.get(f"/api/v1/operator/{path}/inc-real-001", headers=_AUTH)
    assert response.status_code == 503, response.text
    err = response.json().get("error") or {}
    assert err.get("code") == "DEPENDENCY_UNAVAILABLE"


def test_multi_status_query_matches_real_owner_equality_contract(incident_service_env) -> None:
    """Multi-status query preserves case-normalized filtering against faithful exact-match owner."""
    stub_srv, base_url = incident_service_env
    stub_srv.incidents["inc-real-002"] = {
        "incident_id": "inc-real-002",
        "title": "Database connection drop",
        "severity": "high",
        "status": "investigating",
        "created_at": "2026-09-30T01:00:00Z",
    }
    port = DomainIncidentPort(incidents_api_url=base_url)
    results = port.list_incidents(status="open,investigating")
    assert len(results) == 2

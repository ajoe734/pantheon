"""Command target binding and admission ordering on the production composition entry points."""
import runpy
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_real = runpy.run_path(str(Path(__file__).parent / "test_incidents_real_source.py"))
StubIncidentsServer = _real["StubIncidentsServer"]
CommandStore = _real["CommandStore"]

# A stub token carries a tenant only in its third segment; bare operator tokens are tenantless.
_TENANT_AUTH = {"Authorization": "Bearer tester:operator:tenant-dev"}


@pytest.fixture
def mounted_prod_callback(monkeypatch, tmp_path):
    server = StubIncidentsServer()
    url = server.start()
    for name in ("PANTHEON_INCIDENTS_API_URL", "PANTHEON_INCIDENTS_URL"):
        monkeypatch.setenv(name, url)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    main = __import__("services.control_plane.bff." + "main", fromlist=["main"])
    store = CommandStore(str(tmp_path / "production-commands.jsonl"))
    monkeypatch.setattr(main, "_check_read_surface_state", lambda: None)
    monkeypatch.setattr(main, "command_store", store)
    monkeypatch.setattr(main._command_adapter_service, "_get_command_store", lambda: store)
    try:
        yield server, TestClient(main.app, raise_server_exceptions=False)
    finally:
        server.stop()


def _v1_command(client, command, target, params, headers):
    return client.post("/bff/v1/commands", headers={**headers, "Idempotency-Key": str(uuid.uuid4())}, json={
        "command": command, "target": target, "action": params.get("action_id"), "params": params,
        "audit_context": {"reason": "target binding regression"},
    })


@pytest.mark.parametrize("command,target,params", [
    ("IncidentAction", {"type": "Incident", "id": "inc-real-001"}, {"incident_id": "inc-other", "action_id": "resolve"}),
    ("RiskAlertAction", {"type": "RiskAlert", "id": "alert-incident-inc-real-001"},
     {"alert_id": "alert-incident-inc-other", "action_id": "acknowledge"}),
])
def test_v1_commands_reject_body_id_that_differs_from_target(mounted_prod_callback, command, target, params):
    server, client = mounted_prod_callback
    response = _v1_command(client, command, target, params, _TENANT_AUTH)
    assert response.status_code == 422 and server.status_calls == [], {
        "http": response.status_code, "body": response.json(), "writes": server.status_calls}
    assert response.json()["error"]["details"]["precondition_failed"] == "route_target_mismatch"


def test_v1_commands_null_body_id_acts_on_target(mounted_prod_callback):
    server, client = mounted_prod_callback
    response = _v1_command(client, "IncidentAction", {"type": "Incident", "id": "inc-real-001"},
                           {"incident_id": None, "action_id": "resolve"}, _TENANT_AUTH)
    assert response.status_code == 202, response.text
    assert [c["incident_id"] for c in server.status_calls] == ["inc-real-001"]


def test_tenantless_retired_action_is_410_before_tenant_scope(mounted_prod_callback, monkeypatch):
    _, client = mounted_prod_callback
    main = __import__("services.control_plane.bff." + "main", fromlist=["main"])
    monkeypatch.setattr(type(main.read_store), "get_job_bff", lambda self, job_id: {"job_id": job_id, "status": "failed"}, raising=False)
    response = client.post("/bff/jobs/j1/actions/retry", json={}, headers={
        "Authorization": "Bearer op-1:operator", "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 410, response.text
    assert response.json()["error"]["code"] == "ACTION_RETIRED"

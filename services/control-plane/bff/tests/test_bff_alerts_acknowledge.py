from __future__ import annotations

import tempfile
import uuid
from typing import Any, Dict, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.models import ErrorCode


def _reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    body_key = (
        "idempotencyKey"
        if "idempotencyKey" in payload
        else "idempotency_key" if "idempotency_key" in payload else None
    )
    if body_key is not None:
        raise auth_policy.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            f"{body_key} must not appear in the request body",
            (
                "Final contract routes require idempotency via the Idempotency-Key header, "
                "not the request body"
            ),
            precondition_failed="body_idempotency_key",
            suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
        )

_INCIDENT_ALERT_ID = "alert-incident-inc-001"
_INCIDENT_ALERT: Dict[str, Any] = {
    "alert_id": _INCIDENT_ALERT_ID,
    "severity": "high",
    "category": "incident",
    "raised_at": "2026-05-23T00:00:00Z",
    "summary": "Test incident alert for acknowledge tests.",
    "target_ref": {
        "surface_id": "PKT-002",
        "target_id": "inc-001",
    },
}
_ORPHAN_ALERT_ID = "alert-runtime-001"
_ORPHAN_ALERT: Dict[str, Any] = {
    "alert_id": _ORPHAN_ALERT_ID,
    "severity": "high",
    "category": "runtime",
    "raised_at": "2026-05-23T00:00:00Z",
    "summary": "Test runtime alert without durable owner.",
}
_OPERATOR_AUTH = "Bearer op-ack-tester:operator"


def _default_alerts_payload(snapshot_at: str) -> Dict[str, Any]:
    return {
        "alerts": [_INCIDENT_ALERT, _ORPHAN_ALERT],
        "summary": {"total_active": 2, "highest_severity": "high", "by_severity": {"high": 2}, "by_category": {"incident": 1, "runtime": 1}},
        "meta": {
            "snapshot_at": snapshot_at,
            "acknowledgement_supported": True,
            "surfaces": {"alerts": {"status": "ok", "dataset": "alerts"}},
        },
    }


class _AlertsPayloadHolder:
    def __init__(self) -> None:
        self.builder = _default_alerts_payload

    def __call__(self, snapshot_at: str) -> Dict[str, Any]:
        return self.builder(snapshot_at)


class _StubReadStore:
    def __init__(self, incidents: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.incidents = dict(incidents or {
            "inc-001": {
                "incident_id": "inc-001",
                "title": "Test DB Outage",
                "severity": "high",
                "status": "open",
            }
        })

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.incidents.get(incident_id)

    def list_incidents(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return list(self.incidents.values())

    def update_incident_status(
        self,
        incident_id: str,
        status: str,
        resolved_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        if incident_id in self.incidents:
            self.incidents[incident_id]["status"] = status
            if resolved_at:
                self.incidents[incident_id]["resolved_at"] = resolved_at
            return self.incidents[incident_id]
        return {"incident_id": incident_id, "status": status}


@pytest.fixture()
def harness(tmp_path):
    command_store = CommandStore(str(tmp_path / f"commands-{uuid.uuid4().hex[:8]}.jsonl"))
    idempotency_ledger: Dict[str, Any] = {}
    alerts_payload = _AlertsPayloadHolder()
    read_store = _StubReadStore()

    app = FastAPI()
    app.include_router(
        create_incident_router(
            read_surface=read_store,
            command_store=command_store,
            build_operator_alerts_payload=alerts_payload,
            idempotency_ledger=idempotency_ledger,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            reject_body_idempotency_key=_reject_body_idempotency_key,
        )
    )
    register_error_handlers(app)
    return app, read_store, alerts_payload, command_store


def _client(harness, monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    app, _read_store, _alerts_payload, _cmd_store = harness
    return TestClient(app)


def test_acknowledge_returns_200_with_command_response(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    _app, read_store, _alerts_payload, _cmd_store = harness
    resp = client.post(
        f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-key-001"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "acknowledged"
    assert body["data"]["status"] == "acknowledged"
    assert body["data"]["incident_status"] == "investigating"
    assert read_store.incidents["inc-001"]["status"] == "investigating"
    assert "command_id" in body["data"] or "commandId" in body["data"]
    assert "meta" in body


def test_acknowledge_idempotency_replay(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    headers = {"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-replay-key"}

    first = client.post(f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge", headers=headers)
    assert first.status_code == 200, first.text

    second = client.post(f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge", headers=headers)
    assert second.status_code == 200, second.text
    assert first.json()["data"]["command_id"] == second.json()["data"]["command_id"]


def test_acknowledge_idempotency_conflict_returns_409(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    key = "ack-conflict-key-001"

    first = client.post(
        f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge",
        json={"note": "first reason"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": key},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge",
        json={"note": "different reason"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": key},
    )
    assert second.status_code == 409
    detail = second.json()
    assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_acknowledge_anonymous_returns_401(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post("/bff/alerts/alert-kill-switch-state/acknowledge")
    assert resp.status_code == 401


def test_acknowledge_unknown_alert_returns_404_when_surface_available(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post(
        "/bff/alerts/no-such-alert-id-xyz999/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-404-key"},
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()
    assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert detail["error"]["details"].get("precondition_failed") == "alert_id"


def test_acknowledge_body_idempotency_key_rejected(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post(
        f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge",
        json={"idempotency_key": "should-be-rejected"},
        headers={"Authorization": _OPERATOR_AUTH},
    )
    assert resp.status_code == 400
    detail = resp.json()
    assert detail["error"]["code"] == "VALIDATION_FAILED"


def test_acknowledge_response_has_tracking_url(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post(
        f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-tracking-key"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("trackingUrl") or data.get("tracking_url"), "Response must include a trackingUrl"


def test_acknowledge_without_durable_owner_fails_closed(harness, monkeypatch) -> None:
    """Alert without durable owner reports acknowledgement as unavailable (422)."""
    client = _client(harness, monkeypatch)
    resp = client.post(
        f"/bff/alerts/{_ORPHAN_ALERT_ID}/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-orphan-key"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()
    assert detail["error"]["code"] == "OPERATION_NOT_ALLOWED"
    assert detail["error"]["details"].get("precondition_failed") == "durable_owner_unavailable"


def test_acknowledge_persists_to_durable_owner_surviving_restart(harness, monkeypatch) -> None:
    """Alert acknowledgement persists in durable owner (incident) and survives restart."""
    client = _client(harness, monkeypatch)
    _app, read_store, _alerts_payload, cmd_store = harness

    resp = client.post(
        f"/bff/alerts/{_INCIDENT_ALERT_ID}/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-restart-key"},
    )
    assert resp.status_code == 200
    # Durable owner updated
    assert read_store.incidents["inc-001"]["status"] == "investigating"

    # Simulate restart by mounting a fresh router/app on the same store
    new_app = FastAPI()
    new_app.include_router(
        create_incident_router(
            read_surface=read_store,
            command_store=cmd_store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
        )
    )
    register_error_handlers(new_app)
    new_client = TestClient(new_app)
    get_resp = new_client.get(f"/bff/incidents/inc-001", headers={"Authorization": _OPERATOR_AUTH})
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["status"] == "investigating"


def test_alerts_list_meta_acknowledgement_supported(harness, monkeypatch) -> None:
    """GET /bff/alerts must return meta.acknowledgement_supported = true."""
    client = _client(harness, monkeypatch)
    resp = client.get("/bff/alerts", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    assert meta.get("acknowledgement_supported") is True

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.models import ErrorCode, OperatorIdentity

_TEST_ALERT_ID = "alert-test-ack-001"
_TEST_ALERT: Dict[str, Any] = {
    "alert_id": _TEST_ALERT_ID,
    "severity": "high",
    "category": "runtime",
    "raised_at": "2026-05-23T00:00:00Z",
    "summary": "Test runtime alert for acknowledge tests.",
}
_OPERATOR_AUTH = "Bearer op-ack-tester:operator"

_ACKNOWLEDGED_ALERTS: Dict[str, Any] = {}
_GOV_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_CUSTOM_ALERTS_PAYLOAD: Optional[Callable[[str], Dict[str, Any]]] = None


def _reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    body_key = "idempotencyKey" if "idempotencyKey" in payload else "idempotency_key" if "idempotency_key" in payload else None
    if body_key is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED.value,
                    "message": f"{body_key} must not appear in the request body",
                    "details": {"precondition_failed": "body_idempotency_key"},
                }
            },
        )


def _extract_identity(authorization: Optional[str] = None, **kwargs) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": ErrorCode.AUTH_REQUIRED.value,
                    "message": "Authorization header is required",
                    "details": {},
                }
            },
        )
    token = authorization[len("Bearer "):].strip()
    parts = token.split(":")
    op = parts[0]
    roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
    return OperatorIdentity(operator_id=op, roles=roles)


def _bff_error(status_code: int, code: Any, message: str, reason: str = "", precondition_failed: Optional[str] = None, **kwargs):
    code_val = getattr(code, "value", str(code))
    details: Dict[str, Any] = {"reason": reason}
    if precondition_failed:
        details["precondition_failed"] = precondition_failed
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code_val,
                "message": message,
                "details": details,
            }
        },
    )


def _alerts_payload_builder(snapshot_at: str) -> Dict[str, Any]:
    if _CUSTOM_ALERTS_PAYLOAD is not None:
        return _CUSTOM_ALERTS_PAYLOAD(snapshot_at)
    return {
        "alerts": [],
        "summary": {"total_active": 0},
        "meta": {
            "snapshot_at": snapshot_at,
            "acknowledgement_supported": True,
            "surfaces": {"alerts": {"status": "ok"}},
        },
    }


def _client(monkeypatch=None) -> TestClient:
    app = FastAPI()
    router = create_incident_router(
        extract_identity=_extract_identity,
        bff_error=_bff_error,
        build_operator_alerts_payload=_alerts_payload_builder,
        acknowledged_alerts=_ACKNOWLEDGED_ALERTS,
        idempotency_ledger=_GOV_BFF_IDEMPOTENCY,
        reject_body_idempotency_key=_reject_body_idempotency_key,
    )
    app.include_router(router)

    @app.exception_handler(HTTPException)
    def _exc_handler(request: Request, exc: HTTPException):
        content = exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail)}}
        return JSONResponse(status_code=exc.status_code, content=content)

    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_idempotency_store():
    _GOV_BFF_IDEMPOTENCY.clear()
    _ACKNOWLEDGED_ALERTS.clear()
    global _CUSTOM_ALERTS_PAYLOAD
    _CUSTOM_ALERTS_PAYLOAD = None
    yield
    _GOV_BFF_IDEMPOTENCY.clear()
    _ACKNOWLEDGED_ALERTS.clear()
    _CUSTOM_ALERTS_PAYLOAD = None


@pytest.fixture()
def seeded_alerts(monkeypatch):
    """Patch alerts builder to return known test alert."""
    global _CUSTOM_ALERTS_PAYLOAD

    def _patched(snapshot_at: str) -> Dict[str, Any]:
        return {
            "alerts": [_TEST_ALERT],
            "summary": {"total_active": 1, "highest_severity": "high", "by_severity": {"high": 1}, "by_category": {"runtime": 1}},
            "meta": {
                "snapshot_at": snapshot_at,
                "acknowledgement_supported": False,
                "surfaces": {"alerts": {"status": "ok", "dataset": "alerts"}},
            },
        }

    _CUSTOM_ALERTS_PAYLOAD = _patched
    yield _TEST_ALERT_ID


def test_acknowledge_returns_202_with_command_response(monkeypatch, seeded_alerts) -> None:
    client = _client(monkeypatch)
    resp = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-key-001"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "data" in body
    assert body["data"]["status"] == "accepted"
    assert "command_id" in body["data"] or "commandId" in body["data"]
    assert "meta" in body


def test_acknowledge_idempotency_replay(monkeypatch, seeded_alerts) -> None:
    client = _client(monkeypatch)
    headers = {"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-replay-key"}

    first = client.post(f"/bff/alerts/{seeded_alerts}/acknowledge", headers=headers)
    assert first.status_code == 202, first.text

    second = client.post(f"/bff/alerts/{seeded_alerts}/acknowledge", headers=headers)
    assert second.status_code == 202, second.text
    assert first.json()["data"]["command_id"] == second.json()["data"]["command_id"]


def test_acknowledge_idempotency_conflict_returns_409(monkeypatch, seeded_alerts) -> None:
    client = _client(monkeypatch)
    key = "ack-conflict-key-001"

    first = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        json={"note": "first reason"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": key},
    )
    assert first.status_code == 202, first.text

    second = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        json={"note": "different reason"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": key},
    )
    assert second.status_code == 409
    detail = second.json()
    assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_acknowledge_anonymous_returns_401(monkeypatch) -> None:
    client = _client(monkeypatch)
    resp = client.post("/bff/alerts/alert-kill-switch-state/acknowledge")
    assert resp.status_code == 401


def test_acknowledge_unknown_alert_returns_404_when_surface_available(monkeypatch, seeded_alerts) -> None:
    client = _client(monkeypatch)
    resp = client.post(
        "/bff/alerts/no-such-alert-id-xyz999/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-404-key"},
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()
    assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert detail["error"]["details"].get("precondition_failed") == "alert_id"


def test_acknowledge_body_idempotency_key_rejected(monkeypatch, seeded_alerts) -> None:
    client = _client(monkeypatch)
    resp = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        json={"idempotency_key": "should-be-rejected"},
        headers={"Authorization": _OPERATOR_AUTH},
    )
    assert resp.status_code == 400
    detail = resp.json()
    assert detail["error"]["code"] == "VALIDATION_FAILED"


def test_acknowledge_response_has_tracking_url(monkeypatch, seeded_alerts) -> None:
    client = _client(monkeypatch)
    resp = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-tracking-key"},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data.get("trackingUrl") or data.get("tracking_url"), "Response must include a trackingUrl"


def test_acknowledge_populates_ack_store(monkeypatch, seeded_alerts) -> None:
    """POST /bff/alerts/{id}/acknowledge must write to _ACKNOWLEDGED_ALERTS."""
    client = _client(monkeypatch)
    resp = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        json={"note": "ack store test"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-store-key"},
    )
    assert resp.status_code == 202, resp.text
    assert seeded_alerts in _ACKNOWLEDGED_ALERTS
    ack = _ACKNOWLEDGED_ALERTS[seeded_alerts]
    assert "acknowledged_by" in ack
    assert "acknowledged_at" in ack


def test_alerts_list_meta_acknowledgement_supported(monkeypatch) -> None:
    """GET /bff/alerts must return meta.acknowledgement_supported = true."""
    client = _client(monkeypatch)
    resp = client.get("/bff/alerts", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    assert meta.get("acknowledgement_supported") is True, (
        f"meta.acknowledgement_supported should be True, got {meta.get('acknowledgement_supported')!r}"
    )

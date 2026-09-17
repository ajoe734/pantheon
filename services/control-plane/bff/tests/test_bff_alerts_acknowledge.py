from __future__ import annotations

import tempfile
import uuid
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.models import ErrorCode


# NOTE (BFF-TEST-MIGRATION-CB03-AUTH-SESSION-SECURITY-001 known gap):
# ``reject_body_idempotency_key`` is a cross-cutting "final contract" request
# validation policy (reject Idempotency-Key duplicated in the body) that is
# still only defined inline in main.py (``_reject_body_idempotency_key``,
# ~main.py:1376) and has not been extracted into ``auth/policy.py`` or any
# other importable module alongside its sibling policy helpers
# (``bff_error``, ``extract_identity``, ``require_read_role``, ...).
# ``incidents.router.create_incident_router`` already anticipates injection
# of this callable (its own built-in default is an intentional no-op), so
# this test supplies a byte-for-byte behavioral equivalent built from the
# same real ``auth_policy.bff_error`` envelope constructor, rather than
# reimplementing the alert-acknowledge business logic under test. See the
# evidence.json for this task for the recommended follow-up: extract
# ``_reject_body_idempotency_key`` into ``auth/policy.py`` so real callers and
# tests share one definition.
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

_TEST_ALERT_ID = "alert-test-ack-001"
_TEST_ALERT: Dict[str, Any] = {
    "alert_id": _TEST_ALERT_ID,
    "severity": "high",
    "category": "runtime",
    "raised_at": "2026-05-23T00:00:00Z",
    "summary": "Test runtime alert for acknowledge tests.",
}
_OPERATOR_AUTH = "Bearer op-ack-tester:operator"


def _default_alerts_payload(snapshot_at: str) -> Dict[str, Any]:
    """Baseline alerts payload used when a test does not seed a fixed alert."""
    return {
        "alerts": [],
        "summary": {"total_active": 0, "highest_severity": None, "by_severity": {}, "by_category": {}},
        "meta": {
            "snapshot_at": snapshot_at,
            "acknowledgement_supported": True,
            "surfaces": {"alerts": {"status": "ok", "dataset": "alerts"}},
        },
    }


class _AlertsPayloadHolder:
    """Test-local mutable holder so tests can swap the alerts payload builder
    without reconstructing the router (mirrors monkeypatching a module-level
    function in the original ``main`` singleton)."""

    def __init__(self) -> None:
        self.builder = _default_alerts_payload

    def __call__(self, snapshot_at: str) -> Dict[str, Any]:
        return self.builder(snapshot_at)


@pytest.fixture()
def harness(tmp_path):
    command_store = CommandStore(str(tmp_path / f"commands-{uuid.uuid4().hex[:8]}.jsonl"))
    acknowledged_alerts: Dict[str, Any] = {}
    idempotency_ledger: Dict[str, Any] = {}
    alerts_payload = _AlertsPayloadHolder()

    app = FastAPI()
    app.include_router(
        create_incident_router(
            command_store=command_store,
            build_operator_alerts_payload=alerts_payload,
            acknowledged_alerts=acknowledged_alerts,
            idempotency_ledger=idempotency_ledger,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            reject_body_idempotency_key=_reject_body_idempotency_key,
        )
    )
    register_error_handlers(app)
    return app, acknowledged_alerts, alerts_payload


def _client(harness, monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    app, _acknowledged_alerts, _alerts_payload = harness
    return TestClient(app)


@pytest.fixture()
def seeded_alerts(harness):
    """Make the alerts payload builder always return a known test alert."""
    _app, _acknowledged_alerts, alerts_payload = harness

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

    alerts_payload.builder = _patched
    yield _TEST_ALERT_ID


def test_acknowledge_returns_202_with_command_response(harness, monkeypatch, seeded_alerts) -> None:
    client = _client(harness, monkeypatch)
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


def test_acknowledge_idempotency_replay(harness, monkeypatch, seeded_alerts) -> None:
    client = _client(harness, monkeypatch)
    headers = {"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-replay-key"}

    first = client.post(f"/bff/alerts/{seeded_alerts}/acknowledge", headers=headers)
    assert first.status_code == 202, first.text

    second = client.post(f"/bff/alerts/{seeded_alerts}/acknowledge", headers=headers)
    assert second.status_code == 202, second.text
    assert first.json()["data"]["command_id"] == second.json()["data"]["command_id"]


def test_acknowledge_idempotency_conflict_returns_409(harness, monkeypatch, seeded_alerts) -> None:
    client = _client(harness, monkeypatch)
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


def test_acknowledge_anonymous_returns_401(harness, monkeypatch) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post("/bff/alerts/alert-kill-switch-state/acknowledge")
    assert resp.status_code == 401


def test_acknowledge_unknown_alert_returns_404_when_surface_available(harness, monkeypatch, seeded_alerts) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post(
        "/bff/alerts/no-such-alert-id-xyz999/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-404-key"},
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()
    assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert detail["error"]["details"].get("precondition_failed") == "alert_id"


def test_acknowledge_body_idempotency_key_rejected(harness, monkeypatch, seeded_alerts) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        json={"idempotency_key": "should-be-rejected"},
        headers={"Authorization": _OPERATOR_AUTH},
    )
    assert resp.status_code == 400
    detail = resp.json()
    assert detail["error"]["code"] == "VALIDATION_FAILED"


def test_acknowledge_response_has_tracking_url(harness, monkeypatch, seeded_alerts) -> None:
    client = _client(harness, monkeypatch)
    resp = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-tracking-key"},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data.get("trackingUrl") or data.get("tracking_url"), "Response must include a trackingUrl"


def test_acknowledge_populates_ack_store(harness, monkeypatch, seeded_alerts) -> None:
    """POST /bff/alerts/{id}/acknowledge must write to the acknowledged_alerts store."""
    client = _client(harness, monkeypatch)
    _app, acknowledged_alerts, _alerts_payload = harness
    resp = client.post(
        f"/bff/alerts/{seeded_alerts}/acknowledge",
        json={"note": "ack store test"},
        headers={"Authorization": _OPERATOR_AUTH, "Idempotency-Key": "ack-store-key"},
    )
    assert resp.status_code == 202, resp.text
    assert seeded_alerts in acknowledged_alerts
    ack = acknowledged_alerts[seeded_alerts]
    assert "acknowledged_by" in ack
    assert "acknowledged_at" in ack


def test_alerts_list_meta_acknowledgement_supported(harness, monkeypatch) -> None:
    """GET /bff/alerts must return meta.acknowledgement_supported = true."""
    client = _client(harness, monkeypatch)
    resp = client.get("/bff/alerts", headers={"Authorization": _OPERATOR_AUTH})
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    assert meta.get("acknowledgement_supported") is True, (
        f"meta.acknowledgement_supported should be True, got {meta.get('acknowledgement_supported')!r}"
    )

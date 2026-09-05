from __future__ import annotations

import os
import sys
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main

_TEST_ALERT_ID = "alert-test-ack-001"
_TEST_ALERT: Dict[str, Any] = {
    "alert_id": _TEST_ALERT_ID,
    "severity": "high",
    "category": "runtime",
    "raised_at": "2026-05-23T00:00:00Z",
    "summary": "Test runtime alert for acknowledge tests.",
}
_OPERATOR_AUTH = "Bearer op-ack-tester:operator"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    return TestClient(bff_main.app)


@pytest.fixture(autouse=True)
def clear_idempotency_store():
    bff_main._GOV_BFF_IDEMPOTENCY.clear()
    bff_main._ACKNOWLEDGED_ALERTS.clear()
    yield
    bff_main._GOV_BFF_IDEMPOTENCY.clear()
    bff_main._ACKNOWLEDGED_ALERTS.clear()


@pytest.fixture()
def seeded_alerts(monkeypatch):
    """Patch _build_operator_alerts_payload to always return a known test alert."""
    original = bff_main._build_operator_alerts_payload

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

    monkeypatch.setattr(bff_main, "_build_operator_alerts_payload", _patched)
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
    assert seeded_alerts in bff_main._ACKNOWLEDGED_ALERTS
    ack = bff_main._ACKNOWLEDGED_ALERTS[seeded_alerts]
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

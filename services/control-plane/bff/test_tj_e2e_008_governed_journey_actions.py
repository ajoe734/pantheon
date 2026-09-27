"""TJ-E2E-008 governed Trade Journey action admission contracts."""
from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

BFF_DIR = os.path.dirname(os.path.abspath(__file__))

from services.control_plane.bff import trade_journeys as tj  # noqa: E402
from test_tj_e2e_005_trade_journeys_read_api import InMemoryPostgresProjectionReader  # noqa: E402
from services.trade_journey.materializer import JourneyMaterializer  # noqa: E402


def _client(dispatch=None, *, unrelated_events=0, action_ledger=None):
    events = [{
        "event_id": "event-1", "journey_id": "tj-8", "tenant_id": "tenant-a",
        "environment": "paper", "occurred_at": "2026-07-12T00:00:00Z",
        "source": "test", "stage": "order_submission", "stage_status": "succeeded",
    }]
    for index in range(unrelated_events):
        events.append({
            "event_id": f"event-other-{index}", "journey_id": "tj-other",
            "tenant_id": "tenant-a", "environment": "paper",
            "occurred_at": f"2026-07-12T00:00:{index + 1:02d}Z",
            "source": "test", "stage": "order_submission", "stage_status": "succeeded",
        })
    reader = InMemoryPostgresProjectionReader(events)

    def identity(auth):
        if not auth:
            raise HTTPException(401, "authentication required")
        roles = auth.removeprefix("Bearer ").split(",")
        return type("Identity", (), {"roles": roles, "claims": {"tenant_ids": ["tenant-a"]}})()

    def read_role(subject):
        if not set(subject.roles) & {"viewer", "operator", "admin"}:
            raise HTTPException(403, "read denied")

    def operator_role(subject):
        if not set(subject.roles) & {"operator", "admin"}:
            raise HTTPException(403, "operator denied")

    app = FastAPI()
    app.include_router(tj.create_trade_journeys_router(
        extract_identity=identity,
        require_read_role=read_role,
        require_operator_role=operator_role,
        get_projection_reader=lambda: reader,
        dispatch_action=dispatch or (lambda command: {
            "status": "succeeded", "receipt_id": "receipt-1",
            "readback": {"journey_id": command["journey_id"], "state": "paused"},
        }),
        action_ledger=action_ledger or tj.JourneyActionLedger(),
        utc_now=lambda: "2026-07-12T01:00:00Z",
    ))
    revision = reader.materializer.get(
        "tj-8", tenant_id="tenant-a", environment="paper",
    ).snapshot["revision"]
    return TestClient(app), revision


def _post(client, revision, *, key="idem-key-0001", action="pause", reason="operator requested pause", roles="operator"):
    return client.post(
        "/bff/management/trade-journeys/tj-8/actions?tenant_id=tenant-a&environment=paper",
        headers={"Authorization": f"Bearer {roles}", "Idempotency-Key": key},
        json={"action": action, "reason": reason, "expected_revision": revision, "confirm_token": "confirm-1"},
    )


def test_operator_action_returns_receipt_readback_and_refetch_contract():
    client, revision = _client()
    response = _post(client, revision)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "succeeded"
    assert body["data"]["readback"]["state"] == "paused"
    assert body["meta"]["refetch_required"] is True
    assert body["data"]["human_inbox"]["return_to"].endswith("/tj-8")


def test_action_revision_is_scoped_to_target_journey():
    client, revision = _client(unrelated_events=2)

    response = _post(client, revision, key="idem-multi-journey")

    assert revision == 1
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "succeeded"


def test_rbac_stale_duplicate_conflict_and_partial_failure():
    client, revision = _client()
    assert _post(client, revision, roles="viewer").status_code == 403
    stale = _post(client, revision + 1, key="idem-stale-01")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_JOURNEY_REVISION"

    first = _post(client, revision)
    replay = _post(client, revision)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["data"]["idempotent_replay"] is True
    conflict = _post(client, revision, reason="different confirmed reason")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    failed_client, failed_revision = _client(lambda command: {"status": "succeeded", "readback": None})
    failed = _post(failed_client, failed_revision, key="idem-partial-01")
    assert failed.status_code == 502
    assert failed.json()["data"]["code"] == "READBACK_REQUIRED"


def test_human_review_preserves_inbox_return_context():
    captured = []
    client, revision = _client(lambda command: captured.append(command) or {
        "status": "succeeded", "receipt_id": "review-1", "readback": {"queued": True},
    })
    response = _post(client, revision, key="idem-review-01", action="human_review")
    assert response.status_code == 200
    assert captured[0]["human_inbox"]["href"] == "/bff/management/human-inbox"
    assert captured[0]["human_inbox"]["return_to"] == "/management/trade-journeys/tj-8"


def test_concurrent_reservation_fails_closed_without_double_dispatch():
    ledger = tj.JourneyActionLedger()
    dispatched = []
    client, revision = _client(lambda command: dispatched.append(command) or {
        "status": "succeeded", "readback": {"state": "paused"},
    }, action_ledger=ledger)
    body = {
        "journey_id": "tj-8", "tenant_id": "tenant-a", "environment": "paper",
        "action": "pause", "reason": "operator requested pause",
        "expected_revision": revision, "confirm_token": "confirm-1",
        "incident_id": None, "return_to": None,
    }
    import hashlib, json
    request_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    assert ledger.reserve("idem-pending-01", request_hash)[0] == "new"
    response = _post(client, revision, key="idem-pending-01")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACTION_IN_PROGRESS"
    assert dispatched == []

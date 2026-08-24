"""TJ-E2E-007 revisioned SSE and server-composed attention contracts."""
from __future__ import annotations

import json
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

BFF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BFF_DIR)

import trade_journeys as tj  # noqa: E402
from test_tj_e2e_005_trade_journeys_read_api import InMemoryPostgresProjectionReader  # noqa: E402
from services.trade_journey.materializer import JourneyMaterializer  # noqa: E402


def _event(event_id: str, journey_id: str, minute: int, stage: str = "broker_acknowledgement"):
    return {"event_id": event_id, "journey_id": journey_id, "tenant_id": "tenant-a",
            "environment": "paper", "occurred_at": f"2026-07-12T00:{minute:02d}:00Z",
            "source": "test", "stage": stage, "stage_status": "running"}


def _client(events):
    reader = InMemoryPostgresProjectionReader(events)

    def identity(auth):
        if not auth:
            raise HTTPException(401)
        return type("Identity", (), {"roles": ["operator"], "claims": {"tenant_ids": ["tenant-a"]}})()

    app = FastAPI()
    app.include_router(tj.create_trade_journeys_router(
        extract_identity=identity, require_read_role=lambda _: None,
        get_projection_reader=lambda: reader,
        utc_now=lambda: "2026-07-12T00:20:00Z"))
    return TestClient(app), reader.materializer


def test_sse_emits_revisioned_invalidation_and_reconnect_gap_refetch():
    client, materializer = _client([_event("e1", "tj-1", 1)])
    headers = {"Authorization": "Bearer test"}
    response = client.get("/bff/management/trade-journeys/events?tenant_id=tenant-a&environment=paper",
                          headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert f"id: {materializer.revision}" in response.text
    assert "event: journeys_changed" in response.text
    assert '"snapshot_refetch":true' in response.text

    gap = client.get("/bff/management/trade-journeys/events?tenant_id=tenant-a&environment=paper",
                     headers={**headers, "Last-Event-ID": "99"})
    assert "event: snapshot_refetch_required" in gap.text
    assert '"gap":true' in gap.text


def test_sse_rejects_invalid_cursor_and_cross_tenant_access():
    client, _ = _client([_event("e1", "tj-1", 1)])
    url = "/bff/management/trade-journeys/events?tenant_id=tenant-a&environment=paper"
    assert client.get(url, headers={"Authorization": "Bearer test", "Last-Event-ID": "bad"}).status_code == 400
    assert client.get(url.replace("tenant-a", "tenant-b"), headers={"Authorization": "Bearer test"}).status_code == 403


def test_attention_applies_stage_threshold_and_sorts_severity(monkeypatch):
    monkeypatch.setenv(tj._STALLED_THRESHOLDS_ENV, json.dumps({
        "paper": {"default": 1200, "broker_acknowledgement": 60}
    }))
    client, _ = _client([_event("e1", "tj-stalled", 1), _event("e2", "tj-recent", 19)])
    response = client.get("/bff/management/trade-journeys/attention?tenant_id=tenant-a&environment=paper",
                          headers={"Authorization": "Bearer test"})
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["journey_id"] for item in items] == ["tj-stalled", "tj-recent"]
    assert items[0]["severity"] == "critical"
    assert items[0]["threshold_seconds"] == 60
    assert "stage_stalled" in items[0]["reason_codes"]
    assert items[0]["allowed_actions"] == ["open_journey", "inspect_evidence"]

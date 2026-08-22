"""TJ-E2E-011 runtime SLO/data-quality endpoint and governed alert path.

Reviewer feedback on PR #3460 found that `slo_data_quality.py` was a pure
in-memory evaluator with no runtime/materializer-health integration, no
dashboard or alert-rule artifact, and no alert transport consuming
`DataQualityIncident`. These tests exercise the real BFF endpoint
(`/bff/management/trade-journeys/slo`) against a live `JourneyMaterializer`
and prove an emitted incident reaches the governed alert transport's durable
outbox — not just an in-memory return value.
"""
from __future__ import annotations

import os
import sys

BFF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BFF_DIR)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import trade_journeys as tj  # noqa: E402
from services.trade_journey.alert_transport import DataQualityAlertTransport  # noqa: E402
from services.trade_journey.api_latency_recorder import ApiLatencyRecorder  # noqa: E402
from services.trade_journey.materializer import JourneyMaterializer  # noqa: E402

from test_tj_e2e_005_trade_journeys_read_api import InMemoryPostgresProjectionReader  # noqa: E402


OPERATOR_HEADERS = {"Authorization": "Bearer op:operator"}


def _event(event_id, journey_id, stage, status, minute, *, tenant="tenant-a", environment="paper", **extra):
    return {
        "event_id": event_id, "journey_id": journey_id, "tenant_id": tenant,
        "environment": environment, "occurred_at": f"2026-07-12T00:{minute:02d}:00Z",
        "source": "test", "stage": stage, "stage_status": status, **extra,
    }


def _identity(authorization):
    if not authorization:
        raise HTTPException(401, "authentication required")
    token = authorization.removeprefix("Bearer ")
    _, roles_part = token.split(":", 1)
    return type("Identity", (), {"roles": roles_part.split(","), "claims": {"tenant_ids": ["tenant-a"]}})()


def _require_read_role(identity):
    if not set(identity.roles) & {"viewer", "operator", "admin"}:
        raise HTTPException(403, "read denied")


def _client(events, *, transport=None, latency_recorder=None, utc_now="2026-07-12T00:05:00Z"):
    materializer = JourneyMaterializer()
    materializer.rebuild(events)
    store = tj.TradeJourneyEventStore()
    store.materializer = lambda: materializer
    reader = InMemoryPostgresProjectionReader(events)

    app = FastAPI()
    app.include_router(tj.create_trade_journeys_router(
        extract_identity=_identity,
        require_read_role=_require_read_role,
        get_event_store=lambda: store,
        get_projection_reader=lambda: reader,
        utc_now=lambda: utc_now,
        get_slo_alert_transport=lambda: transport,
        latency_recorder=latency_recorder or ApiLatencyRecorder(),
    ))
    return TestClient(app)


def test_slo_endpoint_reports_metrics_and_dashboard_snapshot(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    client = _client(
        [_event("e1", "tj-1", "order_submission", "succeeded", 0, order_id="order-only")],
        transport=transport,
    )
    resp = client.get(
        "/bff/management/trade-journeys/slo?tenant_id=tenant-a&environment=paper",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["id"] == "trade-journey-slo"
    assert data["metrics"]["total_journeys"] == 1
    assert data["dashboard"]["panels"], "dashboard snapshot must have panels"


def test_slo_endpoint_publishes_emitted_incidents_to_the_governed_alert_path(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    client = _client(
        [_event("e1", "tj-orphan", "order_submission", "succeeded", 0, order_id="order-only")],
        transport=transport,
    )
    resp = client.get(
        "/bff/management/trade-journeys/slo?tenant_id=tenant-a&environment=paper",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["alerts_published"] == len(data["incidents"])
    assert data["alerts_published"] > 0

    # The governed alert path is durable: a fresh transport instance reading
    # the same outbox path sees the published records, not just the
    # in-process return value.
    reopened = DataQualityAlertTransport(data_dir=tmp_path)
    published = reopened.load_published()
    assert len(published) == data["alerts_published"]
    published_codes = {record.event.payload["code"] for record in published}
    assert "orphan_identifier" in published_codes
    orphan_record = next(r for r in published if r.event.payload["code"] == "orphan_identifier")
    assert orphan_record.event.payload["journey_id"] == "tj-orphan"


def test_slo_endpoint_measures_detail_api_p95_from_recorded_latency(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    recorder = ApiLatencyRecorder()
    client = _client(
        [_event("e1", "tj-1", "signal_generation", "succeeded", 0, signal_id="sig-1")],
        transport=transport,
        latency_recorder=recorder,
    )

    detail_resp = client.get(
        "/bff/management/trade-journeys/tj-1?tenant_id=tenant-a&environment=paper",
        headers=OPERATOR_HEADERS,
    )
    assert detail_resp.status_code == 200, detail_resp.text
    assert recorder.samples("detail"), "detail handler must record a latency sample"

    slo_resp = client.get(
        "/bff/management/trade-journeys/slo?tenant_id=tenant-a&environment=paper",
        headers=OPERATOR_HEADERS,
    )
    assert slo_resp.status_code == 200, slo_resp.text
    assert slo_resp.json()["data"]["metrics"]["detail_api_p95_ms"] is not None


def test_slo_endpoint_enforces_tenant_and_environment_scope(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    client = _client(
        [_event("e1", "tj-1", "signal_generation", "succeeded", 0, signal_id="sig-1")],
        transport=transport,
    )
    bad_env = client.get(
        "/bff/management/trade-journeys/slo?tenant_id=tenant-a&environment=not-a-real-env",
        headers=OPERATOR_HEADERS,
    )
    assert bad_env.status_code == 400

    no_auth = client.get("/bff/management/trade-journeys/slo?tenant_id=tenant-a&environment=paper")
    assert no_auth.status_code == 401


def test_slo_endpoint_registered_before_journey_id_param_route():
    def _collect_route_paths(routes) -> list[str]:
        paths = []
        for r in routes:
            if hasattr(r, "path"):
                paths.append(r.path)
            if hasattr(r, "routes"):
                paths.extend(_collect_route_paths(r.routes))
            if hasattr(r, "original_router") and hasattr(r.original_router, "routes"):
                paths.extend(_collect_route_paths(r.original_router.routes))
        return paths

    app = FastAPI()
    app.include_router(tj.create_trade_journeys_router(
        extract_identity=_identity,
        require_read_role=_require_read_role,
    ))
    paths_in_order = [
        path for path in _collect_route_paths(app.routes)
        if path.startswith("/bff/management/trade-journeys")
    ]
    slo_idx = paths_in_order.index("/bff/management/trade-journeys/slo")
    detail_idx = paths_in_order.index("/bff/management/trade-journeys/{journey_id}")
    assert slo_idx < detail_idx

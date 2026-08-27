"""Unit tests for Agora Operational & Data Readiness (SD-AGC-06).

Verifies:
  1. operational readiness binds source snapshot and producer identity
  2. stale, empty_fresh, and unavailable are distinct and mutually exclusive
  3. route is read-only and never auth-critical (requiredForAuthentication=False)
  4. meta has no_order_route_proof='agora_operational_readiness_read_only'
  5. downstream surface statuses correctly reflect upstream source and producer health
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .operational_readiness import (
    AgoraOperationalReadinessService,
    AgoraOperationalReadinessEnvelope,
    create_operational_readiness_router,
)


def _utc_now_iso(offset_seconds: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.fixture
def readiness_service() -> AgoraOperationalReadinessService:
    svc = AgoraOperationalReadinessService(default_sla_seconds=86400)
    svc.reset_custom_state()
    return svc


@pytest.fixture
def test_app(readiness_service: AgoraOperationalReadinessService) -> FastAPI:
    app = FastAPI()
    router = create_operational_readiness_router(
        utc_now=lambda: _utc_now_iso(0),
        service=readiness_service,
    )
    app.include_router(router)
    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


# --------------------------------------------------------------------------- #
# Test Cases
# --------------------------------------------------------------------------- #

def test_operational_readiness_fresh_ok(readiness_service: AgoraOperationalReadinessService) -> None:
    """Fresh source snapshot and active producer yield 'ok' overall readiness."""
    now_iso = _utc_now_iso(0)
    source_ts = _utc_now_iso(-1800)  # 30 mins ago, well within 86400s SLA

    readiness_service.set_source_snapshot({
        "snapshot_id": "mss-test-001",
        "source_instance_id": "src-demo-tw-stock",
        "event_time": source_ts,
        "sla_seconds": 86400,
    })
    readiness_service.set_source_instance({
        "source_instance_id": "src-demo-tw-stock",
        "desired_state": "enabled",
        "observed_state": "healthy",
    })
    readiness_service.set_signal_producer({
        "status": "ok",
        "active_binding": "rb-binding-001",
        "consumed_snapshot_id": "mss-test-001",
        "last_success_at": now_iso,
        "enqueued": 12,
        "reason": "healthy",
    })
    readiness_service.set_surface_data("signals", {"status": "ok", "count": 12, "cursor": "sig-012"})
    readiness_service.set_surface_data("decision_events", {"status": "ok", "count": 8, "cursor": "dec-008"})

    envelope = readiness_service.compose_readiness(now_iso=now_iso)

    assert isinstance(envelope, AgoraOperationalReadinessEnvelope)
    data = envelope.data
    meta = envelope.meta

    assert data.status == "ok"
    assert data.source.snapshot_id == "mss-test-001"
    assert data.source.source_instance_id == "src-demo-tw-stock"
    assert data.source.freshness == "fresh"
    assert data.source.age_seconds is not None
    assert data.source.age_seconds <= 86400

    assert data.signal_producer.status == "ok"
    assert data.signal_producer.consumed_snapshot_id == "mss-test-001"
    assert data.signal_producer.enqueued == 12

    assert data.surfaces["signals"].status == "ok"
    assert data.surfaces["signals"].count == 12
    assert data.surfaces["decision_events"].status == "ok"
    assert data.surfaces["decision_events"].count == 8

    assert meta.requiredForAuthentication is False
    assert meta.no_order_route_proof == "agora_operational_readiness_read_only"


def test_operational_readiness_stale_source(readiness_service: AgoraOperationalReadinessService) -> None:
    """Stale source snapshot degrades producer and marks surfaces as unavailable (upstream_stale)."""
    now_iso = _utc_now_iso(0)
    source_ts = _utc_now_iso(-500107)  # ~5.79 days ago (> 86400s SLA)

    readiness_service.set_source_snapshot({
        "snapshot_id": "mss-stale-001",
        "source_instance_id": "src-demo-tw-stock",
        "event_time": source_ts,
        "sla_seconds": 86400,
    })
    readiness_service.set_signal_producer({
        "status": "ok",
        "active_binding": "rb-binding-001",
        "consumed_snapshot_id": "mss-stale-001",
        "enqueued": 0,
    })

    envelope = readiness_service.compose_readiness(now_iso=now_iso)
    data = envelope.data

    assert data.status == "degraded"
    assert data.source.freshness == "stale"
    assert data.source.age_seconds is not None
    assert data.source.age_seconds > 86400

    # Producer degraded due to stale source
    assert data.signal_producer.status == "degraded"
    assert data.signal_producer.reason == "source_snapshot_stale"

    # Surfaces marked unavailable with upstream_stale reason
    assert data.surfaces["signals"].status == "unavailable"
    assert data.surfaces["signals"].reason == "upstream_stale"
    assert data.surfaces["decision_events"].status == "unavailable"
    assert data.surfaces["decision_events"].reason == "upstream_stale"


def test_operational_readiness_empty_fresh_distinction(readiness_service: AgoraOperationalReadinessService) -> None:
    """Legitimately fresh source that produces 0 signals is 'empty_fresh', not 'unavailable' or 'stale'."""
    now_iso = _utc_now_iso(0)
    source_ts = _utc_now_iso(-600)  # 10 minutes ago (fresh)

    readiness_service.set_source_snapshot({
        "snapshot_id": "mss-fresh-zero-001",
        "source_instance_id": "src-demo-tw-stock",
        "event_time": source_ts,
        "sla_seconds": 86400,
        "is_empty_fresh": True,
    })
    readiness_service.set_signal_producer({
        "status": "empty_fresh",
        "active_binding": "rb-binding-001",
        "consumed_snapshot_id": "mss-fresh-zero-001",
        "enqueued": 0,
        "reason": "rule_evaluation_zero_signals",
    })

    envelope = readiness_service.compose_readiness(now_iso=now_iso)
    data = envelope.data

    assert data.status == "empty_fresh"
    assert data.source.freshness == "empty_fresh"
    assert data.signal_producer.status == "empty_fresh"
    assert data.signal_producer.reason == "rule_evaluation_zero_signals"

    assert data.surfaces["signals"].status == "empty_fresh"
    assert data.surfaces["signals"].count == 0
    assert data.surfaces["signals"].reason == "rule_evaluation_zero_signals"


def test_operational_readiness_unavailable_source(readiness_service: AgoraOperationalReadinessService) -> None:
    """Unconfigured or unreachable source returns 'unavailable'."""
    now_iso = _utc_now_iso(0)
    readiness_service.reset_custom_state()  # No source configured

    envelope = readiness_service.compose_readiness(now_iso=now_iso)
    data = envelope.data

    assert data.status == "unavailable"
    assert data.source.freshness == "unavailable"
    assert data.signal_producer.status == "unavailable"
    assert data.surfaces["signals"].status == "unavailable"
    assert data.surfaces["signals"].reason == "upstream_unavailable"


def test_operational_readiness_distinct_states_matrix(readiness_service: AgoraOperationalReadinessService) -> None:
    """Ensure stale, empty_fresh, and unavailable states are mutually distinct."""
    states_observed = set()

    # 1. Stale state
    readiness_service.set_source_snapshot({
        "snapshot_id": "mss-1",
        "event_time": _utc_now_iso(-200000),
        "sla_seconds": 86400,
    })
    env_stale = readiness_service.compose_readiness(now_iso=_utc_now_iso(0))
    states_observed.add(env_stale.data.source.freshness)
    assert env_stale.data.source.freshness == "stale"

    # 2. Empty fresh state
    readiness_service.set_source_snapshot({
        "snapshot_id": "mss-2",
        "event_time": _utc_now_iso(-300),
        "sla_seconds": 86400,
        "is_empty_fresh": True,
    })
    readiness_service.set_signal_producer({"status": "empty_fresh"})
    env_empty_fresh = readiness_service.compose_readiness(now_iso=_utc_now_iso(0))
    states_observed.add(env_empty_fresh.data.source.freshness)
    assert env_empty_fresh.data.source.freshness == "empty_fresh"

    # 3. Unavailable state
    readiness_service.reset_custom_state()
    env_unavail = readiness_service.compose_readiness(now_iso=_utc_now_iso(0))
    states_observed.add(env_unavail.data.source.freshness)
    assert env_unavail.data.source.freshness == "unavailable"

    assert len(states_observed) == 3
    assert states_observed == {"stale", "empty_fresh", "unavailable"}


def test_operational_readiness_http_endpoint(client: TestClient, readiness_service: AgoraOperationalReadinessService) -> None:
    """GET /bff/agora/operational-readiness returns HTTP 200 without auth and matches contract."""
    readiness_service.set_source_snapshot({
        "snapshot_id": "mss-http-001",
        "source_instance_id": "src-demo-tw-stock",
        "event_time": _utc_now_iso(-500107),
        "sla_seconds": 86400,
    })
    readiness_service.set_signal_producer({
        "status": "degraded",
        "consumed_snapshot_id": "mss-http-001",
        "enqueued": 0,
        "reason": "source_snapshot_stale",
    })

    resp = client.get("/bff/agora/operational-readiness")
    assert resp.status_code == 200

    body = resp.json()
    assert "data" in body
    assert "meta" in body

    data = body["data"]
    meta = body["meta"]

    assert data["status"] == "degraded"
    assert data["source"]["snapshot_id"] == "mss-http-001"
    assert data["source"]["freshness"] == "stale"
    assert data["signal_producer"]["status"] == "degraded"
    assert data["signal_producer"]["reason"] == "source_snapshot_stale"
    assert data["surfaces"]["signals"]["status"] == "unavailable"
    assert data["surfaces"]["signals"]["reason"] == "upstream_stale"

    assert meta["requiredForAuthentication"] is False
    assert meta["no_order_route_proof"] == "agora_operational_readiness_read_only"
    assert meta["capability"] == "agora.operational_readiness.v1"


def test_operational_readiness_with_auth_header(client: TestClient, readiness_service: AgoraOperationalReadinessService) -> None:
    """Route handles optional auth header and attaches tenant/user audience without breaking."""
    readiness_service.set_source_snapshot({
        "snapshot_id": "mss-auth-001",
        "event_time": _utc_now_iso(-100),
        "sla_seconds": 86400,
    })
    readiness_service.set_signal_producer({
        "status": "ok",
        "consumed_snapshot_id": "mss-auth-001",
        "enqueued": 5,
    })

    # Even with an arbitrary or bearer auth header, request succeeds
    resp = client.get(
        "/bff/agora/operational-readiness",
        headers={"Authorization": "Bearer some-token", "X-Tenant-Id": "tenant-test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "ok"
    assert body["meta"]["requiredForAuthentication"] is False

"""Focused BFF contract tests for v5 loop/sentinel/health/control-room routes (SEM-004)."""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
import downstream_health_monitor as health_module  # noqa: E402
from downstream_health_monitor import (  # noqa: E402
    DownstreamHealthMonitor,
    DownstreamProbeResult,
)
from read_store import ReadSurfaceStore, ServiceBackedReadAdapter  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402
import services.telemetry.main as telemetry_main  # noqa: E402
from services.telemetry.ingest_svc import TelemetryIngestService  # noqa: E402
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore  # noqa: E402
from services.telemetry.test_infrastructure_health_ingest import (  # noqa: E402
    _DurableFileBroker,
)

HEADERS = {"Authorization": "Bearer op-execute-plans:operator,reviewer,admin:mfa::tenant-dev"}

_INCIDENT_SEED = {
    "inc-loop-1": {
        "incident_id": "inc-loop-1",
        "title": "Loop Anomaly Detected",
        "status": "open",
        "severity": "high",
        "runtime_id": "rt-loop-1",
        "binding_id": "binding-loop-1",
        "capital_pool_id": "pool-main",
        "created_at": "2026-05-09T10:00:00Z",
        "resolved_at": None,
    },
    "inc-sentinel-1": {
        "incident_id": "inc-sentinel-1",
        "title": "Sentinel Finding Triggered",
        "status": "open",
        "severity": "medium",
        "runtime_id": "rt-sentinel-1",
        "binding_id": "binding-sentinel-1",
        "capital_pool_id": "pool-secondary",
        "created_at": "2026-05-09T11:00:00Z",
        "resolved_at": None,
    },
}


@contextmanager
def _v5_store(*, seed_incidents: bool = True) -> Iterator[TestClient]:
    import json as _json
    with tempfile.TemporaryDirectory() as td:
        snapshot_path = os.path.join(td, "read_surfaces.json")
        if seed_incidents:
            with open(snapshot_path, "w") as f:
                _json.dump({"incidents": _INCIDENT_SEED}, f)
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            snapshot_path,
            allow_local_snapshot_fallback=seed_incidents,
        )
        bff_main.read_store = store
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_store


def test_v5_loop_runs_list_returns_200(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/loop-runs", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "items" in payload
    assert "meta" in payload


def test_v5_loop_runs_list_contains_seeded_incident_derived_record(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/loop-runs", headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    items = payload["items"]
    ids = [item.get("id") for item in items]
    assert "inc-loop-1" in ids
    # sentinel incident is excluded from loop runs
    assert "inc-sentinel-1" not in ids
    assert items[0]["source"] == "legacy_incident_backfill"
    assert items[0]["projection_mode"] == "backfill"
    surface = payload["meta"]["surfaces"]["loop_runs"]
    assert surface["source"] == "legacy_incident_backfill"
    assert surface["status"] == "degraded"
    assert surface["accepted_live"] is False


def test_v5_loop_runs_projector_wrapper_precedes_incidents(monkeypatch, tmp_path):
    import json as _json

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    loop_store = tmp_path / "loop_runs.json"
    loop_store.write_text(
        _json.dumps(
            {
                "schema_version": "pantheon.loop-run-projection.v1",
                "generation": 7,
                "controller": {
                    "accepted_live": True,
                    "status": "ready",
                    "mode": "live",
                    "truth_level": "canonical_live",
                },
                "records": {
                    "lr-canonical": {
                        "id": "lr-canonical",
                        "status": "active",
                        "source": "canonical_telemetry_lifecycle_projector",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PANTHEON_BFF_LOOP_RUN_STORE", str(loop_store))

    with _v5_store() as client:
        response = client.get("/bff/v5/loop-runs", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["id"] for item in payload["items"]] == ["lr-canonical"]
    surface = payload["meta"]["surfaces"]["loop_runs"]
    assert surface["source"] != "legacy_incident_backfill"
    assert surface["status"] == "ok"
    assert surface["truth_status"] == "formal"
    assert surface["projection_generation"] == 7


@pytest.mark.parametrize(
    "controller",
    [
        {},
        {"accepted_live": False, "status": "ready", "mode": "live", "truth_level": "canonical_live"},
        {"accepted_live": True, "status": "failed", "mode": "live", "truth_level": "canonical_live"},
        {"accepted_live": True, "status": "degraded", "mode": "live", "truth_level": "canonical_live"},
        {"accepted_live": True, "status": "ready", "mode": "backfill", "truth_level": "backfill_only"},
        {"accepted_live": True, "status": "ready", "mode": "replay", "truth_level": "canonical_live"},
        {"accepted_live": True, "status": "ready", "mode": "recovery", "truth_level": "canonical_live"},
        {"accepted_live": True, "status": "ready", "mode": "live"},
    ],
)
def test_loop_run_controller_truth_gate_fails_closed(controller):
    assert bff_main._loop_run_controller_is_formal(
        {
            "schema_version": "pantheon.loop-run-projection.v1",
            "controller": controller,
        }
    ) is False


def test_v5_recovery_controller_degrades_all_loop_surfaces_without_incident_fallback(
    monkeypatch, tmp_path
):
    import json as _json

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    loop_store = tmp_path / "loop_runs.json"
    loop_store.write_text(
        _json.dumps(
            {
                "schema_version": "pantheon.loop-run-projection.v1",
                "generation": 8,
                "controller": {
                    "accepted_live": True,
                    "status": "recovering",
                    "mode": "recovery",
                    "truth_level": "canonical_live",
                },
                "records": {
                    "lr-canonical": {
                        "id": "lr-canonical",
                        "status": "active",
                        "runtime_id": "rt-canonical",
                        "source": "canonical_telemetry_lifecycle_projector",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PANTHEON_BFF_LOOP_RUN_STORE", str(loop_store))

    with _v5_store() as client:
        list_response = client.get("/bff/v5/loop-runs", headers=HEADERS)
        detail_response = client.get("/bff/v5/loop-runs/lr-canonical", headers=HEADERS)
        control_response = client.get("/bff/v5/control-room", headers=HEADERS)
        throughput_response = client.get("/bff/management/loop-throughput", headers=HEADERS)

    assert list_response.status_code == 200, list_response.text
    assert [item["id"] for item in list_response.json()["items"]] == ["lr-canonical"]
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["data"]["id"] == "lr-canonical"
    assert control_response.status_code == 200, control_response.text
    assert [item["id"] for item in control_response.json()["loops"]["items"]] == ["lr-canonical"]
    assert throughput_response.status_code == 200, throughput_response.text
    assert [item["loop_run_id"] for item in throughput_response.json()["data"]["items"]] == ["lr-canonical"]

    surfaces = [
        list_response.json()["meta"]["surfaces"]["loop_runs"],
        detail_response.json()["meta"]["surfaces"]["loop_run_detail"],
        control_response.json()["loops"]["meta"]["surfaces"]["loop_runs"],
        throughput_response.json()["meta"]["surfaces"]["loop_runs"],
    ]
    for surface in surfaces:
        assert surface["status"] == "degraded"
        assert surface["truth_status"] == "degraded"
        assert surface["source"] != "legacy_incident_backfill"
        assert surface["projection_mode"] == "recovery"
        assert surface["controller"]["status"] == "recovering"
    assert throughput_response.json()["meta"]["surfaces"]["loop_throughput"]["status"] == "degraded"


def test_v5_loop_runs_detail_seeded_record_returns_200(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/loop-runs/inc-loop-1", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json().get("data", {})
    assert data.get("id") == "inc-loop-1"
    assert data.get("derived_from_incident_id") == "inc-loop-1"


def test_v5_loop_runs_detail_unknown_id_is_404_when_source_available(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/loop-runs/not-a-loop-run", headers=HEADERS)
    assert response.status_code == 404, response.text


def test_v5_loop_runs_detail_missing_source_returns_degraded(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store(seed_incidents=False) as client:
        response = client.get("/bff/v5/loop-runs/any-loop-run", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json().get("data", {})
    assert data.get("status") == "degraded"


def test_v5_sentinel_findings_list_returns_200(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/sentinel/findings", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "items" in payload
    assert "meta" in payload


def test_v5_sentinel_findings_list_contains_seeded_incident_derived_record(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/sentinel/findings", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()["items"]
    ids = [item.get("id") for item in items]
    assert "inc-sentinel-1" in ids
    # loop incident is excluded from sentinel findings
    assert "inc-loop-1" not in ids


def test_v5_sentinel_findings_detail_seeded_record_returns_200(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/sentinel/findings/inc-sentinel-1", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json().get("data", {})
    assert data.get("id") == "inc-sentinel-1"
    assert data.get("derived_from_incident_id") == "inc-sentinel-1"


def test_v5_sentinel_findings_detail_unknown_id_is_404_when_source_available(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/sentinel/findings/not-a-finding", headers=HEADERS)
    assert response.status_code == 404, response.text


def test_v5_sentinel_findings_detail_missing_source_returns_degraded(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store(seed_incidents=False) as client:
        response = client.get("/bff/v5/sentinel/findings/any-finding", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json().get("data", {})
    assert data.get("status") == "degraded"


def test_v5_control_room_returns_200(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/control-room", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "loops" in payload
    assert "sentinel" in payload
    assert "interventions" in payload
    assert "meta" in payload


def test_v5_control_room_composes_loop_and_sentinel_read_models(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/control-room", headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    loop_ids = [item.get("id") for item in payload["loops"]["items"]]
    sentinel_ids = [item.get("id") for item in payload["sentinel"]["items"]]
    assert "inc-loop-1" in loop_ids
    assert "inc-sentinel-1" in sentinel_ids
    assert "inc-loop-1" not in sentinel_ids
    assert "inc-sentinel-1" not in loop_ids


def test_v5_persona_health_returns_200(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/execution/persona-health", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "items" in payload
    assert "meta" in payload


def test_v5_strategy_health_returns_200(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_store() as client:
        response = client.get("/bff/v5/execution/strategy-health", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "items" in payload
    assert "meta" in payload


def test_v5_loop_sentinel_routes_no_500_without_source(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    routes = [
        "/bff/v5/loop-runs",
        "/bff/v5/sentinel/findings",
        "/bff/v5/control-room",
        "/bff/v5/execution/persona-health",
        "/bff/v5/execution/strategy-health",
    ]
    with _v5_store(seed_incidents=False) as client:
        for path in routes:
            response = client.get(path, headers=HEADERS)
            assert response.status_code != 500, f"{path} returned 500: {response.text}"


# --- Regression: dedicated fallback store with data, incidents absent ---

@contextmanager
def _v5_fallback_store(
    *,
    loop_runs_data: Optional[dict] = None,
    sentinel_findings_data: Optional[dict] = None,
) -> Iterator[TestClient]:
    import json as _json
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as td:
        env_overrides: dict = {}
        if loop_runs_data is not None:
            lr_path = os.path.join(td, "loop_runs.json")
            with open(lr_path, "w") as f:
                _json.dump(loop_runs_data, f)
            env_overrides["PANTHEON_BFF_LOOP_RUN_STORE"] = lr_path
        if sentinel_findings_data is not None:
            sf_path = os.path.join(td, "sentinel_findings.json")
            with open(sf_path, "w") as f:
                _json.dump(sentinel_findings_data, f)
            env_overrides["PANTHEON_BFF_SENTINEL_FINDING_STORE"] = sf_path

        # No incidents seeded — snapshot path points to a non-existent file
        snapshot_path = os.path.join(td, "read_surfaces.json")
        original_store = bff_main.read_store
        store = ReadSurfaceStore(snapshot_path, allow_local_snapshot_fallback=False)
        bff_main.read_store = store
        with patch.dict(os.environ, env_overrides):
            try:
                yield TestClient(bff_main.app, raise_server_exceptions=False)
            finally:
                bff_main.read_store = original_store


_LOOP_RUNS_SEED = {
    "lr-001": {"id": "lr-001", "status": "active", "activePeriod": {"start": "2026-05-09T10:00:00Z", "end": None}},
}

_SENTINEL_FINDINGS_SEED = {
    "sf-001": {"id": "sf-001", "status": "active", "severity": "high"},
}


def test_v5_loop_runs_dedicated_fallback_store_source_not_missing(monkeypatch):
    """Regression: when PANTHEON_BFF_LOOP_RUN_STORE has data and incidents are absent,
    its records stay conclusive, but a legacy wrapper without controller truth is degraded."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_fallback_store(loop_runs_data=_LOOP_RUNS_SEED) as client:
        response = client.get("/bff/v5/loop-runs", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    surface = payload.get("meta", {}).get("surfaces", {}).get("loop_runs", {})
    assert surface.get("source") != "missing", f"source must not be 'missing', got: {surface}"
    assert surface.get("status") == "degraded", f"missing controller truth must degrade: {surface}"
    assert surface.get("truth_status") == "degraded"
    assert surface.get("projection_schema_version") is None
    assert len(payload.get("items", [])) == 1


def test_v5_sentinel_findings_dedicated_fallback_store_source_not_missing(monkeypatch):
    """Regression: when PANTHEON_BFF_SENTINEL_FINDING_STORE has data and incidents are absent,
    meta.surfaces.sentinel_findings.source must NOT be 'missing' and status must not be 'unavailable'."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_fallback_store(sentinel_findings_data=_SENTINEL_FINDINGS_SEED) as client:
        response = client.get("/bff/v5/sentinel/findings", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    surface = payload.get("meta", {}).get("surfaces", {}).get("sentinel_findings", {})
    assert surface.get("source") != "missing", f"source must not be 'missing', got: {surface}"
    assert surface.get("status") != "unavailable", f"status must not be 'unavailable', got: {surface}"
    assert len(payload.get("items", [])) == 1


def test_v5_control_room_partial_dedicated_fallback_surfaces_are_source_aware(monkeypatch):
    """Regression: control-room must not use a healthy sentinel fallback surface for missing loop runs."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_fallback_store(sentinel_findings_data=_SENTINEL_FINDINGS_SEED) as client:
        response = client.get("/bff/v5/control-room", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    loop_surface = payload.get("loops", {}).get("meta", {}).get("surfaces", {}).get("loop_runs", {})
    sentinel_surface = payload.get("sentinel", {}).get("meta", {}).get("surfaces", {}).get("sentinel_findings", {})
    control_surface = payload.get("meta", {}).get("surfaces", {}).get("control_room", {})

    assert payload.get("loops", {}).get("items") == []
    assert len(payload.get("sentinel", {}).get("items", [])) == 1
    assert loop_surface.get("source") == "missing", f"missing loop source must stay explicit, got: {loop_surface}"
    assert loop_surface.get("status") == "unavailable", f"missing loop source must be unavailable, got: {loop_surface}"
    assert sentinel_surface.get("source") != "missing", f"sentinel source must reflect fallback store, got: {sentinel_surface}"
    assert sentinel_surface.get("status") != "unavailable", f"sentinel source must stay available, got: {sentinel_surface}"
    assert control_surface.get("status") == "degraded", f"partial control-room data should be degraded, got: {control_surface}"


def test_v5_loop_runs_empty_incidents_source_not_missing(monkeypatch):
    """Regression: when incidents source is available but has zero records,
    meta.surfaces.loop_runs.source must NOT be 'missing' and items must be []."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    # Seed an empty incidents dict in snapshot
    import json as _json
    with tempfile.TemporaryDirectory() as td:
        snapshot_path = os.path.join(td, "read_surfaces.json")
        with open(snapshot_path, "w") as f:
            _json.dump({"incidents": {}}, f)
        original_store = bff_main.read_store
        store = ReadSurfaceStore(snapshot_path, allow_local_snapshot_fallback=True)
        bff_main.read_store = store
        try:
            client = TestClient(bff_main.app, raise_server_exceptions=False)
            response = client.get("/bff/v5/loop-runs", headers=HEADERS)
        finally:
            bff_main.read_store = original_store
    assert response.status_code == 200, response.text
    payload = response.json()
    surface = payload.get("meta", {}).get("surfaces", {}).get("loop_runs", {})
    assert surface.get("source") != "missing", f"source must not be 'missing', got: {surface}"
    assert payload.get("items") == [], "empty incidents should produce empty items list"


# ---------------------------------------------------------------------------
# L12-BFF-001 durable infrastructure-health controller
# ---------------------------------------------------------------------------


def _health_monitor(tmp_path: Path, **overrides) -> DownstreamHealthMonitor:
    defaults = {
        "telemetry_url": "http://telemetry:8083",
        "incidents_url": "http://incidents:8090",
        "telemetry_service_jwt": "service-jwt",
        "tenant_id": "tenant-alpha",
        "producer": "control-plane-bff",
        "state_path": str(tmp_path / "downstream-health.sqlite3"),
        "probe_interval_seconds": 60,
        "failure_threshold": 2,
        "delivery_max_attempts": 2,
        "delivery_backoff_seconds": 0.1,
    }
    defaults.update(overrides)
    return DownstreamHealthMonitor(**defaults)


def _probe(
    *,
    ok: bool,
    checked_at: str = "2026-07-27T18:40:00Z",
    consecutive_failures: int = 0,
    target_name: str = "telemetry",
) -> DownstreamProbeResult:
    return DownstreamProbeResult(
        target_name=target_name,
        ok=ok,
        status_code=200 if ok else 503,
        latency_ms=12.5,
        checked_at=checked_at,
        failure_reason=None if ok else "HTTP 503",
        consecutive_failures=consecutive_failures,
        window_started_at=checked_at,
    )


def test_l12_bff_strict_infrastructure_event_has_no_fake_runtime_binding(
    tmp_path,
):
    monitor = _health_monitor(tmp_path, incidents_url="")
    captured = []

    def accept(url, body, timeout):
        captured.append(
            {
                "url": url,
                "body": body,
                "headers": dict(health_module._POST_HEADERS.get()),
            }
        )
        return True, 202

    with patch.object(health_module, "_post_json", side_effect=accept):
        event_id = monitor._emit_telemetry_sync(_probe(ok=False))

    assert event_id and event_id.startswith("infra-health-")
    assert len(captured) == 1
    request = captured[0]
    assert request["url"].endswith("/api/v1/telemetry/infrastructure-health")
    assert request["headers"]["Authorization"] == "Bearer service-jwt"
    assert request["headers"]["X-Tenant-Id"] == "tenant-alpha"
    event = request["body"]
    assert event["schema_version"] == "pantheon.infrastructure-health/1"
    assert event["event_type"] == "infrastructure_health"
    assert event["component"]["service_name"] == "telemetry"
    assert event["observation"]["probe_kind"] == "interval_probe"
    forbidden = {
        "binding_id",
        "runtime_id",
        "capital_pool_id",
        "artifact_id",
        "artifact_version",
        "deployment_stage",
        "execution_mode",
        "plan_id",
        "persona_capital_binding_id",
    }
    assert forbidden.isdisjoint(event)

    schema_document = json.loads(
        (Path(__file__).parents[2] / "telemetry" / "telemetry_event.schema.json")
        .read_text(encoding="utf-8")
    )
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(
        event,
        schema=schema_document["definitions"]["InfrastructureHealthEvent"],
    )


def test_l12_bff_monitor_event_is_admitted_by_real_strict_telemetry_route(
    tmp_path,
):
    secret = "l12-bff-strict-route-secret"
    tenant = "tenant-alpha"
    producer = "control-plane-bff"
    token = encode_jwt_hs256(
        {
            "sub": "bff-health-probe",
            "roles": ["service"],
            "allowed_tenants": [tenant],
            "allowed_producers": [producer],
        },
        secret=secret,
    )
    storage_dir = tmp_path / "telemetry"
    storage_dir.mkdir()
    loop = asyncio.new_event_loop()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    service = TelemetryIngestService(
        schema_path=str(
            Path(__file__).parents[2] / "telemetry" / "telemetry_event.schema.json"
        ),
        storage_dir=str(storage_dir),
        buffer=_DurableFileBroker(str(storage_dir / "broker.jsonl")),
        batch_size=10,
        batch_interval=60,
        runtime_summary_store=RuntimeSummaryProjectionStore(
            heartbeat_stale_after_seconds=10_000_000_000
        ),
    )
    asyncio.run_coroutine_threadsafe(service.start(), loop).result(timeout=10)
    original_loop = telemetry_main._loop
    original_service = telemetry_main._svc
    telemetry_main._loop = loop
    telemetry_main._svc = service
    client = telemetry_main.app.test_client()
    monitor = _health_monitor(
        tmp_path,
        incidents_url="",
        telemetry_service_jwt=token,
        tenant_id=tenant,
        producer=producer,
    )

    def real_route(url, body, timeout):
        response = client.post(
            "/api/v1/telemetry/infrastructure-health",
            json=body,
            headers=health_module._POST_HEADERS.get(),
        )
        return 200 <= response.status_code < 300, response.status_code

    strict_env = {
        "PANTHEON_TELEMETRY_AUTH_MODE": "strict",
        "PANTHEON_TELEMETRY_JWT_SECRET": secret,
        "PANTHEON_TELEMETRY_INFRA_PRODUCERS": producer,
    }
    try:
        with patch.dict(os.environ, strict_env, clear=False):
            with patch.object(health_module, "_post_json", side_effect=real_route):
                event_id = monitor._emit_telemetry_sync(_probe(ok=False))
        assert service._infrastructure_health_ledger.is_committed(event_id)
        [delivery] = monitor.list_delivery_records(event_id=event_id)
        assert delivery["status"] == "delivered"
        assert delivery["body"]["tenant_id"] == tenant
        assert "binding_id" not in delivery["body"]
    finally:
        asyncio.run_coroutine_threadsafe(
            service.stop(graceful=True),
            loop,
        ).result(timeout=10)
        telemetry_main._svc = original_service
        telemetry_main._loop = original_loop
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


def test_l12_bff_stable_event_id_dedupes_restart_and_two_replicas(
    tmp_path,
):
    state_path = str(tmp_path / "shared.sqlite3")
    replica_a = _health_monitor(
        tmp_path,
        state_path=state_path,
        instance_id="replica-a",
        incidents_url="",
    )
    replica_b = _health_monitor(
        tmp_path,
        state_path=state_path,
        instance_id="replica-b",
        incidents_url="",
    )
    result = _probe(ok=False, consecutive_failures=1)
    posted = []

    def accept(url, body, timeout):
        posted.append(body["event_id"])
        return True, 202

    with patch.object(health_module, "_post_json", side_effect=accept):
        first = replica_a._emit_telemetry_sync(result)
        duplicate = replica_b._emit_telemetry_sync(
            _probe(ok=False, consecutive_failures=1)
        )

    assert first == duplicate
    assert posted == [first]
    assert len(replica_b.list_delivery_records(event_id=first)) == 1

    asyncio.run(replica_a._handle_probe_result(result))
    restarted = _health_monitor(
        tmp_path,
        state_path=state_path,
        instance_id="restarted",
        incidents_url="",
    )
    state = restarted.get_state()
    assert state["targets"]["telemetry"]["ok"] is False
    assert state["durability"]["shared_replica_state"] is True


def test_l12_bff_delivery_409_enters_dlq_and_replays_same_event(
    tmp_path,
):
    monitor = _health_monitor(tmp_path, incidents_url="")
    result = _probe(ok=False)

    with patch.object(health_module, "_post_json", return_value=(False, 409)):
        event_id = monitor._emit_telemetry_sync(result)

    [dead] = monitor.list_delivery_records(event_id=event_id)
    assert dead["status"] == "dead_letter"
    assert dead["body"]["event_id"] == event_id

    replayed_bodies = []

    def accept(url, body, timeout):
        replayed_bodies.append(body)
        return True, 202

    with patch.object(health_module, "_post_json", side_effect=accept):
        replay = monitor.replay_dead_letters(
            actor_id="operator-test",
            approval_ref="approval://l12-bff-001/replay",
            reason="retry the exact reviewed infrastructure event",
            event_id=event_id,
        )

    assert replay["replayed"] == 1
    assert replay["delivered_now"] == 1
    assert replayed_bodies[0]["event_id"] == event_id
    [delivered] = monitor.list_delivery_records(event_id=event_id)
    assert delivered["status"] == "delivered"


def test_l12_bff_replay_route_requires_mfa_approval_and_audits_actor(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monitor = _health_monitor(tmp_path, incidents_url="")
    with patch.object(health_module, "_post_json", return_value=(False, 409)):
        event_id = monitor._emit_telemetry_sync(_probe(ok=False))
    original = bff_main.downstream_health_monitor
    bff_main.downstream_health_monitor = monitor
    try:
        client = TestClient(bff_main.app, raise_server_exceptions=False)
        no_mfa_headers = {"Authorization": "Bearer op-execute-plans:operator,reviewer,admin:tenant-dev"}
        unauthorized = client.post(
            "/bff/v5/downstream-health/dlq/replay",
            headers=no_mfa_headers,
            json={"event_id": event_id},
        )
        assert unauthorized.status_code == 403

        missing_approval = client.post(
            "/bff/v5/downstream-health/dlq/replay",
            headers=HEADERS,
            json={"event_id": event_id},
        )
        assert missing_approval.status_code == 422

        with patch.object(health_module, "_post_json", return_value=(True, 202)):
            response = client.post(
                "/bff/v5/downstream-health/dlq/replay",
                headers=HEADERS,
                json={
                    "event_id": event_id,
                    "approval_ref": "approval://l12-bff-001/route-replay",
                    "reason": "operator reviewed the exact dead letter",
                },
            )
    finally:
        bff_main.downstream_health_monitor = original

    assert response.status_code == 200, response.text
    assert response.json()["data"]["replayed"] == 1
    [audit] = monitor._store.list_replay_audit()
    assert audit["actor_id"] == "op-execute-plans"
    assert audit["approval_ref"] == "approval://l12-bff-001/route-replay"
    assert audit["replayed_count"] == 1


def test_l12_bff_complete_registry_and_error_rate_spike(
    tmp_path,
    monkeypatch,
):
    expected_targets = set()
    for index, (name, env_names) in enumerate(
        health_module._DEFAULT_TARGET_SPECS,
        start=1,
    ):
        expected_targets.add(name)
        monkeypatch.setenv(
            env_names[0],
            f"http://127.0.0.1:{20_000 + index}",
        )
    monkeypatch.setenv("PANTHEON_NEW_OWNER_API_URL", "http://127.0.0.1:29999")
    expected_targets.add("new-owner")
    monitor = _health_monitor(
        tmp_path,
        incidents_url="",
        error_rate_window_seconds=300,
        error_rate_threshold=2 / 3,
        error_rate_min_samples=3,
    )
    registry = monitor._resolve_target_registry()
    assert expected_targets.issubset(registry)
    assert len({target.base_url for target in registry.values()}) == len(registry)

    posted = []

    def accept(url, body, timeout):
        posted.append(body)
        return True, 202

    with patch.object(health_module, "_post_json", side_effect=accept):
        assert monitor.record_downstream_outcome(
            target_name="source-ingest",
            ok=True,
            status_code=200,
            observed_at="2026-07-27T18:40:01Z",
        ) is None
        assert monitor.record_downstream_outcome(
            target_name="source-ingest",
            ok=False,
            status_code=503,
            detail="first failure",
            observed_at="2026-07-27T18:40:02Z",
        ) is None
        threshold = monitor.record_downstream_outcome(
            target_name="source-ingest",
            ok=False,
            status_code=503,
            detail="spike",
            observed_at="2026-07-27T18:40:03Z",
        )

    assert threshold is not None
    error_events = [
        body
        for body in posted
        if body.get("observation", {}).get("probe_kind") == "error_rate"
    ]
    assert len(error_events) == 1
    assert error_events[0]["observation"]["sample_count"] == 3
    assert error_events[0]["observation"]["failure_count"] == 2
    assert error_events[0]["observation"]["error_rate"] == pytest.approx(2 / 3)


def test_l12_bff_real_target_stop_and_recovery_resolves_durable_mapping(
    tmp_path,
    monkeypatch,
):
    class ReusableServer(http.server.ThreadingHTTPServer):
        allow_reuse_address = True

    class HealthyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, format, *args):
            return

    server = ReusableServer(("127.0.0.1", 0), HealthyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "PANTHEON_BFF_HEALTH_TARGETS_JSON",
        json.dumps(
            {
                "real-target": {
                    "url": f"http://127.0.0.1:{port}",
                    "health_path": "/",
                }
            }
        ),
    )
    monitor = _health_monitor(
        tmp_path,
        failure_threshold=1,
        http_timeout=0.3,
        delivery_max_attempts=1,
    )
    posted_channels = []

    def accept(url, body, timeout):
        posted_channels.append(
            (
                "telemetry"
                if url.endswith("/api/v1/telemetry/infrastructure-health")
                else "incident"
            )
        )
        return True, 202

    try:
        with patch.object(health_module, "_post_json", side_effect=accept):
            healthy = asyncio.run(
                monitor._probe_one(
                    "real-target",
                    f"http://127.0.0.1:{port}",
                    health_path="/",
                    checked_at="2026-07-27T18:40:00Z",
                    window_started_at="2026-07-27T18:40:00Z",
                )
            )
            asyncio.run(monitor._handle_probe_result(healthy))
            assert healthy.ok is True

            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            stopped = asyncio.run(
                monitor._probe_one(
                    "real-target",
                    f"http://127.0.0.1:{port}",
                    health_path="/",
                    checked_at="2026-07-27T18:41:00Z",
                    window_started_at="2026-07-27T18:41:00Z",
                )
            )
            assert stopped.ok is False
            asyncio.run(monitor._handle_probe_result(stopped))
            assert monitor.get_state()["incidents"]["real-target"]["status"] == "open"

            server = ReusableServer(("127.0.0.1", port), HealthyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            recovered = asyncio.run(
                monitor._probe_one(
                    "real-target",
                    f"http://127.0.0.1:{port}",
                    health_path="/",
                    checked_at="2026-07-27T18:42:00Z",
                    window_started_at="2026-07-27T18:42:00Z",
                )
            )
            assert recovered.ok is True
            asyncio.run(monitor._handle_probe_result(recovered))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    final_state = monitor.get_state()
    assert final_state["targets"]["real-target"]["ok"] is True
    assert final_state["incidents"]["real-target"]["status"] == "resolved"
    assert posted_channels.count("telemetry") == 3
    assert posted_channels.count("incident") == 2


def test_l12_bff_incident_mapping_survives_failure_restart_and_recovery(
    tmp_path,
):
    state_path = str(tmp_path / "incident-shared.sqlite3")
    monitor = _health_monitor(
        tmp_path,
        state_path=state_path,
        failure_threshold=1,
        delivery_max_attempts=1,
    )

    def fail_incident_create(url, body, timeout):
        if url.endswith("/api/v1/telemetry/infrastructure-health"):
            return True, 202
        if url.endswith("/api/incidents/consume-infrastructure-health"):
            return False, 503
        return True, 200

    with patch.object(
        health_module,
        "_post_json",
        side_effect=fail_incident_create,
    ):
        asyncio.run(
            monitor._handle_probe_result(
                _probe(ok=False, consecutive_failures=1)
            )
        )

    incident = monitor.get_state()["incidents"]["telemetry"]
    opening_event_id = incident["opening_event_id"]
    incident_id = incident["incident_id"]
    assert incident["status"] == "opening"
    opening_deliveries = monitor.list_delivery_records(event_id=opening_event_id)
    assert {item["status"] for item in opening_deliveries} == {
        "delivered",
        "dead_letter",
    }

    restarted = _health_monitor(
        tmp_path,
        state_path=state_path,
        instance_id="replica-after-restart",
        failure_threshold=1,
        delivery_max_attempts=1,
    )
    assert restarted.get_state()["incidents"]["telemetry"]["incident_id"] == incident_id

    with patch.object(health_module, "_post_json", return_value=(True, 202)):
        asyncio.run(
            restarted._handle_probe_result(
                _probe(ok=True, checked_at="2026-07-27T18:41:00Z")
            )
        )

    # Recovery stays pending because its durable dependency (incident create)
    # is still dead-lettered; the mapping is not falsely cleared.
    assert restarted.get_state()["incidents"]["telemetry"]["status"] == "resolving"
    assert "telemetry" in restarted._open_incident_ids

    with patch.object(health_module, "_post_json", return_value=(True, 202)):
        replay = restarted.replay_dead_letters(
            actor_id="operator-test",
            approval_ref="approval://l12-bff-001/recovery",
            reason="recover the exact dead-lettered incident create",
            event_id=opening_event_id,
        )
        assert replay["replayed"] == 1
        restarted._deliver_due_sync()

    resolved = restarted.get_state()["incidents"]["telemetry"]
    assert resolved["status"] == "resolved"
    assert resolved["incident_id"] == incident_id
    assert "telemetry" not in restarted._open_incident_ids


def test_l12_bff_recovery_survives_delivered_history_retention(
    tmp_path,
):
    state_path = str(tmp_path / "incident-retention.sqlite3")
    monitor = _health_monitor(
        tmp_path,
        state_path=state_path,
        failure_threshold=1,
        delivery_max_attempts=1,
    )

    with patch.object(health_module, "_post_json", return_value=(True, 202)):
        asyncio.run(
            monitor._handle_probe_result(
                _probe(ok=False, consecutive_failures=1)
            )
        )

    incident = monitor.get_state()["incidents"]["telemetry"]
    opening_delivery_id = incident["create_delivery_id"]
    assert incident["status"] == "open"
    with monitor._store._connect() as connection:
        connection.execute(
            """
            UPDATE delivery_outbox
            SET updated_at='2026-01-01T00:00:00Z'
            WHERE status='delivered'
            """
        )

    pruned = monitor._store.prune_retained_history(
        delivery_history_seconds=60,
    )
    retained_ids = {
        item["delivery_id"] for item in monitor.list_delivery_records()
    }
    assert pruned["delivery_outbox"] == 1
    assert opening_delivery_id in retained_ids

    restarted = _health_monitor(
        tmp_path,
        state_path=state_path,
        instance_id="replica-after-retention",
        failure_threshold=1,
        delivery_max_attempts=1,
    )
    with patch.object(health_module, "_post_json", return_value=(True, 202)):
        asyncio.run(
            restarted._handle_probe_result(
                _probe(ok=True, checked_at="2026-07-27T18:41:00Z")
            )
        )

    resolved = restarted.get_state()["incidents"]["telemetry"]
    assert resolved["status"] == "resolved"
    assert resolved["incident_id"] == incident["incident_id"]
    assert restarted.get_state()["delivery"]["backlog"] == 0


def test_command_executor_post_and_get_json_integration_with_downstream_monitor(tmp_path, monkeypatch):
    import command_executor
    target_url = "http://127.0.0.1:28097"
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", target_url)
    monitor = DownstreamHealthMonitor(
        state_path=str(tmp_path / "test_downstream.sqlite3"),
        incidents_url="",
        error_rate_window_seconds=300,
        error_rate_threshold=0.5,
        error_rate_min_samples=1,
    )
    bff_main.downstream_health_monitor = monitor

    class MockResponse:
        def __init__(self, status=200, body=b'{"status":"ok"}'):
            self.status = status
            self._body = body
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    def mock_urlopen(req, timeout=None):
        if req.full_url.startswith(target_url):
            return MockResponse(200, b'{"result":"success"}')
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Error", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    res_post = command_executor._post_json(f"{target_url}/api/ingest", {"test": 1})
    assert res_post == {"result": "success"}

    res_get = command_executor._get_json(f"{target_url}/api/status")
    assert res_get == {"result": "success"}

    state = monitor.get_state()
    target_state = state["targets"].get("source-ingest")
    assert target_state is not None
    assert target_state["ok"] is True


def test_worker_functional_health_probing_and_paper_signal_producer_attribution(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    health_file = tmp_path / "paper-signal-producer-health.json"
    health_payload = {
        "worker_name": "paper-signal-producer",
        "status": "degraded",
        "ready": False,
        "ok": False,
        "reason": "artifact_store_missing",
        "ticks": 5,
    }
    health_file.write_text(json.dumps(health_payload), encoding="utf-8")
    monkeypatch.setenv("PAPER_PRODUCER_HEALTH_FILE", str(health_file))

    monitor = DownstreamHealthMonitor(
        state_path=str(tmp_path / "downstream_worker.sqlite3"),
        incidents_url="",
    )
    bff_main.downstream_health_monitor = monitor

    asyncio.run(monitor._probe_all())
    state = monitor.get_state()
    target_info = state["targets"].get("paper-signal-producer")
    assert target_info is not None
    assert target_info["ok"] is False
    assert "artifact_store_missing" in str(target_info.get("failure_reason"))

    with _v5_store(seed_incidents=False) as client:
        response = client.get("/bff/v5/loop-health", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    items = payload.get("items", [])
    capital_loop = next((item for item in items if item.get("loop_id") == "capital_pool_execution"), None)
    assert capital_loop is not None
    downstream_state = capital_loop.get("downstream_actual_state", {})
    assert downstream_state.get("status") == "degraded"
    assert "artifact_store_missing" in str(downstream_state.get("summary"))


def test_loop_12_controller_truth_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monitor = DownstreamHealthMonitor(
        state_path=str(tmp_path / "downstream_loop12.sqlite3"),
        incidents_url="",
    )
    bff_main.downstream_health_monitor = monitor

    record = monitor.publish_loop_12_controller_truth()
    assert record["loop_id"] == "bff_health_monitoring"
    assert record["controller_name"] == "bff_downstream_health_monitor"
    assert record["truth_level"] == "reconciled_live_proof"
    assert record["status"] in {"ready", "degraded"}
    assert "downstream_actual_state" in record

    with _v5_store(seed_incidents=False) as client:
        response = client.get("/bff/v5/loop-health/bff_health_monitoring", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json().get("data", {})
    assert data.get("loop_id") == "bff_health_monitoring"
    assert data.get("read_model") == "loop_health"



"""Focused BFF contract tests for v5 loop/sentinel/health/control-room routes (SEM-004)."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import pytest
from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore, ServiceBackedReadAdapter  # noqa: E402

HEADERS = {"Authorization": "Bearer op-execute-plans:operator,reviewer,admin:mfa"}

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
    items = response.json()["items"]
    ids = [item.get("id") for item in items]
    assert "inc-loop-1" in ids
    # sentinel incident is excluded from loop runs
    assert "inc-sentinel-1" not in ids


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
    meta.surfaces.loop_runs.source must NOT be 'missing' and status must not be 'unavailable'."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _v5_fallback_store(loop_runs_data=_LOOP_RUNS_SEED) as client:
        response = client.get("/bff/v5/loop-runs", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    surface = payload.get("meta", {}).get("surfaces", {}).get("loop_runs", {})
    assert surface.get("source") != "missing", f"source must not be 'missing', got: {surface}"
    assert surface.get("status") != "unavailable", f"status must not be 'unavailable', got: {surface}"
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

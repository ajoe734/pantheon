"""Focused BFF contract tests for v5 loop/sentinel/health/control-room routes (SEM-004)."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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

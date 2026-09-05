"""MGMT-LOAD-002 contract tests for management shell summary and jobs routes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient
from starlette.routing import Route

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import ReadSurfacePorts, create_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-mgmt-load-002:operator,admin:mfa"}


@contextmanager
def _isolated_bff(monkeypatch) -> Iterator[tuple[TestClient, ReadSurfacePorts]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_read_surface_ports()
        bff_main.read_store = store
        bff_main._SHELL_SUMMARY_COUNT_CACHE.clear()
        bff_main._GOV_BFF_JOB_OVERLAY.clear()
        bff_main._ACKNOWLEDGED_ALERTS.clear()
        bff_main.app.openapi_schema = None

        original_dataset_source = store.dataset_source

        def dataset_source(dataset: str, *args, **kwargs) -> str:
            if dataset in {
                "approval_queue_items",
                "governance_review_queue_items",
                "incidents",
                "jobs",
                "kill_switch",
            }:
                return "service_store"
            return original_dataset_source(dataset, *args, **kwargs)

        monkeypatch.setattr(store, "dataset_source", dataset_source)
        monkeypatch.setattr(
            store,
            "list_approval_queue_items",
            lambda: [
                {"decision_id": "approval-pending", "decision_state": "pending"},
                {"decision_id": "approval-approved", "decision_state": "approved"},
            ],
        )
        monkeypatch.setattr(
            store,
            "list_governance_review_queue_items",
            lambda: [
                {"item_id": "review-pending", "status": "pending"},
                {"item_id": "review-done", "status": "done"},
            ],
        )
        monkeypatch.setattr(
            store,
            "list_incidents",
            lambda: [
                {"incident_id": "incident-open", "status": "open"},
                {"incident_id": "incident-closed", "status": "closed"},
            ],
        )
        monkeypatch.setattr(
            store,
            "get_kill_switch_status",
            lambda: {"status": "armed", "safe_mode_status": "off", "active": False},
        )
        monkeypatch.setattr(
            store,
            "list_jobs_bff",
            lambda status=None, job_type=None: [
                {"job_id": "job-running", "status": "running"},
                {"job_id": "job-queued", "status": "queued"},
                {"job_id": "job-complete", "status": "completed"},
            ],
        )

        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False), store
        finally:
            bff_main.read_store = original_store
            bff_main._SHELL_SUMMARY_COUNT_CACHE.clear()
            bff_main._GOV_BFF_JOB_OVERLAY.clear()
            bff_main._ACKNOWLEDGED_ALERTS.clear()
            bff_main.app.openapi_schema = None


def test_shell_summary_returns_counts_without_full_lists(monkeypatch) -> None:
    def fail_full_alert_builder(snapshot_at: str) -> dict:
        raise AssertionError("_build_operator_alerts_payload must not be used for shell-summary counts")

    monkeypatch.setattr(bff_main, "_build_operator_alerts_payload", fail_full_alert_builder)

    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/shell-summary", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["counts"] == {
        "pending_approvals": 1,
        "open_alerts": 3,
        "running_jobs": 2,
    }
    assert "items" not in payload
    assert "approvals" not in payload["data"]
    assert "alerts" not in payload["data"]
    assert "jobs" not in payload["data"]
    assert payload["meta"]["surfaces"]["shell_summary"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["open_alerts"]["source"] == "bff_cheap_count"


def test_shell_summary_redacts_session_and_exposes_transport(monkeypatch) -> None:
    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/shell-summary", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    session = data["session"]
    assert session["operator_id"] == "op-mgmt-load-002"
    assert session["display_label"] == "op-mgmt-load-002"
    assert "admin" in session["roles"]
    assert "capabilities" not in session
    assert "token" not in json.dumps(session).lower()
    assert data["transport"] == {
        "bff_status": "ok",
        "service": "operator-bff",
        "api_version": bff_main.app.version,
    }


def test_shell_summary_surfaces_degraded_count_state(monkeypatch) -> None:
    monkeypatch.setenv("BFF_READ_SURFACE_STATE", "degraded")
    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/shell-summary", headers=HEADERS)

    assert response.status_code == 200, response.text
    surfaces = response.json()["meta"]["surfaces"]
    assert surfaces["shell_summary"]["status"] == "degraded"
    assert surfaces["pending_approvals"]["status"] == "degraded"
    assert surfaces["open_alerts"]["status"] == "degraded"
    assert surfaces["running_jobs"]["status"] == "degraded"
    assert surfaces["shell_summary"]["freshness"]["ttl_seconds"] >= 0


def test_shell_summary_is_registered_in_openapi(monkeypatch) -> None:
    with _isolated_bff(monkeypatch):
        schema = bff_main.app.openapi()

    assert "/bff/management/shell-summary" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/shell-summary"]


def test_jobs_route_has_one_canonical_get_handler(monkeypatch) -> None:
    # ACG-01-002: the handler now lives in jobs/router.py (app.include_router),
    # so it is a Starlette _IncludedRouter node rather than a flat Route in
    # bff_main.app.routes. Assert via the compiled OpenAPI schema instead,
    # which is what actually governs duplicate-route detection for clients.
    with _isolated_bff(monkeypatch):
        schema = bff_main.app.openapi()
    operations = schema["paths"]["/bff/jobs"]
    assert list(operations.keys()) == ["get"]

    source = Path(bff_main.__file__).read_text()
    assert source.count('@app.get("/bff/jobs")') == 0

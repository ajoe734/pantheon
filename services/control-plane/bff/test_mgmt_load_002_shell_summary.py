"""MGMT-LOAD-002 contract tests for management shell summary and jobs routes."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")

from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.management_read_models.service import _SHELL_SUMMARY_COUNT_CACHE
from services.control_plane.bff.ports import ReadSurfacePorts, create_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-mgmt-load-002:operator,admin:mfa"}


@contextmanager
def _isolated_bff(monkeypatch) -> Iterator[tuple[TestClient, ReadSurfacePorts]]:
    store = create_read_surface_ports()
    _SHELL_SUMMARY_COUNT_CACHE.clear()

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

    app = FastAPI(title="Management router contract", version="0.1.0")
    app.include_router(create_management_router(read_surface=store))
    try:
        yield TestClient(app, raise_server_exceptions=False), store
    finally:
        _SHELL_SUMMARY_COUNT_CACHE.clear()


def test_shell_summary_returns_counts_without_full_lists(monkeypatch) -> None:
    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/shell-summary", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["counts"] == {
        "pending_approvals": 2,
        "open_alerts": 1,
        "running_jobs": 1,
    }
    assert "items" not in payload
    assert "approvals" not in payload["data"]
    assert "alerts" not in payload["data"]
    assert "jobs" not in payload["data"]
    assert payload["meta"]["surfaces"]["shell_summary"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["incident_alerts"]["source"] == "store"


def test_shell_summary_redacts_session_and_exposes_transport(monkeypatch) -> None:
    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/shell-summary", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    session = data["session"]
    assert session["operator_id"] == "op-mgmt-load-002"
    assert session["displayLabel"] == "op-mgmt-load-002"
    assert "admin" in session["roles"]
    assert "capabilities" not in session
    assert "token" not in json.dumps(session).lower()
    assert data["transport"] == {
        "bff_status": "ok",
        "service": "operator-bff",
        "api_version": "0.2.0",
    }


def test_shell_summary_reports_injected_store_surfaces(monkeypatch) -> None:
    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/shell-summary", headers=HEADERS)

    assert response.status_code == 200, response.text
    surfaces = response.json()["meta"]["surfaces"]
    assert surfaces["shell_summary"]["status"] == "ok"
    assert surfaces["governance_approvals"]["source"] == "store"
    assert surfaces["incident_alerts"]["source"] == "store"
    assert surfaces["jobs_read_model"]["source"] == "store"


def test_shell_summary_is_registered_in_openapi(monkeypatch) -> None:
    with _isolated_bff(monkeypatch) as (client, _store):
        schema = client.get("/openapi.json").json()

    assert "/bff/management/shell-summary" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/shell-summary"]


def test_jobs_route_has_one_canonical_get_handler(monkeypatch) -> None:
    # The full-app route-uniqueness suite owns runtime duplicate detection.
    # This router-layer test only verifies that composition no longer declares
    # a competing legacy handler for the canonical Jobs router.
    source = (Path(__file__).resolve().parent / "main.py").read_text()
    assert source.count('@app.get("/bff/jobs")') == 0

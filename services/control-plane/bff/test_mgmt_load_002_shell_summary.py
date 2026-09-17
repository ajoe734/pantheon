"""MGMT-LOAD-002 contract tests for management shell summary and jobs routes."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.governance.router import _default_dataset_surface_status
from services.control_plane.bff.incidents.router import (
    _default_page_slice,
    _default_raise_if_read_surface_unavailable,
    _default_read_surface_meta,
)
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.management_read_models.router import (
    _default_bff_error,
    _default_extract_identity,
    _default_require_read_role,
    _default_snapshot_meta,
    _utc_now_rfc3339,
    create_management_router,
)
from services.control_plane.bff.management_read_models.service import _SHELL_SUMMARY_COUNT_CACHE
from services.control_plane.bff.ports import ReadSurfacePorts, create_read_surface_ports


HEADERS = {"Authorization": "Bearer op-mgmt-load-002:operator,admin:mfa"}

_MAIN_PY_PATH = Path(__file__).resolve().parent / "main.py"


def _build_app(store) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=_utc_now_rfc3339,
            bff_error=_default_bff_error,
        )
    )
    app.include_router(
        create_jobs_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=_default_bff_error,
            utc_now=_utc_now_rfc3339,
            page_slice=_default_page_slice,
            read_surface_meta=_default_read_surface_meta,
            dataset_surface_status=_default_dataset_surface_status,
            raise_if_read_surface_unavailable=_default_raise_if_read_surface_unavailable,
            reject_body_idempotency_key=lambda payload: None,
            resolve_final_idempotency_key=lambda header_key, body_key: header_key or body_key or "idem-key",
            submit_job_action=lambda job_id, action_id, resolved_key, identity, payload: {},
        )
    )
    return app


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

    app = _build_app(store)
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
        "api_version": "0.2.0",
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
    with _isolated_bff(monkeypatch) as (client, _store):
        schema = client.app.openapi()

    assert "/bff/management/shell-summary" in schema["paths"]
    assert "get" in schema["paths"]["/bff/management/shell-summary"]


def test_jobs_route_has_one_canonical_get_handler(monkeypatch) -> None:
    # ACG-01-002: the handler now lives in jobs/router.py (app.include_router),
    # so it is a Starlette _IncludedRouter node rather than a flat Route in
    # bff_main.app.routes. Assert via the compiled OpenAPI schema instead,
    # which is what actually governs duplicate-route detection for clients.
    with _isolated_bff(monkeypatch) as (client, _store):
        schema = client.app.openapi()
    operations = schema["paths"]["/bff/jobs"]
    assert list(operations.keys()) == ["get"]

    source = _MAIN_PY_PATH.read_text()
    assert source.count('@app.get("/bff/jobs")') == 0

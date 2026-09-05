from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports, create_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer inc001-operator:operator"}

_INCIDENT_CASE_EVIDENCE_FIELDS = (
    "binding_id",
    "deployment_stage",
    "deployment_plan_id",
    "capital_pool_id",
    "persona_capital_binding_id",
    "artifact_id",
    "artifact_version",
    "runtime_id",
    "trace_id",
)


@contextmanager
def _isolated_incident_bff(
    incidents: list[dict[str, Any]] | None,
) -> Iterator[TestClient]:
    if incidents is not None:
        store = create_in_memory_read_surface_ports(
            lifecycle_telemetry_governance_kwargs={
                "incidents": {i["incident_id"]: i for i in incidents},
            }
        )
        store.dataset_source = lambda ds: "service_store" if ds == "incidents" else "typed_store"
    else:
        store = create_in_memory_read_surface_ports()
        store.dataset_source = lambda ds: "missing" if ds == "incidents" else "typed_store"

    app = FastAPI(title="Incident router contract")

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(req, exc):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        elif isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "ERROR", "message": str(exc.detail)}},
        )

    def _extract_id(auth: str | None = None, **kwargs: Any) -> OperatorIdentity:
        return OperatorIdentity(operator_id="inc001-operator", roles=["operator", "viewer"])

    router = create_incident_router(
        read_surface=store,
        extract_identity=_extract_id,
        incident_overlay={},
        idempotency_ledger={},
    )
    app.include_router(router)
    yield TestClient(app, raise_server_exceptions=False)


def _incident_records() -> list[dict[str, Any]]:
    return [
        {
            "incident_id": "inc-inc001-live",
            "title": "Paper runtime threshold breach",
            "severity": "high",
            "status": "open",
            "created_at": "2026-05-16T06:40:00Z",
            "binding_id": "rb-inc001-paper",
            "deployment_stage": "paper",
            "deployment_plan_id": "plan-inc001-paper",
            "capital_pool_id": "pool-inc001",
            "persona_capital_binding_id": "pcb-inc001",
            "artifact_id": "artifact-inc001",
            "artifact_version": "2026.05.16",
            "runtime_id": "runtime-inc001-paper",
            "trace_id": "trace-inc001-threshold",
            "telemetry_event_ids": ["tel-inc001-threshold"],
            "evidence_summary": "Runtime heartbeat breached the paper drawdown threshold.",
            "lineage_ref": "artifact-inc001@2026.05.16",
        },
        {
            "incident_id": "inc-inc001-resolved",
            "title": "Resolved paper latency warning",
            "severity": "medium",
            "status": "resolved",
            "created_at": "2026-05-16T05:10:00Z",
            "resolved_at": "2026-05-16T05:30:00Z",
            "binding_id": "rb-inc001-paper",
            "deployment_stage": "paper",
            "deployment_plan_id": "plan-inc001-paper",
            "capital_pool_id": "pool-inc001",
            "persona_capital_binding_id": "pcb-inc001",
            "artifact_id": "artifact-inc001",
            "artifact_version": "2026.05.16",
            "runtime_id": "runtime-inc001-paper",
            "trace_id": "trace-inc001-latency",
        },
    ]


def test_bff_incidents_returns_live_incident_case_records_with_evidence_fields() -> None:
    with _isolated_incident_bff(_incident_records()) as client:
        response = client.get(
            "/bff/incidents?status=open&affected_pool_id=pool-inc001&page_size=1",
            headers=HEADERS,
        )
        detail = client.get("/bff/incidents/inc-inc001-live", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"] == payload["items"]
    assert payload["page_info"] == {"next_page_token": None, "total": 1}
    assert payload["meta"]["surfaces"]["incidents"]["source"] == "service_store"
    assert payload["meta"]["surfaces"]["incidents"]["status"] == "ok"

    item = payload["items"][0]
    assert item["incident_id"] == "inc-inc001-live"
    assert item["lineage_ref"] == "artifact-inc001@2026.05.16"
    for field in _INCIDENT_CASE_EVIDENCE_FIELDS:
        assert item[field], f"missing IncidentCase evidence field: {field}"

    assert detail.status_code == 200, detail.text
    detail_payload = detail.json()
    assert detail_payload["data"]["incident_id"] == "inc-inc001-live"
    assert detail_payload["data"]["deployment_plan_id"] == "plan-inc001-paper"
    assert detail_payload["meta"]["surfaces"]["incident"]["source"] == "service_store"


def test_bff_incident_create_preserves_canonical_incident_case_fields() -> None:
    created_incident = {
        "incident_id": "inc-inc001-created",
        "title": "Threshold breach opened IncidentCase",
        "severity": "critical",
        "status": "open",
        "binding_id": "rb-inc001-created",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-inc001-created",
        "capital_pool_id": "pool-inc001",
        "persona_capital_binding_id": "pcb-inc001-created",
        "artifact_id": "artifact-inc001-created",
        "artifact_version": "2026.05.16",
        "runtime_id": "runtime-inc001-created",
        "trace_id": "trace-inc001-created",
        "telemetry_event_ids": ["tel-inc001-created"],
        "evidence_summary": "Threshold breach opened an incident case.",
    }
    with _isolated_incident_bff(_incident_records()) as client:
        response = client.post(
            "/bff/incidents",
            json=created_incident,
            headers={**HEADERS, "Idempotency-Key": "inc001-create"},
        )
        replay = client.post(
            "/bff/incidents",
            json=created_incident,
            headers={**HEADERS, "Idempotency-Key": "inc001-create"},
        )

    assert response.status_code == 201, response.text
    assert replay.status_code == 201, replay.text
    payload = response.json()
    assert replay.json() == payload
    for field in _INCIDENT_CASE_EVIDENCE_FIELDS:
        assert payload[field] == created_incident[field]
    assert payload["lineage_ref"] == "artifact-inc001-created@2026.05.16"


def test_bff_incident_detail_fails_closed_when_incident_backend_is_missing() -> None:
    with _isolated_incident_bff(None) as client:
        list_response = client.get("/bff/incidents", headers=HEADERS)
        detail_response = client.get("/bff/incidents/inc-missing", headers=HEADERS)

    assert list_response.status_code == 200, list_response.text
    list_payload = list_response.json()
    assert list_payload["data"] == []
    assert list_payload["items"] == []
    assert list_payload["meta"]["surfaces"]["incidents"]["status"] == "unavailable"
    assert list_payload["meta"]["surfaces"]["incidents"]["source"] == "missing"

    assert detail_response.status_code == 503, detail_response.text
    assert detail_response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

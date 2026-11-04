"""Focused contract tests for the dedicated Postmortem router and service."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


from postmortems.router import create_postmortem_router
from postmortems.service import PostmortemService


def _real_postmortem_record(
    postmortem_id: str,
    *,
    incident_id: str = "inc-1",
    root_cause: str = "stale market snapshot",
    created_at: str = "2026-11-03T12:00:00Z",
    status: str = "draft",
) -> Dict[str, Any]:
    return {
        "postmortem_id": postmortem_id,
        "report_id": postmortem_id,
        "incident_id": incident_id,
        "title": f"Postmortem draft: BFF downstream degradation: {postmortem_id}",
        "status": status,
        "created_at": created_at,
        "binding_id": f"bind-{postmortem_id}",
        "deployment_stage": "paper",
        "deployment_plan_id": f"plan-{postmortem_id}",
        "capital_pool_id": "pool-alpha",
        "persona_capital_binding_id": f"pcb-{postmortem_id}",
        "artifact_id": f"art-{postmortem_id}",
        "artifact_version": "v1.0.0",
        "runtime_id": f"rt-{postmortem_id}",
        "trace_id": f"trace-{postmortem_id}",
        "telemetry_event_ids": [f"tel-{postmortem_id}"],
        "incident_evidence_summary": root_cause,
        "lineage_ref": f"art-{postmortem_id}@v1.0.0",
        "incident_cluster_id": None,
        "root_cause": root_cause,
        "contributing_factors": ["downstream timeout"],
        "timeline": [{"ts": created_at, "event": "Incident opened"}],
        "action_items": ["Verify owner read path"],
        "author_ids": ["system"],
    }


class _ReadStore:
    def __init__(self) -> None:
        self.postmortems: Dict[str, Dict[str, Any]] = {}
        self.incidents: Dict[str, Dict[str, Any]] = {}
        self.time_ranges: List[Optional[str]] = []
        self.detail_ids: List[str] = []

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        self.time_ranges.append(time_range)
        return list(self.postmortems.values())

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        self.detail_ids.append(report_id)
        return self.postmortems.get(report_id)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.incidents.get(incident_id)


def _error(status_code: int, code: Any, message: str, reason: str, **_: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code.value if hasattr(code, "value") else str(code),
            "message": message,
            "reason": reason,
        },
    )


def _client(
    store: _ReadStore,
    *,
    extract_identity=lambda authorization: {"authorization": authorization},
    require_read_role=lambda identity: None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_postmortem_router(
            get_read_store=lambda: store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=_error,
            meta_staleness=lambda: {"served_from": "cache", "last_known_at": "2026-08-30T00:00:00Z"},
        )
    )
    return TestClient(app)


def test_router_registers_exactly_the_two_postmortem_decorators() -> None:
    router = create_postmortem_router()
    routes = {
        (method, route.path)
        for route in router.routes
        if hasattr(route, "methods")
        for method in route.methods
    }

    assert routes == {
        ("GET", "/api/v1/postmortems"),
        ("GET", "/api/v1/postmortems/{report_id}"),
    }


def test_list_postmortems_preserves_time_range_data_and_meta_contract() -> None:
    store = _ReadStore()
    store.postmortems = {
        "pm-1": _real_postmortem_record(
            "pm-1",
            root_cause="stale market snapshot",
            created_at="2026-11-03T12:00:00Z",
        ),
        "pm-2": _real_postmortem_record(
            "pm-2",
            root_cause="broker timeout",
            created_at="2026-11-02T12:00:00Z",
        ),
    }
    auth_seen: List[Optional[str]] = []

    response = _client(
        store,
        extract_identity=lambda authorization: auth_seen.append(authorization) or {"roles": ["viewer"]},
    ).get(
        "/api/v1/postmortems?time_range=7d",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": list(store.postmortems.values()),
        "page_info": {
            "total": 2,
            "page_size": 50,
            "next_page_token": None,
        },
        "meta": {
            "total": 2,
            "staleness": {
                "served_from": "cache",
                "last_known_at": "2026-08-30T00:00:00Z",
            },
            "surfaces": {
                "postmortems": {
                    "status": "ok",
                    "source": "typed_store",
                },
            },
        },
    }
    assert store.time_ranges == ["7d"]
    assert auth_seen == ["Bearer test-token"]


def test_get_postmortem_enriches_detail_with_linked_incident_without_mutating_store() -> None:
    store = _ReadStore()
    store.postmortems["pm-1"] = _real_postmortem_record(
        "pm-1",
        incident_id="inc-1",
        root_cause="stale market snapshot",
    )
    store.incidents["inc-1"] = {"incident_id": "inc-1", "severity": "high"}

    response = _client(store).get("/api/v1/postmortems/pm-1")

    assert response.status_code == 200
    assert response.json()["data"] == {
        **store.postmortems["pm-1"],
        "linked_incident": store.incidents["inc-1"],
    }
    assert response.json()["meta"]["staleness"]["served_from"] == "cache"
    assert "linked_incident" not in store.postmortems["pm-1"]
    assert store.detail_ids == ["pm-1"]


def test_get_postmortem_returns_detail_without_missing_linked_incident() -> None:
    store = _ReadStore()
    store.postmortems["pm-1"] = _real_postmortem_record("pm-1", incident_id="inc-missing")

    response = _client(store).get("/api/v1/postmortems/pm-1")

    assert response.status_code == 200
    assert response.json()["data"] == store.postmortems["pm-1"]
    assert "linked_incident" not in response.json()["data"]


def test_get_postmortem_preserves_structured_not_found_error_contract() -> None:
    response = _client(_ReadStore()).get("/api/v1/postmortems/pm-missing")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "RESOURCE_NOT_FOUND",
        "message": "Postmortem report not found",
        "reason": "Postmortem pm-missing does not exist",
    }


def test_read_role_gate_runs_before_store_access() -> None:
    store = _ReadStore()

    def reject_read(_: Any) -> None:
        raise HTTPException(status_code=403, detail="read role required")

    response = _client(store, require_read_role=reject_read).get("/api/v1/postmortems")

    assert response.status_code == 403
    assert store.time_ranges == []


def test_service_uses_injected_canonical_store_for_list_and_detail() -> None:
    store = _ReadStore()
    store.postmortems["pm-1"] = _real_postmortem_record("pm-1", incident_id="inc-1")
    store.incidents["inc-1"] = {"incident_id": "inc-1"}
    service = PostmortemService(store)

    assert service.list_postmortems(time_range="30d") == [store.postmortems["pm-1"]]
    assert service.get_postmortem("pm-1") == {
        **store.postmortems["pm-1"],
        "linked_incident": store.incidents["inc-1"],
    }
    assert service.get_postmortem("pm-missing") is None
    assert store.time_ranges == ["30d"]

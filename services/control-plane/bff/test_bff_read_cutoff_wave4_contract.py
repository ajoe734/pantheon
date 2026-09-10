from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.deployment.adapters import DeploymentReadSurfaceAdapter
from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas.service import (
    _composed_surface_status,
    _snapshot_meta,
    utc_now,
)
from services.control_plane.bff.ports import ReadSurfacePorts


AUTH = {"Authorization": "Bearer operator_001"}


def _local_read_cutoff_data() -> dict[str, Any]:
    return {
        "deployment_plans": {
            "plan-F-042": {
                "id": "plan-F-042",
                "plan_id": "plan-F-042",
                "persona_id": "persona-alpha",
                "binding_id": "binding-alpha",
                "capital_pool_id": "pool-main",
                "deployment_mode": "paper",
                "status": "active",
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-05-01T00:00:00Z",
            }
        }
    }


_READ_SOURCE_ENVS = {
    "PANTHEON_DEPLOYMENT_API_URL": "",
    "PANTHEON_DEPLOYMENT_SERVICE_URL": "",
    "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "",
    "PANTHEON_GOVERNANCE_SERVICE_URL": "",
    "PANTHEON_CAPITAL_API_URL": "",
    "PANTHEON_CAPITAL_SERVICE_URL": "",
    "PANTHEON_RUNTIME_MANAGER_URL": "",
    "PANTHEON_INTERNAL_API_URL": "",
    "PANTHEON_GOVERNANCE_DATA_DIR": "",
    "PANTHEON_RUNTIME_DATA_DIR": "",
}


class ReadCutoffWave4TestReadPorts(ReadSurfacePorts):
    def __init__(self, *, allow_local_snapshot_fallback: bool = False, seed_data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._allow_local_snapshot_fallback = allow_local_snapshot_fallback
        self._data = seed_data if seed_data is not None else (_local_read_cutoff_data() if allow_local_snapshot_fallback else {})

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        if self._allow_local_snapshot_fallback:
            return "local_snapshot"
        return "missing"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        if self._allow_local_snapshot_fallback:
            return {
                "status": "degraded",
                "source": "local_snapshot",
                "snapshot_at": snapshot_at,
                "freshness": "degraded",
                "observed_time": snapshot_at,
                "coverage": 1.0,
                "missing_bindings": False,
                "note": "using local BFF snapshot fallback",
            }
        return {
            "status": "unavailable",
            "source": "missing",
            "snapshot_at": snapshot_at,
            "freshness": "unavailable",
            "observed_time": snapshot_at,
            "coverage": 0.0,
            "missing_bindings": True,
        }

    def list_deployment_plans(self, status: str | None = None, capital_pool_id: str | None = None, include_fixture_pack: bool = True, **kwargs: Any) -> list[dict[str, Any]]:
        if not self._allow_local_snapshot_fallback:
            return []
        ds = self._data.get("deployment_plans", {})
        plans = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if not include_fixture_pack:
            plans = [p for p in plans if "pack" not in str(p.get("id") or p.get("plan_id") or "")]
        return plans

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        if not self._allow_local_snapshot_fallback:
            return None
        ds = self._data.get("deployment_plans", {})
        if isinstance(ds, dict):
            return ds.get(str(plan_id or ""))
        return next((p for p in ds if p.get("id") == plan_id or p.get("plan_id") == plan_id), None)


def _surface_degradation_reason(
    surface: dict[str, Any],
    *,
    degraded_reason: str,
    unavailable_reason: str,
) -> str | None:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason
    if surface.get("message"):
        return str(surface["message"])
    if surface.get("note"):
        return str(surface["note"])
    return degraded_reason


def _raise_if_read_surface_unavailable(
    surface: dict[str, Any],
    *,
    label: str,
) -> None:
    if surface.get("status") != "unavailable":
        return
    raise bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"{label} read surface unavailable",
        str(surface.get("message") or surface.get("note") or f"{label} downstream read source is unavailable."),
        precondition_failed="read_surface_unavailable",
        suggestion="Verify the owning service URL and health before retrying this read.",
    )


def _make_client(store: ReadSurfacePorts) -> TestClient:
    router = create_deployment_router(
        queries=DeploymentReadSurfaceAdapter(store),
        commands=None,
        extract_identity=extract_identity_stub,
        require_read_role=require_read_role,
        require_operator_role=require_operator_role,
        bff_error=bff_error,
        utc_now=utc_now,
        page_slice=lambda items, token, size: (items, None),
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=lambda dataset, **kw: store.dataset_surface_status(dataset, snapshot_at=kw.get("snapshot_at") or utc_now()),
        composed_surface_status=_composed_surface_status,
        read_surface_meta=lambda dataset, surface_key, **kw: {
            "snapshot_at": kw.get("snapshot_at") or utc_now(),
            "surfaces": {
                surface_key: kw.get("surface") or store.dataset_surface_status(dataset, snapshot_at=kw.get("snapshot_at") or utc_now())
            },
            **({"total": kw["total"]} if "total" in kw else {}),
            **({"degradation": {"reason": _surface_degradation_reason(
                kw.get("surface") or store.dataset_surface_status(dataset, snapshot_at=kw.get("snapshot_at") or utc_now()),
                degraded_reason=kw.get("degraded_reason") or f"{surface_key.replace('_', ' ')} is degraded and may be stale.",
                unavailable_reason=kw.get("unavailable_reason") or f"{surface_key.replace('_', ' ')} is currently unavailable.",
            )}} if _surface_degradation_reason(
                kw.get("surface") or store.dataset_surface_status(dataset, snapshot_at=kw.get("snapshot_at") or utc_now()),
                degraded_reason=kw.get("degraded_reason") or f"{surface_key.replace('_', ' ')} is degraded and may be stale.",
                unavailable_reason=kw.get("unavailable_reason") or f"{surface_key.replace('_', ' ')} is currently unavailable.",
            ) is not None else {}),
        },
        raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
        aggregate_group_surface=lambda *a, **kw: {"status": "available"},
        split_csv_query=lambda val: val.split(",") if val else None,
        meta_staleness=lambda: None,
        stable_json_hash=lambda val: "hash",
        resolve_final_idempotency_key=lambda r, h: r or h or "key",
        reject_body_idempotency_key=lambda p: None,
        request_dry_run_requested=lambda *a, **kw: False,
        gov_bff_idempotency={},
        publish_event=lambda *a, **kw: "event-id",
        sse_buffers={},
        sse_subscribers={},
        gov_bff_action_command=lambda *a, **kw: {},
        deprecated_bff_path_response=lambda *a, **kw: None,
        sem_command_response=lambda *a, **kw: {},
        stream_generic_events=lambda *a, **kw: iter(()),
        surface_degradation_reason=lambda surf, **kw: _surface_degradation_reason(
            surf,
            degraded_reason=kw.get("degraded_reason", "deployment plan list is degraded and may be stale."),
            unavailable_reason=kw.get("unavailable_reason", "deployment plan list is currently unavailable."),
        ),
    )
    app = FastAPI()
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request, exc: HTTPException):
        content = exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail), "code": "ERROR"}}
        return JSONResponse(status_code=exc.status_code, content=content)

    app.include_router(router)
    return TestClient(app)


def test_prod_catalog_read_does_not_mask_cutoff_with_local_snapshot(monkeypatch) -> None:
    for env_name, value in _READ_SOURCE_ENVS.items():
        monkeypatch.setenv(env_name, value)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

    store = ReadCutoffWave4TestReadPorts(allow_local_snapshot_fallback=False)
    client = _make_client(store)
    list_response = client.get("/api/v1/deployment-plans", headers=AUTH)

    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["data"] == []
    surface = list_payload["meta"]["surfaces"]["deployment_plan_list"]
    assert surface["status"] == "unavailable"
    assert surface["source"] == "missing"
    assert list_payload["meta"]["degradation"]["reason"] == "deployment plan list is currently unavailable."

    detail_response = client.get("/api/v1/deployment-plans/plan-F-042", headers=AUTH)
    assert detail_response.status_code == 503
    assert detail_response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_dev_catalog_snapshot_fallback_is_explicitly_degraded(monkeypatch) -> None:
    for env_name, value in _READ_SOURCE_ENVS.items():
        monkeypatch.setenv(env_name, value)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

    store = ReadCutoffWave4TestReadPorts(allow_local_snapshot_fallback=True)
    client = _make_client(store)
    response = client.get("/api/v1/deployment-plans", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert [plan["plan_id"] for plan in payload["data"]] == ["plan-F-042"]
    surface = payload["meta"]["surfaces"]["deployment_plan_list"]
    assert surface["status"] == "degraded"
    assert surface["source"] == "local_snapshot"
    assert "local BFF snapshot fallback" in surface["note"]

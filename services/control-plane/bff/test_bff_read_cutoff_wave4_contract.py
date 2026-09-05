from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.deployment.router import create_deployment_router
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


def _make_client(allow_local_snapshot_fallback: bool) -> TestClient:
    store = ReadCutoffWave4TestReadPorts(allow_local_snapshot_fallback=allow_local_snapshot_fallback)

    def _bff_error(status_code: int, code: Any, message: str, details: Any = None, **kwargs: Any) -> HTTPException:
        code_val = code.value if hasattr(code, "value") else str(code)
        return HTTPException(
            status_code=status_code,
            detail={"error": {"code": code_val, "message": message, "details": details or {}}},
        )

    def _read_surface_meta(dataset: str, surface_key: str, *, snapshot_at: str | None = None, total: int | None = None, **kwargs: Any) -> dict[str, Any]:
        snapshot_at = snapshot_at or "2026-05-01T00:00:00Z"
        surface = store.dataset_surface_status(dataset, snapshot_at=snapshot_at)
        meta: dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {surface_key: surface},
        }
        if total is not None:
            meta["total"] = total
        if surface.get("status") == "unavailable":
            meta["degradation"] = {"reason": "deployment plan list is currently unavailable."}
        elif surface.get("status") == "degraded":
            meta["degradation"] = {"reason": "deployment plan list is degraded and may be stale."}
        return meta

    def _raise_if_read_surface_unavailable(surface: dict[str, Any], *, label: str) -> None:
        if surface.get("status") == "unavailable":
            raise _bff_error(503, "DEPENDENCY_UNAVAILABLE", f"{label} read surface unavailable")

    app = FastAPI(title="Deployment Read Cutoff Contract")

    @app.exception_handler(HTTPException)
    def _http_exception_handler(request: Any, exc: HTTPException) -> Any:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            content = detail
        elif isinstance(detail, dict):
            content = {"error": detail}
        else:
            content = {"error": {"message": str(detail), "code": "HTTP_ERROR"}}
        return JSONResponse(status_code=exc.status_code, content=content)

    app.include_router(
        create_deployment_router(
            queries=store,
            extract_identity=lambda _auth: object(),
            require_read_role=lambda _identity: None,
            require_operator_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-05-01T00:00:00Z",
            page_slice=lambda items, token, size: (items, None),
            snapshot_meta=lambda s: {"snapshot_at": s},
            dataset_surface_status=lambda ds, snapshot_at=None, **kw: store.dataset_surface_status(ds, snapshot_at=snapshot_at or "2026-05-01T00:00:00Z"),
            composed_surface_status=lambda **kw: {"status": "available"},
            read_surface_meta=_read_surface_meta,
            raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
            aggregate_group_surface=lambda **kw: {"status": "available"},
            split_csv_query=lambda val: val.split(",") if val else None,
            meta_staleness=lambda: None,
            stable_json_hash=lambda p: "hash",
            resolve_final_idempotency_key=lambda r, h: r or h or "key",
            reject_body_idempotency_key=lambda p: None,
            request_dry_run_requested=lambda *a, **k: False,
            gov_bff_idempotency={},
            publish_event=lambda *a, **k: "event-id",
            sse_buffers={},
            sse_subscribers={},
            gov_bff_action_command=lambda *a, **k: {},
            deprecated_bff_path_response=lambda *a, **k: None,
            sem_command_response=lambda *a, **k: {},
            stream_generic_events=lambda *a, **k: iter(()),
            surface_degradation_reason=lambda *a, **k: None,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_prod_catalog_read_does_not_mask_cutoff_with_local_snapshot() -> None:
    client = _make_client(allow_local_snapshot_fallback=False)
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


def test_dev_catalog_snapshot_fallback_is_explicitly_degraded() -> None:
    client = _make_client(allow_local_snapshot_fallback=True)
    response = client.get("/api/v1/deployment-plans", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert [plan["plan_id"] for plan in payload["data"]] == ["plan-F-042"]
    surface = payload["meta"]["surfaces"]["deployment_plan_list"]
    assert surface["status"] == "degraded"
    assert surface["source"] == "local_snapshot"
    assert "local BFF snapshot fallback" in surface["note"]

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.ports import ReadSurfacePorts
from services.control_plane.bff.runtime.router import create_runtime_router


HEADERS = {"Authorization": "Bearer rt-003-operator:operator"}


class RuntimesTestReadPorts(ReadSurfacePorts):
    def __init__(self, runtime_bindings: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self._runtime_bindings = runtime_bindings

    @staticmethod
    def _project(raw: dict[str, Any]) -> dict[str, Any]:
        binding_id = raw.get("binding_id") or raw.get("id")
        deployment_mode = raw.get("deployment_mode") or raw.get("deployment_stage")
        deployment_stage = raw.get("deployment_stage") or raw.get("deployment_mode")
        projected = dict(raw)
        projected["id"] = binding_id
        projected["binding_id"] = binding_id
        projected["runtime_binding_id"] = raw.get("runtime_binding_id") or binding_id
        projected["runtime_id"] = raw.get("runtime_id") or binding_id
        projected["deployment_mode"] = deployment_mode
        projected["deployment_stage"] = deployment_stage
        return projected

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        if self._runtime_bindings is not None:
            return "canonical"
        return "missing"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        if self._runtime_bindings is not None:
            return {
                "status": "ok",
                "source": "canonical",
                "snapshot_at": snapshot_at,
                "freshness": "fresh",
                "observed_time": snapshot_at,
                "coverage": 1.0,
                "missing_bindings": False,
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

    def list_runtime_bindings(
        self,
        status: str | None = None,
        deployment_stage: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if self._runtime_bindings is None:
            return []
        items = [self._project(item) for item in self._runtime_bindings]
        if status:
            items = [item for item in items if str(item.get("status") or "").lower() == status.lower()]
        if deployment_stage:
            items = [
                item
                for item in items
                if str(item.get("deployment_stage") or item.get("deployment_mode") or "").lower()
                == deployment_stage.lower()
            ]
        return items

    def get_runtime_binding(self, binding_id: str | None) -> dict[str, Any] | None:
        if self._runtime_bindings is None or not binding_id:
            return None
        target = str(binding_id).strip()
        record = next(
            (
                r
                for r in self._runtime_bindings
                if str(r.get("id") or "").strip() == target
                or str(r.get("binding_id") or "").strip() == target
                or str(r.get("runtime_binding_id") or "").strip() == target
                or str(r.get("runtime_id") or "").strip() == target
            ),
            None,
        )
        return self._project(record) if record else None

    def get_runtime_binding_by_runtime_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        return self.get_runtime_binding(runtime_id)


def _extract_identity(authorization: str | None = None, **kwargs: Any) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = authorization[len("Bearer "):].strip()
    parts = raw.split(":")
    operator_id = parts[0] if parts else "op"
    roles = [r.strip() for r in parts[1].split(",")] if len(parts) > 1 else []
    return OperatorIdentity(operator_id=operator_id, roles=roles, auth_mode="bearer")


def _require_role(identity: OperatorIdentity) -> None:
    if not identity or not identity.roles:
        raise HTTPException(status_code=403, detail="Forbidden")


def _bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: str = "",
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> HTTPException:
    val = code.value if hasattr(code, "value") else str(code)
    details: dict[str, Any] = {
        "reason": reason or message,
        "precondition_failed": precondition_failed,
        "suggestion": suggestion,
    }
    if details_extra:
        details.update(details_extra)
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": val,
                "message": message,
                "details": details,
            }
        },
    )


def _raise_if_read_surface_unavailable(surface: dict[str, Any], *, label: str) -> None:
    if surface.get("status") != "unavailable":
        return
    raise _bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"{label} read surface unavailable",
        str(surface.get("message") or surface.get("note") or f"{label} downstream read source is unavailable."),
        precondition_failed="read_surface_unavailable",
        suggestion="Verify the owning service URL and health before retrying this read.",
    )


@contextmanager
def _isolated_runtime_bff(
    runtime_bindings: list[dict[str, Any]] | None,
) -> Iterator[TestClient]:
    store = RuntimesTestReadPorts(runtime_bindings)
    app = FastAPI(title="Runtimes Test App")

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

    router = create_runtime_router(
        get_read_store=lambda: store,
        dependencies={
            "_extract_identity": _extract_identity,
            "_require_read_role": _require_role,
            "_dataset_surface_status": lambda ds, snapshot_at=None, **kwargs: store.dataset_surface_status(
                ds, snapshot_at=snapshot_at or "2026-05-16T05:40:00Z"
            ),
            "_page_slice": lambda items, token, size: (items[:size], None),
            "_snapshot_meta": lambda ts: {"snapshot_at": ts},
            "_meta_staleness": lambda: None,
            "_raise_if_read_surface_unavailable": _raise_if_read_surface_unavailable,
            "_bff_error": _bff_error,
            "utc_now": lambda: "2026-05-16T05:40:00Z",
        },
    )
    app.include_router(router)
    yield TestClient(app, raise_server_exceptions=False)


def _runtime_records() -> list[dict[str, Any]]:
    return [
        {
            "binding_id": "rb-rt003-paper-001",
            "runtime_id": "runtime-rt003-paper-001",
            "capital_pool_id": "pool-rt003-paper",
            "artifact_id": "artifact-rt003-paper",
            "artifact_version": "1.2.3",
            "deployment_mode": "paper",
            "effective_at": "2026-05-16T05:40:00Z",
            "status": "active",
            "plan_id": "plan-rt003-paper",
            "persona_capital_binding_id": "pcb-rt003-paper",
            "metadata": {
                "engine_bridge_repo": "ajoe734/pantheon-lean",
                "engine_bridge_commit": "rt003abc",
            },
        },
        {
            "binding_id": "rb-rt003-paused-001",
            "runtime_id": "runtime-rt003-paused-001",
            "capital_pool_id": "pool-rt003-paper",
            "artifact_id": "artifact-rt003-paused",
            "artifact_version": "1.2.0",
            "deployment_mode": "paper",
            "effective_at": "2026-05-16T05:00:00Z",
            "status": "paused",
            "plan_id": "plan-rt003-paused",
            "persona_capital_binding_id": "pcb-rt003-paper",
        },
    ]


def test_bff_runtimes_list_filters_canonical_runtime_bindings_without_local_fallback() -> None:
    with _isolated_runtime_bff(_runtime_records()) as client:
        response = client.get(
            "/bff/runtimes?status=active&deployment_stage=paper",
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta"]["surfaces"]["runtimes"]["source"] == "canonical"
    assert payload["meta"]["total"] == 1
    assert payload["page_info"] == {"next_page_token": None}
    assert len(payload["items"]) == 1
    runtime = payload["items"][0]
    assert runtime["id"] == "rb-rt003-paper-001"
    assert runtime["binding_id"] == "rb-rt003-paper-001"
    assert runtime["runtime_binding_id"] == "rb-rt003-paper-001"
    assert runtime["runtime_id"] == "runtime-rt003-paper-001"
    assert runtime["deployment_stage"] == "paper"
    assert runtime["deployment_mode"] == "paper"
    assert runtime["artifact_id"] == "artifact-rt003-paper"
    assert runtime["metadata"]["engine_bridge_commit"] == "rt003abc"


def test_bff_runtime_detail_resolves_runtime_id_and_binding_id() -> None:
    with _isolated_runtime_bff(_runtime_records()) as client:
        by_runtime_id = client.get("/bff/runtimes/runtime-rt003-paper-001", headers=HEADERS)
        by_binding_id = client.get("/bff/runtimes/rb-rt003-paper-001", headers=HEADERS)

    assert by_runtime_id.status_code == 200, by_runtime_id.text
    runtime_payload = by_runtime_id.json()
    assert runtime_payload["meta"]["surfaces"]["runtime"]["source"] == "canonical"
    assert runtime_payload["data"]["binding_id"] == "rb-rt003-paper-001"
    assert runtime_payload["data"]["effective_at"] == "2026-05-16T05:40:00Z"

    assert by_binding_id.status_code == 200, by_binding_id.text
    assert by_binding_id.json()["data"]["runtime_id"] == "runtime-rt003-paper-001"


def test_bff_runtime_detail_reports_downstream_unavailable_when_runtime_store_missing() -> None:
    with _isolated_runtime_bff(None) as client:
        list_response = client.get("/bff/runtimes", headers=HEADERS)
        detail_response = client.get("/bff/runtimes/runtime-missing", headers=HEADERS)

    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["items"] == []
    assert list_response.json()["meta"]["surfaces"]["runtimes"]["status"] == "unavailable"

    assert detail_response.status_code == 503, detail_response.text
    assert detail_response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

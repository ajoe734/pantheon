from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports import ReadSurfacePorts
from services.control_plane.bff.personas.service import (
    _extract_identity,
    _require_read_role,
    _require_operator_role,
    _bff_error,
)


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


OPERATOR_TOKEN = "Bearer op-agora-extended:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


class AgoraExtendedTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None, fallback_degraded: bool = True) -> None:
        super().__init__()
        self._data = data or {}
        self._fallback_degraded = fallback_degraded

    def list_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("postmortems", {}).values())

    def list_agora_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_postmortems(**kwargs)

    def dataset_source(self, dataset: str) -> str:
        if dataset in self._data:
            return "local_snapshot"
        return "missing"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        source = kwargs.get("source") or self.dataset_source(dataset)
        if source == "missing":
            return {"status": "unavailable", "source": "missing", "snapshot_at": snapshot_at}
        status = "degraded" if self._fallback_degraded else "ok"
        return {"status": status, "source": source, "snapshot_at": snapshot_at}


def _seed_read_store() -> AgoraExtendedTestReadPorts:
    data = {
        "postmortems": {
            "pm-agora-001": {
                "id": "pm-agora-001",
                "postmortem_id": "pm-agora-001",
                "incident_id": "inc-agora-001",
                "title": "Agora signal review postmortem",
                "status": "published",
                "created_at": "2026-05-08T10:03:00Z",
                "updated_at": "2026-05-08T10:03:00Z",
            }
        },
    }
    return AgoraExtendedTestReadPorts(data, fallback_degraded=True)


def _build_agora_extended_client(store: AgoraExtendedTestReadPorts, command_store_path: str) -> TestClient:
    command_store = CommandStore(command_store_path)
    idempotency_store: dict[str, Any] = {}
    router = create_agora_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_write_role=_require_operator_role,
        require_operator_role=_require_operator_role,
        require_journal_write_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=_utc_now_rfc3339,
        read_surface=store,
        journal_write_owner=store,
        command_store=command_store,
        idempotency_store=idempotency_store,
        sync_servant_agent=lambda p: {},
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request, exc):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
        )

    app.include_router(router)
    return TestClient(app)


@contextmanager
def _isolated_agora_extended_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        yield _build_agora_extended_client(_seed_read_store(), os.path.join(td, "commands.jsonl"))


def _has_item(items: list[dict], field: str, expected: str) -> bool:
    return any(str(item.get(field) or "") == expected for item in items)


def test_agora_extended_final_read_routes_return_seeded_data_with_source_meta() -> None:
    with _isolated_agora_extended_bff() as client:
        cases = [
            ("/bff/agora/postmortems", "agora_postmortems", "postmortem_id", "pm-agora-001"),
        ]

        for path, surface_key, id_field, expected_id in cases:
            response = client.get(path, headers=HEADERS)

            assert response.status_code == 200, response.text
            payload = response.json()
            assert _has_item(payload["items"], id_field, expected_id)
            surface = payload["meta"]["surfaces"][surface_key]
            assert surface["source"] == "local_snapshot"
            assert surface["status"] == "degraded"

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


OPERATOR_TOKEN = "Bearer op-agora:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


class AgoraCoreTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None) -> None:
        super().__init__()
        self._data = data or {}

    def list_decision_journal_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("decision_journal_entries", {}).values())

    def create_decision_journal_entry(
        self, *, title: str, body: str, actor_id: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        entry = {
            "id": "dje-001",
            "title": title,
            "body": body,
            "author": actor_id or "op-agora",
            "canonicalWriteAuthority": "agora_journal_service",
            "tenant_id": kwargs.get("tenant_id") or "pantheon-dev",
            "tenantId": kwargs.get("tenant_id") or "pantheon-dev",
            "user_id": kwargs.get("user_id") or actor_id or "op-agora",
            "userId": kwargs.get("user_id") or actor_id or "op-agora",
        }
        self._data.setdefault("decision_journal_entries", {})[entry["id"]] = entry
        return entry

    def dataset_source(self, dataset: str) -> str:
        return "in_memory"

    def dataset_surface_status(
        self, dataset: str, *, snapshot_at: str, **kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "ok", "source": "in_memory", "snapshot_at": snapshot_at}


def _seed_read_store() -> AgoraCoreTestReadPorts:
    data = {
        "decision_journal_entries": {},
    }
    return AgoraCoreTestReadPorts(data)


@contextmanager
def _isolated_agora_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        store = _seed_read_store()
        command_store = CommandStore(os.path.join(td, "commands.jsonl"))
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
        yield TestClient(app)


def test_agora_note_journal_insight_and_training_creation() -> None:
    with _isolated_agora_bff() as client:
        journal = client.post(
            "/bff/agora/journal",
            headers={**HEADERS, "Idempotency-Key": "agora-journal-001"},
            json={
                "title": "Delay promotion",
                "decision": "Keep sig-001 in paper observation.",
                "rationale": "Auction slippage risk remains elevated.",
                "tags": ["paper.rollout"],
                "linkedStrategyIds": ["strategy-alpha"],
            },
        )
        assert journal.status_code == 201, journal.text
        assert journal.json()["data"]["canonicalWriteAuthority"] == "agora_journal_service"

        journal_list = client.get("/bff/agora/journal", headers=HEADERS)
        assert journal_list.status_code == 200, journal_list.text
        assert journal_list.json()["items"][0]["title"] == "Delay promotion"

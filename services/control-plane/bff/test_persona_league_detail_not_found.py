"""Regression: persona-league detail must 404 (not 500) for unknown ids, and
no BFF handler may reference a non-existent ErrorCode member.

The not-found branch of bff_persona_league_detail raised
ErrorCode.OBJECT_NOT_FOUND, which is not a member of ErrorCode. Python raised
AttributeError, surfacing as a 500 INTERNAL_ERROR instead of a clean 404
(verification campaign 2026-06-14, round 3, finding F5).

The static guard catches the whole class: any ErrorCode.<NAME> reference whose
NAME is not a real enum member would raise AttributeError at request time.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

BFF_DIR = Path(__file__).resolve().parent


class _FakeOwner:
    pass


class _FakeCommandStore:
    def get_all(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass


def _client() -> TestClient:
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"

    read_store = create_in_memory_read_surface_ports()
    service = PersonaService(
        read_store=read_store,
        write_owner=_FakeOwner(),
        ranking_write_owner=_FakeOwner(),
        command_store=_FakeCommandStore(),
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    app.include_router(create_personas_router(service=service))
    return TestClient(app)


HEADERS = {"Authorization": "Bearer op-verify:reader,operator,admin:mfa"}


def test_persona_league_detail_unknown_id_returns_404() -> None:
    client = _client()
    for path in (
        "/bff/persona-league/does-not-exist-xyz",
        "/bff/management/persona-league/does-not-exist-xyz",
    ):
        resp = client.get(path, headers=HEADERS)
        assert resp.status_code == 404, f"{path} -> {resp.status_code}: {resp.text}"
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_no_invalid_errorcode_references_in_main() -> None:
    valid = set(ErrorCode.__members__.keys())
    text = (BFF_DIR / "main.py").read_text(encoding="utf-8")
    referenced = set(re.findall(r"ErrorCode\.([A-Z_]+)", text))
    invalid = sorted(referenced - valid)
    assert not invalid, f"main.py references non-existent ErrorCode members: {invalid}"


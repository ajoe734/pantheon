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
import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent


from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.ports import (
    create_in_memory_read_surface_ports,
    create_persona_registry_write_owner,
)


class _FakeRankingWriteOwner:
    def put_ranking_snapshot(self, snapshot: dict) -> dict:
        return {"status": "created"}

    def get_ranking_snapshot(self, snapshot_id: str) -> None:
        return None


class _FakeCommandStore:
    pass


def _client() -> TestClient:
    read_store = create_in_memory_read_surface_ports()
    service = PersonaService(
        write_owner=create_persona_registry_write_owner(),
        ranking_write_owner=_FakeRankingWriteOwner(),
        read_store=read_store,
        command_store=_FakeCommandStore(),
    )
    router = create_personas_router(
        service=service,
        extract_identity_fn=lambda auth: OperatorIdentity(
            operator_id="op-verify", roles=["reader", "operator", "admin"], mfa_verified=True
        ),
        require_read_role_fn=lambda identity: None,
    )
    app = FastAPI()

    async def _http_exc_handler(request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.add_exception_handler(HTTPException, _http_exc_handler)
    app.include_router(router)
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
    from services.control_plane.bff.models import ErrorCode

    valid = set(ErrorCode.__members__.keys())
    text = (BFF_DIR / "main.py").read_text()
    referenced = set(re.findall(r"ErrorCode\.([A-Z_]+)", text))
    invalid = sorted(referenced - valid)
    assert not invalid, f"main.py references non-existent ErrorCode members: {invalid}"

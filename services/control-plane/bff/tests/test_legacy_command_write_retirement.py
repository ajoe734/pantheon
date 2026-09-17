"""Regression coverage proving the legacy generic command-write routes are
fully retired from the BFF backend while the sole canonical write route
(``POST /bff/v1/commands``) and the surviving read routes (``GET /bff/actions``,
``GET /api/v1/operator/commands/{command_id}``) keep working end to end.

Retired (must no longer route to anything):
  - POST /bff/actions/{type}/{id}/{action}
  - POST /api/v1/operator/commands

Kept:
  - POST /bff/v1/commands            (canonical generic command write)
  - GET  /bff/actions                (action catalog read)
  - GET  /api/v1/operator/commands/{command_id}  (command status read)
"""
from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters import (
    create_action_command_router,
    create_command_adapters_router,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import OperatorIdentity

AUTH_HEADER = {"Authorization": "Bearer op-1:operator,approver:mfa"}


def _test_extract_identity(
    authorization: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        return OperatorIdentity(
            operator_id="anonymous",
            roles=["viewer"],
            auth_mode="anonymous",
            has_mfa=False,
        )
    token = authorization[len("Bearer ") :].strip()
    parts = token.split(":")
    actor = parts[0] if parts else "system"
    roles = [r.strip() for r in parts[1].split(",")] if len(parts) > 1 else ["operator"]
    return OperatorIdentity(
        operator_id=actor,
        roles=roles,
        auth_mode="bearer",
        has_mfa=len(parts) > 2 and parts[2] == "mfa",
    )


def _build_app(command_store: CommandStore) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_action_command_router(anything="ignored", command_store=command_store))
    app.include_router(
        create_command_adapters_router(
            get_command_store=lambda: command_store,
            get_read_store=lambda: None,
            extract_identity=_test_extract_identity,
        )
    )
    return app


def test_create_action_command_router_is_a_zero_route_stub() -> None:
    """create_action_command_router used to register the retired
    POST /bff/actions/{type}/{id}/{action} route. It is kept as a callable
    no-op stub (rather than deleted outright) only so that existing callers
    of this exact symbol -- command_adapters/__init__.py's re-export and
    test_v5_interventions.py's direct call -- do not need to change; it must
    register zero routes.
    """
    router = create_action_command_router(anything="ignored", command_store=None)
    assert router.routes == []


def test_legacy_action_route_no_longer_matches_any_route() -> None:
    """POST /bff/actions/{type}/{id}/{action} was fully retired: the route
    registration inside create_action_command_router is gone (see
    test_create_action_command_router_is_a_zero_route_stub above -- the
    factory itself is kept only as a zero-route stub for import
    compatibility). Since GET /bff/actions (no path suffix) is a distinct,
    still-registered route and no other router in the app claims the
    /bff/actions/{...}/{...}/{...} shape, FastAPI has no route to match this
    request against, so it falls through to the framework's default "no
    route matched" handling: 404 Not Found.
    """
    with tempfile.TemporaryDirectory() as td:
        cmd_store = CommandStore(os.path.join(td, "main_commands.jsonl"))
        client = TestClient(_build_app(cmd_store))

        resp = client.post(
            "/bff/actions/persona/persona-1/run_eval",
            headers={**AUTH_HEADER, "Idempotency-Key": "legacy-action-route-01"},
            json={"reason": "legacy retirement probe"},
        )
        assert resp.status_code == 404, resp.text


def test_legacy_operator_commands_post_no_longer_matches_any_route() -> None:
    """POST /api/v1/operator/commands was fully deleted (the create_command_adapters_router
    function no longer registers a POST handler for this exact path; only
    GET /api/v1/operator/commands/{command_id} remains, which requires a path
    segment and does not match a bare POST to the parent path).
    """
    with tempfile.TemporaryDirectory() as td:
        cmd_store = CommandStore(os.path.join(td, "main_commands.jsonl"))
        client = TestClient(_build_app(cmd_store))

        resp = client.post(
            "/api/v1/operator/commands",
            headers={**AUTH_HEADER, "Idempotency-Key": "legacy-operator-commands-post-01"},
            json={
                "command": "RejectDecision",
                "target": {"type": "ApprovalDecision", "id": "dec-legacy-1"},
                "params": {"decision_id": "dec-legacy-1", "rejection_reason": "legacy retirement probe"},
                "audit_context": {"reason": "legacy retirement probe"},
            },
        )
        assert resp.status_code == 404, resp.text


def test_action_catalog_read_route_still_serves_real_catalog() -> None:
    """GET /bff/actions (the surviving read route) still returns 200 with a
    real, non-empty action catalog body."""
    with tempfile.TemporaryDirectory() as td:
        cmd_store = CommandStore(os.path.join(td, "main_commands.jsonl"))
        client = TestClient(_build_app(cmd_store))

        resp = client.get("/bff/actions", headers=AUTH_HEADER)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "catalog" in body
        assert isinstance(body["catalog"], list)
        assert len(body["catalog"]) > 0, "action catalog must be non-empty"

        entry = body["catalog"][0]
        for key in ("action_id", "entity_type", "endpoint", "risk_level"):
            assert key in entry, f"catalog entry missing expected key {key!r}: {entry}"

        action_ids = {e["action_id"] for e in body["catalog"]}
        assert "PersonaAction" in action_ids


def test_persona_action_submitted_via_canonical_route_then_status_readable() -> None:
    """The most important test: proves that persona domain actions (which
    flow through the generic command-adapter entity-spec mechanism) still
    work correctly after the legacy routes were retired, dispatched
    exclusively through the surviving canonical POST /bff/v1/commands route,
    and that the surviving GET /api/v1/operator/commands/{command_id} read
    route resolves the resulting command_id for a definitive 200 (not a
    "route not found" 404).
    """
    with tempfile.TemporaryDirectory() as td:
        cmd_store = CommandStore(os.path.join(td, "main_commands.jsonl"))
        client = TestClient(_build_app(cmd_store))

        persona_id = "persona-legacy-retirement-1"
        submit_resp = client.post(
            "/bff/v1/commands",
            headers={**AUTH_HEADER, "Idempotency-Key": "persona-action-e2e-01"},
            json={
                "command": "PersonaAction",
                "target": {"type": "Persona", "id": persona_id},
                "action": "run_eval",
                "params": {
                    "action_id": "run_eval",
                    "entity_type": "persona",
                    "entity_id": persona_id,
                },
                "audit_context": {"reason": "test coverage for legacy command write retirement"},
            },
        )
        assert submit_resp.status_code == 202, submit_resp.text
        submit_body = submit_resp.json()
        assert submit_body["status"] == "accepted", submit_body
        data = submit_body["data"]
        assert data["command"] == "PersonaAction"
        command_id = data["receipt_id"]
        assert command_id, submit_body
        assert data["command_id"] == command_id

        status_resp = client.get(
            f"/api/v1/operator/commands/{command_id}",
            headers=AUTH_HEADER,
        )
        assert status_resp.status_code == 200, status_resp.text
        status_body = status_resp.json()
        assert status_body["command_id"] == command_id
        assert status_body["type"] == "PersonaAction"
        assert status_body["target"]["id"] == persona_id
        assert status_body["status"] in (
            "submitted",
            "processing",
            "succeeded",
            "failed",
        ), status_body


def test_operator_commands_status_get_route_resolves_for_unknown_id() -> None:
    """GET /api/v1/operator/commands/{command_id} is still a real, registered
    route: passing a fake/unknown command_id must resolve to a resource-level
    404 ("command not found" style error body from the service layer), which
    is fundamentally different from the routing-level 404s asserted above for
    the two retired routes (there, no route matches the path shape at all).
    """
    with tempfile.TemporaryDirectory() as td:
        cmd_store = CommandStore(os.path.join(td, "main_commands.jsonl"))
        client = TestClient(_build_app(cmd_store))

        resp = client.get(
            "/api/v1/operator/commands/does-not-exist-12345",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert "detail" not in body, body
        error = body.get("error")
        assert error is not None, body
        assert error.get("code") == "RESOURCE_NOT_FOUND", body
        assert "does-not-exist-12345" in str(error.get("message")), body

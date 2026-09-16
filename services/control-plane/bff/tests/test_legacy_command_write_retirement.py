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

This test stands up the FULL ``services.control_plane.bff.main`` FastAPI app
(not a synthetic minimal router), matching the convention established by
``test_command_adapters_router.py::test_main_app_final_command_submission_regression``.
"""
from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

AUTH_HEADER = {"Authorization": "Bearer op-1:operator,approver:mfa"}


def test_create_action_command_router_is_a_zero_route_stub() -> None:
    """create_action_command_router used to register the retired
    POST /bff/actions/{type}/{id}/{action} route. It is kept as a callable
    no-op stub (rather than deleted outright) only so that existing callers
    of this exact symbol -- command_adapters/__init__.py's re-export and
    test_v5_interventions.py's direct call -- do not need to change; it must
    register zero routes.
    """
    from services.control_plane.bff.command_adapters.router import create_action_command_router

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
    from services.control_plane.bff.main import app as main_app, command_store as main_command_store

    with tempfile.TemporaryDirectory() as td:
        main_command_store.file_path = os.path.join(td, "main_commands.jsonl")
        client = TestClient(main_app)

        resp = client.post(
            "/bff/actions/persona/persona-1/run_eval",
            headers={**AUTH_HEADER, "Idempotency-Key": "legacy-action-route-01"},
            json={"reason": "legacy retirement probe"},
        )
        # Observed: 404. There is no partial path match for this 4-segment
        # shape anywhere else in the app, so FastAPI returns its default
        # "Not Found" response for an unmatched route.
        assert resp.status_code == 404, resp.text


def test_legacy_operator_commands_post_no_longer_matches_any_route() -> None:
    """POST /api/v1/operator/commands was fully deleted (the create_command_adapters_router
    function no longer registers a POST handler for this exact path; only
    GET /api/v1/operator/commands/{command_id} remains, which requires a path
    segment and does not match a bare POST to the parent path).
    """
    from services.control_plane.bff.main import app as main_app, command_store as main_command_store

    with tempfile.TemporaryDirectory() as td:
        main_command_store.file_path = os.path.join(td, "main_commands.jsonl")
        client = TestClient(main_app)

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
        # Observed: 404. No route (GET-only child route requires a
        # {command_id} path segment, so a bare POST to the parent path
        # doesn't even trip a 405 method-not-allowed on that route) matches
        # this exact path, so FastAPI returns its default "Not Found".
        assert resp.status_code == 404, resp.text


def test_action_catalog_read_route_still_serves_real_catalog() -> None:
    """GET /bff/actions (the surviving read route) still returns 200 with a
    real, non-empty action catalog body."""
    from services.control_plane.bff.main import app as main_app, command_store as main_command_store

    with tempfile.TemporaryDirectory() as td:
        main_command_store.file_path = os.path.join(td, "main_commands.jsonl")
        client = TestClient(main_app)

        resp = client.get("/bff/actions", headers=AUTH_HEADER)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Real shape assertions, not just "it's a 200".
        assert "catalog" in body
        assert isinstance(body["catalog"], list)
        assert len(body["catalog"]) > 0, "action catalog must be non-empty"

        entry = body["catalog"][0]
        for key in ("action_id", "entity_type", "endpoint", "risk_level"):
            assert key in entry, f"catalog entry missing expected key {key!r}: {entry}"

        # PersonaAction must still be present in the catalog since it is the
        # action dispatched end to end below via the canonical write route.
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

    PersonaAction's action_catalog.py entry declares
    requires_confirm_token=False, requires_approval=False,
    requires_two_man=False, so a plain envelope with no extra evidence is
    sufficient for admission to succeed (202) even though the persona id
    used here ("persona-legacy-retirement-1") is not backed by a real
    persona resource -- admission only needs the command to be *accepted*,
    not necessarily executed to a terminal "succeeded" status downstream.
    """
    from services.control_plane.bff.main import app as main_app, command_store as main_command_store

    with tempfile.TemporaryDirectory() as td:
        main_command_store.file_path = os.path.join(td, "main_commands.jsonl")
        client = TestClient(main_app)

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
        # command_id is dual-written under both keys; verify consistency.
        assert data["command_id"] == command_id

        # Now resolve the command's status via the surviving read route.
        # This is a real route match (200 with a real status payload), not
        # just "didn't 404 as unmatched route".
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
    We confirm this is a resource-level 404 by checking the error body shape
    rather than only the status code.
    """
    from services.control_plane.bff.main import app as main_app, command_store as main_command_store

    with tempfile.TemporaryDirectory() as td:
        main_command_store.file_path = os.path.join(td, "main_commands.jsonl")
        client = TestClient(main_app)

        resp = client.get(
            "/api/v1/operator/commands/does-not-exist-12345",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404, resp.text
        body = resp.json()
        # FastAPI's default "route not found" 404 body is exactly
        # {"detail": "Not Found"}. The actual observed body here is instead
        # the app's structured error envelope
        # {"error": {"code": "RESOURCE_NOT_FOUND", "message": "Command
        # does-not-exist-12345 not found", ...}, "meta": {...}}, which is
        # produced by the BFF error-handling machinery wrapping the
        # service-layer HTTPException -- proof the route itself matched and
        # this is a resource-level 404, not a routing-level one.
        assert "detail" not in body, body
        error = body.get("error")
        assert error is not None, body
        assert error.get("code") == "RESOURCE_NOT_FOUND", body
        assert "does-not-exist-12345" in str(error.get("message")), body

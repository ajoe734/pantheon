"""U8A BFF-level contract test: Evolution Program owner data contract.

Exercises the real ``create_evolution_programs_router`` wired to a fake
``EvolutionProgramCommandPort``-shaped double (mirroring the fakes in
``test_router.py``/``test_evolution_router.py``), proving:

- PATCH with any field other than ``name`` is rejected with 422 before the
  command port is ever called (the allowlist is enforced at the router).
- A stale/wrong ``revision`` precondition on PATCH surfaces the command
  port's 409 conflict.
- Create rejects a client-supplied ``status`` (or any other unsupported
  field) with 422.
- The ``/actions/{action_id}`` endpoint is honest about being unavailable
  for every action name in the lifecycle contract table (never a fabricated
  ``prog-001``/``executed``/``active``) — proven directly against
  ``EvolutionCommandAdapter._execute_program_action``, the actual production
  adapter this endpoint dispatches through.
"""
from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Dict, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from evolution.router import create_evolution_programs_router  # noqa: E402
from ports.evolution_program_commands import (  # noqa: E402
    EvolutionProgramCommandError,
    EvolutionProgramConflictError,
    EvolutionProgramNotFoundError,
    EvolutionProgramValidationError,
)
from services.control_plane.bff.command_adapters.base import ActionUnavailableError  # noqa: E402
from services.control_plane.bff.command_adapters.evolution_adapter import (  # noqa: E402
    EvolutionCommandAdapter,
)


class _FakeIdentity:
    def __init__(self, operator_id: str = "operator-1") -> None:
        self.operator_id = operator_id
        self.roles = {"operator", "viewer"}
        self.claims = {"tenant_id": "tenant-a"}


class _FakeReadStore:
    """Read-only double: no create/patch methods exist here at all — the
    U8A contract requires writes to go through program_commands instead."""

    def __init__(self) -> None:
        self._programs: Dict[str, Dict[str, Any]] = {}

    def seed(self, program: Dict[str, Any]) -> None:
        self._programs[program["program_id"]] = program

    def list_evolution_programs(self):
        return list(self._programs.values())

    def get_evolution_program(self, program_id: Optional[str]):
        return self._programs.get(program_id) if program_id else None

    def list_evolution_program_runs(self, program_id: str):
        return []

    def list_evolution_program_candidates(self, program_id: str):
        return []


class _FakeProgramCommandPort:
    """Stub standing in for ``EvolutionServiceProgramCommandPort`` — models
    the Evolution service owner API's create/PATCH(name) semantics without a
    real HTTP call, exactly like ``client.py``-shaped doubles used elsewhere
    in this test suite."""

    def __init__(self, read_store: _FakeReadStore) -> None:
        self._read_store = read_store
        self._programs: Dict[str, Dict[str, Any]] = {}

    async def create_program(self, *, tenant_id, actor_id, name, idempotency_key):
        program_id = f"evp-{uuid.uuid4().hex[:8]}"
        program = {
            "program_id": program_id,
            "tenant_id": tenant_id,
            "created_by": actor_id,
            "name": name,
            "status": "draft",
            "revision": 1,
            "created_at": "2026-09-14T00:00:00Z",
            "updated_at": "2026-09-14T00:00:00Z",
            "run_ids": [],
            "candidate_ids": [],
        }
        self._programs[program_id] = program
        self._read_store.seed(program)
        return program

    async def patch_program_name(self, *, tenant_id, actor_id, program_id, name, expected_revision, idempotency_key):
        current = self._programs.get(program_id)
        if current is None:
            raise EvolutionProgramNotFoundError(f"Evolution program not found: {program_id}")
        if int(current.get("revision") or 0) != expected_revision:
            raise EvolutionProgramConflictError(f"Evolution program {program_id} was modified concurrently")
        updated = dict(current)
        updated["name"] = name
        updated["revision"] = int(current["revision"]) + 1
        updated["updated_at"] = "2026-09-14T01:00:00Z"
        self._programs[program_id] = updated
        self._read_store.seed(updated)
        return updated


def _build_app():
    read_store = _FakeReadStore()
    program_commands = _FakeProgramCommandPort(read_store)

    def _submit_program_action_unavailable(entity_type, entity_id, action_id, resolved_key, identity, payload):
        # Mirrors production wiring: dispatch reaches the real
        # EvolutionCommandAdapter, which raises ActionUnavailableError for
        # every program action in U8A (see the direct adapter tests below).
        # A router-level double surfaces that as an HTTP 422 the same way
        # the production BFF error-handling pipeline does.
        adapter = EvolutionCommandAdapter()
        adapter.execute(
            command_id="cmd-1",
            command_type="EvolutionProgramAction",
            params={"action_id": action_id, "program_id": entity_id},
        )

    router = create_evolution_programs_router(
        get_read_store=lambda: read_store,
        extract_identity=lambda auth: _FakeIdentity(),
        submit_program_action=_submit_program_action_unavailable,
        program_commands=program_commands,
    )
    app = FastAPI()

    @app.exception_handler(ActionUnavailableError)
    async def _handle_unavailable(request, exc: ActionUnavailableError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.downstream_status,
            content={"code": exc.error_code, "message": str(exc), "action_id": exc.action_id},
        )

    @app.exception_handler(EvolutionProgramCommandError)
    async def _handle_program_command_error(request, exc: EvolutionProgramCommandError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"message": str(exc)})

    app.include_router(router)
    return app, read_store, program_commands


@pytest.fixture
def client():
    app, read_store, program_commands = _build_app()
    return TestClient(app), read_store, program_commands


def test_create_rejects_client_supplied_status(client) -> None:
    test_client, _read_store, _commands = client
    resp = test_client.post(
        "/bff/evolution-programs", json={"name": "Alpha", "status": "active"},
    )
    assert resp.status_code == 422


def test_create_rejects_client_supplied_actor_or_tenant_id_override(client) -> None:
    test_client, _read_store, _commands = client
    resp = test_client.post(
        "/bff/evolution-programs",
        json={"name": "Alpha", "actor_id": "someone-else"},
    )
    assert resp.status_code == 422


def test_create_happy_path_starts_draft(client) -> None:
    test_client, _read_store, _commands = client
    resp = test_client.post("/bff/evolution-programs", json={"name": "Alpha Program"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["revision"] == 1
    assert body["name"] == "Alpha Program"


def test_patch_rejects_unknown_fields_422(client) -> None:
    test_client, read_store, _commands = client
    create_resp = test_client.post("/bff/evolution-programs", json={"name": "Beta"})
    program_id = create_resp.json()["program_id"]

    resp = test_client.patch(
        f"/bff/evolution-programs/{program_id}",
        json={"name": "Beta v2", "revision": 1, "status": "active"},
    )
    assert resp.status_code == 422
    # The record is unchanged.
    assert read_store.get_evolution_program(program_id)["name"] == "Beta"


def test_patch_status_only_is_rejected_422_not_a_pause(client) -> None:
    """Transformation note (per the task's explicit instruction, preserving
    business intent instead of deleting the assertion): a direct
    ``PATCH .../{program_id}`` with a ``status`` field used to be accepted
    and applied straight onto the read surface (the exact defect this
    decision record calls out). Under the now-adopted U8A contract, ``name``
    is the only allowlisted PATCH field, so this must now assert 422 — the
    real "pause a program" business capability is asserted separately via
    the ``pause_program`` *action* endpoint below
    (``test_pause_program_action_is_honestly_unavailable``), which in U8A
    must assert the honest "unavailable" outcome (real effects are U8B's
    obligation). When U8B lands real pause_program effects, that action
    test's assertion should flip from "unavailable" to a real positive
    "program is now paused" assertion; this PATCH test's 422 assertion does
    not change, since status will never become PATCH-allowlisted.
    """
    test_client, read_store, _commands = client
    create_resp = test_client.post("/bff/evolution-programs", json={"name": "Gamma"})
    program_id = create_resp.json()["program_id"]

    resp = test_client.patch(
        f"/bff/evolution-programs/{program_id}",
        json={"revision": 1, "status": "paused"},
    )
    assert resp.status_code == 422
    assert read_store.get_evolution_program(program_id)["status"] == "draft"


def test_patch_missing_revision_precondition_422(client) -> None:
    test_client, _read_store, _commands = client
    create_resp = test_client.post("/bff/evolution-programs", json={"name": "Delta"})
    program_id = create_resp.json()["program_id"]
    resp = test_client.patch(f"/bff/evolution-programs/{program_id}", json={"name": "Delta v2"})
    assert resp.status_code == 422


def test_patch_stale_revision_conflict_409(client) -> None:
    test_client, read_store, _commands = client
    create_resp = test_client.post("/bff/evolution-programs", json={"name": "Epsilon"})
    program_id = create_resp.json()["program_id"]

    first_patch = test_client.patch(
        f"/bff/evolution-programs/{program_id}",
        json={"name": "Epsilon v2", "revision": 1},
    )
    assert first_patch.status_code == 200
    assert first_patch.json()["data"]["revision"] == 2

    stale_patch = test_client.patch(
        f"/bff/evolution-programs/{program_id}",
        json={"name": "Should not apply", "revision": 1},
    )
    assert stale_patch.status_code == 409
    assert read_store.get_evolution_program(program_id)["name"] == "Epsilon v2"


# ---------------------------------------------------------------------------
# Program lifecycle actions: honest "unavailable" for U8A, never fabricated
# success. Exercised directly against the real production adapter
# (EvolutionCommandAdapter._execute_program_action) for every canonical
# action name in evolution-lifecycle.md §4's table.
# ---------------------------------------------------------------------------

_CONTRACT_ACTIONS = [
    "submit_evolution_review",
    "approve_program",
    "pause_program",
    "resume_program",
    "complete_program",
    "retire_program",
    "stop",
    "freeze_generation",
    "promote_candidate_paper",
    "promote_candidate_live",
    "approve_mutation",
    "reject_mutation",
]


@pytest.mark.parametrize("action_id", _CONTRACT_ACTIONS)
def test_program_action_is_honestly_unavailable_not_fabricated(action_id: str) -> None:
    adapter = EvolutionCommandAdapter()
    with pytest.raises(ActionUnavailableError) as exc_info:
        adapter.execute(
            command_id="cmd-1",
            command_type="EvolutionProgramAction",
            params={"action_id": action_id, "program_id": "evp-real-id"},
        )
    err = exc_info.value
    assert err.downstream_status == 422
    assert "evp-real-id" in str(err)
    assert "prog-001" not in str(err)
    assert err.entity_type == "EvolutionProgram"


def test_program_action_never_fabricates_prog_001_when_id_missing() -> None:
    adapter = EvolutionCommandAdapter()
    with pytest.raises(ValueError) as exc_info:
        adapter.execute(
            command_id="cmd-1",
            command_type="EvolutionProgramAction",
            params={"action_id": "pause_program"},
        )
    assert "prog-001" not in str(exc_info.value)


def test_pause_program_action_is_honestly_unavailable_via_router(client) -> None:
    """The BFF ``/actions/pause_program`` endpoint reports the same honest
    "unavailable" outcome the direct adapter test above proves — never a
    fabricated ``active``/``executed`` status. See the docstring on
    ``test_patch_status_only_is_rejected_422_not_a_pause`` for why "pausing a
    program" is asserted here (the action) and not via PATCH."""
    test_client, _read_store, _commands = client
    create_resp = test_client.post("/bff/evolution-programs", json={"name": "Zeta"})
    program_id = create_resp.json()["program_id"]

    resp = test_client.post(
        f"/bff/evolution-programs/{program_id}/actions/pause_program", json={},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "prog-001" not in str(body)
    assert body.get("action_id") == "pause_program"

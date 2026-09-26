"""Standalone contract tests for the prepared Evolution Programs router.

These tests build `create_evolution_programs_router()` into a bare FastAPI
app with fakes for every injected dependency -- they do not import
`main.py` and do not touch the live `/bff/evolution-programs*` routes
main.py currently serves. They characterize router.py's own behavior
against CHARACTERIZATION.md so a reviewer (and the future cutover task)
can see it holds the documented contract before it is ever wired in.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


from evolution.router import create_evolution_programs_router  # noqa: E402
from ports.evolution_program_commands import (  # noqa: E402
    EvolutionProgramCommandError,
    EvolutionProgramConflictError,
    EvolutionProgramNotFoundError,
)


class _FakeIdentity:
    def __init__(self, operator_id: str = "operator-1") -> None:
        self.operator_id = operator_id
        self.claims: Dict[str, Any] = {}


class _FakeReadStore:
    """Minimal durable-store double covering exactly the functions the
    router calls, mirroring read_store.py's real signatures/behavior.

    Read-only: there is no ``create_evolution_program``/
    ``patch_evolution_program`` here at all -- the U8A owner contract routes
    every write through the injected ``program_commands`` port instead.
    ``seed`` is a test-setup helper standing in for what the command port's
    fake would durably persist.
    """

    def __init__(self) -> None:
        self._programs: Dict[str, Dict[str, Any]] = {}
        self._decisions: List[Dict[str, Any]] = []

    def seed(self, program: Dict[str, Any]) -> None:
        self._programs[program["program_id"]] = program

    def list_evolution_programs(self) -> List[Dict[str, Any]]:
        items = list(self._programs.values())
        return sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not program_id:
            return None
        return self._programs.get(program_id)

    def list_evolution_program_runs(self, program_id: str) -> List[Dict[str, Any]]:
        if program_id not in self._programs:
            return []
        return [d for d in self._decisions if d.get("program_id") == program_id]

    def list_evolution_program_candidates(self, program_id: str) -> List[Dict[str, Any]]:
        if program_id not in self._programs:
            return []
        return [d for d in self._decisions if d.get("program_id") == program_id and d.get("status") == "pending"]


class _FakeProgramCommandPort:
    """Stub standing in for ``EvolutionServiceProgramCommandPort`` -- models
    the Evolution service owner API's create/PATCH(name) semantics without a
    real HTTP call, matching the fake used in
    ``test_evolution_program_owner_contract.py``."""

    def __init__(self, read_store: _FakeReadStore) -> None:
        self._read_store = read_store
        self._seq = 0

    async def create_program(self, *, tenant_id, actor_id, name, idempotency_key):
        self._seq += 1
        program_id = f"p{self._seq}"
        program = {
            "id": program_id,
            "program_id": program_id,
            "tenant_id": tenant_id,
            "name": name,
            "status": "draft",
            "revision": 1,
            "created_at": "2026-08-28T00:00:00Z",
            "updated_at": "2026-08-28T00:00:00Z",
            "created_by": actor_id,
        }
        self._read_store.seed(program)
        return program

    async def patch_program_name(self, *, tenant_id, actor_id, program_id, name, expected_revision, idempotency_key):
        current = self._read_store.get_evolution_program(program_id)
        if current is None:
            raise EvolutionProgramNotFoundError(f"Evolution program not found: {program_id}")
        if int(current.get("revision") or 0) != expected_revision:
            raise EvolutionProgramConflictError(f"Evolution program {program_id} was modified concurrently")
        updated = dict(current)
        updated["name"] = name
        updated["revision"] = int(current["revision"]) + 1
        updated["updated_at"] = "2026-08-28T01:00:00Z"
        updated["updated_by"] = actor_id
        self._read_store.seed(updated)
        return updated


def _bff_error(status_code, code, message, reason, **extra):
    return HTTPException(status_code=status_code, detail={"code": code.value, "message": message, "reason": reason, **extra})


def _build_app(
    read_store: _FakeReadStore,
    *,
    submit_program_action=None,
    program_commands: Optional[_FakeProgramCommandPort] = None,
) -> TestClient:
    calls: Dict[str, Any] = {}

    def require_operator_role(identity):
        calls["operator_role_checked"] = True

    resolved_commands = program_commands if program_commands is not None else _FakeProgramCommandPort(read_store)

    router = create_evolution_programs_router(
        get_read_store=lambda: read_store,
        extract_identity=lambda authorization: _FakeIdentity(),
        require_read_role=lambda identity: None,
        require_operator_role=require_operator_role,
        bff_error=_bff_error,
        utc_now=lambda: "2026-08-28T00:00:00Z",
        submit_program_action=submit_program_action,
        program_commands=resolved_commands,
    )
    app = FastAPI()

    @app.exception_handler(EvolutionProgramCommandError)
    async def _handle_program_command_error(request, exc: EvolutionProgramCommandError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"message": str(exc)})

    app.include_router(router)
    client = TestClient(app)
    client._pantheon_calls = calls  # type: ignore[attr-defined]
    return client


def test_list_empty_uses_items_envelope():
    client = _build_app(_FakeReadStore())
    resp = client.get("/bff/evolution-programs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["page_info"] == {"next_page_token": None}
    assert "surfaces" in body["meta"]
    assert body["meta"]["snapshot_at"] == "2026-08-28T00:00:00Z"


def test_create_requires_name_and_persists_durably():
    store = _FakeReadStore()
    client = _build_app(store)

    bad = client.post("/bff/evolution-programs", json={})
    assert bad.status_code == 422

    created = client.post("/bff/evolution-programs", json={"name": "Alpha"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Alpha"
    assert body["program_id"] in store._programs
    assert client._pantheon_calls.get("operator_role_checked") is True

    # Durable: a fresh router instance over the same store sees the write.
    other_client = _build_app(store)
    listed = other_client.get("/bff/evolution-programs").json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["program_id"] == body["program_id"]


def _seed_program(store: _FakeReadStore, program_id: str, name: str, *, created_at: str, revision: int = 1) -> None:
    store.seed({
        "id": program_id,
        "program_id": program_id,
        "name": name,
        "status": "draft",
        "revision": revision,
        "created_at": created_at,
        "updated_at": created_at,
        "created_by": "op",
    })


def test_get_detail_404_and_data_envelope():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store)

    missing = client.get("/bff/evolution-programs/does-not-exist")
    assert missing.status_code == 404

    found = client.get("/bff/evolution-programs/p1")
    assert found.status_code == 200
    assert found.json()["data"]["program_id"] == "p1"


def test_patch_updates_whitelisted_fields_only():
    """PATCH accepts only ``name`` (plus the ``revision`` CAS precondition);
    every other field -- including the old ``status`` write path -- is
    rejected with 422 before the command port is ever called, and a
    whitelisted ``name`` update is durably persisted through the
    ``program_commands`` port."""
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store)

    resp = client.patch("/bff/evolution-programs/p1", json={"status": "paused", "unexpected_field": "ignored"})
    assert resp.status_code == 422

    resp = client.patch("/bff/evolution-programs/p1", json={"name": "P1 Renamed", "revision": 1})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "P1 Renamed"
    assert "unexpected_field" not in data
    assert store.get_evolution_program("p1")["name"] == "P1 Renamed"


def test_status_filter_is_csv_case_insensitive():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    store._programs["p1"]["status"] = "Active"
    _seed_program(store, "p2", "P2", created_at="2026-01-02T00:00:00Z")
    store._programs["p2"]["status"] = "paused"
    client = _build_app(store)

    resp = client.get("/bff/evolution-programs", params={"status": "active,PAUSED"})
    ids = {item["program_id"] for item in resp.json()["items"]}
    assert ids == {"p1", "p2"}

    resp = client.get("/bff/evolution-programs", params={"status": "archived"})
    assert resp.json()["items"] == []


def test_runs_and_candidates_404_on_missing_program():
    client = _build_app(_FakeReadStore())
    assert client.get("/bff/evolution-programs/missing/runs").status_code == 404
    assert client.get("/bff/evolution-programs/missing/candidates").status_code == 404


def test_action_without_injected_dispatch_is_501():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store, submit_program_action=None)

    resp = client.post("/bff/evolution-programs/p1/actions/approve", json={})
    assert resp.status_code == 501


def test_action_dispatches_through_injected_callable():
    store = _FakeReadStore()
    _seed_program(store, "p1", "P1", created_at="2026-01-01T00:00:00Z")

    captured = {}

    def submit_program_action(entity_type, entity_id, action_id, resolved_key, identity, payload):
        captured.update(
            entity_type=entity_type,
            entity_id=entity_id,
            action_id=action_id,
            resolved_key=resolved_key,
            payload=payload,
        )
        return {"status": "accepted"}

    client = _build_app(store, submit_program_action=submit_program_action)
    resp = client.post("/bff/evolution-programs/p1/actions/approve", json={"reason": "looks good"})
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}
    assert captured == {
        "entity_type": "EvolutionProgram",
        "entity_id": "p1",
        "action_id": "approve",
        "resolved_key": "",
        "payload": {"reason": "looks good"},
    }

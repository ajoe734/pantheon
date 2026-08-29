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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from evolution.router import create_evolution_programs_router  # noqa: E402


class _FakeIdentity:
    def __init__(self, operator_id: str = "operator-1") -> None:
        self.operator_id = operator_id


class _FakeReadStore:
    """Minimal durable-store double covering exactly the functions the
    router calls, mirroring read_store.py's real signatures/behavior."""

    def __init__(self) -> None:
        self._programs: Dict[str, Dict[str, Any]] = {}
        self._decisions: List[Dict[str, Any]] = []

    def list_evolution_programs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        items = list(self._programs.values())
        if status:
            items = [i for i in items if i.get("status") == status]
        return sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not program_id:
            return None
        return self._programs.get(program_id)

    def create_evolution_program(self, *, program_id, name, actor_id, created_at=None, params=None):
        record = {
            "id": program_id,
            "program_id": program_id,
            "name": name,
            "status": "active",
            "params": params or {},
            "created_at": created_at,
            "updated_at": created_at,
            "created_by": actor_id,
        }
        self._programs[program_id] = record
        return record

    def patch_evolution_program(self, program_id, *, patch, actor_id, updated_at=None):
        record = self._programs.get(program_id)
        if record is None:
            return None
        for field in ("name", "status", "params"):
            if field in patch:
                record[field] = patch[field]
        record["updated_at"] = updated_at
        record["updated_by"] = actor_id
        return record

    def list_evolution_program_runs(self, program_id: str) -> List[Dict[str, Any]]:
        if program_id not in self._programs:
            return []
        return [d for d in self._decisions if d.get("program_id") == program_id]

    def list_evolution_program_candidates(self, program_id: str) -> List[Dict[str, Any]]:
        if program_id not in self._programs:
            return []
        return [d for d in self._decisions if d.get("program_id") == program_id and d.get("status") == "pending"]


def _bff_error(status_code, code, message, reason, **extra):
    return HTTPException(status_code=status_code, detail={"code": code.value, "message": message, "reason": reason, **extra})


def _build_app(read_store: _FakeReadStore, *, submit_program_action=None) -> TestClient:
    calls: Dict[str, Any] = {}

    def require_operator_role(identity):
        calls["operator_role_checked"] = True

    router = create_evolution_programs_router(
        get_read_store=lambda: read_store,
        extract_identity=lambda authorization: _FakeIdentity(),
        require_read_role=lambda identity: None,
        require_operator_role=require_operator_role,
        bff_error=_bff_error,
        utc_now=lambda: "2026-08-28T00:00:00Z",
        submit_program_action=submit_program_action,
    )
    app = FastAPI()
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


def test_get_detail_404_and_data_envelope():
    store = _FakeReadStore()
    store.create_evolution_program(program_id="p1", name="P1", actor_id="op", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store)

    missing = client.get("/bff/evolution-programs/does-not-exist")
    assert missing.status_code == 404

    found = client.get("/bff/evolution-programs/p1")
    assert found.status_code == 200
    assert found.json()["data"]["program_id"] == "p1"


def test_patch_updates_whitelisted_fields_only():
    store = _FakeReadStore()
    store.create_evolution_program(program_id="p1", name="P1", actor_id="op", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store)

    resp = client.patch("/bff/evolution-programs/p1", json={"status": "paused", "unexpected_field": "ignored"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "paused"
    assert "unexpected_field" not in data


def test_status_filter_is_csv_case_insensitive():
    store = _FakeReadStore()
    store.create_evolution_program(program_id="p1", name="P1", actor_id="op", created_at="2026-01-01T00:00:00Z")
    store._programs["p1"]["status"] = "Active"
    store.create_evolution_program(program_id="p2", name="P2", actor_id="op", created_at="2026-01-02T00:00:00Z")
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
    store.create_evolution_program(program_id="p1", name="P1", actor_id="op", created_at="2026-01-01T00:00:00Z")
    client = _build_app(store, submit_program_action=None)

    resp = client.post("/bff/evolution-programs/p1/actions/approve", json={})
    assert resp.status_code == 501


def test_action_dispatches_through_injected_callable():
    store = _FakeReadStore()
    store.create_evolution_program(program_id="p1", name="P1", actor_id="op", created_at="2026-01-01T00:00:00Z")

    captured = {}

    def submit_program_action(entity_type, entity_id, action_id, identity, payload):
        captured.update(entity_type=entity_type, entity_id=entity_id, action_id=action_id, payload=payload)
        return {"status": "accepted"}

    client = _build_app(store, submit_program_action=submit_program_action)
    resp = client.post("/bff/evolution-programs/p1/actions/approve", json={"reason": "looks good"})
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}
    assert captured == {
        "entity_type": "EvolutionProgram",
        "entity_id": "p1",
        "action_id": "approve",
        "payload": {"reason": "looks good"},
    }

"""Program aggregate owner-store/service/router tests (U8A).

Exercises both backends:

- JSON dev backend (``EVOLUTION_PROGRAM_STORE_BACKEND=json``, the default):
  runs unconditionally, using a local temp file mirroring
  ``services/governance/record_store.py::JsonGovernanceRecordStore``.
- Real PostgreSQL (``EVOLUTION_PROGRAM_STORE_BACKEND=postgres``): gated on
  ``TEST_DATABASE_URL`` per the same convention as
  ``services/registry/test_owner_durability.py`` — skipped cleanly (with an
  explicit skip reason, not silently) when no live database is configured in
  this environment.

Both backend test classes run the identical scenario matrix: create/list/get/
patch happy path; 20 concurrent same-idempotency-key creates -> exactly one
program, all callers get the identical result; a changed payload under the
same key -> divergent-replay 409; PATCH with a stale/wrong expected_revision
-> 409 with no partial write; PATCH with any field other than name -> 422 at
the service boundary (ProgramValidationError from the request itself, since
the service method signature only accepts ``name``); restart/crash
simulation (a fresh store instance against the same durable storage sees the
prior commit, and a same-key replay after "restart" does not re-run); missing/
foreign-tenant read is a non-disclosing "not found".
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.evolution.program_router import create_program_router
from services.evolution.program_service import (
    ProgramConflictError,
    ProgramDivergentReplayError,
    ProgramNotFoundError,
    ProgramService,
    ProgramValidationError,
)
from services.evolution.program_store import JsonProgramStore, ProgramStore


def _run_scenarios(make_store) -> None:
    """Run the full scenario matrix against a fresh store from ``make_store()``."""

    # -- Create / list / get / patch happy path --------------------------
    store: ProgramStore = make_store()
    service = ProgramService(store)

    program, replayed = service.create_program(
        tenant_id="tenant-a", actor_id="actor-1", name="  Alpha Program  ",
    )
    assert replayed is False
    assert program["status"] == "draft"
    assert program["revision"] == 1
    assert program["name"] == "Alpha Program"
    assert program["tenant_id"] == "tenant-a"
    assert program["created_by"] == "actor-1"
    assert program["run_ids"] == []
    assert program["candidate_ids"] == []
    program_id = program["program_id"]
    assert program_id

    fetched = service.get_program(tenant_id="tenant-a", program_id=program_id)
    assert fetched == program

    listed = service.list_programs(tenant_id="tenant-a")
    assert [p["program_id"] for p in listed] == [program_id]

    patched, replayed = service.patch_program_name(
        tenant_id="tenant-a", actor_id="actor-1", program_id=program_id,
        name="Alpha Program v2", expected_revision=1,
    )
    assert replayed is False
    assert patched["name"] == "Alpha Program v2"
    assert patched["revision"] == 2
    assert patched["status"] == "draft"  # never changed by a metadata patch

    # -- PATCH with any field other than name is rejected at the service
    # boundary: patch_program_name's signature only accepts name (+ the
    # expected_revision precondition), so a caller cannot smuggle status/
    # params/etc through it at all. The BFF and Evolution HTTP routers
    # additionally reject unknown fields with 422 (see
    # program_router.ProgramPatchRequest / evolution/router.py).
    with pytest.raises(TypeError):
        service.patch_program_name(  # type: ignore[call-arg]
            tenant_id="tenant-a", actor_id="actor-1", program_id=program_id,
            name="x", expected_revision=2, status="active",
        )

    # -- Stale revision precondition -> conflict, no partial write --------
    before = service.get_program(tenant_id="tenant-a", program_id=program_id)
    with pytest.raises(ProgramConflictError):
        service.patch_program_name(
            tenant_id="tenant-a", actor_id="actor-1", program_id=program_id,
            name="Should not apply", expected_revision=1,
        )
    after = service.get_program(tenant_id="tenant-a", program_id=program_id)
    assert after == before  # no partial write

    # -- Missing / foreign-tenant read: non-disclosing "not found" --------
    assert service.get_program(tenant_id="tenant-a", program_id="does-not-exist") is None
    assert service.get_program(tenant_id="tenant-foreign", program_id=program_id) is None
    with pytest.raises(ProgramNotFoundError):
        service.patch_program_name(
            tenant_id="tenant-foreign", actor_id="actor-1", program_id=program_id,
            name="hijack", expected_revision=2,
        )

    # -- Divergent replay under the same idempotency key -> 409, no second
    # record ---------------------------------------------------------
    key = f"idem-{uuid.uuid4().hex}"
    first, first_replay = service.create_program(
        tenant_id="tenant-b", actor_id="actor-2", name="Replay Program", idempotency_key=key,
    )
    assert first_replay is False
    same, same_replay = service.create_program(
        tenant_id="tenant-b", actor_id="actor-2", name="Replay Program", idempotency_key=key,
    )
    assert same_replay is True
    assert same == first
    with pytest.raises(ProgramDivergentReplayError):
        service.create_program(
            tenant_id="tenant-b", actor_id="actor-2", name="Different Program", idempotency_key=key,
        )
    all_b = service.list_programs(tenant_id="tenant-b")
    assert len(all_b) == 1

    # -- Validation --------------------------------------------------------
    with pytest.raises(ProgramValidationError):
        service.create_program(tenant_id="tenant-a", actor_id="actor-1", name="   ")
    with pytest.raises(ProgramValidationError):
        service.create_program(tenant_id="", actor_id="actor-1", name="ok")


def _run_concurrent_same_key_create(make_store) -> None:
    """20 concurrent callers creating with the same idempotency key -> exactly
    one program created; every caller gets the identical committed result."""

    store: ProgramStore = make_store()
    service = ProgramService(store)
    key = f"concurrent-{uuid.uuid4().hex}"

    def _create():
        program, replayed = service.create_program(
            tenant_id="tenant-c", actor_id="actor-3", name="Concurrent Program", idempotency_key=key,
        )
        return program["program_id"], program["created_at"]

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: _create(), range(20)))

    program_ids = {r[0] for r in results}
    assert len(program_ids) == 1, f"expected exactly one program, got {program_ids}"
    all_c = service.list_programs(tenant_id="tenant-c")
    assert len(all_c) == 1


def _run_restart_recovery(make_store) -> None:
    """A fresh store instance (simulating a process restart) reads back the
    already-committed program and does not re-run a same-key create."""

    store1: ProgramStore = make_store()
    service1 = ProgramService(store1)
    key = f"restart-{uuid.uuid4().hex}"
    program, _ = service1.create_program(
        tenant_id="tenant-d", actor_id="actor-4", name="Durable Program", idempotency_key=key,
    )

    store2: ProgramStore = make_store()  # fresh instance against the same durable storage
    service2 = ProgramService(store2)
    reread = service2.get_program(tenant_id="tenant-d", program_id=program["program_id"])
    assert reread == program

    replay, replayed = service2.create_program(
        tenant_id="tenant-d", actor_id="actor-4", name="Durable Program", idempotency_key=key,
    )
    assert replayed is True
    assert replay == program
    assert len(service2.list_programs(tenant_id="tenant-d")) == 1


def _run_action_scenarios(make_store) -> None:
    """Validate all lifecycle transitions, D1 active run handling, D2 freeze,
    D3 steering, and receipt/idempotency invariants."""
    store: ProgramStore = make_store()
    service = ProgramService(store)

    program, _ = service.create_program(
        tenant_id="tenant-act", actor_id="actor-act", name="Action Test Program"
    )
    pid = program["program_id"]

    # 1. draft -> submit_evolution_review -> under_review
    res, replayed = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
        action_id="submit_evolution_review", note="Submitting review",
    )
    assert replayed is False
    assert res["program_status"] == "under_review"
    assert res["program"]["status"] == "under_review"
    assert res["action_id"] == "submit_evolution_review"

    # 2. Illegal transition: draft action while under_review
    with pytest.raises(ProgramConflictError) as exc_info:
        service.execute_action(
            tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
            action_id="submit_evolution_review",
        )
    assert "Cannot submit review for program in status 'under_review'" in str(exc_info.value)

    # 3. under_review -> approve_program -> active
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="approve_program", note="Approved by committee",
    )
    assert res["program_status"] == "active"
    assert res["program"]["status"] == "active"

    # 4. active -> pause_program -> paused
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
        action_id="pause_program", note="Pausing for inspection",
    )
    assert res["program_status"] == "paused"
    assert res["program"]["status"] == "paused"

    # 5. paused -> resume_program -> active
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
        action_id="resume_program", note="Resuming runs",
    )
    assert res["program_status"] == "active"
    assert res["program"]["status"] == "active"

    # 6. Stop and resume semantics (D1: stop cancels nonterminal runs -> stopped; resume refuses to reverse stop)
    p_stop, _ = service.create_program(tenant_id="tenant-act", actor_id="actor-act", name="Stop Test Program")
    pid_stop = p_stop["program_id"]
    service.execute_action(tenant_id="tenant-act", actor_id="actor-act", program_id=pid_stop, action_id="submit_evolution_review")
    service.execute_action(tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid_stop, action_id="approve_program")

    res_stop, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid_stop,
        action_id="stop", note="Emergency stop",
    )
    assert res_stop["program_status"] == "stopped"
    assert res_stop["program"]["status"] == "stopped"

    # resume_program refuses to reverse a stop
    with pytest.raises(ProgramConflictError) as exc_info:
        service.execute_action(
            tenant_id="tenant-act", actor_id="actor-act", program_id=pid_stop,
            action_id="resume_program",
        )
    assert "cannot resume stopped program" in str(exc_info.value).lower()

    # pause_program refuses on stopped
    with pytest.raises(ProgramConflictError):
        service.execute_action(
            tenant_id="tenant-act", actor_id="actor-act", program_id=pid_stop,
            action_id="pause_program",
        )

    # retire_program succeeds on stopped
    res_retire_stop, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid_stop,
        action_id="retire_program",
    )
    assert res_retire_stop["program_status"] == "retired"

    # 7. Freeze & Unfreeze generation (D2)
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="freeze_generation", note="Freeze generation",
    )
    assert res["program"]["is_frozen"] is True

    # While frozen, promote_candidate_paper and approve_mutation are blocked with 409
    with pytest.raises(ProgramConflictError) as exc_info:
        service.execute_action(
            tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
            action_id="promote_candidate_paper", payload={"candidate_id": "cand-01"},
        )
    assert "frozen" in str(exc_info.value).lower()

    with pytest.raises(ProgramConflictError) as exc_info:
        service.execute_action(
            tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
            action_id="approve_mutation", payload={"mutation_id": "mut-01"},
        )
    assert "frozen" in str(exc_info.value).lower()

    # Unfreeze generation
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="unfreeze_generation",
    )
    assert res["program"]["is_frozen"] is False

    # Now promote_candidate_paper and promote_candidate_live succeed
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="promote_candidate_paper", payload={"candidate_id": "cand-01"},
    )
    assert res["details"]["stage"] == "paper"
    assert res["program_status"] == "active"
    assert len(res["program"]["promotions"]) == 1
    assert res["program"]["promotions"][0]["stage"] == "paper"

    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="promote_candidate_live", payload={"candidate_id": "cand-01"},
    )
    assert res["details"]["stage"] == "live"
    assert res["details"]["capital_authority"] == "none"
    assert res["program_status"] == "active"
    assert len(res["program"]["promotions"]) == 2
    assert res["program"]["promotions"][1]["stage"] == "live"

    # approve_mutation and reject_mutation
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="approve_mutation", payload={"mutation_id": "mut-01"},
    )
    assert res["details"]["decision"] == "approved"
    assert res["program_status"] == "active"

    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="reject_mutation", payload={"mutation_id": "mut-02"},
    )
    assert res["details"]["decision"] == "rejected"
    assert res["program_status"] == "active"

    # 8. Steering actions (D3)
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
        action_id="create_constraint", payload={"name": "max_drawdown", "value": 0.15},
    )
    assert res["details"]["name"] == "max_drawdown"
    assert res["program_status"] == "active"
    assert len(res["program"]["constraints"]) == 1

    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
        action_id="create_fitness_formula", payload={"expression": "sharpe * 0.7 + sortino * 0.3"},
    )
    assert res["details"]["expression"] == "sharpe * 0.7 + sortino * 0.3"
    assert res["program_status"] == "active"
    assert len(res["program"]["fitness_formulas"]) == 1

    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
        action_id="create_mutation_rule", payload={"expression": "gaussian_perturbation"},
    )
    assert res["details"]["expression"] == "gaussian_perturbation"
    assert res["program_status"] == "active"
    assert len(res["program"]["mutation_rules"]) == 1

    # 9. active -> complete_program -> completed
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
        action_id="complete_program",
    )
    assert res["program_status"] == "completed"
    assert res["program"]["status"] == "completed"

    # 10. completed -> retire_program -> retired (terminal)
    res, _ = service.execute_action(
        tenant_id="tenant-act", actor_id="approver-1", actor_role="approver", program_id=pid,
        action_id="retire_program",
    )
    assert res["program_status"] == "retired"
    assert res["program"]["status"] == "retired"

    # Cannot mutate after retirement
    with pytest.raises(ProgramConflictError):
        service.execute_action(
            tenant_id="tenant-act", actor_id="actor-act", program_id=pid,
            action_id="resume_program",
        )

    # 11. Idempotency test on action
    idem_key = f"idem-act-{uuid.uuid4().hex}"
    p2, _ = service.create_program(tenant_id="tenant-act", actor_id="actor-act", name="Idem Program")
    pid2 = p2["program_id"]

    act1, rep1 = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid2,
        action_id="submit_evolution_review", idempotency_key=idem_key,
    )
    assert rep1 is False
    assert act1["program_status"] == "under_review"

    # Replay same action & key
    act2, rep2 = service.execute_action(
        tenant_id="tenant-act", actor_id="actor-act", program_id=pid2,
        action_id="submit_evolution_review", idempotency_key=idem_key,
    )
    assert rep2 is True
    assert act2["receipt_id"] == act1["receipt_id"]
    assert act2["idempotent_replay"] is True

    # Divergent replay -> 409
    with pytest.raises(ProgramDivergentReplayError):
        service.execute_action(
            tenant_id="tenant-act", actor_id="actor-act", program_id=pid2,
            action_id="submit_evolution_review", idempotency_key=idem_key,
            payload={"different": "payload"},
        )
    with pytest.raises(ProgramDivergentReplayError):
        service.execute_action(
            tenant_id="tenant-act", actor_id="actor-act", actor_role="approver", program_id=pid2,
            action_id="approve_program", idempotency_key=idem_key,
        )


def _run_router_actions(make_store) -> None:
    store: ProgramStore = make_store()
    service = ProgramService(store)
    router = create_program_router(
        service=service,
        current_tenant=lambda: "tenant-r",
        authorize_request_tenant=lambda t: t or "tenant-r",
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Create program
    resp = client.post("/api/evolution/programs", json={"name": "Router Test", "actor_id": "act-1"})
    assert resp.status_code == 201
    pid = resp.json()["program_id"]

    # Action submit_evolution_review
    resp = client.post(f"/api/evolution/programs/{pid}/actions/submit_evolution_review", json={"actor_id": "act-1"})
    assert resp.status_code == 200
    assert resp.json()["program_status"] == "under_review"

    # Action approve_program
    resp = client.post(f"/api/evolution/programs/{pid}/actions/approve_program", json={"actor_id": "appr-1", "actor_role": "approver"})
    assert resp.status_code == 200
    assert resp.json()["program_status"] == "active"

    # Conflict on invalid action (already active, cannot approve again)
    resp = client.post(f"/api/evolution/programs/{pid}/actions/approve_program", json={"actor_id": "appr-1", "actor_role": "approver"})
    assert resp.status_code == 409

    # Role rejection when operator attempts approver action
    resp = client.post(f"/api/evolution/programs/{pid}/actions/retire_program", json={"actor_id": "act-1", "actor_role": "operator"})
    assert resp.status_code == 422

    # Not found for unknown program
    resp = client.post("/api/evolution/programs/non-existent/actions/pause_program", json={"actor_id": "act-1"})
    assert resp.status_code == 404

    # Promotion via router (flat schema)
    resp = client.post(
        f"/api/evolution/programs/{pid}/actions/promote_candidate_paper",
        json={
            "actor_id": "appr-1",
            "actor_role": "approver",
            "candidate_id": "cand-router-1",
            "run_id": "run-router-1",
            "artifact_id": "art-router-1",
        },
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["details"]["candidate_id"] == "cand-router-1"
    assert res_data["details"]["run_id"] == "run-router-1"
    assert res_data["details"]["artifact_id"] == "art-router-1"
    assert res_data["details"]["stage"] == "paper"

    # Promotion via router with nested payload (BFF compatibility)
    resp = client.post(
        f"/api/evolution/programs/{pid}/actions/promote_candidate_live",
        json={
            "actor_id": "appr-1",
            "actor_role": "approver",
            "payload": {
                "candidate_id": "cand-router-live",
                "run_id": "run-router-live",
            },
        },
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["details"]["candidate_id"] == "cand-router-live"
    assert res_data["details"]["run_id"] == "run-router-live"
    assert res_data["details"]["stage"] == "live"
    assert res_data["details"]["capital_authority"] == "none"

    # approve_mutation via router (real caller mutation_id preserved)
    resp = client.post(
        f"/api/evolution/programs/{pid}/actions/approve_mutation",
        json={
            "actor_id": "appr-1",
            "actor_role": "approver",
            "mutation_id": "mut-real-999",
        },
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["details"]["mutation_id"] == "mut-real-999"
    assert res_data["details"]["decision"] == "approved"

    # reject_mutation via router with nested payload
    resp = client.post(
        f"/api/evolution/programs/{pid}/actions/reject_mutation",
        json={
            "actor_id": "appr-1",
            "actor_role": "approver",
            "payload": {
                "mutation_id": "mut-real-888",
                "reason": "Exceeded risk boundary",
            },
        },
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["details"]["mutation_id"] == "mut-real-888"
    assert res_data["details"]["decision"] == "rejected"
    assert res_data["details"]["reason"] == "Exceeded risk boundary"

    # approve_mutation without mutation_id raises 422
    resp = client.post(
        f"/api/evolution/programs/{pid}/actions/approve_mutation",
        json={
            "actor_id": "appr-1",
            "actor_role": "approver",
        },
    )
    assert resp.status_code == 422
    assert "mutation_id is required" in resp.json()["detail"]

    # stop via router transitions to stopped
    resp = client.post(
        f"/api/evolution/programs/{pid}/actions/stop",
        json={"actor_id": "act-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["program_status"] == "stopped"

    # resume_program on stopped program raises 409
    resp = client.post(
        f"/api/evolution/programs/{pid}/actions/resume_program",
        json={"actor_id": "act-1"},
    )
    assert resp.status_code == 409
    assert "cannot resume stopped program" in resp.json()["detail"].lower()


class TestJsonProgramStore:
    """Runs unconditionally against the JSON dev backend."""

    def _make_store_factory(self, tmp_path: Path):
        storage_path = tmp_path / "programs.json"

        def make_store() -> ProgramStore:
            return JsonProgramStore(storage_path)

        return make_store

    def test_scenarios(self, tmp_path: Path) -> None:
        _run_scenarios(self._make_store_factory(tmp_path))

    def test_action_scenarios(self, tmp_path: Path) -> None:
        _run_action_scenarios(self._make_store_factory(tmp_path))

    def test_concurrent_same_key_create(self, tmp_path: Path) -> None:
        _run_concurrent_same_key_create(self._make_store_factory(tmp_path))

    def test_restart_recovery(self, tmp_path: Path) -> None:
        _run_restart_recovery(self._make_store_factory(tmp_path))

    def test_router_program_actions(self, tmp_path: Path) -> None:
        _run_router_actions(self._make_store_factory(tmp_path))


def _postgres_dsn() -> str:
    return os.getenv("TEST_DATABASE_URL", "").strip()


@pytest.fixture
def pg_store_factory():
    dsn = _postgres_dsn()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres program-owner proof")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    from services.evolution.program_store import PostgresProgramStore

    schema = f"evo_program_owner_{uuid.uuid4().hex}"
    created_stores = []

    def make_store() -> ProgramStore:
        store = PostgresProgramStore(
            dsn=dsn,
            programs_table=f"{schema}.programs",
            receipts_table=f"{schema}.program_command_receipts",
            bootstrap=True,
        )
        created_stores.append(store)
        return store

    try:
        yield make_store
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


class TestPostgresProgramStore:
    """Gated on TEST_DATABASE_URL — a live Postgres instance is required to
    prove real transactional atomicity/concurrency; skipped cleanly (with an
    explicit reason) otherwise, never silently."""

    def test_scenarios(self, pg_store_factory) -> None:
        _run_scenarios(pg_store_factory)

    def test_concurrent_same_key_create(self, pg_store_factory) -> None:
        _run_concurrent_same_key_create(pg_store_factory)

    def test_restart_recovery(self, pg_store_factory) -> None:
        _run_restart_recovery(pg_store_factory)

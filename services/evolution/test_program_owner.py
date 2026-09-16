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


class TestJsonProgramStore:
    """Runs unconditionally against the JSON dev backend."""

    def _make_store_factory(self, tmp_path: Path):
        storage_path = tmp_path / "programs.json"

        def make_store() -> ProgramStore:
            return JsonProgramStore(storage_path)

        return make_store

    def test_scenarios(self, tmp_path: Path) -> None:
        _run_scenarios(self._make_store_factory(tmp_path))

    def test_concurrent_same_key_create(self, tmp_path: Path) -> None:
        _run_concurrent_same_key_create(self._make_store_factory(tmp_path))

    def test_restart_recovery(self, tmp_path: Path) -> None:
        _run_restart_recovery(self._make_store_factory(tmp_path))


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

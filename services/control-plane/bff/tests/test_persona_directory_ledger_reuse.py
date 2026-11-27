"""Stamp reuse at the SQL seam and in local Postgres, with the hosted row shape.

Optional real-DB checks: PANTHEON_TEST_PROVISIONING_DSN=<local DSN> pytest <file>.
The integration fixtures seed and drop only a unique local schema.
Timing/RSS acceptance is pending the task brief correction recorded in evidence.
"""
from __future__ import annotations

import json
import os
import random
import uuid
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.persona_provisioning import (
    PostgresPersonaProvisioningStore, ProvisioningRecord,
)
from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.personas import service as ps
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.tests import test_strategy_operational_status as strategy_tests

TENANT = "tenant-dev"
NOW = "2026-11-27T23:00:00Z"
RECEIPT_KEYS = (
    "authoritative_readback", "strategy_artifact_approved", "deployment_dispatch",
    "baseline_strategy_artifact_approved", "baseline_strategy_spec_approved",
    "deployment_plan", "strategy_spec_approved", "strategy_artifact_candidate",
)


def hosted_rows():
    rng = random.Random(20261127)
    rows = []
    for i in range(170):
        pid = f"persona-{i:020d}"
        if i == 169:
            pid = "agora-servant-799a3ce8c021b91cbb54"
        name = f"Trader {i}" if i != 169 else f"{pid} trader paper book"
        # Distinct receipts, not a shared tiny object or repeated single byte.
        refs = {
            key: {
                "owner": key, "status": "approved", "persona_id": pid,
                "metadata": {"receipt": "".join(rng.choices("abcdef0123456789", k=3800))},
            }
            for key in RECEIPT_KEYS
        }
        refs.update({
            "runtime_id": f"runtime-{i}", "runtime_binding_id": f"rb-{i}",
            "requested_risk_profile_provenance": {
                "source": "verified_persona_owner", "persona_id": pid,
                "tenant_id": TENANT, "requested_profile": "medium",
            },
            "capital_pool_risk_policy_resolution": {"resolved": False, "status": "unresolved_profile_mapping"},
            "risk_policy_owner_decision_request": {
                "absence_source": "profile_mapping_missing", "reason_code": "review_required",
                "recommended_governance_action": "review_mapping",
            },
        })
        rows.append(ProvisioningRecord(
            tenant_id=TENANT, idempotency_key=f"create-{i}", request_hash=f"hash-{i}",
            normalized_name=name.lower(), persona_id=pid,
            request_payload={"name": name, "tenant_id": TENANT, "requested_by": "operator", "budget": 10000000},
            state="retired" if i < 165 else "succeeded" if i < 169 else "provisioning",
            current_step="authoritative_readback", references=refs,
            result={"paper_running": True, "status": "paper_running"},
            created_at="2026-11-10T00:00:00Z", updated_at="2026-11-16T00:00:00Z",
        ))
    return rows


class _SQLDouble:
    """SQL boundary double only; real MVCC/trigger claims use Postgres tests."""
    def __init__(self, rows):
        self.rows = rows
        self.revision = 1
        self.calls = []
        self.fail = False

    def connect(self, _dsn):
        db = self

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def execute(self, sql, params=()):
                if db.fail:
                    raise RuntimeError("database unreachable")
                db.calls.append(sql)
                self.sql, self.params = sql, params
            def fetchone(self): return (db.revision,)
            def fetchall(self):
                rows = [r for r in db.rows if not self.params or r.tenant_id == self.params[0]]
                return [deepcopy(tuple(getattr(r, k) for k in PostgresPersonaProvisioningStore._RECORD_FIELDS)) for r in rows]

        class Conn:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def cursor(self): return Cursor()
        return Conn()


@pytest.fixture
def ledger():
    db = _SQLDouble(hosted_rows())
    store = PostgresPersonaProvisioningStore("sql-double", connect=db.connect)
    db.calls.clear()
    return store, db


def _full_selects(calls):
    return [sql for sql in calls if sql.startswith("SELECT tenant_id,")]


def _wire(monkeypatch, store, rows, tmp_path):
    class Owner:
        def __init__(self):
            # Hosted residual: one retired ledger row has no Persona owner.
            self.personas = {
                r.persona_id: {
                    "id": r.persona_id, "persona_id": r.persona_id,
                    "name": r.request_payload["name"] if i != 169 else "Agora Servant",
                    "tenant_id": TENANT, "metadata": {"tenant_id": TENANT},
                    "lifecycle_state": "retired" if i < 165 else "draft",
                    "created_at": r.created_at,
                } for i, r in enumerate(rows) if i != 164
            }
        def list_personas(self, **_): return deepcopy(list(self.personas.values()))
        def get_persona(self, pid): return deepcopy(self.personas.get(pid))
    owner = Owner()
    ports = create_read_surface_ports(persona_registry_store=owner)
    # Unrelated Registry count owner is fixed; directory/projection code is real.
    ports.count_strategy_persona_bindings = lambda: {r.persona_id: 1 for r in rows}
    service = PersonaService(
        read_store=ports, provisioning_store=store,
        write_owner=SimpleNamespace(), ranking_write_owner=SimpleNamespace(),
        command_store=CommandStore(str(tmp_path / "commands.jsonl")),
    )
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "1")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setattr(ps, "_get_active_read_store", lambda explicit=None: explicit or ports)
    monkeypatch.setattr(ps, "_get_active_write_owner", lambda: SimpleNamespace())
    monkeypatch.setattr(ps, "_persona_provisioning_store", lambda: store)
    monkeypatch.setattr(ps, "_composed_persona_service", service)
    monkeypatch.setattr(ps, "utc_now", lambda: NOW)
    app = FastAPI()
    app.include_router(create_personas_router(service=service, utc_now_fn=lambda: NOW))
    client = TestClient(app)
    world = strategy_tests._World()
    world.entries = [strategy_tests._spec_entry(
        strategy_id=f"strategy-{i}", title=f"Strategy {i}", persona_ids=[r.persona_id],
        updated_at=NOW,
    ) for i, r in enumerate(rows)]
    world.persona_directory = ps._get_persona_directory_snapshot
    strategy_app, _ = strategy_tests._build_app(monkeypatch, world)
    return client, TestClient(strategy_app), owner


def _payloads(clients):
    personas, strategies, _ = clients
    a = personas.get("/bff/personas?page_size=200", headers=strategy_tests.HEADERS)
    b = strategies.get(
        "/bff/strategies?exclude_state=draft&hide_retired_personas=true&page_size=200",
        headers=strategy_tests.HEADERS,
    )
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    return a.json(), b.json()


def test_hosted_shape_payload_parity_and_conflicting_owner_skip(ledger, monkeypatch, tmp_path, caplog):
    store, db = ledger
    clients = _wire(monkeypatch, store, db.rows, tmp_path)
    # Legacy full reads are the oracle, including the missing/conflicting owner.
    with monkeypatch.context() as patch:
        patch.setattr(store, "list_for_directory", lambda tenant=None: store.list_by_tenant(tenant) if tenant else store.list_all())
        before_directory = ps._get_persona_directory_snapshot(TENANT, snapshot_at=NOW)
        before_payloads = _payloads(clients)
    db.calls.clear()
    after_directory = ps._get_persona_directory_snapshot(TENANT, snapshot_at=NOW)
    assert after_directory == before_directory
    assert _payloads(clients) == before_payloads
    assert _payloads(clients) == before_payloads
    assert len(_full_selects(db.calls)) == 1
    stamps = [sql for sql in db.calls if sql.startswith("SELECT revision")]
    assert len(stamps) == 5  # snapshot + two routes in each of two calls
    assert len(after_directory.records_by_id) == 170
    conflict = after_directory.records_by_id[db.rows[-1].persona_id]
    assert conflict["name"] == "Agora Servant"
    assert "provisioning_idempotency_key" not in conflict["metadata"]
    assert "conflicting owner scope" in caplog.text
    assert len([r for r in db.rows if r.state == "retired"]) == 165
    assert all(31000 < len(json.dumps(r.references).encode()) < 35000 for r in db.rows)


def test_stamp_checked_each_call_all_tenant_isolation_and_detached_copies(ledger):
    store, db = ledger
    first = store.list_for_directory(TENANT)
    first[0].references[RECEIPT_KEYS[0]]["metadata"]["receipt"] = "changed response"
    assert store.list_for_directory(TENANT)[0] != first[0]
    assert store.list_for_directory("other") == []
    assert len(store.list_for_directory()) == 170
    db.rows[0].references["external_sql"] = {"visible": True}
    db.revision += 1  # timestamp deliberately unchanged
    assert store.list_for_directory(TENANT)[0].references["external_sql"] == {"visible": True}
    assert store.list_for_directory()[0].references["external_sql"] == {"visible": True}
    assert len([sql for sql in db.calls if sql.startswith("SELECT revision")]) == 6


def test_unvalidated_cache_never_masks_failure(ledger, monkeypatch, tmp_path):
    store, db = ledger
    _wire(monkeypatch, store, db.rows, tmp_path)
    store.list_for_directory(TENANT)
    db.fail = True
    with pytest.raises(HTTPException) as exc:
        ps._get_persona_directory_snapshot(TENANT)
    assert exc.value.status_code == 503


def test_lru_entry_count_byte_cap_and_oversized_bypass(ledger, monkeypatch):
    store, db = ledger
    for i in range(12):
        store.list_for_directory(f"empty-{i}")
    assert len(store._directory_cache) == 8
    assert "empty-0" not in store._directory_cache
    store.list_for_directory("empty-4")
    store.list_for_directory("another")
    assert "empty-4" in store._directory_cache and "empty-5" not in store._directory_cache
    monkeypatch.setattr(store, "_DIRECTORY_MAX_BYTES", 2500)
    for i in range(12):
        store.list_for_directory(f"bounded-{i}")
    assert store._directory_cache_bytes <= 2500
    before = len(_full_selects(db.calls))
    assert len(store.list_for_directory(TENANT)) == 170
    assert len(store.list_for_directory(TENANT)) == 170
    assert len(_full_selects(db.calls)) == before + 2
    assert TENANT not in store._directory_cache


def _local_dsn():
    dsn = os.environ.get("PANTHEON_TEST_PROVISIONING_DSN")
    if not dsn:
        pytest.skip("PANTHEON_TEST_PROVISIONING_DSN not supplied for local Postgres integration")
    if urlparse(dsn).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ledger tests only seed local Postgres")
    return dsn


@contextmanager
def _postgres():
    import psycopg
    dsn = _local_dsn()
    schema = f"ledger_reuse_{uuid.uuid4().hex}"
    store = PostgresPersonaProvisioningStore(dsn, schema=schema)
    rows = hosted_rows()
    json_keys = {"request_payload", "references", "result", "error", "compensation"}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        placeholders = ",".join("%s::jsonb" if k in json_keys else "%s" for k in store._RECORD_FIELDS)
        cur.executemany(
            f"INSERT INTO {store.table} ({store._COLUMNS}) VALUES ({placeholders})",
            [tuple(json.dumps(getattr(r, k)) if k in json_keys else getattr(r, k) for k in store._RECORD_FIELDS) for r in rows],
        )
    try:
        yield store, rows
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def pg_ledger():
    with _postgres() as item:
        yield item


def test_postgres_hosted_payloads_identical(pg_ledger, monkeypatch, tmp_path):
    store, rows = pg_ledger
    clients = _wire(monkeypatch, store, rows, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(store, "list_for_directory", lambda tenant=None: store.list_by_tenant(tenant) if tenant else store.list_all())
        baseline = ps._get_persona_directory_snapshot(TENANT, snapshot_at=NOW), _payloads(clients)
    for _ in range(2):
        assert (ps._get_persona_directory_snapshot(TENANT, snapshot_at=NOW), _payloads(clients)) == baseline


def test_postgres_each_write_visible_next_read_and_timestamp_unchanged_sql(pg_ledger):
    import psycopg
    store, _ = pg_ledger
    replica = PostgresPersonaProvisioningStore(store.dsn, schema=store.schema)
    assert len(store.list_for_directory(TENANT)) == 170
    record, created = replica.reserve(
        tenant_id=TENANT, idempotency_key="new", request_hash="new", normalized_name="new trader",
        persona_id="persona-new", request_payload={"name": "New trader", "requested_by": "operator"},
    )
    assert created
    def current():
        return next(r for r in store.list_for_directory(TENANT) if r.persona_id == record.persona_id)
    assert current().state == "reserved"
    record = replica.acquire(TENANT, "new", lease_owner="worker", lease_seconds=60)
    assert current().attempt_count == 1
    record.references["deployment_dispatch"] = {"accepted": True}
    record = replica.checkpoint(record, lease_owner="worker")
    assert current().references["deployment_dispatch"] == {"accepted": True}
    record.state = "retired"
    record = replica.checkpoint(record, lease_owner="worker")
    assert current().state == "retired"
    record.state = "compensated"
    record.compensation = {"pool": "released"}
    record = replica.release(record, lease_owner="worker")
    assert current().compensation == {"pool": "released"}
    assert current().lease_owner is None
    previous_time = current().updated_at
    with psycopg.connect(store.dsn) as conn:
        conn.execute(f"UPDATE {store.table} SET current_step='direct_sql' WHERE persona_id='persona-new'")
    assert current().current_step == "direct_sql"
    assert current().updated_at == previous_time
    with psycopg.connect(store.dsn) as conn:
        conn.execute(f"DELETE FROM {store.table} WHERE persona_id='persona-new'")
    assert len(store.list_for_directory(TENANT)) == 170
    with psycopg.connect(store.dsn) as conn:
        conn.execute(f"TRUNCATE {store.table}")
    assert store.list_for_directory(TENANT) == []


def test_postgres_rollback_inflight_commit_and_point_reads(pg_ledger, monkeypatch):
    import psycopg
    store, rows = pg_ledger
    first = store.list_for_directory(TENANT)
    # Rolled-back writes are invisible both in generation and ledger data.
    with psycopg.connect(store.dsn) as conn:
        conn.execute(f"UPDATE {store.table} SET current_step='rolled_back'")
        conn.rollback()
    assert store.list_for_directory(TENANT) == first
    # A committed write arriving between stamp and row load must not poison
    # the cache with rows from a newer snapshot labelled with the old stamp.
    store._directory_cache.clear()
    store._directory_cache_bytes = 0
    original_connect = store._connect
    fired = []
    class CursorProxy:
        def __init__(self, cursor): self.cursor = cursor
        def __enter__(self): self.cursor.__enter__(); return self
        def __exit__(self, *args): return self.cursor.__exit__(*args)
        def __getattr__(self, name): return getattr(self.cursor, name)
        def execute(self, sql, *args):
            result = self.cursor.execute(sql, *args)
            if sql.startswith("SELECT revision") and not fired:
                fired.append(True)
                with original_connect(store.dsn) as writer:
                    writer.execute(f"UPDATE {store.table} SET current_step='concurrent'")
            return result
    class ConnProxy:
        def __init__(self, conn): self.conn = conn
        def __enter__(self): self.conn.__enter__(); return self
        def __exit__(self, *args): return self.conn.__exit__(*args)
        def cursor(self): return CursorProxy(self.conn.cursor())
    monkeypatch.setattr(store, "_connect", lambda dsn: ConnProxy(original_connect(dsn)))
    assert store.list_for_directory(TENANT) == first
    assert all(r.current_step == "concurrent" for r in store.list_for_directory(TENANT))
    # Ordinary coordinator get/list reads must bypass retained directory data.
    monkeypatch.setattr(store, "list_for_directory", lambda *_: pytest.fail("point/list read used directory cache"))
    assert store.get(TENANT, rows[0].idempotency_key).current_step == "concurrent"
    assert store.get_by_persona(TENANT, rows[0].persona_id).current_step == "concurrent"
    assert len(store.list_by_tenant(TENANT)) == len(store.list_all()) == 170


def test_fleet_uses_full_inputs_and_documents_old_projection_differences(ledger, monkeypatch):
    store, db = ledger
    monkeypatch.setattr(store, "list_by_tenant_for_fleet", lambda *_: pytest.fail("lossy slim read used"))
    assert ps._persona_provisioning_records_shared(store, TENANT) == store.list_by_tenant(TENANT)
    record = db.rows[165]
    slim = deepcopy(record)
    slim.references = {k: ({} if k == "authoritative_readback" else v) for k, v in record.references.items() if k in store._FLEET_REFERENCE_KEYS}
    slim.result = {k: v for k, v in record.result.items() if k in store._FLEET_RESULT_KEYS}
    monkeypatch.setattr(ps, "_get_active_read_store", lambda: SimpleNamespace(get_persona=lambda _: None))
    monkeypatch.setattr(ps, "_get_active_write_owner", lambda: SimpleNamespace())
    full_persona, full = ps._persona_record_for_provisioning(record, payload=record.request_payload, owner="operator")
    _, old = ps._persona_record_for_provisioning(slim, payload=slim.request_payload, owner="operator")
    changed = sorted(k for k in full if full.get(k) != old.get(k))
    assert changed == sorted([
        "risk_level", "risk_profile", "risk_policy_resolution", "risk_policy_owner_decision_request",
        "provisioning_waiting_reason", "recommended_governance_action",
    ])
    assert full_persona["risk_level"] == "medium"
    # Create/readback response also consumes the keys that slim SQL discarded.
    assert "deployment_dispatch" in record.references and "deployment_dispatch" not in slim.references
    assert sorted(record.references) != sorted(slim.references)

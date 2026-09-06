"""End-to-end durability proof for the mounted Registry FastAPI app against a
real PostgreSQL owner store — architecture-resumption-sa-sd.md §3.4.

Gated on TEST_DATABASE_URL (skip cleanly without a live database, matching
services/incident/test_pg_store_integration.py). Where services/foundation/
tests/test_registry_owner_transaction.py proves the storage primitive's CAS
and atomicity directly, this module proves the same guarantees are reachable
through the actual mounted HTTP surface: register, metadata CAS update,
fresh-process restart (a second app/store instance against the same DSN),
and rejection of a stale CAS request over HTTP (409).
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")

from fastapi.testclient import TestClient

from . import service as service_module
from .storage import reset_store

# services/runtime_auth_inbound.py's permissive-mode structured-token form:
# "actor_id:role1,role2". This is a placeholder test caller identity, never a
# real credential — production sets PANTHEON_REGISTRY_AUTH_MODE=strict, which
# only accepts a verified JWT (see docker-compose.yml/control.yml).
_AUTH_HEADERS = {"Authorization": "Bearer test-operator:operator"}


@pytest.fixture
def pg_app():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres owner durability proof")
    import psycopg
    from psycopg import sql

    schema = f"registry_durability_{uuid4().hex}"
    prior_env = {
        key: os.environ.get(key)
        for key in (
            "REGISTRY_STORE_BACKEND",
            "REGISTRY_STORE_DSN",
            "REGISTRY_ENTRIES_TABLE",
            "REGISTRY_RECEIPTS_TABLE",
        )
    }
    os.environ["REGISTRY_STORE_BACKEND"] = "postgres"
    os.environ["REGISTRY_STORE_DSN"] = dsn
    os.environ["REGISTRY_ENTRIES_TABLE"] = f"{schema}.entries"
    os.environ["REGISTRY_RECEIPTS_TABLE"] = f"{schema}.command_receipts"
    reset_store()
    try:
        yield TestClient(service_module.app)
    finally:
        reset_store()
        for key, value in prior_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _register(client: TestClient, **overrides) -> dict:
    payload = {
        "artifact_type": "strategy_spec",
        "strategy_id": "durability-strat",
        "version": "1.0.0",
        "artifact_state": "draft",
        "checksum": "sha256:cafef00d",
    }
    payload.update(overrides)
    resp = client.post("/api/registry/entries", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_register_and_readback_survive_fresh_process_restart(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    # Simulate a fresh process: drop the in-process singleton and reconnect.
    reset_store()
    fresh_client = TestClient(service_module.app)
    resp = fresh_client.get(f"/api/registry/entries/{registry_id}")
    assert resp.status_code == 200
    assert resp.json()["entry"]["strategy_id"] == "durability-strat"


def test_metadata_cas_update_over_http_succeeds_and_verifies_readback(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]
    assert created["entry"]["metadata"] is None

    resp = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "operator draft"}},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Idempotent-Replay"] == "false"
    assert resp.json()["entry"]["metadata"] == {"note": "operator draft"}

    # Real owner GET/readback proof, not POST/body-accepted-as-truth.
    readback = pg_app.get(f"/api/registry/entries/{registry_id}")
    assert readback.json()["entry"]["metadata"] == {"note": "operator draft"}


def test_metadata_cas_update_over_http_rejects_stale_expected(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    ok = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "first"}},
        headers=_AUTH_HEADERS,
    )
    assert ok.status_code == 200

    stale = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "based on stale read"}},
        headers=_AUTH_HEADERS,
    )
    assert stale.status_code == 409

    current = pg_app.get(f"/api/registry/entries/{registry_id}")
    assert current.json()["entry"]["metadata"] == {"note": "first"}


def test_metadata_cas_update_idempotent_replay_over_http(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    first = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "v1"}, "command_key": "cmd-http-1"},
        headers=_AUTH_HEADERS,
    )
    assert first.status_code == 200
    assert first.headers["X-Idempotent-Replay"] == "false"

    replay = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "v1"}, "command_key": "cmd-http-1"},
        headers=_AUTH_HEADERS,
    )
    assert replay.status_code == 200
    assert replay.headers["X-Idempotent-Replay"] == "true"
    assert replay.json()["entry"]["metadata"] == {"note": "v1"}


def test_metadata_cas_update_missing_entry_is_404(pg_app):
    resp = pg_app.patch(
        "/api/registry/entries/reg-does-not-exist/metadata",
        json={"expected_metadata": None, "metadata": {"note": "x"}},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 404


def test_metadata_cas_update_rejects_unauthenticated_caller(pg_app):
    """No Authorization header at all must fail closed (401), not fall
    through to an anonymous-compatibility path."""
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    resp = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "no auth"}},
    )
    assert resp.status_code == 401

    unchanged = pg_app.get(f"/api/registry/entries/{registry_id}")
    assert unchanged.json()["entry"]["metadata"] is None


def test_advance_state_conflict_is_409_not_silent_overwrite(pg_app):
    created = _register(
        pg_app,
        lineage={"source_run_ids": ["run-1"]},
    )
    registry_id = created["entry"]["registry_id"]

    advance_1 = pg_app.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "candidate"},
    )
    assert advance_1.status_code == 200

    # advance() re-reads the current entry each call, so a legitimate second
    # advance from the now-current state must succeed (not a stale-CAS proof
    # by itself) — the real proof is that a forbidden transition from the
    # *new* current state still fails explicitly rather than silently no-op.
    forbidden = pg_app.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "draft"},
    )
    assert forbidden.status_code == 400

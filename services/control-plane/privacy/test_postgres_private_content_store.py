"""Real isolated-Postgres tests; never count a missing DSN as passed persistence."""
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from services.control_plane.privacy.postgres_private_content_store import PostgresPrivateContentStore
from services.control_plane.privacy.private_content_models import (
    PrivateContentAccessDenied, PrivateContentExpired, PrivateContentStoreUnavailable,
)


@pytest.fixture
def private_db(monkeypatch):
    dsn = os.environ.get("PRIVATE_CONTENT_TEST_DSN")
    if not dsn:
        pytest.skip("PRIVATE_CONTENT_TEST_DSN not configured")
    import psycopg
    from psycopg import sql
    schema = "test_private_" + uuid.uuid4().hex
    monkeypatch.setenv("PANTHEON_ENV", "test")
    monkeypatch.setenv("AGORA_PRIVATE_CONTENT_DEV_KEK", "17" * 32)
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield dsn, schema
    finally:
        # Only this fixture's freshly-created UUID schema, never an app schema.
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def factory(private_db, **kwargs):
    return PostgresPrivateContentStore(dsn=private_db[0], schema=private_db[1], **kwargs)


def write(store, **overrides):
    return store.put(**dict(
        dict(tenant_id="tenant-a", owner_user_id="owner-a", workshop_id="workshop-a",
             event_id="event-a", content_type="text/plain", plaintext=b"private hypothesis alpha",
             retention_class="workshop_default", idempotency_key="message-a"), **overrides))


def read(store, ref, **overrides):
    return store.get_for_owner(**dict(
        dict(private_content_ref=ref, tenant_id="tenant-a", owner_user_id="owner-a",
             purpose="test_reload", request_id="test-read"), **overrides))


def test_new_store_instance_reads_durable_encrypted_body_and_retry(private_db):
    first = factory(private_db)
    descriptor = write(first)
    del first
    second = factory(private_db)
    assert read(second, descriptor.private_content_ref) == b"private hypothesis alpha"
    assert write(second) == descriptor
    with second._connect() as conn:
        row = conn.execute(f"SELECT * FROM {second._table}").fetchone()
    assert b"private hypothesis alpha" not in bytes(row["ciphertext"])
    assert b"private hypothesis alpha" not in bytes(row["encrypted_dek"])
    assert "payload_hash" not in row, "do not persist a plaintext fingerprint"
    assert len(second.audit_records) == 2
    with pytest.raises(ValueError, match="conflicts"):
        write(second, plaintext=b"different")


@pytest.mark.parametrize("scope", [{"tenant_id": "tenant-b"}, {"owner_user_id": "owner-b"}])
def test_wrong_scope_cannot_read_delete_or_discard(private_db, scope):
    store = factory(private_db)
    ref = write(store).private_content_ref
    with pytest.raises(PrivateContentAccessDenied):
        read(store, ref, **scope)
    args = dict(private_content_ref=ref, tenant_id="tenant-a", owner_user_id="owner-a")
    args.update(scope)
    with pytest.raises(PrivateContentAccessDenied):
        store.delete_for_owner(**args, request_id="bad-delete")
    with pytest.raises(PrivateContentAccessDenied):
        store.discard_failed_write(**args)
    assert read(factory(private_db), ref) == b"private hypothesis alpha"


def test_expiry_delete_and_failed_event_compensation(private_db):
    start = datetime(2026, 9, 13, tzinfo=timezone.utc)
    store = factory(private_db, now_fn=lambda: start)
    ref = write(store).private_content_ref
    later = factory(private_db, now_fn=lambda: start + timedelta(days=91))
    with pytest.raises(PrivateContentExpired):
        read(later, ref)
    assert later.expire_due(now=start + timedelta(days=91)) == 1
    with store._connect() as conn:
        row = conn.execute(f"SELECT * FROM {store._table} WHERE private_content_ref=%s", (ref,)).fetchone()
    assert row["ciphertext"] is None and bytes(row["encrypted_dek"]) == b""
    assert row["state"] == "deleted" and row["deleted_at"] is not None
    saved = write(store, idempotency_key="saved", event_id="saved").private_content_ref
    store.delete_for_owner(private_content_ref=saved, tenant_id="tenant-a", owner_user_id="owner-a", request_id="delete")
    with pytest.raises(PrivateContentAccessDenied):
        read(factory(private_db), saved)
    orphan = write(store, idempotency_key="orphan", event_id="orphan").private_content_ref
    store.discard_failed_write(private_content_ref=orphan, tenant_id="tenant-a", owner_user_id="owner-a")
    with pytest.raises(PrivateContentAccessDenied):
        read(factory(private_db), orphan)


def test_concurrent_instances_converge_on_one_content_identity(private_db):
    stores = [factory(private_db) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        refs = list(pool.map(lambda store: write(store).private_content_ref, stores))
    assert len(set(refs)) == 1
    with stores[0]._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {stores[0]._table}").fetchone()["n"] == 1


def test_missing_key_does_not_break_startup_or_accept_non_durable_write(private_db, monkeypatch):
    monkeypatch.delenv("AGORA_PRIVATE_CONTENT_DEV_KEK")
    store = factory(private_db)
    with pytest.raises(PrivateContentStoreUnavailable, match="key is not configured"):
        write(store)
    with store._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {store._table}").fetchone()["n"] == 0


def test_wrong_key_or_tampered_ciphertext_is_not_returned(private_db, monkeypatch):
    store = factory(private_db)
    ref = write(store).private_content_ref
    monkeypatch.setenv("AGORA_PRIVATE_CONTENT_DEV_KEK", "18" * 32)
    with pytest.raises(PrivateContentStoreUnavailable):
        read(factory(private_db), ref)
    with store._connect() as conn:
        conn.execute(f"UPDATE {store._table} SET ciphertext=%s WHERE private_content_ref=%s", (b"corrupt", ref))
    with pytest.raises(PrivateContentStoreUnavailable, match="ciphertext is invalid"):
        read(store, ref)


def test_separate_process_reads_same_body(private_db):
    ref = write(factory(private_db)).private_content_ref
    code = """
import os,sys
from services.control_plane.privacy.postgres_private_content_store import PostgresPrivateContentStore
s=PostgresPrivateContentStore(dsn=os.environ['PRIVATE_CONTENT_TEST_DSN'],schema=sys.argv[1])
assert s.get_for_owner(private_content_ref=sys.argv[2],tenant_id='tenant-a',owner_user_id='owner-a',
                       purpose='process_reload',request_id='new-process')==b'private hypothesis alpha'
print('RELOADED')
"""
    result = subprocess.run([sys.executable, "-c", code, private_db[1], ref],
                            capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.strip() == "RELOADED"


def test_durable_workshop_reconstructs_actual_body_after_new_store(private_db):
    from services.control_plane.bff.agora.strategy_workshop.store import PostgresWorkshopStore
    from services.control_plane.bff.agora.strategy_workshop.runner import run_reconstruction_worker

    dsn, schema = private_db
    workshop = PostgresWorkshopStore(dsn=dsn, schema=schema)
    workshop.create_session({"workshop_id": "workshop-a", "tenant_id": "tenant-a", "user_id": "owner-a"})
    ref = write(factory(private_db), plaintext=b"Hypothesis: momentum alpha. Universe: SPY equities.").private_content_ref
    workshop.create_event({"event_id": "event-a", "workshop_id": "workshop-a", "actor_type": "operator",
                           "event_type": "message", "private_content_ref": ref,
                           "redacted_summary": "Private workshop message"})
    del workshop
    args = dict(store=PostgresWorkshopStore(dsn=dsn, schema=schema), canonical=None,
                private_content_store=factory(private_db), workshop_id="workshop-a",
                tenant_id="tenant-a", user_id="owner-a")
    result = run_reconstruction_worker(**args)
    assert result["result"]["strategy_map"]["hypothesis"]["status"] == "confirmed"
    args["store"] = PostgresWorkshopStore(dsn=dsn, schema=schema)
    args["private_content_store"] = factory(private_db)
    replay = run_reconstruction_worker(**args)
    assert replay["job_status"] == "replayed" and replay["result"] == result["result"]

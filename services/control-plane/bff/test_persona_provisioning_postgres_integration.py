"""Real-Postgres concurrency/restart proof for the Persona provisioning ledger.

Run with ``PANTHEON_TEST_POSTGRES_DSN``.  The test owns and removes a unique
schema, so it can safely share the development Postgres instance.
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from services.control_plane.bff.persona_provisioning import (
    PostgresPersonaProvisioningStore,
    ProvisioningLeaseLost,
)


DSN = os.getenv("PANTHEON_TEST_POSTGRES_DSN", "").strip()


@pytest.mark.skipif(not DSN, reason="PANTHEON_TEST_POSTGRES_DSN is not configured")
def test_real_postgres_concurrent_reserve_lease_and_restart_are_rpo_zero() -> None:
    import psycopg

    schema = f"bff_persona_it_{uuid.uuid4().hex[:16]}"
    tenant_id = f"tenant-{uuid.uuid4().hex}"
    persona_id = f"persona-{uuid.uuid4().hex}"
    request_hash = f"sha256:{uuid.uuid4().hex}"
    normalized_name = f"real postgres {uuid.uuid4().hex}"
    worker_count = 12

    try:
        stores = [
            PostgresPersonaProvisioningStore(DSN, schema=schema)
            for _ in range(worker_count)
        ]
        reserve_barrier = Barrier(worker_count)

        def reserve(index: int):
            reserve_barrier.wait()
            return stores[index].reserve(
                tenant_id=tenant_id,
                idempotency_key=f"create-{index}",
                request_hash=request_hash,
                normalized_name=normalized_name,
                persona_id=persona_id,
                request_payload={"name": "Real Postgres Persona", "risk": "low"},
            )

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            reservations = list(executor.map(reserve, range(worker_count)))

        records = [record for record, _created in reservations]
        assert sum(1 for _record, created in reservations if created) == 1
        assert {record.idempotency_key for record in records} == {
            records[0].idempotency_key
        }
        assert {record.persona_id for record in records} == {persona_id}

        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {schema}.persona_provisioning "
                "WHERE tenant_id=%s AND normalized_name=%s",
                (tenant_id, normalized_name),
            )
            assert cur.fetchone() == (1,)

        canonical_key = records[0].idempotency_key
        lease_barrier = Barrier(worker_count)

        def acquire(index: int):
            lease_barrier.wait()
            return stores[index].acquire(
                tenant_id,
                canonical_key,
                lease_owner=f"replica-{index}",
                lease_seconds=60,
            )

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            acquired = list(executor.map(acquire, range(worker_count)))

        winners = [record for record in acquired if record is not None]
        assert len(winners) == 1
        winner = winners[0]
        winner.current_step = "authoritative_owner_receipts_checkpointed"
        winner.references = {
            "persona_id": persona_id,
            "deployment_saga_id": f"deployment-saga-{persona_id}",
        }
        saved = stores[0].checkpoint(
            winner,
            lease_owner=str(winner.lease_owner),
            lease_seconds=60,
        )
        stores[0].release(
            saved,
            lease_owner=str(winner.lease_owner),
            lease_seconds=60,
        )

        # A brand-new store object models a replacement BFF process.  It reads
        # the committed checkpoint and does not depend on in-process state.
        restarted = PostgresPersonaProvisioningStore(DSN, schema=schema)
        after_restart = restarted.get(tenant_id, canonical_key)
        assert after_restart is not None
        assert after_restart.current_step == "authoritative_owner_receipts_checkpointed"
        assert after_restart.references == winner.references

        replacement = restarted.acquire(
            tenant_id,
            canonical_key,
            lease_owner="replacement-replica",
            lease_seconds=60,
        )
        assert replacement is not None
        stale = winner
        stale.current_step = "stale-replica-must-not-commit"
        with pytest.raises(ProvisioningLeaseLost):
            restarted.checkpoint(
                stale,
                lease_owner=str(winner.lease_owner),
                lease_seconds=60,
            )
        restarted.release(
            replacement,
            lease_owner="replacement-replica",
            lease_seconds=60,
        )
    finally:
        if DSN:
            with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

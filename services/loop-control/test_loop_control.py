import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
import pytest
import jsonschema

import importlib
store_module = importlib.import_module("services.loop-control.store")
LoopControllerStore = store_module.LoopControllerStore

writer_module = importlib.import_module("services.loop-control.writer")
LoopControllerWriter = writer_module.LoopControllerWriter

projector_module = importlib.import_module("services.loop-control.projector")
project_controller_record_to_bff = projector_module.project_controller_record_to_bff


# Using the DSN available in the environment or default to local dev stack container address
DB_DSN = os.environ.get("DATABASE_URL") or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"


@pytest.mark.asyncio
async def test_store_crud_and_validation():
    store = LoopControllerStore(DB_DSN)
    
    # Clean up test record if exists
    conn = await store.store.connect() if hasattr(store, "store") and hasattr(store.store, "connect") else None
    # Let's do cleanup directly using asyncpg connection
    import asyncpg
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute("DELETE FROM loop_controller_records WHERE loop_id = 'test-loop-1'")
    await conn.close()

    # 1. Validation test (missing required field: truth_level)
    invalid_record = {
        "loop_id": "test-loop-1",
        "tenant_id": "default",
        "environment": "test",
        "controller_id": "ctrl-1",
        "controller_name": "TestController",
        "deployment_sha": "sha-123",
        # missing truth_level
    }
    with pytest.raises(jsonschema.ValidationError):
        store.validate_record(invalid_record)

    # 2. Valid record upsert
    now = datetime.now(timezone.utc)
    valid_record = {
        "loop_id": "test-loop-1",
        "tenant_id": "default",
        "environment": "test",
        "controller_id": "ctrl-1",
        "controller_name": "TestController",
        "deployment_sha": "sha-123",
        "desired_state_query": "SELECT 1",
        "actual_state_query": "SELECT 2",
        "last_heartbeat_at": now,
        "last_tick_at": now,
        "last_success_at": now,
        "truth_level": "reconciled_live_proof",
        "lease_expires_at": now + timedelta(seconds=60),
        "evidence_refs": ["ref-1", "ref-2"],
        "payload": {"key": "value"}
    }
    
    await store.upsert_record(valid_record)
    
    # 3. Get record
    fetched = await store.get_record("test-loop-1", "default", "test")
    assert fetched is not None
    assert fetched["controller_id"] == "ctrl-1"
    assert fetched["controller_name"] == "TestController"
    assert fetched["desired_state_query"] == "SELECT 1"
    assert fetched["truth_level"] == "reconciled_live_proof"

    # 4. List records
    records = await store.list_records("default", "test")
    assert len(records) >= 1
    assert any(r["loop_id"] == "test-loop-1" for r in records)


@pytest.mark.asyncio
async def test_writer_sdk():
    writer = LoopControllerWriter(
        dsn=DB_DSN,
        tenant_id="default",
        environment="test",
        controller_id="ctrl-writer",
        controller_name="WriterController",
        deployment_sha="sha-writer"
    )

    # Clean up
    import asyncpg
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute("DELETE FROM loop_controller_records WHERE loop_id = 'test-writer-loop'")
    await conn.close()

    # Record heartbeat
    await writer.record_heartbeat(
        loop_id="test-writer-loop",
        desired_state_query="desired-q",
        actual_state_query="actual-q",
        backlog=10,
        lag=2,
        evidence_refs=["ref-w1"]
    )

    # Fetch to check
    store = LoopControllerStore(DB_DSN)
    record = await store.get_record("test-writer-loop", "default", "test")
    assert record is not None
    assert record["controller_id"] == "ctrl-writer"
    assert record["desired_state_query"] == "desired-q"
    assert record["actual_state_query"] == "actual-q"
    assert record["backlog"] == 10
    assert record["lag"] == 2
    
    # Record success
    await writer.record_success(
        loop_id="test-writer-loop",
        summary="Run was success",
        evidence_refs=["ref-w2"]
    )
    
    record = await store.get_record("test-writer-loop", "default", "test")
    assert record["last_success_at"] is not None
    
    # Record failure
    await writer.record_failure(
        loop_id="test-writer-loop",
        reason="Something crashed",
        dlq_count=5
    )
    
    record = await store.get_record("test-writer-loop", "default", "test")
    assert record["last_failure_at"] is not None
    assert record["last_failure_reason"] == "Something crashed"
    assert record["dlq_count"] == 5


@pytest.mark.asyncio
async def test_lease_safety_and_stale_heartbeats():
    # 1. Lease safety
    writer1 = LoopControllerWriter(DB_DSN, tenant_id="default", environment="test", controller_id="ctrl-lease-1")
    writer2 = LoopControllerWriter(DB_DSN, tenant_id="default", environment="test", controller_id="ctrl-lease-2")

    import asyncpg
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute("DELETE FROM loop_controller_records WHERE loop_id = 'test-lease-loop'")
    await conn.close()

    # Writer 1 locks lease for 10 seconds
    await writer1.record_heartbeat("test-lease-loop", lease_duration_seconds=10)

    # Writer 2 tries to overwrite, should raise ValueError
    with pytest.raises(ValueError) as excinfo:
        await writer2.record_heartbeat("test-lease-loop")
    assert "Active lease exists" in str(excinfo.value)

    # Writer 1 should be able to update its own lease
    await writer1.record_heartbeat("test-lease-loop", lease_duration_seconds=20)

    # 2. Stale heartbeat test
    store = LoopControllerStore(DB_DSN)
    rec_original = await store.get_record("test-lease-loop", "default", "test")
    original_heartbeat = rec_original["last_heartbeat_at"]

    # Try to write with a heartbeat 1 hour in the past
    past_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
    
    # Use store directly or writer with past heartbeat
    writer_stale = LoopControllerWriter(DB_DSN, tenant_id="default", environment="test", controller_id="ctrl-lease-1")
    await writer_stale._write_status("test-lease-loop", "reconciled_live_proof", last_heartbeat_at=past_heartbeat)

    # Fetch again, heartbeat should NOT have changed (ignored since it's in the past)
    rec_after = await store.get_record("test-lease-loop", "default", "test")
    assert rec_after["last_heartbeat_at"] == original_heartbeat


def test_projector():
    # Test project_controller_record_to_bff
    row = {
        "loop_id": "test-loop-proj",
        "tenant_id": "default",
        "environment": "dev",
        "controller_id": "ctrl-proj",
        "controller_name": "ProjController",
        "deployment_sha": "sha-proj",
        "desired_state_query": "q-des",
        "actual_state_query": "q-act",
        "last_heartbeat_at": datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        "last_tick_at": datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        "last_success_at": datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        "last_failure_at": None,
        "last_failure_reason": None,
        "last_repair_at": None,
        "last_repair_reason": None,
        "backlog": 3,
        "lag": 1,
        "dlq_count": 0,
        "evidence_refs": '["ref-p1"]',
        "truth_level": "reconciled_live_proof",
        "lease_expires_at": datetime(2026, 7, 13, 20, 5, 0, tzinfo=timezone.utc),
        "payload": '{"last_success_summary": "Successfully projected"}'
    }

    projected = project_controller_record_to_bff(row)
    
    assert projected["loop_id"] == "test-loop-proj"
    assert projected["controller_health"]["status"] == "ok"
    assert projected["controller_health"]["controller_name"] == "ProjController"
    assert projected["last_success"]["summary"] == "Successfully projected"
    assert projected["evidence_packet"]["highest_truth_level"] == "reconciled_live_proof"
    assert "ref-p1" in projected["refs"]

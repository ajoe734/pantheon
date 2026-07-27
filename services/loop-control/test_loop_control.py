import asyncio
from datetime import datetime, timedelta, timezone
import pytest
import jsonschema

import importlib
store_module = importlib.import_module("services.loop-control.store")
LoopControllerStore = store_module.LoopControllerStore

writer_module = importlib.import_module("services.loop-control.writer")
LoopControllerWriter = writer_module.LoopControllerWriter
ControllerLeaseConflict = store_module.ControllerLeaseConflict

projector_module = importlib.import_module("services.loop-control.projector")
project_controller_record_to_bff = projector_module.project_controller_record_to_bff
conformance_module = importlib.import_module("services.loop-control.conformance")
CANONICAL_LOOP_IDS = conformance_module.CANONICAL_LOOP_IDS
CONTROLLER_RECORD_FIELDS = conformance_module.CONTROLLER_RECORD_FIELDS
assert_controller_record_conforms = conformance_module.assert_controller_record_conforms


@pytest.mark.asyncio
async def test_store_crud_and_validation(loop_control_db_dsn):
    store = LoopControllerStore(loop_control_db_dsn)

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
        "desired_state": {
            "present": True,
            "source": "test-desired-authority",
            "checked_at": now,
        },
        "downstream_actual_state": {
            "status": "ready",
            "source": "test-actual-authority",
            "checked_at": now,
        },
        "last_heartbeat_at": now,
        "last_tick_at": now,
        "last_success_at": now,
        "truth_level": "reconciled_live_proof",
        "lease_token": "lease-token-1",
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
    assert fetched["desired_state"]["present"] is True
    assert fetched["desired_state"]["checked_at"] == now.isoformat()
    assert fetched["downstream_actual_state"]["status"] == "ready"
    assert fetched["downstream_actual_state"]["checked_at"] == now.isoformat()

    # 4. List records
    records = await store.list_records("default", "test")
    assert len(records) >= 1
    assert any(r["loop_id"] == "test-loop-1" for r in records)


@pytest.mark.asyncio
async def test_writer_sdk(loop_control_db_dsn):
    writer = LoopControllerWriter(
        dsn=loop_control_db_dsn,
        tenant_id="default",
        environment="test",
        controller_id="ctrl-writer",
        controller_name="WriterController",
        deployment_sha="sha-writer"
    )

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
    store = LoopControllerStore(loop_control_db_dsn)
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
async def test_lease_safety_and_stale_heartbeats(loop_control_db_dsn):
    # 1. Lease safety
    writer1 = LoopControllerWriter(
        loop_control_db_dsn,
        tenant_id="default",
        environment="test",
        controller_id="ctrl-lease-1",
        lease_duration_seconds=20,
    )
    writer2 = LoopControllerWriter(
        loop_control_db_dsn,
        tenant_id="default",
        environment="test",
        controller_id="ctrl-lease-2",
    )

    # Writer 1 locks lease for 10 seconds
    await writer1.record_heartbeat("test-lease-loop", lease_duration_seconds=10)

    # Writer 2 tries to overwrite, should raise ValueError
    with pytest.raises(ValueError) as excinfo:
        await writer2.record_heartbeat("test-lease-loop")
    assert "Active lease exists" in str(excinfo.value)

    # Writer 1 should be able to update its own lease
    await writer1.record_heartbeat("test-lease-loop", lease_duration_seconds=20)

    # 2. Stale heartbeat test
    store = LoopControllerStore(loop_control_db_dsn)
    rec_original = await store.get_record("test-lease-loop", "default", "test")
    original_heartbeat = rec_original["last_heartbeat_at"]

    # Try to write with a heartbeat 1 hour in the past
    past_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)

    # Use store directly or writer with past heartbeat
    await writer1._write_status(
        "test-lease-loop",
        "reconciled_live_proof",
        last_heartbeat_at=past_heartbeat,
    )

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
        "desired_state": {
            "present": True,
            "source": "test-desired-authority",
            "checked_at": datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        },
        "downstream_actual_state": {
            "status": "ready",
            "source": "test-actual-authority",
            "checked_at": datetime(2026, 7, 13, 20, 0, 30, tzinfo=timezone.utc),
        },
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
        "lease_token": "lease-token-projector",
        "lease_expires_at": datetime(2026, 7, 13, 20, 5, 0, tzinfo=timezone.utc),
        "payload": '{"last_success_summary": "Successfully projected"}'
    }

    projected = project_controller_record_to_bff(
        row,
        now=datetime(2026, 7, 13, 20, 1, 0, tzinfo=timezone.utc),
    )

    assert projected["loop_id"] == "test-loop-proj"
    assert projected["controller_health"]["status"] == "healthy"
    assert projected["controller_health"]["controller_name"] == "ProjController"
    assert projected["last_success"]["summary"] == "Successfully projected"
    assert projected["evidence_packet"]["highest_truth_level"] == "reconciled_live_proof"
    assert "ref-p1" in projected["refs"]
    assert projected["desired_state_presence"]["present"] is True
    assert projected["desired_state_presence"]["authoritative"] is True
    assert projected["downstream_actual_state"]["status"] == "ready"
    assert projected["downstream_actual_state"]["authoritative"] is True


def test_projector_does_not_manufacture_evidence_or_healthy_expired_lease():
    row = {
        "loop_id": "test-loop-expired",
        "tenant_id": "default",
        "environment": "dev",
        "controller_id": "ctrl-expired",
        "controller_name": "ExpiredController",
        "deployment_sha": "sha-expired",
        "last_heartbeat_at": datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        "last_success_at": datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        "lease_expires_at": datetime(2026, 7, 13, 20, 1, 0, tzinfo=timezone.utc),
        "lease_token": "lease-token-expired",
        "evidence_refs": [],
        "truth_level": "reconciled_live_proof",
        "payload": {},
    }

    projected = project_controller_record_to_bff(
        row,
        now=datetime(2026, 7, 13, 20, 2, 0, tzinfo=timezone.utc),
    )

    assert projected["refs"] == []
    assert projected["evidence_packet"]["refs"] == []
    assert projected["truth_status"] == "missing_evidence"
    assert projected["controller_health"]["status"] == "degraded"
    assert projected["controller_health"]["degraded_reason"] == "controller lease expired"


def test_projector_reports_later_failure_as_unhealthy():
    row = {
        "loop_id": "test-loop-failed",
        "tenant_id": "default",
        "environment": "dev",
        "controller_id": "ctrl-failed",
        "controller_name": "FailedController",
        "deployment_sha": "sha-failed",
        "last_heartbeat_at": datetime(2026, 7, 13, 20, 2, 0, tzinfo=timezone.utc),
        "last_success_at": datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        "last_failure_at": datetime(2026, 7, 13, 20, 1, 0, tzinfo=timezone.utc),
        "last_failure_reason": "downstream unavailable",
        "lease_expires_at": datetime(2026, 7, 13, 20, 5, 0, tzinfo=timezone.utc),
        "lease_token": "lease-token-failed",
        "evidence_refs": ["runtime:failed-controller"],
        "truth_level": "reconciled_live_proof",
        "payload": {},
    }

    projected = project_controller_record_to_bff(
        row,
        now=datetime(2026, 7, 13, 20, 2, 0, tzinfo=timezone.utc),
    )

    assert projected["controller_health"]["status"] == "unhealthy"
    assert projected["controller_health"]["degraded_reason"] == "downstream unavailable"


def test_projector_stale_heartbeat_and_query_text_do_not_manufacture_actual_state():
    row = {
        "loop_id": "test-loop-stale",
        "tenant_id": "default",
        "environment": "dev",
        "controller_id": "ctrl-stale",
        "controller_name": "StaleController",
        "last_heartbeat_at": datetime(2026, 7, 13, 19, 0, 0, tzinfo=timezone.utc),
        "last_tick_at": datetime(2026, 7, 13, 19, 0, 0, tzinfo=timezone.utc),
        "actual_state_query": "SELECT claimed_actual_state",
        "lease_token": "lease-token-stale",
        "evidence_refs": ["runtime:stale-controller"],
        "truth_level": "scheduled_tick",
        "payload": {},
    }

    projected = project_controller_record_to_bff(
        row,
        now=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
    )

    assert projected["controller_health"]["status"] == "degraded"
    assert projected["controller_health"]["degraded_reason"] == "controller heartbeat is stale"
    assert projected["downstream_actual_state"]["status"] == "unobserved"
    assert projected["downstream_actual_state"]["authoritative"] is False
    assert projected["downstream_actual_state"]["checked_at"] is None
    assert projected["evidence_packet"]["captured_at"].startswith("2026-07-13T19:00:00")


@pytest.mark.asyncio
async def test_concurrent_partial_updates_preserve_monotonic_fields(
    loop_control_db_dsn,
):
    loop_id = "test-loop-concurrent"
    writer = LoopControllerWriter(
        loop_control_db_dsn,
        tenant_id="tenant-concurrency",
        environment="test",
        controller_id="ctrl-concurrent",
        lease_duration_seconds=30,
    )

    await writer.record_heartbeat(
        loop_id,
        backlog=1,
        lag=1,
        dlq_count=0,
        evidence_refs=["runtime:initial"],
    )
    await asyncio.gather(
        writer.record_success(
            loop_id,
            summary="concurrent success",
            evidence_refs=["runtime:success"],
        ),
        writer.record_failure(
            loop_id,
            "concurrent failure",
            dlq_count=2,
            evidence_refs=["runtime:failure"],
        ),
        writer.record_heartbeat(
            loop_id,
            backlog=7,
            lag=3,
            evidence_refs=["runtime:heartbeat"],
        ),
    )

    record = await writer.store.get_record(
        loop_id,
        "tenant-concurrency",
        "test",
    )
    assert record["last_success_at"] is not None
    assert record["last_failure_at"] is not None
    assert record["last_failure_reason"] == "concurrent failure"
    assert record["backlog"] == 7
    assert record["lag"] == 3
    assert record["dlq_count"] == 2
    assert set(record["evidence_refs"]) == {
        "runtime:initial",
        "runtime:success",
        "runtime:failure",
        "runtime:heartbeat",
    }


@pytest.mark.asyncio
async def test_store_isolates_same_loop_by_tenant_and_environment(
    loop_control_db_dsn,
):
    loop_id = "test-loop-isolation"
    scopes = (
        ("tenant-a", "dev"),
        ("tenant-b", "dev"),
        ("tenant-a", "prod"),
    )
    writers = {
        scope: LoopControllerWriter(
            loop_control_db_dsn,
            tenant_id=scope[0],
            environment=scope[1],
            controller_id=f"controller-{scope[0]}-{scope[1]}",
        )
        for scope in scopes
    }

    for index, scope in enumerate(scopes):
        await writers[scope].record_heartbeat(
            loop_id,
            backlog=index,
            evidence_refs=[f"runtime:{scope[0]}:{scope[1]}"],
        )

    store = LoopControllerStore(loop_control_db_dsn)
    for index, scope in enumerate(scopes):
        record = await store.get_record(loop_id, scope[0], scope[1])
        assert record is not None
        assert record["tenant_id"] == scope[0]
        assert record["environment"] == scope[1]
        assert record["backlog"] == index
        scoped_records = await store.list_records(scope[0], scope[1])
        matches = [row for row in scoped_records if row["loop_id"] == loop_id]
        assert len(matches) == 1
        assert matches[0]["controller_id"] == f"controller-{scope[0]}-{scope[1]}"

    assert await store.get_record(loop_id, "tenant-b", "prod") is None


@pytest.mark.asyncio
async def test_normal_writes_renew_fence_and_stale_generation_is_rejected(
    loop_control_db_dsn,
):
    loop_id = "test-loop-fenced-generation"
    old_writer = LoopControllerWriter(
        loop_control_db_dsn,
        tenant_id="tenant-fence",
        environment="test",
        controller_id="stable-controller-id",
        lease_duration_seconds=30,
    )
    new_writer = LoopControllerWriter(
        loop_control_db_dsn,
        tenant_id="tenant-fence",
        environment="test",
        controller_id="stable-controller-id",
        lease_duration_seconds=30,
    )

    await old_writer.record_heartbeat(loop_id, lease_duration_seconds=2)
    first = await old_writer.store.get_record(loop_id, "tenant-fence", "test")
    await old_writer.record_success(loop_id)
    renewed = await old_writer.store.get_record(loop_id, "tenant-fence", "test")
    assert renewed["lease_expires_at"] > first["lease_expires_at"]
    assert renewed["lease_token"] == old_writer.lease_token

    import asyncpg
    conn = await asyncpg.connect(loop_control_db_dsn)
    await conn.execute(
        """
        UPDATE loop_controller_records
        SET lease_expires_at = clock_timestamp() - interval '1 second'
        WHERE loop_id=$1 AND tenant_id=$2 AND environment=$3
        """,
        loop_id,
        "tenant-fence",
        "test",
    )
    await conn.close()

    await new_writer.record_heartbeat(loop_id)
    with pytest.raises(ControllerLeaseConflict):
        await old_writer.record_failure(loop_id, "stale generation")

    final = await new_writer.store.get_record(loop_id, "tenant-fence", "test")
    assert final["lease_token"] == new_writer.lease_token
    assert final["last_failure_reason"] is None


@pytest.mark.parametrize("loop_id", CANONICAL_LOOP_IDS)
def test_all_twelve_loops_share_the_complete_controller_contract(loop_id):
    now = datetime.now(timezone.utc)
    record = {
        "loop_id": loop_id,
        "tenant_id": "tenant-conformance",
        "environment": "test",
        "controller_id": f"{loop_id}-controller",
        "controller_name": f"{loop_id}-controller",
        "deployment_sha": "sha-conformance",
        "desired_state_query": "domain desired-state query",
        "actual_state_query": "domain terminal actual-state query",
        "desired_state": {
            "present": True,
            "source": "domain-desired-store",
            "checked_at": now,
        },
        "downstream_actual_state": {
            "status": "ready",
            "source": "domain-terminal-store",
            "checked_at": now,
        },
        "last_heartbeat_at": now,
        "last_tick_at": now,
        "last_success_at": now,
        "last_failure_at": None,
        "last_failure_reason": None,
        "last_repair_at": None,
        "last_repair_reason": None,
        "backlog": 0,
        "lag": 0,
        "dlq_count": 0,
        "evidence_refs": [f"runtime:{loop_id}"],
        "truth_level": "reconciled_live_proof",
        "lease_token": f"fence-{loop_id}",
        "lease_expires_at": now + timedelta(seconds=60),
        "payload": {},
    }

    assert set(CONTROLLER_RECORD_FIELDS) == set(record)
    assert_controller_record_conforms(record)

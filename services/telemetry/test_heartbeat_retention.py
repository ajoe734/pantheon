"""Focused tests for services/telemetry/heartbeat_retention.py (TELEMETRY-HEARTBEAT-RETENTION-20261117)."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any
import urllib.parse
import uuid

import pytest

from scripts.capture_canonical_telemetry_baseline import (
    capture_telemetry_baseline,
    validate_baseline_artifact,
)
from scripts.deploy.staging_sanitize import OWNER_STORE_PATHS, verify_backup_restore_readability
from scripts.project_console_data_rankings_to_bff_surfaces import project_rankings
from services.control_plane.bff.personas.service import _pm12_quarter_metrics
from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    propagate_envelope,
)
from services.telemetry.heartbeat_retention import (
    HEARTBEAT_READERS,
    HEARTBEAT_SUMMARY_TABLE,
    INGESTED_SEQ_CONSUMERS,
    HeartbeatRetentionJob,
    HeartbeatRetentionReaderBlocker,
    check_readers_against_retention_window,
    compute_hourly_summaries_from_rows,
    parse_retention_days,
    rebuild_runtime_summaries_from_db,
    run_heartbeat_retention,
    truncate_utc_hour,
)
from services.telemetry.ingest_svc import (
    build_postgres_event_reader,
    build_postgres_write_fn,
)
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.trade_journey.lifecycle_projector import (
    PostgresLifecycleSource,
    RelationalLifecycleProjector,
)
from services.trade_journey.projection_store import ProjectionStore

ROOT = Path(__file__).resolve().parents[2]
MIGRATE = ROOT / "scripts/db_migrate.sh"
TRADE_JOURNEY_MIGRATION = (
    ROOT / "services/trade_journey/migrations/001_create_trade_journey_projection_schema.sql"
)

HOSTED_RUNTIME_IDS = ("rt-49c1c3d1", "rt-3b65a843", "rt-ad81501b", "rt-3252fa75")


@pytest.fixture(scope="module")
def pg_cluster():
    explicit = os.environ.get("TELEMETRY_TEST_PG_DSN")
    if explicit:
        yield explicit
        return
    if shutil.which("docker") is None:
        pytest.skip("Docker not available for live PostgreSQL tests")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("Docker daemon not reachable for live PostgreSQL tests")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    cname = f"pantheon-hb-ret-{uuid.uuid4().hex[:10]}"
    started = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            cname,
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-e",
            "POSTGRES_USER=postgres",
            "-e",
            "POSTGRES_DB=postgres",
            "-p",
            f"127.0.0.1:{port}:5432",
            "postgres:16-alpine",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"Failed to start PostgreSQL container: {started.stderr}")
    dsn = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
    import time
    import asyncpg  # type: ignore[import]

    deadline = time.monotonic() + 15
    connected = False
    while time.monotonic() < deadline:
        try:
            async def _probe():
                conn = await asyncpg.connect(dsn, timeout=1.0)
                await conn.close()

            asyncio.run(_probe())
            connected = True
            break
        except Exception:
            time.sleep(0.2)
    if not connected:
        subprocess.run(["docker", "rm", "-f", "-v", cname], capture_output=True)
        pytest.skip("PostgreSQL container did not become healthy in time")
    try:
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", cname], capture_output=True)


async def _create_migrated_db(admin_dsn: str, db_name: str, *, with_trade_journey: bool = True) -> str:
    import asyncpg  # type: ignore[import]

    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {db_name}")
        await conn.execute(f"CREATE DATABASE {db_name}")
    finally:
        await conn.close()
    parsed = urllib.parse.urlparse(admin_dsn)
    db_dsn = parsed._replace(path=f"/{db_name}").geturl()

    sql = subprocess.run(
        ["bash", str(MIGRATE), "--print-sql"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    conn = await asyncpg.connect(db_dsn)
    try:
        await conn.execute(sql)
        if with_trade_journey:
            tj_sql = TRADE_JOURNEY_MIGRATION.read_text(encoding="utf-8")
            await conn.execute(tj_sql)
    finally:
        await conn.close()
    return db_dsn


def _build_hosted_event(
    event_type: str,
    *,
    runtime_id: str,
    created_at: datetime,
    event_id: str | None = None,
    connectivity_status: str = "connected",
    metrics: dict[str, Any] | None = None,
    metadata_extra: dict[str, Any] | None = None,
    pnl_as_of: str | None = None,
    journey_id: str | None = None,
    loop_run_id: str | None = None,
    sequence_no: int | None = None,
    causal_parent_id: str | None = None,
) -> dict[str, Any]:
    iso_ts = created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    binding_id = f"rb-{runtime_id}"
    pool_id = f"pool-{runtime_id}"
    artifact_id = f"art-{runtime_id}"
    plan_id = f"plan-{runtime_id}"
    pcb_id = f"pcb-{runtime_id}"
    persona_id = f"persona-{runtime_id}"
    strategy_id = f"strat-{runtime_id}"
    trace_id = f"trace-{runtime_id}"
    eid = event_id or f"evt-{runtime_id}-{event_type}-{uuid.uuid4().hex[:10]}"

    base_meta: dict[str, Any] = {
        "tenant_id": "tenant-dev",
        "runtime_package": "paper_execution_runtime",
        "runtime_binding_effective_at": "2026-10-01T00:00:00Z",
        "engine_bridge_repo": "ajoe734/pantheon-lean.git",
        "engine_bridge_path": "pantheon/lean",
        "engine_bridge_commit": "22b89367a56cdbb4fb8a7345fc7c4ad1d293a118",
        "runtime_adapter_version": "1.0.0",
        "persona_id": persona_id,
    }
    if event_type == "heartbeat":
        base_meta.update(
            {
                "connectivity_status": connectivity_status,
                "broker_status": "ok",
                "runtime_heartbeat": {
                    "connectivity_status": connectivity_status,
                    "broker_status": "ok",
                    "queue_lag_ms": 4.5,
                    "event_delivery_lag_ms": 2.1,
                },
                "performance_telemetry": {
                    "status": "unchanged",
                    "code": "performance_sample_unchanged",
                    "as_of": "2026-11-15T00:00:00Z",
                    "fill_count": 1,
                },
                "member_virtual_books": [
                    {
                        "persona_id": persona_id,
                        "allocated_capital": 250000.0,
                        "nav": 255000.0,
                    }
                ],
            }
        )
    if metadata_extra:
        base_meta.update(metadata_extra)

    ev: dict[str, Any] = {
        "event_id": eid,
        "event_type": event_type,
        "created_at": iso_ts,
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "deployment_stage": "paper",
        "execution_mode": "paper",
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "capital_pool_id": pool_id,
        "artifact_id": artifact_id,
        "artifact_version": "1.0.0",
        "plan_id": plan_id,
        "persona_capital_binding_id": pcb_id,
        "trace_id": trace_id,
        "target": {
            "strategy_id": strategy_id,
            "artifact_version": "1.0.0",
            "artifact_type": "execution_bundle",
        },
        "metrics": (
            metrics
            if metrics is not None
            else ({"heartbeat": 1} if event_type == "heartbeat" else {"action": event_type})
        ),
        "metadata": base_meta,
    }
    if pnl_as_of:
        ev["pnl_as_of"] = pnl_as_of
    if journey_id and loop_run_id and sequence_no is not None:
        envelope: dict[str, Any] = {
            "schema_version": "pantheon.correlation-envelope.v1",
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "execution_mode": "paper",
            "source_mode": "live",
            "journey_id": journey_id,
            "run_id": loop_run_id,
            "loop_run_id": loop_run_id,
            "signal_id": f"sig-{journey_id}",
            "strategy_id": strategy_id,
            "runtime_id": runtime_id,
            "binding_id": binding_id,
            "capital_pool_id": pool_id,
            "persona_id": persona_id,
            "persona_capital_binding_id": pcb_id,
            "artifact_id": artifact_id,
            "artifact_version": "1.0.0",
            "plan_id": plan_id,
            "trace_id": trace_id,
            "sequence_no": sequence_no,
            "occurred_at": iso_ts,
        }
        if causal_parent_id:
            envelope["causation_event_id"] = causal_parent_id
            ev["causal_parent_id"] = causal_parent_id
            ev["metadata"]["causal_parent_id"] = causal_parent_id
        ev["journey_id"] = journey_id
        ev["run_id"] = loop_run_id
        ev["loop_run_id"] = loop_run_id
        ev["signal_id"] = f"sig-{journey_id}"
        ev["sequence_no"] = sequence_no
        ev["source_mode"] = "live"
        ev["authority_refs"] = {"persona_id": persona_id}
        ev["correlation_envelope"] = envelope
        ev["metadata"]["correlation_envelope"] = envelope
        ev["metadata"]["sequence_no"] = sequence_no
    return ev


def _strip_volatile_summary_fields(summary: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(summary)
    cleaned.pop("projection_updated_at", None)
    return cleaned


# ---------------------------------------------------------------------------
# Unit tests for inventories, reader window gate, and summary folding
# ---------------------------------------------------------------------------


def test_ingested_seq_consumers_and_heartbeat_readers_inventories_are_complete() -> None:
    consumer_modules = {item["module"] for item in INGESTED_SEQ_CONSUMERS}
    assert "services/trade_journey/lifecycle_projector.py" in consumer_modules
    assert "services/trade_journey/incremental_materializer.py" in consumer_modules
    assert "services/trade_journey/projection_store.py" in consumer_modules
    assert "services/telemetry/runtime_summary.py" in consumer_modules
    assert "services/telemetry/ingest_svc.py" in consumer_modules
    assert "services/control-plane/bff/trade_journey_projection_store.py" in consumer_modules
    assert "services/control-plane/bff/agora/operational_readiness.py" in consumer_modules
    assert "services/trade_journey/hosted_lifecycle_probe.py" in consumer_modules
    assert "services/trade_journey/hosted_lifecycle_stimulus.py" in consumer_modules
    assert all(item["stalls_on_ingested_seq_gaps"] is False for item in INGESTED_SEQ_CONSUMERS)

    check = check_readers_against_retention_window(3.0)
    assert check["all_readers_within_window"] is True
    assert check["max_reader_required_age_seconds"] <= 3 * 86400
    assert len(HEARTBEAT_READERS) >= 7


def test_reader_requiring_older_heartbeats_blocks_retention_without_deleting() -> None:
    custom_readers = [
        *HEARTBEAT_READERS,
        {
            "reader_id": "hypothetical_long_horizon_heartbeat_reader",
            "module": "services/hypothetical/reader.py",
            "oldest_heartbeat_needed_seconds": 5 * 86400,
        },
    ]
    with pytest.raises(HeartbeatRetentionReaderBlocker) as exc_info:
        check_readers_against_retention_window(3.0, readers=custom_readers)
    assert exc_info.value.reader_id == "hypothetical_long_horizon_heartbeat_reader"
    assert exc_info.value.required_seconds == 5 * 86400


def test_compute_hourly_summaries_tracks_count_bounds_largest_gap_and_last_status() -> None:
    rows = [
        {
            "event_id": "hb-1",
            "event_type": "heartbeat",
            "created_at": "2026-11-10T08:00:02Z",
            "ingested_seq": 10,
            "runtime_id": "rt-49c1c3d1",
            "connectivity_status": "connected",
        },
        {
            "event_id": "hb-2",
            "event_type": "heartbeat",
            "created_at": "2026-11-10T08:00:12Z",
            "ingested_seq": 11,
            "runtime_id": "rt-49c1c3d1",
            "connectivity_status": "connected",
        },
        {
            "event_id": "hb-3",
            "event_type": "heartbeat",
            "created_at": "2026-11-10T08:01:00Z",
            "ingested_seq": 12,
            "runtime_id": "rt-49c1c3d1",
            "connectivity_status": "degraded",
        },
    ]
    summaries = compute_hourly_summaries_from_rows(rows)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.runtime_id == "rt-49c1c3d1"
    assert s.hour_bucket == datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    assert s.heartbeat_count == 3
    assert s.first_heartbeat_at == datetime(2026, 11, 10, 8, 0, 2, tzinfo=timezone.utc)
    assert s.last_heartbeat_at == datetime(2026, 11, 10, 8, 1, 0, tzinfo=timezone.utc)
    assert s.largest_gap_seconds == pytest.approx(48.0)
    assert s.last_connectivity_status == "degraded"


# ---------------------------------------------------------------------------
# Live PostgreSQL tests for AC 1, 2, 3, 4, 5, 8, 9, 10
# ---------------------------------------------------------------------------


def test_retention_summarizes_hours_before_deleting_in_bounded_batches_and_preserves_other_events(
    pg_cluster: str,
) -> None:
    """AC 1 & AC 2: Multi-batch deletion within an hour summarizes the full hour first and never deletes summary or non-heartbeat rows."""
    import asyncpg  # type: ignore[import]

    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_hb_batches", with_trade_journey=False))
    now = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    old_hour_1 = datetime(2026, 11, 10, 14, 0, 0, tzinfo=timezone.utc)
    old_hour_2 = datetime(2026, 11, 11, 9, 0, 0, tzinfo=timezone.utc)
    recent_hour = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)

    write_fn = build_postgres_write_fn(db_dsn)
    batch: list[dict[str, Any]] = []

    # 10 old heartbeats in old_hour_1 with gaps: 5s, 5s, ..., and one 25s gap; last status = degraded
    ts = old_hour_1 + timedelta(seconds=10)
    for i in range(10):
        if i == 5:
            ts += timedelta(seconds=25)
        elif i > 0:
            ts += timedelta(seconds=5)
        batch.append(
            _build_hosted_event(
                "heartbeat",
                runtime_id="rt-49c1c3d1",
                created_at=ts,
                event_id=f"hb-old-h1-{i}",
                connectivity_status="degraded" if i == 9 else "connected",
            )
        )

    # 6 old heartbeats in old_hour_2
    for i in range(6):
        batch.append(
            _build_hosted_event(
                "heartbeat",
                runtime_id="rt-3b65a843",
                created_at=old_hour_2 + timedelta(seconds=i * 15),
                event_id=f"hb-old-h2-{i}",
                connectivity_status="connected",
            )
        )

    # Old non-heartbeat events (must NEVER be deleted)
    batch.append(
        _build_hosted_event(
            "deploy_started",
            runtime_id="rt-49c1c3d1",
            created_at=old_hour_1,
            event_id="evt-deploy-old-1",
        )
    )
    batch.append(
        _build_hosted_event(
            "pnl_snapshot",
            runtime_id="rt-49c1c3d1",
            created_at=old_hour_1 + timedelta(minutes=30),
            event_id="evt-pnl-old-1",
            metrics={"pnl": 1500.0},
        )
    )

    # Recent heartbeats within the 3-day window (must be retained)
    for i in range(4):
        batch.append(
            _build_hosted_event(
                "heartbeat",
                runtime_id="rt-49c1c3d1",
                created_at=recent_hour + timedelta(seconds=i * 15),
                event_id=f"hb-recent-{i}",
            )
        )

    res_write = asyncio.run(write_fn(batch))
    assert res_write.success

    # Run with small batch_size=4 and max_batches=2 first to prove bounded multi-step batching
    run1 = asyncio.run(
        run_heartbeat_retention(
            db_dsn,
            now=now,
            retention_days=3,
            batch_size=4,
            max_batches=2,
            statement_timeout_ms=10000,
        )
    )
    assert run1.status == "ok"
    assert run1.batches_executed == 2
    assert run1.rows_deleted == 8

    # Finish remaining old heartbeats in run2
    run2 = asyncio.run(
        run_heartbeat_retention(
            db_dsn,
            now=now,
            retention_days=3,
            batch_size=4,
            max_batches=10,
            statement_timeout_ms=10000,
        )
    )
    assert run2.status == "ok"
    assert run2.rows_deleted == 8

    async def _verify():
        conn = await asyncpg.connect(db_dsn)
        try:
            remaining = await conn.fetch(
                "SELECT event_id, event_type FROM public.telemetry_events ORDER BY ingested_seq ASC"
            )
            remaining_ids = [r["event_id"] for r in remaining]
            assert "evt-deploy-old-1" in remaining_ids
            assert "evt-pnl-old-1" in remaining_ids
            assert [f"hb-recent-{i}" for i in range(4)] == [
                r["event_id"] for r in remaining if r["event_type"] == "heartbeat"
            ]

            summaries = await conn.fetch(
                f"SELECT * FROM {HEARTBEAT_SUMMARY_TABLE} ORDER BY hour_bucket ASC, runtime_id ASC"
            )
            assert len(summaries) == 2

            s1 = summaries[0]
            assert s1["runtime_id"] == "rt-49c1c3d1"
            assert s1["hour_bucket"] == old_hour_1
            assert s1["heartbeat_count"] == 10
            assert s1["first_heartbeat_at"] == old_hour_1 + timedelta(seconds=10)
            assert s1["last_heartbeat_at"] == old_hour_1 + timedelta(seconds=10 + 8 * 5 + 25)
            assert float(s1["largest_gap_seconds"]) == pytest.approx(25.0)
            assert s1["last_connectivity_status"] == "degraded"

            s2 = summaries[1]
            assert s2["runtime_id"] == "rt-3b65a843"
            assert s2["hour_bucket"] == old_hour_2
            assert s2["heartbeat_count"] == 6
            assert float(s2["largest_gap_seconds"]) == pytest.approx(15.0)
            assert s2["last_connectivity_status"] == "connected"
        finally:
            await conn.close()

    asyncio.run(_verify())


def test_retention_respects_consumer_checkpoints_and_lifecycle_projector_crosses_ingested_seq_gaps(
    pg_cluster: str,
) -> None:
    """AC 3 & AC 4: Never deletes rows ahead of consumer checkpoint; projector advances across resulting ingested_seq gaps without stalling."""
    import asyncpg  # type: ignore[import]

    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_hb_seq_gap", with_trade_journey=True))
    now = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    old_t0 = datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc)
    recent_t0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)

    write_fn = build_postgres_write_fn(db_dsn)

    def _lc_event(
        event_type: str,
        *,
        created_at: datetime,
        event_id: str,
        sequence_no: int,
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        ev = _build_hosted_event(
            event_type,
            runtime_id="rt-49c1c3d1",
            created_at=created_at,
            event_id=event_id,
        )
        ev["run_id"] = "lr-001"
        ev["signal_id"] = "sig-001"
        ev["authority_refs"] = {"persona_id": "persona-rt-49c1c3d1"}
        ev["correlation_envelope"] = envelope
        ev["metadata"].update(
            {
                "run_id": "lr-001",
                "signal_id": "sig-001",
                "persona_id": "persona-rt-49c1c3d1",
                "sequence_no": sequence_no,
                "causal_parent_id": envelope["causation_event_id"],
                "decision_id": "dec-001",
                "client_order_id": "coid-001",
                "order_id": "oid-001",
                "reconciliation_id": "rec-001",
                "correlation_envelope": envelope,
            }
        )
        return ev

    # First batch: 1 old lifecycle event + 5 old heartbeats + 1 old lifecycle event
    eid_sig = str(uuid.UUID(int=1))
    eid_dec = str(uuid.UUID(int=2))
    eid_risk = str(uuid.UUID(int=3))
    env1 = mint_trade_envelope(
        {
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "trace_id": "20000000-0000-0000-0000-000000000001",
        },
        producer="execution.signal-decision",
        event_id=eid_sig,
        journey_id="j-001",
        now=old_t0.isoformat().replace("+00:00", "Z"),
    )
    ev_sig_1 = _lc_event(
        "signal_generation",
        created_at=old_t0,
        event_id=eid_sig,
        sequence_no=1,
        envelope=env1,
    )
    old_hbs = [
        _build_hosted_event(
            "heartbeat",
            runtime_id="rt-49c1c3d1",
            created_at=old_t0 + timedelta(seconds=10 * (i + 1)),
            event_id=f"evt-hb-gap-{i}",
        )
        for i in range(5)
    ]
    t_dec = old_t0 + timedelta(minutes=2)
    env2 = propagate_envelope(
        env1,
        producer="test.lifecycle",
        event_id=eid_dec,
        event_time=t_dec.isoformat().replace("+00:00", "Z"),
    )
    ev_dec_1 = _lc_event(
        "trade_decision",
        created_at=t_dec,
        event_id=eid_dec,
        sequence_no=2,
        envelope=env2,
    )
    asyncio.run(write_fn([ev_sig_1, *old_hbs, ev_dec_1]))

    # Before the projector runs, trade_journey_projection.controller has 0 rows (checkpoint=0).
    # Retention must NOT delete any row before the consumer passes it!
    pre_projector_res = asyncio.run(
        run_heartbeat_retention(db_dsn, now=now, retention_days=3)
    )
    assert pre_projector_res.safe_ingested_seq == 0
    assert pre_projector_res.rows_deleted == 0

    # Also test that a reader blocker prevents deletion
    with pytest.raises(HeartbeatRetentionReaderBlocker):
        asyncio.run(
            run_heartbeat_retention(
                db_dsn,
                now=now,
                retention_days=3,
                readers=[
                    {
                        "reader_id": "test_blocking_reader",
                        "module": "services/test/reader.py",
                        "oldest_heartbeat_needed_seconds": 10 * 86400,
                    }
                ],
            )
        )

    # Now project only up to ingested_seq=3 (passing 2 of the 5 old heartbeats)
    store = ProjectionStore(db_dsn, schema="trade_journey_projection")
    projector = RelationalLifecycleProjector(
        store,
        deployment_sha="22b89367a56cdbb4fb8a7345fc7c4ad1d293a118",
        controller_id="canonical-lifecycle-projector",
    )
    source = PostgresLifecycleSource(db_dsn, include_non_lifecycle=True)

    async def _step_partial_projector():
        high = await source.high_watermark()
        rows = await source.fetch_after(0, limit=3)
        projector.project_records(
            rows,
            mode="live",
            source_high_watermark=high,
            source_frontier_seq=int(rows[-1]["ingested_seq"]),
        )

    asyncio.run(_step_partial_projector())
    assert projector.checkpoint == 3

    # Retention now deletes only the 2 old heartbeats with ingested_seq <= 3
    partial_ret = asyncio.run(
        run_heartbeat_retention(db_dsn, now=now, retention_days=3)
    )
    assert partial_ret.safe_ingested_seq == 3
    assert partial_ret.rows_deleted == 2

    # Advance projector across the remaining events
    async def _step_full_projector():
        high = await source.high_watermark()
        rows = await source.fetch_after(projector.checkpoint, limit=50)
        projector.project_records(
            rows,
            mode="live",
            source_high_watermark=high,
            source_frontier_seq=source.last_frontier_seq,
        )

    asyncio.run(_step_full_projector())
    assert projector.checkpoint == 7
    assert projector.controller["backlog"] == 0

    # Now append more old heartbeats + a newer lifecycle event, advance checkpoint ONLY via frontier after deleting heartbeats
    more_old_hbs = [
        _build_hosted_event(
            "heartbeat",
            runtime_id="rt-49c1c3d1",
            created_at=old_t0 + timedelta(minutes=5, seconds=10 * (i + 1)),
            event_id=f"evt-hb-gap-late-{i}",
        )
        for i in range(4)
    ]
    env3 = propagate_envelope(
        env2,
        producer="test.lifecycle",
        event_id=eid_risk,
        event_time=recent_t0.isoformat().replace("+00:00", "Z"),
    )
    ev_risk_1 = _lc_event(
        "risk_evaluation",
        created_at=recent_t0,
        event_id=eid_risk,
        sequence_no=3,
        envelope=env3,
    )
    asyncio.run(write_fn([*more_old_hbs, ev_risk_1]))

    # Delete the remaining 3 old heartbeats that are <= checkpoint 7
    ret2 = asyncio.run(run_heartbeat_retention(db_dsn, now=now, retention_days=3))
    assert ret2.rows_deleted == 3

    # Also simulate a gap ahead of the projector (e.g. after historical heartbeat cleanup) and verify
    # the projector crosses the ingested_seq hole cleanly to reach ev_risk_1 with backlog=0
    async def _create_gap_and_project():
        conn = await asyncpg.connect(db_dsn)
        try:
            await conn.execute(
                "DELETE FROM public.telemetry_events WHERE event_id LIKE 'evt-hb-gap-late-%' AND event_type = 'heartbeat'"
            )
        finally:
            await conn.close()

        high = await source.high_watermark()
        rows = await source.fetch_after(projector.checkpoint, limit=50)
        assert [r["event_id"] for r in rows] == [eid_risk]
        projector.project_records(
            rows,
            mode="live",
            source_high_watermark=high,
            source_frontier_seq=source.last_frontier_seq,
        )

    asyncio.run(_create_gap_and_project())
    ctrl = projector.controller
    assert ctrl["checkpoint"] == 12
    assert ctrl["backlog"] == 0
    assert ctrl["status"] == "ready"


def test_hosted_shape_runtime_summaries_quarter_equity_and_rankings_identical_before_after_and_cold_rebuild(
    pg_cluster: str,
    tmp_path: Path,
) -> None:
    """AC 5, AC 8, AC 9, AC 10: Hosted shape (4 paper runtimes) parity before/after retention + cold restart rebuild + baseline capture."""
    import asyncpg  # type: ignore[import]

    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_hb_hosted_parity", with_trade_journey=False))
    now = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    quarter_window = {
        "quarter": "2026-Q4",
        "start_at": "2026-10-01T00:00:00Z",
        "end_exclusive_at": "2027-01-01T00:00:00Z",
    }

    write_fn = build_postgres_write_fn(db_dsn)
    all_events: list[dict[str, Any]] = []

    # Build a dataset with the hosted shape for the 4 paper runtimes:
    # - Binding start at 2026-10-01T00:00:00Z (deploy_started, deploy_completed)
    # - 2 full UTC days of old heartbeats (2026-11-10 and 2026-11-11: 48 hours * 4 runtimes = 192 summary rows, 96 rows/day)
    # - Trading fills, lifecycle events, and synchronized quarter_equity pnl_snapshot / drawdown_snapshot observations
    # - Recent heartbeats within the 3-day retention window (2026-11-15 and 2026-11-16)
    binding_start = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    for idx, rid in enumerate(HOSTED_RUNTIME_IDS):
        all_events.append(
            _build_hosted_event(
                "deploy_started",
                runtime_id=rid,
                created_at=binding_start,
                event_id=f"deploy-start-{rid}",
            )
        )
        all_events.append(
            _build_hosted_event(
                "deploy_completed",
                runtime_id=rid,
                created_at=binding_start + timedelta(seconds=5),
                event_id=f"deploy-complete-{rid}",
            )
        )

    # 2 full days (48 UTC hours) of old heartbeats across all 4 runtimes -> 4 * 48 = 192 hourly summary rows (96/day)
    old_day_start = datetime(2026, 11, 10, 0, 0, 0, tzinfo=timezone.utc)
    for hour_offset in range(48):
        hr_time = old_day_start + timedelta(hours=hour_offset)
        for rid in HOSTED_RUNTIME_IDS:
            for sample_idx in range(2):
                all_events.append(
                    _build_hosted_event(
                        "heartbeat",
                        runtime_id=rid,
                        created_at=hr_time + timedelta(seconds=sample_idx * 30 + 5),
                        event_id=f"hb-old-{rid}-h{hour_offset}-{sample_idx}",
                    )
                )

    # Lifecycle + fill + valuation events for each runtime
    val_times = [
        datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc),
    ]
    for idx, rid in enumerate(HOSTED_RUNTIME_IDS):
        base_pnl = float((idx + 1) * 1250.0)
        fill_time = datetime(2026, 11, 11, 6, 0, 0, tzinfo=timezone.utc)
        all_events.append(
            _build_hosted_event(
                "paper_order_simulated",
                runtime_id=rid,
                created_at=fill_time - timedelta(seconds=2),
                event_id=f"ord-{rid}-1",
                metrics={"order_quantity": 10.0, "submitted_to_broker": 1},
                metadata_extra={"symbol": "2330.TW", "order_id": f"oid-{rid}-1"},
                journey_id=f"j-{rid}-1",
                loop_run_id=f"lr-{rid}-1",
                sequence_no=1,
            )
        )
        all_events.append(
            _build_hosted_event(
                "paper_fill_simulated",
                runtime_id=rid,
                created_at=fill_time,
                event_id=f"fill-{rid}-1",
                metrics={
                    "fill_quantity": 10.0,
                    "fill_price": 100.0,
                    "action": "BUY",
                    "submitted_to_broker": 1,
                },
                metadata_extra={
                    "symbol": "2330.TW",
                    "currency": "TWD",
                    "broker": "paper-sim",
                    "order_id": f"oid-{rid}-1",
                    "ledger_fill_count": 1,
                    "ledger_cash": 99000.0,
                    "trade_episode_id": f"ep-{rid}-1",
                },
                journey_id=f"j-{rid}-1",
                loop_run_id=f"lr-{rid}-1",
                sequence_no=2,
                causal_parent_id=f"ord-{rid}-1",
            )
        )
        for v_idx, v_time in enumerate(val_times):
            v_iso = v_time.isoformat().replace("+00:00", "Z")
            pnl_val = base_pnl * (0.8 if v_idx == 0 else 1.2)
            equity_val = 100000.0 + pnl_val
            mark_price = (equity_val - 99000.0) / 10.0
            all_events.append(
                _build_hosted_event(
                    "pnl_snapshot",
                    runtime_id=rid,
                    created_at=v_time,
                    event_id=f"pnl-{rid}-{v_idx}",
                    pnl_as_of=v_iso,
                    metrics={
                        "pnl": pnl_val,
                        "portfolio_value": equity_val,
                        "initial_cash": 100000.0,
                        "cash": 99000.0,
                        "pnl_period_start_at": "2026-10-01T00:00:00Z",
                        "pnl_currency": "TWD",
                        "valuation_mark_count": 1,
                        "fill_count": 1,
                        "sharpe_ratio": 1.5 + idx * 0.2,
                        "avg_slippage_bps": 2.0 + idx * 0.5,
                        "total_trades": 1,
                    },
                    metadata_extra={
                        "valuation_method": "fill_cash_ledger_mark_to_market",
                        "valuation_as_of": v_iso,
                        "mark_refs": [
                            {
                                "symbol": "2330.TW",
                                "price": mark_price,
                                "as_of": v_iso,
                                "source_ref": "twse-closing-quote",
                                "quote_currency": "TWD",
                            }
                        ],
                    },
                )
            )
            all_events.append(
                _build_hosted_event(
                    "drawdown_snapshot",
                    runtime_id=rid,
                    created_at=v_time + timedelta(seconds=1),
                    event_id=f"dd-{rid}-{v_idx}",
                    metrics={
                        "drawdown_pct": 0.02 * (idx + 1),
                        "fill_count": 1,
                    },
                )
            )

    # Recent heartbeats in the 3-day window (2026-11-16)
    recent_base = datetime(2026, 11, 16, 23, 0, 0, tzinfo=timezone.utc)
    for rid in HOSTED_RUNTIME_IDS:
        for s_idx in range(3):
            all_events.append(
                _build_hosted_event(
                    "heartbeat",
                    runtime_id=rid,
                    created_at=recent_base + timedelta(seconds=s_idx * 15),
                    event_id=f"hb-recent-{rid}-{s_idx}",
                )
            )

    all_events.sort(key=lambda e: (e["created_at"], e["event_id"]))
    write_res = asyncio.run(write_fn(all_events))
    assert write_res.success

    # 1. Live summary store populated before retention
    live_store_path = tmp_path / "live_summaries.json"
    live_store, _ = asyncio.run(
        rebuild_runtime_summaries_from_db(db_dsn, summary_store_path=str(live_store_path))
    )
    summaries_before = [
        _strip_volatile_summary_fields(s)
        for s in live_store.list(tenant_id="tenant-dev", now=now)
    ]
    quarter_before = _pm12_quarter_metrics(
        list(HOSTED_RUNTIME_IDS), summaries_before, quarter_window
    )
    os.environ["PANTHEON_BFF_TELEMETRY_SUMMARY_STORE"] = str(live_store_path)
    try:
        rankings_before = project_rankings("2026-11-17T00:00:00Z", timeout=1.0)
    finally:
        os.environ.pop("PANTHEON_BFF_TELEMETRY_SUMMARY_STORE", None)

    # 2. Run retention to delete all old heartbeats (> 3 days)
    retention_res = asyncio.run(
        run_heartbeat_retention(
            db_dsn,
            now=now,
            retention_days=3,
            batch_size=100,
            max_batches=50,
            statement_timeout_ms=15000,
        )
    )
    assert retention_res.status == "ok"
    assert retention_res.rows_deleted == 48 * 4 * 2  # 384 old heartbeat rows deleted
    assert retention_res.hours_summarized == 48 * 4  # 192 summary rows (96 per day for 2 days)

    # 3. Cold restart rebuild from telemetry_events after retention
    cold_store_path = tmp_path / "cold_summaries.json"
    cold_store, _ = asyncio.run(
        rebuild_runtime_summaries_from_db(db_dsn, summary_store_path=str(cold_store_path))
    )
    summaries_after_cold = [
        _strip_volatile_summary_fields(s)
        for s in cold_store.list(tenant_id="tenant-dev", now=now)
    ]
    quarter_after_cold = _pm12_quarter_metrics(
        list(HOSTED_RUNTIME_IDS), summaries_after_cold, quarter_window
    )
    os.environ["PANTHEON_BFF_TELEMETRY_SUMMARY_STORE"] = str(cold_store_path)
    try:
        rankings_after_cold = project_rankings("2026-11-17T00:00:00Z", timeout=1.0)
    finally:
        os.environ.pop("PANTHEON_BFF_TELEMETRY_SUMMARY_STORE", None)

    assert summaries_after_cold == summaries_before
    assert quarter_after_cold == quarter_before
    assert quarter_after_cold["pnl_return_status"] == "known"
    assert quarter_after_cold["drawdown_status"] == "known"
    assert rankings_after_cold == rankings_before

    # 4. AC 9: Previous accepted release ingest & read functions keep working after migration & retention
    post_hb = _build_hosted_event(
        "heartbeat",
        runtime_id="rt-49c1c3d1",
        created_at=now - timedelta(seconds=10),
        event_id="hb-post-retention-check",
    )
    assert asyncio.run(write_fn([post_hb])).success
    event_reader, binding_reader = build_postgres_event_reader(db_dsn)
    fetched = event_reader("hb-post-retention-check", tenant_id="tenant-dev")
    assert fetched is not None
    assert fetched["event_id"] == "hb-post-retention-check"
    binding_events = binding_reader("rb-rt-49c1c3d1", tenant_id="tenant-dev", limit=10)
    assert any(e["event_id"] == "hb-post-retention-check" for e in binding_events)

    # 5. AC 10: capture_canonical_telemetry_baseline and backup/restore verification pass after retention
    baseline = capture_telemetry_baseline(
        db_dsn,
        environment="dev",
        deployment_sha="22b89367a56cdbb4fb8a7345fc7c4ad1d293a118",
        history_disposition="partial",
        operator_note="Post-heartbeat-retention baseline verification",
    )
    assert validate_baseline_artifact(baseline)["row_count"] > 0

    backup_record = {
        "backup_id": "bk-post-retention-001",
        "backup_sha256": "sha256:" + "a" * 64,
        "expected_catalog_sha256": "sha256:" + "b" * 64,
        "restored_catalog_sha256": "sha256:" + "b" * 64,
        "restore_completed": True,
        "read_query_verified": True,
        "catalog_tables": [
            "public.telemetry_events",
            "public.telemetry_heartbeat_hourly_summaries",
        ],
        "live_row_counts": {
            "public.telemetry_events": baseline["row_count"],
            "public.telemetry_heartbeat_hourly_summaries": 192,
        },
        "restored_row_counts": {
            "public.telemetry_events": baseline["row_count"],
            "public.telemetry_heartbeat_hourly_summaries": 192,
        },
        "owner_store_backup_sha256": "sha256:" + "c" * 64,
        "owner_store_manifest_sha256": "sha256:" + "d" * 64,
        "restored_owner_store_manifest_sha256": "sha256:" + "d" * 64,
        "owner_store_dirs": list(OWNER_STORE_PATHS),
    }
    assert verify_backup_restore_readability(backup_record)["backup_restore_verified"] is True


def test_heartbeat_retention_job_lifecycle_and_main_wiring(pg_cluster: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HeartbeatRetentionJob background loop and services/telemetry/main.py wiring."""
    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_hb_job_wiring", with_trade_journey=False))

    async def _exercise_job():
        job = HeartbeatRetentionJob(
            db_dsn,
            retention_days=3,
            batch_size=100,
            max_batches_per_run=5,
            interval_seconds=1.0,
            statement_timeout_ms=5000,
            enabled=True,
        )
        await job.start()
        assert job.is_running()
        res = await job.run_once()
        assert res.status == "ok"
        stats = job.stats()
        assert stats["enabled"] is True
        assert stats["runs_completed"] >= 1
        await job.stop()
        assert not job.is_running()

    asyncio.run(_exercise_job())

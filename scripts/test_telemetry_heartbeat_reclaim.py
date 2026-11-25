"""Focused tests for scripts/telemetry_heartbeat_reclaim.py (TELEMETRY-HEARTBEAT-RETENTION-20261117)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Any
import urllib.parse
import uuid

import pytest

from scripts.telemetry_heartbeat_reclaim import (
    ALLOWED_RECLAIM_TABLES,
    CANONICAL_EVENT_RECEIPTS_TABLE,
    CANONICAL_TELEMETRY_TABLE,
    InsufficientDiskSpaceError,
    main as reclaim_main,
    run_reclaim,
    validate_reclaim_tables,
)
from services.telemetry.batch_writer import WriteResult
from services.telemetry.heartbeat_retention import HEARTBEAT_SUMMARY_TABLE
from services.telemetry.ingest_svc import (
    INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
    TelemetryIngestService,
    build_postgres_write_fn,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATE = ROOT / "scripts/db_migrate.sh"
TRADE_JOURNEY_MIGRATION = (
    ROOT / "services/trade_journey/migrations/001_create_trade_journey_projection_schema.sql"
)
RECLAIM_SCRIPT = ROOT / "scripts/telemetry_heartbeat_reclaim.py"
SCHEMA_PATH = str(ROOT / "services/telemetry/telemetry_event.schema.json")


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
    cname = f"pantheon-hb-reclaim-{uuid.uuid4().hex[:10]}"
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


async def _create_migrated_db(admin_dsn: str, db_name: str) -> str:
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
    finally:
        await conn.close()
    return db_dsn


def _make_event(
    event_type: str,
    *,
    runtime_id: str,
    created_at: datetime,
    event_id: str | None = None,
    padding_kb: int = 0,
) -> dict[str, Any]:
    iso_ts = created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    eid = event_id or f"evt-{runtime_id}-{event_type}-{uuid.uuid4().hex[:10]}"
    meta: dict[str, Any] = {
        "tenant_id": "tenant-dev",
        "runtime_package": "paper_execution_runtime",
        "connectivity_status": "connected",
        "broker_status": "ok",
    }
    if padding_kb > 0:
        meta["diagnostic_pad"] = "x" * (padding_kb * 1024)
    return {
        "event_id": eid,
        "event_type": event_type,
        "created_at": iso_ts,
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "deployment_stage": "paper",
        "execution_mode": "paper",
        "binding_id": f"rb-{runtime_id}",
        "runtime_id": runtime_id,
        "capital_pool_id": f"pool-{runtime_id}",
        "artifact_id": f"art-{runtime_id}",
        "artifact_version": "1.0.0",
        "plan_id": f"plan-{runtime_id}",
        "persona_capital_binding_id": f"pcb-{runtime_id}",
        "target": {
            "strategy_id": f"strat-{runtime_id}",
            "artifact_version": "1.0.0",
            "artifact_type": "execution_bundle",
        },
        "metrics": {"heartbeat": 1} if event_type == "heartbeat" else {"pnl": 100.0},
        "metadata": meta,
    }


def test_reclaim_dry_run_and_disk_floor_gate_and_physical_space_return(
    pg_cluster: str,
    tmp_path: Path,
) -> None:
    """AC 6: Dry-run reports rows/bytes/expected free space; apply refuses when free disk < 1.5x retained size and reclaims physical space via VACUUM FULL when sufficient."""
    import asyncpg  # type: ignore[import]

    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_reclaim_space"))
    now = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    old_base = datetime(2026, 11, 5, 0, 0, 0, tzinfo=timezone.utc)
    recent_base = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)

    write_fn = build_postgres_write_fn(db_dsn)
    seed_events: list[dict[str, Any]] = []

    # 200 padded old heartbeats (~4 KB each) across 4 hours so table occupies measurable physical pages
    for i in range(200):
        hr = i // 50
        sec = i % 50
        seed_events.append(
            _make_event(
                "heartbeat",
                runtime_id="rt-49c1c3d1",
                created_at=old_base + timedelta(hours=hr, seconds=sec * 10),
                event_id=f"hb-old-bulk-{i}",
                padding_kb=4,
            )
        )
    # 10 recent heartbeats and 5 non-heartbeat events (retained)
    for i in range(10):
        seed_events.append(
            _make_event(
                "heartbeat",
                runtime_id="rt-49c1c3d1",
                created_at=recent_base + timedelta(seconds=i * 15),
                event_id=f"hb-recent-keep-{i}",
                padding_kb=1,
            )
        )
    for i in range(5):
        seed_events.append(
            _make_event(
                "pnl_snapshot",
                runtime_id="rt-49c1c3d1",
                created_at=old_base + timedelta(hours=i),
                event_id=f"pnl-old-keep-{i}",
                padding_kb=1,
            )
        )

    assert asyncio.run(write_fn(seed_events)).success

    # 1. Dry-run reports rows, bytes to delete, and expected free space without deleting anything
    dry_report = asyncio.run(
        run_reclaim(
            db_dsn,
            apply=False,
            now=now,
            retention_days=3,
            free_disk_bytes_override=50_000_000,
        )
    )
    assert dry_report["mode"] == "dry_run"
    assert dry_report["rows_total"] == 215
    assert dry_report["heartbeat_rows_total"] == 210
    assert dry_report["rows_to_delete"] == 200
    assert dry_report["rows_to_retain"] == 15
    assert dry_report["non_heartbeat_rows_total"] == 5
    assert dry_report["hours_to_summarize"] == 4
    assert dry_report["bytes_to_delete"] > 0
    assert (
        dry_report["expected_filesystem_free_bytes_after"]
        == 50_000_000 + dry_report["bytes_to_delete"]
    )
    assert dry_report["free_disk_check_passed"] is True

    # 2. Apply mode refuses to start when free disk < 1.5 * retained_table_bytes
    insufficient_free = max(1, int(dry_report["required_free_disk_bytes"]) - 1)
    with pytest.raises(InsufficientDiskSpaceError) as exc_info:
        asyncio.run(
            run_reclaim(
                db_dsn,
                apply=True,
                now=now,
                retention_days=3,
                free_disk_bytes_override=insufficient_free,
            )
        )
    assert exc_info.value.free_disk_bytes == insufficient_free
    assert exc_info.value.required_free_disk_bytes == dry_report["required_free_disk_bytes"]

    # Also verify CLI exit code 2 on insufficient disk space and that 0 rows were deleted
    cli_rc = reclaim_main(
        [
            "--apply",
            "--dsn",
            db_dsn,
            "--now",
            "2026-11-17T00:00:00Z",
            "--free-disk-bytes",
            str(insufficient_free),
        ]
    )
    assert cli_rc == 2

    async def _count_rows():
        conn = await asyncpg.connect(db_dsn)
        try:
            return int(await conn.fetchval("SELECT COUNT(*) FROM public.telemetry_events"))
        finally:
            await conn.close()

    assert asyncio.run(_count_rows()) == 215

    # 3. Apply mode with sufficient disk deletes in batches, populates summaries, and shrinks physical table size
    out_json = tmp_path / "reclaim_apply.json"
    cli_apply_rc = reclaim_main(
        [
            "--apply",
            "--dsn",
            db_dsn,
            "--now",
            "2026-11-17T00:00:00Z",
            "--batch-size",
            "60",
            "--free-disk-bytes",
            "50000000",
            "--output",
            str(out_json),
        ]
    )
    assert cli_apply_rc == 0
    apply_report = json.loads(out_json.read_text(encoding="utf-8"))
    assert apply_report["mode"] == "apply"
    assert apply_report["rows_deleted_actual"] == 200
    assert apply_report["rows_after"] == 15
    assert apply_report["heartbeat_rows_after"] == 10
    assert apply_report["non_heartbeat_rows_after"] == 5
    assert apply_report["summary_rows_after"] == 4
    assert apply_report["table_bytes_after"] < apply_report["table_bytes_before"]
    assert apply_report["actual_bytes_reclaimed"] > 0
    assert apply_report["reclaim"]["method"] == "vacuum_full"
    assert apply_report["reclaim"]["lock_window_ms"] >= 0.0


def test_ingest_stays_available_during_reclaim_and_dlq_retries_with_zero_loss(
    pg_cluster: str,
    tmp_path: Path,
) -> None:
    """AC 7: Telemetry ingest stays available during reclaim; events hitting lock contention are retried via DeadLetterQueue with zero loss."""
    import asyncpg  # type: ignore[import]

    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_reclaim_ingest_dlq"))
    now = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    old_base = datetime(2026, 11, 8, 0, 0, 0, tzinfo=timezone.utc)

    base_write_fn = build_postgres_write_fn(db_dsn)
    old_events = [
        _make_event(
            "heartbeat",
            runtime_id="rt-49c1c3d1",
            created_at=old_base + timedelta(seconds=i * 15),
            event_id=f"hb-reclaim-old-{i}",
        )
        for i in range(30)
    ]
    assert asyncio.run(base_write_fn(old_events)).success

    async def _run_concurrent_ingest_and_reclaim():
        lock_window_active = False

        async def gated_write_fn(batch: list[dict[str, Any]]) -> WriteResult:
            if lock_window_active:
                return WriteResult.fail(
                    "canceling statement due to lock timeout during VACUUM FULL",
                    retryable=True,
                )
            return await base_write_fn(batch)

        svc = TelemetryIngestService(
            schema_path=SCHEMA_PATH,
            storage_dir=str(tmp_path / "telemetry_spill"),
            buffer_backend="memory",
            batch_size=10,
            batch_interval=0.05,
            max_retries=1,
            write_fn=gated_write_fn,
        )
        # Speed up writer retry delay for fast bounded test execution
        svc._writer._base_retry_delay = 0.02
        svc._writer._max_retry_delay = 0.05
        await svc.start()

        try:
            # 1. Ingest 5 events before reclaim
            pre_events = [
                _make_event(
                    "pnl_snapshot",
                    runtime_id="rt-49c1c3d1",
                    created_at=now - timedelta(minutes=10, seconds=i),
                    event_id=f"evt-pre-window-{i}",
                )
                for i in range(5)
            ]
            for ev in pre_events:
                assert await svc.ingest(ev) is True
            await asyncio.sleep(0.2)

            # 2. During the reclaim lock window, post 8 events while writer sees lock contention -> routed to DLQ
            window_events = [
                _make_event(
                    "pnl_snapshot" if i % 2 == 0 else "heartbeat",
                    runtime_id="rt-49c1c3d1",
                    created_at=now - timedelta(minutes=5, seconds=i),
                    event_id=f"evt-during-window-{i}",
                )
                for i in range(8)
            ]

            async def _during_lock_window():
                nonlocal lock_window_active
                lock_window_active = True
                for ev in window_events:
                    # HTTP / service ingest remains available (returns True immediately)
                    accepted = await svc.ingest(ev)
                    assert accepted is True
                # Allow batch writer to attempt write during lock window and spill to DLQ
                await asyncio.sleep(0.25)
                lock_window_active = False

            async def _replay_dlq_after_lock():
                replayed = await svc.replay_dlq()
                await asyncio.sleep(0.25)
                return replayed

            report = await run_reclaim(
                db_dsn,
                apply=True,
                now=now,
                retention_days=3,
                batch_size=15,
                free_disk_bytes_override=100_000_000,
                on_lock_acquired=_during_lock_window,
                replay_dlq_callback=_replay_dlq_after_lock,
            )

            # 3. Ingest 4 events after reclaim
            post_events = [
                _make_event(
                    "heartbeat",
                    runtime_id="rt-49c1c3d1",
                    created_at=now - timedelta(seconds=30 - i),
                    event_id=f"evt-post-window-{i}",
                )
                for i in range(4)
            ]
            for ev in post_events:
                assert await svc.ingest(ev) is True
            await asyncio.sleep(0.25)
        finally:
            await svc.stop(graceful=True)

        conn = await asyncpg.connect(db_dsn)
        try:
            committed_rows = await conn.fetch(
                "SELECT event_id, event_type FROM public.telemetry_events ORDER BY ingested_seq ASC"
            )
            summary_count = int(
                await conn.fetchval(f"SELECT SUM(heartbeat_count) FROM {HEARTBEAT_SUMMARY_TABLE}")
                or 0
            )
        finally:
            await conn.close()

        committed_ids = {r["event_id"] for r in committed_rows}
        expected_ids = {
            *[e["event_id"] for e in pre_events],
            *[e["event_id"] for e in window_events],
            *[e["event_id"] for e in post_events],
        }
        assert committed_ids == expected_ids
        assert len(committed_rows) == 5 + 8 + 4  # 17 retained events, 0 lost!
        assert summary_count == 30  # All 30 old heartbeats summarized before deletion
        assert report["dlq_replayed_count"] == 8
        assert report["reclaim"]["lock_window_ms"] > 0

    asyncio.run(_run_concurrent_ingest_and_reclaim())


def test_reclaim_table_allow_list_validation_and_event_receipts_reclaim(
    pg_cluster: str,
    tmp_path: Path,
) -> None:
    """AC 4: Reclaim script enforces explicit table allow-list (public.telemetry_events and trade_journey_projection.event_receipts) with dry-run, 1.5x free-disk guard, and lock-window measurement for each."""
    import asyncpg  # type: ignore[import]

    assert ALLOWED_RECLAIM_TABLES == (
        "public.telemetry_events",
        "trade_journey_projection.event_receipts",
    )
    assert validate_reclaim_tables(["public.telemetry_events"]) == ("public.telemetry_events",)
    assert validate_reclaim_tables(
        "public.telemetry_events,trade_journey_projection.event_receipts"
    ) == (
        "public.telemetry_events",
        "trade_journey_projection.event_receipts",
    )

    with pytest.raises(ValueError, match="not in the reclaim allow-list"):
        validate_reclaim_tables(["public.other_table"])
    with pytest.raises(ValueError, match="not in the reclaim allow-list"):
        validate_reclaim_tables(["trade_journey_projection.journeys"])

    bad_cli_rc = reclaim_main(
        [
            "--dry-run",
            "--tables",
            "public.other_table",
        ]
    )
    assert bad_cli_rc == 1

    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_reclaim_receipts_allowlist"))
    now = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    old_hb_base = datetime(2026, 11, 5, 0, 0, 0, tzinfo=timezone.utc)
    old_infra_base = datetime(2026, 11, 6, 0, 0, 0, tzinfo=timezone.utc)
    recent_base = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)

    async def _seed_both_tables() -> None:
        conn = await asyncpg.connect(db_dsn)
        try:
            tj_sql = TRADE_JOURNEY_MIGRATION.read_text(encoding="utf-8")
            await conn.execute(tj_sql)
            await conn.execute("CREATE SCHEMA IF NOT EXISTS incident")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incident.incident_cases (
                    record_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                "INSERT INTO incident.incident_cases (record_id, payload) VALUES ($1, $2::jsonb)",
                "inc-reclaim-ref-1",
                json.dumps(
                    {
                        "incident_id": "inc-reclaim-ref-1",
                        "trace_id": "evt-infra-old-protected",
                        "telemetry_event_ids": ["evt-infra-old-protected"],
                    }
                ),
            )

            # Seed 220 padded rows in trade_journey_projection.event_receipts, then delete 200 of them
            # so physical bloat exists waiting for VACUUM FULL reclaim
            pad_fp = "sha256:" + ("f" * 2048)
            for seq in range(1, 221):
                disp = "ignored" if seq <= 200 else "applied"
                jid = "" if disp == "ignored" else f"j-{seq}"
                await conn.execute(
                    """
                    INSERT INTO trade_journey_projection.event_receipts (
                        event_id, ingested_seq, fingerprint, tenant_id, environment,
                        journey_id, loop_run_id, source_event_type, created_at, disposition
                    ) VALUES ($1, $2, $3, 'tenant-dev', 'paper', $4, '', 'heartbeat', $5, $6)
                    """,
                    f"rcpt-{seq}",
                    seq,
                    pad_fp,
                    jid,
                    old_hb_base + timedelta(seconds=seq),
                    disp,
                )
            await conn.execute(
                "DELETE FROM trade_journey_projection.event_receipts WHERE disposition = 'ignored'"
            )
            # Initialize controller checkpoint high enough so retention can delete all old telemetry_events
            await conn.execute(
                """
                INSERT INTO trade_journey_projection.controller (
                    controller_id, tenant_scope, environment_scope, checkpoint_seq,
                    source_high_watermark, backlog_count, mode, status, accepted_live
                ) VALUES ('canonical-lifecycle-projector', '*', '*', 100000, 100000, 0, 'live', 'ok', true)
                """
            )
        finally:
            await conn.close()

    asyncio.run(_seed_both_tables())

    write_fn = build_postgres_write_fn(db_dsn)
    telemetry_seed: list[dict[str, Any]] = []
    # 80 old heartbeats (>3d)
    for i in range(80):
        telemetry_seed.append(
            _make_event(
                "heartbeat",
                runtime_id="rt-49c1c3d1",
                created_at=old_hb_base + timedelta(seconds=i * 15),
                event_id=f"hb-allowlist-old-{i}",
                padding_kb=2,
            )
        )
    # 60 old unreferenced infrastructure_health events (>7d) + 1 protected old infrastructure_health event
    for i in range(60):
        iso_ts = (old_infra_base + timedelta(seconds=i * 30)).isoformat().replace("+00:00", "Z")
        telemetry_seed.append(
            {
                "schema_version": INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
                "event_id": f"evt-infra-old-del-{i}",
                "event_type": "infrastructure_health",
                "created_at": iso_ts,
                "tenant_id": "tenant-dev",
                "producer": "control-plane-bff",
                "component": {
                    "service_name": "runtime-manager",
                    "component_kind": "http_service",
                    "endpoint": "http://runtime-manager:8081/__health__",
                },
                "health_status": "ok",
                "severity": "info",
                "observation": {
                    "probe_kind": "interval_probe",
                    "observed_at": iso_ts,
                    "window_seconds": 60,
                    "sample_count": 4,
                    "failure_count": 0,
                    "consecutive_failures": 0,
                    "error_rate": 0.0,
                    "latency_ms": 10.0,
                    "status_code": 200,
                    "detail": "p" * 900,
                },
            }
        )
    prot_ts = (old_infra_base + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    telemetry_seed.append(
        {
            "schema_version": INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
            "event_id": "evt-infra-old-protected",
            "event_type": "infrastructure_health",
            "created_at": prot_ts,
            "tenant_id": "tenant-dev",
            "producer": "control-plane-bff",
            "component": {
                "service_name": "runtime-manager",
                "component_kind": "http_service",
                "endpoint": "http://runtime-manager:8081/__health__",
            },
            "health_status": "degraded",
            "severity": "critical",
            "observation": {
                "probe_kind": "interval_probe",
                "observed_at": prot_ts,
                "window_seconds": 60,
                "sample_count": 4,
                "failure_count": 4,
                "consecutive_failures": 4,
                "error_rate": 1.0,
                "latency_ms": 500.0,
                "status_code": 503,
            },
        }
    )
    # 5 recent infrastructure_health events (<7d) and 5 recent heartbeats (<3d)
    for i in range(5):
        r_ts = (recent_base + timedelta(seconds=i * 15)).isoformat().replace("+00:00", "Z")
        telemetry_seed.append(
            {
                "schema_version": INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
                "event_id": f"evt-infra-recent-keep-{i}",
                "event_type": "infrastructure_health",
                "created_at": r_ts,
                "tenant_id": "tenant-dev",
                "producer": "control-plane-bff",
                "component": {
                    "service_name": "runtime-manager",
                    "component_kind": "http_service",
                    "endpoint": "http://runtime-manager:8081/__health__",
                },
                "health_status": "ok",
                "severity": "info",
                "observation": {
                    "probe_kind": "interval_probe",
                    "observed_at": r_ts,
                    "window_seconds": 60,
                    "sample_count": 4,
                    "failure_count": 0,
                    "consecutive_failures": 0,
                    "error_rate": 0.0,
                    "latency_ms": 8.0,
                    "status_code": 200,
                },
            }
        )
        telemetry_seed.append(
            _make_event(
                "heartbeat",
                runtime_id="rt-49c1c3d1",
                created_at=recent_base + timedelta(seconds=i * 15),
                event_id=f"hb-allowlist-recent-{i}",
            )
        )

    assert asyncio.run(write_fn(telemetry_seed)).success

    # 1. Dry-run across both allow-listed tables
    dry_both = asyncio.run(
        run_reclaim(
            db_dsn,
            tables=[CANONICAL_TELEMETRY_TABLE, CANONICAL_EVENT_RECEIPTS_TABLE],
            apply=False,
            now=now,
            retention_days=3,
            infra_health_retention_days=7,
            free_disk_bytes_override=50_000_000,
        )
    )
    assert dry_both["mode"] == "dry_run"
    assert dry_both["tables"] == [
        CANONICAL_TELEMETRY_TABLE,
        CANONICAL_EVENT_RECEIPTS_TABLE,
    ]
    assert set(dry_both["table_plans"].keys()) == {
        CANONICAL_TELEMETRY_TABLE,
        CANONICAL_EVENT_RECEIPTS_TABLE,
    }
    te_plan = dry_both["table_plans"][CANONICAL_TELEMETRY_TABLE]
    er_plan = dry_both["table_plans"][CANONICAL_EVENT_RECEIPTS_TABLE]
    assert te_plan["heartbeat_rows_to_delete"] == 80
    assert te_plan["infra_health_rows_to_delete"] == 60
    assert te_plan["rows_to_delete"] == 140
    assert "evt-infra-old-protected" in te_plan["protected_infra_health_event_ids"]
    assert er_plan["rows_total"] == 20
    assert er_plan["table_bytes_before"] > 0
    assert er_plan["required_free_disk_bytes"] > 0
    assert er_plan["free_disk_check_passed"] is True

    # 2. Free-disk guard on trade_journey_projection.event_receipts alone refuses when disk < 1.5x retained
    er_insufficient = max(1, int(er_plan["required_free_disk_bytes"]) - 1)
    with pytest.raises(InsufficientDiskSpaceError) as exc_info:
        asyncio.run(
            run_reclaim(
                db_dsn,
                tables=[CANONICAL_EVENT_RECEIPTS_TABLE],
                apply=True,
                now=now,
                free_disk_bytes_override=er_insufficient,
            )
        )
    assert exc_info.value.table == CANONICAL_EVENT_RECEIPTS_TABLE

    # 3. Apply across both allow-listed tables via CLI
    out_both = tmp_path / "reclaim_both_apply.json"
    rc_both = reclaim_main(
        [
            "--apply",
            "--dsn",
            db_dsn,
            "--tables",
            CANONICAL_TELEMETRY_TABLE,
            CANONICAL_EVENT_RECEIPTS_TABLE,
            "--now",
            "2026-11-17T00:00:00Z",
            "--retention-days",
            "3",
            "--infra-health-retention-days",
            "7",
            "--batch-size",
            "50",
            "--free-disk-bytes",
            "50000000",
            "--output",
            str(out_both),
        ]
    )
    assert rc_both == 0
    apply_both = json.loads(out_both.read_text(encoding="utf-8"))
    assert apply_both["mode"] == "apply"
    assert apply_both["tables"] == [
        CANONICAL_TELEMETRY_TABLE,
        CANONICAL_EVENT_RECEIPTS_TABLE,
    ]
    te_res = apply_both["table_results"][CANONICAL_TELEMETRY_TABLE]
    er_res = apply_both["table_results"][CANONICAL_EVENT_RECEIPTS_TABLE]

    assert te_res["heartbeat_rows_deleted_actual"] == 80
    assert te_res["infra_health_rows_deleted_actual"] == 60
    assert te_res["rows_deleted_actual"] == 140
    assert te_res["rows_after"] == 11  # 5 recent hb + 5 recent infra + 1 protected old infra
    assert te_res["infra_health_rows_after"] == 6
    assert te_res["table_bytes_after"] < te_res["table_bytes_before"]
    assert te_res["reclaim"]["lock_window_ms"] >= 0.0
    assert te_res["reclaim"]["mechanism_sql"] == "VACUUM (FULL, ANALYZE) public.telemetry_events"

    assert er_res["rows_after"] == 20
    assert er_res["table_bytes_after"] < er_res["table_bytes_before"]
    assert er_res["actual_bytes_reclaimed"] > 0
    assert er_res["reclaim"]["lock_window_ms"] >= 0.0
    assert (
        er_res["reclaim"]["mechanism_sql"]
        == "VACUUM (FULL, ANALYZE) trade_journey_projection.event_receipts"
    )


def test_reclaim_reports_and_applies_14d_telemetry_events_retention(
    pg_cluster: str,
    tmp_path: Path,
) -> None:
    """TELEMETRY-EVENTS-RETENTION-14D-20261125 AC 3: scripts/telemetry_heartbeat_reclaim.py reports and applies the 14-day retention window consistently."""
    import asyncpg  # type: ignore[import]

    db_dsn = asyncio.run(_create_migrated_db(pg_cluster, "test_reclaim_14d_telemetry_events"))
    now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
    old_20d = datetime(2026, 11, 5, 0, 0, 0, tzinfo=timezone.utc)
    recent_5d = datetime(2026, 11, 20, 0, 0, 0, tzinfo=timezone.utc)

    write_fn = build_postgres_write_fn(db_dsn)
    seed_events: list[dict[str, Any]] = [
        _make_event(
            "deploy_started",
            runtime_id="rt-49c1c3d1",
            created_at=old_20d,
            event_id="evt-reclaim-14d-deploy-keep",
        ),
        _make_event(
            "pnl_snapshot",
            runtime_id="rt-49c1c3d1",
            created_at=old_20d + timedelta(minutes=1),
            event_id="evt-reclaim-14d-pnl-anchor-keep",
        ),
        _make_event(
            "signal_generation",
            runtime_id="rt-49c1c3d1",
            created_at=recent_5d,
            event_id="evt-reclaim-14d-sig-5d-keep",
        ),
    ]
    for i in range(40):
        seed_events.append(
            _make_event(
                "signal_generation",
                runtime_id="rt-49c1c3d1",
                created_at=old_20d + timedelta(minutes=10 + i),
                event_id=f"evt-reclaim-14d-sig-old-del-{i}",
                padding_kb=2,
            )
        )
    assert asyncio.run(write_fn(seed_events)).success

    dry_plan = asyncio.run(
        run_reclaim(
            db_dsn,
            tables=[CANONICAL_TELEMETRY_TABLE],
            apply=False,
            now=now,
            retention_days=3,
            infra_health_retention_days=7,
            telemetry_events_retention_days=14,
            free_disk_bytes_override=50_000_000,
        )
    )
    assert dry_plan["mode"] == "dry_run"
    assert dry_plan["telemetry_events_retention_days"] == 14.0
    assert dry_plan["telemetry_events_rows_to_delete"] == 40
    assert dry_plan["rows_to_delete"] == 40
    assert dry_plan["rows_to_retain"] == 3

    out_apply = tmp_path / "reclaim_14d_apply.json"
    rc = reclaim_main(
        [
            "--apply",
            "--dsn",
            db_dsn,
            "--tables",
            CANONICAL_TELEMETRY_TABLE,
            "--now",
            "2026-11-25T00:00:00Z",
            "--retention-days",
            "3",
            "--infra-health-retention-days",
            "7",
            "--telemetry-events-retention-days",
            "14",
            "--batch-size",
            "15",
            "--free-disk-bytes",
            "50000000",
            "--output",
            str(out_apply),
        ]
    )
    assert rc == 0
    apply_report = json.loads(out_apply.read_text(encoding="utf-8"))
    assert apply_report["mode"] == "apply"
    assert apply_report["telemetry_events_rows_deleted_actual"] == 40
    assert apply_report["rows_deleted_actual"] == 40
    assert apply_report["rows_after"] == 3
    assert apply_report["table_bytes_after"] < apply_report["table_bytes_before"]

    async def _fetch_surviving() -> set[str]:
        conn = await asyncpg.connect(db_dsn)
        try:
            rows = await conn.fetch("SELECT event_id FROM public.telemetry_events")
            return {str(r["event_id"]) for r in rows}
        finally:
            await conn.close()

    assert asyncio.run(_fetch_surviving()) == {
        "evt-reclaim-14d-deploy-keep",
        "evt-reclaim-14d-pnl-anchor-keep",
        "evt-reclaim-14d-sig-5d-keep",
    }

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import sys
import types
import uuid

import pytest

from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    propagate_envelope,
)
from services.trade_journey import lifecycle_projector as lifecycle_projector_module
from services.trade_journey.lifecycle_projector import (
    ConflictingLifecycleEvent,
    LIFECYCLE_EVENT_TYPES,
    LIFECYCLE_EVENT_TYPE_QUERY,
    LifecycleProjector,
    PostgresLifecycleSource,
    RelationalLifecycleProjector,
    _fingerprint,
    _record_worker_failure,
)
from services.trade_journey.materializer import JourneyMaterializer, SHARED_IDENTIFIER_TYPES
from services.trade_journey.projection_store import ControllerStateRow, ProjectionStore


IDENTITY = {
    "tenant_id": "tenant-a",
    "environment": "paper",
    "run_id": "run-paper-001",
    "signal_id": "signal-paper-001",
    "strategy_id": "strategy-paper-001",
    "runtime_id": "runtime-paper-001",
    "binding_id": "10000000-0000-0000-0000-000000000001",
    "capital_pool_id": "pool-paper-001",
    "persona_id": "persona-paper-001",
    "persona_capital_binding_id": "pcb-paper-001",
    "artifact_id": "artifact-paper-001",
    "artifact_version": "1.2.3",
    "plan_id": "plan-paper-001",
    "trace_id": "20000000-0000-0000-0000-000000000001",
}

NOW = "2026-07-15T00:00:00Z"


def _uuid(number: int) -> str:
    return str(uuid.UUID(int=number))


def lifecycle_rows() -> list[dict]:
    specs = [
        ("signal_generation", 1),
        ("trade_decision", 2),
        ("risk_evaluation", 3),
        ("order_submitted", 4),
        ("order_accepted", 5),
        ("paper_fill_simulated", 6),
        ("position_snapshot", 7),
        ("reconciliation_completed", 8),
    ]
    rows: list[dict] = []
    envelope = None
    for offset, (event_type, sequence_no) in enumerate(specs, start=1):
        event_id = _uuid(offset)
        created_at = f"2026-07-15T00:00:{sequence_no:02d}Z"
        if envelope is None:
            envelope = mint_trade_envelope(
                {
                    "tenant_id": IDENTITY["tenant_id"],
                    "environment": IDENTITY["environment"],
                    "trace_id": IDENTITY["trace_id"],
                },
                producer="execution.signal-decision",
                event_id=event_id,
                journey_id="tj-paper-001",
                now=created_at,
            )
        else:
            envelope = propagate_envelope(
                envelope,
                producer="test.lifecycle",
                event_id=event_id,
                event_time=created_at,
            )
        metadata = {
            "run_id": IDENTITY["run_id"],
            "signal_id": IDENTITY["signal_id"],
            "persona_id": IDENTITY["persona_id"],
            "sequence_no": sequence_no,
            "causal_parent_id": envelope["causation_event_id"],
            "decision_id": "decision-paper-001",
            "client_order_id": "client-order-paper-001",
            "order_id": "order-paper-001",
            "reconciliation_id": "reconciliation-paper-001",
        }
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "execution_mode": "paper",
            "environment": IDENTITY["environment"],
            "deployment_stage": IDENTITY["environment"],
            "binding_id": IDENTITY["binding_id"],
            "runtime_id": IDENTITY["runtime_id"],
            "capital_pool_id": IDENTITY["capital_pool_id"],
            "artifact_id": IDENTITY["artifact_id"],
            "artifact_version": IDENTITY["artifact_version"],
            "plan_id": IDENTITY["plan_id"],
            "persona_capital_binding_id": IDENTITY["persona_capital_binding_id"],
            "run_id": IDENTITY["run_id"],
            "signal_id": IDENTITY["signal_id"],
            "trace_id": IDENTITY["trace_id"],
            "authority_refs": {"persona_id": IDENTITY["persona_id"]},
            "target": {"strategy_id": IDENTITY["strategy_id"]},
            "metrics": {"action": event_type},
            "metadata": metadata,
            "correlation_envelope": envelope,
        }
        if event_type == "paper_fill_simulated":
            payload["metrics"] = {"fill_quantity": 3, "fill_price": 101.5}
        if event_type == "position_snapshot":
            payload["position_qty"] = 3
        rows.append(
            {
                "ingested_seq": offset,
                "ingested_at": f"2026-07-15T00:01:{offset:02d}Z",
                "event_id": event_id,
                "event_type": event_type,
                "created_at": created_at,
                "payload": payload,
            }
        )
    return rows


class _RecordingRelationalStore:
    """In-memory transaction recorder for relational worker unit coverage."""

    def __init__(self, *, fail_transactions: bool = False) -> None:
        self.fail_transactions = fail_transactions
        self.controller: ControllerStateRow | None = None
        self.receipts: dict[str, object] = {}
        self.stage_events: dict[tuple[str, str, str], list[dict]] = {}
        self.mutations: list[object] = []
        self.identity_links: dict[tuple[str, str, str, str], str] = {}
        self.receipt_batch_calls: list[tuple[str, ...]] = []
        self.hydration_batch_calls: list[tuple[tuple[str, str, str], ...]] = []

    def get_controller_state(self, *_args):
        return self.controller

    def get_receipt(self, event_id: str):
        return self.receipts.get(event_id)

    def resolve_identity(self, tenant_id, environment, identifier_type, identifier_value):
        return self.identity_links.get(
            (tenant_id, environment, identifier_type, identifier_value)
        )

    def get_receipts(self, event_ids):
        event_ids = tuple(event_ids)
        self.receipt_batch_calls.append(event_ids)
        return {
            event_id: self.receipts[event_id]
            for event_id in event_ids
            if event_id in self.receipts
        }

    def load_journey_stage_events(self, tenant_id: str, environment: str, journey_id: str):
        key = (tenant_id, environment, journey_id)
        return [dict(event) for event in self.stage_events.get(key, [])]

    def load_journey_stage_events_bulk(self, keys):
        keys = tuple(keys)
        self.hydration_batch_calls.append(keys)
        return {
            key: [dict(event) for event in self.stage_events.get(key, [])]
            for key in keys
        }

    def execute_batch_transaction(self, controller_id, tenant_scope, environment_scope, mutation):
        self.mutations.append(mutation)
        if self.fail_transactions:
            raise OSError("injected relational transaction failure")
        staged_links = dict(self.identity_links)
        for link in mutation.identity_links:
            if link.identifier_type in SHARED_IDENTIFIER_TYPES:
                continue
            bound = staged_links.setdefault(
                (link.tenant_id, link.environment, link.identifier_type, link.identifier_value),
                link.journey_id,
            )
            if bound != link.journey_id:
                raise ValueError(f"identity link already bound to another journey: {link.identifier_type}={link.identifier_value}")
        self.identity_links = staged_links
        for receipt in mutation.receipts:
            self.receipts[receipt.event_id] = receipt
        for stage in mutation.stages:
            key = (stage.tenant_id, stage.environment, stage.journey_id)
            events = self.stage_events.setdefault(key, [])
            if not any(event.get("event_id") == stage.contract_fields.get("event_id") for event in events):
                events.append(dict(stage.contract_fields))
        prior = self.controller
        checkpoint = 0 if prior is None else prior.checkpoint_seq
        while any(receipt.ingested_seq == checkpoint + 1 for receipt in self.receipts.values()):
            checkpoint += 1
        proven_frontier = int(getattr(mutation, "source_frontier_seq", 0) or 0)
        if proven_frontier >= (0 if prior is None else prior.checkpoint_seq):
            checkpoint = max(checkpoint, proven_frontier)
        revision = (0 if prior is None else prior.projection_revision) + bool(mutation.receipts)
        source_high = max(
            0 if prior is None else prior.source_high_watermark,
            mutation.source_high_watermark,
            checkpoint,
        )
        self.controller = ControllerStateRow(
            controller_id=controller_id,
            tenant_scope=tenant_scope,
            environment_scope=environment_scope,
            checkpoint_seq=checkpoint,
            source_high_watermark=source_high,
            backlog_count=max(0, source_high - checkpoint),
            projection_revision=revision,
            deployment_sha=mutation.deployment_sha,
            mode=mutation.mode,
            status=mutation.status,
            accepted_live=mutation.accepted_live,
            last_error_message=getattr(mutation, "error_message", "") or "",
            unresolved_quarantine_count=len(mutation.quarantines),
        )
        return self.controller


def test_relational_projector_writes_one_bounded_transaction_without_json_state(tmp_path):
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="relational-test", clock=lambda: NOW
    )
    rows = lifecycle_rows()

    first = projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    assert first.checkpoint == 2
    assert first.accepted == 2
    assert len(store.mutations) == 1
    mutation = store.mutations[-1]
    assert len(mutation.receipts) == 2
    assert len(mutation.journeys) == 1
    assert len(mutation.stages) == 2
    assert len(mutation.loop_runs) == 1
    assert not hasattr(projector, "state")
    assert not (tmp_path / "controller_state.json").exists()

    # Restart hydrates only this batch's one aggregate from relational stage
    # contract fields; it never restores a controller-wide JSON snapshot.
    restarted = RelationalLifecycleProjector(store, deployment_sha="relational-test")
    second = restarted.project_records(rows[2:3], mode="live", source_high_watermark=3)
    assert second.checkpoint == 3
    assert second.accepted == 1
    aggregate_key = (IDENTITY["tenant_id"], IDENTITY["environment"], "tj-paper-001")
    assert store.receipt_batch_calls == [
        tuple(row["event_id"] for row in rows[:2]),
        (rows[2]["event_id"],),
    ]
    assert store.hydration_batch_calls == [
        (aggregate_key,),
        (aggregate_key,),
    ]
    assert restarted._materializer.stats == {
        "entries_derived": 1,
        "aggregates_rematerialized": 1,
        "aggregates_snapshotted": 1,
    }

    duplicate = restarted.project_records(rows[:1], mode="live", source_high_watermark=3)
    assert duplicate.accepted == 0
    assert duplicate.duplicates == 1
    assert store.receipt_batch_calls[-1] == (rows[0]["event_id"],)
    assert not store.mutations[-1].receipts
    assert not store.mutations[-1].journeys
    assert not store.mutations[-1].stages


def test_relational_projector_prefetches_and_hydrates_many_aggregates_once():
    """A batch may touch many journeys without multiplying DB round trips."""

    from services.trade_journey.lifecycle_projector_capacity import (
        _journey_event_types,
        journey_rows,
    )

    first = journey_rows(1, event_types=_journey_event_types(2), starting_seq=1)
    second = journey_rows(2, event_types=_journey_event_types(2), starting_seq=3)
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, deployment_sha="relational-test")

    result = projector.project_records(first + second, mode="live", source_high_watermark=4)

    assert result.accepted == 4
    assert len(store.receipt_batch_calls) == 1
    assert len(store.receipt_batch_calls[0]) == 4
    assert len(store.hydration_batch_calls) == 1
    assert len(store.hydration_batch_calls[0]) == 2


def test_relational_projector_commits_ignored_and_quarantined_receipts_atomically():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    ignored = {
        "ingested_seq": 1,
        "ingested_at": NOW,
        "event_id": "ignored-source-event",
        "event_type": "unrelated_telemetry",
        "created_at": NOW,
        "payload": {
            "event_id": "ignored-source-event",
            "event_type": "unrelated_telemetry",
            "created_at": NOW,
        },
    }
    invalid = {
        "ingested_seq": 2,
        "ingested_at": NOW,
        "event_id": "invalid-lifecycle-event",
        "event_type": "signal_generation",
        "created_at": NOW,
        "payload": {
            "event_id": "invalid-lifecycle-event",
            "event_type": "signal_generation",
            "created_at": NOW,
        },
    }
    result = projector.project_records([ignored, invalid], mode="recovery", source_high_watermark=2)
    mutation = store.mutations[-1]
    assert result.checkpoint == 2
    assert result.ignored == 1
    assert result.quarantined == 1
    assert {receipt.disposition for receipt in mutation.receipts} == {"ignored", "quarantined"}
    assert len(mutation.quarantines) == 1


def test_relational_projector_does_not_advance_memory_after_transaction_failure():
    store = _RecordingRelationalStore(fail_transactions=True)
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    with pytest.raises(OSError, match="injected relational transaction failure"):
        projector.project_records(lifecycle_rows()[:1], mode="live", source_high_watermark=1)
    assert projector.checkpoint == 0
    assert projector._materializer.aggregates == {}


def test_relational_projector_has_no_legacy_snapshot_serialization_path(monkeypatch):
    source = inspect.getsource(RelationalLifecycleProjector)
    assert "serialize_aggregates" not in source
    assert "render_full_payloads" not in source
    assert "AtomicProjectionBundle" not in source
    assert "state_path" not in source

    monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", "active")
    with pytest.raises(RuntimeError, match="must be 'shadow', 'postgres', or 'relational'"):
        lifecycle_projector_module._configured_relational_projector()


@pytest.fixture(scope="module")
def _ephemeral_pg_cluster() -> str:
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return
    import shutil
    import socket
    import subprocess
    import time

    if shutil.which("docker") is None:
        pytest.skip("TEST_DATABASE_URL is not set and Docker is not available")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("TEST_DATABASE_URL is not set and Docker daemon is not reachable")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    cname = f"pantheon-lc-proj-{uuid.uuid4().hex[:10]}"
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
    import psycopg  # type: ignore[import]

    deadline = time.monotonic() + 15.0
    connected = False
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=1):
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


@pytest.fixture
def relational_postgres_dsn(_ephemeral_pg_cluster: str) -> str:
    return os.getenv("TEST_DATABASE_URL") or _ephemeral_pg_cluster


def test_relational_projector_postgres_restart_duplicate_and_contiguous_receipts(
    relational_postgres_dsn: str, tmp_path
):
    """Exercise the active worker composition against a real transaction store."""

    schema = f"test_relational_worker_{uuid.uuid4().hex[:8]}"
    ctrl_id = f"test_ctrl_{uuid.uuid4().hex[:8]}"
    store = ProjectionStore(relational_postgres_dsn, schema=schema, bootstrap=True)
    try:
        rows = lifecycle_rows()
        first = RelationalLifecycleProjector(
            store, deployment_sha="relational-pg", controller_id=ctrl_id
        )
        result = first.project_records(rows[:2], mode="live", source_high_watermark=2)
        assert result.checkpoint == 2
        assert result.accepted == 2
        assert not (tmp_path / "controller_state.json").exists()

        restarted = RelationalLifecycleProjector(
            store, deployment_sha="relational-pg", controller_id=ctrl_id
        )
        result = restarted.project_records(rows[2:3], mode="live", source_high_watermark=3)
        assert result.checkpoint == 3
        assert result.accepted == 1
        revision_before_duplicate = result.generation

        duplicate = restarted.project_records(rows[:1], mode="live", source_high_watermark=3)
        assert duplicate.checkpoint == 3
        assert duplicate.accepted == 0
        assert duplicate.duplicates == 1
        assert duplicate.generation == revision_before_duplicate

        ignored = {
            "ingested_seq": 4,
            "ingested_at": NOW,
            "event_id": "ignored-relational-worker-event",
            "event_type": "unrelated_telemetry",
            "created_at": NOW,
            "payload": {
                "event_id": "ignored-relational-worker-event",
                "event_type": "unrelated_telemetry",
                "created_at": NOW,
            },
        }
        ignored_result = restarted.project_records(
            [ignored], mode="live", source_high_watermark=4
        )
        assert ignored_result.checkpoint == 4
        assert ignored_result.ignored == 1

        import psycopg

        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {schema}.event_receipts")
            assert cur.fetchone()[0] == 4
            cur.execute(f"SELECT COUNT(*) FROM {schema}.journey_stages")
            assert cur.fetchone()[0] == 3
            cur.execute(
                f"SELECT stage_coverage FROM {schema}.journeys WHERE journey_id=%s",
                ("tj-paper-001",),
            )
            coverage = cur.fetchone()[0]
            assert set(coverage) == {
                "signal_generation",
                "trade_decision",
                "risk_evaluation",
            }
    finally:
        import psycopg

        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_postgres_lifecycle_source_filters_watermark_and_fetch(monkeypatch):
    calls: list[tuple] = []

    class Connection:
        async def fetchval(self, query: str, *event_types: list[str]) -> int:
            calls.append(("fetchval", query, *event_types))
            return 44

        async def fetch(
            self,
            query: str,
            checkpoint: int,
            event_types: list[str],
            limit: int,
        ) -> list[dict]:
            calls.append(("fetch", query, checkpoint, event_types, limit))
            return []

        async def close(self) -> None:
            calls.append(("close",))

    async def connect(dsn: str) -> Connection:
        calls.append(("connect", dsn))
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit")

    assert asyncio.run(source.high_watermark()) == 44
    assert asyncio.run(source.fetch_after(7, limit=9)) == []

    fetchval = next(call for call in calls if call[0] == "fetchval" and "event_type = ANY" in call[1])
    fetch = next(call for call in calls if call[0] == "fetch")
    assert "event_type = ANY" in fetchval[1]
    assert "event_type = ANY" in fetch[1]
    assert "MAX(ingested_seq)" in fetchval[1]
    assert tuple(fetchval[2]) == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[2] == 7
    assert tuple(fetch[3]) == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[4] == 9


def test_postgres_source_fence_crosses_real_sequence_hole_and_waits_for_writer(
    relational_postgres_dsn: str, request: pytest.FixtureRequest,
) -> None:
    """Real PG proof: aborted allocation is skippable; in-flight writer is fenced."""
    import psycopg
    from services.telemetry.ingest_svc import build_postgres_write_fn

    schema = f"frontier_{uuid.uuid4().hex[:12]}"
    projection_schema = f"frontier_projection_{uuid.uuid4().hex[:10]}"
    table = f"{schema}.telemetry_events"
    with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'CREATE SEQUENCE "{schema}".telemetry_events_seq')
        cur.execute(
            f'''CREATE TABLE "{schema}".telemetry_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                ingested_seq BIGINT NOT NULL DEFAULT nextval('"{schema}".telemetry_events_seq'),
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            )'''
        )

    def cleanup() -> None:
        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'DROP SCHEMA IF EXISTS {projection_schema} CASCADE')

    request.addfinalizer(cleanup)
    source = PostgresLifecycleSource(
        relational_postgres_dsn, table=table, include_non_lifecycle=True
    )
    writer = build_postgres_write_fn(relational_postgres_dsn, table=table)
    try:
        # A rolled-back insert consumes sequence 1 without creating a row.
        with psycopg.connect(relational_postgres_dsn) as conn:
            with conn.transaction():
                conn.execute(
                    f'INSERT INTO "{schema}".telemetry_events (event_id,event_type,created_at,payload) VALUES (%s,%s,%s,%s)',
                    ("aborted", "signal_generation", NOW, json.dumps({"event_id": "aborted"})),
                )
                raise RuntimeError("abort sequence allocation")
    except RuntimeError:
        pass

    async def insert_committed() -> None:
        result = await writer([{
            "event_id": "committed-after-hole",
            "event_type": "signal_generation",
            "created_at": NOW,
        }])
        assert result.success

    asyncio.run(insert_committed())
    rows = asyncio.run(source.fetch_after(0, limit=10))
    assert [row["ingested_seq"] for row in rows] == [2]
    assert source.last_frontier_seq == 2
    projection_store = ProjectionStore(
        relational_postgres_dsn, schema=projection_schema, bootstrap=True
    )
    projector = RelationalLifecycleProjector(
        projection_store, controller_id=f"frontier-{uuid.uuid4().hex[:8]}"
    )
    projected = projector.project_records(
        rows,
        mode="recovery",
        source_high_watermark=2,
        source_frontier_seq=source.last_frontier_seq,
    )
    assert projected.checkpoint == 2
    assert projected.quarantined == 1

    # Hold the exact writer fence and lower allocated sequence open. A source
    # snapshot must wait for its commit rather than claiming absence.
    import threading

    ready = threading.Event()
    release = threading.Event()

    def delayed_writer() -> None:
        with psycopg.connect(relational_postgres_dsn) as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (table,))
                conn.execute(
                    f'INSERT INTO "{schema}".telemetry_events (event_id,event_type,created_at,payload) VALUES (%s,%s,%s,%s)',
                    ("delayed-lower", "signal_generation", NOW, json.dumps({"event_id": "delayed-lower"})),
                )
                ready.set()
                release.wait(timeout=5)

    thread = threading.Thread(target=delayed_writer)
    thread.start()
    assert ready.wait(timeout=5)
    async def fetch_after_release():
        task = asyncio.create_task(source.fetch_after(2, limit=10))
        await asyncio.sleep(0.1)
        assert not task.done()
        release.set()
        return await task

    delayed_rows = asyncio.run(fetch_after_release())
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert [row["ingested_seq"] for row in delayed_rows] == [3]
    assert source.last_frontier_seq == 3


def test_postgres_lifecycle_source_startup_check_is_read_only_and_bounded(monkeypatch):
    calls: list[tuple] = []

    class Connection:
        async def fetchrow(self, query: str) -> None:
            calls.append(("fetchrow", query))
            return None

        async def close(self) -> None:
            calls.append(("close",))

    async def connect(dsn: str) -> Connection:
        calls.append(("connect", dsn))
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))

    asyncio.run(PostgresLifecycleSource("postgresql://unit").verify_read_contract())

    assert calls[0] == ("connect", "postgresql://unit")
    assert calls[1] == (
        "fetchrow",
        "SELECT ingested_seq, ingested_at FROM telemetry_events LIMIT 1",
    )
    assert calls[2] == ("close",)
    assert all("ALTER TABLE" not in str(call) and "UPDATE telemetry_events" not in str(call) for call in calls)


def test_postgres_lifecycle_source_startup_close_cannot_extend_deadline(monkeypatch):
    calls: list[str] = []

    class Connection:
        async def fetchrow(self, query: str) -> None:
            calls.append("fetchrow")
            return None

        async def close(self) -> None:
            calls.append("close")
            await asyncio.Event().wait()

        def terminate(self) -> None:
            calls.append("terminate")

    async def connect(dsn: str) -> Connection:
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    monkeypatch.setattr(lifecycle_projector_module, "DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(PostgresLifecycleSource("postgresql://unit").verify_read_contract())

    assert calls == ["connect", "fetchrow", "close", "terminate"]


class _FakeRelationalProjector:
    def __init__(
        self,
        checkpoint: int = 0,
        deployment_sha: str = "unknown",
        status: str = "ready",
        accepted_live: bool = True,
        mode: str = "live",
        backlog: int = 0,
        quarantine_count: int = 0,
    ):
        self.checkpoint = checkpoint
        self.deployment_sha = deployment_sha
        self.controller = {
            "checkpoint": checkpoint,
            "deployment_sha": deployment_sha,
            "status": status,
            "source_high_watermark": 0,
            "backlog": backlog,
            "accepted_live": accepted_live,
            "last_error": None,
            "mode": mode,
            "unresolved_quarantine_count": quarantine_count,
        }

    def project_records(self, rows, mode="live", source_high_watermark=0, source_frontier_seq=0):
        if rows:
            self.checkpoint = max(rows[-1]["ingested_seq"], int(source_frontier_seq or 0))
            self.controller["checkpoint"] = self.checkpoint
        elif source_frontier_seq:
            self.checkpoint = max(self.checkpoint, int(source_frontier_seq))
            self.controller["checkpoint"] = self.checkpoint
        self.controller["source_high_watermark"] = source_high_watermark
        self.controller["mode"] = mode

    def record_poll(self, source_high_watermark=0, backlog=0, mode="live", source_frontier_seq=0):
        if source_frontier_seq:
            self.checkpoint = max(self.checkpoint, int(source_frontier_seq))
            self.controller["checkpoint"] = self.checkpoint
        self.controller["source_high_watermark"] = source_high_watermark
        self.controller["backlog"] = backlog
        self.controller["mode"] = mode

    def record_source_failure(self, error, backlog=0):
        self.controller["status"] = "degraded"
        self.controller["last_error"] = str(error)
        self.controller["backlog"] = backlog


def test_worker_startup_verifies_read_contract_before_publishing_identity(monkeypatch):
    calls: list[str] = []

    class Source:
        async def verify_read_contract(self) -> None:
            calls.append("verify")

        async def high_watermark(self) -> int:
            calls.append("high")
            return 0

        async def start_listener(self) -> None:
            calls.append("listen")

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            calls.append("fetch")
            return []

        async def wait(self, timeout: float) -> None:
            calls.append("wait")

        async def close(self) -> None:
            calls.append("close")

    source = Source()
    fake_projector = _FakeRelationalProjector(
        deployment_sha="cafebabecafebabecafebabecafebabecafebabe"
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: fake_projector,
    )
    monkeypatch.setattr(lifecycle_projector_module, "PostgresLifecycleSource", lambda *args, **kwargs: source)
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "1")
    monkeypatch.setenv("GIT_SHA", "cafebabecafebabecafebabecafebabecafebabe")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    assert calls == ["verify", "high", "listen", "high", "fetch", "close"]
    assert fake_projector.controller["deployment_sha"] == (
        "cafebabecafebabecafebabecafebabecafebabe"
    )


def test_worker_reaffirms_retained_snapshot_after_source_window_truncates(monkeypatch):
    fake_projector = _FakeRelationalProjector(
        checkpoint=6_099_223,
        deployment_sha="feedfacefeedfacefeedfacefeedfacefeedface",
    )

    class Source:
        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            # The source table is a retained window.  Its currently-empty
            # lifecycle subset must not invalidate the durable projection.
            return 0

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            assert checkpoint == 6_099_223
            return []

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: fake_projector,
    )
    monkeypatch.setattr(lifecycle_projector_module, "PostgresLifecycleSource", lambda *args, **kwargs: Source())
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "1")
    monkeypatch.setenv("GIT_SHA", "feedfacefeedfacefeedfacefeedfacefeedface")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    controller = fake_projector.controller
    assert controller["deployment_sha"] == "feedfacefeedfacefeedfacefeedfacefeedface"
    assert controller["checkpoint"] == 6_099_223
    assert controller["source_high_watermark"] == 0
    assert controller["backlog"] == 0
    assert controller["accepted_live"] is True


def test_worker_startup_contract_failure_publishes_degraded_health(monkeypatch):
    calls: list[str] = []

    class Source:
        async def verify_read_contract(self) -> None:
            calls.append("verify")
            raise TimeoutError("telemetry schema check timed out")

        async def high_watermark(self) -> int:
            raise AssertionError("worker must not read after startup contract failure")

        async def start_listener(self) -> None:
            raise AssertionError("worker must not listen after startup contract failure")

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            raise AssertionError("worker must not fetch after startup contract failure")

        async def wait(self, timeout: float) -> None:
            calls.append("wait")

        async def close(self) -> None:
            calls.append("close")

    source = Source()
    fake_projector = _FakeRelationalProjector(
        deployment_sha="cafebabecafebabecafebabecafebabecafebabe"
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: fake_projector,
    )
    monkeypatch.setattr(lifecycle_projector_module, "PostgresLifecycleSource", lambda *args, **kwargs: source)
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "1")
    monkeypatch.setenv("GIT_SHA", "cafebabecafebabecafebabecafebabecafebabe")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    assert calls == ["verify", "close", "close"]
    assert fake_projector.controller["status"] == "degraded"
    assert fake_projector.controller["deployment_sha"] == "cafebabecafebabecafebabecafebabecafebabe"
    assert "TimeoutError: telemetry schema check timed out" == fake_projector.controller["last_error"]


def test_legacy_json_classes_and_writer_paths_are_retired():
    assert not hasattr(lifecycle_projector_module, "AtomicProjectionBundle")
    assert not hasattr(lifecycle_projector_module, "projector_readiness")
    assert not hasattr(lifecycle_projector_module, "DEFAULT_GENERATION_RETENTION")
    assert not hasattr(lifecycle_projector_module, "DEFAULT_STAGING_MAX_AGE_SECONDS")
    assert not hasattr(lifecycle_projector_module, "DEFAULT_HEALTH_STATE_PATH")
    assert not hasattr(lifecycle_projector_module, "DEFAULT_STATE_PATH")
    assert not hasattr(lifecycle_projector_module, "DEFAULT_ROOT")
    assert lifecycle_projector_module.LifecycleProjector is RelationalLifecycleProjector


def test_legacy_writer_backend_fails_closed(monkeypatch):
    for backend in ("disabled", "legacy_json", "json"):
        monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", backend)
        with pytest.raises(RuntimeError, match="Legacy JSON projector writer is retired"):
            lifecycle_projector_module._configured_relational_projector()


def test_healthcheck_reports_relational_health(monkeypatch, capsys):
    fake_projector = _FakeRelationalProjector(
        deployment_sha="relational-health-sha",
        status="ready",
        accepted_live=True,
        mode="live",
        backlog=0,
        quarantine_count=0,
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: fake_projector,
    )
    code = lifecycle_projector_module.healthcheck()
    assert code == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["schema_version"] == "pantheon.lifecycle-projector-relational-health.v1"
    assert data["writer_backend"] == "shadow"
    assert data["ready"] is True
    assert data["controller"]["deployment_sha"] == "relational-health-sha"


def test_source_event_normalizes_datetime_row_and_iso_z_payload():
    from services.trade_journey.lifecycle_projector import InvalidLifecycleEvent

    row = {
        "event_id": "evt-001",
        "event_type": "signal_generation",
        "created_at": datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc),
        "payload": {
            "event_id": "evt-001",
            "event_type": "signal_generation",
            "created_at": "2026-08-24T12:00:00Z",
        },
    }
    event = LifecycleProjector._source_event(row)
    assert event["event_id"] == "evt-001"
    assert event["created_at"] == "2026-08-24T12:00:00Z"

    # Mismatch in actual timestamp should still raise
    mismatch_row = {
        "event_id": "evt-001",
        "event_type": "signal_generation",
        "created_at": datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc),
        "payload": {
            "event_id": "evt-001",
            "event_type": "signal_generation",
            "created_at": "2026-08-24T12:00:01Z",
        },
    }
    with pytest.raises(InvalidLifecycleEvent, match="source row/payload created_at mismatch"):
        LifecycleProjector._source_event(mismatch_row)


def test_relational_projector_accepts_postgres_datetime_created_at_with_z_payload():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, deployment_sha="relational-test", clock=lambda: NOW)
    rows = lifecycle_rows()
    # Modify first row to have PostgreSQL-style datetime object for row created_at and Z string in payload
    first_row = dict(rows[0])
    first_row["created_at"] = datetime(2026, 7, 15, 0, 0, 1, tzinfo=timezone.utc)
    first_row["payload"] = dict(first_row["payload"])
    first_row["payload"]["created_at"] = "2026-07-15T00:00:01Z"

    result = projector.project_records([first_row], mode="live", source_high_watermark=1)
    assert result.checkpoint == 1
    assert result.accepted == 1
    assert result.quarantined == 0
    assert len(store.mutations) == 1
    mutation = store.mutations[-1]
    assert len(mutation.quarantines) == 0
    assert len(mutation.receipts) == 1
    assert mutation.receipts[0].disposition == "applied"


def test_relational_projector_fails_closed_without_psycopg_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND", "shadow")
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://pantheon_app:pantheon_app@localhost:5432/pantheon")
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(RuntimeError, match="psycopg is required for ProjectionStore"):
        lifecycle_projector_module._configured_relational_projector()


def test_projector_runtime_requirements_declares_psycopg_driver() -> None:
    root = Path(__file__).resolve().parents[2]
    req_path = root / "services" / "telemetry" / "requirements.txt"
    content = req_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    assert any("psycopg" in line for line in lines), f"psycopg missing from {req_path}:\n{content}"


def test_run_worker_startup_fails_immediately_without_psycopg_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://pantheon_app:pantheon_app@localhost:5432/pantheon")
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(RuntimeError, match="psycopg is required for ProjectionStore"):
        asyncio.run(lifecycle_projector_module.run_worker())


def test_record_poll_transitions_to_ready_when_live_and_backlog_zero() -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="relational-poll-test", clock=lambda: NOW
    )
    rows = lifecycle_rows()
    # First, project records in recovery mode up to seq 2
    projector.project_records(rows[:2], mode="recovery", source_high_watermark=2)
    assert projector.controller["status"] == "recovering"
    assert projector.controller["accepted_live"] is False
    assert projector.controller["checkpoint"] == 2

    # Polling in live mode with 0 backlog after catch-up must transition controller to ready
    projector.record_poll(source_high_watermark=2, backlog=0, mode="live")
    ctrl = projector.controller
    assert ctrl["status"] == "ready"
    assert ctrl["accepted_live"] is True
    assert ctrl["mode"] == "live"
    assert ctrl["backlog"] == 0
    assert ctrl["source_high_watermark"] == 2
    assert ctrl["checkpoint"] == 2


def test_record_poll_preserves_recovering_when_backlog_positive_or_recovery_mode() -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="relational-poll-test", clock=lambda: NOW
    )
    rows = lifecycle_rows()
    projector.project_records(rows[:2], mode="recovery", source_high_watermark=2)

    # Positive backlog in live mode remains recovering
    projector.record_poll(source_high_watermark=5, backlog=3, mode="live")
    ctrl = projector.controller
    assert ctrl["status"] == "recovering"
    assert ctrl["accepted_live"] is False
    assert ctrl["backlog"] == 3

    # Zero backlog in recovery mode remains recovering
    projector.record_poll(source_high_watermark=2, backlog=0, mode="recovery")
    ctrl = projector.controller
    assert ctrl["status"] == "recovering"
    assert ctrl["accepted_live"] is False
    assert ctrl["mode"] == "recovery"



def test_project_records_sets_ready_status_when_live_and_batch_catches_up() -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="relational-live-catchup", clock=lambda: NOW
    )
    rows = lifecycle_rows()

    # Ingesting batch up to watermark in live mode produces ready controller
    result = projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    assert result.accepted == 2
    ctrl = projector.controller
    assert ctrl["status"] == "ready"
    assert ctrl["accepted_live"] is True
    assert ctrl["mode"] == "live"
    assert ctrl["backlog"] == 0


def test_run_worker_catchup_transitions_from_recovery_to_live_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="live-ready-catchup-sha", clock=lambda: NOW
    )
    rows = lifecycle_rows()[:2]

    class Source:
        def __init__(self):
            self.tick = 0

        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            return 2

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            self.tick += 1
            if checkpoint < 2:
                return rows
            return []

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: projector,
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "PostgresLifecycleSource",
        lambda *args, **kwargs: Source(),
    )
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "2")
    monkeypatch.setenv("GIT_SHA", "live-ready-catchup-sha")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    ctrl = projector.controller
    assert ctrl["checkpoint"] == 2
    assert ctrl["source_high_watermark"] == 2
    assert ctrl["backlog"] == 0
    assert ctrl["mode"] == "live"
    assert ctrl["status"] == "ready"
    assert ctrl["accepted_live"] is True


def test_postgres_lifecycle_source_high_watermark_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def connect(dsn: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(source.high_watermark())


def test_postgres_lifecycle_source_high_watermark_query_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        async def fetchval(self, *args: object, **kwargs: object) -> None:
            calls.append("fetchval")
            await asyncio.Event().wait()

        async def close(self) -> None:
            calls.append("close")

        def terminate(self) -> None:
            calls.append("terminate")

    async def connect(dsn: str) -> Connection:
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(source.high_watermark())

    assert "connect" in calls
    assert "fetchval" in calls
    assert "close" in calls or "terminate" in calls


def test_postgres_lifecycle_source_high_watermark_close_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        async def fetchval(self, *args: object, **kwargs: object) -> int:
            calls.append("fetchval")
            return 10

        async def close(self) -> None:
            calls.append("close")
            await asyncio.Event().wait()

        def terminate(self) -> None:
            calls.append("terminate")

    async def connect(dsn: str) -> Connection:
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(source.high_watermark())

    assert calls == ["connect", "fetchval", "close", "terminate"]


def test_postgres_lifecycle_source_fetch_after_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def connect(dsn: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(source.fetch_after(0, limit=10))


def test_postgres_lifecycle_source_fetch_after_query_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        async def fetch(self, *args: object, **kwargs: object) -> list[dict]:
            calls.append("fetch")
            await asyncio.Event().wait()

        async def close(self) -> None:
            calls.append("close")

        def terminate(self) -> None:
            calls.append("terminate")

    async def connect(dsn: str) -> Connection:
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(source.fetch_after(0, limit=10))

    assert "connect" in calls
    assert "fetch" in calls
    assert "close" in calls or "terminate" in calls


def test_postgres_lifecycle_source_fetch_after_close_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        async def fetch(self, *args: object, **kwargs: object) -> list[dict]:
            calls.append("fetch")
            return []

        async def close(self) -> None:
            calls.append("close")
            await asyncio.Event().wait()

        def terminate(self) -> None:
            calls.append("terminate")

    async def connect(dsn: str) -> Connection:
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(source.fetch_after(0, limit=10))

    assert calls == ["connect", "fetch", "close", "terminate"]


def test_postgres_lifecycle_source_start_listener_and_close_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        async def add_listener(self, *args: object, **kwargs: object) -> None:
            calls.append("add_listener")
            await asyncio.Event().wait()

        async def close(self) -> None:
            calls.append("close")
            await asyncio.Event().wait()

        def terminate(self) -> None:
            calls.append("terminate")

    async def connect(dsn: str) -> Connection:
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(source.start_listener())

    assert "connect" in calls
    assert "add_listener" in calls
    assert "close" in calls or "terminate" in calls


def test_postgres_lifecycle_source_close_listener_bounded_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        async def close(self) -> None:
            calls.append("close")
            await asyncio.Event().wait()

        def terminate(self) -> None:
            calls.append("terminate")

    source = PostgresLifecycleSource("postgresql://unit", timeout_seconds=0.01)
    source._listener = Connection()

    asyncio.run(source.close())
    assert calls == ["close", "terminate"]
    assert source._listener is None


def test_run_worker_recovers_after_source_timeout_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="timeout-recovery-sha", clock=lambda: NOW
    )

    class RecoveringSource:
        def __init__(self) -> None:
            self.tick = 0

        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            self.tick += 1
            if self.tick == 1:
                raise TimeoutError("lifecycle source high_watermark connect deadline exhausted (10.0s)")
            return 0

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            return []

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: projector,
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "PostgresLifecycleSource",
        lambda *args, **kwargs: RecoveringSource(),
    )
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "2")
    monkeypatch.setenv("GIT_SHA", "timeout-recovery-sha")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    ctrl = projector.controller
    assert ctrl["checkpoint"] == 0
    assert ctrl["source_high_watermark"] == 0
    assert ctrl["backlog"] == 0
    assert ctrl["mode"] == "live"
    assert ctrl["status"] == "ready"
    assert ctrl["accepted_live"] is True
    assert ctrl["last_error"] is None


def test_run_worker_env_var_threads_source_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, object] = {}

    class DummySource:
        def __init__(self, dsn: str, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            return 0

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            return []

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    fake_projector = _FakeRelationalProjector()
    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: fake_projector,
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "PostgresLifecycleSource",
        DummySource,
    )
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "1")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    assert captured_kwargs["timeout_seconds"] == 7.5
    assert captured_kwargs["startup_timeout_seconds"] == 12.5


@pytest.mark.parametrize(
    "invalid_timeout",
    [
        float("inf"),
        float("-inf"),
        float("nan"),
        0,
        0.0,
        -1.0,
        -10,
        "inf",
        "-inf",
        "+inf",
        "nan",
        "NaN",
        "0",
        "-5.0",
        True,
        False,
        "invalid",
    ],
)
def test_postgres_lifecycle_source_rejects_non_finite_and_non_positive_timeouts(
    invalid_timeout: object,
) -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be a finite positive number"):
        PostgresLifecycleSource("postgresql://unit", timeout_seconds=invalid_timeout)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="startup_timeout_seconds must be a finite positive number"):
        PostgresLifecycleSource("postgresql://unit", startup_timeout_seconds=invalid_timeout)  # type: ignore[arg-type]


def test_postgres_lifecycle_source_accepts_valid_timeouts() -> None:
    source = PostgresLifecycleSource("postgresql://unit")
    assert source.timeout_seconds == 10.0
    assert source.startup_timeout_seconds == 10.0

    source_custom = PostgresLifecycleSource(
        "postgresql://unit",
        timeout_seconds=5.5,
        startup_timeout_seconds=15.0,
    )
    assert source_custom.timeout_seconds == 5.5
    assert source_custom.startup_timeout_seconds == 15.0

    source_str = PostgresLifecycleSource(
        "postgresql://unit",
        timeout_seconds="3.2",  # type: ignore[arg-type]
        startup_timeout_seconds="8.1",  # type: ignore[arg-type]
    )
    assert source_str.timeout_seconds == 3.2
    assert source_str.startup_timeout_seconds == 8.1


@pytest.mark.parametrize(
    ("env_var", "invalid_value", "expected_name"),
    [
        ("LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS", "inf", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS", "-inf", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS", "nan", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS", "0", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS", "-5.0", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS", "inf", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS", "nan", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS", "0", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS", "-1", "LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS", "inf", "LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS", "-inf", "LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS", "nan", "LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS", "0", "LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS"),
        ("LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS", "-10", "LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS"),
    ],
)
def test_run_worker_rejects_invalid_timeout_env_vars(
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    invalid_value: str,
    expected_name: str,
) -> None:
    fake_projector = _FakeRelationalProjector()
    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: fake_projector,
    )
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv(env_var, invalid_value)

    with pytest.raises(ValueError, match=f"{expected_name} must be a finite positive number"):
        asyncio.run(lifecycle_projector_module.run_worker())


def test_configured_relational_projector_binds_projection_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://pantheon_app:pantheon_app@localhost:5432/pantheon")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_PROJECTION_TIMEOUT_SECONDS", "8.0")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_PROJECTION_CONNECT_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_PROJECTION_STATEMENT_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_PROJECTION_LOCK_TIMEOUT_SECONDS", "2.5")

    captured_kwargs: dict[str, object] = {}

    def mock_store_init(self, *args, **kwargs):
        captured_kwargs.update(kwargs)
        self.dsn = args[0] if args else kwargs.get("dsn", "")
        self.schema = kwargs.get("schema", "trade_journey_projection")
        self.connect_timeout_seconds = kwargs.get("connect_timeout_seconds", 10.0)
        self.statement_timeout_seconds = kwargs.get("statement_timeout_seconds", 10.0)
        self.lock_timeout_seconds = kwargs.get("lock_timeout_seconds", 10.0)
        self._connect = lambda *a, **kw: None

    monkeypatch.setattr(ProjectionStore, "__init__", mock_store_init)
    monkeypatch.setattr(
        ProjectionStore,
        "get_controller_state",
        lambda *a, **kw: None,
    )

    projector = lifecycle_projector_module._configured_relational_projector()
    assert captured_kwargs["timeout_seconds"] == 8.0
    assert captured_kwargs["connect_timeout_seconds"] == 3.5
    assert captured_kwargs["statement_timeout_seconds"] == 4.5
    assert captured_kwargs["lock_timeout_seconds"] == 2.5
    assert projector.store.connect_timeout_seconds == 3.5
    assert projector.store.statement_timeout_seconds == 4.5
    assert projector.store.lock_timeout_seconds == 2.5


def test_run_worker_recovers_after_startup_projector_timeout_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="startup-timeout-sha", clock=lambda: NOW
    )

    class StubSource:
        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            return 1

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            if checkpoint >= 1:
                return []
            return lifecycle_rows()[:1]

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    startup_attempts = 0

    def mock_configured_projector():
        nonlocal startup_attempts
        startup_attempts += 1
        if startup_attempts == 1:
            raise TimeoutError("ProjectionStore connection to database timed out after 10.0s")
        return projector

    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        mock_configured_projector,
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "PostgresLifecycleSource",
        lambda *args, **kwargs: StubSource(),
    )
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "3")
    monkeypatch.setenv("GIT_SHA", "startup-timeout-sha")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    assert startup_attempts == 2
    assert projector.checkpoint == 1
    assert projector.controller["status"] == "ready"
    assert projector.controller["accepted_live"] is True


def test_run_worker_recovers_after_blocked_store_batch_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="batch-timeout-sha", clock=lambda: NOW
    )

    class StubSource:
        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            return 1

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            if checkpoint >= 1:
                return []
            return lifecycle_rows()[:1]

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    orig_exec_batch = store.execute_batch_transaction
    batch_call_count = 0

    def mock_execute_batch_transaction(controller_id, tenant_scope, environment_scope, mutation):
        nonlocal batch_call_count
        if mutation.receipts:
            batch_call_count += 1
            if batch_call_count == 1:
                raise TimeoutError("ProjectionStore connection to database timed out after 10.0s")
        return orig_exec_batch(controller_id, tenant_scope, environment_scope, mutation)

    monkeypatch.setattr(store, "execute_batch_transaction", mock_execute_batch_transaction)
    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: projector,
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "PostgresLifecycleSource",
        lambda *args, **kwargs: StubSource(),
    )
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "3")
    monkeypatch.setenv("GIT_SHA", "batch-timeout-sha")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    assert batch_call_count == 2

    assert len(store.mutations) == 3
    failure_mutation = store.mutations[0]
    assert failure_mutation.status == "failed"
    assert failure_mutation.accepted_live is False
    assert (
        "TimeoutError: ProjectionStore connection to database timed out after 10.0s"
        in failure_mutation.error_message
    )

    recovery_mutation = store.mutations[1]
    assert recovery_mutation.status == "recovering"
    assert recovery_mutation.mode == "recovery"
    assert len(recovery_mutation.receipts) == 1

    poll_mutation = store.mutations[2]
    assert poll_mutation.status == "ready"
    assert poll_mutation.mode == "live"
    assert poll_mutation.accepted_live is True
    assert projector.checkpoint == 1
    assert projector.controller["status"] == "ready"


def test_run_worker_recovers_after_projection_store_timeout_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store, deployment_sha="timeout-recovery-sha", clock=lambda: NOW
    )

    class StubSource:
        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            return 1

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            if checkpoint >= 1:
                return []
            return lifecycle_rows()[:1]

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    call_count = 0
    orig_project_records = projector.project_records

    def failing_project_records(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError("canceling statement due to statement timeout (10.0s)")
        return orig_project_records(*args, **kwargs)

    monkeypatch.setattr(projector, "project_records", failing_project_records)
    monkeypatch.setattr(
        lifecycle_projector_module,
        "_configured_relational_projector",
        lambda: projector,
    )
    monkeypatch.setattr(
        lifecycle_projector_module,
        "PostgresLifecycleSource",
        lambda *args, **kwargs: StubSource(),
    )
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "3")
    monkeypatch.setenv("GIT_SHA", "timeout-recovery-sha")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    assert call_count == 2

    # Verify exact durable mutations recorded across failure and recovery
    assert len(store.mutations) == 3

    # Tick 1: ProjectionStore timeout failure causes durable degraded/failed controller mutation
    # without advancing the checkpoint prematurely.
    failure_mutation = store.mutations[0]
    assert failure_mutation.status == "failed"
    assert failure_mutation.accepted_live is False
    assert (
        "TimeoutError: canceling statement due to statement timeout (10.0s)"
        in failure_mutation.error_message
    )
    assert failure_mutation.receipts == []
    assert failure_mutation.journeys == []

    # Tick 2: Recovery tick successfully projects batch and advances checkpoint
    recovery_mutation = store.mutations[1]
    assert recovery_mutation.status == "recovering"
    assert recovery_mutation.mode == "recovery"
    assert recovery_mutation.error_message == ""
    assert len(recovery_mutation.receipts) == 1
    assert recovery_mutation.receipts[0].ingested_seq == 1

    # Tick 3: Steady-state poll transitions to live and ready status
    poll_mutation = store.mutations[2]
    assert poll_mutation.status == "ready"
    assert poll_mutation.mode == "live"
    assert poll_mutation.accepted_live is True
    assert poll_mutation.error_message == ""

    # Final projector controller reflects successful recovery
    ctrl = projector.controller
    assert ctrl["checkpoint"] == 1
    assert ctrl["status"] == "ready"
    assert ctrl["accepted_live"] is True
    assert ctrl["last_error"] is None


@pytest.mark.parametrize(
    ("env_var", "invalid_value"),
    [
        ("LIFECYCLE_PROJECTOR_PROJECTION_TIMEOUT_SECONDS", "inf"),
        ("LIFECYCLE_PROJECTOR_PROJECTION_TIMEOUT_SECONDS", "0"),
        ("LIFECYCLE_PROJECTOR_PROJECTION_TIMEOUT_SECONDS", "-1.0"),
        ("LIFECYCLE_PROJECTOR_PROJECTION_CONNECT_TIMEOUT_SECONDS", "nan"),
        ("LIFECYCLE_PROJECTOR_PROJECTION_STATEMENT_TIMEOUT_SECONDS", "0"),
        ("LIFECYCLE_PROJECTOR_PROJECTION_LOCK_TIMEOUT_SECONDS", "-5"),
    ],
)
def test_configured_relational_projector_rejects_invalid_timeout_env_vars(
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    invalid_value: str,
) -> None:
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv(env_var, invalid_value)

    with pytest.raises(ValueError, match="must be a finite positive number"):
        lifecycle_projector_module._configured_relational_projector()


def _retry_rows(rows: list[dict], journey_id: str, *, first_seq: int, count: int = 2) -> list[dict]:
    """Re-emit a decision's first rows under a freshly minted journey (a tick retry)."""
    retry = []
    envelope = None
    for offset, source in enumerate(rows[:count]):
        row = json.loads(json.dumps(source))
        payload = row["payload"]
        event_id = _uuid(900 + first_seq + offset)
        if envelope is None:
            envelope = mint_trade_envelope(
                {
                    "tenant_id": payload["correlation_envelope"]["tenant_id"],
                    "environment": payload["correlation_envelope"]["environment"],
                    "trace_id": _uuid(7000 + first_seq),
                },
                producer="execution.signal-decision",
                event_id=event_id,
                journey_id=journey_id,
                now=payload["created_at"],
            )
        else:
            envelope = propagate_envelope(
                envelope,
                producer="test.lifecycle",
                event_id=event_id,
                event_time=payload["created_at"],
            )
        payload["event_id"] = event_id
        payload["correlation_envelope"] = envelope
        row.update(event_id=event_id, ingested_seq=first_seq + offset)
        retry.append(row)
    return retry


def test_retry_of_projected_decision_with_new_journey_keeps_original_binding():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    rows = lifecycle_rows()
    projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    retry = _retry_rows(rows, "tj-paper-retry", first_seq=3)

    restarted = RelationalLifecycleProjector(store, clock=lambda: NOW)
    result = restarted.project_records(retry, mode="live", source_high_watermark=4)

    assert (result.accepted, result.ignored, result.quarantined) == (0, 2, 0)
    assert result.checkpoint == 4
    assert store.controller.unresolved_quarantine_count == 0
    key = (IDENTITY["tenant_id"], IDENTITY["environment"], "decision_id", "decision-paper-001")
    assert store.identity_links[key] == "tj-paper-001"
    assert (IDENTITY["tenant_id"], IDENTITY["environment"], "tj-paper-retry") not in store.stage_events
    # Later original-journey progress still projects after the retry.
    follow = restarted.project_records(rows[2:3], mode="live", source_high_watermark=5)
    assert follow.accepted == 1


def test_retry_in_same_batch_is_ignored_and_distinct_decisions_stay_separate():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    rows = lifecycle_rows()
    retry = _retry_rows(rows, "tj-paper-retry", first_seq=3)
    distinct = _retry_rows(rows, "tj-paper-other", first_seq=5)
    for row in distinct:
        payload = row["payload"]
        payload["run_id"] = "run-paper-002"
        payload["signal_id"] = "signal-paper-002"
        payload["trace_id"] = _uuid(8002)
        payload["metadata"].update(
            run_id="run-paper-002",
            signal_id="signal-paper-002",
            decision_id="decision-paper-002",
            client_order_id="client-order-paper-002",
            order_id="order-paper-002",
            reconciliation_id="reconciliation-paper-002",
        )

    result = projector.project_records(
        rows[:2] + retry + distinct, mode="live", source_high_watermark=6
    )

    assert (result.accepted, result.ignored) == (4, 2)
    assert store.controller.unresolved_quarantine_count == 0


def test_multi_stage_retry_without_decision_id_on_followups_is_ignored():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    rows = lifecycle_rows()
    projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    retry = _retry_rows(rows, "tj-paper-retry", first_seq=3, count=3)
    noop = retry[2]["payload"]
    noop["event_type"] = "paper_order_simulated"
    retry[2]["event_type"] = "paper_order_simulated"
    noop.pop("decision_id", None)
    noop["metadata"].pop("decision_id", None)
    noop["metadata"].update(
        noop_reason="execution_in_progress_elsewhere",
        decision_status="no_order",
        order_status="not_submitted",
        computed_quantity=0.0,
    )
    noop["metrics"] = {
        "action": "execution_in_progress_elsewhere_noop",
        "fill_price": 0.0,
        "fill_quantity": 0.0,
        "submitted_to_broker": False,
    }

    result = projector.project_records(retry, mode="live", source_high_watermark=5)

    assert (result.accepted, result.quarantined) == (0, 0)
    assert result.ignored == 3
    assert (IDENTITY["tenant_id"], IDENTITY["environment"], "tj-paper-retry") not in store.stage_events


def test_retry_journey_with_positive_fill_followup_is_rejected_not_ignored():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    rows = lifecycle_rows()
    projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    retry_with_fill = _retry_rows(rows, "tj-paper-retry", first_seq=3, count=8)
    fill_event_id = retry_with_fill[5]["event_id"]

    with pytest.raises(ValueError, match="already bound"):
        projector.project_records(retry_with_fill, mode="live", source_high_watermark=10)
    assert fill_event_id not in store.receipts

    full_store = _RecordingRelationalStore()
    full_projector = RelationalLifecycleProjector(full_store, clock=lambda: NOW)
    full_projector.project_records(rows[:8], mode="live", source_high_watermark=8)
    second_fill = _retry_rows(rows, "tj-paper-retry-fill", first_seq=9, count=6)
    second_fill[5]["payload"]["metadata"]["fill_id"] = "fill-paper-new-999"
    second_fill[5]["payload"]["metrics"]["fill_quantity"] = 9
    second_fill[5]["payload"]["metrics"]["fill_price"] = 201.5

    with pytest.raises(ValueError, match="already bound"):
        full_projector.project_records(second_fill, mode="live", source_high_watermark=14)
    assert second_fill[5]["event_id"] not in full_store.receipts


def test_same_decision_with_different_signal_is_still_a_conflict():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    rows = lifecycle_rows()
    projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    conflict = _retry_rows(rows, "tj-paper-conflict", first_seq=3)
    for row in conflict:
        row["payload"]["signal_id"] = "signal-paper-other"
        row["payload"]["metadata"]["signal_id"] = "signal-paper-other"

    with pytest.raises(ValueError, match="already bound"):
        projector.project_records(conflict, mode="live", source_high_watermark=4)


def test_same_decision_in_another_tenant_is_not_treated_as_retry():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(store, clock=lambda: NOW)
    rows = lifecycle_rows()
    projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    other = _retry_rows(rows, "tj-paper-tenant-b", first_seq=3)
    for row in other:
        row["payload"]["correlation_envelope"]["tenant_id"] = "tenant-b"
        row["payload"]["metadata"]["tenant_id"] = "tenant-b"

    result = projector.project_records(other, mode="live", source_high_watermark=4)

    assert (result.accepted, result.ignored) == (2, 0)


def test_retained_root42397678868_sell_without_holdings_and_no_order_project_complete_nonfill_truth():
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store,
        deployment_sha="f6d40b77da32309498863a6d66debcc2449268e2",
        clock=lambda: "2026-11-07T18:06:24Z",
    )
    retained_identity = {
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "journey_id": "tj_294b657b-1d64-50d8-b2bf-03e9f3c26a6f",
        "run_id": "run-rb-8aceee4784be4ae581d51ab7ef2243c4-60872d6d0ca7a7522ea9153a",
        "loop_run_id": "lr-run-rb-8aceee4784be4ae581d51ab7ef2243c4-60872d6d0ca7a7522ea9153a",
        "signal_id": "sig-60872d6d0ca7a7522ea9153a11eb8de8",
        "strategy_id": "strategy-persona-e0cd73f41c0fb27c36de",
        "runtime_id": "rt-f45eb161",
        "binding_id": "rb-8aceee4784be4ae581d51ab7ef2243c4",
        "capital_pool_id": "pool-persona-paper-e0cd73f41c0fb27c36de",
        "persona_id": "persona-034dad74863c32a796a4",
        "persona_capital_binding_id": "pcb-persona-paper-e0cd73f41c0fb27c36de",
        "artifact_id": "artifact-persona-paper-e0cd73f41c0fb27c36de",
        "artifact_version": "1.0.0",
        "plan_id": "plan-persona-paper-e0cd73f41c0fb27c36de",
        "trace_id": "f553230a-a918-5a25-b41d-c86e9ccb19f3",
    }
    specs = [
        ("192e0569-ee44-4e5a-9f21-d24415530353", "signal_generation", 1, 11210607, "2026-11-07T18:06:17.000000Z"),
        ("20476142-6e7a-5715-b20f-fa61022cf1aa", "trade_decision", 2, 11210608, "2026-11-07T18:06:17.000000Z"),
        ("483dede1-6213-4d7b-8df4-48d1ae30b747", "order_rejection", 3, 11210635, "2026-11-07T18:06:21.000000Z"),
    ]
    envelope = None
    retained_rows = []
    for event_id, event_type, seq_no, ingested_seq, created_at in specs:
        envelope = (
            mint_trade_envelope(
                {
                    "tenant_id": retained_identity["tenant_id"],
                    "environment": retained_identity["environment"],
                    "trace_id": retained_identity["trace_id"],
                },
                producer="execution.paper_runtime",
                event_id=event_id,
                journey_id=retained_identity["journey_id"],
                now=created_at,
            )
            if envelope is None
            else propagate_envelope(
                envelope,
                producer="execution.paper_runtime",
                event_id=event_id,
                event_time=created_at,
            )
        )
        metadata = {
            **retained_identity,
            "sequence_no": seq_no,
            "causal_parent_id": envelope["causation_event_id"],
            "decision_id": "dec-rb-8aceee4784be4ae581d51ab7ef2243c4-60872d6d0ca7a7522ea9153a",
            "symbol": "2330.TW",
        }
        if event_type == "order_rejection":
            metadata.update(
                side="sell",
                reason="sell_without_holdings",
                reject_reason="sell_without_holdings",
                broker_submission_status="sell_without_holdings",
                rejection_status="rejected",
                order_status="rejected",
                submitted_to_broker=False,
                requested_quantity=1.0,
                holding_quantity=0.0,
            )
        payload = {
            **retained_identity,
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "execution_mode": "paper",
            "deployment_stage": "paper",
            "authority_refs": {"persona_id": retained_identity["persona_id"]},
            "target": {"strategy_id": retained_identity["strategy_id"]},
            "metrics": {"action": "SELL", "fill_quantity": 0.0, "submitted_to_broker": False},
            "metadata": metadata,
            "correlation_envelope": envelope,
        }
        retained_rows.append(
            {
                "ingested_seq": ingested_seq,
                "ingested_at": created_at,
                "event_id": event_id,
                "event_type": event_type,
                "created_at": created_at,
                "payload": payload,
            }
        )

    res = projector.project_records(
        retained_rows, mode="live", source_high_watermark=11210635
    )
    assert res.accepted == 3
    mutation = store.mutations[-1]
    assert len(mutation.journeys) == 1
    assert len(mutation.stages) == 3
    journey = mutation.journeys[0]
    assert journey.journey_id == "tj_294b657b-1d64-50d8-b2bf-03e9f3c26a6f"
    assert journey.status == "failed"
    assert journey.is_terminal is True
    assert journey.stage_coverage["risk_evaluation"]["status"] == "rejected"
    assert journey.stage_coverage["order_submission"]["status"] == "failed"
    diag_stages = journey.diagnostic_summary["diagnostics"][0]["stages"]
    assert "risk_evaluation" not in diag_stages
    assert mutation.loop_runs[0].status == "failed"
    rejection_stage = next(
        s.contract_fields for s in mutation.stages if s.stage_name == "order_submission"
    )
    assert rejection_stage["reason_code"] == "sell_without_holdings"
    assert rejection_stage["quantity"] == 0.0
    assert rejection_stage["requested_quantity"] == 1.0
    assert rejection_stage["holding_quantity"] == 0.0
    assert rejection_stage["submitted_to_broker"] is False

    noop_rows = _retry_rows(lifecycle_rows(), "tj-paper-noop-complete", first_seq=11210636, count=3)
    for row in noop_rows:
        row["payload"]["run_id"] = "run-paper-noop-001"
        row["payload"]["signal_id"] = "sig-paper-noop-001"
        for field in ("client_order_id", "order_id", "reconciliation_id"):
            row["payload"]["metadata"].pop(field, None)
        row["payload"]["metadata"].update(
            run_id="run-paper-noop-001",
            signal_id="sig-paper-noop-001",
            decision_id="dec-paper-noop-001",
        )
    noop_rows[2]["event_type"] = "paper_order_simulated"
    noop_rows[2]["payload"]["event_type"] = "paper_order_simulated"
    noop_rows[2]["payload"]["metadata"].update(
        noop_reason="hold_signal",
        decision_status="no_order",
        order_status="not_submitted",
        computed_quantity=0.0,
        submitted_to_broker=False,
    )
    noop_rows[2]["payload"]["metrics"] = {
        "action": "hold_signal_noop",
        "fill_price": 0.0,
        "fill_quantity": 0.0,
        "submitted_to_broker": False,
    }
    noop_res = projector.project_records(
        noop_rows, mode="live", source_high_watermark=11210638
    )
    assert noop_res.accepted == 3
    noop_mutation = store.mutations[-1]
    noop_journey = noop_mutation.journeys[0]
    assert noop_journey.status == "no_order"
    assert noop_journey.is_terminal is True
    assert noop_journey.stage_coverage["risk_evaluation"]["status"] == "skipped"
    assert noop_journey.stage_coverage["order_submission"]["status"] == "skipped"
    noop_missing_diag = next(
        item
        for item in noop_journey.diagnostic_summary["diagnostics"]
        if item.get("code") == "missing_stages"
    )
    assert "risk_evaluation" not in noop_missing_diag["stages"]
    assert noop_mutation.loop_runs[0].status == "no_order"

    # Negative check: pre-broker transport rejection without bounded guard proof
    # must NOT fabricate risk_evaluation.
    transport_rows = _retry_rows(
        lifecycle_rows(), "tj-paper-transport-reject", first_seq=11210639, count=3
    )
    for row in transport_rows:
        row["payload"]["run_id"] = "run-paper-transport-001"
        row["payload"]["signal_id"] = "sig-paper-transport-001"
        for field in ("client_order_id", "order_id", "reconciliation_id"):
            row["payload"]["metadata"].pop(field, None)
        row["payload"]["metadata"].update(
            run_id="run-paper-transport-001",
            signal_id="sig-paper-transport-001",
            decision_id="dec-paper-transport-001",
        )
    transport_rows[2]["event_type"] = "order_rejection"
    transport_rows[2]["payload"]["event_type"] = "order_rejection"
    transport_rows[2]["payload"]["metadata"].update(
        reason="transport_failed",
        submitted_to_broker=False,
    )
    transport_rows[2]["payload"]["metrics"] = {
        "fill_quantity": 0.0,
        "submitted_to_broker": False,
    }
    transport_res = projector.project_records(
        transport_rows, mode="live", source_high_watermark=11210641
    )
    assert transport_res.accepted == 3
    transport_journey = store.mutations[-1].journeys[0]
    assert "risk_evaluation" not in transport_journey.stage_coverage

    # Negative check: contradictory non-zero fill_quantity or submitted_to_broker=True
    # on a skipped paper_order_simulated after a no_order trade_decision must be
    # preserved through projection and must NOT infer risk_evaluation=skipped.
    for idx, (fill_qty, submitted) in enumerate([(5, False), (0, True)], start=1):
        contra_seq = 11210641 + idx * 3
        contra_rows = _retry_rows(
            lifecycle_rows(), f"tj-paper-contra-{idx}", first_seq=contra_seq - 2, count=3
        )
        for row in contra_rows:
            row["payload"]["run_id"] = f"run-paper-contra-{idx}"
            row["payload"]["signal_id"] = f"sig-paper-contra-{idx}"
            for field in ("client_order_id", "order_id", "reconciliation_id"):
                row["payload"]["metadata"].pop(field, None)
            row["payload"]["metadata"].update(
                run_id=f"run-paper-contra-{idx}",
                signal_id=f"sig-paper-contra-{idx}",
                decision_id=f"dec-paper-contra-{idx}",
            )
        contra_rows[1]["payload"]["metadata"]["decision_status"] = "no_order"
        contra_rows[2]["event_type"] = contra_rows[2]["payload"]["event_type"] = (
            "paper_order_simulated"
        )
        contra_rows[2]["payload"]["metadata"]["order_status"] = "skipped"
        contra_rows[2]["payload"]["metrics"] = {
            "fill_quantity": fill_qty,
            "submitted_to_broker": submitted,
        }
        contra_res = projector.project_records(
            contra_rows, mode="live", source_high_watermark=contra_seq
        )
        assert contra_res.accepted == 3
        contra_mutation = store.mutations[-1]
        contra_journey = contra_mutation.journeys[0]
        assert "risk_evaluation" not in contra_journey.stage_coverage
        contra_sub_stage = next(
            s.contract_fields
            for s in contra_mutation.stages
            if s.stage_name == "order_submission"
        )
        assert contra_sub_stage["quantity"] == fill_qty
        assert contra_sub_stage["submitted_to_broker"] is submitted


@pytest.mark.parametrize(
    "extra",
    [
        {"broker_order_id": "broker-real"},
        {"broker_trade_id": "trade-real"},
        {"ledger_entry_id": "ledger-real"},
        {"reconciliation_id": "recon-real"},
        {"fill_id": "fill-real"},
        {"filled_quantity": "invalid"},
        {"fill_quantity": "invalid"},
        {"quantity": "invalid"},
    ],
)
@pytest.mark.parametrize("location", ["metadata", "metrics"])
@pytest.mark.parametrize("terminal_kind", ["no_trade", "sell_without_holdings"])
def test_projector_preserves_contradictory_nonfill_identifiers_and_quantities(
    extra: dict[str, object],
    location: str,
    terminal_kind: str,
) -> None:
    from services.control_plane.bff import trade_journeys as tj

    rows = lifecycle_rows()[:3]
    for row in rows:
        for key in ("client_order_id", "order_id", "reconciliation_id"):
            row["payload"]["metadata"].pop(key, None)
    if terminal_kind == "no_trade":
        rows[1]["payload"]["metadata"]["decision_status"] = "no_order"
        rows[2]["event_type"] = rows[2]["payload"]["event_type"] = "paper_order_simulated"
        rows[2]["payload"]["metadata"].update(
            order_status="not_submitted",
            decision_status="no_order",
            noop_reason="hold_signal",
        )
    else:
        rows[2]["event_type"] = rows[2]["payload"]["event_type"] = "order_rejection"
        rows[2]["payload"]["metadata"].update(
            reason="sell_without_holdings",
            reject_reason="sell_without_holdings",
            broker_submission_status="sell_without_holdings",
            submitted_to_broker=False,
        )
    rows[2]["payload"]["metrics"] = {"fill_quantity": 0, "submitted_to_broker": False}
    rows[2]["payload"][location].update(extra)

    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store,
        deployment_sha="independent-review",
        clock=lambda: "2026-07-15T00:00:10Z",
    )
    result = projector.project_records(rows, mode="live", source_high_watermark=3)
    assert result.accepted == 3
    mutation = store.mutations[-1]
    assert "risk_evaluation" not in mutation.journeys[0].stage_coverage
    projected_events = [s.contract_fields for s in mutation.stages]
    projected_submission = next(
        e for e in projected_events if e["stage"] == "order_submission"
    )
    for field_name, expected_val in extra.items():
        assert projected_submission.get(field_name) == expected_val
    materializer = JourneyMaterializer()
    materializer.rebuild(projected_events)
    proj = materializer.get("tj-paper-001", tenant_id="tenant-a", environment="paper")
    assert proj is not None
    assert "risk_evaluation" not in proj.snapshot["stages"]
    assert "risk_evaluation" in proj.snapshot["completeness"]["missing_stages"]
    read_state = tj._read_state(proj.snapshot, proj.diagnostics, proj.timeline)
    assert read_state == "partial"


@pytest.mark.parametrize("terminal_kind", ["sell_without_holdings", "no_trade"])
@pytest.mark.parametrize("upstream_index", [0, 1])
@pytest.mark.parametrize("location", ["top", "metadata", "metrics"])
@pytest.mark.parametrize("qty_field", ["fill_quantity", "filled_quantity"])
@pytest.mark.parametrize("with_generic_qty", [False, True])
def test_projector_preserves_upstream_nonzero_fill_quantities_and_keeps_bff_partial(
    terminal_kind: str,
    upstream_index: int,
    location: str,
    qty_field: str,
    with_generic_qty: bool,
) -> None:
    from services.control_plane.bff import trade_journeys as tj

    rows = lifecycle_rows()[:3]
    for row in rows:
        for key in ("client_order_id", "order_id", "reconciliation_id"):
            row["payload"]["metadata"].pop(key, None)
    payload = rows[2]["payload"]
    if terminal_kind == "sell_without_holdings":
        rows[2]["event_type"] = payload["event_type"] = "order_rejection"
        payload["metadata"].update(
            reason="sell_without_holdings",
            submitted_to_broker=False,
        )
    else:
        rows[1]["payload"]["metadata"]["decision_status"] = "no_order"
        rows[2]["event_type"] = payload["event_type"] = "paper_order_simulated"
        payload["metadata"].update(
            order_status="not_submitted",
            decision_status="no_order",
            noop_reason="hold_signal",
        )
    payload["metrics"] = {"fill_quantity": 0, "submitted_to_broker": False}

    upstream = rows[upstream_index]["payload"]
    if with_generic_qty:
        upstream["metadata"]["quantity"] = 10
        upstream["metadata"]["requested_quantity"] = 10
    container = upstream if location == "top" else upstream[location]
    container[qty_field] = 5

    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store,
        deployment_sha="independent-review",
        clock=lambda: "2026-07-15T00:00:10Z",
    )
    result = projector.project_records(rows, mode="live", source_high_watermark=3)
    assert result.accepted == 3
    mutation = store.mutations[-1]
    assert "risk_evaluation" not in mutation.journeys[0].stage_coverage

    events = [s.contract_fields for s in mutation.stages]
    upstream_event = events[upstream_index]
    assert upstream_event.get(qty_field) == 5
    if with_generic_qty:
        assert upstream_event.get("quantity") == 10
        assert upstream_event.get("requested_quantity") == 10
    else:
        assert upstream_event.get("quantity") is None

    materializer = JourneyMaterializer()
    materializer.rebuild(events)
    projection = materializer.get(
        "tj-paper-001", tenant_id="tenant-a", environment="paper"
    )
    assert projection is not None
    assert "risk_evaluation" not in projection.snapshot["stages"]
    assert "risk_evaluation" in projection.snapshot["completeness"]["missing_stages"]
    assert (
        tj._read_state(projection.snapshot, projection.diagnostics, projection.timeline)
        == "partial"
    )

    persisted_snapshot = {
        "status": mutation.journeys[0].status,
        "stages": dict(mutation.journeys[0].stage_coverage),
        "identifiers": projection.snapshot["identifiers"],
        "completeness": {
            "complete": False,
            "missing_stages": ["risk_evaluation"],
        },
    }
    persisted_diagnostics = list(
        mutation.journeys[0].diagnostic_summary.get("diagnostics") or []
    )
    eff_snap, _ = tj._effective_snapshot_and_diagnostics(
        persisted_snapshot, persisted_diagnostics, events
    )
    assert "risk_evaluation" not in eff_snap["stages"]
    assert "risk_evaluation" in eff_snap["completeness"]["missing_stages"]
    assert tj._read_state(persisted_snapshot, persisted_diagnostics, events) == "partial"


def test_projector_retains_valid_requested_quantities_and_partial_fills() -> None:
    from services.control_plane.bff import trade_journeys as tj

    for idx, terminal_kind in enumerate(["sell_without_holdings", "no_trade"], start=1):
        rows = _retry_rows(
            lifecycle_rows(),
            f"tj-valid-req-{idx}",
            first_seq=idx * 10,
            count=3,
        )
        for row in rows:
            row["payload"]["run_id"] = f"run-valid-req-{idx}"
            row["payload"]["signal_id"] = f"sig-valid-req-{idx}"
            for key in ("client_order_id", "order_id", "reconciliation_id"):
                row["payload"]["metadata"].pop(key, None)
            row["payload"]["metadata"].update(
                run_id=f"run-valid-req-{idx}",
                signal_id=f"sig-valid-req-{idx}",
                decision_id=f"dec-valid-req-{idx}",
                quantity=10,
                requested_quantity=10,
            )
        payload = rows[2]["payload"]
        if terminal_kind == "sell_without_holdings":
            rows[2]["event_type"] = payload["event_type"] = "order_rejection"
            payload["metadata"].update(
                reason="sell_without_holdings",
                submitted_to_broker=False,
                quantity=0.0,
                requested_quantity=10,
                holding_quantity=0.0,
            )
            expected_risk = "rejected"
        else:
            rows[1]["payload"]["metadata"]["decision_status"] = "no_order"
            rows[2]["event_type"] = payload["event_type"] = "paper_order_simulated"
            payload["metadata"].update(
                order_status="not_submitted",
                decision_status="no_order",
                noop_reason="hold_signal",
                submitted_to_broker=False,
                quantity=0.0,
                requested_quantity=10,
            )
            expected_risk = "skipped"
        payload["metrics"] = {"fill_quantity": 0.0, "submitted_to_broker": False}

        store = _RecordingRelationalStore()
        projector = RelationalLifecycleProjector(
            store,
            deployment_sha="independent-review",
            clock=lambda: "2026-07-15T00:00:10Z",
        )
        result = projector.project_records(
            rows, mode="live", source_high_watermark=idx * 10 + 2
        )
        assert result.accepted == 3
        mutation = store.mutations[-1]
        assert mutation.journeys[0].stage_coverage["risk_evaluation"]["status"] == expected_risk
        events = [s.contract_fields for s in mutation.stages]
        assert events[0]["quantity"] == 10
        assert events[0]["requested_quantity"] == 10
        assert events[1]["quantity"] == 10
        assert events[1]["requested_quantity"] == 10
        assert events[2]["quantity"] == 0.0
        assert events[2]["requested_quantity"] == 10

        materializer = JourneyMaterializer()
        materializer.rebuild(events)
        proj = materializer.get(
            f"tj-valid-req-{idx}", tenant_id="tenant-a", environment="paper"
        )
        assert proj is not None
        assert proj.snapshot["stages"]["risk_evaluation"]["status"] == expected_risk
        assert tj._read_state(proj.snapshot, proj.diagnostics, proj.timeline) == "formal"

    partial_rows = _retry_rows(
        lifecycle_rows(), "tj-partial-fill-001", first_seq=100, count=6
    )
    partial_rows[5]["event_type"] = partial_rows[5]["payload"]["event_type"] = (
        "order_partially_filled"
    )
    partial_rows[5]["payload"]["metadata"].update(
        quantity=10,
        requested_quantity=10,
        filled_quantity=4,
    )
    partial_rows[5]["payload"]["metrics"] = {"fill_quantity": 4, "fill_price": 101.5}
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store,
        deployment_sha="independent-review",
        clock=lambda: "2026-07-15T00:00:10Z",
    )
    res = projector.project_records(
        partial_rows, mode="live", source_high_watermark=105
    )
    assert res.accepted == 6
    mutation = store.mutations[-1]
    assert mutation.journeys[0].status == "partially_filled"
    fill_stage = next(
        s.contract_fields for s in mutation.stages if s.stage_name == "fill_management"
    )
    assert fill_stage["quantity"] == 10
    assert fill_stage["requested_quantity"] == 10
    assert fill_stage["fill_quantity"] == 4
    assert fill_stage["filled_quantity"] == 4


def test_live_worker_skips_non_lifecycle_receipts_and_reaches_high_watermark_in_one_poll_across_holes(
    relational_postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC 1 & AC 2: In live worker, non-lifecycle rows produce no receipt row and checkpoint reaches high watermark with backlog 0 within one poll across skipped-type and heartbeat-retention holes."""
    import psycopg  # type: ignore[import]
    from services.telemetry.ingest_svc import build_postgres_write_fn

    source_schema = f"test_src_lconly_{uuid.uuid4().hex[:8]}"
    proj_schema = f"test_proj_lconly_{uuid.uuid4().hex[:8]}"
    table = f"{source_schema}.telemetry_events"

    with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{source_schema}"')
        cur.execute(f'CREATE SEQUENCE "{source_schema}".telemetry_events_seq')
        cur.execute(
            f'''CREATE TABLE "{source_schema}".telemetry_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                ingested_seq BIGINT NOT NULL DEFAULT nextval('"{source_schema}".telemetry_events_seq'),
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            )'''
        )

    store = ProjectionStore(relational_postgres_dsn, schema=proj_schema, bootstrap=True)
    projector = RelationalLifecycleProjector(
        store,
        deployment_sha="lifecycle-only-sha",
        controller_id="canonical-lifecycle-projector",
    )

    try:
        writer = build_postgres_write_fn(relational_postgres_dsn, table=table)
        lc_rows = lifecycle_rows()

        def _non_lifecycle_event(seq_idx: int, event_type: str) -> dict:
            eid = f"evt-non-lc-{event_type}-{seq_idx}"
            return {
                "event_id": eid,
                "event_type": event_type,
                "created_at": NOW,
                "runtime_id": "rt-49c1c3d1",
                "metadata": {"tenant_id": "tenant-a", "runtime_id": "rt-49c1c3d1"},
            }

        # Seed initial lifecycle event at seq=1 so projector starts in live mode at checkpoint=1
        assert asyncio.run(writer([lc_rows[0]["payload"]])).success
        source = PostgresLifecycleSource(relational_postgres_dsn, table=table)
        assert source.include_non_lifecycle is False

        async def _initial_poll() -> None:
            high = await source.high_watermark()
            rows = await source.fetch_after(projector.checkpoint, limit=500)
            projector.project_records(
                rows,
                mode="live",
                source_high_watermark= max(high, source.last_frontier_seq),
                source_frontier_seq=source.last_frontier_seq,
            )

        asyncio.run(_initial_poll())
        assert projector.checkpoint == 1
        assert projector.controller["mode"] == "live"
        assert projector.controller["status"] == "ready"
        assert projector.controller["backlog"] == 0

        # Now insert mixed stream after seq=1:
        # seq 2, 3: heartbeats (to be deleted by heartbeat retention -> sequence holes)
        # seq 4: infrastructure_health (skipped non-lifecycle type)
        # seq 5: lifecycle trade_decision (applied)
        # seq 6: malformed lifecycle signal_generation (quarantined)
        # seq 7, 8: superseded-retry lifecycle events (ignored, but lifecycle type!)
        # seq 9, 10, 11: trailing heartbeats & infrastructure_health (skipped non-lifecycle types at tail)
        retry_pair = _retry_rows(lc_rows, "tj-paper-superseded-retry", first_seq=7, count=2)
        malformed_lc = {
            "event_id": "evt-malformed-lc-6",
            "event_type": "signal_generation",
            "created_at": NOW,
        }
        stream = [
            _non_lifecycle_event(2, "heartbeat"),
            _non_lifecycle_event(3, "heartbeat"),
            _non_lifecycle_event(4, "infrastructure_health"),
            lc_rows[1]["payload"],
            malformed_lc,
            retry_pair[0]["payload"],
            retry_pair[1]["payload"],
            _non_lifecycle_event(9, "heartbeat"),
            _non_lifecycle_event(10, "infrastructure_health"),
            _non_lifecycle_event(11, "heartbeat"),
        ]
        # Run the live worker:
        # Tick 1: startup at checkpoint=1, recovery_target=1 -> live ready at checkpoint=1.
        # During wait() after Tick 1: stream (seq 2..11) is ingested and heartbeats 2, 3 are deleted by retention.
        # Tick 2 (ONE live poll): worker polls across retention holes (2, 3), skipped type (4),
        # applied lifecycle (5), superseded-retry lifecycle (7, 8), and trailing skipped types (9, 10, 11),
        # reaching checkpoint=11, source_high_watermark=11, backlog=0 in ONE poll!
        real_source_cls = PostgresLifecycleSource
        constructed_sources: list[PostgresLifecycleSource] = []

        def _insert_stream_with_holes() -> None:
            assert asyncio.run(writer(stream)).success
            with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM \"{source_schema}\".telemetry_events WHERE ingested_seq IN (2, 3) OR event_id = 'evt-malformed-lc-6'"
                )
                conn.commit()

        class _LiveStreamSource(real_source_cls):
            def __init__(self, dsn: str, **kwargs):
                super().__init__(dsn, table=table, **kwargs)
                self._waited = False

            async def wait(self, timeout: float) -> None:
                if not self._waited:
                    self._waited = True
                    await asyncio.to_thread(_insert_stream_with_holes)

        def _build_scoped_source(dsn: str, **kwargs):
            s = _LiveStreamSource(dsn, **kwargs)
            constructed_sources.append(s)
            return s

        monkeypatch.setattr(
            lifecycle_projector_module,
            "_configured_relational_projector",
            lambda: projector,
        )
        monkeypatch.setattr(
            lifecycle_projector_module,
            "PostgresLifecycleSource",
            _build_scoped_source,
        )
        monkeypatch.setenv("TELEMETRY_DB_DSN", relational_postgres_dsn)
        monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "2")
        monkeypatch.setenv("LIFECYCLE_PROJECTOR_RECLASSIFY_ENABLED", "0")
        monkeypatch.setenv("LIFECYCLE_PROJECTOR_RECEIPT_PRUNE_ENABLED", "0")

        assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
        assert len(constructed_sources) == 1
        assert constructed_sources[0].include_non_lifecycle is False

        # Within ONE poll after the events & holes exist: checkpoint reaches the table high watermark (11) with backlog 0!
        ctrl = projector.controller
        assert ctrl["checkpoint"] == 11
        assert ctrl["source_high_watermark"] == 11
        assert ctrl["backlog"] == 0
        assert ctrl["mode"] == "live"
        assert ctrl["status"] == "ready"
        assert ctrl["accepted_live"] is True

        # Now insert malformed lifecycle event (seq 12) + trailing heartbeat (seq 13) to verify quarantined receipt retention
        assert asyncio.run(
            writer([malformed_lc, _non_lifecycle_event(13, "heartbeat")])
        ).success

        async def _second_poll() -> None:
            high = await source.high_watermark()
            rows = await source.fetch_after(projector.checkpoint, limit=500)
            projector.project_records(
                rows,
                mode="live",
                source_high_watermark=max(high, source.last_frontier_seq),
                source_frontier_seq=source.last_frontier_seq,
            )

        asyncio.run(_second_poll())
        assert projector.checkpoint == 13
        assert projector.controller["backlog"] == 0

        # Inspect event_receipts: ZERO non-lifecycle receipts were written;
        # all lifecycle receipts (applied seq 1, 5; superseded-retry ignored seq 7, 8; quarantined seq 12) ARE present!
        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT ingested_seq, event_id, source_event_type, disposition FROM {proj_schema}.event_receipts ORDER BY ingested_seq ASC"
            )
            receipt_rows = cur.fetchall()

        assert [(r[0], r[2], r[3]) for r in receipt_rows] == [
            (1, "signal_generation", "applied"),
            (5, "trade_decision", "applied"),
            (7, "signal_generation", "ignored"),
            (8, "trade_decision", "ignored"),
            (12, "signal_generation", "quarantined"),
        ]
        assert all(r[2] in LIFECYCLE_EVENT_TYPES for r in receipt_rows)

        # AC 2: Running receipt prune keeps all 5 lifecycle receipts permanently,
        # and replay/backfill stays idempotent while fingerprint conflicts still fail!
        prune_res = store.prune_non_lifecycle_receipts(batch_size=10, max_batches=5)
        assert prune_res.rows_deleted == 0

        replay_res = projector.project_records(
            [lc_rows[0], lc_rows[1], retry_pair[0], retry_pair[1]],
            mode="replay",
            source_high_watermark=13,
            source_frontier_seq=13,
        )
        assert replay_res.accepted == 0
        assert replay_res.duplicates == 4

        tampered = json.loads(json.dumps(retry_pair[0]))
        tampered["payload"]["metrics"] = {"action": "tampered-retry"}
        with pytest.raises(ConflictingLifecycleEvent):
            projector.project_records([tampered], mode="replay", source_high_watermark=13)
    finally:
        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{source_schema}" CASCADE')
            cur.execute(f"DROP SCHEMA IF EXISTS {proj_schema} CASCADE")


def test_callers_without_frontier_keep_ignored_receipts_and_capacity_harness_supplies_frontier(
    relational_postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC 3: Backfill and replay CLI (no frontier) keep writing ignored receipts; capacity harness supplies a frontier."""
    import psycopg  # type: ignore[import]
    from services.trade_journey.lifecycle_projector_capacity import run_capacity_benchmark

    schema_cli = f"test_cli_no_frontier_{uuid.uuid4().hex[:8]}"
    schema_cap = f"test_cap_frontier_{uuid.uuid4().hex[:8]}"

    try:
        # Part A: Backfill & replay CLI pass no proven frontier -> keep writing ignored receipts so checkpoint advances.
        store_cli = ProjectionStore(relational_postgres_dsn, schema=schema_cli, bootstrap=True)
        projector_cli = RelationalLifecycleProjector(
            store_cli, deployment_sha="cli-test", controller_id="ctrl-cli"
        )
        monkeypatch.setattr(
            lifecycle_projector_module,
            "_configured_relational_projector",
            lambda: projector_cli,
        )

        lc_rows = lifecycle_rows()[:2]
        ignored_row = {
            "ingested_seq": 3,
            "ingested_at": NOW,
            "event_id": "cli-ignored-hb-3",
            "event_type": "heartbeat",
            "created_at": NOW,
            "payload": {
                "event_id": "cli-ignored-hb-3",
                "event_type": "heartbeat",
                "created_at": NOW,
            },
        }
        input_file = tmp_path / "backfill_input.json"
        input_file.write_text(
            json.dumps({"records": [*lc_rows, ignored_row]}), encoding="utf-8"
        )

        rc_backfill = lifecycle_projector_module.main(
            ["backfill", "--input", str(input_file)]
        )
        assert rc_backfill == 0
        out_backfill = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out_backfill["checkpoint"] == 3
        assert out_backfill["accepted"] == 2
        assert out_backfill["ignored"] == 1
        assert store_cli.get_receipt("cli-ignored-hb-3") is not None
        assert store_cli.get_receipt("cli-ignored-hb-3").disposition == "ignored"

        rc_replay = lifecycle_projector_module.main(
            ["replay", "--input", str(input_file)]
        )
        assert rc_replay == 0
        out_replay = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out_replay["checkpoint"] == 3
        assert out_replay["duplicates"] == 3

        # Part B: Capacity harness supplies source_frontier_seq=high_watermark
        store_cap = ProjectionStore(relational_postgres_dsn, schema=schema_cap, bootstrap=True)
        projector_cap = RelationalLifecycleProjector(
            store_cap, deployment_sha="cap-test", controller_id="ctrl-cap"
        )
        report = run_capacity_benchmark(
            projector_cap,
            total_events=20,
            total_loop_runs=4,
            batch_size=10,
            starting_seq=101,
        )
        # Even starting at seq 101 (after a 1..100 sequence hole), capacity harness supplies frontier and advances checkpoint to 120!
        assert projector_cap.checkpoint == 120
        assert len(report.samples) == 2
        assert report.samples[-1].checkpoint == 120
    finally:
        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_cli} CASCADE")
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_cap} CASCADE")


def test_hosted_shape_growth_bound_and_rollback_with_previous_worker_mode(
    relational_postgres_dsn: str,
) -> None:
    """AC 6 & AC 7: Hosted-shape growth bound (at most lifecycle events/day, 0 for heartbeats/infra_health) and rollback with include_non_lifecycle=True against pruned table and dropped index."""
    import psycopg  # type: ignore[import]
    from services.telemetry.ingest_svc import build_postgres_write_fn

    source_schema = f"test_src_hosted_{uuid.uuid4().hex[:8]}"
    proj_schema = f"test_proj_hosted_{uuid.uuid4().hex[:8]}"
    table = f"{source_schema}.telemetry_events"

    with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{source_schema}"')
        cur.execute(
            f'CREATE SEQUENCE "{source_schema}".telemetry_events_seq START WITH 13700001'
        )
        cur.execute(
            f'''CREATE TABLE "{source_schema}".telemetry_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                ingested_seq BIGINT NOT NULL DEFAULT nextval('"{source_schema}".telemetry_events_seq'),
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            )'''
        )

    store = ProjectionStore(relational_postgres_dsn, schema=proj_schema, bootstrap=True)

    try:
        # 1. Seed existing historical receipts up to 13,700,000 (hosted baseline watermark)
        # including historical applied lifecycle receipts and historical ignored heartbeat/infra_health receipts,
        # then prune historical ignored non-lifecycle receipts.
        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {proj_schema}.controller (
                    controller_id, tenant_scope, environment_scope, checkpoint_seq,
                    source_high_watermark, backlog_count, projection_revision,
                    deployment_sha, mode, status, accepted_live
                ) VALUES (
                    'canonical-lifecycle-projector', '*', '*', 13700000,
                    13700000, 0, 10, 'hosted-baseline-sha', 'live', 'ready', TRUE
                );
                INSERT INTO {proj_schema}.event_receipts (
                    event_id, ingested_seq, fingerprint, tenant_id, environment,
                    journey_id, loop_run_id, source_event_type, created_at, disposition, projection_revision
                )
                SELECT
                    'hist-ig-' || s,
                    s,
                    repeat('b', 64),
                    '',
                    '',
                    '',
                    '',
                    CASE WHEN s % 4 = 0 THEN 'infrastructure_health' ELSE 'heartbeat' END,
                    clock_timestamp(),
                    'ignored',
                    1
                FROM generate_series(13699501, 13700000) AS s;
                """
            )
            conn.commit()

        # Prune the 500 historical ignored receipts
        pruned = store.prune_non_lifecycle_receipts(batch_size=200, max_batches=5)
        assert pruned.safe_ingested_seq == 13700000
        assert pruned.rows_deleted == 500

        # 2. Simulate hosted 2026-11-16 daily telemetry shape:
        # 8 lifecycle events interspersed with 400 non-lifecycle events (heartbeats & infrastructure_health)
        writer = build_postgres_write_fn(relational_postgres_dsn, table=table)
        lc_rows = lifecycle_rows()  # 8 lifecycle events for one full journey
        daily_events: list[dict] = []
        for idx, lc_row in enumerate(lc_rows):
            for hb_idx in range(40):
                daily_events.append(
                    {
                        "event_id": f"hosted-day-hb-{idx}-{hb_idx}",
                        "event_type": "heartbeat",
                        "created_at": NOW,
                        "runtime_id": "rt-49c1c3d1",
                        "metadata": {"tenant_id": "tenant-a"},
                    }
                )
            for inf_idx in range(10):
                daily_events.append(
                    {
                        "event_id": f"hosted-day-infra-{idx}-{inf_idx}",
                        "event_type": "infrastructure_health",
                        "created_at": NOW,
                        "runtime_id": "rt-49c1c3d1",
                        "metadata": {"tenant_id": "tenant-a"},
                    }
                )
            daily_events.append(lc_row["payload"])
        # Plus trailing non-lifecycle events at the end of the day
        for tail_idx in range(20):
            daily_events.append(
                {
                    "event_id": f"hosted-day-tail-hb-{tail_idx}",
                    "event_type": "heartbeat",
                    "created_at": NOW,
                    "runtime_id": "rt-49c1c3d1",
                    "metadata": {"tenant_id": "tenant-a"},
                }
            )
        assert asyncio.run(writer(daily_events)).success

        # Project with the new live worker mode (include_non_lifecycle=False)
        projector = RelationalLifecycleProjector(
            store,
            deployment_sha="new-release-sha",
            controller_id="canonical-lifecycle-projector",
        )
        new_source = PostgresLifecycleSource(
            relational_postgres_dsn, table=table, include_non_lifecycle=False
        )

        async def _poll_new_worker() -> None:
            high = await new_source.high_watermark()
            rows = await new_source.fetch_after(projector.checkpoint, limit=500)
            projector.project_records(
                rows,
                mode="live",
                source_high_watermark=max(high, new_source.last_frontier_seq),
                source_frontier_seq=new_source.last_frontier_seq,
            )

        asyncio.run(_poll_new_worker())
        expected_watermark = 13700000 + len(daily_events)  # 13,700,428
        assert projector.checkpoint == expected_watermark
        assert projector.controller["backlog"] == 0
        assert projector.controller["status"] == "ready"
        assert projector.controller["accepted_live"] is True

        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {proj_schema}.event_receipts")
            receipts_written_for_day = int(cur.fetchone()[0])
            # Also verify redundant index is dropped
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = 'event_receipts'",
                (proj_schema,),
            )
            index_names = {r[0] for r in cur.fetchall()}

        # AC 6: Only 8 receipts written for the day (matching the 8 lifecycle events), 0 for the 420 non-lifecycle events!
        assert receipts_written_for_day == 8
        assert "idx_event_receipts_ingested_seq" not in index_names
        assert "event_receipts_ingested_seq_key" in index_names

        # 3. AC 7: Rollback to the previous accepted release (include_non_lifecycle=True)
        # against the pruned table and dropped index:
        rollback_rows = _retry_rows(
            lc_rows, "tj-paper-rollback-001", first_seq=expected_watermark + 2, count=2
        )
        for r in rollback_rows:
            r["payload"]["run_id"] = "run-paper-rb-001"
            r["payload"]["signal_id"] = "sig-paper-rb-001"
            r["payload"]["metadata"].update(
                run_id="run-paper-rb-001",
                signal_id="sig-paper-rb-001",
                decision_id="dec-paper-rb-001",
                client_order_id="coid-paper-rb-001",
                order_id="oid-paper-rb-001",
                reconciliation_id="rec-paper-rb-001",
            )
        rollback_stream = [
            {
                "event_id": "rb-hb-before",
                "event_type": "heartbeat",
                "created_at": NOW,
                "runtime_id": "rt-49c1c3d1",
            },
            rollback_rows[0]["payload"],
            rollback_rows[1]["payload"],
            {
                "event_id": "rb-infra-after",
                "event_type": "infrastructure_health",
                "created_at": NOW,
                "runtime_id": "rt-49c1c3d1",
            },
        ]
        assert asyncio.run(writer(rollback_stream)).success

        prev_release_source = PostgresLifecycleSource(
            relational_postgres_dsn, table=table, include_non_lifecycle=True
        )
        prev_release_projector = RelationalLifecycleProjector(
            store,
            deployment_sha="previous-release-sha",
            controller_id="canonical-lifecycle-projector",
            persist_non_lifecycle_receipts=True,
        )

        async def _poll_prev_release_worker() -> None:
            high = await prev_release_source.high_watermark()
            rows = await prev_release_source.fetch_after(
                prev_release_projector.checkpoint, limit=500
            )
            assert len(rows) == 4
            prev_release_projector.project_records(
                rows,
                mode="live",
                source_high_watermark=max(high, prev_release_source.last_frontier_seq),
                source_frontier_seq=prev_release_source.last_frontier_seq,
            )

        asyncio.run(_poll_prev_release_worker())
        assert prev_release_projector.checkpoint == expected_watermark + 4
        assert prev_release_projector.controller["backlog"] == 0
        assert prev_release_projector.controller["status"] == "ready"
        assert prev_release_projector.controller["accepted_live"] is True
    finally:
        with psycopg.connect(relational_postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{source_schema}" CASCADE')
            cur.execute(f"DROP SCHEMA IF EXISTS {proj_schema} CASCADE")








def test_rejection_keeps_producer_reason_not_expression_code() -> None:
    from services.trade_journey.lifecycle_projector import LifecycleProjector

    source = {
        "event_id": "91d55953-b575-4fe8-8d39-6e0cade58d59",
        "event_type": "order_rejection",
        "created_at": "2026-11-19T23:46:17Z",
        "reject_reason": "insufficient_cash",
        "required": 100034.3461,
        "available": 100000,
        "limit": 100000,
        "requested_quantity": 452,
        "submitted_to_broker": False,
        "metadata": {
            "reason_code": "long_cash",
            "risk_status": "rejected",
            "risk_gate_evaluated": True,
        },
    }
    entry = {
        "event": source,
        "identity": {
            "journey_id": "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
            "tenant_id": "tenant-dev",
            "environment": "paper",
            "persona_id": "persona-6507750312870d03a241",
            "persona_capital_binding_id": "pcb-persona-paper-039ad01f8bcd2b4aa466",
            "artifact_id": "artifact-persona-pool-039ad01f8bcd2b4aa466-v2",
            "artifact_version": "1",
            "plan_id": "plan-persona-paper-039ad01f8bcd2b4aa466",
            "trace_id": "tr",
        },
        "source_mode": "live",
        "sequence_no": 3,
    }
    (event,) = LifecycleProjector._journey_events(entry)
    assert event["source_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert event["canonical_event_id"] == "91d55953-b575-4fe8-8d39-6e0cade58d59"
    assert event["reason_code"] == "insufficient_cash"
    assert event["expression_reason_code"] == "long_cash"
    assert event["submitted_to_broker"] is False
    assert event["order_status"] == "not_submitted"
    assert event["risk_status"] == "rejected"
    assert event["risk_gate_evaluated"] is True
    assert "missing_evidence" not in event
    assert event["reason_detail"] == {
        "required": 100034.3461,
        "available": 100000,
        "limit": 100000,
        "requested_quantity": 452,
    }


def test_rejection_retains_failure_and_identifies_missing_producer_evidence() -> None:
    from services.trade_journey.lifecycle_projector import LifecycleProjector

    base_identity = {
        "journey_id": "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
        "tenant_id": "tenant-dev",
        "environment": "paper",
        "persona_id": "persona-6507750312870d03a241",
        "persona_capital_binding_id": "pcb-1",
        "artifact_id": "a",
        "artifact_version": "1",
        "plan_id": "pl",
        "trace_id": "tr",
    }
    # 1. Upstream omission: only expression choice code long_cash recorded, no rejection reason
    (omitted_reason_event,) = LifecycleProjector._journey_events(
        {
            "event": {
                "event_id": "91d55953-b575-4fe8-8d39-6e0cade58d59",
                "event_type": "order_rejection",
                "created_at": "2026-11-19T23:46:17Z",
                "submitted_to_broker": False,
                "requested_quantity": 452.0,
                "metadata": {"reason_code": "long_cash"},
            },
            "identity": base_identity,
            "source_mode": "live",
            "sequence_no": 3,
        }
    )
    assert omitted_reason_event["reason_code"] == "reason_not_recorded"
    assert omitted_reason_event["expression_reason_code"] == "long_cash"
    assert omitted_reason_event["submitted_to_broker"] is False
    assert omitted_reason_event["order_status"] == "not_submitted"
    assert omitted_reason_event["missing_evidence"] == ["reject_reason"]

    # 2. Cash rejection with missing required/available figures
    (missing_cash_figures_event,) = LifecycleProjector._journey_events(
        {
            "event": {
                "event_id": "evt-missing-figures",
                "event_type": "order_rejection",
                "created_at": "2026-11-19T23:46:17Z",
                "reject_reason": "insufficient_cash",
                "submitted_to_broker": False,
                "requested_quantity": 452.0,
                "metadata": {"reason_code": "long_cash"},
            },
            "identity": base_identity,
            "source_mode": "live",
            "sequence_no": 3,
        }
    )
    assert missing_cash_figures_event["reason_code"] == "insufficient_cash"
    assert missing_cash_figures_event["expression_reason_code"] == "long_cash"
    assert missing_cash_figures_event["missing_evidence"] == ["required", "available"]

    # 3. sell_without_holdings preserves expression_reason_code and reason_detail
    (no_holdings_event,) = LifecycleProjector._journey_events(
        {
            "event": {
                "event_id": "evt-no-holdings",
                "event_type": "order_rejection",
                "created_at": "2026-11-19T23:46:17Z",
                "side": "sell",
                "reason": "sell_without_holdings",
                "reject_reason": "sell_without_holdings",
                "submitted_to_broker": False,
                "requested_quantity": 1.0,
                "holding_quantity": 0.0,
                "metadata": {"reason_code": "close_leg"},
            },
            "identity": base_identity,
            "source_mode": "live",
            "sequence_no": 3,
        }
    )
    assert no_holdings_event["reason_code"] == "sell_without_holdings"
    assert no_holdings_event["expression_reason_code"] == "close_leg"
    assert no_holdings_event["order_status"] == "not_submitted"
    assert no_holdings_event["reason_detail"] == {
        "holding_quantity": 0.0,
        "requested_quantity": 1.0,
    }
    assert "missing_evidence" not in no_holdings_event

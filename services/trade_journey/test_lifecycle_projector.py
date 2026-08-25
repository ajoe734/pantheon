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
from services.trade_journey.materializer import JourneyMaterializer
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
        self.receipt_batch_calls: list[tuple[str, ...]] = []
        self.hydration_batch_calls: list[tuple[tuple[str, str, str], ...]] = []

    def get_controller_state(self, *_args):
        return self.controller

    def get_receipt(self, event_id: str):
        return self.receipts.get(event_id)

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


@pytest.fixture
def relational_postgres_dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    return dsn


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
        async def fetchval(self, query: str, event_types: list[str]) -> int:
            calls.append(("fetchval", query, event_types))
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

    fetchval = next(call for call in calls if call[0] == "fetchval")
    fetch = next(call for call in calls if call[0] == "fetch")
    assert "event_type = ANY" in fetchval[1]
    assert "event_type = ANY" in fetch[1]
    assert "MAX(ingested_seq)" in fetchval[1]
    assert tuple(fetchval[2]) == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[2] == 7
    assert tuple(fetch[3]) == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[4] == 9


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

    def project_records(self, rows, mode="live", source_high_watermark=0):
        if rows:
            self.checkpoint = rows[-1]["ingested_seq"]
            self.controller["checkpoint"] = self.checkpoint
        self.controller["source_high_watermark"] = source_high_watermark
        self.controller["mode"] = mode

    def record_poll(self, source_high_watermark=0, backlog=0, mode="live"):
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

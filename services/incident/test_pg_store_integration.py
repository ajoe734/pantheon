from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.foundation import (
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    OutboxRecord,
    TraceContext,
)
from services.foundation.reliable_delivery import (
    ReliableInboxConcurrencyError,
    ReliableInboxStore,
    ReliableOutboxStore,
)
from services.incident.incident import (
    IncidentCase,
    IncidentConcurrencyError,
    Postmortem,
)
from services.incident.pg_store import PostgresIncidentStore


@pytest.fixture
def pg_case():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres concurrency proof")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = f"evochain_003_{uuid4().hex}"
    incident_table = f"{schema}.incident_cases"
    postmortem_table = f"{schema}.postmortems"
    try:
        yield dsn, incident_table, postmortem_table
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )


def _store(
    case,
    *,
    bootstrap: bool,
) -> PostgresIncidentStore:
    dsn, incident_table, postmortem_table = case
    return PostgresIncidentStore(
        dsn=dsn,
        incident_table=incident_table,
        postmortem_table=postmortem_table,
        bootstrap=bootstrap,
    )


def _incident() -> IncidentCase:
    return IncidentCase(
        incident_id="inc-pg-integration",
        title="Real Postgres concurrency incident",
        status="resolved",
        severity="high",
        created_at="2026-07-15T00:00:00Z",
        binding_id="binding-pg",
        deployment_stage="paper",
        deployment_plan_id="plan-pg",
        capital_pool_id="pool-pg",
        persona_capital_binding_id="pcb-pg",
        artifact_id="artifact-pg",
        artifact_version="1.0.0",
        runtime_id="runtime-pg",
        trace_id="trace-pg",
        resolved_at="2026-07-15T00:01:00Z",
    )


def _postmortem(postmortem_id: str, incident: IncidentCase) -> Postmortem:
    return Postmortem(
        postmortem_id=postmortem_id,
        title="Real Postgres concurrency postmortem",
        status="draft",
        created_at="2026-07-15T00:02:00Z",
        incident_id=incident.incident_id,
        binding_id=incident.binding_id,
        deployment_stage=incident.deployment_stage,
        deployment_plan_id=incident.deployment_plan_id,
        capital_pool_id=incident.capital_pool_id,
        persona_capital_binding_id=incident.persona_capital_binding_id,
        artifact_id=incident.artifact_id,
        artifact_version=incident.artifact_version,
        runtime_id=incident.runtime_id,
        trace_id=incident.trace_id,
        root_cause="pending",
    )


def _event() -> EventEnvelope:
    trace = TraceContext.new(
        environment=EnvironmentScope(name=EnvironmentName.PAPER),
        source_system="incident-svc",
        idempotency_key="idmp-pg-delivery",
    )
    return EventEnvelope(
        event_id="evt-pg-delivery",
        event_type="incident.resolved",
        aggregate_type="incident",
        aggregate_id="inc-pg-integration",
        sequence_no=1,
        trace=trace,
        payload={"incident_id": "inc-pg-integration", "terminal_status": "resolved"},
        idempotency_key="idmp-pg-delivery",
        producer_service="incident-svc",
    )


def test_parent_change_before_transaction_aborts_postmortem_publish(pg_case):
    incident = _incident()
    postmortem = _postmortem("pm-parent-cas", incident)
    seed = _store(pg_case, bootstrap=True)
    seed.create_incident(incident)
    seed.create_postmortem(postmortem)

    publisher = _store(pg_case, bootstrap=False)
    parent_writer = _store(pg_case, bootstrap=False)
    real_save = publisher._save
    injected = False

    def save_after_parent_commit(**kwargs):
        nonlocal injected
        if not injected and kwargs["aggregate_type"] == "postmortem":
            injected = True
            current = parent_writer.require_incident(incident.incident_id)
            parent_writer.merge_incident_evidence(
                incident.incident_id,
                replace(current, evidence_summary="concurrent parent evidence"),
            )
        return real_save(**kwargs)

    publisher._save = save_after_parent_commit  # type: ignore[method-assign]

    with pytest.raises(
        IncidentConcurrencyError,
        match="IncidentCase changed concurrently",
    ):
        publisher.update_postmortem_status(
            postmortem.postmortem_id,
            "published",
            published_event_id="evt-parent-cas",
            expected_snapshot=postmortem.to_dict(),
            expected_incident_snapshot=incident.to_dict(),
        )

    durable = _store(pg_case, bootstrap=False)
    durable_incident = durable.require_incident(incident.incident_id)
    durable_postmortem = durable.require_postmortem(postmortem.postmortem_id)
    assert durable_incident.evidence_summary == "concurrent parent evidence"
    assert durable_postmortem.to_dict() == postmortem.to_dict()


def test_two_postmortem_ids_race_to_exactly_one_commit(pg_case):
    incident = _incident()
    seed = _store(pg_case, bootstrap=True)
    seed.create_incident(incident)

    first = _store(pg_case, bootstrap=False)
    second = _store(pg_case, bootstrap=False)
    barrier = threading.Barrier(2, timeout=5)

    def gate_postmortem_save(store: PostgresIncidentStore) -> None:
        real_save = store._save

        def gated_save(**kwargs):
            if kwargs["aggregate_type"] == "postmortem":
                barrier.wait()
            return real_save(**kwargs)

        store._save = gated_save  # type: ignore[method-assign]

    gate_postmortem_save(first)
    gate_postmortem_save(second)

    def create(store: PostgresIncidentStore, postmortem_id: str):
        try:
            return "committed", store.create_postmortem(
                _postmortem(postmortem_id, incident)
            )
        except IncidentConcurrencyError as exc:
            return "conflict", exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(create, first, "pm-race-a"),
            pool.submit(create, second, "pm-race-b"),
        ]
        outcomes = [future.result(timeout=10) for future in futures]

    assert [state for state, _ in outcomes].count("committed") == 1
    assert [state for state, _ in outcomes].count("conflict") == 1
    conflict = next(value for state, value in outcomes if state == "conflict")
    assert "already exists for IncidentCase" in str(conflict)

    durable = _store(pg_case, bootstrap=False)
    postmortems = durable.list_postmortems()
    assert len(postmortems) == 1
    assert postmortems[0].postmortem_id in {"pm-race-a", "pm-race-b"}
    assert postmortems[0].incident_id == incident.incident_id


def test_two_terminal_statuses_from_one_snapshot_commit_once_and_keep_first_resolution(pg_case):
    incident = replace(_incident(), status="open", resolved_at=None)
    seed = _store(pg_case, bootstrap=True)
    seed.create_incident(incident)

    resolved_writer = _store(pg_case, bootstrap=False)
    closed_writer = _store(pg_case, bootstrap=False)
    barrier = threading.Barrier(2, timeout=5)

    def gate_incident_save(store: PostgresIncidentStore) -> None:
        real_save = store._save

        def gated_save(**kwargs):
            if kwargs["aggregate_type"] == "incident":
                barrier.wait()
            return real_save(**kwargs)

        store._save = gated_save  # type: ignore[method-assign]

    gate_incident_save(resolved_writer)
    gate_incident_save(closed_writer)

    def transition(store: PostgresIncidentStore, status: str, resolved_at: str):
        try:
            return "committed", store.update_incident_status(
                incident.incident_id,
                status,
                resolved_at=resolved_at,
                expected_snapshot=incident.to_dict(),
            )
        except IncidentConcurrencyError as exc:
            return "conflict", exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                transition,
                resolved_writer,
                "resolved",
                "2026-07-15T00:03:00Z",
            ),
            pool.submit(
                transition,
                closed_writer,
                "closed",
                "2026-07-15T00:04:00Z",
            ),
        ]
        outcomes = [future.result(timeout=10) for future in futures]

    assert [state for state, _ in outcomes].count("committed") == 1
    assert [state for state, _ in outcomes].count("conflict") == 1
    conflict = next(value for state, value in outcomes if state == "conflict")
    assert "changed concurrently" in str(conflict)

    winner = next(value for state, value in outcomes if state == "committed")
    durable = _store(pg_case, bootstrap=False).require_incident(incident.incident_id)
    assert durable.status == winner.status
    assert durable.resolved_at == winner.resolved_at

    replay_store = _store(pg_case, bootstrap=False)
    replayed = replay_store.update_incident_status(
        incident.incident_id,
        "closed",
        resolved_at="2030-01-01T00:00:00Z",
        expected_snapshot=durable.to_dict(),
    )
    assert replayed.resolved_at == winner.resolved_at


def test_postgres_outbox_claim_is_exclusive_and_stale_completion_is_fenced(pg_case):
    dsn, incident_table, _ = pg_case
    schema = incident_table.split(".", 1)[0]
    first = ReliableOutboxStore(
        backend="postgres",
        dsn=dsn,
        table_name=f"{schema}.delivery_outbox",
        json_path="/unused/outbox.json",
        owner_service="incident-svc",
    )
    second = ReliableOutboxStore(
        backend="postgres",
        dsn=dsn,
        table_name=f"{schema}.delivery_outbox",
        json_path="/unused/outbox.json",
        owner_service="incident-svc",
    )
    event = _event()
    prepared = first.prepare(
        record=OutboxRecord(
            outbox_id="outbox-pg-delivery",
            owner_service="incident-svc",
            event=event,
        ),
        transition={"aggregate_type": "incident", "aggregate_id": event.aggregate_id},
    )
    first.activate(prepared)

    barrier = threading.Barrier(2, timeout=5)
    first_compare = first.impl.compare_and_set
    second_compare = second.impl.compare_and_set

    def gated_first(*args, **kwargs):
        barrier.wait()
        return first_compare(*args, **kwargs)

    def gated_second(*args, **kwargs):
        barrier.wait()
        return second_compare(*args, **kwargs)

    first.impl.compare_and_set = gated_first  # type: ignore[method-assign]
    second.impl.compare_and_set = gated_second  # type: ignore[method-assign]
    claimed_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    first.claim_due,
                    worker_id="worker-a",
                    lease_seconds=1,
                    now=claimed_at,
                ),
                pool.submit(
                    second.claim_due,
                    worker_id="worker-b",
                    lease_seconds=1,
                    now=claimed_at,
                ),
            ]
            claims = [record for future in futures for record in future.result(timeout=10)]
    finally:
        first.impl.compare_and_set = first_compare  # type: ignore[method-assign]
        second.impl.compare_and_set = second_compare  # type: ignore[method-assign]

    assert len(claims) == 1
    stale_claim = claims[0]
    reclaimed = second.claim_due(
        worker_id="worker-c",
        lease_seconds=30,
        now=claimed_at + timedelta(seconds=2),
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].claim_token != stale_claim.claim_token

    stale_applied, canonical = first.complete_published(stale_claim)
    assert stale_applied is False
    assert canonical.claim_token == reclaimed[0].claim_token
    applied, published = second.complete_published(reclaimed[0])
    assert applied is True
    assert published.status.value == "published"


def test_postgres_inbox_reservation_fences_stale_claimant(pg_case):
    dsn, incident_table, _ = pg_case
    schema = incident_table.split(".", 1)[0]
    first = ReliableInboxStore(
        backend="postgres",
        dsn=dsn,
        table_name=f"{schema}.delivery_inbox",
        json_path="/unused/inbox.json",
        owner_service="postmortem-svc",
        consumer_name="postmortem-draft-consumer",
    )
    second = ReliableInboxStore(
        backend="postgres",
        dsn=dsn,
        table_name=f"{schema}.delivery_inbox",
        json_path="/unused/inbox.json",
        owner_service="postmortem-svc",
        consumer_name="postmortem-draft-consumer",
    )
    event = _event()
    barrier = threading.Barrier(2, timeout=5)
    first_insert = first.impl.insert_if_absent
    second_insert = second.impl.insert_if_absent

    def gated_first(*args, **kwargs):
        barrier.wait()
        return first_insert(*args, **kwargs)

    def gated_second(*args, **kwargs):
        barrier.wait()
        return second_insert(*args, **kwargs)

    first.impl.insert_if_absent = gated_first  # type: ignore[method-assign]
    second.impl.insert_if_absent = gated_second  # type: ignore[method-assign]
    reserved_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    first.reserve,
                    event,
                    result_ref="pm-pg-delivery",
                    lease_seconds=1,
                    now=reserved_at,
                ),
                pool.submit(
                    second.reserve,
                    event,
                    result_ref="pm-pg-delivery",
                    lease_seconds=1,
                    now=reserved_at,
                ),
            ]
            outcomes = [future.result(timeout=10) for future in futures]
    finally:
        first.impl.insert_if_absent = first_insert  # type: ignore[method-assign]
        second.impl.insert_if_absent = second_insert  # type: ignore[method-assign]

    assert {state for state, _ in outcomes} == {"claimed", "in_progress"}
    stale_reservation = next(payload for state, payload in outcomes if state == "claimed")
    reclaimed_state, reclaimed = first.reserve(
        event,
        result_ref="pm-pg-delivery",
        lease_seconds=30,
        now=reserved_at + timedelta(seconds=2),
    )
    assert reclaimed_state == "claimed"
    assert reclaimed["reservation_token"] != stale_reservation["reservation_token"]

    with pytest.raises(ReliableInboxConcurrencyError, match="changed concurrently"):
        second.record_applied(
            event,
            result_ref="pm-pg-delivery",
            reservation=stale_reservation,
        )
    applied = first.record_applied(
        event,
        result_ref="pm-pg-delivery",
        reservation=reclaimed,
    )
    assert applied["state"] == "applied"
    replay_state, replay = second.reserve(event, result_ref="pm-pg-delivery")
    assert replay_state == "applied"
    assert replay["result_ref"] == "pm-pg-delivery"

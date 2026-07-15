from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

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

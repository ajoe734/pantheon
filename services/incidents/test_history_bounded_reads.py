"""INCIDENTS-MEMORY-OOM-DURING-DEPLOY-CHURN-20261123.

Production-shaped proofs that the Postgres-backed incidents service holds and
reads memory proportional to the OPEN case set (or a page), not to the total
stored history.  Bounds are asserted on rows materialised per owner-table call
(allocation count / query shape), never on wall-clock time.

Volume: the hosted store could not be counted read-only from the worker
(see evidence.json), so the history volume is the previously measured hosted
shape (thousands of resolved machine-generated BFF degradation incidents with
thousands of postmortems), scaled to keep the test fast while remaining far
above the open set.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-history-bounded-token")

import services.incident.pg_store as pg_store_mod
from services.incident.incident import IncidentCase, IncidentStore
from services.incident.pg_store import PostgresIncidentStore
import services.incidents.main as incidents_main
from services.incidents.main import app
from services.incidents.pg_owner_fake import FakeOwnerTable
from services.incidents.test_infrastructure_orphan_recovery import _infra_payload

HISTORY_RESOLVED = 6000  # resolved BFF degradation incidents
HISTORY_POSTMORTEMS = 4000
OPEN_BASELINE = 12
INC_T, PM_T = "incident.incident_cases", "incident.postmortems"


def _template_row() -> Dict[str, Any]:
    scratch = IncidentStore(path=None)
    from services.incidents.consumer import InfrastructureHealthIncidentConsumer

    consumer = InfrastructureHealthIncidentConsumer(incident_store=scratch)
    consumer.consume(
        _infra_payload(incident_id="infra-bff-template", service_name="template", source_event_id="evt-t")
    )
    return scratch.require_incident("infra-bff-template").to_dict()


@pytest.fixture()
def pg_service(monkeypatch):
    template = _template_row()
    rows: List[Dict[str, Any]] = []
    for i in range(HISTORY_RESOLVED):
        row = dict(template)
        row.update(
            incident_id=f"infra-bff-hist{i:08d}",
            status="resolved",
            resolved_at="2026-11-01T00:00:00Z",
            created_at=f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:00:00Z",
        )
        rows.append(row)
    for i in range(OPEN_BASELINE):
        row = dict(template)
        row.update(
            incident_id=f"infra-bff-open{i:08d}",
            status="open",
            created_at="2026-11-23T17:00:00Z",
            artifact_id=f"infra-component-open{i}",
            incident_cluster_id=f"infrastructure:http_service:default:open{i}",
        )
        rows.append(row)
    pms = [
        {"postmortem_id": f"pm-hist{i:08d}", "incident_id": f"infra-bff-hist{i:08d}"}
        for i in range(HISTORY_POSTMORTEMS)
    ]
    FakeOwnerTable.reset({INC_T: rows, PM_T: pms}, {INC_T: "incident_id", PM_T: "postmortem_id"})
    monkeypatch.setattr(pg_store_mod, "PostgresJsonOwnerStore", FakeOwnerTable)
    store = PostgresIncidentStore(dsn="postgresql://fake/fake")

    def _fake_save(*, aggregate_type, record_id, expected_snapshot, consistency_checks=()):
        table = FakeOwnerTable.tables[INC_T if aggregate_type == "incident" else PM_T]
        payload = (
            store._incidents[record_id] if aggregate_type == "incident" else store._postmortems[record_id]
        ).to_dict()
        for idx, (rid, _) in enumerate(table):
            if rid == record_id:
                table[idx] = (rid, payload)
                break
        else:
            table.append((record_id, payload))

    store._save = _fake_save  # type: ignore[assignment]

    class _AcceptAll:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr(incidents_main, "reference_validator", _AcceptAll())
    monkeypatch.setattr(incidents_main, "store", store)
    return store, FakeOwnerTable.stats


def _reset(stats):
    for k in stats:
        stats[k] = 0


def test_startup_loads_only_open_cases_not_history(pg_service):
    store, stats = pg_service
    # Construction already ran; history is ~500x the open set.
    assert stats["list_all_calls"] == 0
    assert stats["rows_returned"] == OPEN_BASELINE
    assert len(store._incidents) == OPEN_BASELINE
    assert store._postmortems == {}


def test_open_only_read_materialises_only_open_cases(pg_service):
    store, stats = pg_service
    client = TestClient(app)
    _reset(stats)
    r = client.get("/api/incidents?open_only=true")
    assert r.status_code == 200
    assert len(r.json()) == OPEN_BASELINE
    assert stats["list_all_calls"] == 0
    assert stats["max_rows_in_call"] <= OPEN_BASELINE
    # a few open-set scans per request, never anything near the history size
    assert stats["rows_returned"] <= 4 * OPEN_BASELINE


def test_unfiltered_list_and_health_count_are_page_bounded(pg_service):
    store, stats = pg_service
    client = TestClient(app)
    _reset(stats)
    r = client.get("/api/incidents")
    assert r.status_code == 200
    assert len(r.json()) == incidents_main.LIST_INCIDENTS_DEFAULT_LIMIT
    assert stats["list_all_calls"] == 0
    assert stats["max_rows_in_call"] <= incidents_main.LIST_INCIDENTS_DEFAULT_LIMIT

    r = client.get("/api/incidents?limit=5")
    assert len(r.json()) == 5

    _reset(stats)
    assert store.count_incidents() == HISTORY_RESOLVED + OPEN_BASELINE
    assert stats["rows_returned"] == 0  # a COUNT, no payload rows


def test_degradation_burst_stays_bounded_by_open_set(pg_service):
    store, stats = pg_service
    client = TestClient(app)
    burst = 40
    _reset(stats)
    peak_cache = 0
    for i in range(burst):
        payload = _infra_payload(
            incident_id=f"infra-bff-burst{i:08d}",
            service_name=f"burstsvc{i}",
            source_event_id=f"evt-burst-{i}",
            created_at="2026-11-23T17:57:00Z",
        )
        r = client.post("/api/incidents/consume-infrastructure-health", json=payload)
        assert r.status_code == 201, r.text
        peak_cache = max(peak_cache, len(store._incidents))
    # Recovery events resolve a few of them (the ~20/min resolve churn).
    for i in range(10):
        payload = _infra_payload(
            incident_id=f"infra-bff-burst{i:08d}",
            service_name=f"burstsvc{i}",
            source_event_id=f"evt-recover-{i}",
            created_at="2026-11-23T18:07:00Z",
            status="resolved",
        )
        payload["probe_kind"] = "recovery"
        r = client.post("/api/incidents/consume-infrastructure-health", json=payload)
        assert r.status_code in (200, 201), r.text
        peak_cache = max(peak_cache, len(store._incidents))

    assert stats["list_all_calls"] == 0
    open_ceiling = OPEN_BASELINE + burst
    # No owner-table call ever returned more than the open set (+ one keyed row).
    assert stats["max_rows_in_call"] <= open_ceiling + 1
    assert stats["max_rows_in_call"] < HISTORY_RESOLVED // 10
    # The resident cache is the open set, not the 6000-row history.
    assert peak_cache <= open_ceiling + 1


def test_postmortem_lookup_is_keyed_not_scanned(pg_service):
    store, stats = pg_service
    _reset(stats)
    assert store.find_postmortem_for_incident("infra-bff-nope") is None
    assert stats["list_all_calls"] == 0
    # open-set refresh (12 rows) + one keyed postmortem probe (0 rows); never 4000
    assert stats["max_rows_in_call"] <= OPEN_BASELINE

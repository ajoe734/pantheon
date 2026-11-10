from __future__ import annotations

import json

import pytest

from services.control_plane.bff.personas import service as svc

REQUIRED = [{"source_id": "tw-prices", "connector_candidates": ["c1"]}]


def _binding() -> dict:
    receipt = {"payload": "x" * 103_000}
    freshness = {"latest_receipt": receipt, "status": "fresh"}
    health = {"status": "healthy", "detail": "y" * 29_000}
    return {
        "connector_id": "c1",
        "connector_freshness": freshness,
        "connectorFreshness": json.loads(json.dumps(freshness)),
        "source_health": health,
        "sourceHealth": json.loads(json.dumps(health)),
    }


def _fake_overlay(status, sources, *, required_data_sources=None, thin=False):
    bindings = [_binding(), _binding()]
    dss = dict(status)
    dss["state"] = "healthy"
    dss["required_source_health"] = json.loads(json.dumps(bindings))
    dss["requiredSourceHealth"] = json.loads(json.dumps(bindings))
    return dss, list(sources), bindings


def _raw(i: int) -> dict:
    return {
        "persona_id": f"persona-{i}",
        "tenant_id": "t1",
        "name": f"P{i}",
        "required_data_sources": REQUIRED,
        "metadata": {"data_source_status": {"state": "healthy"}, "data_sources": [{"id": "s"}]},
    }


def test_list_rows_are_compact_and_detail_dto_keeps_full_shape(monkeypatch):
    monkeypatch.setattr(svc, "_overlay_source_health_truth", _fake_overlay)
    monkeypatch.setattr(svc, "_routed_strategy_counts_by_persona", lambda: {"persona-1": 3})
    raws = [_raw(i) for i in range(142)]
    before = json.dumps(raws, sort_keys=True)

    full = svc._project_persona_dto(raws[0], overlay=None, routed_strategies=0, evaluate_provisioning=False)
    assert len(json.dumps(full)) > 500_000
    assert "sourceHealthBindings" in full
    assert "requiredSourceHealth" in full["dataSourceStatus"]

    rows = svc._project_persona_list_records(raws)
    assert len(rows) == 142
    assert len(json.dumps({"data": rows, "items": rows}).encode()) <= 1_000_000
    for row in rows:
        for key in ("sourceHealthBindings", "source_health_bindings"):
            assert key not in row
        for dkey in [k for k in ("dataSourceStatus", "data_source_status") if k in row]:
            for k in ("required_source_health", "requiredSourceHealth"):
                assert k not in row[dkey]
            assert row[dkey]["state"] == "healthy"
        assert row["dataSources"] == [{"id": "s"}]
        assert row["requiredDataSources"] == REQUIRED
    assert rows[1]["id"] == "persona-1"
    assert json.dumps(raws, sort_keys=True) == before


# --- BFF-PERSONA-LIST-THIN-HEALTH-20261109: real overlay, real row shape ---
import copy
import time

from services.control_plane.bff.tests.test_persona_health_single_owner import (
    _make_persona_service,
)

_CONNECTOR = "tw-twse-tpex-official-market"


def _real_truth() -> dict:
    # Rich blobs sized like the hosted receipts (~290 KB per persona row).
    return {
        _CONNECTOR: {
            "health": {
                "source_id": "tw-twse-tpex-official-market",
                "status": "ok",
                "last_success_at": "2026-09-14T00:00:00Z",
                "row_count_last_run": 8,
                "detail": "h" * 29_000,
            },
            "connector": {
                "status": "configured",
                "schedule": {"cron": "* * * * *"},
                "freshness": {"status": "fresh", "latest_receipt": {"payload": "r" * 103_000}},
                "health_metrics": {},
            },
            "usage_aggregate_30d": {"calls": 3},
            "recommendation": {"action": "keep"},
        }
    }


def _real_raw(i: int) -> dict:
    return {
        "persona_id": f"persona-{i}",
        "tenant_id": "t1",
        "name": f"P{i}",
        "required_data_sources": [
            {"source_id": "tw-prices", "connector_candidates": [_CONNECTOR]},
            {"source_id": "tw-none", "connector_candidates": ["missing"]},
        ],
        "metadata": {
            "data_source_status": {"state": "healthy", "provider_statuses": {"twse": "x"}},
            "data_sources": [
                {"provider_key": "twse", "status": "read_ok"},
                {"provider_key": "other", "status": "credential_unavailable", "secret_ref": "s"},
            ],
        },
    }


@pytest.fixture
def real_service(monkeypatch):
    service = _make_persona_service()
    truth = _real_truth()
    monkeypatch.setattr(service, "_source_ingest_truth_by_connector", lambda: truth)
    monkeypatch.setattr(svc, "_active_persona_service", lambda: service)
    monkeypatch.setattr(svc, "_routed_strategy_counts_by_persona", lambda: {})
    return service


def _previous_projection(raw: dict) -> dict:
    """The pre-change list projection: rich DTO then strip."""
    dto = svc._project_persona_dto(raw, overlay=None, routed_strategies=0, evaluate_provisioning=False)
    svc._strip_persona_list_source_health_detail(dto)
    return dto


_RICH_ONLY_STATUS_KEYS = ("connector_health", "connectorHealth")
# Exactly the keys _source_truth_projection(thin=False) adds beyond thin.
_RICH_ONLY_SOURCE_KEYS = (
    "connector_schedule", "connectorSchedule", "connector_freshness",
    "connectorFreshness", "source_health", "sourceHealth",
    "usage_aggregate_30d", "usageAggregate30d", "recommendation",
)


def test_list_projection_never_builds_rich_source_health(real_service, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("rich source-health computation invoked for list row")

    monkeypatch.setattr(svc, "_source_health_bindings_from_requirements", _boom)
    original = svc._source_truth_projection
    seen = []

    def _spy(connector_id, truth, *, thin=False):
        seen.append(thin)
        return original(connector_id, truth, thin=thin)

    monkeypatch.setattr(svc, "_source_truth_projection", _spy)
    rows = svc._project_persona_list_records([_real_raw(i) for i in range(5)])
    assert len(rows) == 5 and seen and all(seen)
    assert len(json.dumps(rows)) < 60_000


def test_thin_rows_match_previous_projection_field_for_field(real_service):
    raws = [_real_raw(i) for i in range(6)]
    new_rows = svc._project_persona_list_records(copy.deepcopy(raws))
    for raw, new in zip(copy.deepcopy(raws), new_rows):
        old = _previous_projection(raw)
        # Only the rich connector_health blob list is dropped (no list consumer).
        for rich in _RICH_ONLY_STATUS_KEYS:
            old["dataSourceStatus"].pop(rich, None)
        for source in old["dataSources"]:
            for rich in _RICH_ONLY_SOURCE_KEYS:
                source.pop(rich, None)
        assert json.dumps(old, sort_keys=True) == json.dumps(new, sort_keys=True)
    status = new_rows[0]["dataSourceStatus"]
    assert status["source_health_source"] == "source_ingest"
    assert status["live_ingestion_enabled"] is True
    assert status["live_source_connector_ids"] == [_CONNECTOR]
    assert new_rows[0]["dataSources"][0]["status"] == "read_ok"
    assert new_rows[0]["dataSources"][1]["status"] == "credential_unavailable"


def test_list_projection_does_not_mutate_records_or_shared_cache(real_service):
    raws = [_real_raw(i) for i in range(3)]
    before = copy.deepcopy(raws)
    first = svc._project_persona_list_records(raws)
    assert raws == before
    snapshot = json.dumps(first, sort_keys=True)
    first[0]["dataSourceStatus"]["state"] = "MUTATED"
    first[0]["dataSources"].append({"x": 1})
    first[0]["dataSourceStatus"]["provider_statuses"]["twse"] = "MUTATED"
    second = svc._project_persona_list_records(raws)
    assert raws == before
    assert json.dumps(second, sort_keys=True) == snapshot


def test_detail_dto_keeps_full_rich_source_health(real_service):
    raw = _real_raw(1)
    full = svc._project_persona_dto(raw, overlay=None, routed_strategies=0, evaluate_provisioning=False)
    status = full["dataSourceStatus"]
    for key in ("connector_health", "connectorHealth", "required_source_health", "requiredSourceHealth"):
        assert key in status
    assert len(full["sourceHealthBindings"]) == 2
    assert full["dataSources"][0]["connector_freshness"]["latest_receipt"]["payload"]
    assert len(json.dumps(full)) > 250_000


def test_list_cpu_drops_at_least_70_percent(real_service):
    raws = [_real_raw(i) for i in range(160)]
    t0 = time.process_time()
    for raw in raws:
        _previous_projection(copy.deepcopy(raw))
    old = time.process_time() - t0
    t0 = time.process_time()
    svc._project_persona_list_records(copy.deepcopy(raws))
    new = time.process_time() - t0
    assert new <= old * 0.3, (old, new)

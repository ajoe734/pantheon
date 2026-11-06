from __future__ import annotations

import json

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

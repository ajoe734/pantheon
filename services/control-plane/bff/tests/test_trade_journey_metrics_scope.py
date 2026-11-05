"""metrics() must cover the complete tenant/environment population (no silent 500-journey window)."""
from __future__ import annotations

import pytest

from services.control_plane.bff.trade_journey_projection_store import TradeJourneyProjectionStore


def _store(calls):
    store = TradeJourneyProjectionStore("postgresql://x/y", token_secret="s" * 16, connect=lambda *_: None)

    def rows(sql, params):
        calls.append((sql, tuple(params)))
        if "FROM" in sql and "GROUP BY status" in sql:
            return [{"status": "open", "count": 600}]
        if "DISTINCT ON" in sql:
            return [{"stage_name": "fill", "count": 600}]
        return [{"stage_name": "fill", "sample_count": 600, "p50_ms": 1.0, "p95_ms": 120000.0}]

    store._rows = rows
    return store


def test_metrics_queries_cover_complete_population_and_stay_scoped():
    calls = []
    out = _store(calls).metrics(tenant_id="t1", environment="dev")
    assert out["total_journeys"] == 600
    assert out["by_current_stage"] == {"fill": 600}
    assert out["stage_latency_ms"]["fill"]["sample_count"] == 600
    assert len(calls) == 3
    for sql, params in calls:
        assert "LIMIT" not in sql.upper()
        assert "ANY(" not in sql
        assert "tenant_id=%s AND environment=%s" in sql
        assert params == ("t1", "dev")


def test_metrics_requires_scope():
    with pytest.raises(ValueError):
        _store([]).metrics(tenant_id="", environment="dev")

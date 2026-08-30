from __future__ import annotations

from types import SimpleNamespace

from services.source_ingestion.runtime import _frontier_backlog_readback


def test_frontier_backlog_readback_is_exact_and_connector_scoped() -> None:
    frontier = [
        SimpleNamespace(connector_id="connector-b", status="retry"),
        SimpleNamespace(connector_id="connector-a", status="queued"),
        SimpleNamespace(connector_id="connector-a", status="running"),
        SimpleNamespace(connector_id="connector-a", status="done"),
        SimpleNamespace(connector_id="connector-c", status="failed"),
    ]

    total, by_connector = _frontier_backlog_readback(frontier)

    assert total == 3
    assert by_connector == {"connector-a": 2, "connector-b": 1}

"""BFF-ATTRIBUTION-RETIRED-RUNTIME-SCOPE-20261024.

Performance Attribution's default list drops a runtime whose owner lifecycle
(RuntimeBinding ``status``) is terminal and that reports no attribution data.
Retired runtimes with telemetry stay, active runtimes always stay, and explicit
persona/runtime drill-down keeps returning retired runtimes.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.control_plane.bff.agora.performance import service as perf
from services.control_plane.bff.pm12 import service as pm12_service

_BFF_DIR = Path(__file__).resolve().parents[1]


def _runtime(runtime_id: str, persona_id: str, status: str) -> Dict[str, Any]:
    return {
        "runtime_id": runtime_id,
        "persona_id": persona_id,
        "status": status,
        "strategy_id": f"strategy-{persona_id}",
        "capital_pool_id": f"pool-{persona_id}",
    }


def _sources(
    runtimes: List[Dict[str, Any]],
    telemetry: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    personas = [
        {"persona_id": runtime["persona_id"], "name": f"Persona {runtime['persona_id']}"}
        for runtime in runtimes
    ]
    return {
        "tenant_id": None,
        "runtime_bindings": runtimes,
        "deployment_plans": [],
        "bindings": [],
        "capital_pools": [],
        "personas": personas,
        "strategies": [],
        "plans_by_id": {},
        "bindings_by_id": {},
        "pools_by_id": {},
        "personas_by_id": {persona["persona_id"]: persona for persona in personas},
        "strategies_by_id": {},
        "telemetry_by_runtime_id": dict(telemetry or {}),
    }


def _mixed_sources() -> Dict[str, Any]:
    return _sources(
        [
            _runtime("rt-retired-empty-1", "p-retired-empty-1", "retired"),
            _runtime("rt-retired-empty-2", "p-retired-empty-2", "failed_inactive"),
            _runtime("rt-retired-history", "p-retired-history", "retired"),
            _runtime("rt-active-zero", "p-active-zero", "active"),
            _runtime("rt-active-empty", "p-active-empty", "running"),
        ],
        telemetry={
            "rt-retired-history": {"runtime_id": "rt-retired-history", "pnl": 12.5, "total_trades": 3},
            "rt-active-zero": {"runtime_id": "rt-active-zero", "pnl": 0.0, "total_trades": 0},
        },
    )


def _response(sources: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("dimensions", ["runtime"])
    kwargs.setdefault("page_size", 50)
    return perf.pm12_performance_attribution_response(
        period="latest",
        page_token=None,
        sources_fn=lambda _tenant: sources,
        utc_now=lambda: "2026-10-24T00:00:00Z",
        **kwargs,
    )


def _row_keys(payload: Dict[str, Any]) -> List[str]:
    return sorted(row["dimension_key"] for row in payload["data"]["items"])


def test_retired_runtime_without_attribution_data_is_excluded() -> None:
    payload = _response(_mixed_sources())
    keys = _row_keys(payload)
    assert "rt-retired-empty-1" not in keys
    assert "rt-retired-empty-2" not in keys


def test_retired_runtime_with_telemetry_is_kept() -> None:
    assert "rt-retired-history" in _row_keys(_response(_mixed_sources()))


def test_retired_runtime_with_positions_only_is_kept() -> None:
    sources = _sources(
        [_runtime("rt-retired-held", "p-retired-held", "retired")],
        telemetry={"rt-retired-held": {"positions": [{"symbol": "BTC/USD", "quantity": 1}]}},
    )
    assert _row_keys(_response(sources)) == ["rt-retired-held"]


def test_active_runtime_without_telemetry_is_kept() -> None:
    keys = _row_keys(_response(_mixed_sources()))
    assert "rt-active-empty" in keys
    assert "rt-active-zero" in keys


def test_total_and_aggregates_match_scoped_rows() -> None:
    payload = _response(_mixed_sources())
    assert _row_keys(payload) == ["rt-active-empty", "rt-active-zero", "rt-retired-history"]
    assert payload["data"]["summary"]["row_count"] == 3
    assert payload["data"]["summary"]["runtime_count"] == 3
    assert payload["page_info"]["total"] == 3


def test_pagination_runs_over_scoped_rows() -> None:
    sources = _mixed_sources()
    first = _response(sources, page_size=2)
    assert len(first["data"]["items"]) == 2
    assert first["page_info"]["total"] == 3
    second = perf.pm12_performance_attribution_response(
        dimensions=["runtime"],
        period="latest",
        page_token=first["page_info"]["next_page_token"],
        page_size=2,
        sources_fn=lambda _tenant: sources,
        utc_now=lambda: "2026-10-24T00:00:00Z",
    )
    assert len(second["data"]["items"]) == 1
    assert second["page_info"]["next_page_token"] is None
    seen = _row_keys(first) + _row_keys(second)
    assert sorted(seen) == ["rt-active-empty", "rt-active-zero", "rt-retired-history"]


def test_runtime_id_drill_down_returns_retired_runtime() -> None:
    payload = _response(_mixed_sources(), runtime_id="rt-retired-empty-1")
    assert _row_keys(payload) == ["rt-retired-empty-1"]
    assert payload["page_info"]["total"] == 1


def test_persona_id_drill_down_returns_retired_runtime() -> None:
    payload = _response(_mixed_sources(), dimensions=["persona"], persona_id="p-retired-empty-2")
    assert _row_keys(payload) == ["p-retired-empty-2"]


def test_facts_builder_stays_unscoped_for_other_readers() -> None:
    facts = perf.pm12_performance_attribution_facts(_mixed_sources())
    assert {fact["runtime_id"] for fact in facts} >= {"rt-retired-empty-1", "rt-retired-empty-2"}


def test_ops_read_model_still_sees_retired_runtime_facts() -> None:
    """main._ops_read_model_entry_for_persona joins the unscoped facts builder.

    The read model lives in main.py, which BFF tests must not import (see
    test_bff_test_architecture.py), so this checks it statically and then
    exercises the exact facts symbol it calls with its persona filter.
    """
    main_tree = ast.parse((_BFF_DIR / "main.py").read_text(encoding="utf-8"))
    read_model = next(
        node
        for node in ast.walk(main_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_ops_read_model_entry_for_persona"
    )
    called = {
        node.func.id
        for node in ast.walk(read_model)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_pm12_performance_attribution_facts" in called
    assert "_pm12_performance_attribution_response" not in called

    persona_facts = [
        fact
        for fact in pm12_service._pm12_performance_attribution_facts(_mixed_sources())
        if str(fact.get("persona_id") or "") == "p-retired-empty-1"
    ]
    assert [fact["runtime_id"] for fact in persona_facts] == ["rt-retired-empty-1"]

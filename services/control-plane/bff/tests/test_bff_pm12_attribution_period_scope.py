"""BFF-ATTRIBUTION-PERIOD-SCOPE-20261024.

Management Performance Attribution is composed from point-in-time runtime
telemetry summaries; the BFF has no dated per-runtime P&L or trade series to
window over.  A requested period other than ``latest`` must therefore be
reported as not applied (``meta.period``), no row or fact may claim the
requested window, and the response must stay 200 for every period value the
frontend sends.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.control_plane.bff.agora.performance import service as perf
from services.control_plane.bff.personas.service import _filter_by_common_identifiers


def _runtime(runtime_id: str, persona_id: str) -> Dict[str, Any]:
    return {
        "runtime_id": runtime_id,
        "persona_id": persona_id,
        "status": "active",
        "strategy_id": f"strategy-{persona_id}",
        "capital_pool_id": f"pool-{persona_id}",
    }


def _sources() -> Dict[str, Any]:
    runtimes = [
        _runtime("rt-alpha", "p-alpha"),
        _runtime("rt-beta", "p-beta"),
        _runtime("rt-gamma", "p-gamma"),
    ]
    personas = [{"persona_id": runtime["persona_id"]} for runtime in runtimes]
    telemetry = {
        "rt-alpha": {"runtime_id": "rt-alpha", "pnl": 10.0, "total_trades": 4, "collected_at": "2026-10-23T00:00:00Z"},
        "rt-beta": {"runtime_id": "rt-beta", "pnl": -2.5, "total_trades": 1, "collected_at": "2026-08-01T00:00:00Z"},
        "rt-gamma": {"runtime_id": "rt-gamma", "pnl": 4.0, "total_trades": 2, "collected_at": "2026-10-24T00:00:00Z"},
    }
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
        "telemetry_by_runtime_id": telemetry,
    }


def _response(period: str, **kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("dimensions", ["runtime"])
    kwargs.setdefault("page_size", 50)
    kwargs.setdefault("page_token", None)
    return perf.pm12_performance_attribution_response(
        period=period,
        sources_fn=lambda _tenant: _sources(),
        utc_now=lambda: "2026-10-24T00:00:00Z",
        **kwargs,
    )


def _row_keys(payload: Dict[str, Any]) -> List[str]:
    return sorted(row["dimension_key"] for row in payload["data"]["items"])


def test_latest_period_is_applied_as_requested() -> None:
    payload = _response("latest")
    assert payload["meta"]["period"] == {
        "requested": "latest",
        "applied": "latest",
        "windowed": False,
        "basis": "latest_runtime_telemetry_snapshot",
        "reason": None,
        "message": None,
    }
    assert payload["data"]["period"] == "latest"
    assert payload["data"]["summary"]["period"] == "latest"
    assert payload["data"]["summary"]["requested_period"] == "latest"
    assert payload["data"]["summary"]["period_windowed"] is False


@pytest.mark.parametrize("requested", ["7d", "30d", "90d", "all", "1d"])
def test_requested_window_is_reported_as_not_applied(requested: str) -> None:
    payload = _response(requested)
    scope = payload["meta"]["period"]
    assert scope["requested"] == requested
    assert scope["applied"] == "latest"
    assert scope["windowed"] is False
    assert scope["reason"] == "dated_attribution_source_unavailable"
    assert requested in scope["message"]
    assert payload["data"]["period"] == "latest"
    assert payload["data"]["summary"]["period"] == "latest"
    assert payload["data"]["summary"]["requested_period"] == requested
    assert payload["data"]["summary"]["period_windowed"] is False


def test_no_row_claims_the_unapplied_window() -> None:
    payload = _response("30d", dimensions=["runtime", "persona", "strategy"])
    assert payload["data"]["items"]
    assert {row["period"] for row in payload["data"]["items"]} == {"latest"}


def test_facts_are_stamped_with_applied_period() -> None:
    facts = perf.pm12_performance_attribution_facts(_sources())
    assert facts
    assert {fact["period"] for fact in facts} == {"latest"}


def test_requested_window_does_not_change_rows_totals_or_pages() -> None:
    latest = _response("latest")
    windowed = _response("30d")
    assert _row_keys(windowed) == _row_keys(latest) == ["rt-alpha", "rt-beta", "rt-gamma"]
    assert windowed["page_info"] == latest["page_info"]
    for key in ("row_count", "runtime_count", "total_pnl", "total_trades"):
        assert windowed["data"]["summary"][key] == latest["data"]["summary"][key]
    assert windowed["data"]["summary"]["total_pnl"] == 11.5

    first = _response("30d", page_size=2)
    assert first["page_info"]["total"] == 3
    second = _response("30d", page_size=2, page_token=first["page_info"]["next_page_token"])
    assert second["page_info"]["next_page_token"] is None
    assert sorted(_row_keys(first) + _row_keys(second)) == ["rt-alpha", "rt-beta", "rt-gamma"]
    assert {row["period"] for row in first["data"]["items"] + second["data"]["items"]} == {"latest"}


def test_blank_period_defaults_to_latest() -> None:
    scope = _response("  ")["meta"]["period"]
    assert scope["requested"] == "latest"
    assert scope["reason"] is None


def test_common_identifier_period_filter_is_a_real_filter_for_dated_items() -> None:
    # Other management surfaces pass items that carry their own period; for
    # those the shared filter still compares the item's period with the request.
    items = [{"id": "a", "period": "30d"}, {"id": "b", "period": "7d"}, {"id": "c"}]
    assert [item["id"] for item in _filter_by_common_identifiers(items, period="30d")] == ["a"]
    assert [item["id"] for item in _filter_by_common_identifiers(items)] == ["a", "b", "c"]

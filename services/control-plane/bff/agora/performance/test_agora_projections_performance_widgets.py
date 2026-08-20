"""Comprehensive tests for Agora performance suggestions producer, projection service, active widget queries, and explicit unwired widget unavailable states."""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict
import pytest

from .models import AdjustmentSuggestion
from .producer import PerformanceOutcomeEvaluationInput, PerformanceSuggestionProducer
from .service import PerformanceProjectionService
from .store import PerformanceSuggestionStore
from ..trading_data.models import WidgetDataQueryRequest, WidgetDataStatus, WidgetUnavailableReason
from ..trading_data.service import TradingDataService
from ..trading_room.store import TradingRoomStore


def _utc_now() -> str:
    return "2026-08-20T12:00:00Z"


def test_performance_suggestion_producer_creates_durable_suggestion():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
        db_path = tmp.name

    try:
        store = PerformanceSuggestionStore(path=db_path)
        producer = PerformanceSuggestionProducer(store=store)

        eval_input = PerformanceOutcomeEvaluationInput(
            strategy_id="strat-alpha-telemetry",
            period="latest",
            outcome_type="drawdown_breach",
            title="Max Drawdown Exceeded Threshold",
            rationale="Strategy experienced 4.2% drawdown against 3.0% threshold during paper run.",
            metrics={"drawdown_pct": 0.042, "threshold": 0.030, "projected_improvement": "reduce exposure 20%"},
            expected_effect={"action": "downscale", "target_pct": 0.8},
            expected_risk={"drawdown_risk": "mitigated"},
            source_id="paper_telemetry_engine",
            source_type="telemetry_engine",
            evidence_refs=["telemetry://run/20260820-01"],
            as_of=_utc_now(),
        )

        suggestion = producer.produce_suggestion_from_outcome(
            tenant_id="tenant-agora",
            owner_user_id="operator-001",
            evaluation=eval_input,
            utc_now=_utc_now(),
        )

        assert suggestion.strategy_id == "strat-alpha-telemetry"
        assert suggestion.status == "proposed"
        assert suggestion.version == 1
        assert suggestion.no_order_route_proof == "agora_suggestion_state_only"

        # Verify reload from store
        reloaded = store.list_suggestions(
            tenant_id="tenant-agora",
            owner_user_id="operator-001",
            strategy_id="strat-alpha-telemetry",
            period="latest",
        )
        assert len(reloaded) == 1
        assert reloaded[0]["suggestion_id"] == suggestion.suggestion_id
        assert reloaded[0]["title"] == "Max Drawdown Exceeded Threshold"

        # Verify projection service integrates suggestions
        svc = PerformanceProjectionService(
            suggestion_store=store,
            get_trade_journey_store=lambda: None,
            utc_now=_utc_now,
        )
        proj = svc.project(
            tenant_id="tenant-agora",
            owner_user_id="operator-001",
            strategy_id="strat-alpha-telemetry",
            period="latest",
            environment="paper",
        )
        assert proj.adjustment_suggestions.availability.status == "available"
        assert len(proj.adjustment_suggestions.items) == 1
        assert proj.adjustment_suggestions.items[0].suggestion_id == suggestion.suggestion_id
        assert proj.no_order_route_proof == "agora_performance_read_only"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_widget_data_query_active_widgets_and_unwired_unavailable():
    svc = TradingDataService(is_live_profile=False)

    tenant_id = "tenant-agora"
    user_id = "operator-001"
    strategy_id = "strat-active-001"

    # 1. Query active widget: signal_decision_queue
    req_queue = WidgetDataQueryRequest(
        widget_type="signal_decision_queue",
        tenant_id=tenant_id,
        user_id=user_id,
        params={"strategy_id": strategy_id},
    )
    res_queue = svc.query_widget_data(req_queue, scope_tenant_id=tenant_id, scope_user_id=user_id, utc_now=_utc_now())
    assert res_queue.widget_type == "signal_decision_queue"
    assert res_queue.status == WidgetDataStatus.OK.value
    assert "events" in res_queue.data

    # 2. Query active widget: candidate_funnel
    req_funnel = WidgetDataQueryRequest(
        widget_type="candidate_funnel",
        tenant_id=tenant_id,
        user_id=user_id,
        params={"strategy_id": strategy_id},
    )
    res_funnel = svc.query_widget_data(req_funnel, scope_tenant_id=tenant_id, scope_user_id=user_id, utc_now=_utc_now())
    assert res_funnel.widget_type == "candidate_funnel"
    assert res_funnel.status == WidgetDataStatus.OK.value
    assert "funnel_stages" in res_funnel.data

    # 3. Query active widget: candidate_ranking_table
    req_ranking = WidgetDataQueryRequest(
        widget_type="candidate_ranking_table",
        tenant_id=tenant_id,
        user_id=user_id,
        params={"strategy_id": strategy_id},
    )
    res_ranking = svc.query_widget_data(req_ranking, scope_tenant_id=tenant_id, scope_user_id=user_id, utc_now=_utc_now())
    assert res_ranking.widget_type == "candidate_ranking_table"
    assert res_ranking.status == WidgetDataStatus.OK.value

    # 4. Query active widget: evidence_trace
    req_ev = WidgetDataQueryRequest(
        widget_type="evidence_trace",
        tenant_id=tenant_id,
        user_id=user_id,
        params={"strategy_id": strategy_id},
    )
    res_ev = svc.query_widget_data(req_ev, scope_tenant_id=tenant_id, scope_user_id=user_id, utc_now=_utc_now())
    assert res_ev.widget_type == "evidence_trace"
    assert res_ev.status == WidgetDataStatus.OK.value

    # 5. Query unwired widget: e.g. branch_migration_sankey -> MUST return UNAVAILABLE with UNWIRED_WIDGET_TYPE
    req_unwired = WidgetDataQueryRequest(
        widget_type="branch_migration_sankey",
        tenant_id=tenant_id,
        user_id=user_id,
        params={"strategy_id": strategy_id},
    )
    res_unwired = svc.query_widget_data(req_unwired, scope_tenant_id=tenant_id, scope_user_id=user_id, utc_now=_utc_now())
    assert res_unwired.widget_type == "branch_migration_sankey"
    assert res_unwired.status == WidgetDataStatus.UNAVAILABLE.value
    assert res_unwired.unavailable_reason == WidgetUnavailableReason.UNWIRED_WIDGET_TYPE.value
    assert res_unwired.data == {}


def test_widget_data_query_tenant_isolation_fails_closed():
    svc = TradingDataService(is_live_profile=False)

    # Request with mismatched tenant scope
    req = WidgetDataQueryRequest(
        widget_type="signal_decision_queue",
        tenant_id="tenant-attacker",
        user_id="operator-001",
        params={"strategy_id": "strat-secret"},
    )
    res = svc.query_widget_data(req, scope_tenant_id="tenant-legit", scope_user_id="operator-001", utc_now=_utc_now())
    assert res.status == WidgetDataStatus.UNAVAILABLE.value
    assert res.unavailable_reason == WidgetUnavailableReason.TENANT_MISMATCH.value
    assert res.data == {}

"""Tests for Agora trading data widget query adapters, registry, and security rules."""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from .adapters import (
    AccountPositionsWidgetAdapter,
    RiskMetricsWidgetAdapter,
    StrategyPerformanceWidgetAdapter,
    WidgetAdapterRegistry,
)
from .models import WidgetDataQueryRequest, WidgetDataStatus, WidgetUnavailableReason
from .service import TradingDataService


class MockDataProvider:
    """Mock authoritative data provider for testing adapter contracts."""

    def __init__(
        self,
        strategy_records=None,
        positions=None,
        risk_data=None,
        is_stale=False,
        as_of="2026-08-13T12:00:00Z",
    ) -> None:
        self._strategy_records = strategy_records or [
            {"id": "rec-1", "timestamp": "2026-08-13T11:00:00Z", "return": 0.05},
            {"id": "rec-2", "timestamp": "2026-08-13T13:00:00Z", "return": 0.08},
        ]
        self._positions = positions or [
            {"symbol": "AAPL", "qty": 100, "as_of": "2026-08-13T11:30:00Z"}
        ]
        self._risk_data = risk_data if risk_data is not None else {
            "max_drawdown": 0.04,
            "risk_score": 0.12,
            "risk_passed": True,
        }
        self._is_stale = is_stale
        self._as_of = as_of

    def get_strategy_performance(self, tenant_id: str, user_id: str, strategy_id: str = None):
        return self._strategy_records, self._as_of, self._is_stale

    def get_account_positions(self, tenant_id: str, user_id: str):
        return self._positions, self._as_of, self._is_stale

    def get_risk_metrics(self, tenant_id: str, user_id: str):
        return self._risk_data, self._as_of, self._is_stale


def test_widget_query_contract_and_allowlist():
    provider = MockDataProvider()
    registry = WidgetAdapterRegistry()
    registry.register(StrategyPerformanceWidgetAdapter(provider))
    service = TradingDataService(registry=registry, is_live_profile=False)

    req = WidgetDataQueryRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        widget_type="strategy_performance",
        cutoff="2026-08-13T14:00:00Z",
    )

    res = service.query_widget_data(req, scope_tenant_id="tenant-1", scope_user_id="user-1")
    assert res.widget_type == "strategy_performance"
    assert res.status == WidgetDataStatus.OK.value
    assert res.source == "agora.trading_data.strategy_performance"
    assert res.as_of == "2026-08-13T12:00:00Z"
    assert res.cutoff == "2026-08-13T14:00:00Z"
    assert len(res.lineage) == 1
    assert res.unavailable_reason is None
    assert "records" in res.data


def test_unwired_widget_type_fails_closed():
    registry = WidgetAdapterRegistry()
    service = TradingDataService(registry=registry, is_live_profile=False)

    req = WidgetDataQueryRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        widget_type="unwired_custom_widget",
    )

    res = service.query_widget_data(req, scope_tenant_id="tenant-1", scope_user_id="user-1")
    assert res.status == WidgetDataStatus.UNAVAILABLE.value
    assert res.source == "unwired"
    assert res.unavailable_reason == WidgetUnavailableReason.UNWIRED_WIDGET_TYPE.value
    assert res.data == {}


def test_two_tenant_isolation_negative():
    provider = MockDataProvider()
    registry = WidgetAdapterRegistry()
    registry.register(StrategyPerformanceWidgetAdapter(provider))
    service = TradingDataService(registry=registry, is_live_profile=False)

    req = WidgetDataQueryRequest(
        tenant_id="tenant-A",
        user_id="user-A",
        widget_type="strategy_performance",
    )

    # Attempting to query with scope of tenant-B / user-B
    res = service.query_widget_data(req, scope_tenant_id="tenant-B", scope_user_id="user-B")
    assert res.status == WidgetDataStatus.UNAVAILABLE.value
    assert res.unavailable_reason == WidgetUnavailableReason.TENANT_MISMATCH.value
    assert res.data == {}


def test_point_in_time_cutoff():
    provider = MockDataProvider(
        strategy_records=[
            {"id": "rec-1", "timestamp": "2026-08-13T10:00:00Z", "return": 0.02},
            {"id": "rec-2", "timestamp": "2026-08-13T15:00:00Z", "return": 0.09},
        ]
    )
    adapter = StrategyPerformanceWidgetAdapter(provider)
    registry = WidgetAdapterRegistry()
    registry.register(adapter)
    service = TradingDataService(registry=registry, is_live_profile=False)

    req = WidgetDataQueryRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        widget_type="strategy_performance",
        cutoff="2026-08-13T12:00:00Z",
    )

    res = service.query_widget_data(req, scope_tenant_id="tenant-1", scope_user_id="user-1")
    assert res.status == WidgetDataStatus.OK.value
    # Second record at 15:00:00Z must be filtered out by 12:00:00Z cutoff
    records = res.data["records"]
    assert len(records) == 1
    assert records[0]["id"] == "rec-1"


def test_stale_and_unavailable_source():
    # Strategy performance returns degraded when stale
    stale_provider = MockDataProvider(is_stale=True)
    registry = WidgetAdapterRegistry()
    registry.register(StrategyPerformanceWidgetAdapter(stale_provider))
    registry.register(RiskMetricsWidgetAdapter(stale_provider))
    service = TradingDataService(registry=registry, is_live_profile=False)

    req_strat = WidgetDataQueryRequest(
        tenant_id="t-1", user_id="u-1", widget_type="strategy_performance"
    )
    res_strat = service.query_widget_data(req_strat, scope_tenant_id="t-1", scope_user_id="u-1")
    assert res_strat.status == WidgetDataStatus.DEGRADED.value
    assert res_strat.unavailable_reason == WidgetUnavailableReason.STALE_DATA.value

    # Risk metrics fail closed (unavailable) when stale!
    req_risk = WidgetDataQueryRequest(
        tenant_id="t-1", user_id="u-1", widget_type="risk_metrics"
    )
    res_risk = service.query_widget_data(req_risk, scope_tenant_id="t-1", scope_user_id="u-1")
    assert res_risk.status == WidgetDataStatus.UNAVAILABLE.value
    assert res_risk.unavailable_reason == WidgetUnavailableReason.STALE_DATA.value


def test_live_profile_fixture_guard():
    registry = WidgetAdapterRegistry()
    registry.register(StrategyPerformanceWidgetAdapter(data_provider=None))
    registry.register(AccountPositionsWidgetAdapter(data_provider=None))
    registry.register(RiskMetricsWidgetAdapter(data_provider=None))

    # Live profile enabled
    service = TradingDataService(registry=registry, is_live_profile=True)

    for w_type in ["strategy_performance", "account_positions", "risk_metrics"]:
        req = WidgetDataQueryRequest(tenant_id="t-1", user_id="u-1", widget_type=w_type)
        res = service.query_widget_data(req, scope_tenant_id="t-1", scope_user_id="u-1")
        assert res.status == WidgetDataStatus.UNAVAILABLE.value
        assert res.unavailable_reason == WidgetUnavailableReason.LIVE_PROFILE_NO_FIXTURES.value
        assert res.data == {}

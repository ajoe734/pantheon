"""Agora BFF trading_data package — live widget data query adapters and registry."""
from .models import (
    WidgetDataStatus,
    WidgetUnavailableReason,
    WidgetLineageRef,
    WidgetDataQueryRequest,
    WidgetDataQueryResponse,
)
from .adapters import (
    BaseWidgetDataAdapter,
    StrategyPerformanceWidgetAdapter,
    AccountPositionsWidgetAdapter,
    RiskMetricsWidgetAdapter,
    WidgetAdapterRegistry,
)
from .service import TradingDataService
from .router import create_trading_data_router

__all__ = [
    "WidgetDataStatus",
    "WidgetUnavailableReason",
    "WidgetLineageRef",
    "WidgetDataQueryRequest",
    "WidgetDataQueryResponse",
    "BaseWidgetDataAdapter",
    "StrategyPerformanceWidgetAdapter",
    "AccountPositionsWidgetAdapter",
    "RiskMetricsWidgetAdapter",
    "WidgetAdapterRegistry",
    "TradingDataService",
    "create_trading_data_router",
]

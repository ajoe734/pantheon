"""Trading data service orchestrating widget queries, registry, and predicate security checks."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .adapters import (
    AccountPositionsWidgetAdapter,
    CandidateFunnelWidgetAdapter,
    CandidateRankingWidgetAdapter,
    EvidenceTraceWidgetAdapter,
    RiskMetricsWidgetAdapter,
    SignalDecisionQueueWidgetAdapter,
    StrategyPerformanceWidgetAdapter,
    WidgetAdapterRegistry,
)
from .models import (
    WidgetDataQueryRequest,
    WidgetDataQueryResponse,
    WidgetDataStatus,
    WidgetUnavailableReason,
)


class TradingDataService:
    """Core domain service for Agora live widget query resolution."""

    def __init__(
        self,
        registry: Optional[WidgetAdapterRegistry] = None,
        is_live_profile: Optional[bool] = None,
    ) -> None:
        if registry is None:
            registry = WidgetAdapterRegistry()
            # Register default allowlisted adapters
            registry.register(StrategyPerformanceWidgetAdapter())
            registry.register(AccountPositionsWidgetAdapter())
            registry.register(RiskMetricsWidgetAdapter())
            registry.register(SignalDecisionQueueWidgetAdapter())
            registry.register(CandidateFunnelWidgetAdapter())
            registry.register(CandidateRankingWidgetAdapter())
            registry.register(EvidenceTraceWidgetAdapter())
        self.registry = registry

        if is_live_profile is None:
            env = os.environ.get("PANTHEON_ENV", "").lower()
            prof = os.environ.get("PROFILE", "").lower()
            is_live_profile = env in ("prod", "production", "live") or prof in ("prod", "production", "live")
        self.is_live_profile = is_live_profile

    def query_widget_data(
        self,
        request: WidgetDataQueryRequest,
        scope_tenant_id: str,
        scope_user_id: str,
        utc_now: Optional[str] = None,
    ) -> WidgetDataQueryResponse:
        now_str = utc_now or datetime.now(timezone.utc).isoformat()
        cutoff_str = request.cutoff or now_str

        # Strict Tenant Predicate Check
        if request.tenant_id != scope_tenant_id or request.user_id != scope_user_id:
            return WidgetDataQueryResponse(
                widget_type=request.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source="security_guard",
                as_of=now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=WidgetUnavailableReason.TENANT_MISMATCH.value,
            )

        return self.registry.query(
            request,
            is_live_profile=self.is_live_profile,
            utc_now=now_str,
        )

    def list_allowlist(self) -> List[Dict[str, str]]:
        return self.registry.list_allowlist()

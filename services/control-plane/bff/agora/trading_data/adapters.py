"""Authoritative widget data query adapters and allowlist registry."""
from __future__ import annotations

import abc
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    WidgetDataQueryRequest,
    WidgetDataQueryResponse,
    WidgetDataStatus,
    WidgetLineageRef,
    WidgetUnavailableReason,
)


def _compute_query_hash(source_name: str, tenant_id: str, user_id: str, cutoff: str) -> str:
    raw = f"{source_name}:{tenant_id}:{user_id}:{cutoff}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _parse_iso(iso_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


class BaseWidgetDataAdapter(abc.ABC):
    """Abstract base class for all authoritative Agora widget query adapters."""

    @property
    @abc.abstractmethod
    def widget_type(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def source_name(self) -> str:
        pass

    @abc.abstractmethod
    def query(
        self,
        request: WidgetDataQueryRequest,
        is_live_profile: bool = False,
        utc_now: Optional[str] = None,
    ) -> WidgetDataQueryResponse:
        pass


class StrategyPerformanceWidgetAdapter(BaseWidgetDataAdapter):
    """Adapter for strategy_performance widget type."""

    def __init__(self, data_provider: Optional[Any] = None) -> None:
        self._data_provider = data_provider

    @property
    def widget_type(self) -> str:
        return "strategy_performance"

    @property
    def source_name(self) -> str:
        return "agora.trading_data.strategy_performance"

    def query(
        self,
        request: WidgetDataQueryRequest,
        is_live_profile: bool = False,
        utc_now: Optional[str] = None,
    ) -> WidgetDataQueryResponse:
        now_str = utc_now or datetime.now(timezone.utc).isoformat()
        cutoff_str = request.cutoff or now_str

        if self._data_provider is None:
            if is_live_profile:
                return WidgetDataQueryResponse(
                    widget_type=self.widget_type,
                    status=WidgetDataStatus.UNAVAILABLE.value,
                    source=self.source_name,
                    as_of=now_str,
                    cutoff=cutoff_str,
                    lineage=[],
                    data={},
                    unavailable_reason=WidgetUnavailableReason.LIVE_PROFILE_NO_FIXTURES.value,
                )
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source=self.source_name,
                as_of=now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=WidgetUnavailableReason.SOURCE_UNAVAILABLE.value,
            )

        # Query backing data provider with tenant/user predicate
        records, data_as_of, is_stale = self._data_provider.get_strategy_performance(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            strategy_id=request.params.get("strategy_id"),
        )

        if not records and is_live_profile:
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source=self.source_name,
                as_of=now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=WidgetUnavailableReason.DATA_MISSING.value,
            )

        if is_stale:
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.DEGRADED.value,
                source=self.source_name,
                as_of=data_as_of or now_str,
                cutoff=cutoff_str,
                lineage=[
                    WidgetLineageRef(
                        source_name=self.source_name,
                        record_count=len(records),
                        point_in_time=cutoff_str,
                        query_hash=_compute_query_hash(
                            self.source_name, request.tenant_id, request.user_id, cutoff_str
                        ),
                    )
                ],
                data={"records": records},
                unavailable_reason=WidgetUnavailableReason.STALE_DATA.value,
            )

        # Apply point-in-time cutoff filtering
        cutoff_dt = _parse_iso(cutoff_str)
        filtered_records = []
        if cutoff_dt:
            for rec in records:
                ts_dt = _parse_iso(rec.get("timestamp", ""))
                if ts_dt is None or ts_dt <= cutoff_dt:
                    filtered_records.append(rec)
        else:
            filtered_records = records

        lineage_ref = WidgetLineageRef(
            source_name=self.source_name,
            record_count=len(filtered_records),
            point_in_time=cutoff_str,
            query_hash=_compute_query_hash(
                self.source_name, request.tenant_id, request.user_id, cutoff_str
            ),
        )

        return WidgetDataQueryResponse(
            widget_type=self.widget_type,
            status=WidgetDataStatus.OK.value,
            source=self.source_name,
            as_of=data_as_of or now_str,
            cutoff=cutoff_str,
            lineage=[lineage_ref],
            data={"records": filtered_records, "summary": {"total_count": len(filtered_records)}},
            unavailable_reason=None,
        )


class AccountPositionsWidgetAdapter(BaseWidgetDataAdapter):
    """Adapter for account_positions widget type."""

    def __init__(self, data_provider: Optional[Any] = None) -> None:
        self._data_provider = data_provider

    @property
    def widget_type(self) -> str:
        return "account_positions"

    @property
    def source_name(self) -> str:
        return "agora.trading_data.account_positions"

    def query(
        self,
        request: WidgetDataQueryRequest,
        is_live_profile: bool = False,
        utc_now: Optional[str] = None,
    ) -> WidgetDataQueryResponse:
        now_str = utc_now or datetime.now(timezone.utc).isoformat()
        cutoff_str = request.cutoff or now_str

        if self._data_provider is None:
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source=self.source_name,
                as_of=now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=(
                    WidgetUnavailableReason.LIVE_PROFILE_NO_FIXTURES.value
                    if is_live_profile
                    else WidgetUnavailableReason.SOURCE_UNAVAILABLE.value
                ),
            )

        positions, data_as_of, is_stale = self._data_provider.get_account_positions(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
        )

        if is_stale:
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.DEGRADED.value,
                source=self.source_name,
                as_of=data_as_of or now_str,
                cutoff=cutoff_str,
                lineage=[
                    WidgetLineageRef(
                        source_name=self.source_name,
                        record_count=len(positions),
                        point_in_time=cutoff_str,
                        query_hash=_compute_query_hash(
                            self.source_name, request.tenant_id, request.user_id, cutoff_str
                        ),
                    )
                ],
                data={"positions": positions},
                unavailable_reason=WidgetUnavailableReason.STALE_DATA.value,
            )

        cutoff_dt = _parse_iso(cutoff_str)
        filtered = []
        if cutoff_dt:
            for p in positions:
                ts_dt = _parse_iso(p.get("as_of", ""))
                if ts_dt is None or ts_dt <= cutoff_dt:
                    filtered.append(p)
        else:
            filtered = positions

        return WidgetDataQueryResponse(
            widget_type=self.widget_type,
            status=WidgetDataStatus.OK.value,
            source=self.source_name,
            as_of=data_as_of or now_str,
            cutoff=cutoff_str,
            lineage=[
                WidgetLineageRef(
                    source_name=self.source_name,
                    record_count=len(filtered),
                    point_in_time=cutoff_str,
                    query_hash=_compute_query_hash(
                        self.source_name, request.tenant_id, request.user_id, cutoff_str
                    ),
                )
            ],
            data={"positions": filtered, "position_count": len(filtered)},
            unavailable_reason=None,
        )


class RiskMetricsWidgetAdapter(BaseWidgetDataAdapter):
    """Adapter for risk_metrics widget type. Fails closed if risk data is missing or stale."""

    def __init__(self, data_provider: Optional[Any] = None) -> None:
        self._data_provider = data_provider

    @property
    def widget_type(self) -> str:
        return "risk_metrics"

    @property
    def source_name(self) -> str:
        return "agora.trading_data.risk_metrics"

    def query(
        self,
        request: WidgetDataQueryRequest,
        is_live_profile: bool = False,
        utc_now: Optional[str] = None,
    ) -> WidgetDataQueryResponse:
        now_str = utc_now or datetime.now(timezone.utc).isoformat()
        cutoff_str = request.cutoff or now_str

        if self._data_provider is None:
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source=self.source_name,
                as_of=now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=(
                    WidgetUnavailableReason.LIVE_PROFILE_NO_FIXTURES.value
                    if is_live_profile
                    else WidgetUnavailableReason.SOURCE_UNAVAILABLE.value
                ),
            )

        risk_data, data_as_of, is_stale = self._data_provider.get_risk_metrics(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
        )

        if not risk_data:
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source=self.source_name,
                as_of=now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=WidgetUnavailableReason.DATA_MISSING.value,
            )

        if is_stale:
            # Risk metrics fail closed when stale!
            return WidgetDataQueryResponse(
                widget_type=self.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source=self.source_name,
                as_of=data_as_of or now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=WidgetUnavailableReason.STALE_DATA.value,
            )

        return WidgetDataQueryResponse(
            widget_type=self.widget_type,
            status=WidgetDataStatus.OK.value,
            source=self.source_name,
            as_of=data_as_of or now_str,
            cutoff=cutoff_str,
            lineage=[
                WidgetLineageRef(
                    source_name=self.source_name,
                    record_count=1,
                    point_in_time=cutoff_str,
                    query_hash=_compute_query_hash(
                        self.source_name, request.tenant_id, request.user_id, cutoff_str
                    ),
                )
            ],
            data=risk_data,
            unavailable_reason=None,
        )


class WidgetAdapterRegistry:
    """Registry maintaining allowlisted widget query adapters."""

    def __init__(self) -> None:
        self._adapters: Dict[str, BaseWidgetDataAdapter] = {}

    def register(self, adapter: BaseWidgetDataAdapter) -> None:
        self._adapters[adapter.widget_type] = adapter

    def get(self, widget_type: str) -> Optional[BaseWidgetDataAdapter]:
        return self._adapters.get(widget_type)

    def list_allowlist(self) -> List[Dict[str, str]]:
        return [
            {"widget_type": k, "source_name": v.source_name}
            for k, v in self._adapters.items()
        ]

    def query(
        self,
        request: WidgetDataQueryRequest,
        is_live_profile: bool = False,
        utc_now: Optional[str] = None,
    ) -> WidgetDataQueryResponse:
        now_str = utc_now or datetime.now(timezone.utc).isoformat()
        cutoff_str = request.cutoff or now_str

        adapter = self._adapters.get(request.widget_type)
        if adapter is None:
            # Unwired widget types remain unavailable and cannot enter the live registry
            return WidgetDataQueryResponse(
                widget_type=request.widget_type,
                status=WidgetDataStatus.UNAVAILABLE.value,
                source="unwired",
                as_of=now_str,
                cutoff=cutoff_str,
                lineage=[],
                data={},
                unavailable_reason=WidgetUnavailableReason.UNWIRED_WIDGET_TYPE.value,
            )

        return adapter.query(request, is_live_profile=is_live_profile, utc_now=now_str)

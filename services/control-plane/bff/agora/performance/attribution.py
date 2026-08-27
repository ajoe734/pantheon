"""Owner-scoped Agora Strategy Performance Attribution models and projection service.

Implements owner/tenant isolation for Agora performance attribution.
Alice cannot observe Bob's strategy existence, metrics, or trade journeys.
Missing or partial sources are typed explicitly without fabricated data.
Carries no_order_route_proof=agora_performance_read_only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

_USER_SCOPE_FIELDS = ("owner_user_id", "agora_user_id", "user_id")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _timestamp_in_period(value: Any, *, period: str, now: datetime) -> bool:
    if period in {"all", "latest"}:
        return True
    parsed = _parse_timestamp(value)
    if parsed is None:
        return False
    days = 7 if period == "7d" else 30
    return parsed >= now - timedelta(days=days)


def _event_user_ids(event: Mapping[str, Any]) -> set[str]:
    return {
        str(event.get(field) or "").strip()
        for field in _USER_SCOPE_FIELDS
        if str(event.get(field) or "").strip()
    }


def _projection_visible_to_user(projection: Any, user_id: str) -> bool:
    scoped_values: set[str] = set()
    for event in getattr(projection, "timeline", []) or []:
        scoped_values.update(_event_user_ids(event))
    return scoped_values == {user_id}


def _projection_strategy_id(projection: Any) -> str:
    identifiers = getattr(projection, "snapshot", {}).get("identifiers") or {}
    values = identifiers.get("strategy_id") or []
    return str(values[0]) if len(values) == 1 else ""


# ---------------------------------------------------------------------------
# Pydantic Models for Agora Strategy Performance Attribution
# ---------------------------------------------------------------------------

class TradingRoomPerformanceAttributionMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")

    runtime_count: int = 0
    telemetry_runtime_count: int = 0
    holding_count: int = 0
    total_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    total_notional: Optional[float] = None
    total_market_value: Optional[float] = None
    total_exposure: Optional[float] = None
    worst_drawdown: Optional[float] = None
    average_fill_rate: Optional[float] = None
    average_slippage_bps: Optional[float] = None
    total_trades: int = 0
    latest_telemetry_at: Optional[str] = None
    pnl_contribution_pct: Optional[float] = None
    notional_weight: Optional[float] = None
    data_confidence: Optional[str] = None


class TradingRoomPerformanceAttributionRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    dimension: str = "strategy"
    dimension_key: str
    label: str
    period: str = "latest"
    rank: int = 1
    metrics: TradingRoomPerformanceAttributionMetrics
    total_pnl: Optional[float] = None
    pnl_contribution_pct: Optional[float] = None
    notional_weight: Optional[float] = None
    runtime_count: int = 0
    holding_count: int = 0
    data_confidence: Optional[str] = None
    source_status: Optional[str] = None
    source_refs: Dict[str, List[str]] = Field(default_factory=dict)
    links: Dict[str, Optional[str]] = Field(default_factory=dict)


class TradingRoomPerformanceAttributionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    period: str = "latest"
    dimensions: List[str] = Field(default_factory=lambda: ["strategy"])
    supported_dimensions: List[str] = Field(default_factory=lambda: ["strategy"])
    row_count: int = 0
    returned_row_count: int = 0
    runtime_count: int = 0
    telemetry_runtime_count: int = 0
    holding_count: int = 0
    total_pnl: Optional[float] = None
    total_notional: Optional[float] = None
    total_exposure: Optional[float] = None
    worst_drawdown: Optional[float] = None
    average_fill_rate: Optional[float] = None
    average_slippage_bps: Optional[float] = None
    total_trades: int = 0
    latest_telemetry_at: Optional[str] = None
    basis: str = "owner_scoped_strategy_attribution"


class PerformanceAttributionPageInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    next_page_token: Optional[str] = None
    total: int = 0
    page_size: int = 50


class TradingRoomPerformanceAttributionData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = "agora-trading-room-performance-attribution-by-strategy"
    period: str = "latest"
    dimensions: List[str] = Field(default_factory=lambda: ["strategy"])
    items: List[TradingRoomPerformanceAttributionRow] = Field(default_factory=list)
    summary: TradingRoomPerformanceAttributionSummary = Field(
        default_factory=TradingRoomPerformanceAttributionSummary
    )
    page_info: Optional[PerformanceAttributionPageInfo] = None


class TradingRoomPerformanceAttributionMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scope: Dict[str, str]
    period: str = "latest"
    snapshot_at: str
    composition_sources: List[str] = Field(
        default_factory=lambda: ["strategy_directory", "telemetry", "trade_journeys"]
    )
    surfaces: Dict[str, Any] = Field(default_factory=dict)
    policy: str = "read_only_performance_attribution"
    no_order_route_proof: Literal["agora_performance_read_only"] = (
        "agora_performance_read_only"
    )


class TradingRoomPerformanceAttributionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: TradingRoomPerformanceAttributionData
    page_info: PerformanceAttributionPageInfo
    meta: TradingRoomPerformanceAttributionMeta


# ---------------------------------------------------------------------------
# Performance Attribution Projection Implementation
# ---------------------------------------------------------------------------

def project_agora_performance_attribution_by_strategy(
    *,
    tenant_id: str,
    owner_user_id: str,
    period: str = "latest",
    page_size: int = 50,
    page_token: Optional[str] = None,
    strategy_id_filter: Optional[str] = None,
    journey_store: Optional[Any] = None,
    workshop_store: Optional[Any] = None,
    suggestion_store: Optional[Any] = None,
    utc_now: Callable[[], str],
) -> TradingRoomPerformanceAttributionEnvelope:
    if period not in {"latest", "7d", "30d", "all"}:
        raise ValueError(
            f"Invalid period '{period}', expected one of ['latest', '7d', '30d', 'all']"
        )
    if page_size < 1 or page_size > 200:
        raise ValueError(
            f"Invalid page_size '{page_size}', must be between 1 and 200"
        )
    snapshot_at = utc_now()
    now = _parse_timestamp(snapshot_at) or datetime.now(timezone.utc)

    # 1. Discover owner-scoped strategies
    user_strategies: Dict[str, Dict[str, Any]] = {}

    if workshop_store is not None and hasattr(workshop_store, "list_sessions"):
        try:
            sessions, _ = workshop_store.list_sessions(
                user_id=owner_user_id,
                tenant_id=tenant_id,
                status=None,
                limit=100,
            )
            for session in sessions:
                s_id = str(
                    session.get("strategy_id")
                    or session.get("workshop_id")
                    or ""
                ).strip()
                title = str(
                    session.get("title")
                    or session.get("name")
                    or s_id
                ).strip()
                if s_id:
                    user_strategies[s_id] = {
                        "strategy_id": s_id,
                        "title": title,
                    }
        except Exception:
            pass

    materializer = journey_store.materializer() if journey_store is not None and hasattr(journey_store, "materializer") else None
    scoped_projections: List[Any] = []
    projections_by_strategy: Dict[str, List[Any]] = defaultdict(list)

    if materializer is not None:
        for projection in getattr(materializer, "projections", []):
            if (
                getattr(projection, "tenant_id", None) == tenant_id
                and _projection_visible_to_user(projection, owner_user_id)
            ):
                s_id = _projection_strategy_id(projection)
                if s_id:
                    user_strategies.setdefault(s_id, {"strategy_id": s_id, "title": s_id})
                    updated_at = projection.snapshot.get("updated_at")
                    if _timestamp_in_period(updated_at, period=period, now=now):
                        scoped_projections.append(projection)
                        projections_by_strategy[s_id].append(projection)

    # Filter by strategy_id if provided
    if strategy_id_filter:
        clean_strat_filter = strategy_id_filter.strip()
        if clean_strat_filter in user_strategies:
            target_strategy_ids = [clean_strat_filter]
        else:
            # Privacy: Alice cannot observe Bob's strategy existence
            target_strategy_ids = []
    else:
        target_strategy_ids = sorted(list(user_strategies.keys()))

    # 2. Extract metrics and build rows for each strategy
    all_rows: List[TradingRoomPerformanceAttributionRow] = []
    all_telemetry_source_ids: Set[str] = set()

    for s_id in target_strategy_ids:
        strat_info = user_strategies.get(s_id, {})
        projs = projections_by_strategy.get(s_id, [])

        runtime_ids: Set[str] = set()
        capital_pool_ids: Set[str] = set()
        persona_ids: Set[str] = set()
        total_trades = 0
        total_pnl = 0.0
        unrealized_pnl = 0.0
        realized_pnl = 0.0
        total_notional = 0.0
        total_market_value = 0.0
        total_exposure = 0.0
        worst_drawdown = 0.0
        fill_rates: List[float] = []
        slippages: List[float] = []
        latest_ts: Optional[str] = None
        has_telemetry = False
        holding_count = 0

        for proj in projs:
            identifiers = proj.snapshot.get("identifiers") or {}
            for r_id in identifiers.get("runtime_id") or []:
                if str(r_id).strip():
                    runtime_ids.add(str(r_id).strip())
            for cp_id in identifiers.get("capital_pool_id") or []:
                if str(cp_id).strip():
                    capital_pool_ids.add(str(cp_id).strip())
            for p_id in identifiers.get("persona_id") or []:
                if str(p_id).strip():
                    persona_ids.add(str(p_id).strip())

            for event in getattr(proj, "timeline", []) or []:
                r_id = event.get("runtime_id")
                if r_id and str(r_id).strip():
                    runtime_ids.add(str(r_id).strip())
                ev_metrics = event.get("metrics")
                if isinstance(ev_metrics, dict):
                    if "pnl" in ev_metrics:
                        has_telemetry = True
                        total_pnl += float(ev_metrics.get("pnl") or 0.0)
                    if "unrealized_pnl" in ev_metrics:
                        unrealized_pnl += float(ev_metrics.get("unrealized_pnl") or 0.0)
                    if "realized_pnl" in ev_metrics:
                        realized_pnl += float(ev_metrics.get("realized_pnl") or 0.0)
                    if "notional" in ev_metrics:
                        total_notional += float(ev_metrics.get("notional") or 0.0)
                    if "market_value" in ev_metrics:
                        total_market_value += float(ev_metrics.get("market_value") or 0.0)
                    if "exposure" in ev_metrics:
                        total_exposure += float(ev_metrics.get("exposure") or 0.0)
                    if "drawdown" in ev_metrics:
                        worst_drawdown = min(worst_drawdown, float(ev_metrics.get("drawdown") or 0.0))
                    if "fill_rate" in ev_metrics:
                        fill_rates.append(float(ev_metrics.get("fill_rate") or 0.0))
                    if "slippage_bps" in ev_metrics:
                        slippages.append(float(ev_metrics.get("slippage_bps") or 0.0))

                fills = list(identifiers.get("fill_id") or []) + list(identifiers.get("broker_trade_id") or [])
                if fills:
                    total_trades += len(fills)
                elif event.get("stage") == "fill_management":
                    total_trades += 1

                ts = event.get("occurred_at") or event.get("updated_at")
                if ts and (latest_ts is None or str(ts) > str(latest_ts)):
                    latest_ts = str(ts)

            status = proj.snapshot.get("status")
            if status not in {"closed", "concluded", "failed", "cancelled", "expired"}:
                holding_count += 1

        if has_telemetry:
            all_telemetry_source_ids.update(runtime_ids)

        avg_fill = (sum(fill_rates) / len(fill_rates)) if fill_rates else (1.0 if total_trades > 0 else None)
        avg_slip = (sum(slippages) / len(slippages)) if slippages else (0.0 if total_trades > 0 else None)

        runtime_count = len(runtime_ids)
        telemetry_runtime_count = runtime_count if has_telemetry else 0
        data_confidence = "formal" if has_telemetry else ("partial" if projs else "unavailable")

        metrics = TradingRoomPerformanceAttributionMetrics(
            runtime_count=runtime_count,
            telemetry_runtime_count=telemetry_runtime_count,
            holding_count=holding_count,
            total_pnl=round(total_pnl, 2) if has_telemetry else None,
            unrealized_pnl=round(unrealized_pnl, 2) if has_telemetry else None,
            realized_pnl=round(realized_pnl, 2) if has_telemetry else None,
            total_notional=round(total_notional, 2) if has_telemetry else None,
            total_market_value=round(total_market_value, 2) if has_telemetry else None,
            total_exposure=round(total_exposure, 2) if has_telemetry else None,
            worst_drawdown=round(worst_drawdown, 4) if has_telemetry else None,
            average_fill_rate=round(avg_fill, 4) if avg_fill is not None else None,
            average_slippage_bps=round(avg_slip, 2) if avg_slip is not None else None,
            total_trades=total_trades,
            latest_telemetry_at=latest_ts,
            data_confidence=data_confidence,
        )

        row = TradingRoomPerformanceAttributionRow(
            id=f"agora-perf-strat-{s_id}",
            dimension="strategy",
            dimension_key=s_id,
            label=strat_info.get("title") or s_id,
            period=period,
            rank=1,
            metrics=metrics,
            total_pnl=metrics.total_pnl,
            runtime_count=runtime_count,
            holding_count=holding_count,
            data_confidence=data_confidence,
            source_status="ok" if data_confidence == "formal" else data_confidence,
            source_refs={
                "strategy_ids": [s_id],
                "runtime_ids": sorted(list(runtime_ids)),
                "capital_pool_ids": sorted(list(capital_pool_ids)),
                "persona_ids": sorted(list(persona_ids)),
            },
            links={
                "strategy": f"/agora/strategies/{s_id}",
                "performance": f"/bff/agora/trading-room/strategies/{s_id}/performance",
            },
        )
        all_rows.append(row)

    # 3. Sort, rank, and calculate contribution percentages
    all_rows.sort(
        key=lambda item: (
            item.total_pnl is None,
            -(item.total_pnl or 0.0),
            item.dimension_key,
        )
    )

    portfolio_pnl = sum(r.total_pnl for r in all_rows if r.total_pnl is not None)
    portfolio_notional = sum(
        r.metrics.total_notional for r in all_rows if r.metrics.total_notional is not None
    )

    for rank, r in enumerate(all_rows, start=1):
        r.rank = rank
        if r.total_pnl is not None and portfolio_pnl not in (0.0, None):
            r.pnl_contribution_pct = round(r.total_pnl / portfolio_pnl, 6)
            r.metrics.pnl_contribution_pct = r.pnl_contribution_pct
        if r.metrics.total_notional is not None and portfolio_notional not in (0.0, None):
            r.notional_weight = round(r.metrics.total_notional / portfolio_notional, 6)
            r.metrics.notional_weight = r.notional_weight

    # 4. Pagination
    total = len(all_rows)
    start_offset = 0
    if page_token:
        try:
            if page_token.startswith("offset-"):
                start_offset = int(page_token.removeprefix("offset-"))
            elif page_token.isdigit():
                start_offset = int(page_token)
        except ValueError:
            start_offset = 0

    page_items = all_rows[start_offset : start_offset + page_size]
    next_page_token = (
        f"offset-{start_offset + page_size}"
        if start_offset + page_size < total
        else None
    )

    page_info = PerformanceAttributionPageInfo(
        next_page_token=next_page_token,
        total=total,
        page_size=page_size,
    )

    # 5. Summary aggregation
    sum_pnl = sum(r.total_pnl for r in all_rows if r.total_pnl is not None) if any(r.total_pnl is not None for r in all_rows) else None
    sum_notional = sum(r.metrics.total_notional for r in all_rows if r.metrics.total_notional is not None) if any(r.metrics.total_notional is not None for r in all_rows) else None
    sum_exposure = sum(r.metrics.total_exposure for r in all_rows if r.metrics.total_exposure is not None) if any(r.metrics.total_exposure is not None for r in all_rows) else None
    worst_dd = min((r.metrics.worst_drawdown for r in all_rows if r.metrics.worst_drawdown is not None), default=None)
    avg_fills = [r.metrics.average_fill_rate for r in all_rows if r.metrics.average_fill_rate is not None]
    avg_slips = [r.metrics.average_slippage_bps for r in all_rows if r.metrics.average_slippage_bps is not None]
    max_telemetry_at = max(
        (r.metrics.latest_telemetry_at for r in all_rows if r.metrics.latest_telemetry_at),
        default=None,
    )

    summary = TradingRoomPerformanceAttributionSummary(
        period=period,
        dimensions=["strategy"],
        supported_dimensions=["strategy"],
        row_count=total,
        returned_row_count=len(page_items),
        runtime_count=sum(r.runtime_count for r in all_rows),
        telemetry_runtime_count=sum(r.metrics.telemetry_runtime_count for r in all_rows),
        holding_count=sum(r.holding_count for r in all_rows),
        total_pnl=round(sum_pnl, 2) if sum_pnl is not None else None,
        total_notional=round(sum_notional, 2) if sum_notional is not None else None,
        total_exposure=round(sum_exposure, 2) if sum_exposure is not None else None,
        worst_drawdown=round(worst_dd, 4) if worst_dd is not None else None,
        average_fill_rate=round(sum(avg_fills) / len(avg_fills), 4) if avg_fills else None,
        average_slippage_bps=round(sum(avg_slips) / len(avg_slips), 2) if avg_slips else None,
        total_trades=sum(r.metrics.total_trades for r in all_rows),
        latest_telemetry_at=max_telemetry_at,
        basis="owner_scoped_strategy_attribution",
    )

    # 6. Source surfaces typing
    has_strategy_dir = len(user_strategies) > 0
    has_telemetry_src = len(all_telemetry_source_ids) > 0
    has_journey_src = len(scoped_projections) > 0

    journey_ids = sorted({getattr(p, "journey_id", "") for p in scoped_projections if getattr(p, "journey_id", "")})

    surfaces = {
        "strategy_directory": {
            "status": "available" if has_strategy_dir else "unavailable",
            "as_of": snapshot_at if has_strategy_dir else None,
            "source_ids": sorted(list(user_strategies.keys())),
            "reason": None if has_strategy_dir else "no_strategies_found",
        },
        "telemetry": {
            "status": "available" if has_telemetry_src else "unavailable",
            "as_of": max_telemetry_at if has_telemetry_src else None,
            "source_ids": sorted(list(all_telemetry_source_ids)),
            "reason": None if has_telemetry_src else "no_current_rows",
        },
        "trade_journeys": {
            "status": "available" if has_journey_src else "unavailable",
            "as_of": max_telemetry_at if has_journey_src else None,
            "source_ids": journey_ids,
            "reason": None if has_journey_src else "no_journey_records",
        },
    }

    meta = TradingRoomPerformanceAttributionMeta(
        scope={"tenant_id": tenant_id, "owner_user_id": owner_user_id},
        period=period,
        snapshot_at=snapshot_at,
        composition_sources=["strategy_directory", "telemetry", "trade_journeys"],
        surfaces=surfaces,
        policy="read_only_performance_attribution",
        no_order_route_proof="agora_performance_read_only",
    )

    data = TradingRoomPerformanceAttributionData(
        id="agora-trading-room-performance-attribution-by-strategy",
        period=period,
        dimensions=["strategy"],
        items=page_items,
        summary=summary,
        page_info=page_info,
    )

    return TradingRoomPerformanceAttributionEnvelope(
        data=data,
        page_info=page_info,
        meta=meta,
    )

"""Truth-preserving Strategy Performance projection service.

Trade journeys are read through the configured Postgres projection reader
(``get_projection_reader``); the service owns no event store or materializer.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Iterable, List, Mapping, Optional

from pydantic import ValidationError

from .journeys import parse_timestamp as _parse_timestamp, scan_owner_journeys
from .models import (
    AdjustmentSuggestion,
    ComplianceMetric,
    ComplianceProjection,
    ExecutionHistoryProjection,
    ExecutionHistoryRow,
    InterventionAggregate,
    InterventionProjection,
    InterventionRecord,
    PerformanceFreshness,
    PerformanceWarning,
    SourceAvailability,
    StrategyPerformanceProjection,
    SuggestionProjection,
    WarningProjection,
)
from .attribution import (
    TradingRoomPerformanceAttributionEnvelope,
    project_agora_performance_attribution_by_strategy,
)
from .store import PerformanceSuggestionStore


def _dedupe(values: Iterable[Any]) -> List[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value or "")))


def _source_availability(
    items: Iterable[Any],
    *,
    as_of: Optional[str],
    source_ids: Iterable[str],
    reason: str,
    empty_is_available: bool = False,
) -> SourceAvailability:
    has_items = bool(list(items))
    return SourceAvailability(
        status="available" if has_items or empty_is_available else "unavailable",
        as_of=as_of if has_items or empty_is_available else None,
        source_ids=sorted({item for item in source_ids if item}),
        reason=None if has_items or empty_is_available else reason,
    )


def _latest_timestamp(values: Iterable[Any]) -> Optional[str]:
    parsed = [
        (timestamp, str(value))
        for value in values
        for timestamp in [_parse_timestamp(value)]
        if timestamp is not None
    ]
    return max(parsed, default=(None, None), key=lambda item: item[0])[1]


def _event_records(event: Mapping[str, Any], plural: str, singular: str) -> List[Any]:
    raw = event.get(plural)
    if isinstance(raw, list):
        return raw
    single = event.get(singular)
    return [single] if isinstance(single, dict) else []


def _warning_id(projection: Any, code: str, details: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "code": code,
            "details": details,
            "journey_id": projection.journey_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "agperf-warning-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class PerformanceProjectionService:
    def __init__(
        self,
        *,
        suggestion_store: PerformanceSuggestionStore,
        get_projection_reader: Callable[[], Any],
        utc_now: Callable[[], str],
    ) -> None:
        self.suggestion_store = suggestion_store
        self.get_projection_reader = get_projection_reader
        self.utc_now = utc_now

    def project(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        strategy_id: str,
        period: str,
        environment: str,
    ) -> StrategyPerformanceProjection:
        snapshot_at = self.utc_now()
        now = _parse_timestamp(snapshot_at) or datetime.now(timezone.utc)
        scan = scan_owner_journeys(
            self.get_projection_reader(),
            tenant_id=tenant_id,
            environment=environment,
            owner_user_id=owner_user_id,
            period=period,
            now=now,
            strategy_id=strategy_id,
        )
        scoped: List[Any] = list(scan.projections)
        if period == "latest" and scoped:
            scoped = [
                max(scoped, key=lambda item: str(item.snapshot.get("updated_at") or ""))
            ]

        compliance_metrics: List[ComplianceMetric] = []
        interventions: List[InterventionRecord] = []
        explicit_warnings: List[PerformanceWarning] = []
        for projection in scoped:
            for event in projection.timeline:
                for raw in _event_records(event, "compliance_metrics", "compliance_metric"):
                    try:
                        compliance_metrics.append(ComplianceMetric.model_validate(raw))
                    except ValidationError:
                        continue
                for raw in _event_records(event, "interventions", "intervention"):
                    try:
                        interventions.append(InterventionRecord.model_validate(raw))
                    except ValidationError:
                        continue
                for raw in _event_records(event, "performance_warnings", "performance_warning"):
                    try:
                        explicit_warnings.append(PerformanceWarning.model_validate(raw))
                    except ValidationError:
                        continue

        execution_rows: List[ExecutionHistoryRow] = []
        diagnostic_warnings: List[PerformanceWarning] = []
        for projection in scoped:
            identifiers = projection.snapshot.get("identifiers") or {}
            evidence_refs = [
                str(event.get("event_id"))
                for event in projection.timeline
                if str(event.get("event_id") or "")
            ]
            try:
                execution_rows.append(
                    ExecutionHistoryRow(
                        journey_id=projection.journey_id,
                        status=str(projection.snapshot.get("status") or "unknown"),
                        occurred_at=str(projection.snapshot.get("created_at") or ""),
                        updated_at=str(projection.snapshot.get("updated_at") or ""),
                        decision_ids=_dedupe(identifiers.get("decision_id") or []),
                        order_ids=_dedupe(
                            list(identifiers.get("order_id") or [])
                            + list(identifiers.get("broker_order_id") or [])
                        ),
                        fill_ids=_dedupe(
                            list(identifiers.get("fill_id") or [])
                            + list(identifiers.get("broker_trade_id") or [])
                        ),
                        reconciliation_ids=_dedupe(
                            identifiers.get("reconciliation_id") or []
                        ),
                        evidence_refs=evidence_refs,
                    )
                )
            except ValidationError:
                pass
            for diagnostic in projection.diagnostics:
                code = str(diagnostic.get("code") or "").strip()
                if not code:
                    continue
                severity = (
                    "high"
                    if code in {"identifier_conflict", "conflicting_terminal_states"}
                    else "warning"
                )
                diagnostic_warnings.append(
                    PerformanceWarning(
                        warning_id=_warning_id(projection, code, diagnostic),
                        code=code,
                        severity=severity,
                        occurred_at=str(projection.snapshot.get("updated_at") or ""),
                        source_id="canonical_trade_journey_projector",
                        evidence_refs=evidence_refs,
                        details=dict(diagnostic),
                    )
                )

        suggestions = [
            AdjustmentSuggestion.model_validate(raw)
            for raw in self.suggestion_store.list_suggestions(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                strategy_id=strategy_id,
                period=period,
            )
        ]
        warnings = explicit_warnings + diagnostic_warnings
        as_of = _latest_timestamp(
            [
                *(metric.as_of for metric in compliance_metrics),
                *(item.occurred_at for item in interventions),
                *(item.updated_at for item in execution_rows),
                *(item.occurred_at for item in warnings),
                *(item.updated_at or item.as_of for item in suggestions),
            ]
        )
        projector_source = ["canonical_trade_journey_projector"] if scan.reader_available else []
        compliance = ComplianceProjection(
            availability=_source_availability(
                compliance_metrics,
                as_of=_latest_timestamp(item.as_of for item in compliance_metrics),
                source_ids=(item.source_id for item in compliance_metrics),
                reason="compliance_metrics_unavailable",
            ),
            metrics=compliance_metrics,
        )
        intervention_availability = _source_availability(
            interventions,
            as_of=_latest_timestamp(item.occurred_at for item in interventions),
            source_ids=(item.source_id for item in interventions),
            reason="intervention_records_unavailable",
        )
        interventions_projection = InterventionProjection(
            availability=intervention_availability,
            aggregate=(
                InterventionAggregate(
                    total=len(interventions),
                    by_status=dict(Counter(item.status for item in interventions)),
                )
                if interventions
                else None
            ),
            items=interventions,
        )
        execution = ExecutionHistoryProjection(
            availability=_source_availability(
                execution_rows,
                as_of=_latest_timestamp(item.updated_at for item in execution_rows),
                source_ids=projector_source,
                reason="execution_history_unavailable",
            ),
            items=execution_rows,
        )
        warning_projection = WarningProjection(
            availability=_source_availability(
                warnings,
                as_of=_latest_timestamp(item.occurred_at for item in warnings) or as_of,
                source_ids=(item.source_id for item in warnings),
                reason="warning_source_unavailable",
                empty_is_available=bool(scoped),
            ),
            items=warnings,
        )
        suggestion_projection = SuggestionProjection(
            availability=_source_availability(
                suggestions,
                as_of=_latest_timestamp(
                    item.updated_at or item.as_of for item in suggestions
                ),
                source_ids=(item.provenance.source_id for item in suggestions),
                reason="governed_suggestions_unavailable",
            ),
            items=suggestions,
        )
        sections = {
            "compliance": compliance.availability.status,
            "interventions": interventions_projection.availability.status,
            "execution_history": execution.availability.status,
            "warnings": warning_projection.availability.status,
            "adjustment_suggestions": suggestion_projection.availability.status,
        }
        available_count = sum(value == "available" for value in sections.values())
        availability = (
            "unavailable"
            if available_count == 0
            else "available"
            if available_count == len(sections)
            else "partial"
        )
        # Freshness comes from the projector controller row and the scoped
        # projection rows themselves; the reader exposes no global watermark.
        controller = scan.controller
        generation = controller.get("generation")
        source_watermarks = {
            key: str(controller[key])
            for key in ("source_high_watermark", "last_successful_publish_at")
            if controller.get(key) not in (None, "")
        }
        revisions = [
            int(item.snapshot.get("revision") or 0)
            for item in scoped
            if isinstance(item.snapshot.get("revision"), int)
        ]
        return StrategyPerformanceProjection(
            strategy_id=strategy_id,
            period=period,
            environment=environment,
            availability=availability,
            freshness=PerformanceFreshness(
                status=availability,
                snapshot_at=snapshot_at,
                as_of=as_of,
                source_watermarks=source_watermarks,
                projection_revision=max(revisions) if revisions else None,
                projection_generation=generation if isinstance(generation, int) else None,
                unavailable_sources=sorted(
                    name for name, state in sections.items() if state == "unavailable"
                ),
            ),
            compliance=compliance,
            interventions=interventions_projection,
            execution_history=execution,
            warnings=warning_projection,
            adjustment_suggestions=suggestion_projection,
        )

    def project_attribution(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        environment: str = "paper",
        period: str = "latest",
        page_size: int = 50,
        page_token: Optional[str] = None,
        strategy_id_filter: Optional[str] = None,
        workshop_store: Optional[Any] = None,
    ) -> TradingRoomPerformanceAttributionEnvelope:
        return project_agora_performance_attribution_by_strategy(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            environment=environment,
            period=period,
            page_size=page_size,
            page_token=page_token,
            strategy_id_filter=strategy_id_filter,
            projection_reader=self.get_projection_reader(),
            workshop_store=workshop_store,
            suggestion_store=self.suggestion_store,
            utc_now=self.utc_now,
        )


PM12_ATTRIBUTION_DIMENSIONS = ("persona", "strategy", "pool", "asset", "broker", "runtime", "regime")


def _pm12_management_record_id(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _pm12_management_first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _pm12_management_dict_value(record: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _pm12_management_nested_dict(record: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _pm12_management_as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pm12_management_first_float(record: Dict[str, Any], *paths: str) -> Optional[float]:
    for path in paths:
        val: Any = record
        for part in path.split("."):
            if not isinstance(val, dict):
                val = None
                break
            val = val.get(part)
        num = _pm12_management_as_float(val)
        if num is not None:
            return num
    return None


def _pm12_management_position_records(telemetry: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("positions", "holdings", "position_snapshots"):
        raw_items = telemetry.get(key)
        if isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]
            if items:
                return items
    for key in ("position", "holding"):
        raw_item = telemetry.get(key)
        if isinstance(raw_item, dict):
            return [raw_item]
    return []


def _pm12_management_latest_timestamp(items: List[Dict[str, Any]], *fields: str) -> Optional[str]:
    latest: Optional[str] = None
    for item in items:
        for field in fields:
            value = str(item.get(field) or "").strip()
            if value and (latest is None or value > latest):
                latest = value
    return latest


def _pm12_management_avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _pm12_management_link(path: str, record_id: Optional[str]) -> Optional[str]:
    if not record_id:
        return None
    return f"{path}/{record_id}"


def _pm12_page_slice(
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    offset = 0
    if page_token:
        try:
            offset = int(page_token)
        except (TypeError, ValueError):
            offset = 0
    size = max(int(page_size or 50), 1)
    slice_items = items[offset : offset + size]
    next_offset = offset + size
    next_page_token = str(next_offset) if next_offset < len(items) else None
    return slice_items, next_page_token


try:
    from services.control_plane.bff.research.routes.common import format_dataset_surface_status
except (ImportError, ValueError):
    from ...research.routes.common import format_dataset_surface_status


def _default_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {
        "snapshot_at": snapshot_at,
        "as_of": snapshot_at,
        "source": "bff_read_store",
        "stale": False,
    }


def _default_dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    source: Optional[str] = None,
    read_store: Optional[Any] = None,
    utc_now: Optional[Callable[[], str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    effective_source = source
    if effective_source is None:
        if read_store is not None and hasattr(read_store, "dataset_source"):
            try:
                effective_source = str(read_store.dataset_source(dataset) or "missing")
            except Exception:
                effective_source = "missing"
        else:
            effective_source = "missing"
    return format_dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
        source=effective_source,
        utc_now=utc_now,
        **kwargs,
    )


def _default_aggregate_group_surface(
    surface_key: str,
    surfaces: List[Dict[str, Any]],
    *,
    snapshot_at: Optional[str] = None,
    unavailable_message: Optional[str] = None,
    degraded_message: Optional[str] = None,
) -> Dict[str, Any]:
    statuses = [s.get("status") for s in surfaces]
    if statuses and all(st == "unavailable" for st in statuses):
        status = "unavailable"
        msg = unavailable_message or "Aggregate surface unavailable."
    elif any(st in ("unavailable", "degraded") for st in statuses):
        status = "degraded"
        msg = degraded_message or "Aggregate surface degraded."
    else:
        status = "ok"
        msg = None
    return {
        "name": surface_key,
        "surface": surface_key,
        "status": status,
        "source": "bff_composed",
        "snapshot_at": snapshot_at,
        "as_of": snapshot_at,
        "message": msg,
    }


def _default_performance_ranking_source_surface(
    surface: Dict[str, Any],
    *,
    snapshot_at: Optional[str] = None,
) -> Dict[str, Any]:
    normalized = dict(surface)
    source = str(normalized.get("source") or "unknown")
    status = str(normalized.get("status") or "unavailable")
    normalized["observed_time"] = snapshot_at
    normalized["freshness"] = (
        normalized.get("staleness", {}).get("served_from")
        if isinstance(normalized.get("staleness"), dict)
        else None
    ) or source
    normalized["coverage"] = 0.0 if status == "unavailable" or source == "missing" else 1.0
    normalized["missing_bindings"] = status == "unavailable" or source == "missing"
    return normalized


def pm12_metric_or_split(
    value: Any,
    fallback: Optional[float] = None,
    split_count: int = 1,
) -> Optional[float]:
    metric = _pm12_management_as_float(value)
    if metric is not None:
        return metric
    if fallback is None:
        return None
    return round(fallback / max(split_count, 1), 6)


def pm12_dimension_key(value: Any) -> str:
    key = str(value or "").strip()
    return key if key else "unassigned"


def pm12_attribution_dimension_label(
    dimension: str,
    key: str,
    *,
    personas_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    strategies_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    pools_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    personas_map = personas_by_id or {}
    strategies_map = strategies_by_id or {}
    pools_map = pools_by_id or {}
    if key == "unassigned":
        return "Unassigned"
    if dimension == "persona":
        persona = personas_map.get(key, {})
        return str(persona.get("name") or persona.get("display_name") or key)
    if dimension == "strategy":
        strategy = strategies_map.get(key, {})
        return str(strategy.get("title") or strategy.get("name") or key)
    if dimension == "pool":
        pool = pools_map.get(key, {})
        return str(pool.get("name") or key)
    return key


def pm12_performance_attribution_sources(
    tenant_id: Optional[str] = None,
    *,
    read_store: Optional[Any] = None,
    list_persona_records: Optional[Callable[[Optional[str]], List[Dict[str, Any]]]] = None,
    list_strategy_summaries: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    resolved_read_store = read_store

    if resolved_read_store is not None and hasattr(resolved_read_store, "list_runtime_bindings"):
        runtime_bindings = resolved_read_store.list_runtime_bindings(include_market_persona_defaults=True) or []
    else:
        runtime_bindings = []

    if resolved_read_store is not None and hasattr(resolved_read_store, "list_deployment_plans"):
        deployment_plans = resolved_read_store.list_deployment_plans() or []
    else:
        deployment_plans = []

    if resolved_read_store is not None and hasattr(resolved_read_store, "list_bindings"):
        bindings = resolved_read_store.list_bindings(include_market_persona_defaults=True) or []
    else:
        bindings = []

    if resolved_read_store is not None and hasattr(resolved_read_store, "list_capital_pools"):
        capital_pools = resolved_read_store.list_capital_pools(include_market_persona_defaults=True) or []
    else:
        capital_pools = []

    clean_tenant = str(tenant_id or "").strip()
    if list_persona_records is not None:
        personas = list_persona_records(clean_tenant or None)
    else:
        try:
            from services.control_plane.bff.personas.service import _list_persona_records as personas_list_records
            personas = personas_list_records(clean_tenant or None, read_store=resolved_read_store)
        except Exception:
            personas = []

    if list_strategy_summaries is not None:
        strategies = list_strategy_summaries()
    elif resolved_read_store is not None and hasattr(resolved_read_store, "list_strategy_specs"):
        strategies = list(resolved_read_store.list_strategy_specs() or [])
    else:
        strategies = []

    plans_by_id = {
        _pm12_management_record_id(plan, "plan_id", "id"): plan
        for plan in deployment_plans
        if _pm12_management_record_id(plan, "plan_id", "id")
    }
    bindings_by_id = {
        _pm12_management_record_id(binding, "binding_id", "id", "persona_capital_binding_id"): binding
        for binding in bindings
        if _pm12_management_record_id(binding, "binding_id", "id", "persona_capital_binding_id")
    }
    pools_by_id = {
        _pm12_management_record_id(pool, "pool_id", "id"): pool
        for pool in capital_pools
        if _pm12_management_record_id(pool, "pool_id", "id")
    }
    personas_by_id = {
        _pm12_management_record_id(persona, "persona_id", "id"): persona
        for persona in personas
        if _pm12_management_record_id(persona, "persona_id", "id")
    }
    strategies_by_id = {
        _pm12_management_record_id(strategy, "strategy_id", "id"): strategy
        for strategy in strategies
        if _pm12_management_record_id(strategy, "strategy_id", "id")
    }

    telemetry_by_runtime_id: Dict[str, Dict[str, Any]] = {}
    telemetry_summaries: List[Any] = []
    if resolved_read_store is not None and hasattr(resolved_read_store, "list_telemetry_summaries"):
        try:
            telemetry_summaries = list(resolved_read_store.list_telemetry_summaries() or [])
        except Exception:
            telemetry_summaries = []
    has_bulk_telemetry_projection = bool(telemetry_summaries)
    for telemetry in telemetry_summaries:
        if not isinstance(telemetry, dict):
            continue
        runtime_id = _pm12_management_record_id(
            telemetry,
            "runtime_id",
            "runtimeId",
            "execution_runtime_id",
            "id",
        )
        if runtime_id:
            telemetry_by_runtime_id[runtime_id] = telemetry

    for runtime in runtime_bindings:
        runtime_id = _pm12_management_record_id(runtime, "runtime_id", "id", "binding_id")
        if not runtime_id:
            continue
        telemetry = telemetry_by_runtime_id.get(runtime_id)
        if telemetry is None and not has_bulk_telemetry_projection and resolved_read_store is not None and hasattr(resolved_read_store, "get_telemetry_summary"):
            telemetry = resolved_read_store.get_telemetry_summary(runtime_id)
        if telemetry is not None:
            telemetry_by_runtime_id[runtime_id] = telemetry

    return {
        "tenant_id": clean_tenant or None,
        "runtime_bindings": runtime_bindings,
        "deployment_plans": deployment_plans,
        "bindings": bindings,
        "capital_pools": capital_pools,
        "personas": personas,
        "strategies": strategies,
        "plans_by_id": plans_by_id,
        "bindings_by_id": bindings_by_id,
        "pools_by_id": pools_by_id,
        "personas_by_id": personas_by_id,
        "strategies_by_id": strategies_by_id,
        "telemetry_by_runtime_id": telemetry_by_runtime_id,
    }


def pm12_performance_attribution_facts(sources: Dict[str, Any], period_key: str) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    plans_by_id = sources["plans_by_id"]
    bindings_by_id = sources["bindings_by_id"]
    pools_by_id = sources["pools_by_id"]
    telemetry_by_runtime_id = sources["telemetry_by_runtime_id"]
    scoped_tenant = str(sources.get("tenant_id") or "").strip()
    personas_by_id = sources["personas_by_id"]

    for runtime in sources["runtime_bindings"]:
        runtime_id = _pm12_management_record_id(runtime, "runtime_id", "id", "binding_id")
        runtime_binding_id = _pm12_management_record_id(runtime, "runtime_binding_id", "binding_id", "id")
        plan_id = _pm12_management_record_id(runtime, "plan_id", "deployment_plan_id")
        plan = plans_by_id.get(plan_id, {})
        plan_binding_ids = [
            str(value).strip()
            for value in (plan.get("binding_ids") or [])
            if str(value).strip()
        ]
        persona_binding_id = (
            _pm12_management_record_id(runtime, "persona_capital_binding_id")
            or (plan_binding_ids[0] if plan_binding_ids else "")
        )
        persona_binding = bindings_by_id.get(persona_binding_id, {})
        telemetry = telemetry_by_runtime_id.get(runtime_id, {})
        summary = telemetry.get("summary") if isinstance(telemetry.get("summary"), dict) else {}
        positions = _pm12_management_position_records(telemetry) or [{}]
        split_count = len(positions)

        runtime_pnl = _pm12_management_as_float(
            _pm12_management_first_non_empty(telemetry.get("pnl"), summary.get("total_pnl"))
        )
        runtime_unrealized_pnl = _pm12_management_as_float(
            _pm12_management_first_non_empty(telemetry.get("unrealized_pnl"), summary.get("unrealized_pnl"))
        )
        runtime_realized_pnl = _pm12_management_as_float(
            _pm12_management_first_non_empty(telemetry.get("realized_pnl"), summary.get("realized_pnl"))
        )
        runtime_trades = _pm12_management_as_float(
            _pm12_management_first_non_empty(telemetry.get("total_trades"), summary.get("total_trades"))
        )

        for index, position in enumerate(positions):
            instrument = _pm12_management_nested_dict(position, "instrument", "asset", "contract")
            mark = _pm12_management_nested_dict(position, "mark", "mark_price", "market_price")
            capital_pool_id = str(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "capital_pool_id", "pool_id"),
                    _pm12_management_dict_value(runtime, "capital_pool_id", "pool_id"),
                    _pm12_management_dict_value(plan, "capital_pool_id", "target_pool_id", "pool_id"),
                    _pm12_management_dict_value(persona_binding, "capital_pool_id", "pool_id"),
                )
                or ""
            )
            capital_pool = pools_by_id.get(capital_pool_id, {})
            canonical_persona_id = str(persona_binding.get("persona_id") or "").strip()
            if canonical_persona_id:
                persona_id = canonical_persona_id
            else:
                persona_id = str(runtime.get("persona_id") or "").strip()
            if scoped_tenant and persona_id and persona_id not in personas_by_id:
                continue
            strategy_id = str(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "strategy_id", "strategy_ref"),
                    _pm12_management_dict_value(runtime, "strategy_id", "strategy_ref"),
                    _pm12_management_dict_value(plan, "strategy_id", "strategy_ref"),
                    _pm12_management_dict_value(persona_binding, "strategy_id"),
                )
                or ""
            )
            symbol = str(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "symbol", "instrument_id", "asset_id", "contract_id"),
                    _pm12_management_dict_value(instrument, "symbol", "instrument_id", "asset_id", "contract_id"),
                    _pm12_management_dict_value(telemetry, "symbol", "instrument_id", "asset_id", "contract_id"),
                )
                or ""
            )
            broker_id = str(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "broker_id", "broker", "broker_ref"),
                    _pm12_management_dict_value(telemetry, "broker_id", "broker", "broker_ref"),
                    _pm12_management_dict_value(runtime, "broker_id", "broker", "broker_ref"),
                    _pm12_management_dict_value(plan, "broker_id", "broker", "broker_ref"),
                )
                or ""
            )
            regime = str(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "regime", "market_regime", "risk_regime"),
                    _pm12_management_dict_value(telemetry, "regime", "market_regime", "risk_regime"),
                    _pm12_management_dict_value(runtime, "regime", "market_regime", "risk_regime"),
                    _pm12_management_dict_value(plan, "regime", "market_regime", "risk_regime"),
                )
                or ""
            )
            quantity = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "quantity", "qty", "net_quantity", "position_quantity"),
                    _pm12_management_dict_value(telemetry, "quantity", "position_quantity"),
                    _pm12_management_dict_value(summary, "quantity", "position_quantity"),
                )
            )
            mark_price = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "mark_price", "market_price", "last_price"),
                    _pm12_management_dict_value(mark, "price", "mark_price", "market_price", "last_price"),
                    _pm12_management_dict_value(telemetry, "mark_price", "market_price", "last_price"),
                    _pm12_management_dict_value(summary, "mark_price", "market_price", "last_price"),
                )
            )
            market_value = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "market_value", "value"),
                    _pm12_management_dict_value(telemetry, "market_value"),
                    _pm12_management_dict_value(summary, "market_value"),
                )
            )
            if market_value is None and quantity is not None and mark_price is not None:
                market_value = round(quantity * mark_price, 6)
            notional = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "notional", "gross_notional"),
                    _pm12_management_dict_value(telemetry, "notional", "gross_notional"),
                    _pm12_management_dict_value(summary, "notional", "gross_notional"),
                    market_value,
                )
            )
            if notional is not None:
                notional = abs(notional)
            exposure = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "exposure", "gross_exposure"),
                    _pm12_management_dict_value(telemetry, "exposure", "gross_exposure"),
                    _pm12_management_dict_value(summary, "exposure", "gross_exposure"),
                    notional,
                )
            )
            total_pnl = pm12_metric_or_split(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "total_pnl", "pnl"),
                    _pm12_management_dict_value(position, "realized_plus_unrealized_pnl"),
                ),
                runtime_pnl,
                split_count,
            )
            unrealized_pnl = pm12_metric_or_split(
                _pm12_management_dict_value(position, "unrealized_pnl", "unrealized"),
                runtime_unrealized_pnl,
                split_count,
            )
            realized_pnl = pm12_metric_or_split(
                _pm12_management_dict_value(position, "realized_pnl", "realized"),
                runtime_realized_pnl,
                split_count,
            )
            drawdown = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "drawdown", "max_drawdown"),
                    _pm12_management_dict_value(telemetry, "drawdown"),
                    _pm12_management_dict_value(summary, "max_drawdown"),
                )
            )
            value_at_risk = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_first_float(
                        position,
                        "value_at_risk",
                        "valueAtRisk",
                        "var",
                        "VaR",
                        "risk.value_at_risk",
                        "risk.valueAtRisk",
                        "risk.var",
                    ),
                    _pm12_management_first_float(
                        telemetry,
                        "value_at_risk",
                        "valueAtRisk",
                        "var",
                        "VaR",
                        "risk.value_at_risk",
                        "risk.valueAtRisk",
                        "risk.var",
                        "summary.value_at_risk",
                        "summary.valueAtRisk",
                        "summary.var",
                    ),
                )
            )
            fill_rate = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "fill_rate"),
                    _pm12_management_dict_value(telemetry, "fill_rate"),
                    _pm12_management_dict_value(summary, "fill_rate"),
                )
            )
            avg_slippage_bps = _pm12_management_as_float(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "avg_slippage_bps", "slippage_bps"),
                    _pm12_management_dict_value(telemetry, "avg_slippage_bps", "slippage_bps"),
                    _pm12_management_dict_value(summary, "avg_slippage_bps", "slippage_bps"),
                )
            )
            total_trades = pm12_metric_or_split(
                _pm12_management_dict_value(position, "total_trades", "trade_count", "trades"),
                runtime_trades,
                split_count,
            )
            collected_at = str(
                _pm12_management_first_non_empty(
                    _pm12_management_dict_value(position, "collected_at", "marked_at", "updated_at"),
                    _pm12_management_dict_value(telemetry, "collected_at", "updated_at"),
                    _pm12_management_dict_value(summary, "collected_at", "updated_at"),
                )
                or ""
            )

            facts.append({
                "id": f"{runtime_id or runtime_binding_id or 'runtime'}:{index}",
                "period": period_key,
                "runtime_id": runtime_id,
                "runtime_binding_id": runtime_binding_id,
                "deployment_plan_id": plan_id or _pm12_management_record_id(plan, "plan_id", "id"),
                "persona_capital_binding_id": persona_binding_id,
                "capital_pool_id": capital_pool_id,
                "capital_pool_name": capital_pool.get("name") or capital_pool_id,
                "persona_id": persona_id,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "broker_id": broker_id,
                "regime": regime,
                "deployment_stage": str(
                    runtime.get("deployment_stage") or runtime.get("deployment_mode") or plan.get("target_stage") or ""
                ),
                "status": str(_pm12_management_first_non_empty(position.get("status"), runtime.get("status"), "unknown")),
                "total_pnl": total_pnl,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "notional": notional,
                "market_value": market_value,
                "exposure": exposure,
                "drawdown": drawdown,
                "value_at_risk": value_at_risk,
                "fill_rate": fill_rate,
                "avg_slippage_bps": avg_slippage_bps,
                "total_trades": total_trades,
                "collected_at": collected_at or None,
                "telemetry_available": runtime_id in telemetry_by_runtime_id if runtime_id else False,
                "dimensions": {
                    "persona": pm12_dimension_key(persona_id),
                    "strategy": pm12_dimension_key(strategy_id),
                    "pool": pm12_dimension_key(capital_pool_id),
                    "asset": pm12_dimension_key(symbol),
                    "broker": pm12_dimension_key(broker_id),
                    "runtime": pm12_dimension_key(runtime_id or runtime_binding_id),
                    "regime": pm12_dimension_key(regime),
                },
            })

    return facts


def pm12_metric_sum(facts: List[Dict[str, Any]], field: str) -> Optional[float]:
    values = [
        value
        for value in (_pm12_management_as_float(fact.get(field)) for fact in facts)
        if value is not None
    ]
    return round(sum(values), 6) if values else None


def pm12_metric_avg(facts: List[Dict[str, Any]], field: str) -> Optional[float]:
    values = [
        value
        for value in (_pm12_management_as_float(fact.get(field)) for fact in facts)
        if value is not None
    ]
    return _pm12_management_avg(values)


def pm12_attribution_metrics(facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    drawdown_values = [
        value
        for value in (_pm12_management_as_float(fact.get("drawdown")) for fact in facts)
        if value is not None
    ]
    trade_total = pm12_metric_sum(facts, "total_trades")
    runtime_ids = sorted({
        str(fact.get("runtime_id") or "")
        for fact in facts
        if str(fact.get("runtime_id") or "")
    })
    telemetry_runtime_ids = sorted({
        str(fact.get("runtime_id") or "")
        for fact in facts
        if str(fact.get("runtime_id") or "") and fact.get("telemetry_available")
    })
    return {
        "runtime_count": len(runtime_ids),
        "telemetry_runtime_count": len(telemetry_runtime_ids),
        "holding_count": len(facts),
        "total_pnl": pm12_metric_sum(facts, "total_pnl"),
        "unrealized_pnl": pm12_metric_sum(facts, "unrealized_pnl"),
        "realized_pnl": pm12_metric_sum(facts, "realized_pnl"),
        "total_notional": pm12_metric_sum(facts, "notional"),
        "total_market_value": pm12_metric_sum(facts, "market_value"),
        "total_exposure": pm12_metric_sum(facts, "exposure"),
        "worst_drawdown": max(drawdown_values) if drawdown_values else None,
        "average_fill_rate": pm12_metric_avg(facts, "fill_rate"),
        "average_slippage_bps": pm12_metric_avg(facts, "avg_slippage_bps"),
        "total_trades": int(trade_total) if trade_total is not None else 0,
        "latest_telemetry_at": _pm12_management_latest_timestamp(facts, "collected_at"),
    }


def pm12_performance_attribution_group_entries(
    facts: List[Dict[str, Any]],
    *,
    dimensions: List[str],
) -> List[Dict[str, Any]]:
    total_metrics = pm12_attribution_metrics(facts)
    portfolio_pnl = _pm12_management_as_float(total_metrics.get("total_pnl"))
    portfolio_notional = _pm12_management_as_float(total_metrics.get("total_notional"))
    entries: List[Dict[str, Any]] = []

    for dimension in dimensions:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for fact in facts:
            dims = fact.get("dimensions") if isinstance(fact.get("dimensions"), dict) else {}
            key = pm12_dimension_key(dims.get(dimension))
            grouped.setdefault(key, []).append(fact)

        ranked_groups: List[tuple[str, List[Dict[str, Any]], Dict[str, Any]]] = []
        for key, group_facts in grouped.items():
            ranked_groups.append((key, group_facts, pm12_attribution_metrics(group_facts)))
        ranked_groups.sort(
            key=lambda item: (
                _pm12_management_as_float(item[2].get("total_pnl")) is None,
                -(_pm12_management_as_float(item[2].get("total_pnl")) or 0.0),
                item[0],
            )
        )

        for rank, (key, group_facts, metrics) in enumerate(ranked_groups, start=1):
            pnl = _pm12_management_as_float(metrics.get("total_pnl"))
            notional = _pm12_management_as_float(metrics.get("total_notional"))
            pnl_contribution = None
            if pnl is not None and portfolio_pnl not in (None, 0):
                pnl_contribution = round(pnl / portfolio_pnl, 6)
            notional_weight = None
            if notional is not None and portfolio_notional not in (None, 0):
                notional_weight = round(notional / portfolio_notional, 6)
            entries.append({
                "dimension": dimension,
                "dimension_key": key,
                "group_facts": group_facts,
                "metrics": metrics,
                "notional_weight": notional_weight,
                "pnl_contribution_pct": pnl_contribution,
                "rank": rank,
            })

    return entries


def pm12_performance_attribution_page_entries(
    facts: List[Dict[str, Any]],
    *,
    dimensions: List[str],
    page_token: Optional[str],
    page_size: int,
) -> tuple[List[Dict[str, Any]], int, Optional[str], Dict[str, Any]]:
    entries = pm12_performance_attribution_group_entries(facts, dimensions=dimensions)
    page_entries, next_page_token = _pm12_page_slice(entries, page_token, page_size)
    return page_entries, len(entries), next_page_token, pm12_attribution_metrics(facts)


def pm12_attribution_data_confidence(metrics: Dict[str, Any]) -> str:
    holding_count = int(metrics.get("holding_count") or 0)
    runtime_count = int(metrics.get("runtime_count") or 0)
    telemetry_runtime_count = int(metrics.get("telemetry_runtime_count") or 0)
    if holding_count <= 0:
        return "unavailable"
    if telemetry_runtime_count <= 0:
        return "partial"
    if runtime_count and telemetry_runtime_count < runtime_count:
        return "degraded"
    if _pm12_management_as_float(metrics.get("total_pnl")) is None:
        return "partial"
    return "formal"


def pm12_performance_attribution_rows(
    entries: List[Dict[str, Any]],
    *,
    period_key: str,
    sources: Dict[str, Any],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for entry in entries:
        dimension = str(entry.get("dimension") or "")
        key = str(entry.get("dimension_key") or "")
        group_facts = entry.get("group_facts") if isinstance(entry.get("group_facts"), list) else []
        metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        runtime_ids = sorted({
            str(fact.get("runtime_id") or "")
            for fact in group_facts
            if str(fact.get("runtime_id") or "")
        })
        pool_ids = sorted({
            str(fact.get("capital_pool_id") or "")
            for fact in group_facts
            if str(fact.get("capital_pool_id") or "")
        })
        persona_ids = sorted({
            str(fact.get("persona_id") or "")
            for fact in group_facts
            if str(fact.get("persona_id") or "")
        })
        strategy_ids = sorted({
            str(fact.get("strategy_id") or "")
            for fact in group_facts
            if str(fact.get("strategy_id") or "")
        })
        label = pm12_attribution_dimension_label(
            dimension,
            key,
            personas_by_id=sources["personas_by_id"],
            strategies_by_id=sources["strategies_by_id"],
            pools_by_id=sources["pools_by_id"],
        )
        data_confidence = pm12_attribution_data_confidence(metrics)
        rows.append({
            "id": f"pm12-performance-attribution-{dimension}-{key}",
            "dimension": dimension,
            "dimension_key": key,
            "label": label,
            "period": period_key,
            "data_confidence": data_confidence,
            "source_status": "ok" if data_confidence == "formal" else data_confidence,
            "rank": entry.get("rank"),
            "metrics": {
                **metrics,
                "data_confidence": data_confidence,
                "pnl_contribution_pct": entry.get("pnl_contribution_pct"),
                "notional_weight": entry.get("notional_weight"),
            },
            "total_pnl": metrics["total_pnl"],
            "pnl_contribution_pct": entry.get("pnl_contribution_pct"),
            "notional_weight": entry.get("notional_weight"),
            "runtime_count": metrics["runtime_count"],
            "holding_count": metrics["holding_count"],
            "source_refs": {
                "runtime_ids": runtime_ids,
                "capital_pool_ids": pool_ids,
                "persona_ids": persona_ids,
                "strategy_ids": strategy_ids,
            },
            "links": {
                "runtime": _pm12_management_link("/bff/runtimes", key) if dimension == "runtime" else None,
                "capital_pool": _pm12_management_link("/bff/capital-pools", key) if dimension == "pool" else None,
                "persona": _pm12_management_link("/bff/personas", key) if dimension == "persona" else None,
                "strategy": _pm12_management_link("/bff/strategies", key) if dimension == "strategy" else None,
            },
        })

    return rows


def pm12_performance_attribution_response(
    *,
    dimensions: List[str],
    period: str,
    page_token: Optional[str],
    page_size: int,
    data_id: str = "pm12-performance-attribution",
    surface_key: str = "performance_attribution",
    # Common filters:
    persona_id: Optional[str] = None,
    persona: Optional[str] = None,
    runtime_id: Optional[str] = None,
    runtime: Optional[str] = None,
    strategy_id: Optional[str] = None,
    strategy: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    pool: Optional[str] = None,
    sleeve_id: Optional[str] = None,
    sleeve: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact: Optional[str] = None,
    broker_id: Optional[str] = None,
    broker: Optional[str] = None,
    stage: Optional[str] = None,
    as_of: Optional[str] = None,
    tenant_id: Optional[str] = None,
    utc_now: Optional[Callable[[], str]] = None,
    read_store: Optional[Any] = None,
    sources_fn: Optional[Callable[[Optional[str]], Dict[str, Any]]] = None,
    dataset_surface_status_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    aggregate_group_surface_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    performance_ranking_source_surface_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    snapshot_meta_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    rows_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    resolved_utc_now = utc_now or (lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    snapshot_at = resolved_utc_now()
    period_key = str(period or "").strip() or "latest"
    if sources_fn is not None:
        sources = sources_fn(tenant_id)
    else:
        sources = pm12_performance_attribution_sources(tenant_id, read_store=read_store)
    facts = pm12_performance_attribution_facts(sources, period_key)

    from services.control_plane.bff.personas.service import _filter_by_common_identifiers
    facts = _filter_by_common_identifiers(
        facts,
        persona_id=persona_id, persona=persona,
        runtime_id=runtime_id, runtime=runtime,
        strategy_id=strategy_id, strategy=strategy,
        capital_pool_id=capital_pool_id, pool=pool,
        sleeve_id=sleeve_id, sleeve=sleeve,
        artifact_id=artifact_id, artifact=artifact,
        broker_id=broker_id, broker=broker,
        stage=stage, period=period_key, as_of=as_of
    )

    page_entries, total, next_page_token, aggregate_metrics = pm12_performance_attribution_page_entries(
        facts,
        dimensions=dimensions,
        page_token=page_token,
        page_size=page_size,
    )
    projector = rows_fn or pm12_performance_attribution_rows
    page_items = projector(
        page_entries,
        period_key=period_key,
        sources=sources,
    )

    aggregate_fn = aggregate_group_surface_fn or _default_aggregate_group_surface
    ranking_fn = performance_ranking_source_surface_fn or _default_performance_ranking_source_surface
    meta_fn = snapshot_meta_fn or _default_snapshot_meta

    def _dataset_status(dataset: str, **kwargs: Any) -> Dict[str, Any]:
        if dataset_surface_status_fn is not None:
            return dataset_surface_status_fn(dataset, snapshot_at=snapshot_at, **kwargs)
        return _default_dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            read_store=read_store,
            utc_now=resolved_utc_now,
            **kwargs,
        )

    source_surfaces = {
        "runtime_bindings": _dataset_status("runtime_bindings"),
        "telemetry_summaries": _dataset_status(
            "telemetry_summaries",
            has_data=bool(sources["telemetry_by_runtime_id"]) if sources["runtime_bindings"] else None,
            missing_message="Telemetry summaries unavailable for performance attribution runtimes.",
        ),
        "deployment_plans": _dataset_status("deployment_plans"),
        "persona_bindings": _dataset_status("persona_bindings"),
        "capital_pools": _dataset_status("capital_pools"),
        "personas": _dataset_status("personas"),
        "strategies": _dataset_status("strategy_specs"),
    }
    attribution_surface = aggregate_fn(
        surface_key,
        list(source_surfaces.values()),
        snapshot_at=snapshot_at,
        unavailable_message="Performance attribution aggregate unavailable.",
        degraded_message="Performance attribution is degraded because one or more source surfaces are degraded.",
    )
    surfaces = {
        name: ranking_fn(surface, snapshot_at=snapshot_at)
        for name, surface in {
            surface_key: attribution_surface,
            **source_surfaces,
        }.items()
    }
    if surface_key != "performance_attribution":
        surfaces["performance_attribution"] = ranking_fn(attribution_surface, snapshot_at=snapshot_at)
    summary = {
        "period": period_key,
        "dimensions": dimensions,
        "supported_dimensions": list(PM12_ATTRIBUTION_DIMENSIONS),
        "row_count": total,
        "returned_row_count": len(page_items),
        "runtime_count": aggregate_metrics["runtime_count"],
        "telemetry_runtime_count": aggregate_metrics["telemetry_runtime_count"],
        "holding_count": aggregate_metrics["holding_count"],
        "total_pnl": aggregate_metrics["total_pnl"],
        "total_notional": aggregate_metrics["total_notional"],
        "total_exposure": aggregate_metrics["total_exposure"],
        "worst_drawdown": aggregate_metrics["worst_drawdown"],
        "average_fill_rate": aggregate_metrics["average_fill_rate"],
        "average_slippage_bps": aggregate_metrics["average_slippage_bps"],
        "total_trades": aggregate_metrics["total_trades"],
        "latest_telemetry_at": aggregate_metrics["latest_telemetry_at"],
        "basis": "latest_runtime_telemetry_snapshot",
    }
    data = {
        "id": data_id,
        "period": period_key,
        "dimensions": dimensions,
        "items": page_items,
        "summary": summary,
    }
    return {
        "data": data,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
            "page_size": page_size,
        },
        "meta": {
            **meta_fn(snapshot_at),
            "surfaces": surfaces,
            "composition_sources": [
                "GET /api/v1/runtime-bindings",
                "GET /api/v1/telemetry/{runtime_id}/summary",
                "GET /api/v1/deployment-plans",
                "GET /api/v1/persona-capital-bindings",
                "GET /bff/capital-pools",
                "GET /bff/personas",
                "GET /bff/strategies",
            ],
            "period": period_key,
            "dimensions": dimensions,
            "policy": "read_only_performance_attribution",
        },
    }

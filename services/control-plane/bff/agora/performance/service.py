"""Truth-preserving Strategy Performance projection service."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from pydantic import ValidationError

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
    identifiers = projection.snapshot.get("identifiers") or {}
    values = identifiers.get("strategy_id") or []
    return str(values[0]) if len(values) == 1 else ""


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
        get_trade_journey_store: Callable[[], Any],
        utc_now: Callable[[], str],
    ) -> None:
        self.suggestion_store = suggestion_store
        self.get_trade_journey_store = get_trade_journey_store
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
        journey_store = self.get_trade_journey_store()
        materializer = journey_store.materializer() if journey_store is not None else None
        projection_metadata = (
            journey_store.projection_metadata()
            if journey_store is not None
            and callable(getattr(journey_store, "projection_metadata", None))
            else {}
        )
        scoped: List[Any] = []
        if materializer is not None:
            scoped = [
                projection
                for projection in materializer.projections
                if projection.tenant_id == tenant_id
                and projection.environment == environment
                and _projection_strategy_id(projection) == strategy_id
                and _projection_visible_to_user(projection, owner_user_id)
                and _timestamp_in_period(
                    projection.snapshot.get("updated_at"), period=period, now=now
                )
            ]
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
        projector_source = ["canonical_trade_journey_projector"] if materializer else []
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
        controller = (
            projection_metadata.get("controller")
            if isinstance(projection_metadata, Mapping)
            and isinstance(projection_metadata.get("controller"), Mapping)
            else {}
        )
        generation = (
            projection_metadata.get("generation")
            if isinstance(projection_metadata, Mapping)
            else None
        )
        return StrategyPerformanceProjection(
            strategy_id=strategy_id,
            period=period,
            environment=environment,
            availability=availability,
            freshness=PerformanceFreshness(
                status=availability,
                snapshot_at=snapshot_at,
                as_of=as_of,
                source_watermarks=(
                    dict(materializer.source_watermarks) if materializer else {}
                ),
                projection_revision=(materializer.revision if materializer else None),
                projection_generation=(
                    generation if isinstance(generation, int) else controller.get("generation")
                ),
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
        period: str = "latest",
        page_size: int = 50,
        page_token: Optional[str] = None,
        strategy_id_filter: Optional[str] = None,
        workshop_store: Optional[Any] = None,
    ) -> TradingRoomPerformanceAttributionEnvelope:
        return project_agora_performance_attribution_by_strategy(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            period=period,
            page_size=page_size,
            page_token=page_token,
            strategy_id_filter=strategy_id_filter,
            journey_store=self.get_trade_journey_store(),
            workshop_store=workshop_store,
            suggestion_store=self.suggestion_store,
            utc_now=self.utc_now,
        )

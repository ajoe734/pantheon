"""Agora trading room common models, context, and projections."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...dashboard.router import (
    _FORBIDDEN_INTERACTIONS,
    _REGISTRY_VERSION,
    _WIDGET_REGISTRY,
    _validate_widget_spec as _validate_registry_widget_spec,
)
from integrations.openclaw.skills.agora.trading_room_workspace import (
    WINNER_BRANCH_VIEW_IDS as _GENERATOR_WINNER_BRANCH_VIEW_IDS,
    WorkspaceGenerationInput,
    WorkspaceGenerationResult,
    generate_trading_room_workspace_proposal,
)
from ..store import TradingRoomStore, make_trading_room_store


# ---------------------------------------------------------------------------
# Module-level store (singleton per process)
# ---------------------------------------------------------------------------

_store: Optional[TradingRoomStore] = None

_TR_SSE_BUFFER_SIZE = 500
_trading_room_sse_buffers: Dict[str, deque] = {}
_trading_room_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}


def _tr_scope_key(scope: Dict[str, str]) -> str:
    return f"{scope['tenant_id']}:{scope['user_id']}"


def _tr_event_id() -> str:
    return f"trevt-{uuid.uuid4().hex[:16]}"


def _tr_sse_format(event: Dict[str, Any]) -> str:
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def _tr_buffer(scope_key: str) -> deque:
    return _trading_room_sse_buffers.setdefault(scope_key, deque(maxlen=_TR_SSE_BUFFER_SIZE))


def _tr_subscribers(scope_key: str) -> List[asyncio.Queue]:
    return _trading_room_sse_subscribers.setdefault(scope_key, [])


def _tr_publish(scope: Dict[str, str], event_type: str, data: Dict[str, Any], timestamp: str) -> str:
    scope_key = _tr_scope_key(scope)
    event_id = _tr_event_id()
    event = {
        "id": event_id,
        "type": event_type,
        "timestamp": timestamp,
        "data": {"scope": {"tenant_id": scope["tenant_id"], "user_id": scope["user_id"]}, **data},
    }
    _tr_buffer(scope_key).append((event_id, event))
    for queue in list(_tr_subscribers(scope_key)):
        try:
            queue.put_nowait(event)
        except (asyncio.QueueFull, Exception):
            pass
    return event_id


def _tr_replay_after(scope_key: str, last_event_id: str) -> List[Dict[str, Any]]:
    found = False
    replayed: List[Dict[str, Any]] = []
    for event_id, event in _trading_room_sse_buffers.get(scope_key, ()):
        if found:
            replayed.append(event)
        elif event_id == last_event_id:
            found = True
    return replayed


def _get_store() -> TradingRoomStore:
    global _store
    if _store is None:
        _store = make_trading_room_store()
    return _store


# ---------------------------------------------------------------------------
# Pydantic models — aligned field-for-field with v4 schemas
# ---------------------------------------------------------------------------

class EvidenceRef(BaseModel):
    ref_type: str
    ref_id: str = Field(min_length=1)
    summary: Optional[str] = None
    data_cutoff: Optional[str] = None


class ConfidenceAssessment(BaseModel):
    value: float = Field(ge=0, le=1)
    basis: Literal["model", "statistical", "heuristic", "mixed"]
    calibration_state: Literal["calibrated", "partially_calibrated", "uncalibrated"]
    sample_size: Optional[int] = Field(default=None, ge=0)
    source_ref: Optional[str] = None


class ProbabilityForecast(BaseModel):
    target_outcome: str
    horizon: str
    value: float = Field(ge=0, le=1)
    ci_lower: Optional[float] = Field(default=None, ge=0, le=1)
    ci_upper: Optional[float] = Field(default=None, ge=0, le=1)
    model_ref: Optional[str] = None
    as_of: Optional[str] = None


class ExpectedValue(BaseModel):
    horizon: str
    unit: Literal["pct_return", "currency", "risk_units"]
    gross: float
    cost: float
    net: float
    downside: float
    expected_shortfall: Optional[float] = None


class RationaleItem(BaseModel):
    claim: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)


class RiskNote(BaseModel):
    severity: Literal["info", "watch", "warning", "high", "critical"]
    domain: str
    summary: str
    mitigation: Optional[str] = None


class InvalidationState(BaseModel):
    conditions: List[str]
    current_state: Literal["valid", "watch", "invalidated"]
    last_checked_at: Optional[str] = None


class SuggestedSize(BaseModel):
    size_hint: Optional[Literal["small", "medium", "large", "full_position"]] = None
    portfolio_pct: Optional[float] = Field(default=None, ge=0, le=1)
    non_binding: Literal[True] = True


class TriggerInfo(BaseModel):
    rule_id: Optional[str] = None
    summary: Optional[str] = None
    current_value: Optional[Any] = None
    threshold: Optional[Any] = None
    distance_to_trigger: Optional[float] = None


class DecisionEventSubject(BaseModel):
    symbol: str
    asset_class: Optional[str] = None
    venue: Optional[str] = None


class TradingDecisionEvent(BaseModel):
    """Aligned with services/control-plane/specs/agora/v4/trading_decision_event.schema.json."""
    spec_version: Literal["1.0"] = "1.0"
    decision_event_id: str
    dedupe_key: Optional[str] = None
    event_kind: Literal["entry", "add", "reduce", "exit", "review"]
    origin: Literal["strategy_signal", "risk_rule", "position_rule", "servant_analysis", "trader_request"]
    strategy_id: str
    strategy_spec_registry_id: str
    candidate_ref: Optional[str] = None
    position_ref: Optional[str] = None
    subject: DecisionEventSubject
    state: Literal["approaching", "triggered", "pending_review", "decided", "expired", "invalidated", "superseded"]
    trigger: Optional[TriggerInfo] = None
    triggered_at: str
    expires_at: Optional[str] = None
    confidence: ConfidenceAssessment
    probability: ProbabilityForecast
    expected_value: ExpectedValue
    rationale: List[RationaleItem] = Field(min_length=1)
    risk_notes: List[RiskNote] = Field(default_factory=list)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    invalidation: InvalidationState
    suggested_action: Literal["enter", "add", "reduce", "exit", "review", "no_action"]
    suggested_size: Optional[SuggestedSize] = None
    position_snapshot: Optional[Dict[str, Any]] = None
    decision_state: Optional[Literal[
        "pending", "approved_by_trader", "rejected_by_trader",
        "deferred", "expired", "handed_off", "superseded"
    ]] = None
    data_cutoff: Optional[str] = None
    no_order_route_proof: Literal["agora_decision_support_only"] = "agora_decision_support_only"


class PendingEventCounts(BaseModel):
    entry: int = Field(default=0, ge=0)
    add: int = Field(default=0, ge=0)
    reduce: int = Field(default=0, ge=0)
    exit: int = Field(default=0, ge=0)
    review: int = Field(default=0, ge=0)


class TradingRoomStrategy(BaseModel):
    strategy_id: str
    strategy_spec_registry_id: str
    title: str
    readiness_state: Literal["blocked", "conditional", "ready", "stale"]
    dashboard_recipe_id: Optional[str] = None
    monitoring_state: Literal["inactive", "shadow", "paper_requested", "monitoring", "paused"]
    candidate_count: Optional[int] = Field(default=None, ge=0)
    position_count: Optional[int] = Field(default=None, ge=0)
    pending_event_counts: PendingEventCounts = Field(default_factory=PendingEventCounts)
    shadow_status: Optional[str] = None
    performance_summary: Optional[Dict[str, Any]] = None
    staleness_reasons: List[str] = Field(default_factory=list)


class QueueSummary(BaseModel):
    entry: int = Field(ge=0)
    add: int = Field(ge=0)
    reduce: int = Field(ge=0)
    exit: int = Field(ge=0)
    review: int = Field(ge=0)


class RiskSummary(BaseModel):
    state: Literal["normal", "watch", "warning", "critical"]
    summary: Optional[str] = None
    alerts: List[str] = Field(default_factory=list)


class TradingRoomAggregate(BaseModel):
    """Aligned with services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json."""
    spec_version: Literal["1.0"] = "1.0"
    user_scope_ref: str
    strategies: List[TradingRoomStrategy] = Field(default_factory=list)
    queue_summary: QueueSummary
    top_decision_events: List[TradingDecisionEvent] = Field(default_factory=list)
    position_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    risk_summary: RiskSummary
    snapshot_at: str
    data_cutoff: str


class TraderDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "defer", "modify"]
    rationale: Optional[str] = None
    modifications: Optional[Dict[str, Any]] = None


class TradingIntentSubject(BaseModel):
    symbol: str = Field(min_length=1)
    asset_class: Optional[str] = None
    venue: Optional[str] = None
    strategy_ref: Optional[str] = None


class TradingIntent(BaseModel):
    """Aligned with services/control-plane/specs/agora/trading_intent.schema.json."""
    spec_version: Literal["1.0"] = "1.0"
    intent_id: str
    operator_id: str
    persona_id: Optional[str] = None
    session_id: Optional[str] = None
    intent_type: Literal[
        "buy_interest",
        "sell_interest",
        "hold_decision",
        "reduce_exposure",
        "increase_exposure",
        "hedge_intent",
        "exit_intent",
        "entry_interest",
    ]
    direction: Literal["long", "short", "neutral", "reduce", "exit"]
    subject: TradingIntentSubject
    rationale: Optional[str] = None
    size_hint: Optional[Literal["small", "medium", "large", "full_position"]] = None
    timeframe_hint: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    linked_event_ids: List[str] = Field(default_factory=list)
    learning_eligible: bool = True
    no_order_route_proof: Literal["agora_intent_record_only"] = "agora_intent_record_only"
    expressed_at: str
    metadata: Optional[Dict[str, Any]] = None


class GovernedActionProposal(BaseModel):
    action: Optional[Literal["enter", "add", "reduce", "exit", "review"]] = None
    symbol: Optional[str] = None
    direction: Optional[str] = None
    size_hint: Optional[str] = None
    portfolio_pct: Optional[float] = None
    non_binding: Literal[True] = True


class GovernedIntentHandoffRequest(BaseModel):
    """Request body for POST /bff/agora/trading-intents/{intent_id}/handoffs.

    Field-aligned with services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json.
    """
    spec_version: Literal["1.0"] = "1.0"
    handoff_id: str
    intent_id: str
    requested_stage: Literal["shadow", "paper", "canary", "live"]
    handoff_type: Literal["shadow_start", "paper_validation_request", "promotion_review_request"]
    state: Literal["draft", "submitted", "accepted", "rejected", "expired", "withdrawn", "converted"]
    strategy_id: str
    strategy_spec_registry_id: str
    requested_by: Dict[str, Any]
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    no_order_route_proof: Literal["agora_request_only_no_order_route"] = "agora_request_only_no_order_route"
    created_at: str
    target_queue: Optional[Literal["shadow_research", "management_governance", "promotion_review"]] = None
    required_gate_refs: List[str] = Field(default_factory=list)
    action_proposal: Optional[GovernedActionProposal] = None
    rationale: Optional[str] = None
    risk_summary: Optional[str] = None
    management_handoff_ref: Optional[str] = None
    deployment_plan_ref: Optional[str] = None
    runtime_binding_ref: Optional[str] = None
    updated_at: Optional[str] = None
    expires_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Trading Room workspace proposal helpers — V11 dynamic UI contract subset
# ---------------------------------------------------------------------------

_WORKSPACE_CAPABILITY = "agora.trading.v1"

_WINNER_BRANCH_VIEW_IDS = _GENERATOR_WINNER_BRANCH_VIEW_IDS

_VIEW_ALLOWED_FIELDS = frozenset({
    "id",
    "viewKind",
    "title",
    "titleKey",
    "purpose",
    "purposeKey",
    "order",
    "layoutTemplate",
    "thumbnailRef",
    "widgetCount",
    "rationale",
    "rationaleKey",
    "dataAvailability",
    "warnings",
    "warningCodes",
    "widgets",
})

_WIDGET_ALLOWED_FIELDS = frozenset({
    "id",
    "widgetType",
    "title",
    "titleKey",
    "purpose",
    "purposeKey",
    "whyIncluded",
    "whyIncludedKey",
    "dataSource",
    "dataAvailability",
    "query",
    "chartSpec",
    "interactions",
    "placement",
    "minSize",
    "maxSize",
    "sensitivity",
    "visible",
})

_PLACEMENT_ALLOWED_FIELDS = frozenset({
    "x",
    "y",
    "width",
    "height",
    "minWidth",
    "minHeight",
    "maxWidth",
    "maxHeight",
})

_LAYOUT_OPS = frozenset({
    "move_widget",
    "resize_widget",
    "remove_widget",
    "add_registered_widget",
    "replace_chart_spec",
    "update_widget_query",
})

_DATA_AVAILABILITY_VALUES = frozenset({"full", "partial", "missing"})

# Widget-revision preview vocabulary used before the full/partial/missing
# rename; persisted proposals or an out-of-date caller may still send these.
_LEGACY_DATA_AVAILABILITY_ALIASES = {
    "complete": "full",
    "unavailable": "missing",
}


def _normalize_data_availability_value(value: Any) -> Any:
    if isinstance(value, str):
        return _LEGACY_DATA_AVAILABILITY_ALIASES.get(value.strip().lower(), value)
    return value


def _normalize_widget_data_availability(widget: Dict[str, Any]) -> None:
    if "dataAvailability" in widget:
        widget["dataAvailability"] = _normalize_data_availability_value(widget["dataAvailability"])


def _normalize_views_legacy_data_availability(views: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Migrate persisted pre-rename complete/unavailable dataAvailability values
    to full/missing wherever a raw store record surfaces them (workspace load,
    version rollback), so legacy records don't 422 on unrelated mutations that
    revalidate the whole view."""
    for view in views:
        if "dataAvailability" in view:
            view["dataAvailability"] = _normalize_data_availability_value(view["dataAvailability"])
        for widget in view.get("widgets") or []:
            _normalize_widget_data_availability(widget)
    return views


def _normalize_revision_proposal_legacy_data_availability(proposal: Dict[str, Any]) -> Dict[str, Any]:
    if "dataAvailability" in proposal:
        proposal["dataAvailability"] = _normalize_data_availability_value(proposal["dataAvailability"])
    for spec_key in ("beforeSpec", "proposedSpec"):
        spec = proposal.get(spec_key)
        if isinstance(spec, dict):
            _normalize_widget_data_availability(spec)
    return proposal


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _proposal_etag(proposal: Dict[str, Any]) -> str:
    return f'"tr-proposal:{proposal["proposalId"]}:{_stable_hash(proposal)[:8]}"'


def _workspace_etag(workspace: Dict[str, Any]) -> str:
    return f'"tr-workspace:{workspace["id"]}:v{workspace["dashboardVersion"]}:{_stable_hash(workspace)[:8]}"'


def _revision_proposal_etag(proposal: Dict[str, Any]) -> str:
    return f'"tr-widget-revision:{proposal["id"]}:{proposal["status"]}:{_stable_hash(proposal)[:8]}"'


def _version_etag(version: Dict[str, Any]) -> str:
    return f'"tr-dashboard-version:{version["id"]}:{_stable_hash(version)[:8]}"'


def _chart_spec(kind: str, *, click_kind: str = "request_widget_revision") -> Dict[str, Any]:
    encodings_by_kind: Dict[str, Dict[str, Dict[str, str]]] = {
        "bar": {
            "x": {"field": "label", "type": "nominal"},
            "y": {"field": "value", "type": "quantitative"},
        },
        "gauge": {
            "label": {"field": "metric", "type": "nominal"},
            "value": {"field": "value", "type": "quantitative"},
        },
        "heatmap": {
            "row": {"field": "branch_cluster", "type": "nominal"},
            "column": {"field": "trade_date", "type": "temporal"},
            "value": {"field": "net_flow", "type": "quantitative"},
        },
        "line": {
            "x": {"field": "trade_date", "type": "temporal"},
            "y": {"field": "value", "type": "quantitative"},
        },
        "network": {
            "source": {"field": "source", "type": "nominal"},
            "target": {"field": "target", "type": "nominal"},
            "value": {"field": "weight", "type": "quantitative"},
        },
        "sankey": {
            "source": {"field": "source", "type": "nominal"},
            "target": {"field": "target", "type": "nominal"},
            "value": {"field": "flow", "type": "quantitative"},
        },
        "scatter": {
            "x": {"field": "probability", "type": "quantitative"},
            "y": {"field": "expected_value", "type": "quantitative"},
            "size": {"field": "liquidity", "type": "quantitative"},
            "color": {"field": "confidence", "type": "quantitative"},
        },
        "table": {},
        "timeline": {
            "time": {"field": "event_time", "type": "temporal"},
            "label": {"field": "event_label", "type": "nominal"},
        },
    }
    return {
        "spec_version": "1.0",
        "kind": kind,
        "encodings": encodings_by_kind.get(kind, {}),
        "transforms": [],
        "tooltip_fields": [],
        "thresholds": [],
        "click_action": {"kind": click_kind},
        "options": {},
    }


def _placement(x: int, y: int, width: int, height: int) -> Dict[str, int]:
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "minWidth": min(width, 3),
        "minHeight": min(height, 2),
        "maxWidth": 12,
        "maxHeight": 8,
    }


def _widget(
    *,
    wid: str,
    widget_type: str,
    title: str,
    purpose: str,
    why_included: str,
    data_source: str,
    chart_kind: str,
    placement: Dict[str, int],
    interactions: List[str],
    query_filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry = _WIDGET_REGISTRY.get(widget_type) or {}
    sensitivity = str(entry.get("sensitivity") or "user_private")
    return {
        "id": wid,
        "widgetType": widget_type,
        "title": title,
        "purpose": purpose,
        "whyIncluded": why_included,
        "dataSource": data_source,
        "query": {
            "filters": query_filters or {},
            "sort": {},
            "limit": 250 if chart_kind in {"network", "sankey"} else 500,
            "window": "20d",
        },
        "chartSpec": _chart_spec(chart_kind),
        "interactions": [{"kind": kind} for kind in interactions],
        "placement": placement,
        "minSize": {"width": placement["minWidth"], "height": placement["minHeight"]},
        "maxSize": {"width": placement["maxWidth"], "height": placement["maxHeight"]},
        "sensitivity": sensitivity,
        "visible": True,
    }


def _workspace_scope(identity: Any) -> Dict[str, str]:
    claims = getattr(identity, "claims", None)
    if not isinstance(claims, dict):
        claims = identity.get("claims", {}) if isinstance(identity, dict) else {}
    if not isinstance(claims, dict):
        claims = {}
    tenant_id = str(
        claims.get("tenant_id")
        or claims.get("tenantId")
        or (identity.get("tenant_id") if isinstance(identity, dict) else None)
        or (identity.get("tenantId") if isinstance(identity, dict) else None)
        or "pantheon-dev"
    ).strip()
    user_id = str(
        claims.get("user_id")
        or claims.get("userId")
        or claims.get("sub")
        or (identity.get("user_id") if isinstance(identity, dict) else None)
        or (identity.get("userId") if isinstance(identity, dict) else None)
        or (identity.get("operator_id") if isinstance(identity, dict) else None)
        or getattr(identity, "operator_id", "")
        or ""
    ).strip()
    return {"tenant_id": tenant_id, "user_id": user_id}


def _record_visible_to_scope(record: Dict[str, Any], scope: Dict[str, str]) -> bool:
    return (
        str(record.get("tenant_id") or "") == scope["tenant_id"]
        and str(record.get("user_id") or "") == scope["user_id"]
    )


def _gate_state(readiness: Dict[str, Any], gate_name: str) -> str:
    for gate in readiness.get("gates") or []:
        if gate.get("gate") == gate_name:
            return str(gate.get("state") or "")
    return ""


def _ready_strategy_projection(
    *,
    session: Dict[str, Any],
    readiness: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if readiness.get("highest_ready_gate") != "trading_room":
        return None
    if _gate_state(readiness, "trading_room") != "ready":
        return None

    strategy_id = str(readiness.get("strategy_id") or session.get("strategy_id") or "").strip()
    registry_id = str(
        readiness.get("strategy_spec_registry_id")
        or session.get("active_strategy_spec_registry_id")
        or strategy_id
    ).strip()
    if not strategy_id or not registry_id:
        return None

    counts: Dict[str, int] = {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0}
    for ev in events:
        if ev.get("strategy_id") != strategy_id:
            continue
        kind = ev.get("event_kind")
        if kind in counts:
            counts[kind] += 1

    version_id = str(
        readiness.get("workshop_version_id")
        or session.get("selected_version_id")
        or registry_id
    ).strip()
    dashboard_recipe_id = f"agora-trading-{strategy_id}-{version_id}".replace(" ", "-")
    title = str(session.get("title") or "").strip() or f"Ready strategy {strategy_id}"
    evidence_refs = [
        dict(ref)
        for ref in readiness.get("evidence_refs") or []
        if isinstance(ref, dict)
    ]

    return {
        "summary": {
            "strategy_id": strategy_id,
            "strategy_spec_registry_id": registry_id,
            "title": title,
            "readiness_state": "ready",
            "dashboard_recipe_id": dashboard_recipe_id,
            "monitoring_state": "monitoring",
            "candidate_count": 0,
            "position_count": 0,
            "pending_event_counts": counts,
            "staleness_reasons": list(readiness.get("staleness_reasons") or []),
        },
        "detail": {
            "strategy_id": strategy_id,
            "strategy_spec_registry_id": registry_id,
            "strategy_version": version_id,
            "workshop_id": session.get("workshop_id"),
            "workshop_version_id": version_id,
            "assessment_id": readiness.get("assessment_id"),
            "assessment_version": readiness.get("assessment_version"),
            "readiness_state": "ready",
            "highest_ready_gate": "trading_room",
            "readiness_gates": readiness.get("gates") or [],
            "evidence_refs": evidence_refs,
            "dashboard_recipe_id": dashboard_recipe_id,
            "workspace_template_ref": f"winner-branch:{strategy_id}:{version_id}",
            "pending_event_counts": counts,
            "monitoring_state": "monitoring",
        },
    }


def _list_ready_strategy_projections(
    *,
    workshop_store: Any,
    scope: Dict[str, str],
    events: List[Dict[str, Any]],
    assessed_at: str,
) -> List[Dict[str, Any]]:
    if workshop_store is None or not hasattr(workshop_store, "list_sessions"):
        return []
    sessions, _next_cursor = workshop_store.list_sessions(
        user_id=scope["user_id"],
        tenant_id=scope["tenant_id"],
        status=None,
        limit=100,
    )
    projections: List[Dict[str, Any]] = []
    for session in sessions:
        workshop_id = str(session.get("workshop_id") or "")
        if not workshop_id:
            continue
        readiness = (
            workshop_store.get_latest_readiness_assessment(workshop_id)
            if hasattr(workshop_store, "get_latest_readiness_assessment")
            else None
        )
        if not isinstance(readiness, dict) and (
            hasattr(workshop_store, "list_events")
            and hasattr(workshop_store, "get_latest_completeness_snapshot")
        ):
            from ...strategy_workshop.router import _build_readiness_assessment

            readiness = _build_readiness_assessment(
                session=session,
                events=workshop_store.list_events(workshop_id),
                snapshot=workshop_store.get_latest_completeness_snapshot(workshop_id),
                assessed_at=assessed_at,
                assessment_version=1,
            )
        if not isinstance(readiness, dict):
            continue
        projection = _ready_strategy_projection(
            session=session,
            readiness=readiness,
            events=events,
        )
        if projection is not None:
            projections.append(projection)
    projections.sort(key=lambda item: str(item["summary"].get("strategy_id") or ""))
    return projections


def _to_registry_widget_spec(widget: Dict[str, Any], *, created_at: str) -> Dict[str, Any]:
    return {
        "spec_version": "2.0",
        "widget_id": widget.get("id"),
        "widget_type": widget.get("widgetType"),
        "title": widget.get("title"),
        "description": widget.get("purpose"),
        "why_this_widget": widget.get("whyIncluded"),
        "data_source_id": widget.get("dataSource"),
        "query": widget.get("query") or {},
        "chart_spec": widget.get("chartSpec") or {},
        "interactions": widget.get("interactions") or [],
        "sensitivity": widget.get("sensitivity"),
        "can_export": False,
        "registry_version": _REGISTRY_VERSION,
        "layout_constraints": {
            "min_w": (widget.get("minSize") or {}).get("width"),
            "min_h": (widget.get("minSize") or {}).get("height"),
            "max_w": (widget.get("maxSize") or {}).get("width"),
            "max_h": (widget.get("maxSize") or {}).get("height"),
        },
        "version": 1,
        "created_at": created_at,
    }


def _validate_size_object(value: Any, *, path: str) -> List[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors: List[str] = []
    for key in ("width", "height"):
        raw = value.get(key)
        if not isinstance(raw, int) or raw < 1:
            errors.append(f"{path}.{key} must be a positive integer")
    extra = set(value) - {"width", "height"}
    if extra:
        errors.append(f"{path} has unsupported fields: {sorted(extra)}")
    return errors


def _validate_placement(value: Any) -> List[str]:
    if not isinstance(value, dict):
        return ["placement must be an object"]
    errors: List[str] = []
    missing = [key for key in ("x", "y", "width", "height", "minWidth", "minHeight") if key not in value]
    if missing:
        errors.append(f"placement missing required fields: {missing}")
    for key in ("x", "y", "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight"):
        if key not in value:
            continue
        raw = value.get(key)
        minimum = 0 if key in {"x", "y"} else 1
        if not isinstance(raw, int) or raw < minimum:
            errors.append(f"placement.{key} must be an integer >= {minimum}")
    extra = set(value) - _PLACEMENT_ALLOWED_FIELDS
    if extra:
        errors.append(f"placement has unsupported fields: {sorted(extra)}")
    return errors


def _validate_widget(
    widget: Any,
    *,
    now: str,
    path: str = "widget",
    require_data_availability: bool = False,
) -> List[str]:
    if not isinstance(widget, dict):
        return [f"{path} must be an object"]
    errors: List[str] = []
    missing = [
        key for key in (
            "id",
            "widgetType",
            "title",
            "purpose",
            "whyIncluded",
            "dataSource",
            "query",
            "chartSpec",
            "interactions",
            "placement",
            "minSize",
            "maxSize",
            "sensitivity",
        )
        if key not in widget
    ]
    if require_data_availability and "dataAvailability" not in widget:
        missing.append("dataAvailability")
    if missing:
        errors.append(f"{path} missing required fields: {missing}")
    extra = set(widget) - _WIDGET_ALLOWED_FIELDS
    if extra:
        errors.append(f"{path} has unsupported fields: {sorted(extra)}")
    if any(isinstance(widget.get(key), str) and not widget.get(key).strip() for key in ("id", "widgetType", "title")):
        errors.append(f"{path}.id/widgetType/title must be non-empty")
    if "dataAvailability" in widget and widget.get("dataAvailability") not in _DATA_AVAILABILITY_VALUES:
        errors.append(f"{path}.dataAvailability must be one of {sorted(_DATA_AVAILABILITY_VALUES)}")
    errors.extend(_validate_placement(widget.get("placement")))
    errors.extend(_validate_size_object(widget.get("minSize"), path=f"{path}.minSize"))
    errors.extend(_validate_size_object(widget.get("maxSize"), path=f"{path}.maxSize"))

    for interaction in widget.get("interactions") or []:
        kind = interaction.get("kind") if isinstance(interaction, dict) else None
        if kind in _FORBIDDEN_INTERACTIONS:
            errors.append(f"{path}.interactions contains forbidden interaction {kind!r}")

    registry_result = _validate_registry_widget_spec(_to_registry_widget_spec(widget, created_at=now))
    if not registry_result["valid"]:
        errors.extend(f"{path}: {item['message']}" for item in registry_result["errors"])
    return errors


def _validate_view(
    view: Any,
    *,
    now: str,
    path: str = "view",
    require_data_availability: bool = False,
) -> List[str]:
    if not isinstance(view, dict):
        return [f"{path} must be an object"]
    errors: List[str] = []
    missing = [
        key for key in ("id", "title", "purpose", "order", "layoutTemplate", "widgets")
        if key not in view
    ]
    if require_data_availability and "dataAvailability" not in view:
        missing.append("dataAvailability")
    if missing:
        errors.append(f"{path} missing required fields: {missing}")
    extra = set(view) - _VIEW_ALLOWED_FIELDS
    if extra:
        errors.append(f"{path} has unsupported fields: {sorted(extra)}")
    if "order" in view and (not isinstance(view.get("order"), int) or view["order"] < 1):
        errors.append(f"{path}.order must be a positive integer")
    if "dataAvailability" in view and view.get("dataAvailability") not in _DATA_AVAILABILITY_VALUES:
        errors.append(f"{path}.dataAvailability must be one of {sorted(_DATA_AVAILABILITY_VALUES)}")
    widgets = view.get("widgets") or []
    if not isinstance(widgets, list):
        errors.append(f"{path}.widgets must be an array")
    else:
        for index, widget in enumerate(widgets):
            errors.extend(
                _validate_widget(
                    widget,
                    now=now,
                    path=f"{path}.widgets[{index}]",
                    require_data_availability=require_data_availability,
                )
            )
        if "widgetCount" in view and view.get("widgetCount") != len(widgets):
            errors.append(f"{path}.widgetCount must equal widgets length")
    return errors


_WARNING_TEXT_TO_CODE = {
    "Some branch relationships and migration data use inferred confidence.": "inferred_confidence",
    "Restricted relationship graph data is shown as evidence-strength context only.": "restricted_relationship_context",
    "Event lead views state statistical association only.": "statistical_association_only",
    "Position data is scoped to the current trader and remains decision-support only.": "trader_scoped_decision_support",
}


def _normalize_view(view: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(view)
    widgets = normalized.get("widgets") or []
    view_key = f"agora.tradingRoom.views.{normalized['id']}"
    normalized.setdefault("viewKind", normalized["id"])
    normalized.setdefault("titleKey", f"{view_key}.title")
    normalized.setdefault("purposeKey", f"{view_key}.purpose")
    normalized.setdefault("rationaleKey", f"{view_key}.rationale")

    warning_codes = []
    for index, warning in enumerate(normalized.get("warnings") or []):
        code = _WARNING_TEXT_TO_CODE.get(warning)
        if not code:
            code = f"{normalized['id']}.warning.{index + 1}"
        warning_codes.append(code)
    normalized.setdefault("warningCodes", warning_codes)
    if "dataAvailability" in normalized:
        normalized["dataAvailability"] = _normalize_data_availability_value(normalized["dataAvailability"])
    for widget in widgets:
        widget_key = f"agora.tradingRoom.widgets.{widget['id']}"
        widget.setdefault("titleKey", f"{widget_key}.title")
        widget.setdefault("purposeKey", f"{widget_key}.purpose")
        widget.setdefault("whyIncludedKey", f"{widget_key}.whyIncluded")
        _normalize_widget_data_availability(widget)
    normalized["widgetCount"] = len(widgets)
    normalized.setdefault("warnings", [])
    return normalized


def _workspace_data_freshness(
    *,
    store: TradingRoomStore,
    strategy_id: str,
    evidence_refs: List[str],
    reported: Dict[str, Any],
    tenant_id: str,
    user_id: str,
    workshop_store: Optional[Any] = None,
    assessed_at: str = "",
) -> Dict[str, Any]:
    """Combine caller source-health evidence with locally queryable surfaces.

    Every key in the result is assigned from local BFF truth, never from
    ``reported`` -- a caller cannot forge ``full``/``partial`` for any
    source. Sources this deployment has no local verification path for
    (workshop store unwired, or one of the nine source families the BFF
    does not query at all) are conservatively reported as ``missing``
    rather than trusting caller-supplied health.
    """
    resolved = copy.deepcopy(reported)
    events = store.list_decision_events(page_size=10_000).get("items") or []
    strategy_events = [
        event
        for event in events
        if str((event.get("subject") or {}).get("strategy_id") or event.get("strategy_id") or "")
        == strategy_id
    ]
    resolved["agora.trading.events"] = {
        "wired": True,
        "rowCount": len(strategy_events),
        "reason": "scoped TradingRoomStore decision-event query",
    }
    has_ready_strategy = False
    verified_evidence_ref_ids: Optional[set] = None
    if workshop_store is not None and hasattr(workshop_store, "list_sessions"):
        verified_evidence_ref_ids = set()
        matched_sessions: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        # Page through every session in scope and collect every session that
        # matches this strategy_id -- a matching session may not be on the
        # first page, and the caller's own strategy may have more than one
        # session (e.g. an older non-ready session alongside a newer ready
        # one), so stopping at the first match found can hide a later ready
        # session behind an earlier non-ready one.
        for _ in range(1000):  # hard bound against a runaway/looping cursor
            try:
                page, cursor = workshop_store.list_sessions(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    status=None,
                    cursor=cursor,
                    limit=100,
                )
            except Exception:
                page, cursor = [], None
            matched_sessions.extend(
                session
                for session in page
                if str(session.get("strategy_id") or "").strip() == strategy_id
            )
            if not cursor:
                break
        for matched_session in matched_sessions:
            workshop_id = str(matched_session.get("workshop_id") or "")
            if not workshop_id:
                continue
            readiness: Optional[Dict[str, Any]] = (
                workshop_store.get_latest_readiness_assessment(workshop_id)
                if hasattr(workshop_store, "get_latest_readiness_assessment")
                else None
            )
            if not isinstance(readiness, dict) and (
                hasattr(workshop_store, "list_events")
                and hasattr(workshop_store, "get_latest_completeness_snapshot")
            ):
                from ...strategy_workshop.router import _build_readiness_assessment

                try:
                    readiness = _build_readiness_assessment(
                        session=matched_session,
                        events=workshop_store.list_events(workshop_id),
                        snapshot=workshop_store.get_latest_completeness_snapshot(workshop_id),
                        assessed_at=assessed_at,
                        assessment_version=1,
                    )
                except Exception:
                    readiness = None
            if isinstance(readiness, dict) and _gate_state(readiness, "trading_room") == "ready":
                has_ready_strategy = True
                for ref in readiness.get("evidence_refs") or []:
                    if isinstance(ref, dict):
                        ref_id = str(ref.get("ref_id") or "").strip()
                        if ref_id:
                            verified_evidence_ref_ids.add(ref_id)
                break
    resolved["agora.strategy.summary"] = {
        "wired": workshop_store is not None,
        "rowCount": 1 if has_ready_strategy else 0,
        "reason": (
            "ready StrategySpec version (trading_room gate ready) used for workspace generation"
            if workshop_store is not None
            else "workshop store not wired in current BFF deployment; readiness cannot be verified"
        ),
    }
    if verified_evidence_ref_ids is None:
        # workshop_store isn't wired in this deployment at all -- there is no
        # local signal to cross-check evidence refs against, so this source
        # is conservatively unverified rather than trusting the
        # caller-supplied evidence_refs count (same posture as the nine
        # unwired sources below).
        resolved["agora.research.evidence_refs"] = {
            "wired": False,
            "rowCount": 0,
            "reason": "workshop store not wired in current BFF deployment; evidence refs cannot be verified",
        }
    else:
        verified_count = sum(
            1 for ref in evidence_refs if str(ref).strip() in verified_evidence_ref_ids
        )
        resolved["agora.research.evidence_refs"] = {
            "wired": True,
            "rowCount": verified_count,
            "reason": "workspace generation evidence refs verified against the scoped ready readiness assessment",
        }
    all_known_sources = {
        "agora.candidate.members",
        "winner_branch.score_breakdown",
        "winner_branch.branch_flow_daily",
        "winner_branch.branch_profitability",
        "winner_branch.identity_probability",
        "winner_branch.related_branch_flow",
        "winner_branch.event_lead",
        "agora.positions.summary",
        "agora.shadow.outcomes",
    }
    for source in all_known_sources:
        # The BFF has no local query path for any of these sources today --
        # always report them as unverified/missing regardless of what the
        # caller claims, rather than trusting a caller-forged `wired`/
        # `rowCount` via setdefault.
        resolved[source] = {
            "wired": False,
            "rowCount": 0,
            "reason": f"source {source} is not wired in current BFF deployment",
        }
    return resolved


def _find_widget(workspace: Dict[str, Any], widget_id: str) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    for view in workspace.get("views") or []:
        for widget in view.get("widgets") or []:
            if widget.get("id") == widget_id:
                return view, widget
    return None, None


def _find_view(workspace: Dict[str, Any], view_id: str) -> Optional[Dict[str, Any]]:
    for view in workspace.get("views") or []:
        if view.get("id") == view_id:
            return view
    return None


def _extract_strategy_version(body: Dict[str, Any]) -> str:
    return str(
        body.get("strategyVersion")
        or body.get("strategy_version")
        or body.get("strategy_version_id")
        or ""
    ).strip()


def _build_winner_branch_views(strategy_id: str, strategy_version: str) -> List[Dict[str, Any]]:
    base_filters = {"strategyId": strategy_id, "strategyVersion": strategy_version}
    views = [
        {
            "id": "strategy_overview",
            "title": "Strategy overview",
            "purpose": "Understand today's strategy state within ten seconds.",
            "order": 1,
            "layoutTemplate": "winner_branch_overview_grid",
            "thumbnailRef": "winner_branch/overview",
            "rationale": "Summarize candidates, strategy health, decision queues, events, and capital-migration alerts.",
            "warnings": ["Some branch relationships and migration data use inferred confidence."],
            "widgets": [
                _widget(
                    wid="overview_candidate_funnel",
                    widget_type="candidate_funnel",
                    title="Candidate Funnel",
                    purpose="Track new, pending, monitored, Shadow, positioned, and removed candidates.",
                    why_included="V11 View A requires a candidate funnel for day-level status.",
                    data_source="agora.candidate.members",
                    chart_kind="sankey",
                    placement=_placement(0, 0, 4, 3),
                    interactions=["filter_workspace", "open_candidate", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="overview_strategy_health",
                    widget_type="confidence_decomposition",
                    title="Strategy Health",
                    purpose="Show OOS stability, regime, data status, and today's risk.",
                    why_included="V11 requires a strategy health widget in the overview.",
                    data_source="winner_branch.score_breakdown",
                    chart_kind="gauge",
                    placement=_placement(4, 0, 4, 3),
                    interactions=["open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="overview_candidate_ranking",
                    widget_type="candidate_ranking_table",
                    title="Top Candidate Ranking",
                    purpose="Rank candidates by Winner Branch Score, confidence, EV, and liquidity.",
                    why_included="V11 View A requires top candidate and EV context.",
                    data_source="agora.candidate.members",
                    chart_kind="table",
                    placement=_placement(0, 3, 6, 4),
                    interactions=["open_candidate", "add_to_monitoring", "send_to_shadow", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="overview_decision_queue",
                    widget_type="signal_decision_queue",
                    title="Decision Queue",
                    purpose="List entry, add, reduce, exit, and insufficient-data decisions.",
                    why_included="V11 View A requires the pending decision queue.",
                    data_source="agora.trading.events",
                    chart_kind="table",
                    placement=_placement(6, 3, 6, 4),
                    interactions=["open_strategy", "open_position", "send_to_shadow", "request_more_research", "request_widget_revision"],
                    query_filters=base_filters,
                ),
            ],
        },
        {
            "id": "candidates_entry",
            "title": "Candidates and entry",
            "purpose": "Handle unpositioned candidates, approaching signals, and pending decisions.",
            "order": 2,
            "layoutTemplate": "winner_branch_candidate_entry_grid",
            "thumbnailRef": "winner_branch/candidates-entry",
            "rationale": "Place candidate ranking, entry readiness, probability/EV, and signal timelines on one page.",
            "warnings": [],
            "widgets": [
                _widget(
                    wid="entry_candidate_table",
                    widget_type="candidate_ranking_table",
                    title="Candidate Ranking Table",
                    purpose="Show candidate rank, score, confidence, after-cost EV, and entry readiness.",
                    why_included="V11 View B requires the primary candidate table.",
                    data_source="agora.candidate.members",
                    chart_kind="table",
                    placement=_placement(0, 0, 7, 4),
                    interactions=["open_candidate", "add_to_monitoring", "park_candidate", "request_more_research", "send_to_shadow", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="entry_probability_ev",
                    widget_type="expected_value_distribution",
                    title="Probability / EV Scatter",
                    purpose="Compare 20-day upside probability, after-cost EV, liquidity, and confidence.",
                    why_included="V11 View B requires a probability x EV view.",
                    data_source="winner_branch.score_breakdown",
                    chart_kind="scatter",
                    placement=_placement(7, 0, 5, 4),
                    interactions=["open_candidate", "send_to_shadow", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="entry_signal_timeline",
                    widget_type="branch_flow_price_overlay",
                    title="Candidate Signal Timeline",
                    purpose="Present candidate signals, branch changes, price, and event changes.",
                    why_included="V11 View B requires a candidate signal timeline.",
                    data_source="winner_branch.branch_flow_daily",
                    chart_kind="line",
                    placement=_placement(0, 4, 12, 3),
                    interactions=["open_candidate", "cross_highlight", "request_widget_revision"],
                    query_filters=base_filters,
                ),
            ],
        },
        {
            "id": "winner_branch_intelligence",
            "title": "Winner branch intelligence",
            "purpose": "Understand which branches are trustworthy, why, and when they fail.",
            "order": 3,
            "layoutTemplate": "winner_branch_intelligence_grid",
            "thumbnailRef": "winner_branch/intelligence",
            "rationale": "Expand branch rankings, score decomposition, historical horizons, and calibration reliability.",
            "warnings": [],
            "widgets": [
                _widget(
                    wid="branch_leaderboard",
                    widget_type="winner_branch_scoreboard",
                    title="Winner Branch Leaderboard",
                    purpose="Rank winner branches and show recent availability.",
                    why_included="V11 View C requires a winner branch leaderboard.",
                    data_source="winner_branch.branch_profitability",
                    chart_kind="table",
                    placement=_placement(0, 0, 6, 4),
                    interactions=["open_candidate", "open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="branch_score_breakdown",
                    widget_type="winner_branch_score_breakdown",
                    title="Score Component Breakdown",
                    purpose="Break down profitability, consistency, timing, event lead, relationship alignment, and penalties.",
                    why_included="V11 View C requires the Score Breakdown widget.",
                    data_source="winner_branch.score_breakdown",
                    chart_kind="bar",
                    placement=_placement(6, 0, 6, 4),
                    interactions=["open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="branch_profitability_horizon",
                    widget_type="branch_profitability_table",
                    title="Historical Outcome by Horizon",
                    purpose="Compare historical outcomes over 5, 20, 60, and 120 days.",
                    why_included="V11 View C requires horizon comparison.",
                    data_source="winner_branch.branch_profitability",
                    chart_kind="table",
                    placement=_placement(0, 4, 6, 3),
                    interactions=["open_evidence", "filter_workspace", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="branch_stability",
                    widget_type="confidence_decomposition",
                    title="Branch Stability / Regime View",
                    purpose="Observe branch stability and regime transitions.",
                    why_included="V11 View C requires reliability and regime context.",
                    data_source="winner_branch.score_breakdown",
                    chart_kind="bar",
                    placement=_placement(6, 4, 6, 3),
                    interactions=["open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
            ],
        },
        {
            "id": "related_party_flow_migration",
            "title": "Branch relationships and capital migration",
            "purpose": "Handle sparse single-branch observations, migration, related-branch selling, and identity probability.",
            "order": 4,
            "layoutTemplate": "winner_branch_flow_migration_grid",
            "thumbnailRef": "winner_branch/flow-migration",
            "rationale": "Review related-party probability, branch clusters, capital migration, and counter-evidence together.",
            "warnings": ["Restricted relationship graph data is shown as evidence-strength context only."],
            "widgets": [
                _widget(
                    wid="migration_relationship_graph",
                    widget_type="insider_branch_probability_graph",
                    title="Relationship Probability Graph",
                    purpose="Show related-party and branch match probability with supporting and counter-evidence.",
                    why_included="V11 View D requires relationship probability graph.",
                    data_source="winner_branch.identity_probability",
                    chart_kind="network",
                    placement=_placement(0, 0, 6, 4),
                    interactions=["open_evidence", "filter_workspace", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="migration_branch_network",
                    widget_type="related_branch_network",
                    title="Branch Cluster Network",
                    purpose="Show same-broker, co-occurrence, same-symbol migration, and reverse flow.",
                    why_included="V11 View D requires branch cluster network.",
                    data_source="winner_branch.related_branch_flow",
                    chart_kind="network",
                    placement=_placement(6, 0, 6, 4),
                    interactions=["open_evidence", "filter_workspace", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="migration_sankey",
                    widget_type="branch_migration_sankey",
                    title="Migration Alert Table",
                    purpose="Track branch-cluster capital migration and distribution alerts.",
                    why_included="V11 View D requires migration alerts.",
                    data_source="winner_branch.related_branch_flow",
                    chart_kind="sankey",
                    placement=_placement(0, 4, 5, 3),
                    interactions=["open_evidence", "filter_workspace", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="migration_unified_flow",
                    widget_type="branch_flow_price_overlay",
                    title="Unified Flow Timeline",
                    purpose="Compare single-branch net flow, cluster-adjusted net flow, price, and volume.",
                    why_included="V11 View D requires unified flow timeline.",
                    data_source="winner_branch.branch_flow_daily",
                    chart_kind="line",
                    placement=_placement(5, 4, 7, 3),
                    interactions=["open_candidate", "cross_highlight", "request_widget_revision"],
                    query_filters=base_filters,
                ),
            ],
        },
        {
            "id": "event_lead",
            "title": "Event lead",
            "purpose": "Assess whether branch anomalies show pre-event information lead and credible evidence.",
            "order": 5,
            "layoutTemplate": "winner_branch_event_lead_grid",
            "thumbnailRef": "winner_branch/event-lead",
            "rationale": "Use information-lead proxies, statistical association, and evidence strength without alleging misconduct.",
            "warnings": ["Event lead views state statistical association only."],
            "widgets": [
                _widget(
                    wid="event_lead_distribution",
                    widget_type="event_lead_distribution",
                    title="Lead-Time Distribution",
                    purpose="Show the lead-time distribution from anomalous trading to events.",
                    why_included="V11 View E requires lead-time distribution.",
                    data_source="winner_branch.event_lead",
                    chart_kind="bar",
                    placement=_placement(0, 0, 6, 4),
                    interactions=["open_evidence", "filter_workspace", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="event_lead_timeline",
                    widget_type="event_lead_timeline",
                    title="Upcoming / Historical Event Timeline",
                    purpose="Connect anomalous branch trading to events over the next three to six months.",
                    why_included="V11 View E requires event timeline.",
                    data_source="winner_branch.event_lead",
                    chart_kind="timeline",
                    placement=_placement(6, 0, 6, 4),
                    interactions=["open_evidence", "filter_workspace", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="event_lead_confidence",
                    widget_type="confidence_decomposition",
                    title="Information Lead Confidence Summary",
                    purpose="Indicate information-lead proxies and evidence strength.",
                    why_included="V11 View E requires confidence summary.",
                    data_source="winner_branch.score_breakdown",
                    chart_kind="gauge",
                    placement=_placement(0, 4, 12, 3),
                    interactions=["open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
            ],
        },
        {
            "id": "positions_exit",
            "title": "Positions, adds, reductions, and exits",
            "purpose": "Present current positions alongside upcoming exit and adjustment trades.",
            "order": 6,
            "layoutTemplate": "winner_branch_positions_exit_grid",
            "thumbnailRef": "winner_branch/positions-exit",
            "rationale": "Present positions, action queues, thesis health, exposure, and invalidation conditions together.",
            "warnings": ["Position data is scoped to the current trader and remains decision-support only."],
            "widgets": [
                _widget(
                    wid="positions_current_table",
                    widget_type="position_action_queue",
                    title="Current Positions Table",
                    purpose="Show positions, cost, PnL, entry/current score, thesis health, and next action.",
                    why_included="V11 View F requires current positions and next action context.",
                    data_source="agora.positions.summary",
                    chart_kind="table",
                    placement=_placement(0, 0, 6, 4),
                    interactions=["open_position", "send_to_shadow", "create_journal_note", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="positions_action_queue",
                    widget_type="signal_decision_queue",
                    title="Add / Reduce / Exit Queue",
                    purpose="List recommended actions, triggers, adjustment size, deadlines, and confidence.",
                    why_included="V11 View F requires add/reduce/exit queue.",
                    data_source="agora.trading.events",
                    chart_kind="table",
                    placement=_placement(6, 0, 6, 4),
                    interactions=["open_strategy", "open_position", "send_to_shadow", "request_more_research", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="positions_pyramid_plan",
                    widget_type="position_pyramid_plan",
                    title="Portfolio Exposure & Correlation",
                    purpose="Present position exposure, cluster concentration, and correlation risk.",
                    why_included="V11 View F requires portfolio exposure and risk context.",
                    data_source="agora.strategy.summary",
                    chart_kind="table",
                    placement=_placement(0, 4, 6, 3),
                    interactions=["open_strategy", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="positions_shadow_comparison",
                    widget_type="shadow_scoreboard",
                    title="Shadow Alternative Comparison",
                    purpose="Compare the primary version with its Shadow alternative.",
                    why_included="V11 View F requires Shadow alternative comparison.",
                    data_source="agora.shadow.outcomes",
                    chart_kind="bar",
                    placement=_placement(6, 4, 6, 3),
                    interactions=["open_shadow_record", "open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
            ],
        },
        {
            "id": "evidence_monitoring",
            "title": "Evidence and monitoring rules",
            "purpose": "Explain the strategy rules behind the workspace and its alerts.",
            "order": 7,
            "layoutTemplate": "winner_branch_evidence_monitoring_grid",
            "thumbnailRef": "winner_branch/evidence-monitoring",
            "rationale": "Retain the strategy-version summary, monitoring rules, data freshness, evidence, and recent rule changes.",
            "warnings": [],
            "widgets": [
                _widget(
                    wid="evidence_trace",
                    widget_type="evidence_trace",
                    title="Evidence References",
                    purpose="Show strategy rules, research evidence, and data cutoff.",
                    why_included="V11 View G requires evidence references.",
                    data_source="agora.research.evidence_refs",
                    chart_kind="table",
                    placement=_placement(0, 0, 7, 4),
                    interactions=["open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="evidence_monitoring_rules",
                    widget_type="confidence_decomposition",
                    title="Active Monitoring Rules",
                    purpose="Present active monitoring thresholds and data freshness.",
                    why_included="V11 View G requires active monitoring rules and data freshness.",
                    data_source="winner_branch.score_breakdown",
                    chart_kind="bar",
                    placement=_placement(7, 0, 5, 4),
                    interactions=["open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
                _widget(
                    wid="evidence_recent_rule_changes",
                    widget_type="evidence_trace",
                    title="Recent Rule Changes",
                    purpose="Show recent rule changes and their reasons.",
                    why_included="V11 View G requires dashboard change evidence context.",
                    data_source="agora.research.evidence_refs",
                    chart_kind="table",
                    placement=_placement(0, 4, 12, 3),
                    interactions=["open_evidence", "request_widget_revision"],
                    query_filters=base_filters,
                ),
            ],
        },
    ]
    return [_normalize_view(view) for view in views]


def _generate_workspace_proposal(
    *,
    strategy_id: str,
    strategy_version: str,
    proposal_id: str,
    now: str,
    personalization_hints: Dict[str, Any],
    evidence_refs: List[str],
    data_freshness: Dict[str, Any],
    trading_room_ready: bool,
) -> WorkspaceGenerationResult:
    return generate_trading_room_workspace_proposal(
        WorkspaceGenerationInput(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            proposal_id=proposal_id,
            generated_at=now,
            personalization_hints=personalization_hints,
            evidence_refs=evidence_refs,
            data_freshness=data_freshness,
            trading_room_ready=trading_room_ready,
        ),
        view_factory=lambda data: _build_winner_branch_views(
            data.strategy_id,
            data.strategy_version,
        ),
        widget_registry=_WIDGET_REGISTRY,
        validate_widget=lambda widget: _validate_widget(widget, now=now),
        required_view_ids=_WINNER_BRANCH_VIEW_IDS,
    )


# Alias WorkspaceCompiler per AGORA-TRADING-AUTH-20260813 acceptance criteria
WorkspaceCompiler = _generate_workspace_proposal


def _workspace_from_proposal(
    *,
    proposal: Dict[str, Any],
    workspace_id: str,
    user_id: str,
    now: str,
) -> Dict[str, Any]:
    views = copy.deepcopy(proposal["views"])
    return {
        "id": workspace_id,
        "userId": user_id,
        "strategyId": proposal["strategyId"],
        "strategyVersion": proposal["strategyVersion"],
        "dashboardVersion": 1,
        "activeViewId": views[0]["id"],
        "views": views,
        "status": "active",
        "generatedBy": "trading_servant",
        "createdAt": now,
        "updatedAt": now,
    }


def _touch_workspace(workspace: Dict[str, Any], *, now: str, generated_by: str = "user_modified") -> Dict[str, Any]:
    touched = copy.deepcopy(workspace)
    touched["dashboardVersion"] = int(touched.get("dashboardVersion") or 0) + 1
    touched["generatedBy"] = generated_by
    touched["status"] = "active"
    touched["updatedAt"] = now
    for view in touched.get("views") or []:
        view["widgetCount"] = len(view.get("widgets") or [])
    return touched


def _apply_workspace_layout_ops(
    workspace: Dict[str, Any],
    operations: List[Dict[str, Any]],
    *,
    now: str,
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    updated = copy.deepcopy(workspace)
    errors: List[str] = []

    for index, op in enumerate(operations):
        if not isinstance(op, dict):
            errors.append(f"operations[{index}] must be an object")
            continue
        op_name = str(op.get("kind") or op.get("op") or "").strip()
        widget_id = str(op.get("widgetId") or op.get("widget_id") or "").strip()
        payload = op.get("payload") or {}
        if not isinstance(payload, dict):
            errors.append(f"operations[{index}].payload must be an object")
            continue
        if op_name not in _LAYOUT_OPS:
            errors.append(f"operations[{index}] has unsupported kind {op_name!r}")
            continue
        if op_name != "add_registered_widget" and not widget_id:
            errors.append(f"operations[{index}].widgetId is required")
            continue

        _view, widget = _find_widget(updated, widget_id) if widget_id else (None, None)
        if op_name in {"move_widget", "resize_widget", "remove_widget", "replace_chart_spec", "update_widget_query"}:
            if widget is None:
                errors.append(f"operations[{index}] references unknown widget {widget_id!r}")
                continue

        if op_name == "move_widget":
            placement = widget.setdefault("placement", {})
            if "x" in payload:
                placement["x"] = payload["x"]
            if "y" in payload:
                placement["y"] = payload["y"]

        elif op_name == "resize_widget":
            placement = widget.setdefault("placement", {})
            if "width" in payload:
                placement["width"] = payload["width"]
            if "height" in payload:
                placement["height"] = payload["height"]
            if "w" in payload:
                placement["width"] = payload["w"]
            if "h" in payload:
                placement["height"] = payload["h"]

        elif op_name == "remove_widget":
            widget["visible"] = False

        elif op_name == "add_registered_widget":
            restore_id = str(payload.get("widgetId") or payload.get("widget_id") or "").strip()
            if restore_id:
                _restore_view, restore_widget = _find_widget(updated, restore_id)
                if restore_widget is None:
                    errors.append(f"operations[{index}] references unknown restorable widget {restore_id!r}")
                    continue
                restore_widget["visible"] = True
                continue

            view_id = str(payload.get("viewId") or payload.get("view_id") or "").strip()
            widget_spec = payload.get("widgetSpec") or payload.get("widget_spec") or {}
            target_view = _find_view(updated, view_id)
            if target_view is None:
                errors.append(f"operations[{index}] references unknown view {view_id!r}")
                continue
            if not isinstance(widget_spec, dict):
                errors.append(f"operations[{index}].payload.widgetSpec must be an object")
                continue
            _normalize_widget_data_availability(widget_spec)
            widget_errors = _validate_widget(
                widget_spec,
                now=now,
                path=f"operations[{index}].payload.widgetSpec",
                require_data_availability=True,
            )
            if widget_errors:
                errors.extend(widget_errors)
                continue
            target_view.setdefault("widgets", []).append(widget_spec)

        elif op_name == "replace_chart_spec":
            widget["chartSpec"] = payload.get("chartSpec") or payload.get("chart_spec") or {}

        elif op_name == "update_widget_query":
            widget["query"] = payload.get("query") or {}

    if errors:
        return None, errors

    for view in updated.get("views") or []:
        view["widgetCount"] = len(view.get("widgets") or [])

    validation_errors: List[str] = []
    for view_index, view in enumerate(updated.get("views") or []):
        validation_errors.extend(_validate_view(view, now=now, path=f"views[{view_index}]"))
    if validation_errors:
        return None, validation_errors

    return _touch_workspace(updated, now=now), []




# ---------------------------------------------------------------------------
# Trading Room Route Context
# ---------------------------------------------------------------------------

@dataclass
class TradingRoomRouteContext:
    extract_identity: Callable[..., Any]
    require_read_role: Callable[..., None]
    bff_error: Callable[..., HTTPException]
    utc_now: Callable[[], str]
    store: TradingRoomStore
    require_write_role: Optional[Callable[..., None]] = None
    workshop_store: Optional[Any] = None

    def _check_write_auth(self, identity: Any) -> None:
        if self.require_write_role is not None:
            self.require_write_role(identity)

    def _meta(self, capability: str = "agora.trading.v1", **extra: Any) -> Dict[str, Any]:
        meta = {"snapshot_at": self.utc_now(), "capability": capability}
        meta.update({key: value for key, value in extra.items() if value is not None})
        return meta

    def _error_code_enum(self) -> Any:
        try:
            from ....models import ErrorCode
            return ErrorCode
        except Exception:
            pass
        try:
            from bff.models import ErrorCode
            return ErrorCode
        except Exception:
            pass
        try:
            from services.control_plane.bff.models import ErrorCode
            if hasattr(ErrorCode, "FORBIDDEN"):
                return ErrorCode
        except Exception:
            pass
        from enum import Enum
        class FallbackErrorCode(str, Enum):
            AUTH_REQUIRED = "AUTH_REQUIRED"
            FORBIDDEN = "FORBIDDEN"
            RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
            VALIDATION_FAILED = "VALIDATION_FAILED"
            RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
            PRECONDITION_FAILED = "PRECONDITION_FAILED"
            OPERATION_NOT_ALLOWED = "OPERATION_NOT_ALLOWED"
            IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
        return FallbackErrorCode

    def _require_idempotency_key(self, key: Optional[str]) -> str:
        clean = str(key or "").strip()
        if clean:
            return clean
        ErrorCode = self._error_code_enum()

        raise self.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Idempotency-Key header is required",
            "missing_idempotency_key",
            suggestion="Supply a UUID v4 in the Idempotency-Key request header",
        )

    def _require_x_request_id(self, key: Optional[str]) -> str:
        clean = str(key or "").strip()
        if clean:
            return clean
        ErrorCode = self._error_code_enum()

        raise self.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "X-Request-Id header is required",
            "missing_x_request_id",
            suggestion="Supply a stable request identifier in X-Request-Id",
        )

    def _require_if_match(self, header: Optional[str]) -> str:
        clean = str(header or "").strip()
        if clean:
            return clean
        ErrorCode = self._error_code_enum()

        raise self.bff_error(
            428,
            ErrorCode.PRECONDITION_FAILED,
            "If-Match header is required",
            "missing_if_match",
            suggestion="GET the intent or event first and supply the returned ETag in If-Match",
        )

    def _idempotency_scope(self, identity: Any, endpoint: str) -> str:
        scope = _workspace_scope(identity)
        return f"{scope['tenant_id']}:{scope['user_id'] or 'unknown'}:{endpoint}"

    def _check_idempotency(self, identity: Any, endpoint: str, key: str) -> None:
        if self.store.check_and_record_idempotency_key(self._idempotency_scope(identity, endpoint), key):
            ErrorCode = self._error_code_enum()

            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Duplicate Idempotency-Key",
                key,
            )

    def _validation_failed(self, errors: List[str], *, status_code: int = 422) -> None:
        ErrorCode = self._error_code_enum()
        raise self.bff_error(
            status_code,
            ErrorCode.VALIDATION_FAILED,
            "Trading Room workspace validation failed: " + "; ".join(errors),
            "trading_room_workspace_validation_failed",
            details_extra={"errors": errors},
        )

    def _raise_workspace_forbidden(self, resource: str, resource_id: str) -> None:
        ErrorCode = self._error_code_enum()
        raise self.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora Trading Room workspace resource is outside the current user scope",
            "cross_user_workspace_access_forbidden",
            precondition_failed="agora_user_scope",
            details_extra={"resource": resource, "resource_id": resource_id},
        )

    def _load_proposal_for_identity(self,
        *,
        strategy_id: str,
        proposal_id: str,
        identity: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        ErrorCode = self._error_code_enum()
        record = self.store.get_workspace_proposal_record(proposal_id)
        if record is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomWorkspaceProposal {proposal_id!r} not found",
                "workspace_proposal_not_found",
            )
        scope = _workspace_scope(identity)
        if not _record_visible_to_scope(record, scope):
            self._raise_workspace_forbidden("workspace_proposal", proposal_id)
        proposal = record["proposal"]
        if proposal.get("strategyId") != strategy_id:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomWorkspaceProposal {proposal_id!r} not found for strategy {strategy_id!r}",
                "workspace_proposal_not_found",
            )
        return proposal, scope

    def _load_workspace_for_identity(self,
        *,
        workspace_id: str,
        identity: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        ErrorCode = self._error_code_enum()
        record = self.store.get_workspace_record(workspace_id)
        if record is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomWorkspace {workspace_id!r} not found",
                "workspace_not_found",
            )
        scope = _workspace_scope(identity)
        if not _record_visible_to_scope(record, scope):
            self._raise_workspace_forbidden("workspace", workspace_id)
        workspace = record["workspace"]
        _normalize_views_legacy_data_availability(workspace.get("views") or [])
        return workspace, scope

    def _load_revision_proposal_for_identity(self,
        *,
        proposal_id: str,
        identity: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        ErrorCode = self._error_code_enum()
        record = self.store.get_widget_revision_proposal_record(proposal_id)
        if record is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"WidgetRevisionProposal {proposal_id!r} not found",
                "widget_revision_proposal_not_found",
            )
        scope = _workspace_scope(identity)
        if not _record_visible_to_scope(record, scope):
            self._raise_workspace_forbidden("widget_revision_proposal", proposal_id)
        proposal = _normalize_revision_proposal_legacy_data_availability(record["proposal"])
        return proposal, scope

    def _require_workspace_etag(self, if_match: Optional[str], workspace: Dict[str, Any]) -> str:
        ErrorCode = self._error_code_enum()
        current = _workspace_etag(workspace)
        supplied = str(if_match or "").strip()
        if not supplied:
            raise self.bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for Trading Room workspace mutation",
                "missing_if_match",
                suggestion="GET the workspace first and supply the returned ETag.",
                details_extra={"current_etag": current},
            )
        if supplied != current:
            raise self.bff_error(
                412,
                ErrorCode.PRECONDITION_FAILED,
                "Trading Room workspace changed after the client snapshot.",
                "workspace_etag_mismatch",
                details_extra={
                    "current_etag": current,
                    "current_version": workspace.get("dashboardVersion"),
                    "latest_href": f"/bff/agora/trading-room/workspaces/{workspace.get('id')}",
                },
            )
        return current

    def _record_workspace_version(self,
        workspace: Dict[str, Any],
        *,
        scope: Dict[str, str],
        change_summary: str,
        generated_by: Optional[str] = None,
        changed_by: Optional[str] = None,
        reason: Optional[str] = None,
        affected_views: Optional[List[str]] = None,
        affected_widgets: Optional[List[str]] = None,
        effect_evaluation: Optional[str] = None,
        source_revision_proposal_id: Optional[str] = None,
        rollback_of_version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.store.record_workspace_version(
            workspace,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
            created_at=self.utc_now(),
            change_summary=change_summary,
            generated_by=generated_by,
            changed_by=changed_by,
            reason=reason,
            affected_views=affected_views,
            affected_widgets=affected_widgets,
            effect_evaluation=effect_evaluation,
            source_revision_proposal_id=source_revision_proposal_id,
            rollback_of_version_id=rollback_of_version_id,
        )

    def _persist_workspace_with_version(self,
        workspace: Dict[str, Any],
        *,
        scope: Dict[str, str],
        change_summary: str,
        generated_by: Optional[str] = None,
        changed_by: Optional[str] = None,
        reason: Optional[str] = None,
        affected_views: Optional[List[str]] = None,
        affected_widgets: Optional[List[str]] = None,
        effect_evaluation: Optional[str] = None,
        source_revision_proposal_id: Optional[str] = None,
        rollback_of_version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.store.upsert_workspace(
            workspace,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        return self._record_workspace_version(
            workspace,
            scope=scope,
            change_summary=change_summary,
            generated_by=generated_by,
            changed_by=changed_by,
            reason=reason,
            affected_views=affected_views,
            affected_widgets=affected_widgets,
            effect_evaluation=effect_evaluation,
            source_revision_proposal_id=source_revision_proposal_id,
            rollback_of_version_id=rollback_of_version_id,
        )

    def _affected_widgets_from_operations(self, operations: List[Dict[str, Any]]) -> List[str]:
        affected: List[str] = []
        for op in operations:
            if not isinstance(op, dict):
                continue
            widget_id = str(op.get("widgetId") or op.get("widget_id") or "").strip()
            if not widget_id:
                payload = op.get("payload") or {}
                if isinstance(payload, dict):
                    widget_id = str(payload.get("widgetId") or payload.get("widget_id") or "").strip()
            if widget_id and widget_id not in affected:
                affected.append(widget_id)
        return affected

    def _handoff_stage_rule(self, stage: str) -> Dict[str, str]:
        return {
            "shadow": {"handoff_type": "shadow_start", "target_queue": "shadow_research"},
            "paper": {"handoff_type": "paper_validation_request", "target_queue": "management_governance"},
            "canary": {"handoff_type": "promotion_review_request", "target_queue": "promotion_review"},
            "live": {"handoff_type": "promotion_review_request", "target_queue": "promotion_review"},
        }[stage]

    def _intent_type_for_action(self, action: str) -> str:
        return {
            "enter": "entry_interest",
            "entry": "entry_interest",
            "add": "increase_exposure",
            "reduce": "reduce_exposure",
            "exit": "exit_intent",
            "review": "hold_decision",
            "no_action": "hold_decision",
        }.get(action, "hold_decision")

    def _direction_for_action(self, action: str, modifications: Dict[str, Any]) -> str:
        requested = str(modifications.get("direction") or "").strip()
        if requested in {"long", "short", "neutral", "reduce", "exit"}:
            return requested
        return {
            "reduce": "reduce",
            "exit": "exit",
            "review": "neutral",
            "no_action": "neutral",
        }.get(action, "neutral")

    def _rationale_text(self, event: Dict[str, Any], fallback: Optional[str]) -> Optional[str]:
        if fallback:
            return fallback
        claims = [
            str(item.get("claim", "")).strip()
            for item in event.get("rationale", [])
            if isinstance(item, dict) and str(item.get("claim", "")).strip()
        ]
        return "; ".join(claims) if claims else None

    def _intent_from_decision(self,
        *,
        event: Dict[str, Any],
        decision_record: Dict[str, Any],
        body: TraderDecisionRequest,
        identity: Any,
        intent_id: str,
        x_request_id: str,
    ) -> Dict[str, Any]:
        modifications = body.modifications or {}
        action = str(
            modifications.get("action")
            or event.get("suggested_action")
            or event.get("event_kind")
            or "review"
        )
        subject = dict(event.get("subject") or {})
        subject["strategy_ref"] = event.get("strategy_id")
        suggested_size = event.get("suggested_size") or {}
        size_hint = modifications.get("size_hint") or suggested_size.get("size_hint")
        if size_hint not in {"small", "medium", "large", "full_position"}:
            size_hint = None

        scope = _workspace_scope(identity)
        claims = getattr(identity, "claims", None)
        if not isinstance(claims, dict):
            claims = identity.get("claims", {}) if isinstance(identity, dict) else {}
        session_id = (claims or {}).get("session_id") if isinstance(claims, dict) else None

        intent = TradingIntent(
            intent_id=intent_id,
            operator_id=str(scope["user_id"] or "unknown"),
            session_id=session_id,
            intent_type=self._intent_type_for_action(action),  # type: ignore[arg-type]
            direction=self._direction_for_action(action, modifications),  # type: ignore[arg-type]
            subject=TradingIntentSubject(**subject),
            rationale=self._rationale_text(event, body.rationale),
            size_hint=size_hint,
            confidence=(event.get("confidence") or {}).get("value"),
            linked_event_ids=[str(event["decision_event_id"])],
            expressed_at=str(decision_record["decided_at"]),
            metadata={
                "decision_record_id": decision_record["decision_record_id"],
                "decision": body.decision,
                "x_request_id": x_request_id,
                "source": "agora_trading_room",
            },
        )
        return intent.model_dump(exclude_none=True)


    # Public aliases for callers
    check_write_auth = _check_write_auth
    meta = _meta
    error_code_enum = _error_code_enum
    require_idempotency_key = _require_idempotency_key
    require_x_request_id = _require_x_request_id
    require_if_match = _require_if_match
    idempotency_scope = _idempotency_scope
    check_idempotency = _check_idempotency
    validation_failed = _validation_failed
    raise_workspace_forbidden = _raise_workspace_forbidden
    load_proposal_for_identity = _load_proposal_for_identity
    load_workspace_for_identity = _load_workspace_for_identity
    load_revision_proposal_for_identity = _load_revision_proposal_for_identity
    require_workspace_etag = _require_workspace_etag
    record_workspace_version = _record_workspace_version
    persist_workspace_with_version = _persist_workspace_with_version
    affected_widgets_from_operations = _affected_widgets_from_operations
    handoff_stage_rule = _handoff_stage_rule
    intent_type_for_action = _intent_type_for_action
    direction_for_action = _direction_for_action
    rationale_text = _rationale_text
    intent_from_decision = _intent_from_decision


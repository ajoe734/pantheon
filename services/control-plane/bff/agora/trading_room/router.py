"""Agora trading-room router — agora.trading.v1 (observation and decision-support only).

Implements the Trading Room aggregate and decision-event queue per:
  - docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md
  - services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json
  - services/control-plane/specs/agora/v4/trading_decision_event.schema.json
  - services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json
  - services/control-plane/openapi/agora_v1_3.openapi.yaml (trading-room routes)

Safety invariants (D1 boundary):
  - This surface NEVER routes live orders.
  - This surface NEVER creates or mutates RuntimeBinding.
  - This surface NEVER creates capital binding.
  - This surface NEVER approves paper/canary/live promotion.
  - TradingDecisionEvent.no_order_route_proof is always 'agora_decision_support_only'.
  - GovernedIntentHandoff.no_order_route_proof is always 'agora_request_only_no_order_route'.

Route ownership: agora.trading.v1 (capability_manifest.json)

Routes implemented here:
  GET  /bff/agora/trading-room
  GET  /bff/agora/trading-room/strategies/{strategy_id}
  GET  /bff/agora/trading-room/decision-events
  GET  /bff/agora/trading-room/decision-events/{decision_event_id}
  POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions
  GET  /bff/agora/trading-room/stream
  GET  /bff/agora/trading-intents/{intent_id}
  POST /bff/agora/trading-intents/{intent_id}/handoffs  (AG-BE-TR-002 governs)
  POST /bff/agora/trading-intents/{intent_id}/withdraw  (AG-BE-TR-002 governs)
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from ..dashboard.router import (
    _FORBIDDEN_INTERACTIONS,
    _REGISTRY_VERSION,
    _WIDGET_REGISTRY,
    _validate_widget_spec as _validate_registry_widget_spec,
)
from .store import TradingRoomStore, make_trading_room_store


# ---------------------------------------------------------------------------
# Module-level store (singleton per process)
# ---------------------------------------------------------------------------

_store: Optional[TradingRoomStore] = None


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

_WINNER_BRANCH_VIEW_IDS = (
    "strategy_overview",
    "candidates_entry",
    "winner_branch_intelligence",
    "related_party_flow_migration",
    "event_lead",
    "positions_exit",
    "evidence_monitoring",
)

_VIEW_ALLOWED_FIELDS = frozenset({
    "id",
    "title",
    "purpose",
    "order",
    "layoutTemplate",
    "thumbnailRef",
    "widgetCount",
    "rationale",
    "dataAvailability",
    "warnings",
    "widgets",
})

_WIDGET_ALLOWED_FIELDS = frozenset({
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


def _validate_widget(widget: Any, *, now: str, path: str = "widget") -> List[str]:
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
    if missing:
        errors.append(f"{path} missing required fields: {missing}")
    extra = set(widget) - _WIDGET_ALLOWED_FIELDS
    if extra:
        errors.append(f"{path} has unsupported fields: {sorted(extra)}")
    if any(isinstance(widget.get(key), str) and not widget.get(key).strip() for key in ("id", "widgetType", "title")):
        errors.append(f"{path}.id/widgetType/title must be non-empty")
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


def _validate_view(view: Any, *, now: str, path: str = "view") -> List[str]:
    if not isinstance(view, dict):
        return [f"{path} must be an object"]
    errors: List[str] = []
    missing = [
        key for key in ("id", "title", "purpose", "order", "layoutTemplate", "widgets")
        if key not in view
    ]
    if missing:
        errors.append(f"{path} missing required fields: {missing}")
    extra = set(view) - _VIEW_ALLOWED_FIELDS
    if extra:
        errors.append(f"{path} has unsupported fields: {sorted(extra)}")
    if "order" in view and (not isinstance(view.get("order"), int) or view["order"] < 1):
        errors.append(f"{path}.order must be a positive integer")
    widgets = view.get("widgets") or []
    if not isinstance(widgets, list):
        errors.append(f"{path}.widgets must be an array")
    else:
        for index, widget in enumerate(widgets):
            errors.extend(_validate_widget(widget, now=now, path=f"{path}.widgets[{index}]"))
        if "widgetCount" in view and view.get("widgetCount") != len(widgets):
            errors.append(f"{path}.widgetCount must equal widgets length")
    return errors


def _normalize_view(view: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(view)
    widgets = normalized.get("widgets") or []
    normalized["widgetCount"] = len(widgets)
    normalized.setdefault("warnings", [])
    normalized.setdefault("dataAvailability", "partial")
    return normalized


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
            "title": "策略總覽",
            "purpose": "讓交易員在 10 秒內理解今天策略狀況",
            "order": 1,
            "layoutTemplate": "winner_branch_overview_grid",
            "thumbnailRef": "winner_branch/overview",
            "rationale": "彙整候選、策略健康、裁示隊列、事件與資金遷移警示。",
            "dataAvailability": "partial",
            "warnings": ["部分分點關係與遷移資料以推定信賴值呈現。"],
            "widgets": [
                _widget(
                    wid="overview_candidate_funnel",
                    widget_type="candidate_funnel",
                    title="Candidate Funnel",
                    purpose="追蹤新候選、待討論、監控中、Shadow、已形成部位與剔除狀態。",
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
                    purpose="顯示 OOS 穩定度、regime、資料狀態與今日風險。",
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
                    purpose="依 Winner Branch Score、信賴值、EV 與流動性排序候選。",
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
                    purpose="列出接近進場、加碼、減碼、出場與資料不足待裁示項目。",
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
            "title": "候選與進場",
            "purpose": "處理尚未形成部位、接近訊號或需要裁示的標的",
            "order": 2,
            "layoutTemplate": "winner_branch_candidate_entry_grid",
            "thumbnailRef": "winner_branch/candidates-entry",
            "rationale": "把候選排序、進場 readiness、機率/EV 與訊號時間軸放在同一頁。",
            "dataAvailability": "partial",
            "warnings": [],
            "widgets": [
                _widget(
                    wid="entry_candidate_table",
                    widget_type="candidate_ranking_table",
                    title="Candidate Ranking Table",
                    purpose="顯示候選排名、Score、信賴值、成本後 EV 與進場完成度。",
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
                    purpose="比較 20d 上漲機率、成本後 EV、流動性與信賴值。",
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
                    purpose="呈現候選標的訊號、分點變化、價格與事件變化。",
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
            "title": "贏家分點情報",
            "purpose": "理解哪些分點值得信任、為什麼、何時失效",
            "order": 3,
            "layoutTemplate": "winner_branch_intelligence_grid",
            "thumbnailRef": "winner_branch/intelligence",
            "rationale": "放大分點排名、分數拆解、歷史 horizon 與校準可靠度。",
            "dataAvailability": "partial",
            "warnings": [],
            "widgets": [
                _widget(
                    wid="branch_leaderboard",
                    widget_type="winner_branch_scoreboard",
                    title="Winner Branch Leaderboard",
                    purpose="排名贏家分點並顯示近期可用性。",
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
                    purpose="拆解 profitability、consistency、timing、event lead、relationship alignment 與 penalties。",
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
                    purpose="比較 5d / 20d / 60d / 120d 歷史結果。",
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
                    purpose="觀察分點穩定度與 regime 切換。",
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
            "title": "分點關係與資金遷移",
            "purpose": "處理單點觀察不足、分點轉移、關聯分點出貨與身份概率",
            "order": 4,
            "layoutTemplate": "winner_branch_flow_migration_grid",
            "thumbnailRef": "winner_branch/flow-migration",
            "rationale": "把關係人概率、分點 cluster、資金遷移與反向證據合併檢視。",
            "dataAvailability": "partial",
            "warnings": ["Restricted relationship graph data is shown as evidence-strength context only."],
            "widgets": [
                _widget(
                    wid="migration_relationship_graph",
                    widget_type="insider_branch_probability_graph",
                    title="Relationship Probability Graph",
                    purpose="顯示關係人與分點的 match probability 及支持/反向證據。",
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
                    purpose="顯示同券商、共現、同股資金遷移與反向流。",
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
                    purpose="追蹤分點 cluster 資金遷移與出貨警示。",
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
                    purpose="比較單點淨流、cluster-adjusted 淨流、價格與成交量。",
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
            "title": "事件領先",
            "purpose": "判斷分點異常是否具有事件前資訊領先特徵，以及證據可信度",
            "order": 5,
            "layoutTemplate": "winner_branch_event_lead_grid",
            "thumbnailRef": "winner_branch/event-lead",
            "rationale": "使用資訊領先代理、統計關聯與證據強度，不斷言違法或內線。",
            "dataAvailability": "partial",
            "warnings": ["Event lead views state statistical association only."],
            "widgets": [
                _widget(
                    wid="event_lead_distribution",
                    widget_type="event_lead_distribution",
                    title="Lead-Time Distribution",
                    purpose="顯示異常交易到事件的 lead-time 分布。",
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
                    purpose="連接異常分點交易與未來 3 至 6 月事件。",
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
                    purpose="標示資訊領先代理與證據強度。",
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
            "title": "持倉、加碼、減碼與出場",
            "purpose": "同時呈現已持有部位與即將產生的退出／調整交易",
            "order": 6,
            "layoutTemplate": "winner_branch_positions_exit_grid",
            "thumbnailRef": "winner_branch/positions-exit",
            "rationale": "把持倉、行動隊列、thesis health、曝險與失效條件一起呈現。",
            "dataAvailability": "partial",
            "warnings": ["Position data is scoped to the current trader and remains decision-support only."],
            "widgets": [
                _widget(
                    wid="positions_current_table",
                    widget_type="position_action_queue",
                    title="Current Positions Table",
                    purpose="顯示持倉、成本、PnL、進場/現值 Score、Thesis Health 與下一步。",
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
                    purpose="列出建議行動、觸發條件、調整幅度、截止時間與信賴值。",
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
                    purpose="呈現部位曝險、cluster 集中度與 correlation risk。",
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
                    purpose="比較主要版本與 Shadow 對照版本。",
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
            "title": "證據與監控規則",
            "purpose": "讓交易員知道操盤室不是黑箱，以及目前畫面與警示依據什麼策略規則",
            "order": 7,
            "layoutTemplate": "winner_branch_evidence_monitoring_grid",
            "thumbnailRef": "winner_branch/evidence-monitoring",
            "rationale": "保留策略版本摘要、監控規則、資料新鮮度、證據與最近規則變更。",
            "dataAvailability": "partial",
            "warnings": [],
            "widgets": [
                _widget(
                    wid="evidence_trace",
                    widget_type="evidence_trace",
                    title="Evidence References",
                    purpose="顯示策略規則、研究證據與資料 cutoff。",
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
                    purpose="呈現目前啟用的監控門檻與資料新鮮度。",
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
                    purpose="顯示近期規則調整與原因。",
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


def _build_workspace_proposal(
    *,
    strategy_id: str,
    strategy_version: str,
    proposal_id: str,
    now: str,
    personalization_hints: Dict[str, Any],
) -> Dict[str, Any]:
    views = _build_winner_branch_views(strategy_id, strategy_version)
    return {
        "strategyId": strategy_id,
        "strategyVersion": strategy_version,
        "proposalId": proposal_id,
        "generatedAt": now,
        "status": "preview",
        "views": views,
        "rationale": (
            "Generated from the V11 Winner Branch Trading Room contract: seven strategy-specific "
            "views covering overview, entry, branch intelligence, migration, event lead, positions, and evidence."
        ),
        "dataAvailability": {
            "status": "partial",
            "sources": [
                {"dataSource": "agora.candidate.members", "status": "partial", "reason": "candidate projection may lag live research"},
                {"dataSource": "winner_branch.score_breakdown", "status": "partial", "reason": "score calibration is strategy-version scoped"},
                {"dataSource": "winner_branch.related_branch_flow", "status": "partial", "reason": "relationship and migration links are probabilistic"},
                {"dataSource": "agora.trading.events", "status": "partial", "reason": "decision queue is request-only and may be empty"},
            ],
        },
        "warnings": [
            "Workspace is decision-support only; it cannot route orders, bind capital, or mutate runtime bindings.",
            "Relationship, migration, and event-lead widgets represent evidence strength and statistical association only.",
        ],
        "personalizationApplied": {
            "status": "applied" if personalization_hints else "not_applied",
            "items": [
                {"key": str(key), "value": value}
                for key, value in sorted(personalization_hints.items())
                if key not in {"rawHtml", "javascript", "react"}
            ],
        },
    }


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
            target_view.setdefault("widgets", []).append(widget_spec)

        elif op_name == "replace_chart_spec":
            widget["chartSpec"] = payload.get("chartSpec") or payload.get("chart_spec") or {}

        elif op_name == "update_widget_query":
            widget["query"] = payload.get("query") or {}

    if errors:
        return None, errors

    validation_errors: List[str] = []
    for view_index, view in enumerate(updated.get("views") or []):
        validation_errors.extend(_validate_view(view, now=now, path=f"views[{view_index}]"))
    if validation_errors:
        return None, validation_errors

    return _touch_workspace(updated, now=now), []


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_trading_room_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    trading_room_store: Optional[TradingRoomStore] = None,
) -> APIRouter:
    """Trading-room router — agora.trading.v1.

    No live order routing is ever permitted (D1 boundary).
    All routes are operator-scoped (user-private read predicate enforced).
    """
    router = APIRouter(tags=["agora-trading"])
    store = trading_room_store if trading_room_store is not None else _get_store()

    def _meta(capability: str = "agora.trading.v1", **extra: Any) -> Dict[str, Any]:
        meta = {"snapshot_at": utc_now(), "capability": capability}
        meta.update({key: value for key, value in extra.items() if value is not None})
        return meta

    def _error_code_enum() -> Any:
        try:
            from models import ErrorCode
        except ModuleNotFoundError:
            from bff.models import ErrorCode
        return ErrorCode

    def _require_idempotency_key(key: Optional[str]) -> str:
        clean = str(key or "").strip()
        if clean:
            return clean
        ErrorCode = _error_code_enum()

        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Idempotency-Key header is required",
            "missing_idempotency_key",
            suggestion="Supply a UUID v4 in the Idempotency-Key request header",
        )

    def _require_x_request_id(key: Optional[str]) -> str:
        clean = str(key or "").strip()
        if clean:
            return clean
        ErrorCode = _error_code_enum()

        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "X-Request-Id header is required",
            "missing_x_request_id",
            suggestion="Supply a stable request identifier in X-Request-Id",
        )

    def _require_if_match(header: Optional[str]) -> str:
        clean = str(header or "").strip()
        if clean:
            return clean
        ErrorCode = _error_code_enum()

        raise bff_error(
            428,
            ErrorCode.PRECONDITION_FAILED,
            "If-Match header is required",
            "missing_if_match",
            suggestion="GET the intent or event first and supply the returned ETag in If-Match",
        )

    def _idempotency_scope(identity: Dict[str, Any], endpoint: str) -> str:
        tenant_id = identity.get("tenant_id", "unknown")
        user_id = identity.get("user_id", "unknown")
        return f"{tenant_id}:{user_id}:{endpoint}"

    def _check_idempotency(identity: Dict[str, Any], endpoint: str, key: str) -> None:
        if store.check_and_record_idempotency_key(_idempotency_scope(identity, endpoint), key):
            ErrorCode = _error_code_enum()

            raise bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Duplicate Idempotency-Key",
                key,
            )

    def _validation_failed(errors: List[str], *, status_code: int = 422) -> None:
        ErrorCode = _error_code_enum()
        raise bff_error(
            status_code,
            ErrorCode.VALIDATION_FAILED,
            "Trading Room workspace validation failed: " + "; ".join(errors),
            "trading_room_workspace_validation_failed",
            details_extra={"errors": errors},
        )

    def _raise_workspace_forbidden(resource: str, resource_id: str) -> None:
        ErrorCode = _error_code_enum()
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora Trading Room workspace resource is outside the current user scope",
            "cross_user_workspace_access_forbidden",
            precondition_failed="agora_user_scope",
            details_extra={"resource": resource, "resource_id": resource_id},
        )

    def _load_proposal_for_identity(
        *,
        strategy_id: str,
        proposal_id: str,
        identity: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        ErrorCode = _error_code_enum()
        record = store.get_workspace_proposal_record(proposal_id)
        if record is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomWorkspaceProposal {proposal_id!r} not found",
                "workspace_proposal_not_found",
            )
        scope = _workspace_scope(identity)
        if not _record_visible_to_scope(record, scope):
            _raise_workspace_forbidden("workspace_proposal", proposal_id)
        proposal = record["proposal"]
        if proposal.get("strategyId") != strategy_id:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomWorkspaceProposal {proposal_id!r} not found for strategy {strategy_id!r}",
                "workspace_proposal_not_found",
            )
        return proposal, scope

    def _load_workspace_for_identity(
        *,
        workspace_id: str,
        identity: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        ErrorCode = _error_code_enum()
        record = store.get_workspace_record(workspace_id)
        if record is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomWorkspace {workspace_id!r} not found",
                "workspace_not_found",
            )
        scope = _workspace_scope(identity)
        if not _record_visible_to_scope(record, scope):
            _raise_workspace_forbidden("workspace", workspace_id)
        return record["workspace"], scope

    def _load_revision_proposal_for_identity(
        *,
        proposal_id: str,
        identity: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        ErrorCode = _error_code_enum()
        record = store.get_widget_revision_proposal_record(proposal_id)
        if record is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"WidgetRevisionProposal {proposal_id!r} not found",
                "widget_revision_proposal_not_found",
            )
        scope = _workspace_scope(identity)
        if not _record_visible_to_scope(record, scope):
            _raise_workspace_forbidden("widget_revision_proposal", proposal_id)
        return record["proposal"], scope

    def _require_workspace_etag(if_match: Optional[str], workspace: Dict[str, Any]) -> str:
        ErrorCode = _error_code_enum()
        current = _workspace_etag(workspace)
        supplied = str(if_match or "").strip()
        if not supplied:
            raise bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for Trading Room workspace mutation",
                "missing_if_match",
                suggestion="GET the workspace first and supply the returned ETag.",
                details_extra={"current_etag": current},
            )
        if supplied != current:
            raise bff_error(
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

    def _record_workspace_version(
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
        return store.record_workspace_version(
            workspace,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
            created_at=utc_now(),
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

    def _persist_workspace_with_version(
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
        store.upsert_workspace(
            workspace,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        return _record_workspace_version(
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

    def _affected_widgets_from_operations(operations: List[Dict[str, Any]]) -> List[str]:
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

    def _handoff_stage_rule(stage: str) -> Dict[str, str]:
        return {
            "shadow": {"handoff_type": "shadow_start", "target_queue": "shadow_research"},
            "paper": {"handoff_type": "paper_validation_request", "target_queue": "management_governance"},
            "canary": {"handoff_type": "promotion_review_request", "target_queue": "promotion_review"},
            "live": {"handoff_type": "promotion_review_request", "target_queue": "promotion_review"},
        }[stage]

    def _intent_type_for_action(action: str) -> str:
        return {
            "enter": "entry_interest",
            "entry": "entry_interest",
            "add": "increase_exposure",
            "reduce": "reduce_exposure",
            "exit": "exit_intent",
            "review": "hold_decision",
            "no_action": "hold_decision",
        }.get(action, "hold_decision")

    def _direction_for_action(action: str, modifications: Dict[str, Any]) -> str:
        requested = str(modifications.get("direction") or "").strip()
        if requested in {"long", "short", "neutral", "reduce", "exit"}:
            return requested
        return {
            "reduce": "reduce",
            "exit": "exit",
            "review": "neutral",
            "no_action": "neutral",
        }.get(action, "neutral")

    def _rationale_text(event: Dict[str, Any], fallback: Optional[str]) -> Optional[str]:
        if fallback:
            return fallback
        claims = [
            str(item.get("claim", "")).strip()
            for item in event.get("rationale", [])
            if isinstance(item, dict) and str(item.get("claim", "")).strip()
        ]
        return "; ".join(claims) if claims else None

    def _intent_from_decision(
        *,
        event: Dict[str, Any],
        decision_record: Dict[str, Any],
        body: TraderDecisionRequest,
        identity: Dict[str, Any],
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

        intent = TradingIntent(
            intent_id=intent_id,
            operator_id=str(identity.get("user_id", "unknown")),
            session_id=identity.get("session_id"),
            intent_type=_intent_type_for_action(action),  # type: ignore[arg-type]
            direction=_direction_for_action(action, modifications),  # type: ignore[arg-type]
            subject=TradingIntentSubject(**subject),
            rationale=_rationale_text(event, body.rationale),
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

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room")
    def get_trading_room(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        """Return the user-scoped Trading Room aggregate (TradingRoomAggregate)."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        now = utc_now()
        page = store.list_decision_events(page_size=5)
        top_events = page["items"]

        queue_counts: Dict[str, int] = {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0}
        for ev in store.list_decision_events(page_size=1000)["items"]:
            kind = ev.get("event_kind")
            if kind in queue_counts:
                queue_counts[kind] += 1

        aggregate = TradingRoomAggregate(
            spec_version="1.0",
            user_scope_ref=f"operator:{identity.get('user_id', 'unknown')}",
            strategies=[],
            queue_summary=QueueSummary(**queue_counts),
            top_decision_events=[TradingDecisionEvent(**e) for e in top_events],
            position_summaries=[],
            risk_summary=RiskSummary(state="normal"),
            snapshot_at=now,
            data_cutoff=now,
        )
        return aggregate.model_dump(exclude_none=True)

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/strategies/{strategy_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/strategies/{strategy_id}")
    def get_trading_room_strategy(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Return per-strategy Trading Room detail (DetailEnvelope)."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        events = [
            e for e in store.list_decision_events(page_size=1000)["items"]
            if e.get("strategy_id") == strategy_id
        ]
        counts: Dict[str, int] = {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0}
        for ev in events:
            kind = ev.get("event_kind")
            if kind in counts:
                counts[kind] += 1

        return {
            "object_ref": {"type": "trading_room_strategy", "id": strategy_id},
            "status": "active",
            "lifecycle_state": "monitoring",
            "allowedActions": {
                "record_decision": True,
                "submit_handoff": True,
                "request_shadow": True,
            },
            "meta": _meta(),
            "links": {
                "decision_events": f"/bff/agora/trading-room/decision-events?strategy_id={strategy_id}",
            },
            "data": {
                "strategy_id": strategy_id,
                "pending_event_counts": counts,
                "readiness_state": "ready",
                "monitoring_state": "monitoring",
            },
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/strategies/{strategy_id}/trading-room/proposals
    # ------------------------------------------------------------------

    @router.post(
        "/bff/agora/strategies/{strategy_id}/trading-room/proposals",
        status_code=201,
    )
    def create_workspace_proposal(
        strategy_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        """Create a complete V11 TradingRoomWorkspaceProposal preview.

        This is a contract-level proposal surface. The real servant generator is
        owned by AG-BE-DYNUI-003; this route still returns a complete,
        registry-validated Winner Branch workspace proposal so the trader never
        lands in an empty dashboard.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"POST:/bff/agora/strategies/{strategy_id}/trading-room/proposals",
                idempotency_key,
            )

        strategy_version = _extract_strategy_version(body or {})
        if not strategy_version:
            _validation_failed(["strategyVersion is required"], status_code=400)

        personalization_hints = body.get("personalizationHints") or body.get("personalization_hints") or {}
        if personalization_hints and not isinstance(personalization_hints, dict):
            _validation_failed(["personalizationHints must be an object"], status_code=400)

        now = utc_now()
        proposal = _build_workspace_proposal(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            proposal_id=f"trp_{uuid.uuid4().hex[:12]}",
            now=now,
            personalization_hints=personalization_hints,
        )
        errors: List[str] = []
        if tuple(view["id"] for view in proposal["views"]) != _WINNER_BRANCH_VIEW_IDS:
            errors.append("proposal must include the full V11 Winner Branch view set")
        for view_index, view in enumerate(proposal["views"]):
            errors.extend(_validate_view(view, now=now, path=f"views[{view_index}]"))
        if errors:
            _validation_failed(errors)

        scope = _workspace_scope(identity)
        store.upsert_workspace_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        etag = _proposal_etag(proposal)
        response.headers["ETag"] = etag
        return {
            "data": proposal,
            "meta": _meta(etag=etag, strategy_id=strategy_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}")
    def get_workspace_proposal(
        strategy_id: str,
        proposal_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        proposal, _scope = _load_proposal_for_identity(
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            identity=identity,
        )
        etag = _proposal_etag(proposal)
        response.headers["ETag"] = etag
        return {
            "data": proposal,
            "meta": _meta(etag=etag, strategy_id=strategy_id),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept
    # ------------------------------------------------------------------

    @router.post("/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept")
    def accept_workspace_proposal(
        strategy_id: str,
        proposal_id: str,
        response: Response,
        body: Optional[Dict[str, Any]] = None,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"POST:/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept",
                idempotency_key,
            )

        proposal, scope = _load_proposal_for_identity(
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            identity=identity,
        )
        expected_status = (body or {}).get("expectedStatus") or (body or {}).get("expected_status")
        if expected_status and expected_status != "preview":
            _validation_failed(["expectedStatus must be 'preview'"], status_code=400)
        if proposal.get("status") != "preview":
            ErrorCode = _error_code_enum()
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Only preview TradingRoomWorkspaceProposal resources can be accepted",
                "workspace_proposal_not_preview",
                details_extra={"proposal_status": proposal.get("status")},
            )

        workspace = _workspace_from_proposal(
            proposal=proposal,
            workspace_id=f"trw_{uuid.uuid4().hex[:12]}",
            user_id=scope["user_id"],
            now=utc_now(),
        )
        version = _persist_workspace_with_version(
            workspace,
            scope=scope,
            change_summary="v1 - trading servant initial workspace proposal",
            generated_by="trading_servant",
            changed_by="trading_servant",
            reason=proposal.get("rationale"),
            affected_views=[view["id"] for view in workspace.get("views") or []],
            affected_widgets=[
                widget["id"]
                for view in workspace.get("views") or []
                for widget in view.get("widgets") or []
            ],
        )

        proposal["status"] = "accepted"
        store.upsert_workspace_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )

        etag = _workspace_etag(workspace)
        response.headers["ETag"] = etag
        return {
            "data": {
                "workspaceId": workspace["id"],
                "workspace": workspace,
                "version": version,
            },
            "meta": _meta(etag=etag, strategy_id=strategy_id, proposal_id=proposal_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/workspaces/{workspace_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/workspaces/{workspace_id}")
    def get_workspace(
        workspace_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        workspace, _scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        etag = _workspace_etag(workspace)
        response.headers["ETag"] = etag
        return {
            "data": workspace,
            "meta": _meta(etag=etag, workspace_id=workspace_id),
        }

    # ------------------------------------------------------------------
    # PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout
    # ------------------------------------------------------------------

    @router.patch("/bff/agora/trading-room/workspaces/{workspace_id}/layout")
    def patch_workspace_layout(
        workspace_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/layout",
                idempotency_key,
            )
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        _require_workspace_etag(if_match, workspace)

        operations = (body or {}).get("operations") or []
        if not isinstance(operations, list) or not operations:
            _validation_failed(["operations must be a non-empty array"], status_code=400)

        now = utc_now()
        updated, errors = _apply_workspace_layout_ops(workspace, operations, now=now)
        if errors or updated is None:
            _validation_failed(errors)
        version = _persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary="trader adjusted widget layout",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="layout operations accepted by trader",
            affected_widgets=_affected_widgets_from_operations(operations),
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": _meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/views
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/workspaces/{workspace_id}/views", status_code=201)
    def add_workspace_view(
        workspace_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/views",
                idempotency_key,
            )
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        _require_workspace_etag(if_match, workspace)

        view = body.get("viewSpec") or body.get("view_spec") or body
        if not isinstance(view, dict):
            _validation_failed(["viewSpec must be an object"], status_code=400)
        view = _normalize_view(view)
        if _find_view(workspace, str(view.get("id") or "")):
            ErrorCode = _error_code_enum()
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                f"View {view.get('id')!r} already exists",
                "workspace_view_already_exists",
            )

        now = utc_now()
        errors = _validate_view(view, now=now)
        if errors:
            _validation_failed(errors)
        updated = copy.deepcopy(workspace)
        updated.setdefault("views", []).append(view)
        updated = _touch_workspace(updated, now=now)
        version = _persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader added view {view.get('id')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace view addition",
            affected_views=[str(view.get("id") or "")],
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": _meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # PATCH /bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}
    # ------------------------------------------------------------------

    @router.patch("/bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}")
    def patch_workspace_view(
        workspace_id: str,
        view_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}",
                idempotency_key,
            )
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        _require_workspace_etag(if_match, workspace)
        updated = copy.deepcopy(workspace)
        view = _find_view(updated, view_id)
        if view is None:
            ErrorCode = _error_code_enum()
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"View {view_id!r} not found", "workspace_view_not_found")

        patch = body.get("patch") or body
        if not isinstance(patch, dict):
            _validation_failed(["patch must be an object"], status_code=400)
        unsupported = set(patch) - (_VIEW_ALLOWED_FIELDS - {"id"})
        if unsupported:
            _validation_failed([f"view patch has unsupported fields: {sorted(unsupported)}"], status_code=400)
        view.update(copy.deepcopy(patch))
        view["id"] = view_id
        view = _normalize_view(view)
        now = utc_now()
        errors = _validate_view(view, now=now)
        if errors:
            _validation_failed(errors)
        for index, current in enumerate(updated["views"]):
            if current.get("id") == view_id:
                updated["views"][index] = view
                break
        updated = _touch_workspace(updated, now=now)
        version = _persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader updated view {view_id}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace view update",
            affected_views=[view_id],
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": _meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/workspaces/{workspace_id}/widgets", status_code=201)
    def add_workspace_widget(
        workspace_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/widgets",
                idempotency_key,
            )
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        _require_workspace_etag(if_match, workspace)
        view_id = str(body.get("viewId") or body.get("view_id") or "").strip()
        if not view_id:
            _validation_failed(["viewId is required"], status_code=400)
        widget = body.get("widgetSpec") or body.get("widget_spec") or {}
        if not isinstance(widget, dict):
            _validation_failed(["widgetSpec must be an object"], status_code=400)

        updated = copy.deepcopy(workspace)
        view = _find_view(updated, view_id)
        if view is None:
            ErrorCode = _error_code_enum()
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"View {view_id!r} not found", "workspace_view_not_found")
        if _find_widget(updated, str(widget.get("id") or ""))[1] is not None:
            ErrorCode = _error_code_enum()
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, f"Widget {widget.get('id')!r} already exists", "workspace_widget_already_exists")

        now = utc_now()
        errors = _validate_widget(widget, now=now)
        if errors:
            _validation_failed(errors)
        view.setdefault("widgets", []).append(copy.deepcopy(widget))
        view["widgetCount"] = len(view.get("widgets") or [])
        updated = _touch_workspace(updated, now=now)
        version = _persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader added widget {widget.get('id')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace widget addition",
            affected_views=[view_id],
            affected_widgets=[str(widget.get("id") or "")],
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": _meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}
    # ------------------------------------------------------------------

    @router.patch("/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}")
    def patch_workspace_widget(
        workspace_id: str,
        widget_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}",
                idempotency_key,
            )
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        _require_workspace_etag(if_match, workspace)
        patch = body.get("patch") or body
        if not isinstance(patch, dict):
            _validation_failed(["patch must be an object"], status_code=400)
        actor = str(patch.get("initiatedBy") or patch.get("initiated_by") or patch.get("actorType") or "").strip()
        if actor in {"servant", "trading_servant", "ai_servant"}:
            ErrorCode = _error_code_enum()
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Servant-originated widget changes must use WidgetRevisionProposal routes",
                "servant_direct_widget_patch_not_allowed",
            )

        unsupported = set(patch) - (_WIDGET_ALLOWED_FIELDS - {"id"}) - {"initiatedBy", "initiated_by", "actorType"}
        if unsupported:
            _validation_failed([f"widget patch has unsupported fields: {sorted(unsupported)}"], status_code=400)
        updated = copy.deepcopy(workspace)
        _view, widget = _find_widget(updated, widget_id)
        if widget is None:
            ErrorCode = _error_code_enum()
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Widget {widget_id!r} not found", "workspace_widget_not_found")
        clean_patch = {
            key: value
            for key, value in patch.items()
            if key not in {"initiatedBy", "initiated_by", "actorType"}
        }
        widget.update(copy.deepcopy(clean_patch))
        widget["id"] = widget_id
        now = utc_now()
        errors = _validate_widget(widget, now=now)
        if errors:
            _validation_failed(errors)
        updated = _touch_workspace(updated, now=now)
        version = _persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader updated widget {widget_id}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace widget update",
            affected_views=[str((_view or {}).get("id") or "")],
            affected_widgets=[widget_id],
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": _meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals
    # ------------------------------------------------------------------

    @router.post(
        "/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals",
        status_code=201,
    )
    def create_widget_revision_proposal(
        workspace_id: str,
        widget_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals",
                idempotency_key,
            )
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        view, before_widget = _find_widget(workspace, widget_id)
        if view is None or before_widget is None:
            ErrorCode = _error_code_enum()
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Widget {widget_id!r} not found",
                "workspace_widget_not_found",
            )

        payload = body or {}
        instruction = str(payload.get("instruction") or "").strip()
        rationale = str(payload.get("rationale") or "").strip()
        data_availability = str(payload.get("dataAvailability") or payload.get("data_availability") or "").strip()
        proposed_spec = payload.get("proposedSpec") or payload.get("proposed_spec")
        warnings = payload.get("warnings", [])
        errors: List[str] = []
        if not instruction:
            errors.append("instruction is required")
        if not rationale:
            errors.append("rationale is required")
        if data_availability not in {"complete", "partial", "unavailable"}:
            errors.append("dataAvailability must be complete, partial, or unavailable")
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            errors.append("warnings must be an array of strings")
        if not isinstance(proposed_spec, dict):
            errors.append("proposedSpec must be a TradingRoomWidgetSpec object")
        else:
            if proposed_spec.get("id") != widget_id:
                errors.append("proposedSpec.id must match widgetId; keep-copy acceptance creates a new copy id")
            errors.extend(_validate_widget(proposed_spec, now=utc_now(), path="proposedSpec"))
        supplied_view_id = str(payload.get("viewId") or payload.get("view_id") or "").strip()
        if supplied_view_id and supplied_view_id != view.get("id"):
            errors.append("viewId must match the widget's current view")
        supplied_status = str(payload.get("status") or "preview").strip()
        if supplied_status != "preview":
            errors.append("new WidgetRevisionProposal status must be preview")
        if errors:
            _validation_failed(errors)

        proposal = {
            "id": f"wrp_{uuid.uuid4().hex[:12]}",
            "workspaceId": workspace_id,
            "viewId": view["id"],
            "widgetId": widget_id,
            "instruction": instruction,
            "beforeSpec": copy.deepcopy(before_widget),
            "proposedSpec": copy.deepcopy(proposed_spec),
            "rationale": rationale,
            "warnings": list(warnings),
            "dataAvailability": data_availability,
            "status": "preview",
        }
        store.upsert_widget_revision_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        etag = _revision_proposal_etag(proposal)
        response.headers["ETag"] = etag
        return {
            "data": proposal,
            "meta": _meta(
                etag=etag,
                workspace_id=workspace_id,
                widget_id=widget_id,
                before_after_preview=True,
            ),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept")
    def accept_widget_revision_proposal(
        proposal_id: str,
        response: Response,
        body: Optional[Dict[str, Any]] = None,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept",
                idempotency_key,
            )

        proposal, proposal_scope = _load_revision_proposal_for_identity(
            proposal_id=proposal_id,
            identity=identity,
        )
        if proposal.get("status") != "preview":
            ErrorCode = _error_code_enum()
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Only preview WidgetRevisionProposal resources can be accepted",
                "widget_revision_proposal_not_preview",
                details_extra={"proposal_status": proposal.get("status")},
            )

        workspace_id = proposal["workspaceId"]
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        if scope != proposal_scope:
            _raise_workspace_forbidden("widget_revision_proposal", proposal_id)
        _require_workspace_etag(if_match, workspace)

        body = body or {}
        action = str(
            body.get("acceptanceAction")
            or body.get("acceptance_action")
            or body.get("action")
            or "apply"
        ).strip()
        keep_copy_actions = {
            "keep_original_add_modified_copy",
            "keep_original_and_add_modified_copy",
            "add_modified_copy",
            "keep_copy",
        }
        if action not in {"apply"} | keep_copy_actions:
            _validation_failed(
                ["acceptanceAction must be apply or keep_original_add_modified_copy"],
                status_code=400,
            )

        current_view, current_widget = _find_widget(workspace, proposal["widgetId"])
        if current_view is None or current_widget is None:
            ErrorCode = _error_code_enum()
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Widget {proposal['widgetId']!r} not found",
                "workspace_widget_not_found",
            )
        if current_view.get("id") != proposal.get("viewId"):
            _validation_failed(["proposal viewId no longer matches the widget location"], status_code=409)
        if _stable_hash(current_widget) != _stable_hash(proposal["beforeSpec"]):
            ErrorCode = _error_code_enum()
            raise bff_error(
                412,
                ErrorCode.PRECONDITION_FAILED,
                "Widget changed after the revision proposal preview was created.",
                "widget_revision_before_spec_mismatch",
                details_extra={"workspace_id": workspace_id, "widget_id": proposal["widgetId"]},
            )

        updated = copy.deepcopy(workspace)
        updated_view, updated_widget = _find_widget(updated, proposal["widgetId"])
        if updated_view is None or updated_widget is None:
            _validation_failed(["proposal target widget no longer exists"], status_code=409)

        proposed = copy.deepcopy(proposal["proposedSpec"])
        affected_widgets = [proposal["widgetId"]]
        copied_widget_id: Optional[str] = None
        if action in keep_copy_actions:
            copied_widget_id = str(
                body.get("copyWidgetId")
                or body.get("copy_widget_id")
                or f"{proposal['widgetId']}_copy_{uuid.uuid4().hex[:6]}"
            ).strip()
            if not copied_widget_id:
                _validation_failed(["copyWidgetId cannot be empty"], status_code=400)
            if _find_widget(updated, copied_widget_id)[1] is not None:
                ErrorCode = _error_code_enum()
                raise bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    f"Widget {copied_widget_id!r} already exists",
                    "workspace_widget_already_exists",
                )
            proposed["id"] = copied_widget_id
            updated_view.setdefault("widgets", []).append(proposed)
            affected_widgets.append(copied_widget_id)
            change_summary = (
                f"accepted widget revision {proposal_id}; kept original "
                f"{proposal['widgetId']} and added modified copy {copied_widget_id}"
            )
            applied_action = "keep_original_add_modified_copy"
        else:
            proposed["id"] = proposal["widgetId"]
            for index, widget in enumerate(updated_view.get("widgets") or []):
                if widget.get("id") == proposal["widgetId"]:
                    updated_view["widgets"][index] = proposed
                    break
            change_summary = f"accepted widget revision {proposal_id} for {proposal['widgetId']}"
            applied_action = "apply"

        updated_view["widgetCount"] = len(updated_view.get("widgets") or [])
        errors = _validate_view(updated_view, now=utc_now(), path="updatedView")
        if errors:
            _validation_failed(errors)

        updated = _touch_workspace(updated, now=utc_now(), generated_by="trading_servant")
        version = _persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=change_summary,
            generated_by="trading_servant",
            changed_by="trading_servant",
            reason=proposal["rationale"],
            affected_views=[proposal["viewId"]],
            affected_widgets=affected_widgets,
            source_revision_proposal_id=proposal_id,
        )
        proposal["status"] = "accepted"
        store.upsert_widget_revision_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )

        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": {
                "proposal": proposal,
                "workspace": updated,
                "version": version,
                "appliedAction": applied_action,
                "copiedWidgetId": copied_widget_id,
            },
            "meta": _meta(
                etag=etag,
                revision_proposal_etag=_revision_proposal_etag(proposal),
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                version_id=version["id"],
            ),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/workspaces/{workspace_id}/versions
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/workspaces/{workspace_id}/versions")
    def list_workspace_versions(
        workspace_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        versions = store.list_workspace_version_records(
            workspace_id,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        return {
            "data": versions,
            "meta": _meta(
                workspace_id=workspace_id,
                total=len(versions),
                latest_version_id=versions[-1]["id"] if versions else None,
            ),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback")
    def rollback_workspace_version(
        workspace_id: str,
        version_id: str,
        response: Response,
        body: Optional[Dict[str, Any]] = None,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if idempotency_key:
            _check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback",
                idempotency_key,
            )
        workspace, scope = _load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        _require_workspace_etag(if_match, workspace)
        target = store.get_workspace_version_record(
            workspace_id,
            version_id,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        if target is None:
            ErrorCode = _error_code_enum()
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomDashboardVersion {version_id!r} not found",
                "workspace_version_not_found",
            )

        restored_views = copy.deepcopy(target.get("views") or [])
        validation_errors: List[str] = []
        for view_index, view in enumerate(restored_views):
            validation_errors.extend(_validate_view(view, now=utc_now(), path=f"views[{view_index}]"))
        if validation_errors:
            _validation_failed(validation_errors)

        now = utc_now()
        updated = copy.deepcopy(workspace)
        updated["views"] = restored_views
        updated["dashboardVersion"] = int(workspace.get("dashboardVersion") or 0) + 1
        updated["generatedBy"] = "user_modified"
        updated["status"] = "active"
        updated["updatedAt"] = now
        view_ids = [str(view.get("id") or "") for view in restored_views]
        if updated.get("activeViewId") not in view_ids:
            updated["activeViewId"] = view_ids[0] if view_ids else ""

        reason = str((body or {}).get("reason") or "").strip() or f"rollback to {version_id}"
        version = _persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"rollback to dashboard version {target.get('dashboardVersion')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason=reason,
            affected_views=view_ids,
            affected_widgets=[
                widget["id"]
                for view in restored_views
                for widget in view.get("widgets") or []
            ],
            rollback_of_version_id=version_id,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": {
                "workspace": updated,
                "version": version,
                "rollbackOfVersion": target,
            },
            "meta": _meta(
                etag=etag,
                workspace_id=workspace_id,
                version_id=version["id"],
                rollback_of_version_id=version_id,
                rollback_of_version_etag=_version_etag(target),
            ),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/decision-events
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/decision-events")
    def list_trading_decision_events(
        authorization: Optional[str] = Header(default=None),
        event_kind: Optional[str] = Query(
            default=None,
            description="Filter by event kind: entry | add | reduce | exit | review",
        ),
        state: Optional[str] = Query(default=None, description="Filter by lifecycle state"),
        page_size: int = Query(default=20, ge=1, le=100),
        next_page_token: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        """List decision-event queue, filterable by event_kind and state."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        valid_kinds = {"entry", "add", "reduce", "exit", "review"}
        if event_kind and event_kind not in valid_kinds:
            raise bff_error(422, "VALIDATION_ERROR", f"event_kind must be one of {sorted(valid_kinds)}", "invalid_event_kind")

        page = store.list_decision_events(
            event_kind=event_kind,
            state=state,
            page_size=page_size,
            next_page_token=next_page_token,
        )
        return {
            "items": page["items"],
            "page_info": page["page_info"],
            "meta": _meta(),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/decision-events/{decision_event_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/decision-events/{decision_event_id}")
    def get_trading_decision_event(
        decision_event_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Return a single TradingDecisionEvent by ID."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        event = store.get_decision_event(decision_event_id)
        if event is None:
            raise bff_error(404, "NOT_FOUND", f"Decision event {decision_event_id!r} not found", "decision_event_not_found")
        return event

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/decision-events/{decision_event_id}/decisions
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/decision-events/{decision_event_id}/decisions", status_code=201)
    def decide_trading_event(
        decision_event_id: str,
        body: TraderDecisionRequest,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        """Record a trader decision against a pending decision event.

        Allowed decisions: approve | reject | defer | modify
        approve/modify creates and persists a TradingIntent.
        reject/defer are retained as Shadow/Learn evidence subject to consent policy.
        This route NEVER routes live orders.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        idem_key = _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        request_id = _require_x_request_id(x_request_id)

        event = store.get_decision_event(decision_event_id)
        if event is None:
            raise bff_error(404, "NOT_FOUND", f"Decision event {decision_event_id!r} not found", "decision_event_not_found")

        _check_idempotency(
            identity,
            f"POST:/bff/agora/trading-room/decision-events/{decision_event_id}/decisions",
            idem_key,
        )

        if event.get("state") in ("decided", "expired", "invalidated", "superseded"):
            raise bff_error(
                409,
                "TRADING_INTENT_ALREADY_RECORDED",
                f"Decision event {decision_event_id!r} is already in terminal state '{event['state']}'",
                "decision_event_not_actionable",
            )

        decision_record = {
            "decision_record_id": str(uuid.uuid4()),
            "decision_event_id": decision_event_id,
            "decision": body.decision,
            "rationale": body.rationale,
            "modifications": body.modifications,
            "decided_by": identity.get("user_id", "unknown"),
            "decided_at": utc_now(),
        }
        store.record_trader_decision(decision_event_id, decision_record)

        intent_ref: Optional[str] = None
        if body.decision in ("approve", "modify"):
            intent_ref = str(uuid.uuid4())
            intent = _intent_from_decision(
                event=event,
                decision_record=decision_record,
                body=body,
                identity=identity,
                intent_id=intent_ref,
                x_request_id=request_id,
            )
            store.upsert_intent(intent, state="draft")

        data = {
            "decision_record_id": decision_record["decision_record_id"],
            "decision_event_id": decision_event_id,
            "decision": body.decision,
            "intent_ref": intent_ref,
        }
        if intent_ref:
            data["no_order_route_proof"] = "agora_intent_record_only"

        return {
            "status": "completed",
            "data": data,
            "meta": _meta(idempotency_key=idem_key, x_request_id=request_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/stream
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/stream")
    def stream_trading_room(
        authorization: Optional[str] = Header(default=None),
    ) -> Response:
        """User-scoped Trading Room SSE stream stub.

        Returns an empty SSE response.  Full typed-event streaming is
        deferred pending SSE infrastructure task.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        return Response(
            content=": Trading Room SSE ready\n\n",
            media_type="text/event-stream",
        )

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-intents/{intent_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-intents/{intent_id}")
    def get_trading_intent(
        intent_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Return TradingIntent detail (DetailEnvelope).

        Full governed handoff semantics are owned by AG-BE-TR-002.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        intent = store.get_intent(intent_id)
        if intent is None:
            raise bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")
        state = store.get_intent_state(intent_id) or "draft"
        handoffs = store.list_handoffs_for_intent(intent_id)

        return {
            "object_ref": {"type": "trading_intent", "id": intent_id},
            "status": state,
            "lifecycle_state": state,
            "allowedActions": {
                "submit_handoff": state == "draft",
                "withdraw": state in ("draft", "submitted"),
            },
            "meta": _meta(handoff_count=len(handoffs)),
            "links": {
                "handoffs": f"/bff/agora/trading-intents/{intent_id}/handoffs",
                "withdraw": f"/bff/agora/trading-intents/{intent_id}/withdraw",
            },
            "data": intent,
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-intents/{intent_id}/handoffs
    # Governed handoff — AG-BE-TR-002 owns the full implementation.
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-intents/{intent_id}/handoffs", status_code=202)
    def submit_trading_intent_handoff(
        intent_id: str,
        body: GovernedIntentHandoffRequest,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        """Submit a governed handoff request for a TradingIntent.

        Safety: no_order_route_proof must be 'agora_request_only_no_order_route'.
        This is a request-only path; it never routes live orders, creates
        RuntimeBinding, or binds capital.  Management/governance paths remain
        authoritative.

        Validation enforces v1.3 stage/type semantics and keeps canary/live
        request-only.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        idem_key = _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        request_id = _require_x_request_id(x_request_id)

        if body.no_order_route_proof != "agora_request_only_no_order_route":
            raise bff_error(
                422,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                "no_order_route_proof must be 'agora_request_only_no_order_route'",
                "invalid_no_order_route_proof",
            )

        if body.intent_id != intent_id:
            raise bff_error(
                422,
                "VALIDATION_ERROR",
                "intent_id in body must match path parameter",
                "intent_id_mismatch",
            )

        intent = store.get_intent(intent_id)
        if intent is None:
            raise bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")

        _check_idempotency(
            identity,
            f"POST:/bff/agora/trading-intents/{intent_id}/handoffs",
            idem_key,
        )

        intent_state = store.get_intent_state(intent_id) or "draft"
        if intent_state != "draft":
            raise bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                f"TradingIntent {intent_id!r} is not draft; current state is '{intent_state}'",
                "intent_not_handoffable",
            )

        if body.state not in {"draft", "submitted"}:
            raise bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                "Agora can only create draft/submitted request-only handoffs",
                "handoff_state_not_request_only",
            )

        stage_rule = _handoff_stage_rule(body.requested_stage)
        if body.handoff_type != stage_rule["handoff_type"]:
            raise bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                (
                    f"requested_stage '{body.requested_stage}' requires "
                    f"handoff_type '{stage_rule['handoff_type']}'"
                ),
                "stage_handoff_type_mismatch",
            )

        if body.target_queue is not None and body.target_queue != stage_rule["target_queue"]:
            raise bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                (
                    f"requested_stage '{body.requested_stage}' requires "
                    f"target_queue '{stage_rule['target_queue']}'"
                ),
                "stage_target_queue_mismatch",
            )

        if store.get_handoff(body.handoff_id) is not None:
            raise bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                f"handoff_id {body.handoff_id!r} already exists",
                "duplicate_handoff_id",
            )

        handoff = body.model_dump(exclude_none=True)
        handoff["state"] = "submitted"
        handoff["target_queue"] = stage_rule["target_queue"]
        handoff["updated_at"] = handoff.get("updated_at") or utc_now()
        store.upsert_handoff(handoff)

        return {
            "status": "queued",
            "data": {
                "handoff_id": body.handoff_id,
                "intent_id": intent_id,
                "requested_stage": body.requested_stage,
                "handoff_type": body.handoff_type,
                "target_queue": stage_rule["target_queue"],
                "state": "submitted",
                "no_order_route_proof": "agora_request_only_no_order_route",
            },
            "meta": _meta(idempotency_key=idem_key, x_request_id=request_id),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-intents/{intent_id}/withdraw
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-intents/{intent_id}/withdraw")
    def withdraw_trading_intent(
        intent_id: str,
        authorization: Optional[str] = Header(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        """Withdraw a TradingIntent and any pending governed handoff.

        This records withdrawal; it does not cancel any live execution
        (no order routing was ever permitted).
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        idem_key = _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        request_id = _require_x_request_id(x_request_id)

        if store.get_intent(intent_id) is None:
            raise bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")

        _check_idempotency(
            identity,
            f"POST:/bff/agora/trading-intents/{intent_id}/withdraw",
            idem_key,
        )

        withdrawn_at = utc_now()
        withdrawn = store.withdraw_intent(intent_id, withdrawn_at=withdrawn_at)
        withdrawn_handoff_ids = withdrawn.get("withdrawn_handoff_ids", []) if withdrawn else []

        return {
            "status": "completed",
            "data": {
                "intent_id": intent_id,
                "state": "withdrawn",
                "withdrawn_at": withdrawn_at,
                "withdrawn_handoff_ids": withdrawn_handoff_ids,
            },
            "meta": _meta(idempotency_key=idem_key, x_request_id=request_id),
        }

    return router

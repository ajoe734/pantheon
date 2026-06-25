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

import uuid
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

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

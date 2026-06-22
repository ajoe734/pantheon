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


class GovernedIntentHandoffRequest(BaseModel):
    """Request body for POST /bff/agora/trading-intents/{intent_id}/handoffs.

    Field-aligned with services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json.
    Validation is delegated to AG-BE-TR-002 full implementation.
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
    action_proposal: Optional[Dict[str, Any]] = None
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
) -> APIRouter:
    """Trading-room router — agora.trading.v1.

    No live order routing is ever permitted (D1 boundary).
    All routes are operator-scoped (user-private read predicate enforced).
    """
    router = APIRouter(tags=["agora-trading"])
    store = _get_store()

    def _meta(capability: str = "agora.trading.v1") -> Dict[str, Any]:
        return {"snapshot_at": utc_now(), "capability": capability}

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
        return aggregate.model_dump()

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
        approve/modify may create a TradingIntent (not yet persisted here — AG-BE-TR-002).
        reject/defer are retained as Shadow/Learn evidence subject to consent policy.
        This route NEVER routes live orders.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        event = store.get_decision_event(decision_event_id)
        if event is None:
            raise bff_error(404, "NOT_FOUND", f"Decision event {decision_event_id!r} not found", "decision_event_not_found")

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

        return {
            "status": "completed",
            "data": {
                "decision_record_id": decision_record["decision_record_id"],
                "decision_event_id": decision_event_id,
                "decision": body.decision,
                "intent_ref": intent_ref,
            },
            "meta": _meta(),
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

        return {
            "object_ref": {"type": "trading_intent", "id": intent_id},
            "status": intent.get("state", "draft"),
            "lifecycle_state": intent.get("state", "draft"),
            "allowedActions": {
                "submit_handoff": intent.get("state") == "draft",
                "withdraw": intent.get("state") in ("draft", "submitted"),
            },
            "meta": _meta(),
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

        Full validation of handoff state gates and Management routing is
        owned by AG-BE-TR-002.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

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

        return {
            "status": "queued",
            "data": {
                "handoff_id": body.handoff_id,
                "intent_id": intent_id,
                "requested_stage": body.requested_stage,
                "handoff_type": body.handoff_type,
                "state": "submitted",
            },
            "meta": _meta(),
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

        intent = store.get_intent(intent_id)
        if intent is not None:
            intent["state"] = "withdrawn"

        return {
            "status": "completed",
            "data": {
                "intent_id": intent_id,
                "state": "withdrawn",
                "withdrawn_at": utc_now(),
            },
            "meta": _meta(),
        }

    return router

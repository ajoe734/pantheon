"""Agora trading-room application service — use case layer for agora.trading.v1.

Encapsulates:
- Workspaces, proposals, layout mutations, views, widgets, and rollbacks
- Trading decision events and trader decision recording
- Governed trading intents and handoffs
- Persistence and store interactions (TradingRoomStore)
- Zero direct store access from HTTP router handlers
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from fastapi import HTTPException

from .routes.common import (
    EvidenceRef,
    ConfidenceAssessment,
    ProbabilityForecast,
    ExpectedValue,
    RationaleItem,
    RiskNote,
    InvalidationState,
    SuggestedSize,
    TriggerInfo,
    DecisionEventSubject,
    TradingDecisionEvent,
    PendingEventCounts,
    TradingRoomStrategy,
    QueueSummary,
    RiskSummary,
    TradingRoomAggregate,
    TraderDecisionRequest,
    TradingIntentSubject,
    TradingIntent,
    GovernedActionProposal,
    GovernedIntentHandoffRequest,
    _DATA_AVAILABILITY_VALUES,
    _VIEW_ALLOWED_FIELDS,
    _WIDGET_ALLOWED_FIELDS,
    _WINNER_BRANCH_VIEW_IDS,
    _workspace_scope,
    _record_visible_to_scope,
    _proposal_etag,
    _workspace_etag,
    _revision_proposal_etag,
    _version_etag,
    _normalize_views_legacy_data_availability,
    _normalize_widget_data_availability,
    _normalize_revision_proposal_legacy_data_availability,
    _normalize_data_availability_value,
    _stable_hash,
    _chart_spec,
    _placement,
    _widget,
    _to_registry_widget_spec,
    _validate_size_object,
    _validate_placement,
    _validate_widget,
    _validate_view,
    _normalize_view,
    _workspace_data_freshness,
    _find_widget,
    _find_view,
    _extract_strategy_version,
    _build_winner_branch_views,
    _generate_workspace_proposal,
    _workspace_from_proposal,
    _touch_workspace,
    _apply_workspace_layout_ops,
    _list_ready_strategy_projections,
    _tr_publish,
    _tr_scope_key,
    _tr_event_id,
    _tr_buffer,
    _tr_subscribers,
    _tr_replay_after,
    _tr_sse_format,
)
from .store import TradingRoomStore, make_trading_room_store

_default_store: Optional[TradingRoomStore] = None


def get_default_trading_room_store() -> TradingRoomStore:
    global _default_store
    if _default_store is None:
        _default_store = make_trading_room_store()
    return _default_store


def reset_default_trading_room_store() -> None:
    global _default_store
    _default_store = None


class TradingRoomService:
    """Cohesive application service for Agora Trading Room domain."""

    def __init__(
        self,
        store: Optional[TradingRoomStore] = None,
        workshop_store: Optional[Any] = None,
        utc_now: Optional[Callable[[], str]] = None,
        bff_error: Optional[Callable[..., HTTPException]] = None,
    ):
        self.store = store if store is not None else get_default_trading_room_store()
        self.workshop_store = workshop_store
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc).isoformat())
        self.bff_error = bff_error or self._default_bff_error

    def _default_bff_error(
        self,
        status_code: int,
        code: Any,
        message: str,
        detail_type: str = "generic_error",
        **kwargs: Any,
    ) -> HTTPException:
        code_str = code.value if hasattr(code, "value") else str(code)
        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "code": code_str,
                    "message": message,
                    "detail_type": detail_type,
                    **kwargs,
                }
            },
        )

    def _error_code_enum(self) -> Any:
        try:
            from ...models import ErrorCode
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

    # -----------------------------------------------------------------------
    # Idempotency & Validation Helpers
    # -----------------------------------------------------------------------

    def check_idempotency(self, identity: Any, endpoint: str, key: str) -> None:
        scope = _workspace_scope(identity)
        scope_key = f"{scope['tenant_id']}:{scope['user_id'] or 'unknown'}:{endpoint}"
        if self.store.check_and_record_idempotency_key(scope_key, key):
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Duplicate Idempotency-Key",
                key,
            )

    def validation_failed(self, errors: List[str], *, status_code: int = 422) -> None:
        ErrorCode = self._error_code_enum()
        raise self.bff_error(
            status_code,
            ErrorCode.VALIDATION_FAILED,
            "Trading Room workspace validation failed: " + "; ".join(errors),
            "trading_room_workspace_validation_failed",
            details_extra={"errors": errors},
        )

    def raise_workspace_forbidden(self, resource: str, resource_id: str) -> None:
        ErrorCode = self._error_code_enum()
        raise self.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora Trading Room workspace resource is outside the current user scope",
            "cross_user_workspace_access_forbidden",
            precondition_failed="agora_user_scope",
            details_extra={"resource": resource, "resource_id": resource_id},
        )

    # -----------------------------------------------------------------------
    # Workspace & Proposal Persistence
    # -----------------------------------------------------------------------

    def load_proposal_for_identity(
        self,
        *,
        strategy_id: str,
        proposal_id: str,
        identity: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
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
            self.raise_workspace_forbidden("workspace_proposal", proposal_id)
        proposal = record["proposal"]
        if proposal.get("strategyId") != strategy_id:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomWorkspaceProposal {proposal_id!r} not found for strategy {strategy_id!r}",
                "workspace_proposal_not_found",
            )
        return proposal, scope

    def load_workspace_for_identity(
        self,
        *,
        workspace_id: str,
        identity: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
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
            self.raise_workspace_forbidden("workspace", workspace_id)
        workspace = record["workspace"]
        _normalize_views_legacy_data_availability(workspace.get("views") or [])
        return workspace, scope

    def load_revision_proposal_for_identity(
        self,
        *,
        proposal_id: str,
        identity: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
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
            self.raise_workspace_forbidden("widget_revision_proposal", proposal_id)
        proposal = _normalize_revision_proposal_legacy_data_availability(record["proposal"])
        return proposal, scope

    def require_workspace_etag(self, if_match: Optional[str], workspace: Dict[str, Any]) -> str:
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

    def record_workspace_version(
        self,
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

    def persist_workspace_with_version(
        self,
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
        return self.record_workspace_version(
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

    def affected_widgets_from_operations(self, operations: List[Dict[str, Any]]) -> List[str]:
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

    # -----------------------------------------------------------------------
    # Decision Events & Trader Decisions
    # -----------------------------------------------------------------------

    def list_decision_events(
        self,
        *,
        event_kind: Optional[str] = None,
        state: Optional[str] = None,
        page_size: int = 20,
        next_page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        valid_kinds = {"entry", "add", "reduce", "exit", "review"}
        if event_kind and event_kind not in valid_kinds:
            raise self.bff_error(422, "VALIDATION_ERROR", f"event_kind must be one of {sorted(valid_kinds)}", "invalid_event_kind")
        return self.store.list_decision_events(
            event_kind=event_kind,
            state=state,
            page_size=page_size,
            next_page_token=next_page_token,
        )

    def get_decision_event(self, decision_event_id: str) -> Dict[str, Any]:
        event = self.store.get_decision_event(decision_event_id)
        if event is None:
            raise self.bff_error(404, "NOT_FOUND", f"Decision event {decision_event_id!r} not found", "decision_event_not_found")
        return event

    def record_trader_decision(
        self,
        *,
        decision_event_id: str,
        body: TraderDecisionRequest,
        identity: Any,
        idempotency_key: str,
        x_request_id: str,
    ) -> Dict[str, Any]:
        event = self.get_decision_event(decision_event_id)
        self.check_idempotency(
            identity,
            f"POST:/bff/agora/trading-room/decision-events/{decision_event_id}/decisions",
            idempotency_key,
        )
        if event.get("state") in ("decided", "expired", "invalidated", "superseded"):
            raise self.bff_error(
                409,
                "TRADING_INTENT_ALREADY_RECORDED",
                f"Decision event {decision_event_id!r} is already in terminal state '{event['state']}'",
                "decision_event_not_actionable",
            )
        now = self.utc_now()
        scope = _workspace_scope(identity)
        decision_record = {
            "decision_record_id": str(uuid.uuid4()),
            "decision_event_id": decision_event_id,
            "decision": body.decision,
            "rationale": body.rationale,
            "modifications": body.modifications,
            "decided_by": scope["user_id"] or "unknown",
            "decided_at": now,
        }
        self.store.record_trader_decision(decision_event_id, decision_record)

        intent_ref: Optional[str] = None
        if body.decision in ("approve", "modify"):
            intent_ref = str(uuid.uuid4())
            intent = self._intent_from_decision(
                event=event,
                decision_record=decision_record,
                body=body,
                identity=identity,
                intent_id=intent_ref,
                x_request_id=x_request_id,
            )
            self.store.upsert_intent(intent, state="draft")

        data = {
            "decision_record_id": decision_record["decision_record_id"],
            "decision_event_id": decision_event_id,
            "decision": body.decision,
            "intent_ref": intent_ref,
        }
        if intent_ref:
            data["no_order_route_proof"] = "agora_intent_record_only"

        _tr_publish(
            scope,
            "trading_room.decision.recorded",
            data,
            now,
        )
        return data

    # -----------------------------------------------------------------------
    # Trading Intents & Governed Handoffs
    # -----------------------------------------------------------------------

    def get_intent_detail(self, intent_id: str) -> Tuple[Dict[str, Any], str, List[Dict[str, Any]]]:
        intent = self.store.get_intent(intent_id)
        if intent is None:
            raise self.bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")
        state = self.store.get_intent_state(intent_id) or "draft"
        handoffs = self.store.list_handoffs_for_intent(intent_id)
        return intent, state, handoffs

    def submit_intent_handoff(
        self,
        *,
        intent_id: str,
        body: GovernedIntentHandoffRequest,
        identity: Any,
        idempotency_key: str,
        x_request_id: str,
    ) -> Dict[str, Any]:
        if body.no_order_route_proof != "agora_request_only_no_order_route":
            raise self.bff_error(
                422,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                "no_order_route_proof must be 'agora_request_only_no_order_route'",
                "invalid_no_order_route_proof",
            )
        if body.intent_id != intent_id:
            raise self.bff_error(
                422,
                "VALIDATION_ERROR",
                "intent_id in body must match path parameter",
                "intent_id_mismatch",
            )
        intent = self.store.get_intent(intent_id)
        if intent is None:
            raise self.bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")

        self.check_idempotency(
            identity,
            f"POST:/bff/agora/trading-intents/{intent_id}/handoffs",
            idempotency_key,
        )
        intent_state = self.store.get_intent_state(intent_id) or "draft"
        if intent_state != "draft":
            raise self.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                f"TradingIntent {intent_id!r} is not draft; current state is '{intent_state}'",
                "intent_not_handoffable",
            )
        if body.state not in {"draft", "submitted"}:
            raise self.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                "Agora can only create draft/submitted request-only handoffs",
                "handoff_state_not_request_only",
            )
        stage_rule = self._handoff_stage_rule(body.requested_stage)
        if body.handoff_type != stage_rule["handoff_type"]:
            raise self.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                (
                    f"requested_stage '{body.requested_stage}' requires "
                    f"handoff_type '{stage_rule['handoff_type']}'"
                ),
                "stage_handoff_type_mismatch",
            )
        if body.target_queue is not None and body.target_queue != stage_rule["target_queue"]:
            raise self.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                (
                    f"requested_stage '{body.requested_stage}' requires "
                    f"target_queue '{stage_rule['target_queue']}'"
                ),
                "stage_target_queue_mismatch",
            )
        if self.store.get_handoff(body.handoff_id) is not None:
            raise self.bff_error(
                409,
                "TRADING_INTENT_HANDOFF_NOT_ALLOWED",
                f"handoff_id {body.handoff_id!r} already exists",
                "duplicate_handoff_id",
            )

        handoff = body.model_dump(exclude_none=True)
        handoff["state"] = "submitted"
        handoff["target_queue"] = stage_rule["target_queue"]
        handoff["updated_at"] = handoff.get("updated_at") or self.utc_now()
        self.store.upsert_handoff(handoff)
        return {
            "handoff_id": body.handoff_id,
            "intent_id": intent_id,
            "requested_stage": body.requested_stage,
            "handoff_type": body.handoff_type,
            "target_queue": stage_rule["target_queue"],
            "state": "submitted",
            "no_order_route_proof": "agora_request_only_no_order_route",
        }

    def withdraw_intent(
        self,
        *,
        intent_id: str,
        identity: Any,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if self.store.get_intent(intent_id) is None:
            raise self.bff_error(404, "NOT_FOUND", f"TradingIntent {intent_id!r} not found", "intent_not_found")
        self.check_idempotency(
            identity,
            f"POST:/bff/agora/trading-intents/{intent_id}/withdraw",
            idempotency_key,
        )
        withdrawn_at = self.utc_now()
        withdrawn = self.store.withdraw_intent(intent_id, withdrawn_at=withdrawn_at)
        withdrawn_handoff_ids = withdrawn.get("withdrawn_handoff_ids", []) if withdrawn else []
        return {
            "intent_id": intent_id,
            "state": "withdrawn",
            "withdrawn_at": withdrawn_at,
            "withdrawn_handoff_ids": withdrawn_handoff_ids,
        }

    # -----------------------------------------------------------------------
    # Trading Room Aggregations & Workspace Operations
    # -----------------------------------------------------------------------

    def get_trading_room_aggregate(self, identity: Any) -> Dict[str, Any]:
        now = self.utc_now()
        page = self.store.list_decision_events(page_size=5)
        top_events = page["items"]
        all_events = self.store.list_decision_events(page_size=1000)["items"]

        queue_counts: Dict[str, int] = {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0}
        for ev in all_events:
            kind = ev.get("event_kind")
            if kind in queue_counts:
                queue_counts[kind] += 1

        scope = _workspace_scope(identity)
        projections = _list_ready_strategy_projections(
            workshop_store=self.workshop_store,
            scope=scope,
            events=all_events,
            assessed_at=now,
        )
        aggregate = TradingRoomAggregate(
            spec_version="1.0",
            user_scope_ref=f"operator:{scope['user_id'] or 'unknown'}",
            strategies=[TradingRoomStrategy(**item["summary"]) for item in projections],
            queue_summary=QueueSummary(**queue_counts),
            top_decision_events=[TradingDecisionEvent(**e) for e in top_events],
            position_summaries=[],
            risk_summary=RiskSummary(state="normal"),
            snapshot_at=now,
            data_cutoff=now,
        )
        return aggregate.model_dump(exclude_none=True)

    def get_strategy_aggregate(self, strategy_id: str, identity: Any) -> Tuple[Dict[str, Any], Dict[str, int]]:
        scope = _workspace_scope(identity)
        all_events = self.store.list_decision_events(page_size=1000)["items"]
        events = [e for e in all_events if e.get("strategy_id") == strategy_id]
        counts: Dict[str, int] = {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0}
        for ev in events:
            kind = ev.get("event_kind")
            if kind in counts:
                counts[kind] += 1
        projection = next(
            (
                item for item in _list_ready_strategy_projections(
                    workshop_store=self.workshop_store,
                    scope=scope,
                    events=all_events,
                    assessed_at=self.utc_now(),
                )
                if item["summary"].get("strategy_id") == strategy_id
            ),
            None,
        )
        if projection is None and not events:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Trading Room strategy {strategy_id!r} not found",
                "trading_room_strategy_not_found",
            )
        data = {
            "strategy_id": strategy_id,
            **((projection or {}).get("detail") or {}),
            "pending_event_counts": counts,
            "readiness_state": "ready",
            "monitoring_state": "monitoring",
        }
        return data, counts

    def create_workspace_proposal(
        self,
        strategy_id: str,
        body: Dict[str, Any],
        identity: Any,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"POST:/bff/agora/strategies/{strategy_id}/trading-room/proposals",
                idempotency_key,
            )
        strategy_version = _extract_strategy_version(body)
        if not strategy_version:
            self.validation_failed(["strategyVersion is required"], status_code=400)

        personalization_hints = body.get("personalizationHints") or body.get("personalization_hints") or {}
        if personalization_hints and not isinstance(personalization_hints, dict):
            self.validation_failed(["personalizationHints must be an object"], status_code=400)

        evidence_refs = body.get("evidenceRefs") or body.get("evidence_refs") or []
        if evidence_refs and not isinstance(evidence_refs, list):
            self.validation_failed(["evidenceRefs must be an array"], status_code=400)

        data_freshness = body.get("dataFreshness") or body.get("data_freshness") or {}
        if data_freshness and not isinstance(data_freshness, dict):
            self.validation_failed(["dataFreshness must be an object keyed by data source"], status_code=400)

        trading_room_ready = body.get(
            "tradingRoomReady",
            body.get("trading_room_ready", True),
        )
        if not isinstance(trading_room_ready, bool):
            self.validation_failed(["tradingRoomReady must be a boolean"], status_code=400)

        now = self.utc_now()
        scope = _workspace_scope(identity)
        resolved_data_freshness = _workspace_data_freshness(
            store=self.store,
            strategy_id=strategy_id,
            evidence_refs=evidence_refs,
            reported=data_freshness,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
            workshop_store=self.workshop_store,
            assessed_at=now,
        )
        generation = _generate_workspace_proposal(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            proposal_id=f"trp_{uuid.uuid4().hex[:12]}",
            now=now,
            personalization_hints=personalization_hints,
            evidence_refs=evidence_refs,
            data_freshness=resolved_data_freshness,
            trading_room_ready=trading_room_ready,
        )
        if generation.status != "completed" or generation.proposal is None:
            self.validation_failed(
                generation.validation_errors
                or generation.blocking_reasons
                or ["workspace proposal generation failed"],
                status_code=422,
            )
        proposal = generation.proposal
        errors: List[str] = []
        if tuple(view["id"] for view in proposal["views"]) != _WINNER_BRANCH_VIEW_IDS:
            errors.append("proposal must include the full V11 Winner Branch view set")
        for view_index, view in enumerate(proposal["views"]):
            errors.extend(_validate_view(view, now=now, path=f"views[{view_index}]"))
        if errors:
            self.validation_failed(errors)

        self.store.upsert_workspace_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
            generation_meta=generation.meta(),
        )
        return proposal, generation.meta()

    def get_workspace_proposal(
        self,
        strategy_id: str,
        proposal_id: str,
        identity: Any,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        proposal, _scope = self.load_proposal_for_identity(
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            identity=identity,
        )
        meta = self.store.get_workspace_proposal_generation_meta(proposal_id)
        return proposal, meta

    def accept_workspace_proposal(
        self,
        strategy_id: str,
        proposal_id: str,
        body: Optional[Dict[str, Any]],
        identity: Any,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"POST:/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept",
                idempotency_key,
            )
        proposal, scope = self.load_proposal_for_identity(
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            identity=identity,
        )
        expected_status = (body or {}).get("expectedStatus") or (body or {}).get("expected_status")
        if expected_status and expected_status != "preview":
            self.validation_failed(["expectedStatus must be 'preview'"], status_code=400)
        if proposal.get("status") != "preview":
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
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
            now=self.utc_now(),
        )
        version = self.persist_workspace_with_version(
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
        self.store.upsert_workspace_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        return workspace, version

    def lookup_workspace(
        self,
        strategy_id: Optional[str],
        strategy_version: Optional[str],
        identity: Any,
    ) -> Dict[str, Any]:
        scope = _workspace_scope(identity)
        if not strategy_id:
            self.validation_failed(["strategy_id query parameter is required for workspace lookup"], status_code=400)
        workspace = self.store.get_workspace_for_strategy(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        if workspace is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"No workspace found for strategy '{strategy_id}'",
                "workspace_not_found",
            )
        _normalize_views_legacy_data_availability(workspace.get("views") or [])
        return workspace

    def get_strategy_workspace(
        self,
        strategy_id: str,
        version: Optional[str],
        identity: Any,
    ) -> Dict[str, Any]:
        scope = _workspace_scope(identity)
        workspace = self.store.get_workspace_for_strategy(
            strategy_id=strategy_id,
            strategy_version=version,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        if workspace is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"No workspace found for strategy '{strategy_id}'",
                "workspace_not_found",
            )
        _normalize_views_legacy_data_availability(workspace.get("views") or [])
        return workspace

    def get_workspace(self, workspace_id: str, identity: Any) -> Dict[str, Any]:
        workspace, _scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        return workspace

    def update_workspace_layout(
        self,
        workspace_id: str,
        body: Dict[str, Any],
        identity: Any,
        if_match: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/layout",
                idempotency_key,
            )
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        self.require_workspace_etag(if_match, workspace)

        operations = (body or {}).get("operations") or []
        if not isinstance(operations, list) or not operations:
            self.validation_failed(["operations must be a non-empty array"], status_code=400)

        now = self.utc_now()
        updated, errors = _apply_workspace_layout_ops(workspace, operations, now=now)
        if errors or updated is None:
            self.validation_failed(errors)
        version = self.persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary="trader adjusted widget layout",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="layout operations accepted by trader",
            affected_widgets=self.affected_widgets_from_operations(operations),
        )
        return updated, version

    def add_workspace_view(
        self,
        workspace_id: str,
        body: Dict[str, Any],
        identity: Any,
        if_match: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/views",
                idempotency_key,
            )
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        self.require_workspace_etag(if_match, workspace)

        view = body.get("viewSpec") or body.get("view_spec") or body
        if not isinstance(view, dict):
            self.validation_failed(["viewSpec must be an object"], status_code=400)
        view = _normalize_view(view)
        if _find_view(workspace, str(view.get("id") or "")):
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                f"View {view.get('id')!r} already exists",
                "workspace_view_already_exists",
            )

        now = self.utc_now()
        errors = _validate_view(view, now=now, require_data_availability=True)
        if errors:
            self.validation_failed(errors)
        updated = copy.deepcopy(workspace)
        updated.setdefault("views", []).append(view)
        updated = _touch_workspace(updated, now=now)
        version = self.persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader added view {view.get('id')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace view addition",
            affected_views=[str(view.get("id") or "")],
        )
        return updated, version

    def patch_workspace_view(
        self,
        workspace_id: str,
        view_id: str,
        body: Dict[str, Any],
        identity: Any,
        if_match: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}",
                idempotency_key,
            )
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        self.require_workspace_etag(if_match, workspace)
        updated = copy.deepcopy(workspace)
        view = _find_view(updated, view_id)
        if view is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"View {view_id!r} not found", "workspace_view_not_found")

        patch = body.get("patch") or body
        if not isinstance(patch, dict):
            self.validation_failed(["patch must be an object"], status_code=400)
        unsupported = set(patch) - (_VIEW_ALLOWED_FIELDS - {"id"})
        if unsupported:
            self.validation_failed([f"view patch has unsupported fields: {sorted(unsupported)}"], status_code=400)
        view.update(copy.deepcopy(patch))
        view["id"] = view_id
        view = _normalize_view(view)
        now = self.utc_now()
        errors = _validate_view(view, now=now, require_data_availability=True)
        if errors:
            self.validation_failed(errors)
        for index, current in enumerate(updated["views"]):
            if current.get("id") == view_id:
                updated["views"][index] = view
                break
        updated = _touch_workspace(updated, now=now)
        version = self.persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader updated view {view_id}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace view update",
            affected_views=[view_id],
        )
        return updated, version

    def add_workspace_widget(
        self,
        workspace_id: str,
        body: Dict[str, Any],
        identity: Any,
        if_match: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/widgets",
                idempotency_key,
            )
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        self.require_workspace_etag(if_match, workspace)
        view_id = str(body.get("viewId") or body.get("view_id") or "").strip()
        if not view_id:
            self.validation_failed(["viewId is required"], status_code=400)
        widget = body.get("widgetSpec") or body.get("widget_spec") or {}
        if not isinstance(widget, dict):
            self.validation_failed(["widgetSpec must be an object"], status_code=400)

        updated = copy.deepcopy(workspace)
        view = _find_view(updated, view_id)
        if view is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"View {view_id!r} not found", "workspace_view_not_found")
        if _find_widget(updated, str(widget.get("id") or ""))[1] is not None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(409, ErrorCode.RESOURCE_CONFLICT, f"Widget {widget.get('id')!r} already exists", "workspace_widget_already_exists")

        _normalize_widget_data_availability(widget)
        now = self.utc_now()
        errors = _validate_widget(widget, now=now, require_data_availability=True)
        if errors:
            self.validation_failed(errors)
        view.setdefault("widgets", []).append(copy.deepcopy(widget))
        view["widgetCount"] = len(view.get("widgets") or [])
        updated = _touch_workspace(updated, now=now)
        version = self.persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader added widget {widget.get('id')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace widget addition",
            affected_views=[view_id],
            affected_widgets=[str(widget.get("id") or "")],
        )
        return updated, version

    def patch_workspace_widget(
        self,
        workspace_id: str,
        widget_id: str,
        body: Dict[str, Any],
        identity: Any,
        if_match: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}",
                idempotency_key,
            )
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        self.require_workspace_etag(if_match, workspace)
        patch = body.get("patch") or body
        if not isinstance(patch, dict):
            self.validation_failed(["patch must be an object"], status_code=400)
        actor = str(patch.get("initiatedBy") or patch.get("initiated_by") or patch.get("actorType") or "").strip()
        if actor in {"servant", "trading_servant", "ai_servant"}:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Servant-originated widget changes must use WidgetRevisionProposal routes",
                "servant_direct_widget_patch_not_allowed",
            )

        unsupported = set(patch) - (_WIDGET_ALLOWED_FIELDS - {"id"}) - {"initiatedBy", "initiated_by", "actorType"}
        if unsupported:
            self.validation_failed([f"widget patch has unsupported fields: {sorted(unsupported)}"], status_code=400)
        updated = copy.deepcopy(workspace)
        _view, widget = _find_widget(updated, widget_id)
        if widget is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Widget {widget_id!r} not found", "workspace_widget_not_found")
        clean_patch = {
            key: value
            for key, value in patch.items()
            if key not in {"initiatedBy", "initiated_by", "actorType"}
        }
        _normalize_widget_data_availability(clean_patch)
        widget.update(copy.deepcopy(clean_patch))
        widget["id"] = widget_id
        now = self.utc_now()
        errors = _validate_widget(widget, now=now, require_data_availability=True)
        if errors:
            self.validation_failed(errors)
        updated = _touch_workspace(updated, now=now)
        version = self.persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader updated widget {widget_id}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace widget update",
            affected_views=[str((_view or {}).get("id") or "")],
            affected_widgets=[widget_id],
        )
        return updated, version

    def create_widget_revision_proposal(
        self,
        workspace_id: str,
        widget_id: str,
        body: Dict[str, Any],
        identity: Any,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals",
                idempotency_key,
            )
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        view, before_widget = _find_widget(workspace, widget_id)
        if view is None or before_widget is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Widget {widget_id!r} not found",
                "workspace_widget_not_found",
            )

        payload = body or {}
        instruction = str(payload.get("instruction") or "").strip()
        rationale = str(payload.get("rationale") or "").strip()
        data_availability = _normalize_data_availability_value(
            str(payload.get("dataAvailability") or payload.get("data_availability") or "").strip()
        )
        proposed_spec = payload.get("proposedSpec") or payload.get("proposed_spec")
        warnings = payload.get("warnings", [])
        errors: List[str] = []
        if not instruction:
            errors.append("instruction is required")
        if not rationale:
            errors.append("rationale is required")
        if data_availability not in _DATA_AVAILABILITY_VALUES:
            errors.append("dataAvailability must be full, partial, or missing")
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            errors.append("warnings must be an array of strings")
        if not isinstance(proposed_spec, dict):
            errors.append("proposedSpec must be a TradingRoomWidgetSpec object")
        else:
            _normalize_widget_data_availability(proposed_spec)
            if proposed_spec.get("id") != widget_id:
                errors.append("proposedSpec.id must match widgetId; keep-copy acceptance creates a new copy id")
            errors.extend(
                _validate_widget(
                    proposed_spec,
                    now=self.utc_now(),
                    path="proposedSpec",
                    require_data_availability=True,
                )
            )
        supplied_view_id = str(payload.get("viewId") or payload.get("view_id") or "").strip()
        if supplied_view_id and supplied_view_id != view.get("id"):
            errors.append("viewId must match the widget's current view")
        supplied_status = str(payload.get("status") or "preview").strip()
        if supplied_status != "preview":
            errors.append("new WidgetRevisionProposal status must be preview")
        if errors:
            self.validation_failed(errors)

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
        self.store.upsert_widget_revision_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        return proposal

    def accept_widget_revision_proposal(
        self,
        proposal_id: str,
        body: Optional[Dict[str, Any]],
        identity: Any,
        if_match: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str, Optional[str]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept",
                idempotency_key,
            )
        proposal, proposal_scope = self.load_revision_proposal_for_identity(
            proposal_id=proposal_id,
            identity=identity,
        )
        if proposal.get("status") != "preview":
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Only preview WidgetRevisionProposal resources can be accepted",
                "widget_revision_proposal_not_preview",
                details_extra={"proposal_status": proposal.get("status")},
            )

        workspace_id = proposal["workspaceId"]
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        if scope != proposal_scope:
            self.raise_workspace_forbidden("widget_revision_proposal", proposal_id)
        self.require_workspace_etag(if_match, workspace)

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
            self.validation_failed(
                ["acceptanceAction must be apply or keep_original_add_modified_copy"],
                status_code=400,
            )

        current_view, current_widget = _find_widget(workspace, proposal["widgetId"])
        if current_view is None or current_widget is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Widget {proposal['widgetId']!r} not found",
                "workspace_widget_not_found",
            )
        if current_view.get("id") != proposal.get("viewId"):
            self.validation_failed(["proposal viewId no longer matches the widget location"], status_code=409)
        if _stable_hash(current_widget) != _stable_hash(proposal["beforeSpec"]):
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                412,
                ErrorCode.PRECONDITION_FAILED,
                "Widget changed after the revision proposal preview was created.",
                "widget_revision_before_spec_mismatch",
                details_extra={"workspace_id": workspace_id, "widget_id": proposal["widgetId"]},
            )

        updated = copy.deepcopy(workspace)
        updated_view, updated_widget = _find_widget(updated, proposal["widgetId"])
        if updated_view is None or updated_widget is None:
            self.validation_failed(["proposal target widget no longer exists"], status_code=409)

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
                self.validation_failed(["copyWidgetId cannot be empty"], status_code=400)
            if _find_widget(updated, copied_widget_id)[1] is not None:
                ErrorCode = self._error_code_enum()
                raise self.bff_error(
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
        errors = _validate_view(updated_view, now=self.utc_now(), path="updatedView")
        if errors:
            self.validation_failed(errors)

        now = self.utc_now()
        updated = _touch_workspace(updated, now=now, generated_by="trading_servant")
        version = self.persist_workspace_with_version(
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
        self.store.upsert_widget_revision_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        return proposal, updated, version, applied_action, copied_widget_id

    def list_workspace_versions(self, workspace_id: str, identity: Any) -> List[Dict[str, Any]]:
        _workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        versions = self.store.list_workspace_version_records(
            workspace_id,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        for version in versions:
            _normalize_views_legacy_data_availability(version.get("views") or [])
        return versions

    def rollback_workspace_version(
        self,
        workspace_id: str,
        version_id: str,
        body: Optional[Dict[str, Any]],
        identity: Any,
        if_match: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        if idempotency_key:
            self.check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback",
                idempotency_key,
            )
        workspace, scope = self.load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        self.require_workspace_etag(if_match, workspace)
        target = self.store.get_workspace_version_record(
            workspace_id,
            version_id,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        if target is None:
            ErrorCode = self._error_code_enum()
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomDashboardVersion {version_id!r} not found",
                "workspace_version_not_found",
            )

        restored_views = _normalize_views_legacy_data_availability(
            copy.deepcopy(target.get("views") or [])
        )
        validation_errors: List[str] = []
        for view_index, view in enumerate(restored_views):
            validation_errors.extend(_validate_view(view, now=self.utc_now(), path=f"views[{view_index}]"))
        if validation_errors:
            self.validation_failed(validation_errors)

        now = self.utc_now()
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
        version = self.persist_workspace_with_version(
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
        return updated, version, target

    # -----------------------------------------------------------------------
    # Decision / Intent Helpers
    # -----------------------------------------------------------------------

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

    def _intent_from_decision(
        self,
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

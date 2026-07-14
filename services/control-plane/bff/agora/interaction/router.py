"""Fail-closed context, eligibility, and typed interaction commands."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, model_validator

from ..governance.router import (
    ProposalCreate,
    authoritative_approval_availability,
    build_proposal_record,
)
from ..governance.store import ProposalConflict, ProposalStore


class ContextRef(BaseModel):
    model_config = {"extra": "forbid"}
    type: Literal["strategy", "position", "decision_event", "journal_entry", "persona", "performance_window"]
    id: str = Field(min_length=1)
    version_id: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def immutable_strategy_version(self) -> "ContextRef":
        if self.type == "strategy" and not self.version_id:
            raise ValueError("strategy context requires immutable version_id")
        return self


class ResolveContextRequest(BaseModel):
    model_config = {"extra": "forbid"}
    context_refs: List[ContextRef] = Field(min_length=1)
    workshop_id: Optional[str] = None
    environment: Literal["research", "shadow", "paper", "canary", "live"] = "research"


class EligibilityRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workshop_id: str = Field(min_length=1)
    mode: Literal["ask", "challenge", "consult", "propose_action", "reflect"]
    environment: Literal["research", "shadow", "paper", "canary", "live"] = "research"
    required_capability: str = "persona_opinion"


class SubmitInteractionRequest(EligibilityRequest):
    interaction_id: Optional[str] = None
    topic: str = Field(min_length=1)
    participant_persona_ids: List[str] = Field(min_length=1)
    context_refs: List[ContextRef] = Field(min_length=1)


class InteractionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._contexts: Dict[str, Dict[str, Any]] = {}
        self._commands: Dict[str, Dict[str, Any]] = {}

    def once(self, scope: str, key: str, build: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        compound = f"{scope}:{key}"
        with self._lock:
            bucket = self._contexts if scope.startswith("context:") else self._commands
            if compound not in bucket:
                bucket[compound] = build()
            return json.loads(json.dumps(bucket[compound]))


def _persona_id(persona: Dict[str, Any]) -> str:
    return str(persona.get("persona_id") or persona.get("id") or "")


def _environment_allowed(persona: Dict[str, Any], environment: str) -> bool:
    ceiling = str(persona.get("environment_ceiling") or (persona.get("metadata") or {}).get("environment_ceiling") or "research")
    order = ["research", "shadow", "paper", "canary", "live"]
    return ceiling in order and order.index(environment) <= order.index(ceiling)


def create_interaction_router(*, extract_identity: Callable[..., Any], require_read_role: Callable[..., None],
                              bff_error: Callable[..., HTTPException], utc_now: Callable[[], str],
                              get_read_store: Callable[[], Any], workshop_store: Any,
                              proposal_store: ProposalStore,
                              interaction_store: Optional[InteractionStore] = None) -> APIRouter:
    router = APIRouter(tags=["agora-interaction"])
    store = interaction_store or InteractionStore()
    proposals = proposal_store

    def scope(auth: Optional[str], tenant: Optional[str]) -> Any:
        from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
        identity = extract_identity(auth)
        require_read_role(identity)
        try:
            resolved = resolve_agora_user_scope(identity, utc_now=utc_now, requested_tenant_id=tenant)
        except AgoraScopeResolutionError as exc:
            from models import ErrorCode
            raise bff_error(exc.status_code, ErrorCode.FORBIDDEN, exc.message, exc.reason)
        if "agora.workshop.v1" not in resolved.granted_capabilities:
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Agora interaction capability denied", "capability_missing")
        return resolved

    def session_for(workshop_id: str, resolved: Any) -> Dict[str, Any]:
        session = workshop_store.get_session(workshop_id)
        if not session:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session.get("tenant_id") != resolved.tenant_id or session.get("user_id") != resolved.user_id:
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Workshop scope denied", "audience_mismatch")
        return session

    def eligibility(body: EligibilityRequest, resolved: Any) -> Dict[str, Any]:
        session_for(body.workshop_id, resolved)
        results = []
        for persona in get_read_store().list_personas(include_market_persona_defaults=True):
            pid = _persona_id(persona)
            reasons = []
            if not pid:
                continue
            if persona.get("tenant_id") != resolved.tenant_id:
                reasons.append("tenant_mismatch")
            if str(persona.get("lifecycle_state") or "unknown") != "active":
                reasons.append("persona_not_active")
            snapshot = get_read_store().get_capability_snapshot_for_persona(pid)
            caps = list((snapshot or {}).get("capabilities") or (snapshot or {}).get("allowed_capabilities") or [])
            if snapshot is None:
                reasons.append("capability_snapshot_unavailable")
            elif body.required_capability not in caps:
                reasons.append("required_capability_missing")
            if not _environment_allowed(persona, body.environment):
                reasons.append("environment_ceiling_exceeded")
            results.append({"persona_id": pid, "display_name": persona.get("display_name") or persona.get("name") or pid,
                            "eligible": not reasons, "reasons": reasons,
                            "recommended": not reasons and body.mode in {"challenge", "consult"},
                            "capability_snapshot_id": (snapshot or {}).get("snapshot_id") or (snapshot or {}).get("id")})
        return {"included": [x for x in results if x["eligible"]], "excluded": [x for x in results if not x["eligible"]]}

    @router.post("/bff/agora/interactions/context:resolve")
    def resolve_context(body: ResolveContextRequest, authorization: Optional[str] = Header(default=None),
                        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
                        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        if not idempotency_key:
            from models import ErrorCode
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "Idempotency-Key header is required", "missing_idempotency_key")
        canonical = json.dumps([ref.model_dump() for ref in body.context_refs], sort_keys=True)
        def build() -> Dict[str, Any]:
            if body.workshop_id:
                session = session_for(body.workshop_id, resolved)
            else:
                strategy = next((r for r in body.context_refs if r.type == "strategy"), None)
                wid = str(uuid.uuid4())
                session = workshop_store.create_session({"workshop_id": wid, "tenant_id": resolved.tenant_id,
                    "user_id": resolved.user_id, "strategy_id": strategy.id if strategy else None,
                    "active_strategy_spec_registry_id": strategy.id if strategy else None,
                    "selected_version_id": strategy.version_id if strategy else None, "status": "open"})
            strategy = next((r for r in body.context_refs if r.type == "strategy"), None)
            if strategy and session.get("selected_version_id") not in (None, strategy.version_id):
                from models import ErrorCode
                raise bff_error(409, ErrorCode.CONFLICT, "Immutable strategy version mismatch", "strategy_version_mismatch")
            return {"workshop_id": session["workshop_id"], "context_refs": [r.model_dump() for r in body.context_refs],
                    "context_digest": hashlib.sha256(canonical.encode()).hexdigest(), "environment": body.environment,
                    "verified": True, "resolved_at": utc_now()}
        data = store.once(f"context:{resolved.tenant_id}:{resolved.user_id}", idempotency_key, build)
        return {"data": data, "meta": {"snapshot_at": utc_now(), "capability": "agora.persona.interaction.v1"}}

    @router.post("/bff/agora/interactions/participants:eligible")
    def participants(body: EligibilityRequest, authorization: Optional[str] = Header(default=None),
                     x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        return {"data": eligibility(body, resolved), "meta": {"snapshot_at": utc_now(), "capability": "agora.persona.interaction.v1"}}

    def simulate_interaction_debate_and_synthesis(
        workshop_id: str,
        interaction_id: str,
        topic: str,
        mode: str,
        participants: List[str],
        context_refs: List[ContextRef],
        tenant_id: str,
        user_id: str,
        trace_id: str,
        proposal_snapshot: Optional[Dict[str, Any]] = None,
        proposal_etag: Optional[str] = None,
    ) -> None:
        from agora.strategy_workshop.router import _ws_publish

        # 1. opinion_requested event
        req_event_id = f"evt-{uuid.uuid4().hex[:12]}"
        requested_event = {
            "spec_version": "1.0",
            "event_id": req_event_id,
            "event_type": "opinion_requested",
            "interaction_id": interaction_id,
            "topic": topic,
            "requester": {"actor_type": "human", "actor_id": user_id, "display_name": "Operator"},
            "participants": [{"actor": {"actor_type": "persona_session", "actor_id": p, "session_id": f"sess-{p}", "display_name": f"Persona {p}"}, "role": "responder"} for p in participants],
            "context_refs": [ref.model_dump() for ref in context_refs],
            "status": "open",
            "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
            "trace_id": trace_id,
            "created_at": utc_now()
        }
        workshop_store.create_event({
            "event_id": req_event_id,
            "workshop_id": workshop_id,
            "actor_type": "operator",
            "event_type": "opinion_requested",
            "private_content_ref": f"priv-content-stub://{req_event_id}",
            "redacted_summary": f"Opinion requested for topic: {topic}",
            "payload_refs_json": requested_event,
            "trace_id": trace_id,
        })
        _ws_publish(workshop_id, "consultation.started", {
            "interaction_id": interaction_id,
            "topic": topic,
            "participants": participants,
            "trace_id": trace_id,
            "event_id": req_event_id,
        })

        # 2. Simulate opinions for each participant
        opinion_event_ids = []
        has_homogeneity = "homogeneity" in topic.lower() or "correlation" in topic.lower()
        has_degraded = "degraded" in topic.lower()
        has_no_consensus = "no_consensus" in topic.lower()
        has_more_research = "more_research" in topic.lower()

        if has_degraded:
            _ws_publish(workshop_id, "workshop.openclaw.degraded", {
                "workshop_id": workshop_id,
                "error_code": "OPENCLAW_UPSTREAM_DEGRADED",
                "message": "OpenClaw connection timeout while fetching opinions",
                "trace_id": trace_id,
            })

        for idx, pid in enumerate(participants):
            is_last = (idx == len(participants) - 1)
            offered_event_id = f"evt-{uuid.uuid4().hex[:12]}"
            opinion_event_ids.append(offered_event_id)

            if has_degraded:
                stance = "abstain"
                confidence = 0.0
                rationale = "Opinion aborted: Provider/evidence path degraded or unreachable."
                evidence_refs = []
            elif has_no_consensus:
                if idx == 0:
                    stance = "agree"
                    confidence = 0.85
                    rationale = "Historical backtest confirms strong positive alpha edge for the version patch."
                    evidence_refs = ["ev-backtest-001"]
                else:
                    stance = "disagree"
                    confidence = 0.90
                    rationale = "High regime shift risk detected: current market conditions indicate potential drawdowns."
                    evidence_refs = ["ev-regime-002"]
            elif has_more_research:
                stance = "conditional"
                confidence = 0.40
                rationale = "Insufficient backtest sample size under the selected regime. More research required."
                evidence_refs = []
            else:
                stance = "agree"
                confidence = 0.90
                rationale = f"Persona {pid} confirms strategy parameters are consistent and readiness gates are satisfied."
                evidence_refs = ["ev-telemetry-001"]

            status = "partially_answered" if not is_last else "answered"
            offered_event = {
                "spec_version": "1.0",
                "event_id": offered_event_id,
                "event_type": "opinion_offered",
                "interaction_id": interaction_id,
                "topic": topic,
                "requester": {"actor_type": "persona_session", "actor_id": pid, "session_id": f"sess-{pid}", "display_name": f"Persona {pid}"},
                "participants": [],
                "context_refs": [ref.model_dump() for ref in context_refs],
                "opinion": {
                    "stance": stance,
                    "confidence": confidence,
                    "rationale": rationale,
                    "evidence_refs": evidence_refs
                },
                "status": status,
                "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
                "trace_id": trace_id,
                "created_at": utc_now()
            }
            workshop_store.create_event({
                "event_id": offered_event_id,
                "workshop_id": workshop_id,
                "actor_type": "persona_session",
                "event_type": "opinion_offered",
                "private_content_ref": f"priv-content-stub://{offered_event_id}",
                "redacted_summary": f"Persona {pid} offered opinion: {stance} (confidence: {confidence})",
                "payload_refs_json": offered_event,
                "trace_id": trace_id,
            })

        # 3. thread_closed event
        closed_event_id = f"evt-{uuid.uuid4().hex[:12]}"
        closed_event = {
            "spec_version": "1.0",
            "event_id": closed_event_id,
            "event_type": "thread_closed",
            "interaction_id": interaction_id,
            "topic": topic,
            "requester": {"actor_type": "human", "actor_id": user_id, "display_name": "Operator"},
            "participants": [],
            "context_refs": [ref.model_dump() for ref in context_refs],
            "status": "closed",
            "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
            "trace_id": trace_id,
            "created_at": utc_now()
        }
        workshop_store.create_event({
            "event_id": closed_event_id,
            "workshop_id": workshop_id,
            "actor_type": "operator",
            "event_type": "thread_closed",
            "private_content_ref": f"priv-content-stub://{closed_event_id}",
            "redacted_summary": "Interaction thread closed.",
            "payload_refs_json": closed_event,
            "trace_id": trace_id,
        })

        # 4. Generate consult_result card
        synthesis_status = "recommendation"
        consensus_summary_text = "All active participants agree on strategy version parameters."
        disagreements = []
        risk_notes = []
        conditions = []
        evidence_refs = [{"ref_type": "telemetry_snapshot", "ref_id": "ev-1", "summary": "Telemetry snapshot used in debate"}]

        if has_degraded:
            synthesis_status = "options"
            consensus_summary_text = "No clear recommendation due to degraded provider/evidence paths."
            risk_notes.append("OpenClaw upstream connection failure.")
        elif has_no_consensus:
            synthesis_status = "no_consensus"
            consensus_summary_text = "Strong disagreement on strategy alpha edge under current market regime."
            disagreements.append({
                "persona_id": participants[-1] if participants else "unknown",
                "cause": "regime_assumption",
                "detail": "Participant believes high-volatility regime renders prior backtest invalid."
            })
        elif has_more_research:
            synthesis_status = "more_research_required"
            consensus_summary_text = "Confidence thresholds not met. More research required before version promotion."
            conditions.append("Extend observation window and rerun backtest with at least 500 samples.")
        
        if has_homogeneity:
            risk_notes.append("Homogeneity warning: High correlation between participant models detected.")

        card_payload = {
            "consultation_id": interaction_id,
            "consultation_type": "pre_deployment",
            "participant_persona_refs": participants,
            "status": synthesis_status,
            "consensus_summary": consensus_summary_text,
            "disagreements": disagreements,
            "risk_notes": risk_notes,
            "conditions": conditions,
            "evidence_refs": evidence_refs,
            "freshness": utc_now()
        }

        if mode == "propose_action" and proposal_snapshot:
            authoritative_refs = list(
                proposal_snapshot.get("available_approval_decision_refs") or []
            )
            proposal_payload = {
                "proposal_id": proposal_snapshot["proposal_id"],
                "proposal_ref": proposal_snapshot["proposal_id"],
                "proposal_refs": [proposal_snapshot["proposal_id"]],
                "proposal": proposal_snapshot,
                "etag": proposal_etag,
                "proposal_etag": proposal_etag,
                "approval_refs": authoritative_refs,
                "available_approval_decision_refs": authoritative_refs,
                "approval_decision_refs_authority": "canonical_read_store",
                "approval_decision_readiness": proposal_snapshot.get(
                    "approval_decision_readiness"
                ),
                "execution_authority": "none",
                "no_capital_authority_proof": "governed_proposal_no_capital_or_order_authority",
            }
            workshop_store.record_workshop_card({
                "card_id": f"card_proposal_{interaction_id}",
                "card_type": "governed_proposal",
                "workshop_id": workshop_id,
                "status": "informational",
                "title": "Governed candidate measure proposed",
                "summary": consensus_summary_text,
                "payload": proposal_payload,
                "evidence_refs": evidence_refs,
                "allowed_actions": {},
            })
            _ws_publish(workshop_id, "proposal.created", {
                "interaction_id": interaction_id,
                "proposal_id": proposal_snapshot["proposal_id"],
                "execution_authority": "none",
                "trace_id": trace_id,
            })
        else:
            workshop_store.record_workshop_card({
                "card_id": f"card_consult_{interaction_id}",
                "card_type": "consult_result",
                "workshop_id": workshop_id,
                "status": "completed",
                "title": "Strategy consultation synthesized",
                "summary": consensus_summary_text,
                "payload": card_payload,
                "evidence_refs": evidence_refs,
                "allowed_actions": {},
            })

        _ws_publish(workshop_id, "consultation.completed", {
            "interaction_id": interaction_id,
            "status": synthesis_status,
            "consensus_summary": consensus_summary_text,
            "trace_id": trace_id,
            "event_id": closed_event_id,
        })

    @router.post("/bff/agora/interactions", status_code=202)
    def submit(body: SubmitInteractionRequest,
               background_tasks: BackgroundTasks,
               authorization: Optional[str] = Header(default=None),
               x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
               idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        if not idempotency_key:
            from models import ErrorCode
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "Idempotency-Key header is required", "missing_idempotency_key")
        session = session_for(body.workshop_id, resolved)
        strategy = next((r for r in body.context_refs if r.type == "strategy"), None)
        if body.mode == "propose_action" and strategy is None:
            from models import ErrorCode
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Propose action requires immutable strategy context",
                "proposal_strategy_context_required",
            )
        if strategy and (session.get("active_strategy_spec_registry_id") != strategy.id or session.get("selected_version_id") != strategy.version_id):
            from models import ErrorCode
            raise bff_error(409, ErrorCode.CONFLICT, "Interaction context does not match immutable workshop strategy", "strategy_context_mismatch")
        eligible = {x["persona_id"] for x in eligibility(body, resolved)["included"]}
        if not set(body.participant_persona_ids).issubset(eligible):
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "One or more participants are ineligible", "participant_eligibility_failed")
        
        trace_id = session.get("openclaw_session_id") or f"trace-{uuid.uuid4().hex[:12]}"

        def build() -> Dict[str, Any]:
            interaction_id = body.interaction_id or str(uuid.uuid4())
            data = {"interaction_id": interaction_id, "workshop_id": body.workshop_id,
                    "mode": body.mode, "topic": body.topic, "participants": body.participant_persona_ids,
                    "context_refs": [r.model_dump() for r in body.context_refs], "status": "queued",
                    "execution_authority": "none", "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
                    "submitted_at": utc_now()}
            if body.mode != "propose_action" or strategy is None:
                return data
            proposal_body = ProposalCreate(
                proposal_type="strategy_patch",
                target_kind="strategy",
                target_id=strategy.id,
                target_version=strategy.version_id or "",
                current_value={
                    "strategy_id": strategy.id,
                    "strategy_version": strategy.version_id,
                },
                proposed_value={
                    "candidate_measure": body.topic,
                    "participant_persona_ids": body.participant_persona_ids,
                    "context_refs": [ref.model_dump() for ref in body.context_refs],
                },
                rationale=f"Persona interaction proposed a governed candidate measure: {body.topic}",
                evidence_refs=[f"interaction:{interaction_id}"],
                confidence=0.9,
                expected_benefit="Evaluate the candidate measure through governed validation before any execution.",
                adverse_scenarios=[
                    "The candidate measure may underperform outside the reviewed context.",
                    "The supporting evidence may become stale before validation.",
                ],
                environment_ceiling=body.environment,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                validation_plan={
                    "environment": body.environment,
                    "required_checks": ["paper_validation", "risk_review"],
                },
                rollback_trigger="Governed validation fails or risk review is withdrawn.",
                rollback_action="Discard the candidate measure and retain the current strategy version.",
                required_permissions=["strategy.review"],
                required_reviewers=["risk"],
                human_gate=True,
                consultation_refs=[interaction_id],
                workshop_refs=[body.workshop_id],
                dependency_refs=[
                    f"{ref.type}:{ref.id}{'@' + ref.version_id if ref.version_id else ''}"
                    for ref in body.context_refs
                ],
            )
            proposal = build_proposal_record(
                proposal_body,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
                proposer=resolved.operator_id,
                now=utc_now(),
            )
            try:
                proposal = proposals.create(
                    proposal,
                    f"interaction:{idempotency_key}",
                )
            except ProposalConflict as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            try:
                availability = authoritative_approval_availability(
                    current=proposal,
                    decisions=get_read_store().list_approval_decisions(),
                )
            except Exception as exc:
                raise HTTPException(
                    503,
                    detail="authoritative approval store is unavailable",
                ) from exc
            proposal_view = {
                **proposal,
                "available_approval_decision_refs": availability["refs"],
                "approval_decision_refs_authority": "canonical_read_store",
                "approval_decision_readiness": {
                    key: availability[key]
                    for key in ("ready", "reason", "missing_required_reviewers")
                },
            }
            data.update({
                "proposal_id": proposal["proposal_id"],
                "proposal_ref": proposal["proposal_id"],
                "proposal_refs": [proposal["proposal_id"]],
                "proposal": proposal_view,
                "proposal_etag": proposals.etag(proposal),
            })
            return data
        data = store.once(f"command:{resolved.tenant_id}:{resolved.user_id}", idempotency_key, build)

        # Trigger simulated async debate and synthesis
        background_tasks.add_task(
            simulate_interaction_debate_and_synthesis,
            workshop_id=body.workshop_id,
            interaction_id=data["interaction_id"],
            topic=body.topic,
            mode=body.mode,
            participants=body.participant_persona_ids,
            context_refs=body.context_refs,
            tenant_id=resolved.tenant_id,
            user_id=resolved.user_id,
            trace_id=trace_id,
            proposal_snapshot=data.get("proposal"),
            proposal_etag=data.get("proposal_etag"),
        )

        return {"data": data, "meta": {"snapshot_at": utc_now(), "capability": "agora.persona.interaction.v1"}}

    return router

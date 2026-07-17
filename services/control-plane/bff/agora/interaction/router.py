"""Fail-closed context, eligibility, and typed interaction commands."""
from __future__ import annotations

import hashlib
import json
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
from ..governance.store import ProposalConflict, ProposalStore, payload_fingerprint
from .provider import build_participant_admission
from .runner import run_selected_persona_interaction


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


class InteractionStore(ProposalStore):
    """Backward-compatible isolated store for unit tests."""

    def __init__(self) -> None:
        super().__init__(backend="off")


def _persona_id(persona: Dict[str, Any]) -> str:
    return str(persona.get("persona_id") or persona.get("id") or "")


def _persona_operational(persona: Dict[str, Any]) -> bool:
    # Only explicit Persona Registry lifecycle truth is accepted.  Generic
    # deployment labels (for example ``deployed``) must not grant interaction
    # eligibility.
    lifecycle = str(persona.get("lifecycle_state") or "").strip().lower()
    return lifecycle in {"active", "paper_running", "paper_only"}


def _environment_allowed(persona: Dict[str, Any], environment: str) -> bool:
    order = ["research", "shadow", "paper", "canary", "live"]
    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    explicit_ceiling = str(
        persona.get("environment_ceiling") or metadata.get("environment_ceiling") or ""
    ).strip().lower()
    ceiling = explicit_ceiling if explicit_ceiling in order else ""

    # Missing or unrecognised ceiling truth is deliberately fail-closed even
    # for research requests.  A deployment label alone never creates this
    # separate interaction authority.
    return bool(ceiling) and environment in order and order.index(environment) <= order.index(ceiling)


def create_interaction_router(*, extract_identity: Callable[..., Any], require_read_role: Callable[..., None],
                              require_write_role: Callable[..., None],
                              bff_error: Callable[..., HTTPException], utc_now: Callable[[], str],
                              get_read_store: Callable[[], Any], workshop_store: Any,
                              proposal_store: ProposalStore,
                              interaction_store: Optional[InteractionStore] = None) -> APIRouter:
    router = APIRouter(tags=["agora-interaction"])
    # The production proposal store also owns command idempotency/outbox state,
    # so restarts and independent BFF workers share one durable truth.
    store = interaction_store or proposal_store
    proposals = proposal_store

    def scope(auth: Optional[str], tenant: Optional[str], *, write: bool = False) -> Any:
        from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
        identity = extract_identity(auth)
        require_read_role(identity)
        if write:
            require_write_role(identity)
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
        read_store = get_read_store()
        for persona in read_store.list_personas(include_market_persona_defaults=True):
            pid = _persona_id(persona)
            reasons = []
            if not pid:
                continue
            if persona.get("tenant_id") != resolved.tenant_id:
                reasons.append("tenant_mismatch")
            if not _persona_operational(persona):
                reasons.append("persona_not_active")
            metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
            declared_snapshot_id = str(
                persona.get("capability_snapshot_id")
                or metadata.get("capability_snapshot_id")
                or ""
            ).strip()
            snapshot = None
            if declared_snapshot_id:
                get_snapshot = getattr(read_store, "get_capability_snapshot", None)
                if callable(get_snapshot):
                    snapshot = get_snapshot(declared_snapshot_id)
                if snapshot is not None and str(snapshot.get("persona_id") or "") != pid:
                    reasons.append("capability_snapshot_persona_mismatch")
                    snapshot = None
            else:
                snapshot = read_store.get_capability_snapshot_for_persona(pid)
            caps = list((snapshot or {}).get("capabilities") or (snapshot or {}).get("allowed_capabilities") or [])
            if snapshot is None:
                if "capability_snapshot_persona_mismatch" not in reasons:
                    reasons.append("capability_snapshot_unavailable")
            elif body.required_capability not in caps:
                reasons.append("required_capability_missing")
            elif caps != ["persona_opinion"]:
                reasons.append("capability_snapshot_not_advice_only")
            if not _environment_allowed(persona, body.environment):
                reasons.append("environment_ceiling_exceeded")
            participant_snapshot = None
            if not reasons:
                try:
                    participant_snapshot, _ = build_participant_admission(
                        persona=persona,
                        capability_snapshot=snapshot or {},
                        environment=body.environment,
                        captured_at=utc_now(),
                    )
                except ValueError:
                    reasons.append("persona_opinion_admission_invalid")
            results.append({"persona_id": pid, "display_name": persona.get("display_name") or persona.get("name") or pid,
                            "eligible": not reasons, "reasons": reasons,
                            "recommended": not reasons and body.mode in {"challenge", "consult"},
                            "capability_snapshot_id": (snapshot or {}).get("snapshot_id") or (snapshot or {}).get("id"),
                            "participant_snapshot": participant_snapshot})
        return {"included": [x for x in results if x["eligible"]], "excluded": [x for x in results if not x["eligible"]]}

    @router.post("/bff/agora/interactions/context:resolve")
    def resolve_context(body: ResolveContextRequest, authorization: Optional[str] = Header(default=None),
                        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
                        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        if not idempotency_key:
            from models import ErrorCode
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "Idempotency-Key header is required", "missing_idempotency_key")
        request_payload = body.model_dump(mode="json")
        canonical = json.dumps(request_payload, sort_keys=True)
        def build() -> Dict[str, Any]:
            if body.workshop_id:
                session = session_for(body.workshop_id, resolved)
            else:
                strategy = next((r for r in body.context_refs if r.type == "strategy"), None)
                wid = "ws_" + hashlib.sha256(
                    f"{resolved.tenant_id}:{resolved.user_id}:{idempotency_key}".encode()
                ).hexdigest()[:24]
                session = workshop_store.get_session(wid)
                if session is None:
                    session = workshop_store.create_session({"workshop_id": wid, "tenant_id": resolved.tenant_id,
                        "user_id": resolved.user_id, "strategy_id": strategy.id if strategy else None,
                        "active_strategy_spec_registry_id": strategy.version_id if strategy else None,
                        # selected_version_id is the active Workshop-version alias.  An
                        # interaction context carries a Strategy Registry version, not
                        # a Workshop version, so resolving context must not populate it.
                        "selected_version_id": None, "status": "open"})
            strategy = next((r for r in body.context_refs if r.type == "strategy"), None)
            if strategy and (
                session.get("strategy_id") != strategy.id
                or session.get("active_strategy_spec_registry_id") != strategy.version_id
            ):
                from models import ErrorCode
                raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Immutable strategy version mismatch", "strategy_version_mismatch")
            return {"workshop_id": session["workshop_id"], "context_refs": [r.model_dump() for r in body.context_refs],
                    "context_digest": hashlib.sha256(canonical.encode()).hexdigest(), "environment": body.environment,
                    "verified": True, "resolved_at": utc_now()}
        try:
            result = store.once(
                f"context:{resolved.tenant_id}:{resolved.user_id}",
                idempotency_key,
                payload_fingerprint(request_payload),
                build,
            )
        except ProposalConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        data = result.data
        if result.run_side_effects:
            store.complete_side_effects(f"context:{resolved.tenant_id}:{resolved.user_id}", idempotency_key)
        return {"data": data, "meta": {"snapshot_at": utc_now(), "capability": "agora.persona.interaction.v1"}}

    @router.post("/bff/agora/interactions/participants:eligible")
    def participants(body: EligibilityRequest, authorization: Optional[str] = Header(default=None),
                     x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        return {"data": eligibility(body, resolved), "meta": {"snapshot_at": utc_now(), "capability": "agora.persona.interaction.v1"}}

    @router.post("/bff/agora/interactions", status_code=202)
    def submit(body: SubmitInteractionRequest,
               background_tasks: BackgroundTasks,
               authorization: Optional[str] = Header(default=None),
               x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
               idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
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
        if strategy and (
            session.get("strategy_id") != strategy.id
            or session.get("active_strategy_spec_registry_id") != strategy.version_id
        ):
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Interaction context does not match immutable workshop strategy", "strategy_context_mismatch")
        eligible = {x["persona_id"] for x in eligibility(body, resolved)["included"]}
        if not set(body.participant_persona_ids).issubset(eligible):
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "One or more participants are ineligible", "participant_eligibility_failed")
        command_scope = f"command:{resolved.tenant_id}:{resolved.user_id}"
        request_payload = body.model_dump(mode="json")
        command_fingerprint = payload_fingerprint(request_payload)
        trace_id = session.get("openclaw_session_id") or f"trace-{uuid.uuid4().hex[:12]}"

        def build() -> Dict[str, Any]:
            interaction_id = body.interaction_id or "int_" + hashlib.sha256(
                f"{command_scope}:{idempotency_key}".encode()
            ).hexdigest()[:24]
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
                    f"interaction:{interaction_id}",
                    fingerprint=command_fingerprint,
                )
            except ProposalConflict as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            try:
                availability = authoritative_approval_availability(
                    current=proposal,
                    decisions=get_read_store().list_approval_decisions(),
                )
            except Exception:
                availability = {
                    "refs": [], "ready": False,
                    "reason": "authoritative_approval_store_unavailable",
                    "missing_required_reviewers": list(proposal.get("required_reviewers") or []),
                }
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
        try:
            result = store.once(command_scope, idempotency_key, command_fingerprint, build)
        except ProposalConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        data = result.data
        trace_id = session.get("openclaw_session_id") or f"trace-{data['interaction_id']}"

        if result.run_side_effects:
            def run_side_effects() -> None:
                try:
                    run_selected_persona_interaction(
                        workshop_store=workshop_store,
                        read_store=get_read_store(),
                        workshop_id=body.workshop_id,
                        interaction_id=data["interaction_id"],
                        topic=body.topic,
                        mode=body.mode,
                        participants=body.participant_persona_ids,
                        context_refs=[ref.model_dump(mode="json") for ref in body.context_refs],
                        environment=body.environment,
                        tenant_id=resolved.tenant_id,
                        user_id=resolved.user_id,
                        operator_id=resolved.operator_id,
                        trace_id=trace_id,
                        proposal_snapshot=data.get("proposal"),
                        proposal_etag=data.get("proposal_etag"),
                        occurred_at=data["submitted_at"],
                    )
                except Exception:
                    store.release_side_effects(command_scope, idempotency_key)
                    raise
                else:
                    store.complete_side_effects(command_scope, idempotency_key)

            # Run the claimed deterministic outbox work before acknowledging.
            # This avoids leaving an undrained pending row if the process exits
            # after returning 202; a failed run is released and safely retried.
            try:
                run_side_effects()
            except ValueError as exc:
                if "event_id reused" in str(exc):
                    raise HTTPException(409, detail=str(exc)) from exc
                raise

        return {"data": data, "meta": {"snapshot_at": utc_now(), "capability": "agora.persona.interaction.v1"}}

    return router

"""Fail-closed context, eligibility, and typed interaction commands."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, model_validator


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
                              interaction_store: Optional[InteractionStore] = None) -> APIRouter:
    router = APIRouter(tags=["agora-interaction"])
    store = interaction_store or InteractionStore()

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
            if str(persona.get("tenant_id") or resolved.tenant_id) != resolved.tenant_id:
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

    @router.post("/bff/agora/interactions", status_code=202)
    def submit(body: SubmitInteractionRequest, authorization: Optional[str] = Header(default=None),
               x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
               idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        if not idempotency_key:
            from models import ErrorCode
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "Idempotency-Key header is required", "missing_idempotency_key")
        session = session_for(body.workshop_id, resolved)
        strategy = next((r for r in body.context_refs if r.type == "strategy"), None)
        if strategy and (session.get("active_strategy_spec_registry_id") != strategy.id or session.get("selected_version_id") != strategy.version_id):
            from models import ErrorCode
            raise bff_error(409, ErrorCode.CONFLICT, "Interaction context does not match immutable workshop strategy", "strategy_context_mismatch")
        eligible = {x["persona_id"] for x in eligibility(body, resolved)["included"]}
        if not set(body.participant_persona_ids).issubset(eligible):
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "One or more participants are ineligible", "participant_eligibility_failed")
        def build() -> Dict[str, Any]:
            return {"interaction_id": body.interaction_id or str(uuid.uuid4()), "workshop_id": body.workshop_id,
                    "mode": body.mode, "topic": body.topic, "participants": body.participant_persona_ids,
                    "context_refs": [r.model_dump() for r in body.context_refs], "status": "queued",
                    "execution_authority": "none", "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
                    "submitted_at": utc_now()}
        data = store.once(f"command:{resolved.tenant_id}:{resolved.user_id}", idempotency_key, build)
        return {"data": data, "meta": {"snapshot_at": utc_now(), "capability": "agora.persona.interaction.v1"}}

    return router

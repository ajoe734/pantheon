"""Fail-closed context, eligibility, and typed interaction commands."""
from __future__ import annotations

import hashlib
import json
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Header, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from ..governance.router import (
    ProposalCreate,
    authoritative_approval_availability,
    build_proposal_record,
)
from ..governance.store import ProposalConflict, ProposalStore, payload_fingerprint
from .provider import (
    _persona_version,
    authority_boundary,
    build_participant_admission,
    is_persona_operational,
)
from .runner import drain_interaction_outbox, run_selected_persona_interaction
from .store import InteractionConflict, InteractionLifecycleStore


class ContextRef(BaseModel):
    model_config = {"extra": "forbid"}
    type: Literal["strategy", "position", "decision_event", "journal_entry", "persona", "performance_window", "human_inbox_item", "workshop"]
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
    source_route: Optional[str] = Field(default=None, min_length=1)
    focused_object: Optional[Dict[str, Any]] = None
    evidence_cutoff: Optional[datetime] = None
    selected_persona_ids: Optional[List[str]] = None
    initial_mode: Optional[Literal["ask", "challenge", "compare", "propose_action", "reflect"]] = None
    return_route: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def complete_daily_binding(self) -> "ResolveContextRequest":
        fields = (
            self.source_route, self.focused_object, self.evidence_cutoff,
            self.selected_persona_ids, self.initial_mode, self.return_route,
        )
        if any(value is not None for value in fields) and not all(value is not None for value in fields):
            raise ValueError("daily context binding fields are required together")
        if self.selected_persona_ids is not None and len(set(self.selected_persona_ids)) != len(self.selected_persona_ids):
            raise ValueError("selected_persona_ids must be unique")
        return self

    @property
    def has_daily_binding(self) -> bool:
        return self.source_route is not None


class EligibilityRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workshop_id: str = Field(min_length=1)
    mode: Literal["ask", "challenge", "consult", "compare", "propose_action", "reflect"]
    environment: Literal["research", "shadow", "paper", "canary", "live"] = "research"
    required_capability: str = "persona_opinion"


class ImmutableHumanRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    mode: Literal["ask", "challenge", "compare", "propose_action", "reflect"]
    request_text: str = Field(min_length=1)
    submitted_at: datetime
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def exact_digest(self) -> "ImmutableHumanRequest":
        if hashlib.sha256(self.request_text.encode("utf-8")).hexdigest() != self.request_sha256:
            raise ValueError("human request sha256 does not match request_text")
        return self


class FocusedObject(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["persona", "strategy", "decision_event", "journal_entry", "position", "human_inbox_item", "workshop"]
    id: str = Field(min_length=1)
    version: Optional[str] = Field(default=None, min_length=1)


class DailyContextRef(BaseModel):
    model_config = {"extra": "forbid"}
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: Optional[str] = Field(default=None, min_length=1)


class InteractionContextSnapshot(BaseModel):
    model_config = {"extra": "forbid"}
    tenant_id: str = Field(min_length=1)
    source_route: str = Field(min_length=1)
    focused_object: FocusedObject
    context_refs: List[DailyContextRef] = Field(min_length=1)
    strategy_ref: Optional[Dict[str, str]] = None
    decision_ref: Optional[str] = Field(default=None, min_length=1)
    journal_ref: Optional[str] = Field(default=None, min_length=1)
    position_risk_snapshot_refs: List[str] = Field(default_factory=list)
    evidence_cutoff: datetime
    selected_persona_ids: List[str] = Field(min_length=1)
    initial_mode: Literal["ask", "challenge", "compare", "propose_action", "reflect"]
    return_route: str = Field(min_length=1)
    captured_at: datetime


class SubmittedParticipant(BaseModel):
    model_config = {"extra": "forbid"}
    persona_id: str = Field(min_length=1)
    persona_version: str = Field(min_length=1)
    session_persona_id: str = Field(min_length=1)
    display_name: Optional[str] = None
    provider_agent_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    environment_ceiling: Literal["analysis", "research", "shadow", "paper"]
    capability_snapshot: List[str] = Field(min_length=1)
    captured_at: datetime


class SubmitInteractionRequest(BaseModel):
    """PINT-015 v1.9 request plus the pre-v1.9 compatibility shape."""
    model_config = {"extra": "forbid"}
    workshop_id: str = Field(min_length=1)
    interaction_id: Optional[str] = None
    demo_run_id: Optional[str] = None
    human_request: Optional[ImmutableHumanRequest] = None
    context_snapshot: Optional[InteractionContextSnapshot] = None
    participants: Optional[List[SubmittedParticipant]] = None
    mode: Optional[Literal["ask", "challenge", "consult", "compare", "propose_action", "reflect"]] = None
    topic: Optional[str] = Field(default=None, min_length=1)
    participant_persona_ids: Optional[List[str]] = None
    context_refs: Optional[List[ContextRef]] = None
    environment: Literal["research", "shadow", "paper", "canary", "live"] = "research"
    required_capability: str = "persona_opinion"

    @model_validator(mode="after")
    def unique_participants(self) -> "SubmitInteractionRequest":
        v19 = self.human_request is not None or self.context_snapshot is not None or self.participants is not None
        if v19 and not (self.human_request and self.context_snapshot and self.participants):
            raise ValueError("human_request, context_snapshot, and participants are required together")
        if v19:
            legacy_fields = {
                "mode", "topic", "participant_persona_ids", "context_refs",
                "environment", "required_capability",
            } & self.model_fields_set
            if legacy_fields:
                raise ValueError(
                    "v1.9 interaction request cannot include legacy fields: "
                    + ",".join(sorted(legacy_fields))
                )
        if not v19 and not (self.mode and self.topic and self.participant_persona_ids and self.context_refs):
            raise ValueError("legacy interaction shape requires mode, topic, participant_persona_ids, and context_refs")
        ids = self.selected_persona_ids
        if len(ids) != len(set(ids)):
            raise ValueError("participant_persona_ids must be unique")
        if self.context_snapshot:
            if self.context_snapshot.selected_persona_ids != ids:
                raise ValueError("context selected_persona_ids must match participants")
            if self.context_snapshot.initial_mode != self.normalized_mode:
                raise ValueError("context initial_mode must match the human request")
        if self.is_v19 and self.normalized_mode == "compare" and len(ids) != 2:
            raise ValueError("compare mode requires exactly two selected Personas")
        return self

    @property
    def is_v19(self) -> bool:
        return self.human_request is not None

    @property
    def normalized_mode(self) -> str:
        value = self.human_request.mode if self.human_request else str(self.mode)
        return "compare" if value == "consult" else value

    @property
    def eligibility_mode(self) -> str:
        return "consult" if self.normalized_mode == "compare" else self.normalized_mode

    @property
    def request_text(self) -> str:
        return self.human_request.request_text if self.human_request else str(self.topic)

    @property
    def selected_persona_ids(self) -> List[str]:
        if self.participants is not None:
            return [item.persona_id for item in self.participants]
        return list(self.participant_persona_ids or [])

    @property
    def normalized_context_refs(self) -> List[ContextRef]:
        if self.context_snapshot is not None:
            return [ContextRef(type=item.kind, id=item.id, version_id=item.version) for item in self.context_snapshot.context_refs]
        return list(self.context_refs or [])


class RetryInteractionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    reason: str = Field(min_length=1, max_length=500)


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
    return is_persona_operational(persona)


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
                              interaction_store: Optional[Any] = None,
                              canonical_context_ref_resolver: Optional[Callable[..., Any]] = None) -> APIRouter:
    router = APIRouter(tags=["agora-interaction"])
    # The production proposal store also owns command idempotency/outbox state,
    # so restarts and independent BFF workers share one durable truth.
    store = interaction_store if isinstance(interaction_store, ProposalStore) else proposal_store
    proposals = proposal_store
    lifecycle = (
        interaction_store
        if isinstance(interaction_store, InteractionLifecycleStore)
        else InteractionLifecycleStore.from_governance_store(proposal_store)
    )

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
                    receipt = lifecycle.latest_context_binding(
                        resolved.tenant_id, resolved.user_id, body.workshop_id,
                    )
                    participant_snapshot, _ = build_participant_admission(
                        persona=persona,
                        capability_snapshot=snapshot or {},
                        environment=body.environment,
                        tenant_id=resolved.tenant_id,
                        captured_at=str((receipt or {}).get("resolved_at") or utc_now()),
                    )
                except ValueError:
                    reasons.append("persona_opinion_admission_invalid")
            results.append({"persona_id": pid, "display_name": persona.get("display_name") or persona.get("name") or pid,
                            "eligible": not reasons, "reasons": reasons,
                            "recommended": not reasons and body.mode in {"challenge", "consult"},
                            "capability_snapshot_id": (snapshot or {}).get("snapshot_id") or (snapshot or {}).get("id"),
                            "participant_snapshot": participant_snapshot})
        return {"included": [x for x in results if x["eligible"]], "excluded": [x for x in results if not x["eligible"]]}

    def validate_v19_context(body: SubmitInteractionRequest, session: Dict[str, Any], resolved: Any) -> Optional[Dict[str, Any]]:
        snapshot = body.context_snapshot
        human = body.human_request
        if snapshot is None or human is None:
            return None
        submitted = snapshot.model_dump(mode="json")
        binding = lifecycle.matching_context_binding(
            resolved.tenant_id, resolved.user_id, body.workshop_id, submitted,
        )
        if binding is None:
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "A durable daily context resolver receipt is required", "context_binding_missing")
        if snapshot.tenant_id != resolved.tenant_id:
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Interaction context tenant does not match authenticated tenant", "tenant_mismatch")
        submitted_at = human.submitted_at
        server_now = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
        if submitted_at > server_now + timedelta(seconds=30):
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Human request time cannot be in the future", "human_submitted_at_future")
        if snapshot.captured_at != submitted_at:
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Context capture must bind the immutable human request time", "context_capture_mismatch")
        if snapshot.evidence_cutoff > snapshot.captured_at:
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Evidence cutoff cannot be later than context capture", "evidence_cutoff_future")
        for route_value in (snapshot.source_route, snapshot.return_route):
            if not route_value.startswith("/") or "://" in route_value or "\\" in route_value:
                from models import ErrorCode
                raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Source and return routes must be local Pantheon routes", "invalid_source_route")
        if snapshot.source_route != snapshot.return_route:
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Source and return route binding differs", "source_return_route_mismatch")
        refs = snapshot.context_refs
        focused = snapshot.focused_object
        if not any(
            item.kind == focused.kind and item.id == focused.id and item.version == focused.version
            for item in refs
        ):
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Focused object is not present in the immutable context refs", "focused_object_unbound")
        strategy_items = [item for item in refs if item.kind == "strategy"]
        expected_strategy = (
            {"strategy_id": strategy_items[0].id, "version_id": strategy_items[0].version}
            if strategy_items else None
        )
        if snapshot.strategy_ref != expected_strategy:
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Strategy reference does not match immutable context refs", "strategy_ref_mismatch")
        if strategy_items and (
            session.get("strategy_id") != strategy_items[0].id
            or session.get("active_strategy_spec_registry_id") != strategy_items[0].version
        ):
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Strategy context does not match canonical Workshop state", "workshop_strategy_mismatch")
        expected_decision = next((item.id for item in refs if item.kind == "decision_event"), None)
        expected_journal = next((item.id for item in refs if item.kind == "journal_entry"), None)
        if snapshot.decision_ref != expected_decision or snapshot.journal_ref != expected_journal:
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Journal or decision binding does not match immutable context refs", "source_reference_mismatch")
        expected_positions = [item.id for item in refs if item.kind == "position"]
        if snapshot.position_risk_snapshot_refs != expected_positions:
            from models import ErrorCode
            raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Position/risk snapshot refs do not match immutable context refs", "position_snapshot_mismatch")
        exact_keys = (
            "tenant_id", "source_route", "focused_object", "context_refs", "strategy_ref",
            "decision_ref", "journal_ref", "position_risk_snapshot_refs", "evidence_cutoff",
            "selected_persona_ids", "initial_mode", "return_route",
        )
        mismatched = [key for key in exact_keys if submitted.get(key) != binding.get(key)]
        if mismatched:
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "Submitted context does not match the authoritative resolver receipt",
                "context_binding_mismatch:" + ",".join(mismatched),
            )
        return binding

    def public_resource(resource: Dict[str, Any]) -> Dict[str, Any]:
        if resource.get("_legacy_shape"):
            return {key: value for key, value in resource.items() if not key.startswith("_")}
        allowed = {
            "interaction_id", "workshop_id", "status", "human_request", "context_snapshot",
            "participants", "provider_invocations", "opinions", "synthesis",
            "missing_participant_ids", "degraded_participant_ids", "candidate_proposal_links",
            "audit_refs", "created_at", "updated_at", "authority",
        }
        return {key: value for key, value in resource.items() if key in allowed}

    def daily_meta(resolved: Any, *, next_page_token: Optional[str] = None, replayed: bool = False, admitted_at: Optional[str] = None) -> Dict[str, Any]:
        meta = {
            "snapshot_at": utc_now(),
            "capability": "agora.persona.interaction.daily.v1",
            "audience": f"tenant:{resolved.tenant_id}:user:{resolved.user_id}",
            "authoritative_store": (
                "agora_interaction_postgres" if lifecycle.backend == "postgres"
                else "agora_interaction_memory_test"
            ),
            "next_page_token": next_page_token,
        }
        if admitted_at is not None:
            meta["admitted_at"] = admitted_at
            meta["replayed"] = replayed
        return meta

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
            resolved_at = utc_now()
            response = {"workshop_id": session["workshop_id"], "context_refs": [r.model_dump() for r in body.context_refs],
                        "context_digest": hashlib.sha256(canonical.encode()).hexdigest(), "environment": body.environment,
                        "verified": True, "resolved_at": resolved_at}
            if body.has_daily_binding:
                focused = dict(body.focused_object or {})
                if set(focused) - {"kind", "id", "version"} or not focused.get("kind") or not focused.get("id"):
                    from models import ErrorCode
                    raise bff_error(422, ErrorCode.VALIDATION_FAILED, "focused_object is invalid", "focused_object_invalid")
                focused = {"kind": focused["kind"], "id": focused["id"], "version": focused.get("version")}
                normalized_refs = [
                    {"kind": ref.type, "id": ref.id, "version": ref.version_id}
                    for ref in body.context_refs
                ]
                if not any(
                    item["kind"] == focused["kind"] and item["id"] == focused["id"]
                    and item.get("version") == focused.get("version")
                    for item in normalized_refs
                ):
                    from models import ErrorCode
                    raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Focused object is not in resolved context", "focused_object_unbound")
                if body.source_route != body.return_route or not str(body.source_route).startswith("/") or "://" in str(body.source_route):
                    from models import ErrorCode
                    raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Daily source/return routes are not a canonical local binding", "source_route_invalid")
                source_path = unquote(urlsplit(str(body.source_route)).path).rstrip("/")
                if focused["kind"] == "persona" and source_path != f"/management/personas/{focused['id']}":
                    from models import ErrorCode
                    raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Persona source route is not canonical", "focused_source_route_mismatch")
                if focused["kind"] == "workshop" and source_path != f"/agora/strategy-workshop/{focused['id']}":
                    from models import ErrorCode
                    raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Workshop source route is not canonical", "focused_source_route_mismatch")
                canonical_read = get_read_store()
                personas = {
                    _persona_id(item): item
                    for item in canonical_read.list_personas(include_market_persona_defaults=True)
                    if isinstance(item, dict)
                }
                for persona_id in body.selected_persona_ids or []:
                    persona = personas.get(persona_id)
                    if not persona or persona.get("tenant_id") != resolved.tenant_id:
                        from models import ErrorCode
                        raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Selected Persona is not canonical in this tenant", "selected_persona_not_found")
                from models import ErrorCode
                journal_reader = getattr(canonical_read, "list_decision_journal_entries", None)
                journal_rows = (
                    {
                        str(item.get("id") or item.get("entry_id") or ""): item
                        for item in journal_reader() if isinstance(item, dict)
                    }
                    if callable(journal_reader) else None
                )
                def legacy_canonical_ref(kind: str, ref_id: str) -> Optional[Dict[str, Any]]:
                    if kind == "decision_event":
                        from agora.trading_room.router import _get_store as get_trading_room_store
                        return get_trading_room_store().get_decision_event(ref_id)
                    if kind == "journal_entry":
                        if journal_rows is None:
                            raise bff_error(
                                503, ErrorCode.DEPENDENCY_UNAVAILABLE,
                                "Canonical journal readback is unavailable", "journal_store_unavailable",
                            )
                        return journal_rows.get(ref_id)
                    singular = getattr(canonical_read, f"get_{kind}", None)
                    if callable(singular):
                        row = singular(ref_id)
                        return row if isinstance(row, dict) else None
                    plural = getattr(canonical_read, f"list_{kind}s", None)
                    if not callable(plural):
                        raise bff_error(
                            503, ErrorCode.DEPENDENCY_UNAVAILABLE,
                            f"Canonical {kind} readback is unavailable",
                            f"{kind}_store_unavailable",
                        )
                    rows = plural()
                    return next(
                        (
                            row for row in rows if isinstance(row, dict)
                            and str(row.get("id") or row.get(f"{kind}_id") or "") == ref_id
                        ),
                        None,
                    )

                for ref in normalized_refs:
                    kind, ref_id, ref_version = ref["kind"], ref["id"], ref.get("version")
                    row: Optional[Dict[str, Any]]
                    canonical_version: Optional[str] = None
                    audience_verified = False
                    if kind == "persona":
                        row = personas.get(ref_id)
                        canonical_version = _persona_version(row) if row else None
                        audience_verified = bool(row and row.get("tenant_id") == resolved.tenant_id)
                    elif kind == "strategy":
                        canonical_version = str(session.get("active_strategy_spec_registry_id") or "") or None
                        strategy_reader = getattr(canonical_read, "get_strategy_spec_detail", None)
                        if not callable(strategy_reader):
                            raise bff_error(
                                503, ErrorCode.DEPENDENCY_UNAVAILABLE,
                                "Canonical strategy registry readback is unavailable",
                                "strategy_store_unavailable",
                            )
                        row = strategy_reader(ref_id, version_selector=ref_version)
                        registry_id = str((row or {}).get("strategy_id") or (row or {}).get("id") or "")
                        registry_version = str(
                            (row or {}).get("strategy_spec_registry_id")
                            or (row or {}).get("spec_version_id")
                            or (row or {}).get("version_id")
                            or (row or {}).get("version")
                            or ""
                        ) or None
                        if (
                            registry_id != ref_id
                            or session.get("strategy_id") != ref_id
                            or canonical_version != ref_version
                            or (registry_version is not None and registry_version != ref_version)
                        ):
                            row = None
                        audience_verified = row is not None
                    elif kind == "workshop":
                        row = workshop_store.get_session(ref_id)
                        canonical_version = str((row or {}).get("selected_version_id") or "") or None
                        audience_verified = bool(
                            row and row.get("tenant_id") == resolved.tenant_id
                            and row.get("user_id") == resolved.user_id
                        )
                    else:
                        resolved_ref = None
                        if callable(canonical_context_ref_resolver):
                            resolved_ref = canonical_context_ref_resolver(
                                kind=kind,
                                ref_id=ref_id,
                                ref_version=ref_version,
                                resolved=resolved,
                                session=session,
                                context_refs=normalized_refs,
                                authorization=authorization,
                                source_route=body.source_route,
                                focused_object=focused,
                            )
                        if isinstance(resolved_ref, dict) and "row" in resolved_ref:
                            row = resolved_ref.get("row")
                            audience_verified = resolved_ref.get("audience_verified") is True
                            canonical_version = str(resolved_ref.get("canonical_version") or "") or None
                        else:
                            row = legacy_canonical_ref(kind, ref_id)
                    if row is None:
                        raise bff_error(
                            409, ErrorCode.RESOURCE_CONFLICT,
                            f"{kind} reference is not canonical", f"{kind}_not_found",
                        )
                    row_tenant = str(row.get("tenant_id") or "")
                    row_user = str(row.get("owner_user_id") or row.get("user_id") or "")
                    if (row_tenant and row_tenant != resolved.tenant_id) or (
                        row_user and row_user != resolved.user_id
                    ):
                        raise bff_error(
                            403, ErrorCode.FORBIDDEN,
                            f"{kind} reference is outside the interaction audience",
                            f"{kind}_audience_mismatch",
                        )
                    if not audience_verified and not (
                        row_tenant == resolved.tenant_id and row_user == resolved.user_id
                    ):
                        raise bff_error(
                            503, ErrorCode.DEPENDENCY_UNAVAILABLE,
                            f"Canonical {kind} audience could not be verified",
                            f"{kind}_scope_unavailable",
                        )
                    if ref_version is not None:
                        canonical_version = canonical_version or str(
                            row.get("version_id") or row.get("version") or row.get("revision") or ""
                        ) or None
                        if canonical_version != str(ref_version):
                            raise bff_error(
                                409, ErrorCode.RESOURCE_CONFLICT,
                                f"{kind} reference version is not canonical",
                                f"{kind}_version_mismatch",
                            )
                authoritative_cutoff = resolved_at
                focused_kind = str(focused["kind"])
                advice_environment = (
                    "paper" if focused_kind in {"journal_entry", "decision_event", "position", "human_inbox_item"}
                    else "research"
                )
                strategy_item = next((item for item in normalized_refs if item["kind"] == "strategy"), None)
                binding_core = {
                    "workshop_id": session["workshop_id"],
                    "tenant_id": resolved.tenant_id,
                    "source_route": body.source_route,
                    "focused_object": focused,
                    "context_refs": normalized_refs,
                    "strategy_ref": ({"strategy_id": strategy_item["id"], "version_id": strategy_item["version"]} if strategy_item else None),
                    "decision_ref": next((item["id"] for item in normalized_refs if item["kind"] == "decision_event"), None),
                    "journal_ref": next((item["id"] for item in normalized_refs if item["kind"] == "journal_entry"), None),
                    "position_risk_snapshot_refs": [item["id"] for item in normalized_refs if item["kind"] == "position"],
                    "evidence_cutoff": authoritative_cutoff,
                    "selected_persona_ids": list(body.selected_persona_ids or []),
                    "initial_mode": body.initial_mode,
                    "return_route": body.return_route,
                    "advice_environment": advice_environment,
                    "resolved_at": resolved_at,
                }
                binding_digest = payload_fingerprint(binding_core)
                binding = {
                    "binding_id": f"ctxb-{binding_digest[:24]}",
                    **binding_core,
                    "context_digest": binding_digest,
                }
                # Keep the idempotency candidate pure.  ProposalStore.once()
                # builds a candidate before it knows whether this command is a
                # replay; persisting here would make a discarded candidate the
                # canonical eligibility receipt while returning the original
                # response to the caller.
                response["context_binding"] = binding
                response["environment"] = advice_environment
                response["context_digest"] = binding_digest
                response["context_refs"] = [
                    {"type": item["kind"], "id": item["id"], "version_id": item.get("version")}
                    for item in normalized_refs
                ]
            return response
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
        # Persist only the exact response selected by the idempotency store.
        # Re-saving the returned binding on replay is intentionally idempotent
        # and can never publish the discarded candidate built by once().
        context_binding = data.get("context_binding")
        if isinstance(context_binding, dict):
            data["context_binding"] = lifecycle.save_context_binding(
                context_binding, owner_user_id=resolved.user_id,
            )
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
        normalized_refs = body.normalized_context_refs
        normalized_mode = body.normalized_mode
        selected_persona_ids = body.selected_persona_ids
        context_binding = validate_v19_context(body, session, resolved)
        advice_environment = str(context_binding["advice_environment"]) if context_binding else body.environment
        strategy = next((r for r in normalized_refs if r.type == "strategy"), None)
        if normalized_mode == "propose_action" and strategy is None:
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
        eligibility_request = EligibilityRequest(
            workshop_id=body.workshop_id,
            mode=body.eligibility_mode,
            environment=advice_environment,
            required_capability=body.required_capability,
        )
        eligibility_result = eligibility(eligibility_request, resolved)
        eligible_rows = {x["persona_id"]: x for x in eligibility_result["included"]}
        if not set(selected_persona_ids).issubset(eligible_rows):
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "One or more participants are ineligible", "participant_eligibility_failed")
        command_scope = f"command:{resolved.tenant_id}:{resolved.user_id}"
        request_payload = body.model_dump(mode="json")
        command_fingerprint = payload_fingerprint(request_payload)
        trace_id = session.get("openclaw_session_id") or f"trace-{uuid.uuid4().hex[:12]}"

        if body.human_request and body.human_request.operator_id != resolved.operator_id:
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Human request operator does not match authenticated operator", "operator_mismatch")
        canonical_personas = {
            _persona_id(persona): persona
            for persona in get_read_store().list_personas(include_market_persona_defaults=True)
            if isinstance(persona, dict)
        }
        frozen_entries: List[Dict[str, Any]] = []
        for persona_id in selected_persona_ids:
            persona = canonical_personas.get(persona_id)
            if persona is None:
                from models import ErrorCode
                raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Selected Persona disappeared before freeze", "participant_registry_drift")
            metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
            snapshot_id = str(persona.get("capability_snapshot_id") or metadata.get("capability_snapshot_id") or "")
            if snapshot_id:
                getter = getattr(get_read_store(), "get_capability_snapshot", None)
                capability_snapshot = getter(snapshot_id) if callable(getter) else None
            else:
                capability_snapshot = get_read_store().get_capability_snapshot_for_persona(persona_id)
            captured_at = str((context_binding or {}).get("resolved_at") or utc_now())
            try:
                canonical_participant, admission = build_participant_admission(
                    persona=persona,
                    capability_snapshot=capability_snapshot or {},
                    environment=advice_environment,
                    tenant_id=resolved.tenant_id,
                    captured_at=captured_at,
                )
            except ValueError as exc:
                from models import ErrorCode
                raise bff_error(409, ErrorCode.RESOURCE_CONFLICT, "Persona admission drifted before freeze", str(exc)) from exc
            if body.participants:
                submitted = next(item for item in body.participants if item.persona_id == persona_id)
                submitted_json = submitted.model_dump(mode="json")
                mismatched = [key for key in canonical_participant if submitted_json.get(key) != canonical_participant.get(key)]
                if mismatched:
                    from models import ErrorCode
                    raise bff_error(
                        409, ErrorCode.RESOURCE_CONFLICT,
                        "Submitted Persona snapshot is no longer canonical",
                        "participant_snapshot_mismatch:" + ",".join(mismatched),
                    )
            frozen_entries.append({
                "persona_profile": json.loads(json.dumps(persona, default=str)),
                "participant": canonical_participant,
                "admission": admission,
            })

        def build() -> Dict[str, Any]:
            interaction_id = body.interaction_id or "int_" + hashlib.sha256(
                f"{command_scope}:{idempotency_key}".encode()
            ).hexdigest()[:24]
            data = {"interaction_id": interaction_id, "workshop_id": body.workshop_id,
                    "mode": normalized_mode, "topic": body.request_text, "participants": selected_persona_ids,
                    "context_refs": [r.model_dump() for r in normalized_refs], "status": "queued",
                    "execution_authority": "none", "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
                    "submitted_at": utc_now()}
            # Pre-v1.9 compatibility only.  Daily v1.9 candidates are created
            # later from an exact persisted provider recommended_measure;
            # human request text is never candidate truth.
            if body.is_v19 or normalized_mode != "propose_action" or strategy is None:
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
                    "candidate_measure": body.request_text,
                    "participant_persona_ids": selected_persona_ids,
                    "context_refs": [ref.model_dump() for ref in normalized_refs],
                },
                rationale=f"Persona interaction proposed a governed candidate measure: {body.request_text}",
                evidence_refs=[f"interaction:{interaction_id}"],
                confidence=0.9,
                expected_benefit="Evaluate the candidate measure through governed validation before any execution.",
                adverse_scenarios=[
                    "The candidate measure may underperform outside the reviewed context.",
                    "The supporting evidence may become stale before validation.",
                ],
                environment_ceiling=advice_environment,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                validation_plan={
                    "environment": advice_environment,
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
                    for ref in normalized_refs
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
        data = build()
        trace_id = session.get("openclaw_session_id") or f"trace-{data['interaction_id']}"
        submitted_at = (
            body.human_request.model_dump(mode="json")["submitted_at"]
            if body.human_request else data["submitted_at"]
        )
        participant_snapshots = [item["participant"] for item in frozen_entries]
        human_request = (
            body.human_request.model_dump(mode="json")
            if body.human_request else {
                "request_id": f"req:{data['interaction_id']}",
                "operator_id": resolved.operator_id,
                "mode": normalized_mode,
                "request_text": body.request_text,
                "submitted_at": submitted_at,
                "request_sha256": hashlib.sha256(body.request_text.encode("utf-8")).hexdigest(),
            }
        )
        if body.context_snapshot:
            context_snapshot = body.context_snapshot.model_dump(mode="json")
        else:
            first = normalized_refs[0]
            context_snapshot = {
                "tenant_id": resolved.tenant_id,
                "source_route": f"/bff/agora/workshops/{body.workshop_id}",
                "focused_object": {"kind": first.type, "id": first.id, "version": first.version_id},
                "context_refs": [{"kind": ref.type, "id": ref.id, "version": ref.version_id} for ref in normalized_refs],
                "strategy_ref": ({"strategy_id": strategy.id, "version_id": strategy.version_id} if strategy else None),
                "decision_ref": next((ref.id for ref in normalized_refs if ref.type == "decision_event"), None),
                "journal_ref": next((ref.id for ref in normalized_refs if ref.type == "journal_entry"), None),
                "position_risk_snapshot_refs": [ref.id for ref in normalized_refs if ref.type == "position"],
                "evidence_cutoff": submitted_at,
                "selected_persona_ids": selected_persona_ids,
                "initial_mode": normalized_mode,
                "return_route": f"/agora/strategy-workshop/{body.workshop_id}",
                "captured_at": submitted_at,
            }
        accepted_at = utc_now()
        record = {
            "interaction_id": data["interaction_id"],
            "workshop_id": body.workshop_id,
            "tenant_id": resolved.tenant_id,
            "owner_user_id": resolved.user_id,
            "status": "queued",
            "human_request": human_request,
            "context_snapshot": context_snapshot,
            "participants": participant_snapshots,
            "provider_invocations": [],
            "opinions": [],
            "synthesis": None,
            "missing_participant_ids": [],
            "degraded_participant_ids": [],
            "candidate_proposal_links": [],
            "audit_refs": [],
            "created_at": accepted_at,
            "updated_at": accepted_at,
            "trace_id": trace_id,
            "execution_authority": "none",
            "no_capital_authority_proof": "persona_interaction_event_no_capital_or_order_authority",
            "authority": authority_boundary(),
            "_legacy_shape": not body.is_v19,
            **({"_legacy_environment": body.environment} if not body.is_v19 else {}),
            **({"_context_binding": context_binding} if context_binding else {}),
            "_frozen_personas": frozen_entries,
            **({key: data[key] for key in ("proposal_id", "proposal_ref", "proposal_refs", "proposal", "proposal_etag") if key in data}),
        }
        if getattr(body, "demo_run_id", None):
            record["demo_run_id"] = body.demo_run_id
        try:
            resource, created = lifecycle.create_request(
                record,
                idempotency_scope=command_scope,
                idempotency_key=idempotency_key,
                fingerprint=command_fingerprint,
                trace_id=trace_id,
            )
        except InteractionConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {
            "data": public_resource(resource),
            "meta": daily_meta(resolved, replayed=not created, admitted_at=resource.get("created_at")),
        }

    @router.get("/bff/agora/interactions")
    def list_interactions(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        workshop_id: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=100),
        page_token: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        try:
            items, next_token = lifecycle.list_page(
                resolved.tenant_id, resolved.user_id, workshop_id=workshop_id,
                page_size=page_size, page_token=page_token,
            )
        except ValueError as exc:
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Invalid interaction page token", str(exc)) from exc
        return {"data": [public_resource(item) for item in items], "meta": daily_meta(resolved, next_page_token=next_token)}

    @router.get("/bff/agora/interactions/{interaction_id}")
    def get_interaction(
        interaction_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        resource = lifecycle.get(interaction_id, resolved.tenant_id, resolved.user_id)
        if resource is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Interaction not found", interaction_id)
        return {"data": public_resource(resource), "meta": daily_meta(resolved)}

    @router.get("/bff/agora/interactions/{interaction_id}/timeline")
    def interaction_timeline(
        interaction_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        items = lifecycle.timeline(interaction_id, resolved.tenant_id, resolved.user_id)
        if items is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Interaction not found", interaction_id)
        return {"data": items, "meta": daily_meta(resolved)}

    @router.post("/bff/agora/interactions/{interaction_id}:retry", status_code=202)
    def retry_interaction(
        interaction_id: str,
        body: RetryInteractionRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        if not idempotency_key:
            from models import ErrorCode
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "Idempotency-Key header is required", "missing_idempotency_key")
        try:
            retry_fingerprint = payload_fingerprint({
                "interaction_id": interaction_id,
                "actor_id": resolved.operator_id,
                "reason": body.reason,
            })
            resource, replayed = lifecycle.prepare_retry(
                interaction_id,
                resolved.tenant_id,
                resolved.user_id,
                idempotency_key=idempotency_key,
                fingerprint=retry_fingerprint,
                actor_id=resolved.operator_id,
                reason=body.reason,
            )
        except KeyError:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Interaction not found", interaction_id)
        except InteractionConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"data": public_resource(resource), "meta": daily_meta(resolved, replayed=replayed)}

    @router.post("/bff/agora/interactions:recover", status_code=202)
    def recover_interactions(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        try:
            drained = drain_interaction_outbox(lifecycle, workshop_store)
        except Exception:
            drained = 0
        recovered = lifecycle.recoverable(resolved.tenant_id, resolved.user_id)
        return {"data": [public_resource(item) for item in recovered],
                "meta": {**daily_meta(resolved), "outbox_drained": drained}}

    @router.get("/bff/agora/interactions/{interaction_id}/stream")
    async def stream_interaction(
        interaction_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        resolved = scope(authorization, x_tenant_id)
        timeline = lifecycle.timeline(interaction_id, resolved.tenant_id, resolved.user_id)
        if timeline is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Interaction not found", interaction_id)
        start = 0
        if last_event_id:
            match = next((index for index, item in enumerate(timeline) if item["outbox_id"] == last_event_id), None)
            if match is None:
                raise HTTPException(409, detail={"code": "SSE_REPLAY_UNAVAILABLE", "reason": "INTERACTION_OUTBOX_EVENT_NOT_FOUND"})
            start = match + 1

        def format_event(item: Dict[str, Any]) -> str:
            event_type = f"interaction.{item['projection_kind']}"
            data = {"interaction_id": interaction_id, **item}
            return f"id: {item['outbox_id']}\nevent: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"

        async def events() -> Any:
            seen = {item["outbox_id"] for item in timeline[:start]}
            for item in timeline[start:]:
                seen.add(item["outbox_id"])
                yield format_event(item)
            while True:
                await asyncio.sleep(1)
                current = lifecycle.timeline(interaction_id, resolved.tenant_id, resolved.user_id) or []
                emitted = False
                for item in current:
                    if item["outbox_id"] not in seen:
                        seen.add(item["outbox_id"])
                        emitted = True
                        yield format_event(item)
                if not emitted:
                    yield ": heartbeat\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
            "X-SSE-Channel": f"interaction:{interaction_id}",
            "X-SSE-Replay-Supported": "true",
            "X-SSE-Replay-Store": lifecycle.backend,
            "X-SSE-Resync-Routes": f"/bff/agora/interactions/{interaction_id}",
        })

    return router

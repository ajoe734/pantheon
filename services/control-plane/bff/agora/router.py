"""Agora BFF router factory.

create_agora_router() assembles all Agora sub-routers and is included by main.py
via app.include_router(create_agora_router(...)).

Route ownership per capability_manifest.json (frozen AG-XR-001):
  /bff/agora/me              → identity   (agora.identity.v1)
  /bff/agora/capabilities    → identity   (agora.identity.v1)
  Sub-router boundaries → see each sub-module's router.py
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response
from ..models import CommandResponse, DecisionJournalEntryDTO

from services.control_plane.bff.ports import (
    OpenClawOpsClient,
    OpenClawOpsClientError,
    ReadSurfacePorts,
    create_read_surface_ports,
)
from services.control_plane.bff.governance.decision_journal_write_owner import (
    wrap_get_read_store_with_decision_journal_owner,
)

from .models import (
    AgoraCapabilityScope,
    AgoraEnvelope,
    AgoraMeta,
)
from .identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from .identity.router import create_identity_router
from .servant.router import create_servant_router
from .strategy_workshop.router import create_strategy_workshop_router
from .strategy_workshop.operations import WorkshopCanonicalOperations
from .strategy_workshop.store import make_workshop_store
from .research.router import create_research_router
from .trading_room.router import create_trading_room_router
from .trading_room.store import make_trading_room_store
from .dashboard.router import create_dashboard_router
from .shadow.router import create_shadow_router
from .personalization.router import create_personalization_router
from .management_projection.router import create_management_projection_router
from .dataset_extraction.router import create_dataset_extraction_router
from .interaction.router import create_interaction_router
from .interaction.store import InteractionLifecycleStore
from .governance.router import create_governance_router
from .governance.store import ProposalStore
from .performance.router import create_performance_router
from .candidate_decisions.adapters import (
    CandidateBindingValidationAdapter,
    ReadStoreApprovalAdapter,
)
from .candidate_decisions.router import create_candidate_decision_router
from .candidate_decisions.service import CandidateDecisionService
from .candidate_decisions.store import CandidateDecisionStore
from .trading_data.router import create_trading_data_router
from .decision_projection.router import create_decision_projection_router
from .operational_readiness import (
    AgoraOperationalReadinessService,
    create_operational_readiness_router,
)
from .service import AgoraService


_CAPABILITY_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "specs", "agora", "capability_manifest.json",
)


def _load_capability_manifest() -> Dict[str, Any]:
    try:
        with open(_CAPABILITY_MANIFEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"capabilities": [], "manifest_version": "1.0", "source": "unavailable"}


def _raise_scope_error(exc: AgoraScopeResolutionError, bff_error: Callable[..., HTTPException]) -> None:
    from ..models import ErrorCode

    code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
    raise bff_error(
        exc.status_code,
        code,
        exc.message,
        exc.reason,
        precondition_failed="agora_user_scope",
        details_extra=exc.details,
    )


def create_agora_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    read_surface: Optional[Any] = None,
    command_store: Optional[Any] = None,
    persona_write_owner: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    sync_servant_agent: Callable[[Dict[str, Any]], Dict[str, Any]],
    get_audit_store: Optional[Callable[[], Any]] = None,
    canonical_context_ref_resolver: Optional[Callable[..., Any]] = None,
    get_trade_journey_store: Callable[[], Any] = lambda: None,
    get_persona_write_owner: Optional[Callable[[], Any]] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    idempotency_store: Optional[Dict[str, Any]] = None,
    sse_buffers: Optional[Dict[str, Any]] = None,
    sse_subscribers: Optional[Dict[str, Any]] = None,
    assistant_ask_enabled: Optional[Callable[[], bool]] = None,
    assistant_build_context_pack: Optional[Callable[..., Any]] = None,
    get_assistant_session_store: Optional[Callable[[], Any]] = None,
    get_assistant_transcript_store: Optional[Callable[[], Any]] = None,
    openclaw_ops_client_factory: Optional[Callable[[], Any]] = None,
    require_operator_role: Optional[Callable[..., None]] = None,
    require_journal_write_role: Optional[Callable[..., None]] = None,
    require_agora_signal_write_role: Optional[Callable[..., None]] = None,
    require_agora_bulk_feedback_role: Optional[Callable[..., None]] = None,
    handle_sse_stream: Optional[Callable[..., Any]] = None,
    publish_event_fn: Optional[Callable[..., Any]] = None,
    service: Optional[AgoraService] = None,
) -> APIRouter:
    """Return the Agora top-level APIRouter.

    Mount with:  app.include_router(create_agora_router(...))
    """
    if read_surface is not None:
        get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
    elif get_read_store is None:
        raise RuntimeError("Neither read_surface nor get_read_store was configured.")

    # JOURNAL-OWNER-001: every Agora sub-router below shares this same
    # get_read_store closure, so wrapping it once here is the single
    # composition point that binds the whole Agora journal surface (reads
    # and writes) to the canonical governance Decision Journal owner.
    get_read_store = wrap_get_read_store_with_decision_journal_owner(get_read_store)

    if command_store is not None:
        get_command_store = (lambda: command_store() if callable(command_store) else command_store)

    if persona_write_owner is not None:
        get_persona_write_owner = (lambda: persona_write_owner() if callable(persona_write_owner) else persona_write_owner)

    router = APIRouter(tags=["agora"])
    workshop_store = make_workshop_store()
    workshop_canonical_operations = WorkshopCanonicalOperations(
        approval_resolver=lambda decision_id: get_read_store().get_approval_decision(
            decision_id
        )
    )
    proposal_store = ProposalStore()
    interaction_lifecycle = InteractionLifecycleStore.from_governance_store(proposal_store)
    candidate_store = CandidateDecisionStore.from_governance_store(proposal_store)
    trading_room_store = make_trading_room_store()
    candidate_service = CandidateDecisionService(
        candidate_store,
        interaction_store=interaction_lifecycle,
        validation_adapter=CandidateBindingValidationAdapter(),
        approval_store=ReadStoreApprovalAdapter(get_read_store),
    )

    agora_service = service or AgoraService(
        get_read_store=get_read_store,
        get_audit_store=get_audit_store,
        get_command_store=get_command_store,
        idempotency_store=idempotency_store,
        sse_buffers=sse_buffers,
        sse_subscribers=sse_subscribers,
        assistant_ask_enabled=assistant_ask_enabled,
        assistant_build_context_pack=assistant_build_context_pack,
        get_assistant_session_store=get_assistant_session_store,
        get_assistant_transcript_store=get_assistant_transcript_store,
        openclaw_ops_client_factory=openclaw_ops_client_factory,
        utc_now=utc_now,
        bff_error=bff_error,
        handle_sse_stream=handle_sse_stream,
        publish_event_fn=publish_event_fn,
    )

    def _operational_surface_readback(
        scope: Optional[AgoraCapabilityScope],
    ) -> Dict[str, Any]:
        """Read actual Agora stores; keep every private read scope-bound."""

        result: Dict[str, Any] = {}

        def _read_list(name: str, reader: Callable[[], Any]) -> List[Dict[str, Any]]:
            try:
                value = reader()
                if isinstance(value, dict):
                    value = value.get("items") or value.get("data") or []
                return [dict(item) for item in (value or []) if isinstance(item, dict)]
            except Exception:
                result[name] = {
                    "status": "unavailable",
                    "count": 0,
                    "reason": f"{name}_provider_unavailable",
                }
                return []

        read_store = get_read_store()
        if read_store is None:
            raise RuntimeError("read store is not configured")
        result["signals"] = _read_list(
            "signals",
            lambda: read_store.list_agora_signals(),
        )
        result["inbox"] = _read_list(
            "inbox",
            lambda: read_store.list_evidence_refs(),
        )
        result["journal"] = _read_list(
            "journal",
            lambda: read_store.list_decision_journal_entries(),
        )
        result["decision_events"] = _read_list(
            "decision_events",
            lambda: trading_room_store.list_decision_events(page_size=10_000),
        )

        if scope is None:
            interactions: List[Dict[str, Any]] = []
            result["performance"] = {
                "status": "ok",
                "count": 0,
                "reason": "public_scope_excludes_private_performance",
            }
        else:
            interactions = _read_list(
                "interactions",
                lambda: interaction_lifecycle.list(scope.tenant_id, scope.user_id),
            )
            try:
                journey_store = get_trade_journey_store()
                if journey_store is None:
                    raise RuntimeError("trade journey projection is not configured")
                page = journey_store.page_journeys(
                    tenant_id=scope.tenant_id,
                    environment="paper",
                    page_size=1,
                )
                result["performance"] = {
                    "status": "ok",
                    "count": int(page.total),
                    "cursor": page.next_page_token,
                }
            except Exception:
                result["performance"] = {
                    "status": "unavailable",
                    "count": 0,
                    "reason": "performance_provider_unavailable",
                }
        result.setdefault("interactions", interactions)
        result["candidates"] = [
            dict(candidate)
            for interaction in interactions
            for candidate in list(interaction.get("candidate_proposals") or [])
            if isinstance(candidate, dict)
        ]
        return result

    # ------------------------------------------------------------------ #
    # GET /bff/agora/me  — operator identity and capability scope (§18 envelope)
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/me")
    def agora_me(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the operator's Agora identity scope and audience-filtered capabilities."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)
        envelope = AgoraEnvelope(
            data=scope.model_dump(),
            meta=AgoraMeta(
                snapshot_at=utc_now(),
                capability="agora.identity.v1",
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        return envelope.model_dump()

    # ------------------------------------------------------------------ #
    # GET /bff/agora/capabilities  — frozen capability manifest
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/capabilities")
    def agora_capabilities(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the frozen Agora v1 capability manifest filtered by audience."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        manifest = _load_capability_manifest()
        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)
        caps = scope.granted_capabilities
        allowed = {c["name"] for c in manifest.get("capabilities", [])} & set(caps)
        manifest_filtered = {
            **manifest,
            "capabilities": [
                c for c in manifest.get("capabilities", []) if c["name"] in allowed
            ],
            "scope": {
                "scope_id": scope.scope_id,
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "read_predicate": scope.read_predicate.model_dump(),
            },
        }
        return {
            "data": manifest_filtered,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.identity.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # Include sub-routers
    # ------------------------------------------------------------------ #
    _kw: Dict[str, Any] = dict(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now=utc_now,
    )
    router.include_router(create_identity_router(service=agora_service, require_write_role=require_write_role, **_kw))
    router.include_router(create_servant_router(
        **_kw,
        require_write_role=require_write_role,
        get_read_store=get_read_store,
        sync_servant_agent=sync_servant_agent,
        get_persona_write_owner=get_persona_write_owner,
    ))
    router.include_router(create_strategy_workshop_router(
        **_kw,
        require_write_role=require_write_role,
        workshop_store=workshop_store,
        canonical_operations=workshop_canonical_operations,
    ))
    router.include_router(create_research_router(**_kw, require_write_role=require_write_role))
    router.include_router(create_trading_room_router(
        **_kw,
        require_write_role=require_write_role,
        trading_room_store=trading_room_store,
        workshop_store=workshop_store,
    ))
    router.include_router(create_performance_router(
        **_kw,
        require_write_role=require_write_role,
        get_trade_journey_store=get_trade_journey_store,
        workshop_store=workshop_store,
    ))
    router.include_router(create_dashboard_router(**_kw))
    router.include_router(create_shadow_router(**_kw))
    router.include_router(create_personalization_router(service=agora_service, require_write_role=require_write_role, **_kw))
    router.include_router(create_management_projection_router(**_kw))
    router.include_router(
        create_dataset_extraction_router(
            **_kw,
            require_write_role=require_write_role,
        )
    )
    router.include_router(create_interaction_router(
        **_kw,
        require_write_role=require_write_role,
        get_read_store=get_read_store,
        workshop_store=workshop_store,
        proposal_store=proposal_store,
        interaction_store=interaction_lifecycle,
        canonical_context_ref_resolver=canonical_context_ref_resolver,
    ))
    router.include_router(create_candidate_decision_router(
        **_kw,
        require_write_role=require_write_role,
        service=candidate_service,
    ))
    router.include_router(create_governance_router(
        **_kw,
        require_write_role=require_write_role,
        get_approval_decision=lambda approval_id: get_read_store().get_approval_decision(approval_id),
        list_approval_decisions=lambda: get_read_store().list_approval_decisions(),
        store=proposal_store,
    ))
    router.include_router(create_trading_data_router(**_kw))
    router.include_router(create_decision_projection_router(**_kw, require_write_role=require_write_role))
    router.include_router(create_operational_readiness_router(
        **_kw,
        get_read_store=get_read_store,
        surfaces_reader=_operational_surface_readback,
    ))
    # ------------------------------------------------------------------ #
    # Migrated Agora Route Handlers (40 decorators, 38 endpoints/aliases)
    # ------------------------------------------------------------------ #

    @router.patch("/bff/agora/journal/{entry_id}", response_model=CommandResponse[DecisionJournalEntryDTO])
    def agora_patch_journal_entry(
        entry_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        content_type: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> CommandResponse[DecisionJournalEntryDTO]:
        identity = extract_identity(authorization, mfa_token=x_mfa_token)
        (require_journal_write_role or require_write_role)(identity)
        agora_service.reject_body_idempotency_key(payload)
        agora_service.require_merge_patch_content_type(content_type)
        resolved_key = agora_service.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        patch = agora_service.validate_journal_merge_patch_payload(payload, identity)
        return agora_service.patch_journal_entry(
            entry_id=entry_id,
            patch=patch,
            identity=identity,
            resolved_key=resolved_key,
            correlation_id=x_correlation_id or x_trace_id,
            x_request_id=x_request_id,
        )

    @router.get("/bff/agora/daily")
    def agora_daily_brief(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.get_daily_brief()

    @router.get("/bff/agora/signals")
    def agora_list_signals(
        review_status: Optional[str] = Query(default=None, alias="reviewStatus"),
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_signals(
            review_status=review_status or status,
            page_token=page_token,
            page_size=page_size,
        )

    @router.post("/bff/agora/signals", status_code=201)
    def agora_create_signal(
        response: Response,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ) -> Any:
        identity = extract_identity(authorization)
        (require_agora_signal_write_role or require_write_role)(identity)
        if x_correlation_id and response is not None:
            response.headers["X-Correlation-Id"] = x_correlation_id
        if x_request_id and response is not None:
            response.headers["X-Request-Id"] = x_request_id
        return agora_service.create_signal(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_correlation_id=x_correlation_id,
            x_request_id=x_request_id,
            x_dry_run=x_dry_run,
            response=response,
        )

    @router.get("/bff/agora/signals/{signalId}")
    def agora_get_signal(
        signalId: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.get_signal(signalId)

    @router.post("/bff/agora/feedback", status_code=201)
    def agora_create_bulk_feedback(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    ) -> Any:
        identity = extract_identity(authorization)
        (require_agora_bulk_feedback_role or require_write_role)(identity)
        return agora_service.create_bulk_feedback(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_dry_run=x_dry_run,
            x_correlation_id=x_correlation_id,
        )

    @router.post("/bff/agora/signals/{signalId}/feedback")
    def agora_record_signal_feedback(
        signalId: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.record_signal_feedback(
            signal_id=signalId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_dry_run=x_dry_run,
        )

    @router.get("/bff/agora/markets")
    @router.get("/bff/agora/watchlist")
    def agora_list_watchlist(
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_watchlist(page_token=page_token, page_size=page_size)

    @router.post("/bff/agora/committee/{sessionId}/evidence-pack", status_code=201)
    def agora_committee_evidence_pack(
        sessionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.create_committee_evidence_pack(
            session_id=sessionId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.post("/bff/agora/committee/{sessionId}/evidence-pack/files", status_code=201)
    def agora_committee_evidence_files(
        sessionId: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.upload_committee_evidence_files(
            session_id=sessionId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.get("/bff/agora/market-notes")
    @router.get("/bff/agora/notes")
    def agora_list_notes(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_notes(page_token=page_token, page_size=page_size)

    @router.post("/bff/agora/notes", status_code=201)
    def agora_create_note(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.create_note(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_dry_run=x_dry_run,
        )

    @router.get("/bff/agora/decision-journal")
    @router.get("/bff/agora/journal")
    def agora_list_journal_entries(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_journal_entries(
            identity=identity,
            page_token=page_token,
            page_size=page_size,
        )

    @router.post("/bff/agora/journal", status_code=201)
    def agora_create_journal_entry(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ) -> Any:
        identity = extract_identity(authorization)
        (require_journal_write_role or require_write_role)(identity)
        return agora_service.create_journal_entry(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_dry_run=x_dry_run,
        )

    @router.get("/bff/agora/training-examples")
    def agora_list_training_examples(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_training_examples(page_token=page_token, page_size=page_size)

    @router.post("/bff/agora/training-examples", status_code=201)
    def agora_create_training_example(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.create_training_example(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_dry_run=x_dry_run,
        )

    @router.get("/bff/agora/research-tasks")
    @router.get("/bff/research/tasks")
    def agora_list_research_tasks(
        status: Optional[str] = Query(default=None),
        owner: Optional[str] = Query(default=None),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_research_tasks(
            status=status,
            owner=owner,
            page_token=page_token,
            page_size=page_size,
        )

    @router.post("/bff/agora/persona-lab/{draftId}/actions/submit-commit", status_code=202)
    def agora_submit_persona_lab_commit(
        draftId: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        (require_operator_role or require_write_role)(identity)
        return agora_service.submit_persona_lab_commit(
            draft_id=draftId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.get("/api/v1/agora/ask/stream")
    def agora_ask_stream(
        authorization: Optional[str] = Header(default=None),
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.stream_channel_events("ask", last_event_id=last_event_id or last_event_id_header)

    @router.get("/bff/sse/agora/signals")
    def agora_signals_stream(
        authorization: Optional[str] = Header(default=None),
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.stream_channel_events("signal", last_event_id=last_event_id or last_event_id_header)

    @router.get("/bff/sse/agora/sessions/{sessionId}")
    def agora_session_stream(
        sessionId: str,
        authorization: Optional[str] = Header(default=None),
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.stream_channel_events(
            f"session:{sessionId}", last_event_id=last_event_id or last_event_id_header
        )

    @router.get("/bff/agora/committee/sessions")
    def agora_list_committee_sessions(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_committee_sessions()

    @router.post("/bff/agora/committee/sessions", status_code=201)
    def agora_create_committee_session(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.create_committee_session(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.get("/bff/agora/committee/sessions/{sessionId}")
    def agora_get_committee_session(
        sessionId: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.get_committee_session(sessionId)

    @router.post("/bff/agora/committee/sessions/{sessionId}/open")
    def agora_open_committee_session(
        sessionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.open_committee_session(
            sessionId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.post("/bff/agora/committee/sessions/{sessionId}/close")
    def agora_close_committee_session(
        sessionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.close_committee_session(
            sessionId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.get("/bff/agora/committee/sessions/{sessionId}/memos")
    def agora_list_committee_session_memos(
        sessionId: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_committee_session_memos(sessionId)

    @router.post("/bff/agora/committee/sessions/{sessionId}/memos", status_code=201)
    def agora_submit_committee_session_memo(
        sessionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.submit_committee_session_memo(
            sessionId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.get("/bff/agora/committee/sessions/{sessionId}/memos/{memoId}")
    def agora_get_committee_session_memo(
        sessionId: str,
        memoId: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.get_committee_session_memo(sessionId, memoId)

    @router.post("/bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish")
    def agora_publish_committee_session_memo(
        sessionId: str,
        memoId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = extract_identity(authorization)
        require_write_role(identity)
        return agora_service.publish_committee_session_memo(
            sessionId,
            memoId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.get("/bff/agora/skill-coaching/sessions")
    def agora_list_skill_coaching_sessions(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_skill_coaching_sessions()

    @router.get("/bff/agora/persona-lab/runs")
    def agora_list_persona_lab_runs(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_persona_lab_runs()

    @router.get("/bff/agora/postmortems")
    def agora_list_postmortems(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_postmortems()

    @router.get("/bff/agora/evaluation-suites")
    def agora_list_evaluation_suites(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_evaluation_suites()

    @router.get("/bff/agora/evaluation-runs")
    def agora_list_evaluation_runs(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_evaluation_runs()

    @router.get("/bff/agora/alerts/triage")
    def agora_list_alerts_triage(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return agora_service.list_alerts_triage()

    router.interaction_lifecycle = interaction_lifecycle
    router.workshop_store = workshop_store
    router.proposal_store = proposal_store
    router.trading_room_store = trading_room_store
    router.agora_service = agora_service

    return router

"""Agora BFF router factory.

create_agora_router() assembles all Agora sub-routers and is included by main.py
via app.include_router(create_agora_router(...)).  Some handlers are complete;
remaining downstream AG-BE-ID-* / AG-BE-SW-* surfaces stay in their sub-router
stubs until implemented.

Route ownership per capability_manifest.json (frozen AG-XR-001):
  /bff/agora/me              → identity   (agora.identity.v1)
  /bff/agora/capabilities    → identity   (agora.identity.v1)
  Sub-router boundaries → see each sub-module's router.py
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

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
    from models import ErrorCode  # BFF top-level models; available via sys.path in runtime

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
    get_read_store: Callable[[], Any],
    sync_servant_agent: Callable[[Dict[str, Any]], Dict[str, Any]],
    canonical_context_ref_resolver: Optional[Callable[..., Any]] = None,
    get_trade_journey_store: Callable[[], Any] = lambda: None,
    get_persona_write_owner: Optional[Callable[[], Any]] = None,
    main_module: Any = None,
) -> APIRouter:
    """Return the Agora top-level APIRouter.

    Mount with:  app.include_router(create_agora_router(...))
    """
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
    candidate_service = CandidateDecisionService(
        candidate_store,
        interaction_store=interaction_lifecycle,
        validation_adapter=CandidateBindingValidationAdapter(),
        approval_store=ReadStoreApprovalAdapter(get_read_store),
    )

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
    router.include_router(create_identity_router(**_kw, main_module=main_module))
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
    router.include_router(create_personalization_router(**_kw, main_module=main_module))
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
    router.include_router(create_operational_readiness_router(**_kw, get_read_store=get_read_store))

    router.interaction_lifecycle = interaction_lifecycle
    router.workshop_store = workshop_store
    router.proposal_store = proposal_store

    return router

"""Persona provisioning reconciliation routes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.control_plane.bff.models import ErrorCode
from ..service import (
    PersonaProvisioningCoordinationError,
    ensure_chain_paper_capital,
    retire_chain_paper_capital,
    _bff_me_tenant_payload,
    _evaluate_persona_provisioning_status,
    _get_persona_directory_snapshot,
    _persona_provisioning_authoritative_meta,
    _persona_record_tenant_id,
    _project_persona_dto,
    _routed_strategies_for_persona,
)
from .common import PersonaRouteContext, make_context_dependency

log = logging.getLogger(__name__)

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class ChainPaperCapitalRequest(BaseModel):
    """Exact existing chain ids; the caller never supplies actors, tenant or scope."""

    model_config = ConfigDict(extra="forbid")

    capital_pool_id: str = Field(pattern=_ID_PATTERN)
    binding_id: str = Field(pattern=_ID_PATTERN)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=8)
    name: Optional[str] = Field(default=None, max_length=200)
    budget: Optional[Any] = None
    mandate: Optional[Any] = None
    risk_policy_ref: Optional[Any] = None
    strategy_spec_registry_id: Optional[str] = Field(default=None, pattern=_ID_PATTERN)
    strategy_artifact_id: Optional[str] = Field(default=None, pattern=_ID_PATTERN)
    strategy_artifact_checksum: Optional[str] = Field(default=None, max_length=256)
    labels: Optional[Dict[str, Any]] = None


class ChainPaperCapitalRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capital_pool_id: str = Field(pattern=_ID_PATTERN)
    binding_id: str = Field(pattern=_ID_PATTERN)
    chain_id: Optional[str] = Field(default=None, pattern=_ID_PATTERN)


def build_provisioning_router(ctx: PersonaRouteContext) -> APIRouter:
    router = APIRouter(tags=["personas"], dependencies=[make_context_dependency(ctx)])

    _service = ctx.service
    _extract_identity = ctx.extract_identity
    _require_read_role = ctx.require_read_role
    _require_operator_role = ctx.require_operator_role
    _bff_error = ctx.bff_error
    utc_now = ctx.utc_now
    _page_slice = ctx.page_slice
    _snapshot_meta = ctx.snapshot_meta
    _dataset_surface_status = ctx.dataset_surface_status
    _read_surface_meta = ctx.read_surface_meta
    _raise_if_read_surface_unavailable = ctx.raise_if_read_surface_unavailable
    _reject_body_idempotency_key = ctx.reject_body_idempotency_key
    _resolve_final_idempotency_key = ctx.resolve_final_idempotency_key

    @router.post("/bff/personas/{persona_id}/provisioning/reconcile")
    async def bff_reconcile_persona_provisioning(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """Operator-triggered controller pass; Persona GET/list remain pure reads."""

        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        caller_tenant = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        directory = _get_persona_directory_snapshot(caller_tenant)
        raw = directory.records_by_id.get(persona_id) or _service.get_persona(persona_id)
        if (
            raw is None
            or _persona_record_tenant_id(raw) != caller_tenant
        ):
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )
        diagnostics: List[str] = []
        state = await asyncio.to_thread(
            _evaluate_persona_provisioning_status,
            persona_id,
            raw,
            diagnostics=diagnostics,
        )
        if state in ("paper_running", "provisioning_failed"):
            metadata = raw.get("metadata") or {}
            persisted = _service.persist_terminal_transition(
                persona_id,
                lifecycle_state=state,
                metadata=metadata,
            )
            if persisted:
                raw.setdefault("metadata", {})["provisioning_reconciliation_state"] = state
        dto = _project_persona_dto(
            raw,
            overlay=None,
            routed_strategies=_routed_strategies_for_persona(persona_id),
            evaluate_provisioning=False,
        )
        authoritative_meta = _persona_provisioning_authoritative_meta(raw)
        return {
            "data": dto,
            "meta": {
                "snapshot_at": utc_now(),
                "reconciled_by": "persona_provisioning_controller",
                "lifecycle_state": state,
                "status": "degraded" if diagnostics else "ok",
                "degraded_dependencies": sorted(set(diagnostics)),
                "authoritative_readback": authoritative_meta,
            },
        }

    def _chain_capital_persona(identity: Any, persona_id: str) -> str:
        """Caller tenant for a Persona the caller's tenant owns; 404 otherwise."""

        caller_tenant = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        directory = _get_persona_directory_snapshot(caller_tenant)
        raw = directory.records_by_id.get(persona_id) or _service.get_persona(persona_id)
        if raw is None or _persona_record_tenant_id(raw) != caller_tenant:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )
        return caller_tenant

    def _chain_capital_failure(exc: Exception):
        return _bff_error(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Paper capital precondition failed",
            str(exc) or exc.__class__.__name__,
        )

    @router.post("/bff/personas/{persona_id}/provisioning/paper-capital")
    async def bff_ensure_persona_chain_paper_capital(
        persona_id: str,
        body: ChainPaperCapitalRequest,
        authorization: Optional[str] = Header(default=None),
    ):
        """Capital-only paper ensure: no approval, review or decision is ever made."""

        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        tenant_id = _chain_capital_persona(identity, persona_id)
        try:
            data = await asyncio.to_thread(
                ensure_chain_paper_capital,
                tenant_id,
                persona_id,
                requested_by=str(identity.operator_id),
                **body.model_dump(),
            )
        except PersonaProvisioningCoordinationError as exc:
            raise _chain_capital_failure(exc) from exc
        return {"data": data, "meta": {"snapshot_at": utc_now()}}

    @router.post("/bff/personas/{persona_id}/provisioning/paper-capital/retire")
    async def bff_retire_persona_chain_paper_capital(
        persona_id: str,
        body: ChainPaperCapitalRetireRequest,
        authorization: Optional[str] = Header(default=None),
    ):
        """Binding-before-pool retirement of only the named chain paper records."""

        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        tenant_id = _chain_capital_persona(identity, persona_id)
        try:
            data = await asyncio.to_thread(
                retire_chain_paper_capital,
                tenant_id,
                persona_id,
                **body.model_dump(),
            )
        except PersonaProvisioningCoordinationError as exc:
            raise _chain_capital_failure(exc) from exc
        return {"data": data, "meta": {"snapshot_at": utc_now()}}

    return router

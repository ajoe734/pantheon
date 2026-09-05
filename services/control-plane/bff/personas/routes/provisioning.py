"""Persona provisioning reconciliation routes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException

from services.control_plane.bff.models import ErrorCode
from ..service import (
    _PERSONA_BFF_OVERLAY,
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


def build_provisioning_router(ctx: PersonaRouteContext) -> APIRouter:
    router = APIRouter(tags=["personas"], dependencies=[make_context_dependency(ctx)])

    read_store = ctx.read_store
    command_store = ctx.command_store
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
        raw = directory.records_by_id.get(persona_id) or read_store.get_persona(persona_id)
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
        dto = _project_persona_dto(
            raw,
            overlay=_PERSONA_BFF_OVERLAY.get(persona_id),
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

    return router

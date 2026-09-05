"""Persona Management canonical domain router.

Part of OPGAP-BE-PERSONA-ROUTER-V2-20260830 / BFF-ROUTER-STRUCT-001.
Zero reverse imports of main.py.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException

from . import service as _service_mod
from .service import PersonaService
from .routes.common import PersonaRouteContext, make_context_dependency
from .routes.collection import build_collection_router
from .routes.detail import build_detail_router
from .routes.lifecycle import build_lifecycle_router
from .routes.provisioning import build_provisioning_router
from .routes.ranking import build_ranking_router

log = logging.getLogger(__name__)


def create_personas_router(
    *,
    service: Optional[PersonaService] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    get_provisioning_store: Optional[Callable[[], Any]] = None,
    extract_identity_fn: Optional[Callable[..., Any]] = None,
    require_read_role_fn: Optional[Callable[..., None]] = None,
    require_operator_role_fn: Optional[Callable[..., None]] = None,
    bff_error_fn: Optional[Callable[..., HTTPException]] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
    page_slice_fn: Optional[Callable[..., Any]] = None,
    snapshot_meta_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    dataset_surface_status_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    raise_if_read_surface_unavailable_fn: Optional[Callable[..., None]] = None,
    reject_body_idempotency_key_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
    resolve_final_idempotency_key_fn: Optional[Callable[[Optional[str], Optional[str]], str]] = None,
    submit_persona_action_fn: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Build the canonical Persona Management domain router.

    Registers 49 route decorators across 45 unique handlers partitioned into
    5 cohesive subrouters (collection, detail, lifecycle, provisioning, ranking).
    """
    if service is None:
        raise RuntimeError("PersonaService must be explicitly provided; router cannot self-create defaults.")
    _service = service

    read_store = get_read_store() if get_read_store else _service.get_read_store()
    command_store = get_command_store() if get_command_store else _service.get_command_store()
    ranking_write_owner = _service.get_ranking_write_owner()
    write_owner = _service.get_write_owner()

    _extract_identity = extract_identity_fn or getattr(_service_mod, "_extract_identity")
    _require_read_role = require_read_role_fn or getattr(_service_mod, "_require_read_role")
    _require_operator_role = require_operator_role_fn or getattr(_service_mod, "_require_operator_role")
    _bff_error = bff_error_fn or getattr(_service_mod, "_bff_error")
    utc_now = utc_now_fn or getattr(_service_mod, "utc_now")
    _page_slice = page_slice_fn or getattr(_service_mod, "_page_slice")
    _snapshot_meta = snapshot_meta_fn or getattr(_service_mod, "_snapshot_meta")
    _dataset_surface_status = dataset_surface_status_fn or _service.dataset_surface_status
    _read_surface_meta = _service.read_surface_meta
    _raise_if_read_surface_unavailable = raise_if_read_surface_unavailable_fn or getattr(_service_mod, "_raise_if_read_surface_unavailable")
    _reject_body_idempotency_key = reject_body_idempotency_key_fn or getattr(_service_mod, "_reject_body_idempotency_key")
    _resolve_final_idempotency_key = resolve_final_idempotency_key_fn or getattr(_service_mod, "_resolve_final_idempotency_key")
    _submit_persona_action = submit_persona_action_fn or getattr(_service_mod, "_submit_persona_action", None)

    ctx = PersonaRouteContext(
        service=_service,
        read_store=read_store,
        command_store=command_store,
        ranking_write_owner=ranking_write_owner,
        write_owner=write_owner,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        read_surface_meta=_read_surface_meta,
        raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
        reject_body_idempotency_key=_reject_body_idempotency_key,
        resolve_final_idempotency_key=_resolve_final_idempotency_key,
        submit_persona_action=_submit_persona_action,
    )

    router = APIRouter(tags=["personas"], dependencies=[make_context_dependency(ctx)])

    subrouters = [
        build_collection_router(ctx),
        build_detail_router(ctx),
        build_lifecycle_router(ctx),
        build_provisioning_router(ctx),
        build_ranking_router(ctx),
    ]

    for sub in subrouters:
        router.routes.extend(sub.routes)

    return router

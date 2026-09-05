"""Common definitions and route context for Persona domain subrouters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from fastapi import Depends, HTTPException


@dataclass(frozen=True)
class PersonaRouteContext:
    service: Any
    read_store: Any
    command_store: Any
    ranking_write_owner: Any
    write_owner: Any
    extract_identity: Callable[..., Any]
    require_read_role: Callable[..., None]
    require_operator_role: Callable[..., None]
    bff_error: Callable[..., HTTPException]
    utc_now: Callable[[], str]
    page_slice: Callable[..., Any]
    snapshot_meta: Callable[..., Dict[str, Any]]
    dataset_surface_status: Callable[..., Dict[str, Any]]
    read_surface_meta: Callable[..., Dict[str, Any]]
    raise_if_read_surface_unavailable: Callable[..., None]
    reject_body_idempotency_key: Callable[[Dict[str, Any]], None]
    resolve_final_idempotency_key: Callable[[Optional[str], Optional[str]], str]
    submit_persona_action: Optional[Callable[..., Any]] = None


def make_context_dependency(ctx: PersonaRouteContext):
    async def _bind_service_context():
        from ..service import _current_persona_service
        token = _current_persona_service.set(ctx.service)
        try:
            yield
        finally:
            _current_persona_service.reset(token)

    return Depends(_bind_service_context)

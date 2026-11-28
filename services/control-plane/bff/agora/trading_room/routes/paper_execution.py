"""Paper execution service boundary for trader-confirmed Agora intents.

AGORA-PAPER-INTENT-EXECUTION-20261127 (SD-AGORA-SERVANT-CORE-CAPABILITIES-20261127 §8.3, T8).

The reader of ``target_queue=paper_execution`` handoffs is the paper execution
lane (the consumer inside ``paper-signal-producer``). It drains the open
handoffs of its tenants here and writes each handoff's state back:
``accepted``, ``rejected`` with a typed reason, ``filled`` or
``partially_filled``.

Routes (service principal only, never a browser user):
  GET  /internal/agora/paper-execution/handoffs                     — tenant-scoped drain
  POST /internal/agora/paper-execution/handoffs/{handoff_id}/state  — state writeback

Agora keeps no order route: these routes only read and annotate handoff
records. The execution owner validates and executes in the trader's paper book.
Nothing on this path can reach canary or live (``environment`` is fixed to
``paper`` and ``no_order_route_proof`` is required on every writeback).
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .common import TradingRoomRouteContext

log = logging.getLogger(__name__)

PAPER_EXECUTION_SERVICE_ACTOR = "paper-signal-producer"
_LOCAL_SERVICE_TOKEN = "pantheon-local-agora-paper-execution-service-token"
_ENV_EXAMPLE_SERVICE_TOKEN = "replace-me-agora-paper-execution-service-token"
_DRAIN_LIMIT_DEFAULT = 100
_DRAIN_LIMIT_MAX = 200

PAPER_EXECUTION_REJECT_REASONS = (
    "daily_cap",
    "owner_mismatch",
    "paper_book_missing",
    "binding_not_paper",
    "non_paper_stage",
    "invalid_order",
    "symbol_not_executable",
    "market_data_unavailable",
    "market_data_stale",
    "signal_validation_failed",
    "enqueue_failed",
    "broker_rejected",
    "expired",
)


class PaperExecutionStateUpdate(BaseModel):
    """State written back by the paper execution owner."""

    state: Literal["accepted", "rejected", "filled", "partially_filled"]
    environment: Literal["paper"] = "paper"
    no_order_route_proof: Literal["agora_request_only_no_order_route"]
    reason_code: Optional[Literal[PAPER_EXECUTION_REJECT_REASONS]] = None  # type: ignore[valid-type]
    detail: Optional[str] = Field(default=None, max_length=300)
    runtime_binding_id: Optional[str] = None
    capital_pool_id: Optional[str] = None
    signal_id: Optional[str] = None
    daily_slot: Optional[int] = Field(default=None, ge=1)
    market_price: Optional[float] = Field(default=None, gt=0)
    market_as_of: Optional[str] = None
    market_source_ref: Optional[str] = None
    broker_order_id: Optional[str] = None
    fill_qty: Optional[float] = Field(default=None, ge=0)
    fill_price: Optional[float] = Field(default=None, gt=0)
    filled_at: Optional[str] = None
    accepted_at: Optional[str] = None


_PLACEHOLDER_TOKEN_PREFIXES = ("replace-me", "changeme", "change-me", "example", "dummy", "placeholder")
_LOCAL_TOKEN_PROFILE = "local"


def is_public_or_placeholder_token(token: str) -> bool:
    """True for the public compose default, the env example value or a placeholder."""

    normalized = str(token or "").strip().lower()
    if normalized in {_LOCAL_SERVICE_TOKEN, _ENV_EXAMPLE_SERVICE_TOKEN}:
        return True
    return normalized.startswith(_PLACEHOLDER_TOKEN_PREFIXES)


def paper_execution_boundary_status() -> Dict[str, Any]:
    """Readable configuration state of the paper execution service boundary.

    The repository is public, so the compose default token is public too. Only
    the explicit local profile (``AGORA_PAPER_EXECUTION_TOKEN_PROFILE=local``,
    never in staging/prod posture) may use it. Everywhere else an unset, empty,
    default or placeholder token leaves the boundary ``not_configured`` and every
    call is rejected; the service itself keeps running and stays healthy.
    """

    token = str(os.getenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN") or "").strip()
    actor = str(
        os.getenv("AGORA_PAPER_EXECUTION_ALLOWED_SERVICE_ACTOR") or PAPER_EXECUTION_SERVICE_ACTOR
    ).strip()
    tenants = [
        item.strip()
        for item in str(os.getenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS") or "").split(",")
        if item.strip()
    ]
    posture = str(
        os.getenv("PANTHEON_PERSISTENCE_POSTURE") or os.getenv("PANTHEON_ENV") or "dev"
    ).strip().lower()
    profile = str(os.getenv("AGORA_PAPER_EXECUTION_TOKEN_PROFILE") or "").strip().lower()
    local_profile = profile == _LOCAL_TOKEN_PROFILE and not posture.startswith(("stage", "staging", "prod"))
    reason: Optional[str] = None
    if not token:
        reason = "service_token_unset"
    elif is_public_or_placeholder_token(token) and not local_profile:
        reason = "service_token_public_default_or_placeholder"
    elif not actor:
        reason = "service_actor_unset"
    elif not tenants:
        reason = "service_tenants_unset"
    status: Dict[str, Any] = {
        "boundary": "agora_paper_execution",
        "state": "configured" if reason is None else "not_configured",
        "token_profile": "local" if local_profile else "hosted",
    }
    if reason is not None:
        status["reason"] = reason
    return status


def _service_scope(
    authorization: Optional[str],
    service_actor: Optional[str],
    requested_tenant_id: Optional[str],
) -> tuple[str, str]:
    """Authenticate the paper execution lane (not an Agora browser user)."""

    status = paper_execution_boundary_status()
    if status["state"] != "configured":
        raise HTTPException(
            status_code=503,
            detail={
                **status,
                "detail": "Agora paper execution service boundary is not safely configured",
            },
        )
    expected_token = str(os.getenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN") or "").strip()
    expected_actor = str(
        os.getenv("AGORA_PAPER_EXECUTION_ALLOWED_SERVICE_ACTOR") or PAPER_EXECUTION_SERVICE_ACTOR
    ).strip()
    allowed_tenants = {
        item.strip()
        for item in str(os.getenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS") or "").split(",")
        if item.strip()
    }
    scheme, _, supplied = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied, expected_token):
        raise HTTPException(status_code=401, detail="Agora paper execution service credential is invalid")
    actor = str(service_actor or "").strip()
    if actor != expected_actor:
        raise HTTPException(status_code=403, detail="Agora paper execution service actor is outside authority")
    tenant_id = str(requested_tenant_id or "").strip()
    if not tenant_id or tenant_id not in allowed_tenants:
        raise HTTPException(status_code=403, detail="Agora paper execution tenant is outside service authority")
    return actor, tenant_id


def build_paper_execution_router(ctx: TradingRoomRouteContext) -> APIRouter:
    router = APIRouter()
    status = paper_execution_boundary_status()
    # Readable configuration state (never the token value); a not_configured
    # boundary rejects every call but never stops operator-bff.
    log.info(
        "agora paper execution boundary state=%s reason=%s token_profile=%s",
        status["state"], status.get("reason", "none"), status["token_profile"],
    )

    @router.get("/internal/agora/paper-execution/handoffs")
    def drain_paper_execution_handoffs(
        limit: int = Query(default=_DRAIN_LIMIT_DEFAULT, ge=1, le=_DRAIN_LIMIT_MAX),
        authorization: Optional[str] = Header(default=None),
        service_actor: Optional[str] = Header(default=None, alias="X-Pantheon-Service-Actor"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        actor, tenant_id = _service_scope(authorization, service_actor, x_tenant_id)
        items = ctx.service.list_paper_execution_handoffs(tenant_id=tenant_id, limit=limit)
        return {
            "status": "success",
            "items": items,
            "total": len(items),
            "meta": ctx._meta(
                audience=f"tenant:{tenant_id}:service:{actor}",
                target_queue="paper_execution",
                no_order_route_proof="agora_request_only_no_order_route",
            ),
        }

    @router.post("/internal/agora/paper-execution/handoffs/{handoff_id}/state")
    def write_paper_execution_state(
        handoff_id: str,
        body: PaperExecutionStateUpdate,
        authorization: Optional[str] = Header(default=None),
        service_actor: Optional[str] = Header(default=None, alias="X-Pantheon-Service-Actor"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        actor, tenant_id = _service_scope(authorization, service_actor, x_tenant_id)
        if body.state == "rejected" and not body.reason_code:
            raise ctx.bff_error(
                422, "VALIDATION_ERROR", "A rejected paper execution needs a typed reason_code",
                "paper_execution_reason_required",
            )
        if body.state in {"filled", "partially_filled"} and (
            body.fill_qty is None or body.fill_price is None or not body.broker_order_id
        ):
            raise ctx.bff_error(
                422, "VALIDATION_ERROR", "A fill state needs broker_order_id, fill_qty and fill_price",
                "paper_execution_fill_incomplete",
            )
        result = body.model_dump(exclude_none=True)
        result.pop("state", None)
        handoff, changed = ctx.service.write_paper_execution_state(
            handoff_id=handoff_id,
            tenant_id=tenant_id,
            state=body.state,
            result=result,
            actor=actor,
        )
        return {
            "status": "updated" if changed else "exists",
            "idempotent": not changed,
            "data": handoff,
            "meta": ctx._meta(audience=f"tenant:{tenant_id}:service:{actor}"),
        }

    return router


__all__ = [
    "PAPER_EXECUTION_REJECT_REASONS",
    "PAPER_EXECUTION_SERVICE_ACTOR",
    "PaperExecutionStateUpdate",
    "build_paper_execution_router",
]

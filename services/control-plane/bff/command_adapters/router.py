"""BFF Domain Command Adapters router.

Owns the command adapter endpoints:
  1. GET /bff/actions
  2. GET /api/v1/operator/commands/{command_id}
  3. POST /bff/v1/commands
  4. POST /bff/command-confirmations
  5. GET /bff/command-confirmations/{token}
  6. POST /bff/command-confirmations/{token}/confirm
  7. POST /bff/confirm-tokens
  8. GET /bff/confirm-tokens/{tokenId}
  9. POST /bff/confirm-tokens/{tokenId}/redeem
  10. DELETE /bff/confirm-tokens/{tokenId}

`POST /bff/v1/commands` is the sole canonical generic command write route.
The legacy `POST /bff/actions/{type}/{id}/{action}` and
`POST /api/v1/operator/commands` routes have been retired; use
`POST /bff/v1/commands` for all generic command writes.

Matrix item: ACG-01-011 / OPGAP-BE-COMMAND-ADAPTERS-20260830
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Header, Request, Response

try:
    from ..models import (
        ActionCommandStatus,
        BffActionCatalogResponse,
        CommandReceipt,
        CommandReceiptStatus,
        CommandResponse,
        CommandResultMeta,
        CommandStatus,
        CommandStatusResponse,
        OperatorCommand,
        OperatorIdentity,
        StalenessWarning,
        TargetObject,
        utc_now,
    )
except (ImportError, ValueError):
    from models import (
        ActionCommandStatus,
        BffActionCatalogResponse,
        CommandReceipt,
        CommandReceiptStatus,
        CommandResponse,
        CommandResultMeta,
        CommandStatus,
        CommandStatusResponse,
        OperatorCommand,
        OperatorIdentity,
        StalenessWarning,
        TargetObject,
        utc_now,
    )
from .service import CommandAdapterService

log = logging.getLogger(__name__)


def create_command_adapters_router(
    *,
    command_store: Optional[Any] = None,
    read_surface: Optional[Any] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[..., OperatorIdentity]] = None,
    require_operator_role: Optional[Callable[[OperatorIdentity], None]] = None,
    require_read_role: Optional[Callable[[OperatorIdentity], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    submit_command_admission: Optional[Callable[..., Any]] = None,
    dispatch_command: Optional[Callable[..., Any]] = None,
    publish_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    gov_bff_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
    check_read_surface_state: Optional[Callable[[], Optional[StalenessWarning]]] = None,
    service: Optional[CommandAdapterService] = None,
) -> APIRouter:
    """Create the full command adapters router with all 10 command endpoints."""
    router = APIRouter()
    svc = service or CommandAdapterService(
        command_store=command_store,
        read_surface=read_surface,
        get_command_store=get_command_store,
        get_read_store=get_read_store,
        extract_identity=extract_identity,
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
        submit_command_admission=submit_command_admission,
        dispatch_command_fn=dispatch_command,
        publish_event=publish_event,
        gov_bff_idempotency=gov_bff_idempotency,
        check_read_surface_state=check_read_surface_state,
    )

    # 1. Action Catalog
    @router.get("/bff/actions", response_model=BffActionCatalogResponse)
    async def get_action_catalog_endpoint(
        authorization: Optional[str] = Header(default=None),
    ) -> BffActionCatalogResponse:
        """Return the canonical backend action catalog."""
        identity = svc.extract_identity(authorization)
        return svc.get_action_catalog(identity)

    # 2. Command Status Lookup
    @router.get("/api/v1/operator/commands/{command_id}", response_model=CommandStatusResponse)
    async def get_command_status(
        command_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> CommandStatusResponse:
        """Poll for the status of a previously submitted command."""
        identity = svc.extract_identity(authorization)
        return svc.get_command_status(command_id, identity)

    # 3. Final BFF Command Submission
    @router.post("/bff/v1/commands", status_code=202)
    async def submit_final_command(
        background_tasks: BackgroundTasks,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Submit an operator command (final BFF contract)."""
        return svc.submit_final_command(
            background_tasks=background_tasks,
            payload=payload,
            authorization=authorization,
            x_mfa_token=x_mfa_token,
            x_trace_id=x_trace_id,
            x_correlation_id=x_correlation_id,
            x_request_id=x_request_id,
            x_confirm_token=x_confirm_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 4. Command Confirmation (submit token)
    @router.post("/bff/command-confirmations", status_code=202)
    async def bff_command_confirmation(
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: submit a command confirmation token."""
        identity = svc.extract_identity(authorization)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        return svc.submit_command_confirmation(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 5. Command Confirmation Status Read
    @router.get("/bff/command-confirmations/{token}")
    async def bff_command_confirmation_status(
        token: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: read the command-confirmation lifecycle state for a token."""
        identity = svc.extract_identity(authorization)
        return svc.get_command_confirmation_status(token=token, identity=identity)

    # 6. Confirm Command by Token
    @router.post("/bff/command-confirmations/{token}/confirm", status_code=202)
    async def bff_confirm_command_by_token(
        token: str,
        request: Request,
        response: Response,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """BFF: confirm a pending high-risk command by its token."""
        identity = svc.extract_identity(authorization)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        return svc.confirm_command_by_token(
            token=token,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_correlation_id=x_correlation_id,
            x_request_id=x_request_id,
            x_dry_run=x_dry_run,
            response=response,
        )

    # 7. Create Confirm Token
    @router.post("/bff/confirm-tokens", status_code=201)
    async def sem_create_confirm_token_command(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Create a new confirm token."""
        identity = svc.extract_identity(authorization)
        return svc.create_confirm_token(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 8. Get Confirm Token Status
    @router.get("/bff/confirm-tokens/{tokenId}")
    async def sem_get_confirm_token(
        tokenId: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """Read confirm token lifecycle status."""
        identity = svc.extract_identity(authorization)
        return svc.get_confirm_token(token_id=tokenId, identity=identity)

    # 9. Redeem Confirm Token
    @router.post("/bff/confirm-tokens/{tokenId}/redeem", status_code=202)
    async def sem_redeem_confirm_token_command(
        tokenId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Redeem a confirm token."""
        identity = svc.extract_identity(authorization)
        return svc.redeem_confirm_token(
            token_id=tokenId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 10. Delete Confirm Token
    @router.delete("/bff/confirm-tokens/{tokenId}", status_code=202)
    async def sem_delete_confirm_token_command(
        tokenId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Delete a confirm token."""
        identity = svc.extract_identity(authorization)
        return svc.delete_confirm_token(
            token_id=tokenId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    return router

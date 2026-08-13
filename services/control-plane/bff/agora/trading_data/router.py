"""FastAPI router for Agora trading data widget queries."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from ..models import AgoraEnvelope, AgoraMeta
from .models import WidgetDataQueryRequest, WidgetDataQueryResponse
from .service import TradingDataService


def create_trading_data_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    service: Optional[TradingDataService] = None,
) -> APIRouter:
    router = APIRouter(tags=["agora-trading-data"])
    data_service = service or TradingDataService()

    @router.post("/bff/agora/trading-data/query")
    def query_trading_data(
        req: WidgetDataQueryRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            raise bff_error(exc.status_code, "FORBIDDEN", exc.message, exc.reason)

        now_str = utc_now()
        res = data_service.query_widget_data(
            req,
            scope_tenant_id=scope.tenant_id,
            scope_user_id=scope.user_id,
            utc_now=now_str,
        )

        envelope = AgoraEnvelope(
            data=res.model_dump(),
            meta=AgoraMeta(
                snapshot_at=now_str,
                capability="agora.trading.v1",
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        return envelope.model_dump()

    @router.get("/bff/agora/trading-data/allowlist")
    def list_allowlist(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            raise bff_error(exc.status_code, "FORBIDDEN", exc.message, exc.reason)

        now_str = utc_now()
        allowlist = data_service.list_allowlist()

        envelope = AgoraEnvelope(
            data={"allowlisted_widgets": allowlist, "count": len(allowlist)},
            meta=AgoraMeta(
                snapshot_at=now_str,
                capability="agora.trading.v1",
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        return envelope.model_dump()

    return router

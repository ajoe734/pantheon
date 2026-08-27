"""Scoped Agora Strategy Performance read and governed action routes."""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from .models import (
    PerformanceProjectionEnvelope,
    SuggestionActionEnvelope,
    SuggestionActionRequest,
    SuggestionActionReceipt,
)
from .attribution import TradingRoomPerformanceAttributionEnvelope
from .service import PerformanceProjectionService
from .store import (
    PerformanceSuggestionConflict,
    PerformanceSuggestionNotFound,
    PerformanceSuggestionStore,
)


def create_performance_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_trade_journey_store: Callable[[], Any],
    workshop_store: Optional[Any] = None,
    suggestion_store: Optional[PerformanceSuggestionStore] = None,
) -> APIRouter:
    router = APIRouter(tags=["agora-performance"])
    store = suggestion_store or PerformanceSuggestionStore()
    service = PerformanceProjectionService(
        suggestion_store=store,
        get_trade_journey_store=get_trade_journey_store,
        utc_now=utc_now,
    )

    def resolve_scope(
        authorization: Optional[str], tenant_id: Optional[str], *, write: bool = False
    ) -> Any:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if write:
            require_write_role(identity)
        try:
            return resolve_agora_user_scope(
                identity, utc_now=utc_now, requested_tenant_id=tenant_id
            )
        except AgoraScopeResolutionError as exc:
            code = "AUTH_REQUIRED" if exc.status_code == 401 else "FORBIDDEN"
            raise bff_error(
                exc.status_code,
                code,
                exc.message,
                exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            ) from exc

    def meta(scope: Any, *, replayed: bool = False) -> dict[str, Any]:
        return {
            "snapshot_at": utc_now(),
            "capability": "agora.performance.truth.v1",
            "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            "authoritative_store": "agora_performance_sqlite",
            "idempotent_replay": replayed,
            "execution_authority": "none",
        }

    @router.get(
        "/bff/agora/trading-room/strategies/{strategy_id}/performance",
        response_model=PerformanceProjectionEnvelope,
    )
    def get_strategy_performance(
        strategy_id: str,
        period: Literal["latest", "7d", "30d", "all"] = Query(default="latest"),
        environment: Literal["paper", "broker_sandbox", "canary", "live"] = Query(
            default="paper"
        ),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        scope = resolve_scope(authorization, x_tenant_id)
        if environment == "live" and not {
            "operator", "reviewer", "approver", "admin"
        }.intersection(scope.roles):
            raise bff_error(
                403,
                "FORBIDDEN",
                "Live performance identities require operator-level access",
                "live_performance_identity_role_denied",
                precondition_failed="role_check",
            )
        projection = service.project(
            tenant_id=scope.tenant_id,
            owner_user_id=scope.user_id,
            strategy_id=strategy_id,
            period=period,
            environment=environment,
        )
        return {
            "data": projection.model_dump(mode="json"),
            "meta": meta(scope),
        }

    @router.post(
        "/bff/agora/trading-room/strategies/{strategy_id}/performance/suggestions/{suggestion_id}/actions",
        response_model=SuggestionActionEnvelope,
    )
    def act_on_suggestion(
        strategy_id: str,
        suggestion_id: str,
        body: SuggestionActionRequest,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        scope = resolve_scope(authorization, x_tenant_id, write=True)
        clean_key = str(idempotency_key or "").strip()
        if len(clean_key) < 8:
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key header must contain at least 8 characters",
            )
        try:
            receipt, replayed = store.act(
                tenant_id=scope.tenant_id,
                owner_user_id=scope.user_id,
                strategy_id=strategy_id,
                suggestion_id=suggestion_id,
                action=body.action,
                expected_version=body.expected_version,
                reason=body.reason,
                actor_id=scope.operator_id,
                idempotency_key=clean_key,
                recorded_at=utc_now(),
            )
        except PerformanceSuggestionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PerformanceSuggestionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response.headers["ETag"] = f'"{receipt["version"]}"'
        return {
            "data": SuggestionActionReceipt.model_validate(receipt).model_dump(mode="json"),
            "meta": meta(scope, replayed=replayed),
        }

    @router.get(
        "/bff/agora/performance/action-receipts/{receipt_id}",
        response_model=SuggestionActionEnvelope,
    )
    def get_action_receipt(
        receipt_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        scope = resolve_scope(authorization, x_tenant_id)
        receipt = store.get_receipt(
            tenant_id=scope.tenant_id,
            owner_user_id=scope.user_id,
            receipt_id=receipt_id,
        )
        if receipt is None:
            raise HTTPException(status_code=404, detail="suggestion action receipt not found")
        return {
            "data": SuggestionActionReceipt.model_validate(receipt).model_dump(mode="json"),
            "meta": meta(scope),
        }

    @router.get(
        "/bff/agora/trading-room/performance-attribution/by-strategy",
        response_model=TradingRoomPerformanceAttributionEnvelope,
    )
    def get_agora_performance_attribution_by_strategy(
        period: str = Query(default="latest"),
        page_size: int = Query(default=50, ge=1, le=200),
        pageSize: Optional[int] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        pageToken: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None),
        strategyId: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> dict[str, Any]:
        scope = resolve_scope(authorization, x_tenant_id or x_pantheon_tenant)
        envelope = service.project_attribution(
            tenant_id=scope.tenant_id,
            owner_user_id=scope.user_id,
            period=period,
            page_size=pageSize if pageSize is not None else page_size,
            page_token=pageToken or page_token,
            strategy_id_filter=strategyId or strategy_id,
            workshop_store=workshop_store,
        )
        return envelope.model_dump(mode="json")

    return router


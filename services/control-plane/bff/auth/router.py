"""Named BFF auth router prepared for composition-root cutover."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Cookie, Header, Query, Response

from .service import AuthFacadeService, ProviderReadinessCache


def _safe_provider_readiness(cache: ProviderReadinessCache) -> Dict[str, Any]:
    """Compatibility helper whose implementation is deliberately cache-only."""
    return cache.snapshot()


def create_auth_router(*, service: AuthFacadeService) -> APIRouter:
    """Create the seven-route auth/session facade from injected local services."""
    router = APIRouter(tags=["auth"])

    @router.post("/bff/auth/dev-login")
    async def bff_auth_dev_login(payload: Dict[str, Any] = Body(default_factory=dict)):
        return await service.invoke("bff_auth_dev_login", payload=payload)

    @router.get("/bff/me")
    async def bff_me(
        response: Response,
        tenant_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_locale: Optional[str] = Header(default=None, alias="X-Locale"),
        accept_language: Optional[str] = Header(default=None, alias="Accept-Language"),
    ):
        return await service.invoke(
            "bff_me",
            response=response,
            tenant_id=tenant_id,
            authorization=authorization,
            pantheon_session=pantheon_session,
            x_mfa_token=x_mfa_token,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
            x_correlation_id=x_correlation_id,
            x_locale=x_locale,
            accept_language=accept_language,
        )

    @router.get("/bff/auth/readiness")
    async def bff_auth_readiness(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ):
        return await service.readiness(
            authorization=authorization,
            pantheon_session=pantheon_session,
            x_mfa_token=x_mfa_token,
            x_tenant_id=x_tenant_id,
        )

    @router.post("/bff/auth/refresh")
    async def bff_auth_refresh(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        pantheon_refresh: Optional[str] = Cookie(default=None),
        pantheon_refresh_token: Optional[str] = Cookie(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_refresh_token: Optional[str] = Header(default=None, alias="X-Refresh-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        return await service.invoke(
            "bff_auth_refresh",
            payload=payload,
            authorization=authorization,
            pantheon_session=pantheon_session,
            pantheon_refresh=pantheon_refresh,
            pantheon_refresh_token=pantheon_refresh_token,
            x_mfa_token=x_mfa_token,
            x_refresh_token=x_refresh_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.post("/bff/logout")
    async def bff_logout(
        response: Response,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        return await service.invoke(
            "bff_logout",
            response=response,
            payload=payload,
            authorization=authorization,
            pantheon_session=pantheon_session,
            x_mfa_token=x_mfa_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    @router.post("/bff/switch-tenant")
    async def bff_switch_tenant(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ):
        return await service.invoke(
            "bff_switch_tenant",
            payload=payload,
            authorization=authorization,
        )

    @router.patch("/bff/me/locale")
    async def bff_update_locale(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ):
        return await service.invoke(
            "bff_update_locale",
            payload=payload,
            authorization=authorization,
        )

    return router


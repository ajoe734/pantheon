"""Use the existing dev-login JWT as a browser session, without another IdP."""
from __future__ import annotations

from typing import Any, Callable

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse


# These handlers already accept the cookie directly and preserve session kind.
_COOKIE_ROUTES = frozenset({
    "/bff/me", "/bff/auth/readiness", "/bff/auth/refresh", "/bff/logout",
    "/bff/auth/dev-login",  # A stale cookie must not prevent reauthentication.
})


class DevBrowserSessionMiddleware:
    """Adapt cookie credentials for existing bearer-only business handlers.

    An explicit Authorization header always takes precedence. Cookie mutations
    require an allowed Origin because the browser attaches cookies automatically.
    Signature, expiry, roles and tenant remain owned by the existing JWT policy.
    This pure-ASGI adapter does not buffer requests or streaming responses.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: Callable[[], bool],
        origin_allowed: Callable[[str | None], bool],
        validate_session: Callable[[str], None],
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.origin_allowed = origin_allowed
        self.validate_session = validate_session

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/bff/") or not self.enabled():
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        token = request.cookies.get("pantheon_session")
        if not token or "authorization" in request.headers:
            await self.app(scope, receive, send)
            return
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not self.origin_allowed(request.headers.get("origin")):
            await JSONResponse(
                {"error": {"code": "FORBIDDEN", "message": "Browser session requires an allowed Origin"}},
                status_code=403,
            )(scope, receive, send)
            return
        if scope["path"] not in _COOKIE_ROUTES:
            try:
                self.validate_session(token)
            except HTTPException as exc:
                await JSONResponse(exc.detail, status_code=exc.status_code, headers=exc.headers)(scope, receive, send)
                return
            scope = dict(scope)
            scope["headers"] = [*scope.get("headers", []), (b"authorization", f"Bearer {token}".encode("latin-1"))]
        await self.app(scope, receive, send)

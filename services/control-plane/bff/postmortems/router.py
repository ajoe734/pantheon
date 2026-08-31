"""Postmortem domain canonical router.

OPGAP-BE-POSTMORTEM-ROUTER-V2-20260830 moves the two postmortem read
decorators behind a dedicated domain factory while preserving the existing
``/api/v1/postmortems`` list/detail contracts.  ``main.py`` assembly is owned
by the later BFF assembly task, so this module has no dependency on main.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

try:
    from models import ErrorCode
except ImportError:
    try:
        from ..models import ErrorCode  # type: ignore[no-redef]
    except Exception:
        class ErrorCode(str, Enum):  # type: ignore[no-redef]
            RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"

from .service import PostmortemService


def _default_extract_identity(authorization: Optional[str] = None) -> Any:
    class Identity:
        roles = {"viewer"}

    return Identity()


def _default_require_read_role(identity: Any) -> None:
    return None


def _default_bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: str,
    **kwargs: Any,
) -> HTTPException:
    code_value = code.value if hasattr(code, "value") else str(code)
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code_value,
            "message": message,
            "reason": reason,
            **kwargs,
        },
    )


def create_postmortem_router(
    *,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[[Optional[str]], Any]] = None,
    require_read_role: Optional[Callable[[Any], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    meta_staleness: Optional[Callable[[], Any]] = None,
    postmortem_service: Optional[PostmortemService] = None,
) -> APIRouter:
    """Build the canonical two-route Postmortem read router."""

    router = APIRouter()
    _get_store = get_read_store or (lambda: getattr(postmortem_service, "read_store", None))
    _extract_identity = extract_identity or _default_extract_identity
    _require_read_role = require_read_role or _default_require_read_role
    _bff_error = bff_error or _default_bff_error
    _meta_staleness = meta_staleness or (lambda: None)

    def _get_service() -> PostmortemService:
        if postmortem_service is not None:
            return postmortem_service
        return PostmortemService(_get_store())

    @router.get("/api/v1/postmortems")
    async def list_postmortems(
        time_range: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """IN-03: Postmortem List."""

        identity = _extract_identity(authorization)
        _require_read_role(identity)

        postmortems = _get_service().list_postmortems(time_range=time_range)
        return {
            "data": postmortems,
            "meta": {
                "total": len(postmortems),
                "staleness": _meta_staleness(),
            },
        }

    @router.get("/api/v1/postmortems/{report_id}")
    async def get_postmortem(
        report_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """IN-04: Postmortem Detail."""

        identity = _extract_identity(authorization)
        _require_read_role(identity)

        postmortem = _get_service().get_postmortem(report_id)
        if not postmortem:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Postmortem report not found",
                f"Postmortem {report_id} does not exist",
            )

        return {
            "data": postmortem,
            "meta": {
                "staleness": _meta_staleness(),
            },
        }

    return router

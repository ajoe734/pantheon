"""Agora servant router — servant persona profile (agora.identity.v1).

New routes (not yet in main.py) registered here as stubs:
  POST /bff/agora/servant/ensure   — AG-BE-ID-002

Migration note: All other identity routes are currently in main.py.
This module is the intended home for servant-specific logic.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

def create_servant_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    router = APIRouter(tags=["agora-identity"])

    @router.post("/bff/agora/servant/ensure")
    def agora_servant_ensure(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Provision or reconcile the user-private Agora servant persona. Stub — see AG-BE-ID-002."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        from models import ErrorCode  # BFF top-level models; available via sys.path in runtime
        raise bff_error(
            501,
            ErrorCode.NOT_IMPLEMENTED,
            "POST /bff/agora/servant/ensure is not yet implemented — see AG-BE-ID-002",
            "stub: agora servant provision not implemented",
        )

    return router

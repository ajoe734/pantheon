from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, Query

from ._envelope import _gov_list_envelope

_DATASET = "governance_permissions"
_SURFACE_KEY = "governance_permissions"


def create_permissions_router(
    *,
    get_read_store: Callable[[], Any],
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/bff/management/permissions")
    def bff_management_permissions(
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF-GOVRULES-01: Governance permission rules list."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        store = get_read_store()
        source = store.dataset_source(_DATASET)
        from models import utc_now
        snapshot_at = utc_now()

        items: List[Dict[str, Any]] = store.list_governance_permissions() if source != "missing" else []

        return _gov_list_envelope(
            items=items,
            surface_key=_SURFACE_KEY,
            source=source,
            snapshot_at=snapshot_at,
            page_token=page_token,
            page_size=page_size,
        )

    return router

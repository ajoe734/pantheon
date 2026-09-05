from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, Query

from ._envelope import _gov_list_envelope
from .contracts import ManagementRecordsEnvelope

_DATASET = "memory_governance_rules"
_SURFACE_KEY = "memory_governance_rules"


def create_memory_governance_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/bff/management/memory-governance",
        response_model=ManagementRecordsEnvelope,
        response_model_exclude={"items"},
    )
    def bff_management_memory_governance(
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF-GOVRULES-02: Memory governance rules list."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        store = read_surface if read_surface is not None else (get_read_store() if get_read_store is not None else None)
        if store is None:
            raise RuntimeError("Required read_surface is absent; failing startup closed.")
        source = store.dataset_source(_DATASET)
        from models import utc_now
        snapshot_at = utc_now()

        items: List[Dict[str, Any]] = store.list_memory_governance_rules() if source != "missing" else []

        return _gov_list_envelope(
            items=items,
            surface_key=_SURFACE_KEY,
            source=source,
            snapshot_at=snapshot_at,
            page_token=page_token,
            page_size=page_size,
        )

    return router

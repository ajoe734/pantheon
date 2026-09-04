"""BFF-AF-001: GET /bff/alpha-factory — idea→strategy kanban board."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, Query

_SURFACE_KEY = "alpha_factory"
_DATASET = "alpha_factory_cards"

_LANES = [
    {"id": "ideas", "label": "Ideas"},
    {"id": "strategies", "label": "Strategies"},
    {"id": "experiments", "label": "Experiments"},
]


def _build_surface(store: Any, snapshot_at: str) -> Dict[str, Any]:
    source = store.dataset_source(_DATASET)
    if source == "missing":
        return {
            "status": "unavailable",
            "source": "missing",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }
    if source == "local_snapshot":
        return {"status": "degraded", "source": source}
    return {"status": "ok", "source": source}


def _build_alpha_factory_payload(
    snapshot_at: str,
    *,
    items: List[Dict[str, Any]],
    page: int,
    page_size: int,
    lane: Optional[str],
    surface: Dict[str, Any],
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": "alpha-factory",
        "snapshotAt": snapshot_at,
        "snapshot_at": snapshot_at,
        "lanes": _LANES,
        "items": items,
    }
    return {
        "data": data,
        "items": items,
        "page_info": {
            "page": page,
            "page_size": page_size,
            "total": len(items),
            "next_page_token": None,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {_SURFACE_KEY: surface},
            "filters": {"lane": lane},
        },
    }


def create_alpha_factory_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Callable,
    require_read_role: Callable,
    utc_now: Callable[[], str],
) -> APIRouter:
    router = APIRouter()

    def _resolve_store() -> Any:
        if read_surface is not None:
            return read_surface() if callable(read_surface) else read_surface
        if get_read_store is not None:
            return get_read_store()
        raise RuntimeError("Neither read_surface nor get_read_store was configured.")

    @router.get("/bff/alpha-factory")
    async def get_alpha_factory(
        lane: Optional[str] = Query(default=None, description="Filter by lane id: ideas | strategies | experiments"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF-AF-001: AlphaFactory idea→strategy kanban board (lanes/cards)."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = _resolve_store()
        surface = _build_surface(store, snapshot_at)

        if surface.get("status") == "unavailable":
            items: List[Dict[str, Any]] = []
        else:
            items = store.list_alpha_factory_cards(page=page, page_size=page_size, lane=lane)

        return _build_alpha_factory_payload(
            snapshot_at,
            items=items,
            page=page,
            page_size=page_size,
            lane=lane,
            surface=surface,
        )

    return router

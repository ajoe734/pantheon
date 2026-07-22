from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from .contracts import ManagementRecordsEnvelope


ReadStoreProvider = Callable[[], Any]
ExtractIdentity = Callable[[Optional[str]], Any]
RequireReadRole = Callable[[Any], None]
SnapshotNow = Callable[[], str]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _decode_page_token(page_token: Optional[str]) -> int:
    if page_token in (None, ""):
        return 0
    try:
        offset = int(str(page_token))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="page_token must be a non-negative integer offset") from exc
    if offset < 0:
        raise HTTPException(status_code=422, detail="page_token must be a non-negative integer offset")
    return offset


def _page_slice(items: List[Dict[str, Any]], page_token: Optional[str], page_size: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
    start = _decode_page_token(page_token)
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


def _surface_state(*, source: str, has_items: bool, snapshot_at: str) -> Dict[str, Any]:
    if source == "missing":
        return {
            "status": "unavailable",
            "source": "missing",
            "staleness": {
                "served_from": "unverifiable",
                "last_known_at": snapshot_at,
            },
        }
    if not has_items:
        return {
            "status": "unavailable",
            "source": source,
            "staleness": {
                "served_from": source,
                "last_known_at": snapshot_at,
            },
        }
    if source == "local_snapshot":
        return {
            "status": "degraded",
            "source": "local_snapshot",
            "note": "Served from local BFF snapshot fallback instead of a backend-owned read store.",
            "staleness": {
                "served_from": "local_snapshot",
                "last_known_at": snapshot_at,
            },
        }
    return {"status": "ok", "source": source}


def _list_response(
    *,
    store: Any,
    dataset: str,
    surface_key: str,
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
    snapshot_now: SnapshotNow,
) -> Dict[str, Any]:
    snapshot_at = snapshot_now()
    source = "missing"
    dataset_source = getattr(store, "dataset_source", None)
    if callable(dataset_source):
        source = str(dataset_source(dataset) or "missing")

    page_items: List[Dict[str, Any]]
    next_page_token: Optional[str]
    if source == "missing" or not items:
        page_items = []
        next_page_token = None
        total = 0
    else:
        total = len(items)
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    surface = _surface_state(
        source=source,
        has_items=bool(items) and source != "missing",
        snapshot_at=snapshot_at,
    )
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "status": surface["status"],
        "source": surface["source"],
        "total": total,
        "surfaces": {
            surface_key: surface,
        },
    }
    if surface["status"] == "unavailable":
        meta["degradation"] = {
            "reason": f"{surface_key.replace('_', ' ')} is currently unavailable.",
        }

    return {
        "data": page_items,
        "items": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
            "page_size": page_size,
            "returned": len(page_items),
            "has_more": next_page_token is not None,
        },
        "meta": meta,
    }


def create_workflows_hooks_router(
    *,
    read_store_provider: ReadStoreProvider,
    extract_identity: ExtractIdentity,
    require_read_role: RequireReadRole,
    snapshot_now: Optional[SnapshotNow] = None,
) -> APIRouter:
    router = APIRouter(tags=["automation-registry"])
    now = snapshot_now or _utc_now

    @router.get(
        "/bff/workflows",
        summary="List workflow templates",
        operation_id="listBffWorkflowTemplates",
        response_model=ManagementRecordsEnvelope,
    )
    async def list_workflows(
        page_token: Optional[str] = None,
        page_size: int = Query(default=100, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        store = read_store_provider()
        return _list_response(
            store=store,
            dataset="workflow_templates",
            surface_key="workflow_templates",
            items=list(store.list_workflow_templates()),
            page_token=page_token,
            page_size=page_size,
            snapshot_now=now,
        )

    @router.get(
        "/bff/hooks",
        summary="List cron and hook registry entries",
        operation_id="listBffHookRegistry",
        response_model=ManagementRecordsEnvelope,
    )
    async def list_hooks(
        page_token: Optional[str] = None,
        page_size: int = Query(default=100, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        store = read_store_provider()
        return _list_response(
            store=store,
            dataset="hook_registry",
            surface_key="hook_registry",
            items=list(store.list_hook_registry()),
            page_token=page_token,
            page_size=page_size,
            snapshot_now=now,
        )

    return router

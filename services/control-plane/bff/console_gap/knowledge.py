from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Query

from .contracts import ManagementRecordsEnvelope


ExtractIdentity = Callable[[Optional[str]], Any]
RequireReadRole = Callable[[Any], None]
ReadStoreGetter = Callable[[], Any]
UtcNow = Callable[[], str]
DatasetSurfaceStatus = Callable[..., Dict[str, Any]]


_SOURCE_DATASETS = (
    ("research_notes", "research_note", "knowledge_inbox_notes", "GET /api/v1/knowledge/notes"),
    ("evidence_refs", "evidence_ref", "knowledge_inbox_evidence", "GET /api/v1/knowledge/evidence"),
    ("insight_cards", "insight", "knowledge_inbox_insights", "GET /api/v1/knowledge/insights"),
    (
        "strategy_specs",
        "strategy_spec",
        "knowledge_inbox_strategy_specs",
        "GET /api/v1/knowledge/strategy-specs",
    ),
    (
        "institutional_memory_entries",
        "memory_entry",
        "knowledge_inbox_memory",
        "GET /api/v1/knowledge/memory",
    ),
)


def create_knowledge_router(
    *,
    extract_identity: ExtractIdentity,
    require_read_role: RequireReadRole,
    read_store_getter: Optional[ReadStoreGetter] = None,
    port: Optional[Any] = None,
    port_getter: Optional[Callable[[], Any]] = None,
    utc_now: UtcNow,
    dataset_surface_status: DatasetSurfaceStatus,
) -> APIRouter:
    router = APIRouter(prefix="/bff", tags=["knowledge"])

    @router.get(
        "/knowledge",
        operation_id="get_bff_knowledge_inbox",
        response_model=ManagementRecordsEnvelope,
    )
    async def get_knowledge_inbox(
        item_type: Optional[str] = Query(default=None),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        if port is not None:
            store = port
        elif port_getter is not None:
            store = port_getter()
        elif read_store_getter is not None:
            store = read_store_getter()
        else:
            raise RuntimeError("Either port, port_getter, or read_store_getter must be provided.")
        items, surfaces, source_counts = _collect_knowledge_items(
            store,
            snapshot_at=snapshot_at,
            dataset_surface_status=dataset_surface_status,
        )

        if item_type:
            requested_type = item_type.strip()
            items = [item for item in items if item.get("inboxType") == requested_type]

        items.sort(key=_item_sort_value, reverse=True)
        total = len(items)
        page_items, next_page_token = _page_slice(items, page_token, page_size)
        available_sources = [
            surface
            for surface in surfaces.values()
            if surface.get("status") != "unavailable" or surface.get("source") != "missing"
        ]
        knowledge_inbox_surface = _knowledge_inbox_surface(
            snapshot_at=snapshot_at,
            available=bool(available_sources),
            degraded=any(surface.get("status") != "ok" for surface in surfaces.values()),
        )

        meta = {
            "snapshot_at": snapshot_at,
            "status": knowledge_inbox_surface["status"],
            "source": knowledge_inbox_surface["source"],
            "surfaces": {
                "knowledge_inbox": knowledge_inbox_surface,
                **surfaces,
            },
            "composition": {
                "datasets": [dataset for dataset, _, _, _ in _SOURCE_DATASETS],
                "itemCounts": source_counts,
            },
            "composition_sources": [route for _, _, _, route in _SOURCE_DATASETS],
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

    return router


def _collect_knowledge_items(
    store: Any,
    *,
    snapshot_at: str,
    dataset_surface_status: DatasetSurfaceStatus,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, int]]:
    items: List[Dict[str, Any]] = []
    surfaces: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, int] = {}

    for dataset, inbox_type, surface_key, _ in _SOURCE_DATASETS:
        records = _records_for_dataset(store, dataset)
        source = _dataset_source(store, dataset, records)
        surfaces[surface_key] = dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
            has_data=(source != "missing"),
            missing_message=f"{dataset} has no readable source records.",
        )
        projected = [
            _knowledge_inbox_item(record, inbox_type=inbox_type, source_dataset=dataset)
            for record in records
            if isinstance(record, dict)
        ]
        counts[inbox_type] = len(projected)
        items.extend(projected)

    return items, surfaces, counts


def _records_for_dataset(store: Any, dataset: str) -> List[Dict[str, Any]]:
    if dataset == "research_notes":
        reader = getattr(store, "list_research_notes", None)
        return _safe_list(reader)
    if dataset == "evidence_refs":
        reader = getattr(store, "list_evidence_refs", None)
        return _safe_list(reader)
    if dataset == "insight_cards":
        reader = getattr(store, "list_insight_cards", None)
        return _safe_list(reader)
    if dataset == "strategy_specs":
        reader = getattr(store, "list_strategy_specs", None)
        if not callable(reader):
            return []
        try:
            return [
                item
                for item in reader(
                    lifecycle_state="all",
                    include_retired=True,
                    include_fixture_pack=False,
                )
                if isinstance(item, dict)
            ]
        except TypeError:
            return _safe_list(reader)
    if dataset == "institutional_memory_entries":
        reader = getattr(store, "list_institutional_memory_entries", None)
        return _safe_list(reader)
    return []


def _safe_list(reader: Any) -> List[Dict[str, Any]]:
    if not callable(reader):
        return []
    return [item for item in reader() if isinstance(item, dict)]


def _dataset_source(store: Any, dataset: str, records: List[Dict[str, Any]]) -> str:
    source_fn = getattr(store, "dataset_source", None)
    if callable(source_fn):
        source = str(source_fn(dataset) or "").strip()
        if source:
            return source
    return "bff_composed" if records else "missing"


def _knowledge_inbox_item(
    record: Dict[str, Any],
    *,
    inbox_type: str,
    source_dataset: str,
) -> Dict[str, Any]:
    item = json.loads(json.dumps(record))
    item_id = _record_id(item, inbox_type)
    item.setdefault("id", item_id)
    item["inboxType"] = inbox_type
    item["sourceDataset"] = source_dataset
    item["title"] = item.get("title") or _record_title(item, inbox_type, item_id)
    item["summary"] = item.get("summary") or _record_summary(item)
    item["route_href"] = item.get("route_href") or _route_href(item, inbox_type, item_id)
    item["routeHref"] = item.get("routeHref") or item.get("route_href")
    item["updated_at"] = _record_timestamp(item, prefer_updated=True)
    item["created_at"] = _record_timestamp(item, prefer_updated=False)
    return item


def _record_id(item: Dict[str, Any], inbox_type: str) -> str:
    candidates = {
        "research_note": ("note_id", "id"),
        "evidence_ref": ("ref_id", "id"),
        "insight": ("insight_id", "id"),
        "strategy_spec": ("strategy_id", "id"),
        "memory_entry": ("entry_id", "id"),
    }.get(inbox_type, ("id",))
    for key in candidates:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _record_title(item: Dict[str, Any], inbox_type: str, item_id: str) -> Optional[str]:
    if inbox_type == "evidence_ref":
        source_document = item.get("source_document") if isinstance(item.get("source_document"), dict) else {}
        linked = item.get("linked_object_summary") if isinstance(item.get("linked_object_summary"), dict) else {}
        return source_document.get("title") or linked.get("display_label") or item_id or None
    if inbox_type == "insight":
        return item.get("summary") or item_id or None
    if inbox_type == "memory_entry":
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        return content.get("headline") or item.get("knowledge_type") or item_id or None
    return item_id or None


def _record_summary(item: Dict[str, Any]) -> Optional[str]:
    for key in ("body", "hypothesis_excerpt", "description"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    for key in ("summary", "body", "lesson"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _route_href(item: Dict[str, Any], inbox_type: str, item_id: str) -> Optional[str]:
    if not item_id:
        return None
    route_map = {
        "research_note": "/knowledge/notes",
        "evidence_ref": "/knowledge/evidence",
        "insight": "/knowledge/insights",
        "strategy_spec": "/knowledge/strategy-specs",
        "memory_entry": "/knowledge/memory",
    }
    base = route_map.get(inbox_type)
    return f"{base}/{item_id}" if base else None


def _record_timestamp(item: Dict[str, Any], *, prefer_updated: bool) -> Optional[str]:
    keys = (
        ("updated_at", "updatedAt", "last_modified_at", "aggregated_at", "written_at", "created_at", "createdAt")
        if prefer_updated
        else ("created_at", "createdAt", "captured_at", "aggregated_at", "written_at", "updated_at", "updatedAt")
    )
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    provenance = item.get("aggregation_provenance") if isinstance(item.get("aggregation_provenance"), dict) else {}
    value = provenance.get("aggregated_at")
    return str(value) if value not in (None, "") else None


def _item_sort_value(item: Dict[str, Any]) -> str:
    return str(
        item.get("updated_at")
        or item.get("created_at")
        or item.get("id")
        or item.get("route_href")
        or ""
    )


def _page_slice(
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        start = int(page_token) if page_token not in (None, "") else 0
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "VALIDATION_FAILED", "message": "Invalid page_token"}},
        ) from exc
    if start < 0:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "VALIDATION_FAILED", "message": "Invalid page_token"}},
        )
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


def _knowledge_inbox_surface(
    *,
    snapshot_at: str,
    available: bool,
    degraded: bool,
) -> Dict[str, Any]:
    if not available:
        return {
            "status": "unavailable",
            "source": "missing",
            "message": "Knowledge inbox has no readable knowledge source records.",
            "staleness": {
                "served_from": "unverifiable",
                "last_known_at": snapshot_at,
            },
        }
    if degraded:
        return {
            "status": "degraded",
            "source": "bff_composed",
            "message": "Knowledge inbox is partially composed because one or more source surfaces are degraded.",
        }
    return {"status": "ok", "source": "bff_composed"}

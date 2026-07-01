from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


def _surface_state(
    *,
    status: str,
    source: str,
    snapshot_at: str,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    surface: Dict[str, Any] = {
        "status": status,
        "source": source,
    }
    if message:
        surface["message"] = message
    if status == "unavailable":
        surface["staleness"] = {
            "served_from": "unverifiable" if source == "missing" else source,
            "last_known_at": snapshot_at,
        }
    return surface


def _gov_list_envelope(
    *,
    items: List[Dict[str, Any]],
    surface_key: str,
    source: str,
    snapshot_at: str,
    page_token: Optional[str],
    page_size: int,
) -> Dict[str, Any]:
    """Build canonical list envelope for governance sub-rules read endpoints."""
    start = 0
    if page_token:
        try:
            start = int(page_token)
        except (ValueError, TypeError):
            start = 0
    page_items = items[start: start + page_size]
    next_page_token: Optional[str] = None
    if start + page_size < len(items):
        next_page_token = str(start + page_size)

    if source == "missing":
        status = "unavailable"
        surface_status = _surface_state(
            status=status,
            source="missing",
            snapshot_at=snapshot_at,
            message=f"{surface_key} has no readable source records.",
        )
    else:
        status = "ok" if items else "degraded"
        surface_status = _surface_state(
            status=status,
            source=source,
            snapshot_at=snapshot_at,
            message=f"{surface_key} source is readable but currently empty." if status == "degraded" else None,
        )

    return {
        "data": page_items,
        "items": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": len(items),
            "page_size": page_size,
            "returned": len(page_items),
            "has_more": next_page_token is not None,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "status": status,
            "source": source,
            "surfaces": {
                surface_key: surface_status,
            },
            **(
                {
                    "degradation": {
                        "reason": f"{surface_key.replace('_', ' ')} is currently unavailable.",
                    },
                }
                if status == "unavailable"
                else {}
            ),
        },
    }


def _flat_records(raw: Any) -> List[Dict[str, Any]]:
    """Normalize dict-of-records or list-of-records from local_fallback."""
    if isinstance(raw, dict):
        return [v for v in raw.values() if isinstance(v, dict)]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


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
        surface_status: Dict[str, Any] = {"status": "unavailable", "source": "missing"}
    else:
        surface_status = {"status": "ok", "source": source}

    return {
        "data": page_items,
        "items": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": len(items),
            "page_size": len(page_items),
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                surface_key: surface_status,
            },
        },
    }


def _flat_records(raw: Any) -> List[Dict[str, Any]]:
    """Normalize dict-of-records or list-of-records from local_fallback."""
    if isinstance(raw, dict):
        return [v for v in raw.values() if isinstance(v, dict)]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []

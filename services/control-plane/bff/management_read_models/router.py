from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from fastapi import APIRouter, Header, HTTPException, Query

from .models import (
    ActivityEnvelope,
    FormulaJobsEnvelope,
    PaperTelemetryEnvelope,
    PostmortemDetailEnvelope,
    PostmortemsEnvelope,
)


def create_management_read_models_router(
    *,
    get_read_store: Callable,
    extract_identity: Callable,
    require_read_role: Callable,
    snapshot_meta: Callable,
    utc_now: Callable,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/bff/management/formula-jobs",
        response_model=FormulaJobsEnvelope,
    )
    async def bff_management_formula_jobs(
        status: Optional[str] = Query(default=None),
        formula_id: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Real Formula execution/evaluation jobs read model."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = get_read_store()
        raw_res = store.get_formula_jobs_read_model(status=status, formula_id=formula_id)

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Formula job executor read store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Formula job read store is readable but currently empty."

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "formula_jobs": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Formula jobs read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-formula-jobs",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    @router.get(
        "/bff/management/activity",
        response_model=ActivityEnvelope,
    )
    async def bff_management_activity(
        event_type: Optional[str] = Query(default=None),
        actor_id: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Consolidated Management system activity read model."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = get_read_store()
        raw_res = store.get_activity_read_model(event_type=event_type, actor_id=actor_id)

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Activity audit event store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Activity read store is readable but currently empty."

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "activity": surface,
                **(raw_res.get("surfaces") or {}),
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Activity read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-activity",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    @router.get(
        "/bff/management/paper-telemetry",
        response_model=PaperTelemetryEnvelope,
    )
    async def bff_management_paper_telemetry(
        strategy_id: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Real strategy paper execution telemetry & series read model."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = get_read_store()
        raw_res = store.get_paper_telemetry_read_model(strategy_id=strategy_id, persona_id=persona_id)

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Paper execution telemetry store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Paper telemetry store is readable but currently empty."

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "paper_telemetry": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Paper telemetry read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-paper-telemetry",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    @router.get(
        "/bff/management/postmortems",
        response_model=PostmortemsEnvelope,
    )
    async def bff_management_postmortems(
        severity: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Postmortem incident analysis list read model."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = get_read_store()
        raw_res = store.get_postmortems_read_model(severity=severity, status=status)

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Postmortem analysis store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Postmortem analysis store is readable but currently empty."

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "postmortems": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Postmortems read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-postmortems",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    @router.get(
        "/bff/management/postmortems/{postmortem_id}",
        response_model=PostmortemDetailEnvelope,
    )
    async def bff_management_postmortem_detail(
        postmortem_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Postmortem detail read model."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = get_read_store()
        raw_res = store.get_postmortem_detail_read_model(postmortem_id=postmortem_id)

        source: str = str(raw_res.get("source") or "missing")
        item: Optional[Dict[str, Any]] = raw_res.get("item")

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        elif not item:
            raise HTTPException(status_code=404, detail=f"Postmortem '{postmortem_id}' not found")
        else:
            surface_state = "ok"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Postmortem analysis store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "postmortem_detail": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Postmortem detail read model is currently unavailable.",
            }

        return {
            "data": item,
            "meta": meta,
        }

    return router

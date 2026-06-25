from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header


def create_datasources_router(
    *,
    get_read_store: Callable,
    extract_identity: Callable,
    require_read_role: Callable,
    snapshot_meta: Callable,
    utc_now: Callable,
) -> APIRouter:
    """Factory for the GET /bff/management/data-sources route.

    Dependencies are injected so tests can replace read_store without circular
    imports (main.py is the module that holds the live singletons).
    """
    router = APIRouter()

    @router.get("/bff/management/data-sources")
    async def bff_management_data_sources(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF BFFGAP-DATASOURCES: Data-source registry with ingestion health.

        Returns the canonical list envelope (data / items / page_info / meta).
        When the source-ingest service is unreachable or unconfigured the
        response carries a degraded status envelope instead of a bare [].
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = get_read_store()
        registry = store.get_source_connector_registry()

        source: str = str(registry.get("source") or "missing")
        items: List[Dict[str, Any]] = list(registry.get("connectors") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "data_sources": surface_state,
            },
        }

        if source in ("missing", "unavailable"):
            return {
                "data": {
                    "id": "management-data-sources",
                    "items": [],
                    "status": "unavailable",
                    "source": source,
                },
                "items": [],
                "page_info": {
                    "next_page_token": None,
                    "total": 0,
                    "page_size": 0,
                },
                "meta": meta,
            }

        data: Dict[str, Any] = {
            "id": "management-data-sources",
            "items": items,
            "status": surface_state,
            "source": source,
        }
        if registry.get("policy_registry") is not None:
            data["policy_registry"] = registry["policy_registry"]
        if registry.get("financial_data_source_catalog") is not None:
            data["financial_data_source_catalog"] = registry["financial_data_source_catalog"]
        if registry.get("active_universe_policy") is not None:
            data["active_universe_policy"] = registry["active_universe_policy"]
        provider_examples = list(registry.get("provider_examples") or [])
        if provider_examples:
            data["provider_examples"] = provider_examples

        return {
            "data": data,
            "items": items,
            "page_info": {
                "next_page_token": None,
                "total": len(items),
                "page_size": len(items),
            },
            "meta": meta,
        }

    return router

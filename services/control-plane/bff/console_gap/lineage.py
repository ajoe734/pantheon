from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, Query

from .contracts import LineageEnvelope


def create_lineage_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable] = None,
    extract_identity: Callable,
    require_read_role: Callable,
    snapshot_meta: Callable,
    utc_now: Callable,
) -> APIRouter:
    """Factory for the GET /bff/lineage route.

    Dependencies are injected so tests can replace read_store without circular
    imports (main.py holds the live singletons).
    """
    router = APIRouter()

    @router.get(
        "/bff/lineage",
        response_model=LineageEnvelope,
    )
    def bff_lineage(
        root_id: Optional[str] = Query(default=None),
        root_type: Optional[str] = Query(default=None),
        depth: int = Query(default=3, ge=1, le=20),
        artifact_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF BFFGAP-LINEAGE: Data/artifact lineage graph (nodes/edges).

        Returns the canonical list envelope (data / items / page_info / meta).
        Reads the registry lineage owner. When the owner is unconfigured or
        unreachable the response is status "unavailable"; a reachable owner with
        no lineage is "ok" with zero nodes.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        store = read_surface if read_surface is not None else (get_read_store() if get_read_store is not None else None)
        if store is None:
            raise RuntimeError("Required read_surface is absent; failing startup closed.")

        effective_root_id = root_id or artifact_id
        edges: List[Dict[str, Any]] = store.get_lineage_graph(
            root_type=root_type,
            root_id=effective_root_id,
            depth=depth,
        )
        nodes: List[Dict[str, Any]] = store.get_lineage_graph_nodes(edges)
        edges = [{k: v for k, v in e.items() if k not in ("from_node", "to_node")} for e in edges]

        source: str = str(store.dataset_source("lineage_edges") or "missing")

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            # A real empty graph from a reachable owner is ok with zero nodes.
            surface_state = "ok"
        has_more = bool(getattr(store, "lineage_has_more", lambda: False)()) and source not in ("missing", "unavailable")

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "lineage": {
                    "status": surface_state,
                    "source": source,
                },
            },
        }

        if source in ("missing", "unavailable"):
            return {
                "data": {
                    "id": "lineage",
                    "nodes": [],
                    "edges": [],
                    "status": "unavailable",
                    "source": source,
                },
                "items": [],
                "page_info": {
                    "next_page_token": None,
                    "total": 0,
                    "page_size": 0,
                    "returned": 0,
                    "has_more": False,
                },
                "meta": meta,
            }

        return {
            "data": {
                "id": "lineage",
                "nodes": nodes,
                "edges": edges,
                "status": surface_state,
                "source": source,
            },
            "items": edges,
            "page_info": {
                "next_page_token": None,
                "total": len(edges),
                "page_size": len(edges),
                "returned": len(edges),
                "has_more": has_more,
            },
            "meta": meta,
        }

    return router

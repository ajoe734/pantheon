from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, Query

from ..core import display_names, owner_reads
from ..core.display_names import DisplayNameResolver
from .contracts import LineageEnvelope


def _strategy_labels_configured(store: Any) -> bool:
    """True when ``store.get_strategy_labels`` is backed by a configured or injected source."""
    if store is None or not hasattr(store, "get_strategy_labels"):
        return False
    if not hasattr(store, "research_knowledge_source"):
        return True
    if "get_strategy_labels" in getattr(store, "__dict__", {}):
        return True
    rks = getattr(store, "research_knowledge_source", None)
    if getattr(rks, "_has_injected_strategy_specs", False):
        return True
    return bool(os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL"))


def _lineage_display_name_resolver(store: Any) -> DisplayNameResolver:
    """Resolver for GET /bff/lineage bound to the lineage route's read surface."""
    if display_names._source is not None:
        return DisplayNameResolver(store)

    def _load_personas() -> Any:
        rows = owner_reads.shared_persona_directory_list(store, store.list_personas)
        p_port = getattr(getattr(store, "persona_capital_runtime", None), "persona", None)
        if p_port is not None and callable(getattr(p_port, "get_surface_status", None)):
            if p_port.get_surface_status().get("source") == "unavailable":
                raise RuntimeError("persona_store_unavailable")
        return rows

    persona_loader = _load_personas if store is not None and hasattr(store, "list_personas") else None
    strategy_loader: Optional[Callable[[], Any]] = (
        (
            lambda: owner_reads.cached_owner_call(
                ("display_names_strategy_labels", id(store), owner_reads.selected_tenant.get()),
                store.get_strategy_labels,
            )
        )
        if _strategy_labels_configured(store)
        else (lambda: {})
    )
    return DisplayNameResolver(
        store,
        persona_loader=persona_loader,
        strategy_loader=strategy_loader,
    )


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
        auth_tok = (
            owner_reads.authorization.set(authorization)
            if authorization and not owner_reads.authorization.get()
            else None
        )
        try:
            with owner_reads.request_read_scope():
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
                    names_surface: Dict[str, Any] = {
                        "status": "unavailable",
                        "source": "display_name_resolver",
                    }
                else:
                    resolver = _lineage_display_name_resolver(store)
                    nodes = resolver.annotate_lineage_nodes(nodes, edges)
                    names_surface = resolver.surface()
                    # A real empty graph from a reachable owner is ok with zero nodes;
                    # a failed display-name resolver degrades the surface while leaving the graph readable.
                    surface_state = "degraded" if resolver.failed else "ok"
        finally:
            if auth_tok is not None:
                owner_reads.authorization.reset(auth_tok)

        has_more = bool(getattr(store, "lineage_has_more", lambda: False)()) and source not in ("missing", "unavailable")

        lineage_surface: Dict[str, Any] = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "degraded" and names_surface.get("reason"):
            lineage_surface["reason"] = names_surface["reason"]

        meta: Dict[str, Any] = {
            **snapshot_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "lineage": lineage_surface,
                "display_names": names_surface,
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

"""Persona domain subrouters and route context."""
from .common import PersonaRouteContext, make_context_dependency
from .collection import build_collection_router
from .detail import build_detail_router
from .lifecycle import build_lifecycle_router
from .provisioning import build_provisioning_router
from .ranking import build_ranking_router

__all__ = [
    "PersonaRouteContext",
    "make_context_dependency",
    "build_collection_router",
    "build_detail_router",
    "build_lifecycle_router",
    "build_provisioning_router",
    "build_ranking_router",
]

"""Strategy subrouters package."""
from .collection import build_collection_router
from .common import StrategyRouteContext
from .detail import build_detail_router
from .seeds import build_seeds_router

__all__ = [
    "StrategyRouteContext",
    "build_collection_router",
    "build_detail_router",
    "build_seeds_router",
]

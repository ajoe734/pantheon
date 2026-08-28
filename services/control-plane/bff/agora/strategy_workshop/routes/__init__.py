"""Route-group modules for the strategy-workshop router.

Each module exposes one build_*_router(...) factory that registers a
disjoint group of the /bff/agora/workshops/* contracts on its own
APIRouter. strategy_workshop/router.py composes them; it does not
register routes itself (ACG-06-004).
"""

"""Governed Agora proposal lifecycle with lazy router import."""

__all__ = ["create_governance_router"]


def __getattr__(name: str):
    if name == "create_governance_router":
        from .router import create_governance_router

        return create_governance_router
    raise AttributeError(name)

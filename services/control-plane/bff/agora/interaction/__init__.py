"""Contextual Persona interaction command surface with lazy router import."""

__all__ = ["create_interaction_router"]


def __getattr__(name: str):
    if name == "create_interaction_router":
        from .router import create_interaction_router

        return create_interaction_router
    raise AttributeError(name)

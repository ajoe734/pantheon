"""Contextual Persona interaction command surface with lazy router import."""

__all__ = ["create_interaction_router", "AgoraInteractionWorker", "InteractionLifecycleStore"]


def __getattr__(name: str):
    if name == "create_interaction_router":
        from .router import create_interaction_router

        return create_interaction_router
    if name == "AgoraInteractionWorker":
        from .worker import AgoraInteractionWorker

        return AgoraInteractionWorker
    if name == "InteractionLifecycleStore":
        from .store import InteractionLifecycleStore

        return InteractionLifecycleStore
    raise AttributeError(name)

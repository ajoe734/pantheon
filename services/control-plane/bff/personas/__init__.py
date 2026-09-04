"""Persona Management canonical domain package.

Part of OPGAP-BE-PERSONA-ROUTER-V2-20260830.
"""
from __future__ import annotations

from .service import PersonaService
from .router import create_personas_router

__all__ = ["PersonaService", "create_personas_router"]


def __getattr__(name: str):
    if name == "router":
        from .router import router
        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

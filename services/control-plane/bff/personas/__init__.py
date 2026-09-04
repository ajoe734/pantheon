"""Persona Management canonical domain package.

Part of OPGAP-BE-PERSONA-ROUTER-V2-20260830.
"""
from __future__ import annotations

from .service import PersonaService
from .router import create_personas_router, router

__all__ = ["PersonaService", "create_personas_router", "router"]

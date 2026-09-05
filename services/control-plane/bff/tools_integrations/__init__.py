"""Tools & Integrations canonical domain package.

Part of OPGAP-BE-TOOLS-INTEGRATIONS-V2-20260830.
"""
from __future__ import annotations

from .service import IntegrationsService
from .router import (
    create_integrations_router,
    create_tools_integrations_router,
    router,
)

__all__ = [
    "IntegrationsService",
    "create_integrations_router",
    "create_tools_integrations_router",
    "router",
]

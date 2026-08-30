"""BFF Incidents domain package.

Provides canonical Incident, Risk Alert, Kill Switch, and Audit routes.
"""
from __future__ import annotations

from .router import create_incident_router, create_incidents_router
from .service import IncidentService

__all__ = [
    "create_incident_router",
    "create_incidents_router",
    "IncidentService",
]

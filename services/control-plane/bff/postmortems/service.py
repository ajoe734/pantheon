"""Postmortem domain read service.

The service keeps the postmortem list/detail projection independent from the
FastAPI router.  Persistence remains owned by the injected BFF read store; this
slice only exposes that canonical store through the domain boundary and joins
the linked incident for detail responses.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class PostmortemReadStore(Protocol):
    """Narrow read-store contract required by the postmortem domain."""

    def list_postmortems(self, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return canonical postmortem records for the requested time range."""

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Return one canonical postmortem record by report id."""

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """Return the incident linked to a postmortem, when available."""


class PostmortemService:
    """Read and project postmortem records from their canonical store."""

    def __init__(self, read_store: PostmortemReadStore):
        self.read_store = read_store

    def list_postmortems(self, *, time_range: Optional[str] = None) -> List[Dict[str, Any]]:
        """List postmortems without inventing fallback or fixture records."""

        return list(self.read_store.list_postmortems(time_range=time_range) or [])

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Return postmortem detail enriched with its linked incident."""

        postmortem = self.read_store.get_postmortem(report_id)
        if not postmortem:
            return None

        payload = dict(postmortem)
        incident_id = postmortem.get("incident_id")
        incident = self.read_store.get_incident(incident_id) if incident_id else None
        if incident:
            payload["linked_incident"] = incident
        return payload

"""Postmortem domain read service.

The service keeps the postmortem list/detail projection independent from the
FastAPI router.  Persistence remains owned by the injected BFF read store; this
slice only exposes that canonical store through the domain boundary and joins
the linked incident for detail responses.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from services.control_plane.bff.ports.lifecycle_telemetry_governance import DomainIncidentPort


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

    def get_surface_status(self) -> Dict[str, Any]:
        """Return the read-surface status and source for postmortems."""
        store = self.read_store
        source: Optional[str] = "typed_store"
        if store is not None and hasattr(store, "dataset_source") and callable(store.dataset_source):
            try:
                source = store.dataset_source("postmortems")
            except TypeError:
                source = store.dataset_source()

        inc_port = (
            getattr(getattr(store, "lifecycle_telemetry_governance", None), "incidents", None)
            or getattr(store, "incident_port", None)
            or getattr(store, "incidents", None)
            or (store if isinstance(store, DomainIncidentPort) else None)
        )
        if getattr(inc_port, "_last_postmortems_error", False):
            source = "unavailable"
        elif source in ("typed_store", None) and inc_port is not None and hasattr(inc_port, "dataset_source"):
            try:
                source = inc_port.dataset_source("postmortems")
            except TypeError:
                source = inc_port.dataset_source()

        if source in ("missing", "unavailable", None):
            return {
                "status": "unavailable",
                "source": source or "unavailable",
            }
        return {
            "status": "ok",
            "source": str(source),
        }

    def list_postmortems_page(
        self,
        *,
        time_range: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        page_size: Optional[int] = 50,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List a bounded page of postmortems from the canonical owner."""
        if hasattr(self.read_store, "list_postmortems_page"):
            return getattr(self.read_store, "list_postmortems_page")(
                time_range=time_range,
                status=status,
                q=q,
                page_size=page_size,
                page_token=page_token,
            )
        rows = list(self.read_store.list_postmortems(time_range=time_range) or [])
        return DomainIncidentPort._paginate_postmortem_rows(
            rows,
            time_range=time_range,
            status=status,
            q=q,
            page_size=page_size,
            page_token=page_token,
        )

    def list_postmortems(
        self,
        *,
        time_range: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List postmortems without inventing fallback or fixture records."""
        if status is None and q is None and page_size is None and page_token is None:
            return list(self.read_store.list_postmortems(time_range=time_range) or [])
        page = self.list_postmortems_page(
            time_range=time_range,
            status=status,
            q=q,
            page_size=page_size,
            page_token=page_token,
        )
        return list(page.get("items") or [])

    def get_postmortem(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Return postmortem detail enriched with its linked incident."""
        clean_id = str(report_id or "").strip()
        postmortem = self.read_store.get_postmortem(clean_id)
        if not postmortem:
            return None

        payload = dict(postmortem)
        payload["postmortem_id"] = clean_id
        if "id" in payload:
            payload["id"] = clean_id
        if "report_id" in payload:
            payload["report_id"] = clean_id
        incident_id = payload.get("incident_id")
        incident = self.read_store.get_incident(incident_id) if incident_id else None
        if incident:
            payload["linked_incident"] = incident
        return payload

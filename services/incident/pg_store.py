from __future__ import annotations

import os
from pathlib import Path

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

from .incident import IncidentCase, IncidentStore, Postmortem


class PostgresIncidentStore(IncidentStore):
    """Postgres owner store for IncidentCase and Postmortem records."""

    def __init__(
        self,
        dsn: str,
        incident_table: str = "incident.incident_cases",
        postmortem_table: str = "incident.postmortems",
        bootstrap: bool = True,
    ) -> None:
        self._incident_records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=incident_table,
            owner_service="incident-svc",
            bootstrap=bootstrap,
        )
        self._postmortem_records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=postmortem_table,
            owner_service="postmortem-svc",
            bootstrap=bootstrap,
        )
        super().__init__(path=None)
        self._refresh_from_disk()

    def _refresh_from_disk(self) -> None:
        self._incidents.clear()
        self._postmortems.clear()
        for record in self._incident_records.list_all():
            incident = IncidentCase.from_dict(record)
            self._incidents[incident.incident_id] = incident
        for record in self._postmortem_records.list_all():
            postmortem = Postmortem.from_dict(record)
            self._postmortems[postmortem.postmortem_id] = postmortem

    def _save(self) -> None:
        for incident in self._incidents.values():
            self._incident_records.put(incident.incident_id, incident.to_dict())
        for postmortem in self._postmortems.values():
            self._postmortem_records.put(postmortem.postmortem_id, postmortem.to_dict())


def build_incident_store(path: Path) -> IncidentStore | PostgresIncidentStore:
    backend = (os.getenv("INCIDENT_STORE_BACKEND") or os.getenv("POSTMORTEM_STORE_BACKEND", "json")).strip().lower()
    if backend in ("", "json"):
        return IncidentStore(path=path)
    if backend != "postgres":
        raise ValueError("INCIDENT_STORE_BACKEND must be json or postgres")
    dsn = os.getenv("INCIDENT_STORE_DSN") or os.getenv("POSTMORTEM_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("INCIDENT_STORE_DSN or DATABASE_URL is required for Postgres incident store")
    bootstrap = os.getenv("INCIDENT_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return PostgresIncidentStore(
        dsn=dsn,
        incident_table=os.getenv("INCIDENT_STORE_TABLE", "incident.incident_cases"),
        postmortem_table=os.getenv("POSTMORTEM_STORE_TABLE", "incident.postmortems"),
        bootstrap=bootstrap,
    )

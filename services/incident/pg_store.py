from __future__ import annotations

import os
from pathlib import Path

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

from .incident import IncidentCase, IncidentError, IncidentStore, Postmortem


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
        snapshot = self._active_write_snapshot
        if snapshot is None:
            raise RuntimeError("Postgres incident writes require a guarded domain mutation")
        incidents_before, postmortems_before = snapshot
        removed_incidents = set(incidents_before) - set(self._incidents)
        removed_postmortems = set(postmortems_before) - set(self._postmortems)
        if removed_incidents or removed_postmortems:
            raise IncidentError("Postgres incident store does not support aggregate deletion")

        changed_incidents = [
            incident_id
            for incident_id, incident in self._incidents.items()
            if incidents_before.get(incident_id) != incident
        ]
        changed_postmortems = [
            postmortem_id
            for postmortem_id, postmortem in self._postmortems.items()
            if postmortems_before.get(postmortem_id) != postmortem
        ]
        changed_count = len(changed_incidents) + len(changed_postmortems)
        if changed_count == 0:
            return
        if changed_count != 1:
            raise IncidentError(
                "Postgres incident mutations must persist exactly one changed aggregate"
            )

        if changed_incidents:
            record_id = changed_incidents[0]
            previous = incidents_before.get(record_id)
            desired = self._incidents[record_id]
            replaced, _ = self._incident_records.compare_and_set(
                record_id,
                previous.to_dict() if previous is not None else None,
                desired.to_dict(),
            )
            aggregate_name = "IncidentCase"
        else:
            record_id = changed_postmortems[0]
            previous = postmortems_before.get(record_id)
            desired = self._postmortems[record_id]
            replaced, _ = self._postmortem_records.compare_and_set(
                record_id,
                previous.to_dict() if previous is not None else None,
                desired.to_dict(),
            )
            aggregate_name = "Postmortem"

        if not replaced:
            self._refresh_from_disk()
            raise IncidentError(
                f"{aggregate_name} changed concurrently before durable write: {record_id}"
            )


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

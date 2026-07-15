from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

from .incident import (
    IncidentCase,
    IncidentConcurrencyError,
    IncidentError,
    IncidentStore,
    Postmortem,
)


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

    def _save(
        self,
        *,
        aggregate_type: str,
        record_id: str,
        expected_snapshot: Optional[Mapping[str, Any]],
        consistency_checks: tuple[tuple[str, str, Mapping[str, Any]], ...] = (),
    ) -> None:
        """Persist the explicitly owned aggregate in one guarded transaction.

        The target row is never inferred by diffing the process-wide cache:
        another service instance may legitimately change unrelated rows while
        this mutation is running.  Parent snapshots are selected ``FOR SHARE``
        in the same transaction as the target CAS, preventing a Postmortem
        publication from committing against a concurrently changed Incident.
        """

        owner, desired = self._owner_and_payload(aggregate_type, record_id)
        expected = dict(expected_snapshot) if expected_snapshot is not None else None
        encoded_desired = json.dumps(desired, ensure_ascii=True, sort_keys=True)

        with owner._connect() as conn:
            for check_type, check_id, check_snapshot in consistency_checks:
                check_owner, _ = self._owner_and_payload(check_type, check_id)
                if check_owner.dsn != owner.dsn:
                    raise RuntimeError("Postgres consistency checks require one database")
                cursor = conn.execute(
                    f"""
                    SELECT payload FROM {check_owner.table}
                    WHERE record_id = %s AND payload = %s::jsonb
                    FOR SHARE
                    """,
                    (
                        check_id,
                        json.dumps(dict(check_snapshot), ensure_ascii=True, sort_keys=True),
                    ),
                )
                if check_owner._fetch_one(cursor) is None:
                    raise IncidentConcurrencyError(
                        f"{self._aggregate_name(check_type)} changed concurrently "
                        f"before durable write: {check_id}"
                    )

            # A Postmortem is the canonical one-to-one incident result.  The
            # table lock makes the JSONB uniqueness probe and insert atomic
            # even when two service instances choose different record IDs.
            if aggregate_type == "postmortem" and expected is None:
                conn.execute(f"LOCK TABLE {owner.table} IN SHARE ROW EXCLUSIVE MODE")
                duplicate_cursor = conn.execute(
                    f"""
                    SELECT payload FROM {owner.table}
                    WHERE payload ->> 'incident_id' = %s
                    ORDER BY updated_at ASC
                    LIMIT 1
                    """,
                    (str(desired.get("incident_id") or ""),),
                )
                duplicate_row = owner._fetch_one(duplicate_cursor)
                if duplicate_row is not None:
                    payload = duplicate_row[0] if isinstance(duplicate_row, tuple) else duplicate_row.get("payload")
                    canonical = owner._decode_payload(payload) or {}
                    raise IncidentConcurrencyError(
                        "Postmortem already exists for IncidentCase "
                        f"{desired.get('incident_id')}: {canonical.get('postmortem_id')}"
                    )

            if expected is None:
                cursor = conn.execute(
                    f"""
                    INSERT INTO {owner.table} (record_id, payload, updated_at)
                    VALUES (%s, %s::jsonb, now())
                    ON CONFLICT (record_id) DO NOTHING
                    RETURNING payload
                    """,
                    (record_id, encoded_desired),
                )
            else:
                cursor = conn.execute(
                    f"""
                    UPDATE {owner.table}
                    SET payload = %s::jsonb, updated_at = now()
                    WHERE record_id = %s AND payload = %s::jsonb
                    RETURNING payload
                    """,
                    (
                        encoded_desired,
                        record_id,
                        json.dumps(expected, ensure_ascii=True, sort_keys=True),
                    ),
                )

            if owner._fetch_one(cursor) is None:
                raise IncidentConcurrencyError(
                    f"{self._aggregate_name(aggregate_type)} changed concurrently "
                    f"before durable write: {record_id}"
                )

    def _owner_and_payload(
        self,
        aggregate_type: str,
        record_id: str,
    ) -> tuple[PostgresJsonOwnerStore, dict[str, Any]]:
        if aggregate_type == "incident":
            desired = self._incidents.get(record_id)
            owner = self._incident_records
        elif aggregate_type == "postmortem":
            desired = self._postmortems.get(record_id)
            owner = self._postmortem_records
        else:
            raise ValueError(f"unsupported incident aggregate type: {aggregate_type}")
        if desired is None:
            raise IncidentError(
                f"{self._aggregate_name(aggregate_type)} missing from guarded mutation: {record_id}"
            )
        return owner, desired.to_dict()

    @staticmethod
    def _aggregate_name(aggregate_type: str) -> str:
        return "IncidentCase" if aggregate_type == "incident" else "Postmortem"


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

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
    _serialized_store_read,
    _serialized_store_write,
    build_incident_timeline,
    derive_incident_summary_fields,
    extract_infrastructure_incident_component,
    extract_infrastructure_incident_referenced_ids,
    is_infrastructure_health_incident,
)


class PostgresIncidentStore(IncidentStore):
    """Postgres owner store for IncidentCase and Postmortem records.

    Repeated reports for an open incident update occurrence_count, last_seen_at,
    observed, and the single-report evidence_summary in place on the existing
    incident.incident_cases row without growing row count per report.
    """

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

    # Statuses for which IncidentCase.is_open() is true.  The resident cache is
    # bounded by this set (plus rows touched by the current call), never by the
    # total stored history.
    _OPEN_STATUS_SQL = "payload ->> 'status' IN ('open', 'investigating')"
    # Hard ceiling for any single history read so one request cannot
    # materialise the whole table.
    HISTORY_READ_ROW_CAP = 1000
    _INFRA_BINDING_SQL = "payload ->> 'binding_id' LIKE %s"
    _INFRA_BINDING_PARAM = "infra-subject-%"

    @staticmethod
    def _select_payloads(owner: PostgresJsonOwnerStore, where: str, params: tuple, *, tail: str = "") -> list[dict[str, Any]]:
        return owner.select_where(where, params, tail_sql=tail)

    def _refresh_from_disk(self) -> None:
        """Reload only open incidents; history is read on demand by query."""
        self._incidents.clear()
        self._postmortems.clear()
        for record in self._select_payloads(
            self._incident_records,
            self._OPEN_STATUS_SQL,
            (),
            tail="ORDER BY updated_at ASC",
        ):
            incident = IncidentCase.from_dict(record)
            self._incidents[incident.incident_id] = incident

    def _incident_by_id(self, incident_id: str) -> Optional[IncidentCase]:
        cached = self._incidents.get(incident_id)
        if cached is not None:
            return cached
        if not incident_id:
            return None
        record = self._incident_records.get(incident_id)
        if record is None:
            return None
        incident = IncidentCase.from_dict(record)
        self._incidents[incident.incident_id] = incident
        return incident

    def _postmortem_by_id(self, postmortem_id: str) -> Optional[Postmortem]:
        cached = self._postmortems.get(postmortem_id)
        if cached is not None:
            return cached
        if not postmortem_id:
            return None
        record = self._postmortem_records.get(postmortem_id)
        if record is None:
            return None
        pm = Postmortem.from_dict(record)
        self._postmortems[pm.postmortem_id] = pm
        return pm

    def _postmortem_for_incident_id(self, incident_id: str) -> Optional[Postmortem]:
        for pm in self._postmortems.values():
            if pm.incident_id == incident_id:
                return pm
        rows = self._select_payloads(
            self._postmortem_records,
            "payload ->> 'incident_id' = %s",
            (incident_id,),
            tail="ORDER BY updated_at ASC LIMIT 1",
        )
        if not rows:
            return None
        pm = Postmortem.from_dict(rows[0])
        self._postmortems[pm.postmortem_id] = pm
        return pm

    @_serialized_store_read
    def list_postmortems(self) -> list[Postmortem]:
        rows = self._select_payloads(
            self._postmortem_records,
            "TRUE",
            (),
            tail=f"ORDER BY updated_at DESC LIMIT {self.HISTORY_READ_ROW_CAP}",
        )
        rows.reverse()
        return [Postmortem.from_dict(r) for r in rows]

    @staticmethod
    def _count(owner: PostgresJsonOwnerStore, where: str, params: tuple) -> int:
        return owner.count_where(where, params)

    @_serialized_store_read
    def count_incidents(self) -> int:
        return self._count(self._incident_records, "TRUE", ())

    @_serialized_store_read
    def count_incidents_with_id_prefix(self, prefix: str) -> int:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return self._count(
            self._incident_records, "record_id LIKE %s", (escaped + "%",)
        )

    @_serialized_store_read
    def list_incidents(self) -> list[IncidentCase]:
        """Open incidents plus the newest history, capped at HISTORY_READ_ROW_CAP."""
        merged: dict[str, IncidentCase] = {}
        for inc in self.query_incidents(limit=self.HISTORY_READ_ROW_CAP):
            merged[inc.incident_id] = inc
        for inc in self.query_incidents(open_only=True):
            merged[inc.incident_id] = inc
        return sorted(merged.values(), key=lambda i: (i.created_at, i.incident_id), reverse=True)

    @_serialized_store_read
    def query_incidents(
        self,
        *,
        binding_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        open_only: bool = False,
        limit: Optional[int] = None,
    ) -> list[IncidentCase]:
        clauses: list[str] = []
        params: list[Any] = []
        if binding_id:
            clauses.append("payload ->> 'binding_id' = %s")
            params.append(binding_id)
        if capital_pool_id:
            clauses.append("payload ->> 'capital_pool_id' = %s")
            params.append(capital_pool_id)
        if status:
            clauses.append("payload ->> 'status' = %s")
            params.append(status)
        if severity:
            clauses.append("payload ->> 'severity' = %s")
            params.append(severity)
        if open_only:
            clauses.append(self._OPEN_STATUS_SQL)
        where = " AND ".join(clauses) or "TRUE"
        tail = "ORDER BY payload ->> 'created_at' DESC, record_id DESC"
        if limit is not None:
            tail += f" LIMIT {max(0, int(limit))}"
        return [
            IncidentCase.from_dict(r)
            for r in self._select_payloads(self._incident_records, where, tuple(params), tail=tail)
        ]

    @_serialized_store_read
    def find_incidents_by_binding(self, binding_id: str) -> list[IncidentCase]:
        return self.query_incidents(binding_id=binding_id, limit=self.HISTORY_READ_ROW_CAP)

    @_serialized_store_read
    def find_incidents_by_pool(self, capital_pool_id: str) -> list[IncidentCase]:
        return self.query_incidents(capital_pool_id=capital_pool_id, limit=self.HISTORY_READ_ROW_CAP)

    @_serialized_store_read
    def find_infrastructure_incident_by_id_or_alias(self, incident_id: str) -> Optional[IncidentCase]:
        self._refresh_from_disk()
        target = str(incident_id or "").strip()
        if not target:
            return None
        direct = self._incident_by_id(target)
        if direct is not None and is_infrastructure_health_incident(direct):
            return direct
        # Merged aliases live inside telemetry_event_ids / evidence_summary text.
        escaped = target.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._select_payloads(
            self._incident_records,
            f"{self._INFRA_BINDING_SQL} AND payload::text LIKE %s",
            (self._INFRA_BINDING_PARAM, "%" + escaped + "%"),
            tail="ORDER BY updated_at DESC LIMIT 200",
        )
        candidates = sorted(
            (
                inc
                for inc in (IncidentCase.from_dict(r) for r in rows)
                if is_infrastructure_health_incident(inc)
                and target in extract_infrastructure_incident_referenced_ids(inc)
            ),
            key=lambda item: (0 if item.is_open() else 1, item.created_at, item.incident_id),
        )
        return candidates[0] if candidates else None

    @_serialized_store_read
    def find_latest_infrastructure_incident_for_component(self, component: str) -> Optional[IncidentCase]:
        self._refresh_from_disk()
        rows = self._select_payloads(
            self._incident_records,
            self._INFRA_BINDING_SQL,
            (self._INFRA_BINDING_PARAM,),
            tail=f"ORDER BY updated_at DESC LIMIT {self.HISTORY_READ_ROW_CAP}",
        )
        for record in rows:
            candidate = IncidentCase.from_dict(record)
            if (
                is_infrastructure_health_incident(candidate)
                and extract_infrastructure_incident_component(candidate) == component
            ):
                return candidate
        return None

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

    def _persist_batch_incident_updates(
        self,
        transitions: list[tuple[str, Mapping[str, Any], IncidentCase]],
    ) -> list[IncidentCase]:
        """Persist a batch of guarded IncidentCase updates in one transaction.

        If a specific row changed concurrently (e.g. a finding was just
        re-observed by another instance), only that row is skipped rather than
        aborting the rest of the batch.
        """
        if not transitions:
            return []
        if "_save" in self.__dict__ or type(self)._save is not PostgresIncidentStore._save:
            return super()._persist_batch_incident_updates(transitions)

        owner = self._incident_records
        resolved: list[IncidentCase] = []
        with owner._connect() as conn:
            for record_id, expected_snapshot, updated in transitions:
                encoded_desired = json.dumps(updated.to_dict(), ensure_ascii=True, sort_keys=True)
                encoded_expected = json.dumps(dict(expected_snapshot), ensure_ascii=True, sort_keys=True)
                cursor = conn.execute(
                    f"""
                    UPDATE {owner.table}
                    SET payload = %s::jsonb, updated_at = now()
                    WHERE record_id = %s AND payload = %s::jsonb
                    RETURNING payload
                    """,
                    (encoded_desired, record_id, encoded_expected),
                )
                if owner._fetch_one(cursor) is not None:
                    self._incidents[record_id] = updated
                    resolved.append(updated)
                else:
                    self._incidents[record_id] = IncidentCase.from_dict(dict(expected_snapshot))
        return resolved

    @_serialized_store_write
    def upsert_open_infrastructure_incident(
        self,
        incoming: IncidentCase,
    ) -> tuple[IncidentCase, bool]:
        """Postgres-backed single-open-per-component upsert with CAS retry."""
        for attempt in range(3):
            try:
                return super().upsert_open_infrastructure_incident(incoming)
            except IncidentConcurrencyError:
                if attempt == 2:
                    raise
                self._refresh_from_disk()
        return super().upsert_open_infrastructure_incident(incoming)

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

"""Durable coordination state for paper Persona provisioning.

The BFF is a coordinator, not an owner of CapitalPool, RegistryEntry,
ApprovalDecision, DeploymentPlan, or RuntimeBinding objects.  This ledger only
records the request and the authoritative owner receipts needed to resume that
coordination after a process or replica restart.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Mapping, Optional, Protocol


TERMINAL_STATES = frozenset({"succeeded", "failed", "compensated"})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class ProvisioningConflict(ValueError):
    """The idempotency key or tenant-scoped Persona name has other semantics."""


class ProvisioningLeaseLost(RuntimeError):
    """The caller no longer owns the record's coordination lease."""


@dataclass
class ProvisioningRecord:
    tenant_id: str
    idempotency_key: str
    request_hash: str
    normalized_name: str
    persona_id: str
    request_payload: dict[str, Any]
    state: str = "reserved"
    current_step: str = "reserved"
    references: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    compensation: dict[str, Any] | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    attempt_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProvisioningRecord":
        return cls(
            tenant_id=str(value["tenant_id"]),
            idempotency_key=str(value["idempotency_key"]),
            request_hash=str(value["request_hash"]),
            normalized_name=str(value["normalized_name"]),
            persona_id=str(value["persona_id"]),
            request_payload=dict(value.get("request_payload") or {}),
            state=str(value.get("state") or "reserved"),
            current_step=str(value.get("current_step") or "reserved"),
            references=dict(value.get("references") or {}),
            result=dict(value["result"]) if isinstance(value.get("result"), Mapping) else None,
            error=dict(value["error"]) if isinstance(value.get("error"), Mapping) else None,
            compensation=(
                dict(value["compensation"])
                if isinstance(value.get("compensation"), Mapping)
                else None
            ),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
            lease_owner=(str(value["lease_owner"]) if value.get("lease_owner") else None),
            lease_expires_at=(
                str(value["lease_expires_at"]) if value.get("lease_expires_at") else None
            ),
            attempt_count=int(value.get("attempt_count") or 0),
        )


class PersonaProvisioningStore(Protocol):
    def reserve(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        request_hash: str,
        normalized_name: str,
        persona_id: str,
        request_payload: Mapping[str, Any],
    ) -> tuple[ProvisioningRecord, bool]: ...

    def get(self, tenant_id: str, idempotency_key: str) -> ProvisioningRecord | None: ...

    def get_by_persona(self, tenant_id: str, persona_id: str) -> ProvisioningRecord | None: ...

    def list_by_tenant(self, tenant_id: str) -> list[ProvisioningRecord]: ...

    def list_all(self) -> list[ProvisioningRecord]: ...

    def acquire(
        self,
        tenant_id: str,
        idempotency_key: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> ProvisioningRecord | None: ...

    def checkpoint(
        self,
        record: ProvisioningRecord,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> ProvisioningRecord: ...

    def release(
        self,
        record: ProvisioningRecord,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> ProvisioningRecord: ...


class MemoryProvisioningBackend:
    """Shared durable state for :class:`MemoryPersonaProvisioningStore`.

    Passing the same backend instance to two distinct store objects lets
    tests prove readback survives a process/replica restart (a fresh store
    object reading state it did not itself populate) while every read and
    write still goes through the public store protocol -- exactly what a
    shared Postgres table gives two live BFF replicas.
    """

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], ProvisioningRecord] = {}
        self.names: dict[tuple[str, str], tuple[str, str]] = {}
        self.personas: dict[tuple[str, str], tuple[str, str]] = {}
        self.lock = RLock()


class MemoryPersonaProvisioningStore:
    """Thread-safe test backend with the same lease and conflict semantics."""

    def __init__(self, backend: Optional[MemoryProvisioningBackend] = None) -> None:
        self._backend = backend if backend is not None else MemoryProvisioningBackend()

    @property
    def backend(self) -> MemoryProvisioningBackend:
        """The shared durable-state object; pass it to another store instance
        (``MemoryPersonaProvisioningStore(backend=store.backend)``) to prove
        two identity-distinct stores read the same backing state through the
        public protocol, the way two BFF replicas share one Postgres table."""
        return self._backend

    @staticmethod
    def _copy(record: ProvisioningRecord) -> ProvisioningRecord:
        return ProvisioningRecord.from_mapping(record.to_dict())

    def reserve(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        request_hash: str,
        normalized_name: str,
        persona_id: str,
        request_payload: Mapping[str, Any],
    ) -> tuple[ProvisioningRecord, bool]:
        key = (tenant_id, idempotency_key)
        name_key = (tenant_id, normalized_name)
        with self._backend.lock:
            existing = self._backend.records.get(key)
            if existing is None and name_key in self._backend.names:
                existing = self._backend.records[self._backend.names[name_key]]
            if existing is not None:
                if existing.request_hash != request_hash:
                    scope = "idempotency key" if self._backend.records.get(key) is existing else "Persona name"
                    raise ProvisioningConflict(f"{scope} already has different request semantics")
                return self._copy(existing), False
            record = ProvisioningRecord(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                normalized_name=normalized_name,
                persona_id=persona_id,
                request_payload=dict(request_payload),
            )
            self._backend.records[key] = record
            self._backend.names[name_key] = key
            self._backend.personas[(tenant_id, persona_id)] = key
            return self._copy(record), True

    def get(self, tenant_id: str, idempotency_key: str) -> ProvisioningRecord | None:
        with self._backend.lock:
            record = self._backend.records.get((tenant_id, idempotency_key))
            return self._copy(record) if record is not None else None

    def get_by_persona(self, tenant_id: str, persona_id: str) -> ProvisioningRecord | None:
        with self._backend.lock:
            key = self._backend.personas.get((tenant_id, persona_id))
            record = self._backend.records.get(key) if key is not None else None
            return self._copy(record) if record is not None else None

    def list_by_tenant(self, tenant_id: str) -> list[ProvisioningRecord]:
        with self._backend.lock:
            records = [
                self._copy(record)
                for record in self._backend.records.values()
                if record.tenant_id == tenant_id
            ]
            records.sort(key=lambda r: (r.created_at, r.persona_id))
            return records

    def list_all(self) -> list[ProvisioningRecord]:
        with self._backend.lock:
            records = [self._copy(record) for record in self._backend.records.values()]
            records.sort(key=lambda r: (r.created_at, r.persona_id))
            return records

    def acquire(
        self,
        tenant_id: str,
        idempotency_key: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> ProvisioningRecord | None:
        with self._backend.lock:
            record = self._backend.records.get((tenant_id, idempotency_key))
            if record is None:
                return None
            now = datetime.now(timezone.utc)
            lease_expiry = _parse_time(record.lease_expires_at)
            if (
                record.lease_owner
                and record.lease_owner != lease_owner
                and lease_expiry is not None
                and lease_expiry > now
            ):
                return None
            record.lease_owner = lease_owner
            record.lease_expires_at = (now + timedelta(seconds=lease_seconds)).replace(
                microsecond=0
            ).isoformat().replace("+00:00", "Z")
            record.attempt_count += 1
            if record.state not in TERMINAL_STATES:
                record.state = "provisioning"
            record.updated_at = utc_now()
            return self._copy(record)

    def checkpoint(
        self,
        record: ProvisioningRecord,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> ProvisioningRecord:
        key = (record.tenant_id, record.idempotency_key)
        with self._backend.lock:
            current = self._backend.records.get(key)
            now = datetime.now(timezone.utc)
            lease_expiry = _parse_time(current.lease_expires_at) if current is not None else None
            if (
                current is None
                or current.lease_owner != lease_owner
                or lease_expiry is None
                or lease_expiry <= now
            ):
                raise ProvisioningLeaseLost("Persona provisioning lease is missing or expired")
            saved = self._copy(record)
            saved.lease_owner = current.lease_owner
            saved.lease_expires_at = (now + timedelta(seconds=lease_seconds)).replace(
                microsecond=0
            ).isoformat().replace("+00:00", "Z")
            saved.updated_at = utc_now()
            self._backend.records[key] = saved
            return self._copy(saved)

    def release(
        self,
        record: ProvisioningRecord,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> ProvisioningRecord:
        saved = self.checkpoint(
            record,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
        )
        key = (saved.tenant_id, saved.idempotency_key)
        with self._backend.lock:
            current = self._backend.records[key]
            current.lease_owner = None
            current.lease_expires_at = None
            current.updated_at = utc_now()
            return self._copy(current)


class PostgresPersonaProvisioningStore:
    """Cross-replica durable ledger backed by one Postgres authority table."""

    _RECORD_FIELDS = (
        "tenant_id",
        "idempotency_key",
        "request_hash",
        "normalized_name",
        "persona_id",
        "request_payload",
        "state",
        "current_step",
        "references",
        "result",
        "error",
        "compensation",
        "created_at",
        "updated_at",
        "lease_owner",
        "lease_expires_at",
        "attempt_count",
    )
    _COLUMNS = (
        "tenant_id,idempotency_key,request_hash,normalized_name,persona_id,"
        'request_payload,state,current_step,"references",result,error,compensation,'
        "created_at,updated_at,lease_owner,lease_expires_at,attempt_count"
    )

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "bff",
        connect: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not dsn:
            raise ValueError("Persona provisioning Postgres DSN is required")
        if not schema.replace("_", "").isalnum():
            raise ValueError("invalid Persona provisioning schema")
        if connect is None:
            import psycopg  # type: ignore[import]

            connect = psycopg.connect
        self.dsn = dsn
        self.schema = schema
        self._connect = connect
        self._bootstrap()

    @property
    def table(self) -> str:
        return f"{self.schema}.persona_provisioning"

    def _bootstrap(self) -> None:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    tenant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    request_payload JSONB NOT NULL,
                    state TEXT NOT NULL,
                    current_step TEXT NOT NULL,
                    "references" JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    result JSONB,
                    error JSONB,
                    compensation JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    lease_owner TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, idempotency_key),
                    UNIQUE (tenant_id, normalized_name),
                    UNIQUE (tenant_id, persona_id)
                )"""
            )

    @staticmethod
    def _json(value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value

    @classmethod
    def _record(cls, row: Any) -> ProvisioningRecord:
        values = list(row)
        for index in (5, 8, 9, 10, 11):
            values[index] = cls._json(values[index]) if values[index] is not None else None
        for index in (12, 13, 15):
            if values[index] is not None and hasattr(values[index], "isoformat"):
                values[index] = values[index].isoformat().replace("+00:00", "Z")
        return ProvisioningRecord.from_mapping(
            dict(zip(cls._RECORD_FIELDS, values, strict=True))
        )

    def _select(self, cur: Any, where: str, params: tuple[Any, ...]) -> ProvisioningRecord | None:
        cur.execute(f"SELECT {self._COLUMNS} FROM {self.table} WHERE {where}", params)
        row = cur.fetchone()
        return self._record(row) if row is not None else None

    def reserve(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        request_hash: str,
        normalized_name: str,
        persona_id: str,
        request_payload: Mapping[str, Any],
    ) -> tuple[ProvisioningRecord, bool]:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {self.table}
                    (tenant_id,idempotency_key,request_hash,normalized_name,persona_id,
                     request_payload,state,current_step)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,'reserved','reserved')
                    ON CONFLICT DO NOTHING
                    RETURNING {self._COLUMNS}""",
                (
                    tenant_id,
                    idempotency_key,
                    request_hash,
                    normalized_name,
                    persona_id,
                    json.dumps(dict(request_payload), sort_keys=True),
                ),
            )
            row = cur.fetchone()
            if row is not None:
                return self._record(row), True
            existing = self._select(
                cur,
                "tenant_id=%s AND idempotency_key=%s",
                (tenant_id, idempotency_key),
            )
            conflict_scope = "idempotency key"
            if existing is None:
                existing = self._select(
                    cur,
                    "tenant_id=%s AND normalized_name=%s",
                    (tenant_id, normalized_name),
                )
                conflict_scope = "Persona name"
            if existing is None:
                raise RuntimeError("Persona provisioning reservation disappeared")
            if existing.request_hash != request_hash:
                raise ProvisioningConflict(
                    f"{conflict_scope} already has different request semantics"
                )
            return existing, False

    def get(self, tenant_id: str, idempotency_key: str) -> ProvisioningRecord | None:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            return self._select(
                cur,
                "tenant_id=%s AND idempotency_key=%s",
                (tenant_id, idempotency_key),
            )

    def get_by_persona(self, tenant_id: str, persona_id: str) -> ProvisioningRecord | None:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            return self._select(
                cur,
                "tenant_id=%s AND persona_id=%s",
                (tenant_id, persona_id),
            )

    def list_by_tenant(self, tenant_id: str) -> list[ProvisioningRecord]:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {self._COLUMNS} FROM {self.table} WHERE tenant_id=%s ORDER BY created_at ASC, persona_id ASC",
                (tenant_id,),
            )
            rows = cur.fetchall()
            return [self._record(row) for row in rows]

    def list_all(self) -> list[ProvisioningRecord]:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {self._COLUMNS} FROM {self.table} ORDER BY created_at ASC, persona_id ASC"
            )
            rows = cur.fetchall()
            return [self._record(row) for row in rows]

    def acquire(
        self,
        tenant_id: str,
        idempotency_key: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> ProvisioningRecord | None:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {self.table}
                    SET lease_owner=%s,
                        lease_expires_at=now() + (%s * interval '1 second'),
                        attempt_count=attempt_count+1,
                        state=CASE WHEN state IN ('succeeded','failed','compensated')
                                   THEN state ELSE 'provisioning' END,
                        updated_at=now()
                    WHERE tenant_id=%s AND idempotency_key=%s
                      AND (lease_owner IS NULL OR lease_owner=%s OR lease_expires_at <= now())
                    RETURNING {self._COLUMNS}""",
                (lease_owner, lease_seconds, tenant_id, idempotency_key, lease_owner),
            )
            row = cur.fetchone()
            return self._record(row) if row is not None else None

    def checkpoint(
        self,
        record: ProvisioningRecord,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> ProvisioningRecord:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {self.table}
                    SET state=%s,current_step=%s,"references"=%s::jsonb,result=%s::jsonb,
                        error=%s::jsonb,compensation=%s::jsonb,
                        lease_expires_at=now() + (%s * interval '1 second'),
                        updated_at=now()
                    WHERE tenant_id=%s AND idempotency_key=%s AND lease_owner=%s
                      AND lease_expires_at > now()
                    RETURNING {self._COLUMNS}""",
                (
                    record.state,
                    record.current_step,
                    json.dumps(record.references, sort_keys=True),
                    json.dumps(record.result, sort_keys=True) if record.result is not None else None,
                    json.dumps(record.error, sort_keys=True) if record.error is not None else None,
                    (
                        json.dumps(record.compensation, sort_keys=True)
                        if record.compensation is not None
                        else None
                    ),
                    lease_seconds,
                    record.tenant_id,
                    record.idempotency_key,
                    lease_owner,
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise ProvisioningLeaseLost("Persona provisioning lease is missing or expired")
            return self._record(row)

    def release(
        self,
        record: ProvisioningRecord,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> ProvisioningRecord:
        saved = self.checkpoint(
            record,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
        )
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {self.table}
                    SET lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
                    WHERE tenant_id=%s AND idempotency_key=%s AND lease_owner=%s
                    RETURNING {self._COLUMNS}""",
                (saved.tenant_id, saved.idempotency_key, lease_owner),
            )
            row = cur.fetchone()
            if row is None:
                raise ProvisioningLeaseLost("Persona provisioning lease is missing or expired")
            return self._record(row)


def make_persona_provisioning_store(
    env: Optional[Mapping[str, str]] = None,
) -> PersonaProvisioningStore:
    values = os.environ if env is None else env
    backend = values.get("PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND", "memory").lower()
    environment = str(
        values.get("PANTHEON_ENV")
        or values.get("ENVIRONMENT")
        or ""
    ).strip().lower()
    durable_required = environment in {
        "prod",
        "production",
        "staging",
        "staging-live",
    }
    if backend == "memory":
        if durable_required:
            raise ValueError(
                "PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND=postgres is required "
                f"when PANTHEON_ENV={environment}; refusing restart-unsafe memory state"
            )
        return MemoryPersonaProvisioningStore()
    if backend == "postgres":
        dsn = values.get("PANTHEON_PERSONA_PROVISIONING_STORE_DSN") or values.get("DATABASE_URL")
        if not str(dsn or "").strip():
            raise ValueError(
                "PANTHEON_PERSONA_PROVISIONING_STORE_DSN or DATABASE_URL is required "
                "for the postgres Persona provisioning store"
            )
        return PostgresPersonaProvisioningStore(
            str(dsn),
            schema=values.get("PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA", "bff"),
        )
    raise ValueError("PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND must be memory or postgres")

from __future__ import annotations

import copy
import datetime
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from capital_pool import (  # type: ignore
    CapitalPool,
    CapitalPoolError,
    CapitalPoolStore,
    _validate_status_transition,
    validate_pool,
)
from persona_capital_binding import (  # type: ignore
    PersonaCapitalBinding,
    PersonaCapitalBindingError,
    PersonaCapitalBindingStore,
)

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

try:
    from .allocation_store import AllocationAuthorityStore
except ImportError:
    from allocation_store import AllocationAuthorityStore  # type: ignore

_UTC = datetime.timezone.utc


class PersistentCapitalPoolStore(CapitalPoolStore):
    """Capital-owned store with an atomic canonical pool patch operation."""

    _PATCH_FIELDS = frozenset({"name", "status", "risk_policy_ref", "metadata"})

    def patch(
        self,
        pool_id: str,
        *,
        patch: dict[str, Any],
        updated_at: str,
    ) -> CapitalPool:
        unknown = set(patch) - self._PATCH_FIELDS
        if unknown:
            raise CapitalPoolError(
                f"Unsupported CapitalPool patch fields: {sorted(unknown)}"
            )
        if not patch:
            raise CapitalPoolError("At least one CapitalPool patch field is required")
        with self._lock:
            pool = self.require(pool_id)
            target_status = str(patch.get("status") or pool.status)
            if target_status != pool.status:
                _validate_status_transition(pool.status, target_status)
            payload = {**pool.to_dict(), **patch, "updated_at": updated_at}
            updated = CapitalPool.from_dict(payload)
            errors = validate_pool(updated)
            if errors:
                raise CapitalPoolError(f"Invalid pool patch: {errors}")
            snapshot = dict(self._pools)
            self._pools[pool_id] = updated
            try:
                self._save()
            except Exception:
                self._pools = snapshot
                raise
            return updated


def _ensure_tenant_column(store: Any) -> None:
    if hasattr(store, "_connect"):
        idx = f"idx_{(getattr(store, 'table_name', None) or getattr(store, 'table', '')).replace('\"', '').replace('.', '_')}_tenant_id"
        with store._connect() as conn:
            conn.execute(
                f"ALTER TABLE {store.table} ADD COLUMN IF NOT EXISTS tenant_id TEXT; "
                f'CREATE INDEX IF NOT EXISTS "{idx}" ON {store.table} (tenant_id)'
            )


def _fetch_records(records: Any, key_field: str) -> list[tuple[str, Any, str | None]]:
    if hasattr(records, "_connect"):
        with records._connect() as conn:
            return conn.execute(f"SELECT record_id, payload, tenant_id FROM {records.table}").fetchall()
    return [(r.get(key_field), r, (r.get("metadata") or {}).get("tenant_id") or r.get("tenant_id")) for r in records.list_all()]


def _put_record(records: Any, record_id: str, payload_dict: dict[str, Any], tenant_id: str | None) -> None:
    if not hasattr(records, "_connect"):
        return records.put(record_id, payload_dict)
    with records._connect() as conn:
        conn.execute(
            f"INSERT INTO {records.table} (record_id, payload, updated_at, tenant_id) VALUES (%s, %s::jsonb, now(), %s) "
            "ON CONFLICT (record_id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at, tenant_id=EXCLUDED.tenant_id",
            (record_id, json.dumps(payload_dict, ensure_ascii=True, sort_keys=True), tenant_id),
        )


def _load_entity(cls: Any, row: tuple) -> Any:
    rec, tid = (row[1] if isinstance(row[1], dict) else json.loads(row[1])), row[2]
    ent = cls.from_dict(rec)
    tid = tid or (getattr(ent, "metadata", None) or {}).get("tenant_id") or rec.get("tenant_id")
    if tid:
        object.__setattr__(ent, "tenant_id", tid)
        if getattr(ent, "metadata", None) is not None: ent.metadata["tenant_id"] = tid
    return ent


class PostgresCapitalPoolStore(PersistentCapitalPoolStore):
    """Postgres owner store for CapitalPool records."""

    def __init__(
        self,
        dsn: str,
        table: str = "capital.capital_pools",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="capital-pool-svc",
            bootstrap=bootstrap,
        )
        _ensure_tenant_column(self._records)
        super().__init__(path=None)
        self._refresh_from_postgres()

    def create(self, pool: CapitalPool) -> CapitalPool:
        with self._lock:
            self._refresh_from_postgres()
            return super().create(pool)

    def get(self, pool_id: str) -> Optional[CapitalPool]:
        with self._lock:
            self._refresh_from_postgres()
            return super().get(pool_id)

    def list(self, owner_id: Optional[str] = None, status: Optional[str] = None) -> list[CapitalPool]:
        with self._lock:
            self._refresh_from_postgres()
            return super().list(owner_id=owner_id, status=status)

    def update_status(self, pool_id: str, new_status: str) -> CapitalPool:
        with self._lock:
            self._refresh_from_postgres()
            return super().update_status(pool_id, new_status)

    def patch(
        self,
        pool_id: str,
        *,
        patch: dict[str, Any],
        updated_at: str,
    ) -> CapitalPool:
        with self._lock:
            self._refresh_from_postgres()
            return super().patch(pool_id, patch=patch, updated_at=updated_at)

    def _save(self) -> None:
        for pool in self._pools.values():
            tenant_id = getattr(pool, "tenant_id", None) or (pool.metadata or {}).get("tenant_id")
            _put_record(self._records, pool.pool_id, pool.to_dict(), tenant_id)

    def _refresh_from_postgres(self) -> None:
        with self._lock:
            self._pools = {p.pool_id: p for p in (_load_entity(CapitalPool, r) for r in _fetch_records(self._records, "pool_id"))}


class PostgresPersonaCapitalBindingStore(PersonaCapitalBindingStore):
    """Postgres owner store for PersonaCapitalBinding records."""

    def __init__(
        self,
        dsn: str,
        table: str = "capital.persona_capital_bindings",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="capital-pool-svc",
            bootstrap=bootstrap,
        )
        _ensure_tenant_column(self._records)
        super().__init__(path=None)
        self._refresh_from_postgres()

    def create(self, binding: PersonaCapitalBinding) -> PersonaCapitalBinding:
        with self._lock:
            self._refresh_from_postgres()
            return super().create(binding)

    def get(self, binding_id: str) -> Optional[PersonaCapitalBinding]:
        with self._lock:
            self._refresh_from_postgres()
            return super().get(binding_id)

    def list(
        self,
        persona_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        status: Optional[str] = None,
        role: Optional[str] = None,
    ) -> list[PersonaCapitalBinding]:
        with self._lock:
            self._refresh_from_postgres()
            return super().list(
                persona_id=persona_id,
                capital_pool_id=capital_pool_id,
                status=status,
                role=role,
            )

    def activate(self, binding_id: str, approval_decision_id: str) -> PersonaCapitalBinding:
        with self._lock:
            self._refresh_from_postgres()
            return super().activate(binding_id, approval_decision_id)

    def update_status(self, binding_id: str, new_status: str) -> PersonaCapitalBinding:
        with self._lock:
            self._refresh_from_postgres()
            return super().update_status(binding_id, new_status)

    def _save(self) -> None:
        for binding in self._bindings.values():
            tenant_id = getattr(binding, "tenant_id", None) or (binding.metadata or {}).get("tenant_id")
            _put_record(self._records, binding.binding_id, binding.to_dict(), tenant_id)

    def _refresh_from_postgres(self) -> None:
        with self._lock:
            self._bindings = {}
            for r in _fetch_records(self._records, "binding_id"):
                binding = _load_entity(PersonaCapitalBinding, r)
                self._check_unique_capital_sleeve(binding, exclude_id=None)
                self._bindings[binding.binding_id] = binding


class JsonlCapitalAuditStore:
    def __init__(self, audit_log_path: Path) -> None:
        self.audit_log_path = audit_log_path

    def append_event(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str,
        actor_id: str | None,
        actor_role: str | None,
        detail: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        try:
            from .audit_log import append_audit_event
        except ImportError:
            from audit_log import append_audit_event  # type: ignore

        return append_audit_event(
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            actor_role=actor_role,
            detail=detail,
            audit_log_path=str(self.audit_log_path),
            tenant_id=tenant_id,
        )

    def list_events(
        self,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.audit_log_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.audit_log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not event.get("tenant_id") or (tenant_id and event.get("tenant_id") != tenant_id):
                continue
            if resource_type and event.get("resource_type") != resource_type:
                continue
            if resource_id and event.get("resource_id") != resource_id:
                continue
            events.append(event)
        return events


class PostgresCapitalAuditStore:
    """Postgres owner store for capital audit events."""

    def __init__(
        self,
        dsn: str,
        table: str = "capital.audit_events",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="capital-pool-svc",
            bootstrap=bootstrap,
        )
        _ensure_tenant_column(self._records)

    def append_event(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str,
        actor_id: str | None,
        actor_role: str | None,
        detail: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "detail": detail or {},
            "timestamp": datetime.datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }
        _put_record(self._records, event_id, event, tenant_id)
        return event_id

    def list_events(
        self,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        evs = [(r[1] if isinstance(r[1], dict) else json.loads(r[1]), r[2]) for r in _fetch_records(self._records, "event_id")]
        return [
            e for e, tid in evs
            if (tid or e.get("tenant_id")) and (tenant_id is None or (tid or e.get("tenant_id")) == tenant_id)
            and (not resource_type or e.get("resource_type") == resource_type)
            and (not resource_id or e.get("resource_id") == resource_id)
        ]


class PostgresAllocationAuthorityStore(AllocationAuthorityStore):
    """Postgres JSONB owner store for the atomic allocation aggregate."""

    def __init__(
        self,
        dsn: str,
        table: str = "capital.allocation_authority",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="capital-pool-svc",
            bootstrap=bootstrap,
        )
        if bootstrap:
            _ensure_tenant_column(self._records)
        super().__init__(owner_store=self._records)

    def _persist_locked(self) -> None:
        payload = copy.deepcopy(self._data)
        tenant_id = payload.get("tenant_id") or "default"
        _put_record(self._records, self._POSTGRES_RECORD_ID, payload, tenant_id)


def migrate_capital_tables(dsn: str, default_tenant: str = "default") -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for capital database migrations") from exc
    with psycopg.connect(dsn) as conn:
        for tbl in ("capital.capital_pools", "capital.persona_capital_bindings", "capital.allocation_authority", "capital.audit_events"):
            raw = tbl.replace('"', '').replace('.', '_')
            conn.execute(f'ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS tenant_id TEXT; CREATE INDEX IF NOT EXISTS "idx_{raw}_tenant_id" ON {tbl} (tenant_id)')
            p = "metadata,tenant_id" if "pools" in tbl or "bindings" in tbl else "tenant_id"
            conn.execute(f"UPDATE {tbl} SET tenant_id = COALESCE(payload#>>'{{{p}}}', %s) WHERE tenant_id IS NULL", (default_tenant,))
            conn.execute(f"UPDATE {tbl} SET payload = jsonb_set(payload, '{{{p}}}', to_jsonb(tenant_id), true) WHERE payload#>>'{{{p}}}' IS NULL")
    PostgresAllocationAuthorityStore(dsn=dsn, table="capital.allocation_authority", bootstrap=False).backfill_tenant(default_tenant=default_tenant)



def _capital_backend() -> str:
    return os.getenv("CAPITAL_STORE_BACKEND", "json").strip().lower()


def _bootstrap_env(name: str) -> bool:
    return os.getenv(name, "1").strip().lower() not in ("0", "false", "no")


def _capital_dsn() -> str:
    dsn = os.getenv("CAPITAL_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("CAPITAL_STORE_DSN or DATABASE_URL is required for Postgres capital store")
    return dsn


def build_capital_pool_store(path: Path) -> PersistentCapitalPoolStore | PostgresCapitalPoolStore:
    backend = _capital_backend()
    if backend in ("", "json"):
        return PersistentCapitalPoolStore(path=path)
    if backend != "postgres":
        raise ValueError("CAPITAL_STORE_BACKEND must be json or postgres")
    return PostgresCapitalPoolStore(
        dsn=_capital_dsn(),
        table=os.getenv("CAPITAL_POOL_STORE_TABLE", "capital.capital_pools"),
        bootstrap=_bootstrap_env("CAPITAL_STORE_BOOTSTRAP"),
    )


def build_capital_binding_store(path: Path) -> PersonaCapitalBindingStore | PostgresPersonaCapitalBindingStore:
    backend = _capital_backend()
    if backend in ("", "json"):
        return PersonaCapitalBindingStore(path=path)
    if backend != "postgres":
        raise ValueError("CAPITAL_STORE_BACKEND must be json or postgres")
    return PostgresPersonaCapitalBindingStore(
        dsn=_capital_dsn(),
        table=os.getenv("CAPITAL_BINDING_STORE_TABLE", "capital.persona_capital_bindings"),
        bootstrap=_bootstrap_env("CAPITAL_STORE_BOOTSTRAP"),
    )


def build_capital_audit_store(path: Path) -> JsonlCapitalAuditStore | PostgresCapitalAuditStore:
    backend = os.getenv("CAPITAL_AUDIT_BACKEND") or os.getenv("CAPITAL_STORE_BACKEND", "json")
    backend = backend.strip().lower()
    if backend in ("", "json", "jsonl"):
        return JsonlCapitalAuditStore(path)
    if backend != "postgres":
        raise ValueError("CAPITAL_AUDIT_BACKEND must be jsonl or postgres")
    dsn = os.getenv("CAPITAL_AUDIT_DSN") or os.getenv("CAPITAL_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("CAPITAL_AUDIT_DSN, CAPITAL_STORE_DSN, or DATABASE_URL is required")
    return PostgresCapitalAuditStore(
        dsn=dsn,
        table=os.getenv("CAPITAL_AUDIT_TABLE", "capital.audit_events"),
        bootstrap=_bootstrap_env("CAPITAL_AUDIT_BOOTSTRAP"),
    )


def build_allocation_authority_store(
    path: Path,
) -> AllocationAuthorityStore | PostgresAllocationAuthorityStore:
    backend = _capital_backend()
    if backend in ("", "json"):
        return AllocationAuthorityStore(path=path)
    if backend != "postgres":
        raise ValueError("CAPITAL_STORE_BACKEND must be json or postgres")
    return PostgresAllocationAuthorityStore(
        dsn=_capital_dsn(),
        table=os.getenv("CAPITAL_ALLOCATION_STORE_TABLE", "capital.allocation_authority"),
        bootstrap=_bootstrap_env("CAPITAL_STORE_BOOTSTRAP"),
    )

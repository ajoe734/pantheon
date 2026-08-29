from __future__ import annotations

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
            self._records.put(pool.pool_id, pool.to_dict())

    def _refresh_from_postgres(self) -> None:
        with self._lock:
            self._pools = {}
            for record in self._records.list_all():
                pool = CapitalPool.from_dict(record)
                self._pools[pool.pool_id] = pool


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
            self._records.put(binding.binding_id, binding.to_dict())

    def _refresh_from_postgres(self) -> None:
        with self._lock:
            self._bindings = {}
            for record in self._records.list_all():
                binding = PersonaCapitalBinding.from_dict(record)
                try:
                    self._check_unique_capital_sleeve(binding, exclude_id=None)
                except PersonaCapitalBindingError as exc:
                    raise PersonaCapitalBindingError(
                        "Postgres contains duplicate capital sleeve binding identity"
                    ) from exc
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
        )

    def list_events(
        self,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.audit_log_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.audit_log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
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

    def append_event(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str,
        actor_id: str | None,
        actor_role: str | None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "detail": detail or {},
            "timestamp": datetime.datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }
        self._records.put(event_id, event)
        return event_id

    def list_events(
        self,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> list[dict[str, Any]]:
        events = self._records.list_all()
        if resource_type:
            events = [event for event in events if event.get("resource_type") == resource_type]
        if resource_id:
            events = [event for event in events if event.get("resource_id") == resource_id]
        return events


class PostgresAllocationAuthorityStore(AllocationAuthorityStore):
    """Postgres JSONB owner store for the atomic allocation aggregate."""

    def __init__(
        self,
        dsn: str,
        table: str = "capital.allocation_authority",
        bootstrap: bool = True,
    ) -> None:
        records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="capital-pool-svc",
            bootstrap=bootstrap,
        )
        super().__init__(owner_store=records)


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

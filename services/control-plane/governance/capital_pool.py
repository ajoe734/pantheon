"""
CapitalPool — Capital Pool Platform Object

A CapitalPool is the canonical governance object representing a pool of capital
that personas can be bound to and strategies can be deployed against.

Public API
----------
CapitalPool          — immutable dataclass representing a capital pool
CapitalPoolStore     — in-memory store with optional JSON persistence
validate_pool()      — semantic validation helper

Single-runtime rule
-------------------
By default, a capital pool enforces single_runtime_enforced=True:
at most one live RuntimeBinding may exist for this pool at any time.
Multi-persona use of the same pool must be resolved in upstream
aggregation (judge / committee) before deploying a unified artifact.

Schema
------
The canonical JSON schema lives at:
    services/control-plane/governance/capital_pool.schema.json
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OwnerType(str, Enum):
    ORG = "org"
    FUND = "fund"
    DESK = "desk"
    OPERATOR = "operator"


class PoolStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


# ---------------------------------------------------------------------------
# CapitalPool dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapitalPool:
    """
    Immutable capital pool record.

    Fields
    ------
    pool_id              : immutable unique identifier
    name                 : human-readable display name
    owner_id             : ID of the owning entity (org / fund / desk / operator)
    owner_type           : type of the owning entity
    status               : lifecycle status (active / suspended / archived)
    created_at           : ISO-8601 UTC creation timestamp
    description          : optional free-form description
    currency             : ISO 4217 currency code (default "USD")
    budget               : allocated budget in pool currency units
    risk_policy_ref      : reference to the active risk policy
    single_runtime_enforced : when True, at most one live RuntimeBinding is
                             allowed for this pool at any time (default True)
    updated_at           : ISO-8601 UTC last-update timestamp
    metadata             : arbitrary consumer metadata
    """
    pool_id: str
    name: str
    owner_id: str
    owner_type: str
    status: str
    created_at: str

    description: Optional[str] = None
    currency: str = "USD"
    budget: Optional[float] = None
    risk_policy_ref: Optional[str] = None
    single_runtime_enforced: bool = True
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for enum_cls, val, label in ((OwnerType, self.owner_type, "owner_type"), (PoolStatus, self.status, "status")):
            try:
                enum_cls(val)
            except ValueError:
                raise CapitalPoolError(f"Invalid {label}: {val!r}. Must be one of {[e.value for e in enum_cls]}.")
        if self.budget is not None and self.budget < 0:
            raise CapitalPoolError("budget must be >= 0")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != {}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapitalPool":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class CapitalPoolError(ValueError):
    """Raised when capital pool validation or store operations fail."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_pool(pool: CapitalPool) -> List[str]:
    """Return a list of semantic validation errors. Empty list = valid."""
    errors: List[str] = []
    if not pool.pool_id:
        errors.append("pool_id must not be empty")
    if not pool.name:
        errors.append("name must not be empty")
    if not pool.owner_id:
        errors.append("owner_id must not be empty")
    return errors


def atomic_save_json(path: Path, records: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# CapitalPoolStore
# ---------------------------------------------------------------------------

class CapitalPoolStore:
    """
    In-memory store for CapitalPool records with optional JSON persistence.

    Ownership rule
    --------------
    Write access to pools is restricted to the Capital Pool Plane.
    This store is the canonical single source of truth for pool definitions.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._pools: Dict[str, CapitalPool] = {}
        self._path = path
        self._lock = RLock()
        if path and path.exists():
            self._load(path)

    # ---- CRUD ----

    def create(self, pool: CapitalPool) -> CapitalPool:
        with self._lock:
            if pool.pool_id in self._pools:
                raise CapitalPoolError(f"Pool already exists: {pool.pool_id}")
            errors = validate_pool(pool)
            if errors:
                raise CapitalPoolError(f"Invalid pool: {errors}")
            snapshot = dict(self._pools)
            self._pools[pool.pool_id] = pool
            try:
                self._save()
            except Exception:
                self._pools = snapshot
                raise
            return pool

    def get(self, pool_id: str) -> Optional[CapitalPool]:
        with self._lock:
            return self._pools.get(pool_id)

    def require(self, pool_id: str) -> CapitalPool:
        with self._lock:
            pool = self.get(pool_id)
            if pool is None:
                raise CapitalPoolError(f"Pool not found: {pool_id}")
            return pool

    def list(self, owner_id: Optional[str] = None, status: Optional[str] = None) -> List[CapitalPool]:
        with self._lock:
            pools = list(self._pools.values())
            if owner_id:
                pools = [p for p in pools if p.owner_id == owner_id]
            if status:
                pools = [p for p in pools if p.status == status]
            return pools

    def update_status(self, pool_id: str, new_status: str) -> CapitalPool:
        """
        Transition pool lifecycle status.
        Valid transitions: active <-> suspended, active/suspended -> archived.
        """
        with self._lock:
            pool = self.require(pool_id)
            _validate_status_transition(pool.status, new_status)
            updated = CapitalPool(
                **{**pool.to_dict(), "status": new_status, "updated_at": utc_now()},
            )
            if hasattr(pool, "tenant_id"):  # keep authoritative formal owner through reconstruction
                object.__setattr__(updated, "tenant_id", pool.tenant_id)
            snapshot = dict(self._pools)
            self._pools[pool_id] = updated
            try:
                self._save()
            except Exception:
                self._pools = snapshot
                raise
            return updated

    # ---- Single-runtime query helper ----

    def is_single_runtime_enforced(self, pool_id: str) -> bool:
        """
        Returns True if this pool enforces the single-runtime rule.
        Callers (e.g. runtime-manager) must check this before creating a new
        RuntimeBinding for the pool.
        """
        with self._lock:
            pool = self.require(pool_id)
            return bool(pool.single_runtime_enforced)

    # ---- Persistence ----

    def _save(self) -> None:
        if self._path:
            atomic_save_json(self._path, [p.to_dict() for p in self._pools.values()])

    def _load(self, path: Path) -> None:
        with self._lock:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise CapitalPoolError(f"Expected JSON array in {path}")
            for record in data:
                pool = CapitalPool.from_dict(record)
                self._pools[pool.pool_id] = pool


# ---------------------------------------------------------------------------
# Status transition guard
# ---------------------------------------------------------------------------

_ALLOWED_STATUS_TRANSITIONS: Dict[str, List[str]] = {
    "active": ["suspended", "archived"],
    "suspended": ["active", "archived"],
    "archived": [],
}


def _validate_status_transition(current: str, target: str) -> None:
    allowed = _ALLOWED_STATUS_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise CapitalPoolError(
            f"Invalid status transition: {current!r} -> {target!r}. "
            f"Allowed from {current!r}: {allowed}"
        )

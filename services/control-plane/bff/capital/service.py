"""Capital domain service helpers.

The capital router is deliberately independent of ``bff.main``.  It accepts a
read-store and an optional Capital Allocation Manager write authority at its
composition boundary, so a later composition-root migration can mount the
router without reintroducing a reverse import of the monolith.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence




try:
    from ..pm12.service import (
        _pm12_semantic_json_value,
        _pm12_semantic_values_match,
        _pm12_allocation_line_assertion_hash,
    )
except (ImportError, ValueError):
    from services.control_plane.bff.pm12.service import (
        _pm12_semantic_json_value,
        _pm12_semantic_values_match,
        _pm12_allocation_line_assertion_hash,
    )



class CapitalServiceError(RuntimeError):
    """Base error for an explicit Capital domain boundary failure."""


class CapitalNotFound(CapitalServiceError):
    """The requested capital-owned record was not found."""


class CapitalValidationError(CapitalServiceError):
    """The request does not satisfy the Capital domain contract."""


class CapitalAuthorityUnavailable(CapitalServiceError):
    """A write was requested but no Capital write authority is available."""


def stable_digest(value: Any) -> str:
    """Return a stable digest for allocation and rebalance lineage records."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def first_present(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def capital_pool_id(record: Mapping[str, Any]) -> str:
    return str(first_present(record, "pool_id", "capital_pool_id", "id") or "").strip()


def rebalance_id(record: Mapping[str, Any]) -> str:
    return str(first_present(record, "rebalance_id", "id") or "").strip()


def pool_risk_limits(pool: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize the historical risk-limit spellings into one explicit field."""
    value = first_present(pool, "risk_limits", "risk_limit", "limits", "risk_budget")
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    if value is None:
        return {}
    return {"value": value}


def normalize_pool(pool: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(pool))
    if identifier := capital_pool_id(result):
        for k in ("id", "pool_id", "capital_pool_id"):
            result.setdefault(k, identifier)
    result["risk_limits"] = pool_risk_limits(result)
    return result


def normalize_rebalance(rebalance: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(rebalance))
    if identifier := rebalance_id(result):
        result.setdefault("id", identifier)
        result.setdefault("rebalance_id", identifier)
    if pool_id := str(first_present(result, "capital_pool_id", "pool_id", "target_pool_id") or "").strip():
        result.setdefault("capital_pool_id", pool_id)
    return result


def filter_records(
    records: Iterable[Mapping[str, Any]],
    *,
    status: Optional[str] = None,
    capital_pool_id_value: Optional[str] = None,
    risk_policy_ref: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Apply the common capital filters without assuming a particular store API."""
    status_values = {item.strip().lower() for item in str(status or "").split(",") if item.strip()}
    expected_pool = str(capital_pool_id_value or "").strip()
    expected_policy = str(risk_policy_ref or "").strip()
    filtered: List[Dict[str, Any]] = []
    for raw in records:
        item = deepcopy(dict(raw))
        actual_status = str(item.get("status") or "").strip().lower()
        actual_pool = str(first_present(item, "capital_pool_id", "pool_id", "target_pool_id") or "").strip()
        actual_policy = str(first_present(item, "risk_policy_ref", "risk_policy_id") or "").strip()
        if status_values and actual_status not in status_values:
            continue
        if expected_pool and actual_pool != expected_pool:
            continue
        if expected_policy and actual_policy != expected_policy:
            continue
        filtered.append(item)
    return filtered


def _read_collection(store: Any, method_name: str, **kwargs: Any) -> List[Dict[str, Any]]:
    method = getattr(store, method_name, None)
    if not callable(method):
        return []
    try:
        value = method(**{key: value for key, value in kwargs.items() if value is not None})
    except TypeError:
        value = method()
    return [deepcopy(dict(item)) for item in (value or []) if isinstance(item, Mapping)]


@dataclass
class CapitalService:
    """Store/authority facade shared by all 25 Capital routes."""

    get_read_store: Callable[[], Any]
    get_capital_authority: Optional[Callable[[], Any]] = None
    utc_now: Callable[[], str] = lambda: ""
    _idempotency: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock)

    def _store(self) -> Any:
        store = self.get_read_store()
        if store is None:
            raise CapitalAuthorityUnavailable("Capital read store is unavailable")
        return store

    def list_pools(
        self, *, status: Optional[str] = None, risk_policy_ref: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        pools = filter_records(
            _read_collection(self._store(), "list_capital_pools", status=status, risk_policy_ref=risk_policy_ref),
            status=status,
            risk_policy_ref=risk_policy_ref,
        )
        return sorted((normalize_pool(p) for p in pools), key=capital_pool_id)

    def _get_entity(
        self,
        entity_id: str,
        method: str,
        list_fn: Callable[[], List[Dict[str, Any]]],
        id_fn: Callable[[Mapping[str, Any]], str],
        normalize_fn: Callable[[Mapping[str, Any]], Dict[str, Any]],
        label: str,
    ) -> Dict[str, Any]:
        clean_id = str(entity_id or "").strip()
        if not clean_id:
            raise CapitalNotFound(f"{label} id is required")
        getter = getattr(self._store(), method, None)
        item = getter(clean_id) if callable(getter) else None
        if isinstance(item, Mapping):
            return normalize_fn(item)
        for candidate in list_fn():
            if id_fn(candidate) == clean_id:
                return candidate
        raise CapitalNotFound(f"{label} {clean_id} does not exist")

    def get_pool(self, pool_id: str) -> Dict[str, Any]:
        return self._get_entity(
            pool_id, "get_capital_pool", self.list_pools, capital_pool_id, normalize_pool, "Capital pool"
        )

    def list_rebalances(
        self, *, status: Optional[str] = None, capital_pool_id_value: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        rows = filter_records(
            _read_collection(self._store(), "list_rebalances", status=status, capital_pool_id=capital_pool_id_value),
            status=status,
            capital_pool_id_value=capital_pool_id_value,
        )
        return sorted((normalize_rebalance(r) for r in rows), key=rebalance_id)

    def get_rebalance(self, requested_id: str) -> Dict[str, Any]:
        return self._get_entity(
            requested_id, "get_rebalance", self.list_rebalances, rebalance_id, normalize_rebalance, "Rebalance"
        )

    def allocations(self, *, capital_pool_id_value: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = _read_collection(
            self._store(), "list_capital_allocations", capital_pool_id=capital_pool_id_value
        )
        return filter_records(rows, capital_pool_id_value=capital_pool_id_value)

    @staticmethod
    def _cache_entry(tenant_id: Optional[str], actor_id: str, op: str, key: str, payload: Mapping[str, Any], target_id: Optional[str]) -> Tuple[str, str]:
        return f"{tenant_id or ''}:{actor_id}:{op}:{key}", stable_digest({"payload": payload, "target_id": str(target_id or ""), "tenant_id": str(tenant_id or "")})

    def idempotent(self, *, actor_id: str, key: str, operation: str, payload: Mapping[str, Any], target_id: Optional[str] = None, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not key:
            raise CapitalValidationError("Idempotency-Key is required")
        ck, r_hash = self._cache_entry(tenant_id, actor_id, operation, key, payload, target_id)
        with self._lock:
            saved = self._idempotency.get(ck)
            if saved is None:
                return None
            if saved["request_hash"] != r_hash:
                raise CapitalValidationError("Idempotency key was already used with a different request")
            return deepcopy(saved["response"])

    def remember(self, *, actor_id: str, key: str, operation: str, payload: Mapping[str, Any], response: Mapping[str, Any], target_id: Optional[str] = None, tenant_id: Optional[str] = None) -> None:
        ck, r_hash = self._cache_entry(tenant_id, actor_id, operation, key, payload, target_id)
        with self._lock:
            self._idempotency[ck] = {"request_hash": r_hash, "response": deepcopy(dict(response))}

    def write(self, operation: str, payload: Dict[str, Any], **context: Any) -> Dict[str, Any]:
        """Forward a mutation to the injected Capital owner writer and return its readback."""
        authority = self.get_capital_authority() if self.get_capital_authority else None
        method = getattr(authority, operation, None)
        if not callable(method):
            raise CapitalAuthorityUnavailable(f"Capital owner writer does not expose {operation}")
        return deepcopy(dict(method(payload, **context)))

    def portfolio_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for pool in self.list_pools():
            pool_id = capital_pool_id(pool)
            allocations = self.allocations(capital_pool_id_value=pool_id)
            rows.append({
                "capital_pool_id": pool_id,
                "pool": pool,
                "risk_limits": pool_risk_limits(pool),
                "allocations": allocations,
                "allocation_count": len(allocations),
                "allocation_digest": stable_digest(allocations),
            })
        return rows

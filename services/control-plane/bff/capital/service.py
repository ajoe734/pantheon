"""Capital domain service helpers.

The capital router is deliberately independent of ``bff.main``.  It accepts a
read-store and an optional Capital Allocation Manager write authority at its
composition boundary, so a later composition-root migration can mount the
router without reintroducing a reverse import of the monolith.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


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
    identifier = capital_pool_id(result)
    if identifier:
        result.setdefault("id", identifier)
        result.setdefault("pool_id", identifier)
        result.setdefault("capital_pool_id", identifier)
    result["risk_limits"] = pool_risk_limits(result)
    return result


def normalize_rebalance(rebalance: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(rebalance))
    identifier = rebalance_id(result)
    if identifier:
        result.setdefault("id", identifier)
        result.setdefault("rebalance_id", identifier)
    pool_id = str(first_present(result, "capital_pool_id", "pool_id", "target_pool_id") or "").strip()
    if pool_id:
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


def _call_write(method: Callable[..., Any], payload: Dict[str, Any], context: Dict[str, Any]) -> Any:
    """Call common Capital authority shapes without requiring a monolith adapter.

    The authority is intentionally tried with named envelope forms before a
    positional payload.  A TypeError caused by a signature mismatch is safe to
    retry; other authority failures remain visible to the router.
    """
    attempts = (
        lambda: method(payload=payload, **context),
        lambda: method(body=payload, **context),
        lambda: method(request=payload, **context),
        lambda: method(payload, **context),
        lambda: method(payload),
        lambda: method(**payload),
    )
    signature_error: Optional[TypeError] = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            signature_error = exc
    assert signature_error is not None
    raise signature_error


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

    def _authority(self) -> Any:
        authority = self.get_capital_authority() if self.get_capital_authority else None
        return authority if authority is not None else self._store()

    def list_pools(
        self, *, status: Optional[str] = None, risk_policy_ref: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        pools = _read_collection(
            self._store(), "list_capital_pools", status=status, risk_policy_ref=risk_policy_ref
        )
        pools = filter_records(pools, status=status, risk_policy_ref=risk_policy_ref)
        return sorted((normalize_pool(pool) for pool in pools), key=capital_pool_id)

    def get_pool(self, pool_id: str) -> Dict[str, Any]:
        clean_id = str(pool_id or "").strip()
        if not clean_id:
            raise CapitalNotFound("Capital pool id is required")
        store = self._store()
        getter = getattr(store, "get_capital_pool", None)
        pool = getter(clean_id) if callable(getter) else None
        if isinstance(pool, Mapping):
            return normalize_pool(pool)
        for candidate in self.list_pools():
            if capital_pool_id(candidate) == clean_id:
                return candidate
        raise CapitalNotFound(f"Capital pool {clean_id} does not exist")

    def list_rebalances(
        self, *, status: Optional[str] = None, capital_pool_id_value: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        rows = _read_collection(
            self._store(), "list_rebalances", status=status, capital_pool_id=capital_pool_id_value
        )
        rows = filter_records(rows, status=status, capital_pool_id_value=capital_pool_id_value)
        return sorted((normalize_rebalance(row) for row in rows), key=rebalance_id)

    def get_rebalance(self, requested_id: str) -> Dict[str, Any]:
        clean_id = str(requested_id or "").strip()
        if not clean_id:
            raise CapitalNotFound("Rebalance id is required")
        store = self._store()
        getter = getattr(store, "get_rebalance", None)
        row = getter(clean_id) if callable(getter) else None
        if isinstance(row, Mapping):
            return normalize_rebalance(row)
        for candidate in self.list_rebalances():
            if rebalance_id(candidate) == clean_id:
                return candidate
        raise CapitalNotFound(f"Rebalance {clean_id} does not exist")

    def allocations(self, *, capital_pool_id_value: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = _read_collection(
            self._store(), "list_capital_allocations", capital_pool_id=capital_pool_id_value
        )
        return filter_records(rows, capital_pool_id_value=capital_pool_id_value)

    def idempotent(self, *, actor_id: str, key: str, operation: str, payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        if not key:
            raise CapitalValidationError("Idempotency-Key is required")
        cache_key = f"{actor_id}:{operation}:{key}"
        request_hash = stable_digest(payload)
        with self._lock:
            saved = self._idempotency.get(cache_key)
            if saved is None:
                return None
            if saved["request_hash"] != request_hash:
                raise CapitalValidationError("Idempotency key was already used with a different request")
            return deepcopy(saved["response"])

    def remember(self, *, actor_id: str, key: str, operation: str, payload: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        cache_key = f"{actor_id}:{operation}:{key}"
        with self._lock:
            self._idempotency[cache_key] = {
                "request_hash": stable_digest(payload),
                "response": deepcopy(dict(response)),
            }

    def write(self, operation: str, payload: Dict[str, Any], *, actor_id: str, target_id: Optional[str] = None) -> Dict[str, Any]:
        """Delegate mutation to the Capital owner and preserve its readback shape."""
        authority = self._authority()
        method_names = {
            "create_pool": ("create_capital_pool", "create_pool"),
            "patch_pool": ("patch_capital_pool", "update_capital_pool", "patch_pool"),
            "pool_action": ("capital_pool_action", "apply_capital_pool_action", "pool_action"),
            "create_rebalance": ("create_rebalance",),
            "patch_rebalance": ("patch_rebalance", "update_rebalance"),
            "apply_rebalance": ("apply_rebalance", "apply_rebalance_proposal"),
            "approve_rebalance": ("approve_rebalance", "approve_rebalance_apply"),
            "sign_rebalance": ("sign_rebalance", "sign_rebalance_apply"),
            "rebalance_action": ("rebalance_action", "apply_rebalance_action"),
        }.get(operation, ())
        context = {"actor_id": actor_id, "requested_at": self.utc_now()}
        if target_id:
            context["target_id"] = target_id
            if operation in {"patch_pool", "pool_action"}:
                context["pool_id"] = target_id
            else:
                context["rebalance_id"] = target_id
        for method_name in method_names:
            method = getattr(authority, method_name, None)
            if callable(method):
                result = _call_write(method, payload, context)
                return deepcopy(dict(result)) if isinstance(result, Mapping) else {"result": result}
        raise CapitalAuthorityUnavailable(
            f"Capital authority does not expose a supported {operation} mutation method"
        )

    def evaluate_allocation_policy(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        policy_version = str(payload.get("allocation_policy_version") or payload.get("policy_version") or "").strip()
        if not policy_version:
            raise CapitalValidationError("allocation_policy_version is required")
        raw_lines = payload.get("lines")
        if raw_lines is None:
            raw_lines = self.allocations(capital_pool_id_value=payload.get("capital_pool_id"))
        if not isinstance(raw_lines, Sequence) or isinstance(raw_lines, (str, bytes)):
            raise CapitalValidationError("lines must be an array")
        lines: List[Dict[str, Any]] = []
        for index, raw in enumerate(raw_lines):
            if not isinstance(raw, Mapping):
                raise CapitalValidationError(f"lines[{index}] must be an object")
            line = deepcopy(dict(raw))
            line["allocation_line_digest"] = stable_digest({"index": index, "line": line})
            lines.append(line)
        evaluation_id = str(payload.get("allocation_evaluation_id") or "").strip()
        if not evaluation_id:
            evaluation_id = f"allocation-eval-{stable_digest({'policy': policy_version, 'lines': lines})[:16]}"
        return {
            "allocation_evaluation_id": evaluation_id,
            "allocation_policy_version": policy_version,
            "capital_pool_id": payload.get("capital_pool_id"),
            "lines": lines,
            "allocation_digest": stable_digest(lines),
            "evaluated_at": self.utc_now(),
        }

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

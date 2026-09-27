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
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, runtime_checkable
import uuid


def run_management_read(*args: Any, **kwargs: Any) -> Any:
    try:
        from ..personas.routes.common import run_management_read as _rmr
    except (ImportError, ValueError):
        from personas.routes.common import run_management_read as _rmr
    return _rmr(*args, **kwargs)



def _pm12_semantic_json_value(value: Any) -> Any:
    """Canonicalize JSON values without treating booleans as numbers."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (int, float, Decimal)):
        try:
            numeric = (
                value
                if isinstance(value, Decimal)
                else Decimal(str(value))
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("allocation line contains an invalid number") from exc
        if not numeric.is_finite():
            raise ValueError("allocation line contains a non-finite number")
        if numeric == 0:
            numeric = Decimal(0)
        return ["number", format(numeric.normalize(), "f")]
    if isinstance(value, list):
        return ["array", [_pm12_semantic_json_value(item) for item in value]]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("allocation line contains a non-string object key")
        return [
            "object",
            [
                [key, _pm12_semantic_json_value(value[key])]
                for key in sorted(value)
            ],
        ]
    raise ValueError(
        f"allocation line contains unsupported JSON value {type(value).__name__}"
    )


def _pm12_semantic_values_match(asserted: Any, authoritative: Any) -> bool:
    """Compare an asserted value against its admitted authoritative value using the
    numeric/bool/order-safe semantic canonical form so benign browser JSON
    round-trips (for example 1.0 -> 1, or object key reordering) do not read as an
    assertion mismatch. Values that cannot be canonicalized stay fail-closed by
    returning False, preserving the strict-by-default posture for malformed input."""
    try:
        return (
            _pm12_semantic_json_value(asserted)
            == _pm12_semantic_json_value(authoritative)
        )
    except ValueError:
        return False


def _pm12_allocation_line_assertion_hash(line: Dict[str, Any]) -> str:
    canonical = _pm12_semantic_json_value(line)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()



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


@runtime_checkable
class CapitalAuthority(Protocol):
    """Protocol defining the mutation methods supported by Capital authority."""

    def create_capital_pool(self, payload: Dict[str, Any], *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def patch_capital_pool(self, payload: Dict[str, Any], pool_id: str, *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def capital_pool_action(self, payload: Dict[str, Any], pool_id: str, *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def create_rebalance(self, payload: Dict[str, Any], *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def patch_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def apply_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def approve_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def sign_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...
    def rebalance_action(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = ..., **kwargs: Any) -> Dict[str, Any]: ...


class DefaultCapitalAuthority:
    """Default production Capital write authority delegating to command executor and adapters."""

    def __init__(
        self,
        command_executor: Any = None,
        capital_adapter: Any = None,
        command_store: Any = None,
    ) -> None:
        self._command_executor = command_executor
        self._capital_adapter = capital_adapter
        self._command_store = command_store

    def _get_executor(self) -> Any:
        if self._command_executor is not None:
            return self._command_executor
        try:
            from .. import command_executor
            return command_executor
        except (ImportError, ValueError):
            try:
                from services.control_plane.bff import command_executor
                return command_executor
            except (ImportError, ValueError):
                return None

    def _get_adapter(self) -> Any:
        if self._capital_adapter is not None:
            return self._capital_adapter
        try:
            from ..command_adapters.capital_adapter import CapitalCommandAdapter
            return CapitalCommandAdapter()
        except (ImportError, ValueError):
            try:
                from services.control_plane.bff.command_adapters.capital_adapter import CapitalCommandAdapter
                return CapitalCommandAdapter()
            except (ImportError, ValueError):
                return None

    def _record_receipt(
        self,
        *,
        command_id: str,
        command_type: str,
        target_type: str,
        target_id: str,
        actor_id: str,
        action_id: str,
        params: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        if self._command_store is None:
            return
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._command_store.submit_terminal_command(
            command_id=command_id,
            command_type=command_type,
            target={"type": target_type, "id": target_id},
            submitted_at=now_iso,
            params=params,
            audit_context={
                "operator_id": actor_id,
                "action_id": action_id,
                "idempotency_key": params.get("idempotency_key"),
            },
            foundation_context={"receipt": result},
            result=result,
        )

    def create_capital_pool(self, payload: Dict[str, Any], *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        executor = self._get_executor()
        body = dict(payload)
        body.setdefault("actor_id", actor_id)
        body.setdefault("actor_role", "operator")
        pool_id = str(body.get("pool_id") or body.get("id") or "").strip()
        if not pool_id:
            pool_id = f"pool-{uuid.uuid4().hex[:8]}"
            body["pool_id"] = pool_id
            body["id"] = pool_id
        cmd_id = str(uuid.uuid4())
        if executor is not None and hasattr(executor, "create_capital_pool"):
            result = executor.create_capital_pool(body)
            self._record_receipt(
                command_id=cmd_id,
                command_type="CapitalPoolAction",
                target_type="CapitalPool",
                target_id=pool_id,
                actor_id=actor_id,
                action_id="create",
                params={**body, "action_id": "create"},
                result=result,
            )
            return result
        adapter = self._get_adapter()
        if adapter is not None:
            result = adapter.execute(
                command_id=cmd_id,
                command_type="CapitalPoolAction",
                params={**body, "action_id": "create"},
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="CapitalPoolAction",
                target_type="CapitalPool",
                target_id=pool_id,
                actor_id=actor_id,
                action_id="create",
                params={**body, "action_id": "create"},
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def patch_capital_pool(self, payload: Dict[str, Any], pool_id: str, *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        adapter = self._get_adapter()
        if adapter is not None:
            cmd_id = str(uuid.uuid4())
            params = {**payload, "pool_id": pool_id, "action_id": "patch", "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="CapitalPoolAction",
                params=params,
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="CapitalPoolAction",
                target_type="CapitalPool",
                target_id=pool_id,
                actor_id=actor_id,
                action_id="patch",
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def capital_pool_action(self, payload: Dict[str, Any], pool_id: str, *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        adapter = self._get_adapter()
        if adapter is not None:
            action_id = str(payload.get("action_id") or "action")
            cmd_id = str(uuid.uuid4())
            params = {**payload, "pool_id": pool_id, "action_id": action_id, "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="CapitalPoolAction",
                params=params,
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="CapitalPoolAction",
                target_type="CapitalPool",
                target_id=pool_id,
                actor_id=actor_id,
                action_id=action_id,
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def create_rebalance(self, payload: Dict[str, Any], *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        executor = self._get_executor()
        body = dict(payload)
        body.setdefault("actor_id", actor_id)
        cmd_id = str(uuid.uuid4())
        if executor is not None and hasattr(executor, "create_capital_rebalance_proposal"):
            result = executor.create_capital_rebalance_proposal(body)
            target_id = str(result.get("rebalance_id") or result.get("id") or "")
            self._record_receipt(
                command_id=cmd_id,
                command_type="RebalanceProposal",
                target_type="Rebalance",
                target_id=target_id,
                actor_id=actor_id,
                action_id="propose",
                params={**body, "action_id": "propose", "actor_id": actor_id},
                result=result,
            )
            return result
        adapter = self._get_adapter()
        if adapter is not None:
            params = {**body, "action_id": "propose", "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="RebalanceProposal",
                params=params,
            )
            target_id = str(result.get("rebalance_id") or result.get("id") or "")
            self._record_receipt(
                command_id=cmd_id,
                command_type="RebalanceProposal",
                target_type="Rebalance",
                target_id=target_id,
                actor_id=actor_id,
                action_id="propose",
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def patch_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        adapter = self._get_adapter()
        if adapter is not None:
            cmd_id = str(uuid.uuid4())
            params = {**payload, "rebalance_id": rebalance_id, "action_id": "patch", "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="PatchRebalance",
                params=params,
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="PatchRebalance",
                target_type="Rebalance",
                target_id=rebalance_id,
                actor_id=actor_id,
                action_id="patch",
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def apply_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        adapter = self._get_adapter()
        if adapter is not None:
            cmd_id = str(uuid.uuid4())
            params = {**payload, "rebalance_id": rebalance_id, "action_id": "apply", "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="ApprovedApply",
                params=params,
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="ApprovedApply",
                target_type="Rebalance",
                target_id=rebalance_id,
                actor_id=actor_id,
                action_id="apply",
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def approve_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        adapter = self._get_adapter()
        if adapter is not None:
            cmd_id = str(uuid.uuid4())
            params = {**payload, "rebalance_id": rebalance_id, "action_id": "approve", "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="ApproveRebalance",
                params=params,
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="ApproveRebalance",
                target_type="Rebalance",
                target_id=rebalance_id,
                actor_id=actor_id,
                action_id="approve",
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def sign_rebalance(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        adapter = self._get_adapter()
        if adapter is not None:
            cmd_id = str(uuid.uuid4())
            params = {**payload, "rebalance_id": rebalance_id, "action_id": "two-man-sign", "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="SignRebalance",
                params=params,
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="SignRebalance",
                target_type="Rebalance",
                target_id=rebalance_id,
                actor_id=actor_id,
                action_id="two-man-sign",
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")

    def rebalance_action(self, payload: Dict[str, Any], rebalance_id: str, *, actor_id: str = "operator", **kwargs: Any) -> Dict[str, Any]:
        adapter = self._get_adapter()
        if adapter is not None:
            action_id = str(payload.get("action_id") or "action")
            cmd_id = str(uuid.uuid4())
            params = {**payload, "rebalance_id": rebalance_id, "action_id": action_id, "actor_id": actor_id}
            result = adapter.execute(
                command_id=cmd_id,
                command_type="RebalanceAction",
                params=params,
            )
            self._record_receipt(
                command_id=cmd_id,
                command_type="RebalanceAction",
                target_type="Rebalance",
                target_id=rebalance_id,
                actor_id=actor_id,
                action_id=action_id,
                params=params,
                result=result,
            )
            return result
        raise CapitalAuthorityUnavailable("No capital execution authority available")


@dataclass
class CapitalService:
    """Store/authority facade shared by all 25 Capital routes."""

    get_read_store: Callable[[], Any]
    get_capital_authority: Optional[Callable[[], Any]] = None
    command_store: Optional[Any] = None
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
        if authority is None:
            raise CapitalAuthorityUnavailable("Capital write authority is unavailable")
        return authority

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
        request_hash = stable_digest(payload)
        if self.command_store is not None:
            cmd = self.command_store.get_command_by_idempotency_key(key, operator_id=actor_id)
            if cmd is not None:
                foundation = cmd.get("foundation") if isinstance(cmd.get("foundation"), dict) else {}
                receipt = foundation.get("receipt") if isinstance(foundation.get("receipt"), dict) else None
                saved_hash = cmd.get("audit", {}).get("request_hash") or (receipt.get("request_hash") if isinstance(receipt, dict) else None)
                if saved_hash and saved_hash != request_hash:
                    raise CapitalValidationError("Idempotency key was already used with a different request")
                if cmd.get("result"):
                    return deepcopy(cmd["result"])
                if receipt:
                    return deepcopy(receipt)
        cache_key = f"{actor_id}:{operation}:{key}"
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

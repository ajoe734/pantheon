"""
SignalStoreClient contract for Pantheon.

The store is the transport boundary between research, execution, and control
planes. It accepts the shared signal payload as-is, enforces idempotency on
`signal_id`, and makes stored signals queryable without rewriting the payload.

The base transport contract intentionally supports a small set of schema
aliases so the store does not block collaboration while the shared signal
schema is still converging.

Out of scope:
- symbol translation for LEAN
- broker execution logic
- confidence scaling
- risk policy decisions
- consumer-specific data enrichment
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, TypeAlias

JSONPrimitive = str | int | float | bool | None
JSONValue: TypeAlias = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]
SignalPayload: TypeAlias = dict[str, JSONValue]
SignalValidator: TypeAlias = Callable[[Mapping[str, JSONValue]], None]

REQUIRED_SYMBOL_FIELDS = ("ticker", "market", "security_type")
FIELD_ALIASES: dict[str, tuple[str | tuple[str, ...], ...]] = {
    "schema_version": ("schema_version", "version"),
    "signal_timestamp": ("ts", "timestamp"),
    "quantity": ("size", "quantity"),
    "run_id": ("run_id",),
    "source_worker": ("source_worker",),
}


class SignalStoreError(Exception):
    """Base error for signal store contract failures."""


class DuplicateSignalError(SignalStoreError):
    """Raised when a duplicate signal_id is written."""


class SignalValidationError(SignalStoreError):
    """Raised when a payload fails basic store-side validation."""


class SignalStatus(str, Enum):
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class SignalRecord:
    signal_id: str
    schema_version: str
    strategy_id: str
    signal_timestamp: str
    run_id: str | None
    source_worker: str | None
    payload: SignalPayload
    ingested_at: datetime
    status: SignalStatus = SignalStatus.ACCEPTED


@dataclass(frozen=True, slots=True)
class SignalQuery:
    signal_id: str | None = None
    strategy_id: str | None = None
    run_id: str | None = None
    source_worker: str | None = None
    status: SignalStatus | None = SignalStatus.ACCEPTED
    limit: int = 100
    newest_first: bool = False

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


class SignalStoreClient(ABC):
    """
    Minimal contract shared by all store implementations.

    Concrete implementations may add operational features such as leasing,
    acknowledgements, or delivery receipts, but these base methods must remain
    stable for v1 consumers.
    """

    @abstractmethod
    def write_signal(self, payload: Mapping[str, JSONValue]) -> SignalRecord:
        """
        Persist the exact signal payload after validation.

        Expected behavior:
        - validates the payload before persisting
        - rejects duplicate signal_id values
        - stores the exact payload without mutating signal fields
        """

    @abstractmethod
    def get_signal(self, signal_id: str) -> SignalRecord | None:
        """Return a stored signal by id, or None when missing."""

    @abstractmethod
    def list_signals(self, query: SignalQuery | None = None) -> list[SignalRecord]:
        """Return stored signals filtered by query."""

    def get_run_signals(self, run_id: str, *, newest_first: bool = False) -> list[SignalRecord]:
        """
        Convenience read for multi-signal batches such as FinRL rebalances.

        This is a required store capability for downstream consumers even if a
        concrete store implements it by delegating to list_signals().

        Signals without `run_id` simply do not participate in run-batch reads.
        """
        return self.list_signals(
            SignalQuery(run_id=run_id, newest_first=newest_first, limit=10_000)
        )


def validate_signal_payload_minimal(payload: Mapping[str, JSONValue]) -> None:
    """
    Validate the minimum transport contract for store writes.

    This intentionally stops short of mirroring the full shared JSON schema.
    Concrete stores should compose this with the canonical validator from
    `services/research/schema.json` rather than duplicating every enum rule.
    """
    signal_id = payload.get("signal_id")
    if not isinstance(signal_id, str) or not signal_id.strip():
        raise SignalValidationError("signal_id must be a non-empty string")

    strategy_id = payload.get("strategy_id")
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        raise SignalValidationError("strategy_id must be a non-empty string")

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        raise SignalValidationError("action must be a non-empty string")

    version = _extract_required_string(payload, "schema_version")
    timestamp = _extract_required_string(payload, "signal_timestamp")
    _ = (version, timestamp)

    symbol = payload.get("symbol")
    _validate_symbol(symbol)

    quantity = _extract_required_number(payload, "quantity")
    if quantity < 0:
        raise SignalValidationError("size/quantity must be zero or a positive number")


class InMemorySignalStoreClient(SignalStoreClient):
    """
    Reference implementation for local development and tests.

    It demonstrates the contract semantics without choosing a production
    backend such as Redis, Postgres, or GCS-backed objects.
    """

    def __init__(self, validator: SignalValidator | None = None) -> None:
        self._validator = validator or validate_signal_payload_minimal
        self._records: dict[str, SignalRecord] = {}

    def write_signal(self, payload: Mapping[str, JSONValue]) -> SignalRecord:
        self._validator(payload)

        signal_id = str(payload["signal_id"])
        if signal_id in self._records:
            raise DuplicateSignalError(f"signal_id already exists: {signal_id}")

        stored_payload = _clone_mapping(payload)
        record = SignalRecord(
            signal_id=signal_id,
            schema_version=_extract_required_string(stored_payload, "schema_version"),
            strategy_id=str(stored_payload["strategy_id"]),
            signal_timestamp=_extract_required_string(stored_payload, "signal_timestamp"),
            run_id=_extract_optional_string(stored_payload, "run_id"),
            source_worker=_extract_optional_string(stored_payload, "source_worker"),
            payload=stored_payload,
            ingested_at=datetime.now(timezone.utc),
        )
        self._records[signal_id] = record
        return record

    def get_signal(self, signal_id: str) -> SignalRecord | None:
        return self._records.get(signal_id)

    def list_signals(self, query: SignalQuery | None = None) -> list[SignalRecord]:
        active_query = query or SignalQuery()
        records = list(self._records.values())
        filtered = [record for record in records if _matches(record, active_query)]
        filtered.sort(key=lambda record: record.ingested_at, reverse=active_query.newest_first)
        return filtered[: active_query.limit]


def _matches(record: SignalRecord, query: SignalQuery) -> bool:
    if query.signal_id and record.signal_id != query.signal_id:
        return False
    if query.strategy_id and record.strategy_id != query.strategy_id:
        return False
    if query.run_id and record.run_id != query.run_id:
        return False
    if query.source_worker and record.source_worker != query.source_worker:
        return False
    if query.status and record.status != query.status:
        return False
    return True


def _clone_mapping(value: Mapping[str, Any]) -> SignalPayload:
    return {str(key): _clone_json(item) for key, item in value.items()}


def _clone_json(value: Any) -> JSONValue:
    if isinstance(value, Mapping):
        return {str(key): _clone_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_json(item) for item in value]
    return value


def _extract_required_string(payload: Mapping[str, Any], field_name: str) -> str:
    value = _extract_alias(payload, field_name)
    if not isinstance(value, str) or not value.strip():
        aliases = _format_aliases(field_name)
        raise SignalValidationError(f"{aliases} must be a non-empty string")
    return value


def _extract_optional_string(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = _extract_alias(payload, field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        aliases = _format_aliases(field_name)
        raise SignalValidationError(f"{aliases} must be a non-empty string when provided")
    return value


def _extract_required_number(payload: Mapping[str, Any], field_name: str) -> float:
    value = _extract_alias(payload, field_name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        aliases = _format_aliases(field_name)
        raise SignalValidationError(f"{aliases} must be numeric")
    return float(value)


def _extract_alias(payload: Mapping[str, Any], field_name: str) -> Any:
    aliases = FIELD_ALIASES.get(field_name, (field_name,))
    for alias in aliases:
        value = _get_value(payload, alias)
        if value is not None:
            return value
    return None


def _get_value(payload: Mapping[str, Any], alias: str | tuple[str, ...]) -> Any:
    if isinstance(alias, tuple):
        current: Any = payload
        for part in alias:
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
        return current
    return payload.get(alias)


def _format_aliases(field_name: str) -> str:
    aliases = FIELD_ALIASES.get(field_name, (field_name,))
    normalized = []
    for alias in aliases:
        if isinstance(alias, tuple):
            normalized.append(".".join(alias))
        else:
            normalized.append(alias)
    return "/".join(normalized)


def _validate_symbol(symbol: Any) -> None:
    if isinstance(symbol, str):
        if not symbol.strip():
            raise SignalValidationError("symbol must be a non-empty string")
        return

    if not isinstance(symbol, Mapping):
        raise SignalValidationError("symbol must be either a non-empty string or an object")

    missing_symbol = [field for field in REQUIRED_SYMBOL_FIELDS if field not in symbol]
    if missing_symbol:
        missing = ", ".join(missing_symbol)
        raise SignalValidationError(f"symbol missing required fields: {missing}")

    for field in REQUIRED_SYMBOL_FIELDS:
        value = symbol.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SignalValidationError(f"symbol.{field} must be a non-empty string")


__all__ = [
    "DuplicateSignalError",
    "InMemorySignalStoreClient",
    "JSONValue",
    "SignalPayload",
    "SignalQuery",
    "SignalRecord",
    "SignalStatus",
    "SignalStoreClient",
    "SignalStoreError",
    "SignalValidationError",
    "validate_signal_payload_minimal",
]

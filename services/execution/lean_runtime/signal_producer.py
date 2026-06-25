"""Decision-to-signal producer for LEAN runtime pending queues.

This module is the small boundary between persona/strategy decision artifacts
and the execution-facing signal schema v1 payload consumed by
``RedisPendingSignalStore`` / ``SignalConsumer``.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.execution.lean_runtime.pending_signal_store import (
    PendingSignalStore,
    RedisPendingSignalStore,
    validate_signal_payload_minimal,
)

SIGNAL_SCHEMA_VERSION = "1.0"

_ACTIONS = {"BUY", "SELL", "HOLD", "EXIT"}
_DIRECTIONS = {"LONG", "SHORT"}
_ORDER_TYPES = {"MARKET", "LIMIT"}
_QUANTITY_TYPES = {"SHARES", "PERCENT_PORTFOLIO", "CASH_VALUE"}
_UUID_NAMESPACE = uuid.UUID("b22ee960-00c4-4e60-9d53-203f23a400c3")
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "research" / "schema.json"


class SignalProducerValidationError(ValueError):
    """Raised when a decision cannot be normalized to signal schema v1."""


@dataclass(frozen=True)
class ProducedSignalBatch:
    """Signals created from one decision and enqueued successfully."""

    signals: list[dict[str, Any]]

    @property
    def count(self) -> int:
        return len(self.signals)


class DecisionSignalProducer:
    """Normalize decision outputs to signal schema v1 and enqueue them."""

    def __init__(self, store: PendingSignalStore) -> None:
        self._store = store

    @classmethod
    def redis(
        cls,
        redis_url: str,
        *,
        queue_key: str = "pantheon:signals:pending",
        default_batch_size: int = 100,
    ) -> "DecisionSignalProducer":
        """Build a producer backed by ``RedisPendingSignalStore``."""
        return cls(
            RedisPendingSignalStore(
                redis_url,
                queue_key=queue_key,
                default_batch_size=default_batch_size,
            )
        )

    def build_signals(
        self,
        decision: Mapping[str, Any] | Any,
        *,
        now_iso: str | None = None,
        strategy_id: str | None = None,
        source_worker: str | None = None,
        binding_id: str | None = None,
        runtime_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return schema-v1 signals for *decision* without enqueuing them."""
        return build_decision_signals(
            decision,
            now_iso=now_iso,
            strategy_id=strategy_id,
            source_worker=source_worker,
            binding_id=binding_id,
            runtime_id=runtime_id,
            run_id=run_id,
        )

    def produce(
        self,
        decision: Mapping[str, Any] | Any,
        *,
        now_iso: str | None = None,
        strategy_id: str | None = None,
        source_worker: str | None = None,
        binding_id: str | None = None,
        runtime_id: str | None = None,
        run_id: str | None = None,
    ) -> ProducedSignalBatch:
        """Build, validate, and enqueue all signals for one decision."""
        signals = self.build_signals(
            decision,
            now_iso=now_iso,
            strategy_id=strategy_id,
            source_worker=source_worker,
            binding_id=binding_id,
            runtime_id=runtime_id,
            run_id=run_id,
        )
        for signal in signals:
            self._store.enqueue(signal)
        return ProducedSignalBatch(signals=signals)

    def produce_many(
        self,
        decisions: Sequence[Mapping[str, Any] | Any],
        *,
        now_iso: str | None = None,
        strategy_id: str | None = None,
        source_worker: str | None = None,
        binding_id: str | None = None,
        runtime_id: str | None = None,
        run_id: str | None = None,
    ) -> ProducedSignalBatch:
        """Build, validate, and enqueue signals for multiple decisions."""
        all_signals: list[dict[str, Any]] = []
        for decision in decisions:
            all_signals.extend(
                self.produce(
                    decision,
                    now_iso=now_iso,
                    strategy_id=strategy_id,
                    source_worker=source_worker,
                    binding_id=binding_id,
                    runtime_id=runtime_id,
                    run_id=run_id,
                ).signals
            )
        return ProducedSignalBatch(signals=all_signals)


def build_decision_signals(
    decision: Mapping[str, Any] | Any,
    *,
    now_iso: str | None = None,
    strategy_id: str | None = None,
    source_worker: str | None = None,
    binding_id: str | None = None,
    runtime_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize a persona/strategy decision to one or more schema-v1 signals."""
    payload = _mapping_from_decision(decision)
    timestamp = _first_string(payload, "timestamp", "signal_timestamp", "created_at", "decided_at")
    timestamp = timestamp or now_iso or _utc_now()
    normalized_strategy_id = _strategy_id(payload, strategy_id)
    common = {
        "version": SIGNAL_SCHEMA_VERSION,
        "strategy_id": normalized_strategy_id,
        "timestamp": timestamp,
    }
    if run_id or _first_string(payload, "run_id"):
        common["run_id"] = str(run_id or _first_string(payload, "run_id"))
    if binding_id or _first_string(payload, "binding_id"):
        common["binding_id"] = str(binding_id or _first_string(payload, "binding_id"))
    if runtime_id or _first_string(payload, "runtime_id"):
        common["runtime_id"] = str(runtime_id or _first_string(payload, "runtime_id"))
    if source_worker or _first_string(payload, "source_worker"):
        common["source_worker"] = str(source_worker or _first_string(payload, "source_worker"))

    targets = _decision_targets(payload)
    signals = [
        _build_one_signal(payload, target, index=index, common=common)
        for index, target in enumerate(targets)
    ]
    for signal in signals:
        validate_signal_v1(signal)
    return signals


def validate_signal_v1(signal: Mapping[str, Any]) -> None:
    """Validate producer output before enqueueing it into the pending store."""
    validate_signal_payload_minimal(signal)
    version = str(signal.get("version") or "")
    if version != SIGNAL_SCHEMA_VERSION:
        raise SignalProducerValidationError(
            f"version must be {SIGNAL_SCHEMA_VERSION!r}, got {version!r}"
        )
    action = str(signal.get("action") or "").upper()
    if action not in _ACTIONS:
        raise SignalProducerValidationError(f"action must be one of {sorted(_ACTIONS)}")
    direction = str(signal.get("direction") or "").upper()
    if direction not in _DIRECTIONS:
        raise SignalProducerValidationError(f"direction must be one of {sorted(_DIRECTIONS)}")
    order_type = str(signal.get("order_type") or "MARKET").upper()
    if order_type not in _ORDER_TYPES:
        raise SignalProducerValidationError(f"order_type must be one of {sorted(_ORDER_TYPES)}")
    quantity_type = str(signal.get("quantity_type") or "").upper()
    if quantity_type not in _QUANTITY_TYPES:
        raise SignalProducerValidationError(
            f"quantity_type must be one of {sorted(_QUANTITY_TYPES)}"
        )
    if order_type == "LIMIT" and signal.get("limit_price") is None:
        raise SignalProducerValidationError("limit_price is required when order_type=LIMIT")
    metadata = signal.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise SignalProducerValidationError("metadata must be an object when provided")


def _build_one_signal(
    decision: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    index: int,
    common: Mapping[str, Any],
) -> dict[str, Any]:
    action, direction = _action_direction(decision, target)
    quantity = _target_quantity(decision, target)
    quantity_type = _quantity_type(decision, target)
    order_type = str(_field(decision, "order_type", "orderType") or "MARKET").upper()
    metadata = _signal_metadata(decision, target)
    signal: dict[str, Any] = {
        **dict(common),
        "signal_id": _signal_id(decision, target, index, common),
        "symbol": _target_symbol(target),
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": quantity_type,
        "order_type": order_type,
    }
    limit_price = _field(decision, "limit_price", "limitPrice")
    if limit_price is not None:
        signal["limit_price"] = _number(limit_price, "limit_price")
    if metadata:
        signal["metadata"] = metadata
    return signal


def _decision_targets(decision: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    explicit_targets = _field(decision, "signals", "signal_targets", "targets")
    if isinstance(explicit_targets, Sequence) and not isinstance(explicit_targets, (str, bytes)):
        targets = [_mapping_from_decision(item) for item in explicit_targets]
        if not targets:
            raise SignalProducerValidationError("decision target list must not be empty")
        return targets

    target_weights = _field(decision, "target_weights", "targetWeights")
    if isinstance(target_weights, Mapping):
        if not target_weights:
            raise SignalProducerValidationError("target_weights must not be empty")
        return [
            {"symbol": str(symbol), "quantity": weight, "weight": weight}
            for symbol, weight in target_weights.items()
        ]

    symbol = _field(decision, "symbol", "execution_symbol", "executionSymbol")
    if symbol is None:
        raise SignalProducerValidationError("decision must provide symbol or target_weights")
    return [{"symbol": symbol}]


def _signal_metadata(decision: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _json_object(_field(decision, "metadata")) or {}
    target_metadata = _json_object(_field(target, "metadata")) or {}
    metadata.update(target_metadata)

    for key in ("persona_id", "proposal_id", "artifact_id", "capital_pool_id", "scope_ref"):
        value = _field(decision, key)
        if value not in (None, ""):
            metadata[key] = _json_safe(value)
    decision_id = _decision_id(decision)
    if decision_id:
        metadata.setdefault("decision_id", decision_id)
    proposal_id = _first_string(decision, "proposal_id")
    if proposal_id:
        metadata.setdefault("allocation_proposal_id", proposal_id)

    evidence_refs = _field(decision, "evidence_refs", "evidenceRefs")
    if isinstance(evidence_refs, Sequence) and not isinstance(evidence_refs, (str, bytes)):
        metadata.setdefault("evidence_refs", [_json_safe(ref) for ref in evidence_refs])

    risk_parameters = _field(decision, "risk_parameters", "riskParameters")
    if risk_parameters is None:
        risk_parameters = metadata.get("risk_parameters")
    if isinstance(risk_parameters, Mapping):
        metadata["risk_parameters"] = _json_safe(risk_parameters)

    confidence = _field(decision, "confidence_score", "confidence", "conviction")
    if confidence is None:
        confidence = metadata.get("confidence_score")
    if confidence is not None:
        metadata["confidence_score"] = _clamp_confidence(confidence)

    if "symbol" in target:
        metadata.setdefault("target_symbol", str(target["symbol"]))
    if "weight" in target:
        metadata.setdefault("target_weight", _number(target["weight"], "target weight"))

    return {key: _json_safe(value) for key, value in metadata.items()}


def _action_direction(
    decision: Mapping[str, Any],
    target: Mapping[str, Any],
) -> tuple[str, str]:
    raw_action = _field(target, "action") or _field(decision, "action", "decision")
    raw_direction = _field(target, "direction") or _first_direction(decision)
    action_token = _token(raw_action)
    direction_token = _token(raw_direction)

    if action_token in _ACTIONS:
        action = action_token
    elif action_token in {"BUY", "LONG", "INCREASE", "INCREASE_ALPHA_ALLOCATION", "ADD_LONG"}:
        action = "BUY"
    elif action_token in {"SELL", "SHORT", "OPEN_SHORT", "ADD_SHORT"}:
        action = "SELL"
    elif action_token in {"REDUCE", "REDUCE_POSITION_SIZE", "DECREASE", "DE_RISK", "RISK_OFF"}:
        action = "SELL"
    elif action_token in {"FLAT", "CLOSE", "CLOSE_POSITION", "EXIT_POSITION"}:
        action = "EXIT"
    elif action_token in {"HOLD", "PAUSE", "NOOP"}:
        action = "HOLD"
    elif direction_token in {"SHORT"}:
        action = "SELL"
    else:
        action = "BUY"

    if direction_token in _DIRECTIONS:
        direction = direction_token
    elif direction_token in {"BUY", "INCREASE", "RISK_ON"}:
        direction = "LONG"
    elif direction_token in {"SELL", "SHORT", "DECREASE", "DE_RISK", "RISK_OFF"}:
        direction = "SHORT" if action != "SELL" or action_token in {"SHORT", "OPEN_SHORT"} else "LONG"
    elif action == "SELL" and action_token in {"SHORT", "OPEN_SHORT", "ADD_SHORT"}:
        direction = "SHORT"
    else:
        direction = "LONG"

    return action, direction


def _target_quantity(decision: Mapping[str, Any], target: Mapping[str, Any]) -> float:
    value = _field(
        target,
        "quantity",
        "target_percent",
        "targetPercent",
        "target_weight",
        "targetWeight",
        "weight",
    )
    if value is None:
        value = _field(
            decision,
            "quantity",
            "target_percent",
            "targetPercent",
            "target_weight",
            "targetWeight",
            "weight",
            "size",
        )
    if value is None:
        raise SignalProducerValidationError("decision target must provide quantity or weight")
    numeric = _number(value, "quantity")
    if numeric < 0:
        raise SignalProducerValidationError("quantity must be >= 0")
    return numeric


def _quantity_type(decision: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    value = _field(target, "quantity_type", "quantityType") or _field(
        decision,
        "quantity_type",
        "quantityType",
    )
    if value is None and ("weight" in target or "target_weight" in target or "target_percent" in target):
        value = "PERCENT_PORTFOLIO"
    if value is None:
        raise SignalProducerValidationError("quantity_type is required")
    normalized = str(value).upper()
    if normalized in {"PERCENT", "TARGET_PERCENT", "WEIGHT"}:
        return "PERCENT_PORTFOLIO"
    return normalized


def _signal_id(
    decision: Mapping[str, Any],
    target: Mapping[str, Any],
    index: int,
    common: Mapping[str, Any],
) -> str:
    explicit = _field(target, "signal_id", "signalId") or _field(decision, "signal_id", "signalId")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    seed = {
        "decision_id": _decision_id(decision),
        "strategy_id": common.get("strategy_id"),
        "run_id": common.get("run_id"),
        "symbol": _target_symbol(target),
        "action_direction": _action_direction(decision, target),
        "quantity": _target_quantity(decision, target),
        "quantity_type": _quantity_type(decision, target),
        "index": index,
    }
    return str(uuid.uuid5(_UUID_NAMESPACE, json.dumps(seed, sort_keys=True, default=str)))


def _strategy_id(decision: Mapping[str, Any], override: str | None) -> str:
    value = override or _first_string(
        decision,
        "strategy_id",
        "strategyId",
        "strategy_ref",
        "strategySpecId",
        "strategy_spec_id",
    )
    metadata = _json_object(_field(decision, "metadata")) or {}
    if not value:
        value = _first_string(
            metadata,
            "strategy_id",
            "strategyId",
            "strategy_spec_id",
            "strategy_spec_ref",
            "strategy_family",
        )
    if not value:
        value = _first_string(decision, "proposal_id", "artifact_id", "decision_id")
    if not value:
        raise SignalProducerValidationError("strategy_id is required")
    return str(value)


def _decision_id(decision: Mapping[str, Any]) -> str | None:
    return _first_string(
        decision,
        "decision_id",
        "decisionId",
        "proposal_id",
        "artifact_id",
        "policy_id",
    )


def _first_direction(decision: Mapping[str, Any]) -> Any:
    raw = _field(decision, "direction")
    if raw:
        return raw
    directions = _field(decision, "directions")
    if isinstance(directions, Sequence) and not isinstance(directions, (str, bytes)) and directions:
        return directions[0]
    return None


def _target_symbol(target: Mapping[str, Any]) -> str:
    symbol = _field(target, "symbol", "execution_symbol", "executionSymbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise SignalProducerValidationError("symbol must be a non-empty string")
    return symbol.strip()


def _mapping_from_decision(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        mapped = value.to_dict()
        if isinstance(mapped, Mapping):
            return {str(key): item for key, item in mapped.items()}
    if hasattr(value, "model_dump") and callable(value.model_dump):
        mapped = value.model_dump(exclude_none=True)
        if isinstance(mapped, Mapping):
            return {str(key): item for key, item in mapped.items()}
    raise SignalProducerValidationError("decision must be a mapping or serializable model")


def _json_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SignalProducerValidationError("metadata/risk_parameters must be objects")
    return {str(key): _json_safe(item) for key, item in value.items()}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _field(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _first_string(mapping: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = mapping.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _token(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper().replace("-", "_").replace(" ", "_")


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SignalProducerValidationError(f"{field_name} must be numeric")
    return float(value)


def _clamp_confidence(value: Any) -> float:
    confidence = _number(value, "confidence_score")
    if confidence < 0 or confidence > 1:
        raise SignalProducerValidationError("confidence_score must be in [0, 1]")
    return confidence


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "DecisionSignalProducer",
    "ProducedSignalBatch",
    "SIGNAL_SCHEMA_VERSION",
    "SignalProducerValidationError",
    "build_decision_signals",
    "validate_signal_v1",
]

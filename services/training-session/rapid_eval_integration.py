"""Rapid-eval backend integration for trainer persona patches.

This module is intentionally side-effect free.  It adapts a trainer
persona-patch ref plus StrategySpec-like ref into the governed vectorbt adapter
input shape and returns an advisory eval summary.  Registry writes, runtime
promotion, and TRN-003 endpoint mutation stay out of scope.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

from services.research.vectorbt.adapter import (
    BacktestConfig,
    VectorbtWorkflowError,
    run_vectorbt_workflow,
)


class RapidEvalInputError(ValueError):
    """Raised when rapid-eval input refs are missing or malformed."""


class RapidEvalBackendError(RuntimeError):
    """Raised when the vectorbt adapter rejects or cannot evaluate the request."""


def run_rapid_eval(
    persona_patch_ref: str | Mapping[str, Any],
    strategy_ref: str | Mapping[str, Any],
    *,
    dataset_ref: Mapping[str, Any] | None = None,
    requested_by: str = "Codex",
    vectorbt_runner: Callable[..., Any] = run_vectorbt_workflow,
) -> dict[str, Any]:
    """Run a rapid evaluation through the governed vectorbt adapter facade.

    ``persona_patch_ref`` must identify the candidate patch.  ``strategy_ref``
    may be a StrategySpec-like mapping with OHLCV ``records`` or a stable ref
    string; string-only refs use a deterministic synthetic dataset for local
    smoke evaluation.
    """

    patch_id, patch_payload = _normalize_ref(persona_patch_ref, "persona_patch_ref", strict=True)
    strategy_id, strategy_payload = _normalize_ref(strategy_ref, "strategy_ref", strict=False)

    dataset = _build_vectorbt_dataset(
        strategy_id=strategy_id,
        strategy_payload=strategy_payload,
        dataset_ref=dataset_ref,
    )
    strategy_params = _strategy_params(strategy_payload, patch_payload)
    config = BacktestConfig(requested_by=requested_by, strategy_params=strategy_params)

    try:
        result = vectorbt_runner(dataset, config=config)
    except VectorbtWorkflowError as exc:
        raise RapidEvalBackendError(f"vectorbt rapid eval failed: {exc}") from exc

    aggregate_metrics = result.backtest_result.aggregate_metrics
    eval_metrics = {
        "sharpe": _round_metric(aggregate_metrics.get("mean_sharpe_ratio")),
        "sortino": round(_sortino(_period_returns(dataset["records"])), 6),
        "max_dd": _round_metric(aggregate_metrics.get("mean_max_drawdown")),
    }

    registry_entry = result.registry_entry
    return {
        **eval_metrics,
        "eval_summary": dict(eval_metrics),
        "status": "completed",
        "persona_patch_ref": patch_id,
        "strategy_ref": strategy_id,
        "strategy_id": result.prepared_dataset.strategy_id,
        "dataset_id": result.prepared_dataset.dataset_id,
        "framework": result.artifact_bundle.get("framework"),
        "backend": result.backtest_result.backend,
        "producer_run_id": result.backtest_result.run_id,
        "strategy_params": dict(strategy_params),
        "aggregate_metrics": dict(aggregate_metrics),
        "lineage": dict(registry_entry.get("lineage") or {}),
        "registry_entry": dict(registry_entry),
        "governance": {
            "advisory_only": True,
            "direct_live_influence": False,
            "engine": "services.research.vectorbt.adapter.run_vectorbt_workflow",
        },
    }


def _normalize_ref(
    value: str | Mapping[str, Any],
    field_name: str,
    *,
    strict: bool,
) -> tuple[str, dict[str, Any]]:
    if isinstance(value, str):
        ref = value.strip()
        if not ref:
            raise RapidEvalInputError(f"{field_name} must be a non-empty string or mapping")
        return ref, {}

    if not isinstance(value, Mapping) or not value:
        raise RapidEvalInputError(f"{field_name} must be a non-empty string or mapping")

    payload = dict(value)
    if field_name == "persona_patch_ref":
        ref = _first_text(
            payload.get(field_name),
            payload.get("patch_ref"),
            payload.get("patch_id"),
            payload.get("id"),
            payload.get("ref"),
            payload.get("registry_id"),
        )
    else:
        ref = _first_text(
            payload.get(field_name),
            payload.get("strategy_ref"),
            payload.get("strategy_id"),
            payload.get("strategy_spec_id"),
            payload.get("id"),
            payload.get("ref"),
            payload.get("registry_id"),
        )
    if not ref:
        if strict:
            raise RapidEvalInputError(f"{field_name} must include a stable ref id")
        ref = _stable_inline_ref(field_name, payload)
    return ref, payload


def _build_vectorbt_dataset(
    *,
    strategy_id: str,
    strategy_payload: Mapping[str, Any],
    dataset_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    dataset_payload = dict(dataset_ref or {})
    records = _records_from(dataset_payload, strategy_payload)
    if not records:
        records = _synthetic_records(_symbols(strategy_payload))

    dataset_id = _first_text(
        dataset_payload.get("dataset_id"),
        strategy_payload.get("dataset_id"),
        strategy_payload.get("dataset_version_id"),
        f"rapid-eval-{strategy_id}",
    )
    return {
        "dataset_id": dataset_id,
        "strategy_id": strategy_id,
        "source_dataset_refs": _source_dataset_refs(dataset_payload, strategy_payload, strategy_id),
        "source_strategy_spec_id": _first_text(
            strategy_payload.get("source_strategy_spec_id"),
            strategy_payload.get("strategy_spec_id"),
            strategy_payload.get("registry_id"),
            strategy_id,
        ),
        "data_frequency": _data_frequency(dataset_payload, strategy_payload),
        "records": records,
    }


def _records_from(*payloads: Mapping[str, Any]) -> list[dict[str, Any]]:
    for payload in payloads:
        for candidate in (
            payload.get("records"),
            payload.get("ohlcv_records"),
            _mapping(payload.get("dataset")).get("records"),
            _mapping(payload.get("market_data")).get("records"),
        ):
            records = _coerce_records(candidate)
            if records:
                return records
    return []


def _coerce_records(value: Any) -> list[dict[str, Any]]:
    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RapidEvalInputError("strategy_ref records must be mappings")
        records.append(dict(item))
    return records


def _source_dataset_refs(
    dataset_payload: Mapping[str, Any],
    strategy_payload: Mapping[str, Any],
    strategy_id: str,
) -> list[str]:
    refs = _text_list(dataset_payload.get("source_dataset_refs")) or _text_list(
        strategy_payload.get("source_dataset_refs")
    )
    if refs:
        return refs

    refs = _data_dependency_refs(dataset_payload) or _data_dependency_refs(strategy_payload)
    if refs:
        return refs

    dataset_version_id = _first_text(
        dataset_payload.get("dataset_version_id"),
        strategy_payload.get("dataset_version_id"),
        strategy_payload.get("source_dataset_ref"),
    )
    if dataset_version_id:
        return [dataset_version_id]
    return [f"rapid-eval-synthetic:{strategy_id}"]


def _data_dependency_refs(strategy_payload: Mapping[str, Any]) -> list[str]:
    raw = strategy_payload.get("data_dependencies")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return []
    refs: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            ref = _first_text(item.get("ref"), item.get("id"), item.get("dataset_version_id"))
            if ref:
                refs.append(ref)
    return refs


def _data_frequency(*payloads: Mapping[str, Any]) -> str:
    for payload in payloads:
        frequency = _first_text(
            payload.get("data_frequency"),
            payload.get("frequency"),
            _mapping(payload.get("market_scope")).get("frequency"),
        )
        if frequency:
            return frequency
    return "daily"


def _strategy_params(
    strategy_payload: Mapping[str, Any],
    patch_payload: Mapping[str, Any],
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    params.update(_params_from(strategy_payload))
    params.update(_params_from(patch_payload))
    for key in ("short_window", "long_window"):
        if key in params:
            params[key] = _positive_int(params[key], key)
    return params


def _params_from(payload: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in ("strategy_params", "params", "control_patch", "patch"):
        raw = payload.get(key)
        if isinstance(raw, Mapping):
            params.update(_flatten_params(raw))

    controls = payload.get("controls")
    if isinstance(controls, Sequence) and not isinstance(controls, (str, bytes)):
        for control in controls:
            if not isinstance(control, Mapping):
                continue
            key = _first_text(control.get("parameter_key"), control.get("field"), control.get("key"))
            if not key:
                continue
            value = control.get("proposed_value", control.get("current_value", control.get("value")))
            params[_param_key(key)] = value
    return params


def _flatten_params(payload: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                flattened[_param_key(str(nested_key))] = nested_value
        else:
            flattened[_param_key(str(key))] = value
    return flattened


def _param_key(key: str) -> str:
    return key.rsplit(".", 1)[-1].strip()


def _positive_int(value: Any, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RapidEvalInputError(f"{field_name} must be a positive integer") from exc
    if normalized <= 0:
        raise RapidEvalInputError(f"{field_name} must be a positive integer")
    return normalized


def _period_returns(records: Sequence[Mapping[str, Any]]) -> list[float]:
    by_instrument: dict[str, list[tuple[str, float]]] = {}
    for record in records:
        instrument = _first_text(record.get("instrument"))
        day = _first_text(record.get("date"))
        close = record.get("close")
        if not instrument or not day or isinstance(close, bool) or not isinstance(close, (int, float)):
            continue
        by_instrument.setdefault(instrument, []).append((day, float(close)))

    buckets: dict[str, list[float]] = {}
    for values in by_instrument.values():
        prior_close: float | None = None
        for day, close in sorted(values):
            if prior_close and prior_close > 0:
                buckets.setdefault(day, []).append((close - prior_close) / prior_close)
            prior_close = close
    return [_mean(buckets[day]) for day in sorted(buckets)]


def _sortino(returns: Sequence[float]) -> float:
    downside = [value for value in returns if value < 0.0]
    if not downside:
        return 0.0
    downside_dev = (sum(value**2 for value in downside) / len(downside)) ** 0.5
    if downside_dev <= 1e-12:
        return 0.0
    return (_mean(returns) / downside_dev) * (252**0.5)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _round_metric(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(float(value), 6)


def _synthetic_records(symbols: Sequence[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    start = date(2026, 1, 1)
    for symbol_index, symbol in enumerate(symbols[:2]):
        price = 100.0 + symbol_index * 17.0
        for index in range(40):
            drift = 0.006 if (index + symbol_index) % 5 else -0.011
            price *= 1.0 + drift
            records.append(
                {
                    "instrument": symbol,
                    "date": (start + timedelta(days=index)).isoformat(),
                    "open": round(price * 0.997, 4),
                    "high": round(price * 1.006, 4),
                    "low": round(price * 0.994, 4),
                    "close": round(price, 4),
                    "volume": 1_000_000 + symbol_index * 100_000 + index * 1_000,
                }
            )
    return records


def _symbols(strategy_payload: Mapping[str, Any]) -> list[str]:
    raw_symbols = _text_list(_mapping(strategy_payload.get("market_scope")).get("symbols"))
    symbols = raw_symbols or _text_list(strategy_payload.get("symbols")) or ["SYNTH_A", "SYNTH_B"]
    if len(symbols) == 1:
        symbols.append(f"{symbols[0]}_PAIR")
    return symbols


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Mapping) or not isinstance(value, Sequence):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _stable_inline_ref(field_name: str, payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return f"inline-{field_name}-{digest}"


__all__ = [
    "RapidEvalBackendError",
    "RapidEvalInputError",
    "run_rapid_eval",
]

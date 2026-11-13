"""Evolvable StrategyArtifact validation, mutation, and registry mapping.

StrategyArtifact is an additive payload overlay on the existing
``execution_bundle`` registry artifact type.  This module deliberately keeps
artifact-state transitions in :class:`RegistryService`; it only validates the
payload, produces immutable child revisions, and maps them into the generic
registry envelope.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator, FormatChecker

from .factor_rule import (
    FactorRuleValidationError,
    evaluate_factor_rule_action,
    evaluate_factor_rule_view,
    validate_factor_rule_logic,
)
from .models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntryCreate,
    RegistryEntryView,
    StorageBackend,
    StorageRef,
)
from .split_api import RegistryError, RegistryService


_REGISTRY_DIR = Path(__file__).resolve().parent
STRATEGY_ARTIFACT_SCHEMA_PATH = _REGISTRY_DIR / "strategy_artifact.schema.json"
BUILTIN_STRATEGY_ARTIFACT_PATHS = (
    _REGISTRY_DIR
    / "strategy-artifacts"
    / "tw-session-momentum-v1.registration.json",
)
STRATEGY_ARTIFACT_REGISTRATION_CHECKSUM_KEY = (
    "strategy_artifact_registration_checksum"
)
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_PYTHON_REF_RE = re.compile(
    r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$"
)
EXPRESSION_POLICY_VERSION = "1.0"
EXPRESSION_POLICY_KIND = "notional_target_v1"
FORBIDDEN_PARAMETER_KEY_RE = re.compile(r"^prefer_.*_when_.*$")
FORBIDDEN_MUTATION_CONTROL_KEYS: frozenset[str] = frozenset({
    "max_leverage",
    "gross_limit",
    "net_limit",
    "allowed_asset_classes",
    "allow_pyramiding",
    "close_before_reverse",
})
REQUIRED_EXPRESSION_POLICY_CONTROLS: dict[str, dict[str, Any]] = {
    "strength_scale_k": {
        "value_type": "number",
        "default_value": 1.0,
        "allowed_range": {"min": 0.25, "max": 4.0},
        "step": 0.05,
    },
    "strength_floor_momentum": {
        "value_type": "number",
        "default_value": 0.01,
        "allowed_range": {"min": 0.002, "max": 0.05},
        "step": 0.001,
    },
    "lever_min_strength": {
        "value_type": "number",
        "default_value": 0.5,
        "allowed_range": {"min": 0.0, "max": 1.0},
        "step": 0.05,
    },
    "notional_fraction_at_full_strength": {
        "value_type": "number",
        "default_value": 1.0,
        "allowed_range": {"min": 0.1, "max": 1.0},
        "step": 0.05,
    },
}
_EXPRESSION_POLICY_MUTABLE_PARAM_KEYS: frozenset[str] = frozenset({
    "lever_min_strength",
    "notional_fraction_at_full_strength",
})
_EXPRESSION_POLICY_REQUIRED_PARAM_KEYS: frozenset[str] = frozenset({
    "lever_min_strength",
    "notional_fraction_at_full_strength",
    "allow_pyramiding",
    "close_before_reverse",
})


class StrategyArtifactValidationError(RegistryError):
    """Raised when a StrategyArtifact violates schema or semantic rules."""


SUPPORTED_MARKETS: tuple[str, ...] = ("US", "TW", "CRYPTO", "FX")

_US_MARKET_TOKENS: frozenset[str] = frozenset({
    "US",
    "USA",
    "UNITED STATES",
    "UNITED_STATES",
    "SMART",
    "NASDAQ",
    "NYSE",
    "ARCA",
    "BATS",
    "AMEX",
})

_TW_MARKET_TOKENS: frozenset[str] = frozenset({
    "TW",
    "TWSE",
    "TAIWAN",
    "TPEX",
    "TWO",
    "TSE",
    "OTC",
    "TAIFEX",
})

_CRYPTO_MARKET_TOKENS: frozenset[str] = frozenset({
    "CRYPTO",
    "KRAKEN",
    "BINANCE",
    "COINBASE",
})

_FX_MARKET_TOKENS: frozenset[str] = frozenset({
    "FX",
    "FOREX",
    "OANDA",
})

def classify_market_token(token: Any) -> str | None:
    """Classify a market token or venue string into a canonical market category."""
    if not token or not isinstance(token, str):
        return None
    raw = token.strip().lstrip(".").upper()
    if not raw:
        return None
    if raw in _US_MARKET_TOKENS:
        return "US"
    if raw in _TW_MARKET_TOKENS:
        return "TW"
    if raw in _CRYPTO_MARKET_TOKENS:
        return "CRYPTO"
    if raw in _FX_MARKET_TOKENS:
        return "FX"
    return None


def intrinsic_symbol_market(symbol: str) -> str | None:
    """Detect intrinsic market from dotted suffix."""
    s = str(symbol or "").strip()
    if "." in s:
        suffix = s.rsplit(".", 1)[1]
        return classify_market_token(suffix)
    return None


def canonical_market_context(
    market: Any,
    symbols: Sequence[str] | None = None,
) -> str:
    """Validate and return canonical market string (US, TW, CRYPTO, FX).

    Rejects unsupported, non-canonical, or contradictory market contexts.
    """
    if not isinstance(market, str):
        raise StrategyArtifactValidationError(
            f"market context must be a string, got {type(market).__name__}"
        )
    if not market.strip() or market != market.strip():
        raise StrategyArtifactValidationError(
            "market context must be a canonical non-blank string without leading/trailing whitespace"
        )
    category = classify_market_token(market)
    if category is None:
        raise StrategyArtifactValidationError(
            f"unsupported market context {market!r}; supported markets: {list(SUPPORTED_MARKETS)}"
        )
    if symbols and isinstance(symbols, Sequence) and not isinstance(symbols, (str, bytes)):
        for symbol in symbols:
            if isinstance(symbol, str):
                intrinsic = intrinsic_symbol_market(symbol)
                if intrinsic is not None and intrinsic != category:
                    raise StrategyArtifactValidationError(
                        f"symbol {symbol!r} intrinsic market {intrinsic!r} contradicts explicit market {category!r}"
                    )
    return category


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _mapping_checksum(payload: Mapping[str, Any], label: str) -> str:
    try:
        canonical = _canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise StrategyArtifactValidationError(
            f"{label} is not canonical JSON: {exc}"
        ) from exc
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def strategy_artifact_checksum(artifact: Mapping[str, Any]) -> str:
    """Return the deterministic checksum used by the generic registry entry."""
    return _mapping_checksum(artifact, "StrategyArtifact")


@lru_cache(maxsize=1)
def _strategy_artifact_schema() -> dict[str, Any]:
    schema = json.loads(STRATEGY_ARTIFACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    # SD §6.3: minute-unit inputs carry ``availability_lag_minutes``; the portable
    # contract must admit it (scoped here because the schema file is not a task artifact).
    try:
        input_props = schema["$defs"]["factor_rule_strategy_logic"]["properties"]["inputs"]["items"]["properties"]
    except KeyError:
        input_props = _find_input_properties(schema)
    input_props.setdefault("availability_lag_minutes", {"type": "integer", "minimum": 0})
    Draft7Validator.check_schema(schema)
    return schema


def _find_input_properties(node: Any) -> dict[str, Any]:
    if isinstance(node, dict):
        items = node.get("items")
        if (
            isinstance(items, dict)
            and "availability_lag_days" in (items.get("properties") or {})
        ):
            return items["properties"]
        for v in node.values():
            found = _find_input_properties(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_input_properties(v)
            if found is not None:
                return found
    return None  # type: ignore[return-value]


def _json_path(error: Any) -> str:
    if not error.absolute_path:
        return "$"
    return "$." + ".".join(str(part) for part in error.absolute_path)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    if not _is_number(value):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _decimal(value: Any, label: str) -> Decimal:
    if not _is_finite_number(value):
        raise StrategyArtifactValidationError(f"{label} must be a finite number")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise StrategyArtifactValidationError(f"{label} must be a finite number") from exc


def _validate_control_value(control: Mapping[str, Any], value: Any) -> None:
    key = str(control["parameter_key"])
    value_type = control["value_type"]
    if value_type == "integer":
        if not _is_integer(value):
            raise StrategyArtifactValidationError(
                f"parameter {key!r} requires an integer value"
            )
    elif value_type == "number":
        if not _is_number(value):
            raise StrategyArtifactValidationError(
                f"parameter {key!r} requires a numeric value"
            )
    else:  # Defensive guard; JSON Schema rejects this first.
        raise StrategyArtifactValidationError(
            f"parameter {key!r} declares unsupported value_type {value_type!r}"
        )

    numeric_value = _decimal(value, f"parameter {key!r}")
    allowed_range = control["allowed_range"]
    minimum = _decimal(allowed_range["min"], f"parameter {key!r} minimum")
    maximum = _decimal(allowed_range["max"], f"parameter {key!r} maximum")
    step = _decimal(control["step"], f"parameter {key!r} step")
    if minimum > maximum:
        raise StrategyArtifactValidationError(
            f"parameter {key!r} has minimum greater than maximum"
        )
    if step <= 0:
        raise StrategyArtifactValidationError(
            f"parameter {key!r} step must be greater than zero"
        )
    if numeric_value < minimum or numeric_value > maximum:
        raise StrategyArtifactValidationError(
            f"parameter {key!r} value {value!r} is outside "
            f"[{allowed_range['min']!r}, {allowed_range['max']!r}]"
        )
    step_count = (numeric_value - minimum) / step
    if step_count != step_count.to_integral_value():
        raise StrategyArtifactValidationError(
            f"parameter {key!r} value {value!r} is not aligned to step "
            f"{control['step']!r} from minimum {allowed_range['min']!r}"
        )


def _enforce_declared_bar_frequency(artifact: Mapping[str, Any]) -> None:
    """SD §6.1/§6.3: reject unknown frequencies; fail closed for unsupported minute logic.

    close_to_close_momentum consumes a raw ``closes`` sequence with no dataset
    binding or PIT stamps, so it can only run at the daily default until
    dataset-bound minute support exists.
    """
    params = artifact.get("parameters")
    if not isinstance(params, Mapping) or "bar_frequency" not in params:
        return
    freq = params["bar_frequency"]
    dataset_for_bar_frequency(freq)  # unknown/non-string -> fail closed
    logic = artifact.get("strategy_logic")
    kind = logic.get("kind") if isinstance(logic, Mapping) else None
    if freq != "1d" and kind == "close_to_close_momentum":
        raise StrategyArtifactValidationError(
            f"close_to_close_momentum does not support bar_frequency {freq!r}; "
            "minute evaluation requires dataset-bound PIT inputs (fail closed)"
        )


def validate_strategy_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate both the JSON Schema and mutation-specific invariants.

    JSON Schema makes the payload portable.  The semantic checks below close
    constraints that Draft-07 cannot express cleanly: a total/disjoint
    parameter partition, current-value equality, and step alignment.
    """
    if not isinstance(artifact, Mapping):
        raise StrategyArtifactValidationError("StrategyArtifact must be a JSON object")

    validator = Draft7Validator(
        _strategy_artifact_schema(),
        format_checker=FormatChecker(),
    )
    schema_errors = sorted(
        validator.iter_errors(dict(artifact)),
        key=lambda error: list(error.absolute_path),
    )
    raw_params = artifact.get("parameters")
    if isinstance(raw_params, Mapping):
        for param_key in raw_params:
            if FORBIDDEN_PARAMETER_KEY_RE.match(str(param_key)):
                raise StrategyArtifactValidationError(
                    f"StrategyArtifact schema validation failed: parameter key "
                    f"{param_key!r} is forbidden (matches prefer_.*_when_.*)"
                )
    raw_expr = artifact.get("expression_policy")
    if isinstance(raw_expr, Mapping) and isinstance(raw_expr.get("parameters"), Mapping):
        for expr_key in raw_expr["parameters"]:
            if FORBIDDEN_PARAMETER_KEY_RE.match(str(expr_key)):
                raise StrategyArtifactValidationError(
                    f"StrategyArtifact schema validation failed: "
                    f"expression_policy.parameters key {expr_key!r} is forbidden "
                    "(matches prefer_.*_when_.*)"
                )
    raw_surface = artifact.get("mutation_surface")
    if (
        isinstance(raw_surface, Mapping)
        and isinstance(raw_surface.get("controls"), Sequence)
        and not isinstance(raw_surface.get("controls"), (str, bytes))
    ):
        for control in raw_surface["controls"]:
            if isinstance(control, Mapping):
                ctrl_key = str(control.get("parameter_key") or "")
                if FORBIDDEN_PARAMETER_KEY_RE.match(ctrl_key):
                    raise StrategyArtifactValidationError(
                        f"StrategyArtifact schema validation failed: "
                        f"mutation_surface.controls parameter_key {ctrl_key!r} is "
                        "forbidden (matches prefer_.*_when_.*)"
                    )
                if ctrl_key in FORBIDDEN_MUTATION_CONTROL_KEYS:
                    raise StrategyArtifactValidationError(
                        f"StrategyArtifact schema validation failed: "
                        f"mutation_surface.controls must not declare forbidden control {ctrl_key!r}"
                    )

    raw_logic = artifact.get("strategy_logic")
    if isinstance(raw_logic, Mapping) and raw_logic.get("kind") == "factor_rule_v1":
        try:
            validate_factor_rule_logic(
                raw_logic,
                raw_params if isinstance(raw_params, Mapping) else {},
                raw_surface if isinstance(raw_surface, Mapping) else None,
            )
        except FactorRuleValidationError as exc:
            raise StrategyArtifactValidationError(str(exc)) from exc

    if schema_errors:
        detail = "; ".join(
            f"{_json_path(error)}: {error.message}" for error in schema_errors
        )
        raise StrategyArtifactValidationError(
            f"StrategyArtifact schema validation failed: {detail}"
        )

    _enforce_declared_bar_frequency(artifact)

    for identifier in ("artifact_id", "strategy_id"):
        value = artifact[identifier]
        if not value.strip() or value != value.strip():
            raise StrategyArtifactValidationError(
                f"{identifier} must be a canonical non-blank id"
            )

    algorithm_ref = artifact["algorithm_ref"]
    for field in (
        "repository",
        "path",
        "entrypoint",
        "signal_interface",
        "signal_schema_version",
        "logic_interpreter",
    ):
        value = algorithm_ref[field]
        if not value.strip() or value != value.strip():
            raise StrategyArtifactValidationError(
                f"algorithm_ref.{field} must be a canonical non-blank string"
            )
    algorithm_path = algorithm_ref["path"]
    if (
        algorithm_path.startswith("/")
        or "\\" in algorithm_path
        or any(part in {"", ".", ".."} for part in algorithm_path.split("/"))
    ):
        raise StrategyArtifactValidationError(
            "algorithm_ref.path must be a normalized repository-relative path"
        )
    for field in ("entrypoint", "signal_interface", "logic_interpreter"):
        if not _PYTHON_REF_RE.fullmatch(algorithm_ref[field]):
            raise StrategyArtifactValidationError(
                f"algorithm_ref.{field} must be a module:object Python reference"
            )

    parameters = artifact["parameters"]
    mutation_surface = artifact["mutation_surface"]
    controls = mutation_surface["controls"]
    immutable_parameters = mutation_surface["immutable_parameters"]

    for param_key in parameters:
        if FORBIDDEN_PARAMETER_KEY_RE.match(str(param_key)):
            raise StrategyArtifactValidationError(
                f"parameter key {param_key!r} is forbidden: fixed mapping keys "
                "matching 'prefer_.*_when_.*' are not allowed"
            )

    expression_policy = artifact.get("expression_policy")
    expression_params: Mapping[str, Any] = {}
    if expression_policy is not None:
        if not isinstance(expression_policy, Mapping):
            raise StrategyArtifactValidationError(
                "expression_policy must be a JSON object"
            )
        if expression_policy.get("version") != EXPRESSION_POLICY_VERSION:
            raise StrategyArtifactValidationError(
                f"expression_policy.version must be {EXPRESSION_POLICY_VERSION!r}"
            )
        if expression_policy.get("kind") not in {EXPRESSION_POLICY_KIND, "target_position_v1"}:
            raise StrategyArtifactValidationError(
                f"expression_policy.kind must be {EXPRESSION_POLICY_KIND!r}"
            )
        raw_expr_params = expression_policy.get("parameters")
        if not isinstance(raw_expr_params, Mapping):
            raise StrategyArtifactValidationError(
                "expression_policy.parameters must be a JSON object"
            )
        expression_params = raw_expr_params
        for expr_key in expression_params:
            if FORBIDDEN_PARAMETER_KEY_RE.match(str(expr_key)):
                raise StrategyArtifactValidationError(
                    f"expression_policy.parameters key {expr_key!r} is forbidden: "
                    "fixed mapping keys matching 'prefer_.*_when_.*' are not allowed"
                )
        allowed_expr_keys = set(_EXPRESSION_POLICY_REQUIRED_PARAM_KEYS)
        if expression_policy.get("kind") == "target_position_v1":
            if artifact["strategy_logic"]["kind"] != "persona_portfolio_v1":
                raise StrategyArtifactValidationError("target_position_v1 requires a portfolio envelope")
            allowed_expr_keys.add("rebalance_band_fraction")
            band = expression_params.get("rebalance_band_fraction")
            if not _is_finite_number(band) or not 0 <= band <= 1:
                raise StrategyArtifactValidationError("rebalance_band_fraction must be within [0, 1]")
            if expression_params.get("close_before_reverse") is not True:
                raise StrategyArtifactValidationError("target positions must close before reversing")
        unknown_expr_keys = set(expression_params) - allowed_expr_keys
        if unknown_expr_keys:
            raise StrategyArtifactValidationError(
                "expression_policy.parameters has unknown keys: "
                + ", ".join(sorted(str(k) for k in unknown_expr_keys))
            )
        missing_expr_keys = _EXPRESSION_POLICY_REQUIRED_PARAM_KEYS - set(expression_params)
        if missing_expr_keys:
            raise StrategyArtifactValidationError(
                "expression_policy.parameters is missing required keys: "
                + ", ".join(sorted(missing_expr_keys))
            )
        for bool_key in ("allow_pyramiding", "close_before_reverse"):
            if not isinstance(expression_params.get(bool_key), bool):
                raise StrategyArtifactValidationError(
                    f"expression_policy.parameters.{bool_key} must be a boolean"
                )
        for num_key in ("lever_min_strength", "notional_fraction_at_full_strength"):
            val = expression_params.get(num_key)
            if not _is_finite_number(val):
                raise StrategyArtifactValidationError(
                    f"expression_policy.parameters.{num_key} must be a finite number"
                )

    control_by_key: dict[str, Mapping[str, Any]] = {}
    for control in controls:
        key = control["parameter_key"]
        if FORBIDDEN_PARAMETER_KEY_RE.match(str(key)):
            raise StrategyArtifactValidationError(
                f"mutation_surface.controls parameter_key {key!r} is forbidden "
                "(matches prefer_.*_when_.*)"
            )
        if key in FORBIDDEN_MUTATION_CONTROL_KEYS:
            raise StrategyArtifactValidationError(
                f"mutation_surface.controls must not declare forbidden control {key!r}"
            )
        if key in control_by_key:
            raise StrategyArtifactValidationError(
                f"mutation_surface.controls repeats parameter {key!r}"
            )
        control_by_key[key] = control
        in_parameters = key in parameters
        in_expression_parameters = (
            expression_policy is not None
            and key in _EXPRESSION_POLICY_MUTABLE_PARAM_KEYS
            and key in expression_params
        )
        if not in_parameters and not in_expression_parameters:
            raise StrategyArtifactValidationError(
                f"mutable parameter {key!r} is absent from parameters"
            )
        if in_parameters and parameters[key] != control["current_value"]:
            raise StrategyArtifactValidationError(
                f"mutation control {key!r} current_value must equal parameters[{key!r}]"
            )
        if in_expression_parameters and expression_params[key] != control["current_value"]:
            raise StrategyArtifactValidationError(
                f"mutation control {key!r} current_value must equal "
                f"expression_policy.parameters[{key!r}]"
            )
        _validate_control_value(control, control["current_value"])
        if in_parameters:
            _validate_control_value(control, parameters[key])
        if in_expression_parameters:
            _validate_control_value(control, expression_params[key])

    strategy_logic_kind = (
        artifact["strategy_logic"].get("kind")
        if isinstance(artifact.get("strategy_logic"), Mapping)
        else None
    )
    if expression_policy is not None and strategy_logic_kind != "persona_portfolio_v1":
        required_expr_controls = (
            {
                k: v
                for k, v in REQUIRED_EXPRESSION_POLICY_CONTROLS.items()
                if k in _EXPRESSION_POLICY_MUTABLE_PARAM_KEYS or k in parameters
            }
            if strategy_logic_kind == "factor_rule_v1"
            else REQUIRED_EXPRESSION_POLICY_CONTROLS
        )
        for req_key, req_spec in required_expr_controls.items():
            if req_key not in control_by_key:
                raise StrategyArtifactValidationError(
                    f"expression_policy requires mutation_surface.controls to declare {req_key!r}"
                )
            ctrl = control_by_key[req_key]
            if ctrl.get("value_type") != req_spec["value_type"]:
                raise StrategyArtifactValidationError(
                    f"control {req_key!r} must have value_type {req_spec['value_type']!r}"
                )
            ctrl_range = ctrl.get("allowed_range") or {}
            if (
                _decimal(ctrl_range.get("min"), f"control {req_key!r} min")
                != _decimal(req_spec["allowed_range"]["min"], f"expected {req_key!r} min")
                or _decimal(ctrl_range.get("max"), f"control {req_key!r} max")
                != _decimal(req_spec["allowed_range"]["max"], f"expected {req_key!r} max")
                or _decimal(ctrl.get("step"), f"control {req_key!r} step")
                != _decimal(req_spec["step"], f"expected {req_key!r} step")
            ):
                raise StrategyArtifactValidationError(
                    f"control {req_key!r} must declare allowed_range "
                    f"{req_spec['allowed_range']} and step {req_spec['step']}"
                )
        if strategy_logic_kind != "factor_rule_v1":
            for view_key in ("strength_scale_k", "strength_floor_momentum"):
                if view_key not in parameters:
                    raise StrategyArtifactValidationError(
                        f"expression_policy requires {view_key!r} in parameters"
                    )

    mutable_keys = set(control_by_key)
    mutable_param_keys = {key for key in control_by_key if key in parameters}
    immutable_keys = set(immutable_parameters)
    overlap = mutable_keys & immutable_keys
    if overlap:
        raise StrategyArtifactValidationError(
            "parameters cannot be both mutable and immutable: "
            + ", ".join(sorted(overlap))
        )
    allowed_immutable_sources = set(parameters) | (
        {"allow_pyramiding", "close_before_reverse"}
        if expression_policy is not None
        else set()
    )
    unknown_immutable = immutable_keys - allowed_immutable_sources
    if unknown_immutable:
        raise StrategyArtifactValidationError(
            "immutable parameters are absent from parameters: "
            + ", ".join(sorted(unknown_immutable))
        )
    unclassified = set(parameters) - mutable_param_keys - immutable_keys
    if unclassified:
        raise StrategyArtifactValidationError(
            "every parameter must be classified as mutable or immutable; missing: "
            + ", ".join(sorted(unclassified))
        )

    for key, value in parameters.items():
        if _is_number(value) and not _is_finite_number(value):
            raise StrategyArtifactValidationError(
                f"parameter {key!r} must be a finite number"
            )

    market = parameters.get("market")
    if market is not None:
        if not isinstance(market, str):
            raise StrategyArtifactValidationError("parameter 'market' must be a string")
        canonical = canonical_market_context(
            market,
            symbols=parameters.get("symbols"),
        )
        if market != canonical:
            raise StrategyArtifactValidationError(
                f"parameter 'market' must be canonical {canonical!r}, got {market!r}"
            )

    lineage = artifact["lineage"]
    for field in (
        "parent_registry_ids",
        "source_run_ids",
        "source_dataset_refs",
    ):
        for value in lineage.get(field, []):
            if not value.strip() or value != value.strip():
                raise StrategyArtifactValidationError(
                    f"lineage.{field} must contain canonical non-blank ids"
                )
    source_strategy_spec_id = lineage.get("source_strategy_spec_id")
    if source_strategy_spec_id is not None and (
        not source_strategy_spec_id.strip()
        or source_strategy_spec_id != source_strategy_spec_id.strip()
    ):
        raise StrategyArtifactValidationError(
            "lineage.source_strategy_spec_id must be a canonical non-blank id"
        )

    strategy_logic = artifact["strategy_logic"]
    if strategy_logic["kind"] == "close_to_close_momentum":
        lookback_key = strategy_logic["lookback_parameter"]
        threshold_key = strategy_logic["threshold_parameter"]
        if lookback_key not in parameters or threshold_key not in parameters:
            raise StrategyArtifactValidationError(
                "strategy_logic parameter references must exist in parameters"
            )
        if not _is_integer(parameters[lookback_key]) or parameters[lookback_key] < 2:
            raise StrategyArtifactValidationError(
                f"lookback parameter {lookback_key!r} must be an integer >= 2"
            )
        if not _is_number(parameters[threshold_key]):
            raise StrategyArtifactValidationError(
                f"threshold parameter {threshold_key!r} must be numeric"
            )

        zero_action = parameters.get("zero_momentum_action")
        if zero_action is not None and zero_action != strategy_logic["non_positive_action"]:
            raise StrategyArtifactValidationError(
                "zero_momentum_action must match strategy_logic.non_positive_action"
            )
    elif strategy_logic["kind"] == "factor_rule_v1":
        try:
            validate_factor_rule_logic(strategy_logic, parameters, mutation_surface)
        except FactorRuleValidationError as exc:
            raise StrategyArtifactValidationError(str(exc)) from exc
    elif strategy_logic["kind"] == "persona_portfolio_v1":
        from .portfolio_envelope import validate_portfolio_envelope

        validate_portfolio_envelope(artifact)
    else:
        raise StrategyArtifactValidationError(
            f"unsupported strategy_logic kind: {strategy_logic.get('kind')!r}"
        )

    binding_intent = artifact.get("binding_intent") or {}
    for field, value in binding_intent.items():
        if field in {
            "observed_at",
            "observed_effective_at",
            "observed_deployment_mode",
        }:
            continue
        if isinstance(value, str) and (
            not value.strip() or value != value.strip()
        ):
            raise StrategyArtifactValidationError(
                f"binding_intent.{field} must be a canonical non-blank string"
            )
    for field in ("observed_at", "observed_effective_at"):
        value = binding_intent.get(field)
        if value is None:
            continue
        if not _RFC3339_RE.fullmatch(value):
            raise StrategyArtifactValidationError(
                f"binding_intent.{field} must be an RFC3339 date-time with timezone"
            )
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StrategyArtifactValidationError(
                f"binding_intent.{field} must be a valid RFC3339 date-time"
            ) from exc

    for value in artifact.get("provenance_refs", []):
        if not value.strip() or value != value.strip():
            raise StrategyArtifactValidationError(
                "provenance_refs must contain canonical non-blank references"
            )

    # Force canonical serialization here so NaN/Infinity hidden in nested
    # optional fields also fail closed.
    strategy_artifact_checksum(artifact)


def build_strategy_artifact_registry_payload(
    registration: Mapping[str, Any],
) -> tuple[str, RegistryEntryCreate]:
    """Map a StrategyArtifact registration request to the generic envelope."""
    artifact_value = registration.get("strategy_artifact")
    if not isinstance(artifact_value, Mapping):
        raise StrategyArtifactValidationError(
            "strategy_artifact registration payload is required"
        )
    artifact = copy.deepcopy(dict(artifact_value))
    validate_strategy_artifact(artifact)

    registry_id = str(
        registration.get("registry_id") or artifact.get("artifact_id") or ""
    ).strip()
    if not registry_id:
        raise StrategyArtifactValidationError("registry_id is required")
    if registry_id != artifact["artifact_id"]:
        raise StrategyArtifactValidationError(
            "registry_id must equal strategy_artifact.artifact_id"
        )

    try:
        artifact_state = ArtifactState(
            registration.get("artifact_state", ArtifactState.CANDIDATE.value)
        )
    except ValueError as exc:
        raise StrategyArtifactValidationError(
            f"unsupported artifact_state: {registration.get('artifact_state')!r}"
        ) from exc
    if artifact_state not in (ArtifactState.DRAFT, ArtifactState.CANDIDATE):
        raise StrategyArtifactValidationError(
            "StrategyArtifact registration can only create draft or candidate entries"
        )

    lineage = Lineage.from_dict(artifact["lineage"])
    if lineage.is_empty():
        raise StrategyArtifactValidationError("StrategyArtifact lineage must not be empty")

    metadata_value = registration.get("metadata")
    if metadata_value is None:
        metadata_value = {}
    elif not isinstance(metadata_value, Mapping):
        raise StrategyArtifactValidationError("metadata must be a JSON object")
    metadata = copy.deepcopy(dict(metadata_value))
    if STRATEGY_ARTIFACT_REGISTRATION_CHECKSUM_KEY in metadata:
        raise StrategyArtifactValidationError(
            f"metadata.{STRATEGY_ARTIFACT_REGISTRATION_CHECKSUM_KEY} is reserved"
        )
    embedded = metadata.get("strategy_artifact")
    if embedded is not None and embedded != artifact:
        raise StrategyArtifactValidationError(
            "metadata.strategy_artifact conflicts with strategy_artifact"
        )
    metadata["strategy_artifact"] = artifact

    evaluation_value = registration.get("evaluation_summary")
    if evaluation_value is None:
        evaluation_value = {}
    elif not isinstance(evaluation_value, Mapping):
        raise StrategyArtifactValidationError(
            "evaluation_summary must be a JSON object"
        )
    evaluation_summary = copy.deepcopy(dict(evaluation_value))
    evaluation_summary.setdefault(
        "strategy_artifact_schema_version", artifact["artifact_schema_version"]
    )
    evaluation_summary.setdefault(
        "mutable_parameter_count", len(artifact["mutation_surface"]["controls"])
    )

    source_run_ids = list(lineage.source_run_ids or [])
    producer_run_id = str(registration.get("producer_run_id") or "").strip()
    if not producer_run_id and source_run_ids:
        producer_run_id = source_run_ids[-1]

    rollback_target = registration.get("rollback_target")
    if rollback_target is not None:
        if not isinstance(rollback_target, str) or not rollback_target.strip():
            raise StrategyArtifactValidationError(
                "rollback_target must be a non-blank string when provided"
            )
        if rollback_target != rollback_target.strip():
            raise StrategyArtifactValidationError(
                "rollback_target must not have leading or trailing whitespace"
            )

    registration_identity = {
        "registry_id": registry_id,
        "artifact_state": artifact_state.value,
        "artifact_checksum": strategy_artifact_checksum(artifact),
        "producer_run_id": producer_run_id or None,
        "evaluation_summary": evaluation_summary,
        "rollback_target": rollback_target,
        "supplemental_metadata": {
            key: value
            for key, value in metadata.items()
            if key != "strategy_artifact"
        },
    }
    metadata[STRATEGY_ARTIFACT_REGISTRATION_CHECKSUM_KEY] = _mapping_checksum(
        registration_identity,
        "StrategyArtifact registration envelope",
    )

    payload = RegistryEntryCreate(
        artifact_type=ArtifactType.EXECUTION_BUNDLE,
        strategy_id=artifact["strategy_id"],
        version=artifact["version"],
        artifact_state=artifact_state,
        lineage=lineage,
        storage_ref=StorageRef(
            backend=StorageBackend.INLINE,
            path="$.entry.metadata.strategy_artifact",
        ),
        checksum=strategy_artifact_checksum(artifact),
        producer_run_id=producer_run_id or None,
        evaluation_summary=evaluation_summary,
        rollback_target=rollback_target,
        metadata=metadata,
    )
    return registry_id, payload


def load_strategy_artifact_registration(path: Path) -> dict[str, Any]:
    """Load a checked-in registration request and validate its mapping."""
    registration = json.loads(path.read_text(encoding="utf-8"))
    build_strategy_artifact_registry_payload(registration)
    return registration


def ensure_builtin_strategy_artifacts(
    registry_service: RegistryService,
) -> list[RegistryEntryView]:
    """Idempotently register checked-in v1 artifacts through RegistryService."""
    views: list[RegistryEntryView] = []
    for path in BUILTIN_STRATEGY_ARTIFACT_PATHS:
        registration = load_strategy_artifact_registration(path)
        registry_id, payload = build_strategy_artifact_registry_payload(registration)
        view, created = registry_service.register_if_absent(payload, registry_id)
        if created:
            views.append(view)
            continue

        existing = view.entry
        existing_artifact = (existing.metadata or {}).get("strategy_artifact")
        expected_artifact = (payload.metadata or {}).get("strategy_artifact")
        if (
            existing.artifact_type != ArtifactType.EXECUTION_BUNDLE
            or existing.checksum != payload.checksum
            or existing_artifact != expected_artifact
            or existing.strategy_id != payload.strategy_id
            or existing.version != payload.version
            or existing.lineage.to_dict() != payload.lineage.to_dict()
            or existing.storage_ref.to_dict() != payload.storage_ref.to_dict()
            or existing.producer_run_id != payload.producer_run_id
            or existing.evaluation_summary != payload.evaluation_summary
            or existing.rollback_target != payload.rollback_target
            or existing.metadata != payload.metadata
        ):
            raise StrategyArtifactValidationError(
                f"built-in StrategyArtifact id collision: {registry_id}"
            )
        views.append(view)
    return views


def mutate_strategy_artifact(
    parent: Mapping[str, Any],
    *,
    new_artifact_id: str,
    new_version: str,
    parameter_updates: Mapping[str, Any],
    source_run_ids: Sequence[str],
    parent_registry_id: str | None = None,
) -> dict[str, Any]:
    """Create a validated child artifact without mutating the parent object."""
    validate_strategy_artifact(parent)
    child = copy.deepcopy(dict(parent))

    new_artifact_id = str(new_artifact_id).strip()
    if not new_artifact_id or new_artifact_id == parent["artifact_id"]:
        raise StrategyArtifactValidationError(
            "a mutation requires a new, non-empty artifact_id"
        )
    try:
        parent_semver = tuple(int(part) for part in str(parent["version"]).split("."))
        child_semver = tuple(int(part) for part in str(new_version).split("."))
    except (TypeError, ValueError) as exc:
        raise StrategyArtifactValidationError(
            "mutation version must be semantic version x.y.z"
        ) from exc
    if len(child_semver) != 3 or child_semver <= parent_semver:
        raise StrategyArtifactValidationError(
            "mutation version must be greater than the parent semantic version"
        )

    if not isinstance(parameter_updates, Mapping) or not parameter_updates:
        raise StrategyArtifactValidationError(
            "parameter_updates must contain at least one mutation"
        )
    controls = {
        control["parameter_key"]: control
        for control in child["mutation_surface"]["controls"]
    }
    expression_policy = child.get("expression_policy")
    expression_params = (
        expression_policy.get("parameters")
        if isinstance(expression_policy, Mapping)
        and isinstance(expression_policy.get("parameters"), dict)
        else None
    )
    changed = False
    for key, value in parameter_updates.items():
        if FORBIDDEN_PARAMETER_KEY_RE.match(str(key)):
            raise StrategyArtifactValidationError(
                f"parameter key {key!r} is forbidden (matches prefer_.*_when_.*)"
            )
        if key in FORBIDDEN_MUTATION_CONTROL_KEYS:
            raise StrategyArtifactValidationError(
                f"parameter {key!r} is forbidden as a mutable control"
            )
        in_params = key in child["parameters"]
        in_expr_params = (
            expression_params is not None
            and key in _EXPRESSION_POLICY_MUTABLE_PARAM_KEYS
            and key in expression_params
        )
        if not in_params and not in_expr_params:
            if expression_params is not None and key in expression_params:
                raise StrategyArtifactValidationError(f"parameter {key!r} is immutable")
            raise StrategyArtifactValidationError(f"unknown parameter {key!r}")
        if key not in controls:
            raise StrategyArtifactValidationError(f"parameter {key!r} is immutable")
        _validate_control_value(controls[key], value)
        if in_params:
            if child["parameters"][key] != value:
                changed = True
            child["parameters"][key] = value
        if in_expr_params and expression_params is not None:
            if expression_params[key] != value:
                changed = True
            expression_params[key] = value
        controls[key]["current_value"] = value
    if not changed:
        raise StrategyArtifactValidationError(
            "mutation must change at least one parameter value"
        )

    if isinstance(source_run_ids, (str, bytes)):
        raise StrategyArtifactValidationError(
            "source_run_ids must be a sequence of ids, not a string"
        )
    normalized_source_run_ids: list[str] = []
    for source_run_id in source_run_ids:
        if not isinstance(source_run_id, str):
            raise StrategyArtifactValidationError(
                "source_run_ids must contain only string ids"
            )
        normalized = source_run_id.strip()
        if not normalized or normalized != source_run_id:
            raise StrategyArtifactValidationError(
                "source_run_ids must contain only canonical non-empty ids"
            )
        if normalized not in normalized_source_run_ids:
            normalized_source_run_ids.append(normalized)
    if not normalized_source_run_ids:
        raise StrategyArtifactValidationError(
            "a mutation requires at least one producing source_run_id"
        )

    direct_parent = str(parent_registry_id or parent["artifact_id"]).strip()
    if not direct_parent:
        raise StrategyArtifactValidationError("parent_registry_id must not be blank")

    child["artifact_id"] = new_artifact_id
    child["version"] = str(new_version)
    child_lineage = child["lineage"]
    child_lineage["parent_registry_ids"] = [direct_parent]
    child_lineage["source_run_ids"] = normalized_source_run_ids
    validate_strategy_artifact(child)
    return child


def create_market_transition_revision(
    parent: Mapping[str, Any],
    *,
    market: str,
    new_artifact_id: str,
    new_version: str,
    source_run_ids: Sequence[str],
    parent_registry_id: str | None = None,
) -> dict[str, Any]:
    """Produce a governed new revision of an immutable older artifact to add explicit market context.

    Preserves parent artifact bytes, checksum, version, and raw symbols membership without mutation.
    The new child revision inherits all parameters, controls, and lineage from parent,
    links parent_registry_ids to the parent, records source_run_ids, and sets explicit
    market validated against canonical_market_context(market, symbols=symbols).
    """
    validate_strategy_artifact(parent)
    child = copy.deepcopy(dict(parent))

    new_artifact_id = str(new_artifact_id).strip()
    if not new_artifact_id or new_artifact_id == parent["artifact_id"]:
        raise StrategyArtifactValidationError(
            "a market transition revision requires a new, non-empty artifact_id"
        )
    try:
        parent_semver = tuple(int(part) for part in str(parent["version"]).split("."))
        child_semver = tuple(int(part) for part in str(new_version).split("."))
    except (TypeError, ValueError) as exc:
        raise StrategyArtifactValidationError(
            "revision version must be semantic version x.y.z"
        ) from exc
    if len(child_semver) != 3 or child_semver <= parent_semver:
        raise StrategyArtifactValidationError(
            "revision version must be greater than the parent semantic version"
        )

    symbols = child["parameters"].get("symbols")
    canonical_market = canonical_market_context(market, symbols=symbols)

    if isinstance(source_run_ids, (str, bytes)):
        raise StrategyArtifactValidationError(
            "source_run_ids must be a sequence of ids, not a string"
        )
    normalized_source_run_ids: list[str] = []
    for source_run_id in source_run_ids:
        if not isinstance(source_run_id, str):
            raise StrategyArtifactValidationError(
                "source_run_ids must contain only string ids"
            )
        normalized = source_run_id.strip()
        if not normalized or normalized != source_run_id:
            raise StrategyArtifactValidationError(
                "source_run_ids must contain only canonical non-empty ids"
            )
        if normalized not in normalized_source_run_ids:
            normalized_source_run_ids.append(normalized)
    if not normalized_source_run_ids:
        raise StrategyArtifactValidationError(
            "a market transition revision requires at least one producing source_run_id"
        )

    direct_parent = str(parent_registry_id or parent["artifact_id"]).strip()
    if not direct_parent:
        raise StrategyArtifactValidationError("parent_registry_id must not be blank")

    child["artifact_id"] = new_artifact_id
    child["version"] = str(new_version)
    child_lineage = child["lineage"]
    child_lineage["parent_registry_ids"] = [direct_parent]
    child_lineage["source_run_ids"] = normalized_source_run_ids

    child["parameters"]["market"] = canonical_market
    immutable_params = child["mutation_surface"]["immutable_parameters"]
    if "market" not in immutable_params:
        immutable_params.append("market")

    validate_strategy_artifact(child)
    return child


def build_market_transition_registry_payload(
    parent_entry: Mapping[str, Any],
    *,
    market: str,
    new_artifact_id: str,
    new_version: str,
    source_run_ids: Sequence[str],
    producer_run_id: str | None = None,
    parent_registry_id: str | None = None,
) -> tuple[str, RegistryEntryCreate]:
    """Map a governed market transition revision to the generic registry envelope."""
    parent_metadata = parent_entry.get("metadata") if isinstance(parent_entry.get("metadata"), Mapping) else {}
    parent_artifact = parent_metadata.get("strategy_artifact")
    if not isinstance(parent_artifact, Mapping):
        raise StrategyArtifactValidationError(
            "parent entry is missing metadata.strategy_artifact"
        )
    direct_parent_id = str(
        parent_registry_id
        or parent_entry.get("registry_id")
        or parent_artifact.get("artifact_id")
        or ""
    ).strip()
    child_artifact = create_market_transition_revision(
        parent_artifact,
        market=market,
        new_artifact_id=new_artifact_id,
        new_version=new_version,
        source_run_ids=source_run_ids,
        parent_registry_id=direct_parent_id,
    )
    registration = {
        "registry_id": child_artifact["artifact_id"],
        "artifact_state": ArtifactState.CANDIDATE.value,
        "strategy_artifact": child_artifact,
        "producer_run_id": producer_run_id or (source_run_ids[-1] if source_run_ids else None),
        "rollback_target": direct_parent_id,
        "metadata": {
            "parent_registry_id": direct_parent_id,
            "transition_kind": "legacy_market_addition",
            "market": child_artifact["parameters"]["market"],
        },
    }
    return build_strategy_artifact_registry_payload(registration)


def evaluate_strategy_action(
    strategy_artifact: Mapping[str, Any],
    closes: Sequence[float] | None,
    *,
    features: Mapping[str, Any] | None = None,
    decision_time: Any = None,
    holding_bars: int | None = None,
    current_direction: str | None = None,
    symbol: str | None = None,
) -> str:
    """Interpret a StrategyArtifact rule (close_to_close_momentum or factor_rule_v1) deterministically."""
    validate_strategy_artifact(strategy_artifact)
    if closes is not None and isinstance(closes, (str, bytes)):
        raise StrategyArtifactValidationError("closes must be a numeric sequence")

    if strategy_artifact["strategy_logic"]["kind"] == "persona_portfolio_v1":
        from .portfolio_envelope import evaluate_portfolio_envelope

        result = evaluate_portfolio_envelope(
            strategy_artifact, closes, features=features,
            decision_time=decision_time, symbol=symbol,
            holding_bars=holding_bars, current_direction=current_direction,
        )
        return result["view"]["action"]

    logic = strategy_artifact["strategy_logic"]
    if logic["kind"] == "factor_rule_v1":
        try:
            if "screen" in logic:
                return evaluate_strategy_view(
                    strategy_artifact, closes, features=features, decision_time=decision_time,
                    holding_bars=holding_bars, current_direction=current_direction, symbol=symbol,
                )["action"]
            return evaluate_factor_rule_action(
                strategy_artifact,
                closes,
                features=features,
                decision_time=decision_time,
                holding_bars=holding_bars,
                current_direction=current_direction,
            )
        except FactorRuleValidationError as exc:
            raise StrategyArtifactValidationError(str(exc)) from exc

    if logic["kind"] != "close_to_close_momentum":
        raise StrategyArtifactValidationError(
            f"unsupported strategy_logic kind: {logic['kind']!r}"
        )
    if closes is None:
        raise StrategyArtifactValidationError("closes must be a numeric sequence")
    parameters = strategy_artifact["parameters"]
    lookback = parameters[logic["lookback_parameter"]]
    threshold = parameters[logic["threshold_parameter"]]
    if len(closes) < lookback:
        raise StrategyArtifactValidationError(
            f"close_to_close_momentum requires at least {lookback} closes"
        )

    anchor = closes[-lookback]
    latest = closes[-1]
    if not _is_number(anchor) or not _is_number(latest):
        raise StrategyArtifactValidationError("closes must contain numeric values")
    if not _is_finite_number(anchor) or not _is_finite_number(latest):
        raise StrategyArtifactValidationError("closes must contain finite values")
    anchor_decimal = _decimal(anchor, "momentum anchor close")
    latest_decimal = _decimal(latest, "momentum latest close")
    if anchor_decimal == 0:
        raise StrategyArtifactValidationError("momentum anchor close must not be zero")

    momentum = (latest_decimal - anchor_decimal) / anchor_decimal
    if momentum > _decimal(threshold, "momentum threshold"):
        return logic["positive_action"]
    return logic["non_positive_action"]


def evaluate_strategy_view(
    strategy_artifact: Mapping[str, Any],
    closes: Sequence[float] | None,
    *,
    features: Mapping[str, Any] | None = None,
    decision_time: Any = None,
    holding_bars: int | None = None,
    current_direction: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Interpret a StrategyArtifact view with optional strength for backtest and paper."""
    validate_strategy_artifact(strategy_artifact)
    if closes is not None and isinstance(closes, (str, bytes)):
        raise StrategyArtifactValidationError("closes must be a numeric sequence")

    if strategy_artifact["strategy_logic"]["kind"] == "persona_portfolio_v1":
        from .portfolio_envelope import evaluate_portfolio_envelope

        result = evaluate_portfolio_envelope(
            strategy_artifact, closes, features=features,
            decision_time=decision_time, symbol=symbol,
            holding_bars=holding_bars, current_direction=current_direction,
        )
        return result["view"]

    logic = strategy_artifact["strategy_logic"]
    if logic["kind"] == "factor_rule_v1":
        try:
            if "screen" in logic:
                from .candidate_pool import MARKET_FEATURE_KEY, evaluate_screened_view

                if not symbol:
                    raise FactorRuleValidationError("screen requires the decision symbol")
                return evaluate_screened_view(
                    strategy_artifact, (features or {}).get(MARKET_FEATURE_KEY), symbol,
                    decision_time, holding_bars=holding_bars, current_direction=current_direction,
                )
            return evaluate_factor_rule_view(
                strategy_artifact,
                closes,
                features=features,
                decision_time=decision_time,
                holding_bars=holding_bars,
                current_direction=current_direction,
            )
        except FactorRuleValidationError as exc:
            raise StrategyArtifactValidationError(str(exc)) from exc

    if logic["kind"] != "close_to_close_momentum":
        raise StrategyArtifactValidationError(
            f"unsupported strategy_logic kind: {logic['kind']!r}"
        )
    if closes is None:
        raise StrategyArtifactValidationError("closes must be a numeric sequence")
    parameters = strategy_artifact["parameters"]
    lookback = parameters[logic["lookback_parameter"]]
    threshold = parameters[logic["threshold_parameter"]]
    if len(closes) < lookback:
        raise StrategyArtifactValidationError(
            f"close_to_close_momentum requires at least {lookback} closes"
        )

    anchor = closes[-lookback]
    latest = closes[-1]
    if not _is_number(anchor) or not _is_number(latest):
        raise StrategyArtifactValidationError("closes must contain numeric values")
    if not _is_finite_number(anchor) or not _is_finite_number(latest):
        raise StrategyArtifactValidationError("closes must contain finite values")
    anchor_decimal = _decimal(anchor, "momentum anchor close")
    latest_decimal = _decimal(latest, "momentum latest close")
    if anchor_decimal == 0:
        raise StrategyArtifactValidationError("momentum anchor close must not be zero")

    momentum_decimal = (latest_decimal - anchor_decimal) / anchor_decimal
    threshold_decimal = _decimal(threshold, "momentum threshold")
    if momentum_decimal > threshold_decimal:
        action = logic["positive_action"]
    else:
        action = logic["non_positive_action"]

    if action == "SELL":
        direction = "SHORT"
    elif action == "BUY":
        direction = "LONG"
    else:
        direction = "FLAT"

    strength: float | None = None
    if strategy_artifact.get("expression_policy") is not None:
        scale_k = _decimal(
            parameters["strength_scale_k"],
            "parameter 'strength_scale_k'",
        )
        floor_momentum = _decimal(
            parameters["strength_floor_momentum"],
            "parameter 'strength_floor_momentum'",
        )
        denom = scale_k * max(threshold_decimal, floor_momentum)
        if denom <= 0:
            raise StrategyArtifactValidationError(
                "strength denominator must be greater than zero"
            )
        raw_strength = min(Decimal("1"), abs(momentum_decimal) / denom)
        strength = float(raw_strength)

    return {
        "action": action,
        "direction": direction,
        "momentum": float(momentum_decimal),
        "strength": strength,
    }


# ---------------------------------------------------------------------------
# Source -> Spec -> execution recipe carriage
# ---------------------------------------------------------------------------

EXECUTION_RECIPE_KIND = "close_to_close_momentum"
_EXECUTION_RECIPE_KEYS: frozenset[str] = frozenset({
    "kind",
    "symbols",
    "bar_frequency",
    "data_source",
    "lookback_bars",
    "momentum_threshold",
    "positive_action",
    "non_positive_action",
    "order_quantity",
    "quantity_type",
})
# SD_INTRADAY_MINUTE_BARS §6.1: the only governed bar frequencies and the
# dataset each one reads.  No other value is accepted and no fallback exists.
BAR_FREQUENCY_DATASETS: Mapping[str, str] = MappingProxyType({
    "1d": "tw_price_daily",
    "1m": "tw_price_1m",
    "5m": "tw_price_5m",
    "15m": "tw_price_15m",
    "30m": "tw_price_30m",
    "60m": "tw_price_60m",
})
ALLOWED_BAR_FREQUENCIES: tuple[str, ...] = tuple(BAR_FREQUENCY_DATASETS)


def dataset_for_bar_frequency(bar_frequency: Any) -> str:
    """Map a declared ``bar_frequency`` to its governed dataset or fail closed."""
    if not isinstance(bar_frequency, str) or bar_frequency not in BAR_FREQUENCY_DATASETS:
        raise StrategyArtifactValidationError(
            f"bar_frequency {bar_frequency!r} is not allowed; "
            f"allowed: {', '.join(ALLOWED_BAR_FREQUENCIES)}"
        )
    return BAR_FREQUENCY_DATASETS[bar_frequency]


_EXECUTION_RECIPE_ACTIONS = ("BUY", "SELL", "HOLD")
_EXECUTION_RECIPE_QUANTITY_TYPES = ("SHARES", "CASH_VALUE", "PERCENT_PORTFOLIO")


def validate_execution_recipe(recipe: Any) -> dict[str, Any]:
    """Validate an explicit close_to_close_momentum recipe and return a copy.

    Nothing is defaulted or parsed from prose: every key must be present with
    the declared type, and unsupported kinds are rejected by name.
    """
    if not isinstance(recipe, Mapping):
        raise StrategyArtifactValidationError("execution_recipe must be a JSON object")
    kind = recipe.get("kind")
    if kind != EXECUTION_RECIPE_KIND:
        raise StrategyArtifactValidationError(
            f"unsupported execution_recipe kind {kind!r}; only "
            f"{EXECUTION_RECIPE_KIND!r} is supported"
        )
    missing = sorted(_EXECUTION_RECIPE_KEYS - set(recipe))
    if missing:
        raise StrategyArtifactValidationError(
            "execution_recipe is missing explicit fields: " + ", ".join(missing)
        )
    unknown = sorted(set(recipe) - _EXECUTION_RECIPE_KEYS)
    if unknown:
        raise StrategyArtifactValidationError(
            "execution_recipe has unsupported fields: " + ", ".join(unknown)
        )
    symbols = recipe["symbols"]
    if (
        isinstance(symbols, (str, bytes))
        or not isinstance(symbols, Sequence)
        or not symbols
        or any(not isinstance(s, str) or not s.strip() or s != s.strip() for s in symbols)
        or len(set(symbols)) != len(symbols)
    ):
        raise StrategyArtifactValidationError(
            "execution_recipe.symbols must be a non-empty list of unique canonical symbols"
        )
    for field in ("bar_frequency", "data_source"):
        value = recipe[field]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise StrategyArtifactValidationError(
                f"execution_recipe.{field} must be a canonical non-blank string"
            )
    dataset_for_bar_frequency(recipe["bar_frequency"])
    lookback = recipe["lookback_bars"]
    if not _is_integer(lookback) or lookback < 2:
        raise StrategyArtifactValidationError(
            "execution_recipe.lookback_bars must be an integer >= 2"
        )
    for field in ("momentum_threshold", "order_quantity"):
        value = recipe[field]
        if not _is_number(value) or not _is_finite_number(value):
            raise StrategyArtifactValidationError(
                f"execution_recipe.{field} must be a finite number"
            )
    if recipe["order_quantity"] <= 0:
        raise StrategyArtifactValidationError(
            "execution_recipe.order_quantity must be positive"
        )
    for field in ("positive_action", "non_positive_action"):
        if recipe[field] not in _EXECUTION_RECIPE_ACTIONS:
            raise StrategyArtifactValidationError(
                f"execution_recipe.{field} must be one of "
                + "/".join(_EXECUTION_RECIPE_ACTIONS)
            )
    if recipe["quantity_type"] not in _EXECUTION_RECIPE_QUANTITY_TYPES:
        raise StrategyArtifactValidationError(
            "execution_recipe.quantity_type must be one of "
            + "/".join(_EXECUTION_RECIPE_QUANTITY_TYPES)
        )
    return copy.deepcopy(dict(recipe))


def validate_execution_recipe_against_spec(
    recipe: Any,
    strategy_spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Require the recipe to agree with the actual StrategySpec payload."""
    checked = validate_execution_recipe(recipe)
    if not isinstance(strategy_spec, Mapping):
        raise StrategyArtifactValidationError("strategy_spec must be a JSON object")
    scope = strategy_spec.get("market_scope") or {}
    profile = strategy_spec.get("execution_profile") or {}
    if list(scope.get("symbols") or []) != list(checked["symbols"]):
        raise StrategyArtifactValidationError(
            "execution_recipe.symbols contradict StrategySpec market_scope.symbols: "
            f"{checked['symbols']!r} != {scope.get('symbols')!r}"
        )
    if scope.get("frequency") != checked["bar_frequency"]:
        raise StrategyArtifactValidationError(
            "execution_recipe.bar_frequency contradicts StrategySpec market_scope.frequency: "
            f"{checked['bar_frequency']!r} != {scope.get('frequency')!r}"
        )
    refs = {
        dep.get("ref")
        for dep in strategy_spec.get("data_dependencies") or []
        if isinstance(dep, Mapping)
    }
    if checked["data_source"] not in refs:
        raise StrategyArtifactValidationError(
            "execution_recipe.data_source is not a StrategySpec data dependency: "
            f"{checked['data_source']!r}"
        )
    if profile.get("quantity_type") != checked["quantity_type"]:
        raise StrategyArtifactValidationError(
            "execution_recipe.quantity_type contradicts StrategySpec "
            f"execution_profile.quantity_type: {checked['quantity_type']!r} != "
            f"{profile.get('quantity_type')!r}"
        )
    return checked


def strategy_spec_payload_checksum(payload: Mapping[str, Any]) -> str:
    """Checksum a StrategySpec payload exactly as Source->Spec conversion does."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def build_execution_artifact_registration(
    spec_entry: Mapping[str, Any],
    *,
    artifact_id: str,
    algorithm_commit: str,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """Derive a distinct execution StrategyArtifact registration from a Spec.

    ``spec_entry`` is the owner-read-back registry entry of the Spec.  The
    recipe, symbols, versions and lineage are taken from its stored values; the
    Spec's own registry id, checksum and version are left untouched and the
    result is linked only through ``lineage.source_strategy_spec_id``.
    """
    if not isinstance(spec_entry, Mapping):
        raise StrategyArtifactValidationError("spec_entry must be a JSON object")
    metadata = spec_entry.get("metadata")
    spec = metadata.get("strategy_spec") if isinstance(metadata, Mapping) else None
    if not isinstance(spec, Mapping):
        raise StrategyArtifactValidationError(
            "spec_entry is missing metadata.strategy_spec"
        )
    spec_registry_id = str(spec_entry.get("registry_id") or "").strip()
    if not spec_registry_id:
        raise StrategyArtifactValidationError("spec_entry is missing registry_id")
    stored_checksum = spec_entry.get("checksum")
    if stored_checksum != strategy_spec_payload_checksum(spec):
        raise StrategyArtifactValidationError(
            "spec_entry checksum does not match its stored strategy_spec payload"
        )
    artifact_id = str(artifact_id or "").strip()
    if not artifact_id or artifact_id == spec_registry_id:
        raise StrategyArtifactValidationError(
            "execution artifact requires a distinct non-empty artifact_id"
        )
    spec_metadata = spec.get("metadata")
    recipe_value = (
        spec_metadata.get("execution_recipe")
        if isinstance(spec_metadata, Mapping)
        else None
    )
    if recipe_value is None:
        raise StrategyArtifactValidationError(
            "StrategySpec has no metadata.execution_recipe; it is research-only "
            "and not executable"
        )
    recipe = validate_execution_recipe_against_spec(recipe_value, spec)

    strategy_id = str(spec.get("strategy_id") or "").strip()
    version = str(spec_entry.get("version") or "").strip()
    if not strategy_id or not version:
        raise StrategyArtifactValidationError(
            "spec_entry must carry the original strategy_id and version"
        )
    entry_lineage = spec_entry.get("lineage") if isinstance(spec_entry.get("lineage"), Mapping) else {}
    source_run_ids = [
        str(v) for v in entry_lineage.get("source_run_ids") or [] if str(v).strip()
    ]
    if not source_run_ids:
        raise StrategyArtifactValidationError(
            "spec_entry lineage.source_run_ids is required to keep run lineage"
        )
    dataset_refs = [
        str(v) for v in entry_lineage.get("source_dataset_refs") or [] if str(v).strip()
    ]
    lineage: dict[str, Any] = {
        "source_run_ids": source_run_ids,
        "source_strategy_spec_id": spec_registry_id,
    }
    if dataset_refs:
        lineage["source_dataset_refs"] = dataset_refs

    lookback = recipe["lookback_bars"]
    artifact = {
        "artifact_schema_version": "1.0",
        "artifact_id": artifact_id,
        "strategy_id": strategy_id,
        "version": version,
        "algorithm_ref": {
            "engine": "lean",
            "repository": "ajoe734/pantheon",
            "commit": algorithm_commit,
            "path": "services/registry/strategy_artifact.py",
            "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
            "signal_interface": (
                "services.execution.lean_runtime.paper_signal_producer:Strategy"
            ),
            "signal_schema_version": "1.0",
            "logic_interpreter": (
                "services.registry.strategy_artifact:evaluate_strategy_action"
            ),
        },
        "strategy_logic": {
            "kind": EXECUTION_RECIPE_KIND,
            "lookback_parameter": "lookback_bars",
            "threshold_parameter": "momentum_threshold",
            "positive_action": recipe["positive_action"],
            "non_positive_action": recipe["non_positive_action"],
        },
        "parameters": {
            "symbols": list(recipe["symbols"]),
            "bar_frequency": recipe["bar_frequency"],
            "data_source": recipe["data_source"],
            "lookback_bars": lookback,
            "momentum_threshold": recipe["momentum_threshold"],
            "order_quantity": recipe["order_quantity"],
            "quantity_type": recipe["quantity_type"],
            "zero_momentum_action": recipe["non_positive_action"],
        },
        "mutation_surface": {
            "controls": [
                {
                    "parameter_key": "lookback_bars",
                    "value_type": "integer",
                    "current_value": lookback,
                    "allowed_range": {"min": 2, "max": max(60, lookback)},
                    "step": 1,
                }
            ],
            "immutable_parameters": [
                "symbols",
                "bar_frequency",
                "data_source",
                "momentum_threshold",
                "order_quantity",
                "quantity_type",
                "zero_momentum_action",
            ],
        },
        "lineage": lineage,
        "provenance_refs": [f"strategy-spec:{spec_registry_id}"],
    }
    validate_strategy_artifact(artifact)
    entry_metadata: dict[str, Any] = {
        "source_strategy_spec_registry_id": spec_registry_id,
        "source_strategy_spec_checksum": stored_checksum,
        "execution_recipe_kind": EXECUTION_RECIPE_KIND,
    }
    if requested_by:
        entry_metadata["requested_by"] = requested_by
    return {
        "registry_id": artifact_id,
        "artifact_state": ArtifactState.CANDIDATE.value,
        "strategy_artifact": artifact,
        "producer_run_id": source_run_ids[-1],
        "metadata": entry_metadata,
    }

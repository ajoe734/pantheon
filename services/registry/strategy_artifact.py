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
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator, FormatChecker

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


class StrategyArtifactValidationError(RegistryError):
    """Raised when a StrategyArtifact violates schema or semantic rules."""


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def strategy_artifact_checksum(artifact: Mapping[str, Any]) -> str:
    """Return the deterministic checksum used by the generic registry entry."""
    try:
        canonical = _canonical_json(artifact)
    except (TypeError, ValueError) as exc:
        raise StrategyArtifactValidationError(
            f"StrategyArtifact is not canonical JSON: {exc}"
        ) from exc
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


@lru_cache(maxsize=1)
def _strategy_artifact_schema() -> dict[str, Any]:
    schema = json.loads(STRATEGY_ARTIFACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return schema


def _json_path(error: Any) -> str:
    if not error.absolute_path:
        return "$"
    return "$." + ".".join(str(part) for part in error.absolute_path)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _decimal(value: Any, label: str) -> Decimal:
    if not _is_number(value) or not math.isfinite(float(value)):
        raise StrategyArtifactValidationError(f"{label} must be a finite number")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
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
    if schema_errors:
        detail = "; ".join(
            f"{_json_path(error)}: {error.message}" for error in schema_errors
        )
        raise StrategyArtifactValidationError(
            f"StrategyArtifact schema validation failed: {detail}"
        )

    for identifier in ("artifact_id", "strategy_id"):
        if not str(artifact[identifier]).strip():
            raise StrategyArtifactValidationError(f"{identifier} must not be blank")

    parameters = artifact["parameters"]
    mutation_surface = artifact["mutation_surface"]
    controls = mutation_surface["controls"]
    immutable_parameters = mutation_surface["immutable_parameters"]

    control_by_key: dict[str, Mapping[str, Any]] = {}
    for control in controls:
        key = control["parameter_key"]
        if key in control_by_key:
            raise StrategyArtifactValidationError(
                f"mutation_surface.controls repeats parameter {key!r}"
            )
        control_by_key[key] = control
        if key not in parameters:
            raise StrategyArtifactValidationError(
                f"mutable parameter {key!r} is absent from parameters"
            )
        if parameters[key] != control["current_value"]:
            raise StrategyArtifactValidationError(
                f"mutation control {key!r} current_value must equal parameters[{key!r}]"
            )
        _validate_control_value(control, control["current_value"])

    mutable_keys = set(control_by_key)
    immutable_keys = set(immutable_parameters)
    overlap = mutable_keys & immutable_keys
    if overlap:
        raise StrategyArtifactValidationError(
            "parameters cannot be both mutable and immutable: "
            + ", ".join(sorted(overlap))
        )
    unknown_immutable = immutable_keys - set(parameters)
    if unknown_immutable:
        raise StrategyArtifactValidationError(
            "immutable parameters are absent from parameters: "
            + ", ".join(sorted(unknown_immutable))
        )
    unclassified = set(parameters) - mutable_keys - immutable_keys
    if unclassified:
        raise StrategyArtifactValidationError(
            "every parameter must be classified as mutable or immutable; missing: "
            + ", ".join(sorted(unclassified))
        )

    for key, value in parameters.items():
        if _is_number(value) and not math.isfinite(float(value)):
            raise StrategyArtifactValidationError(
                f"parameter {key!r} must be a finite number"
            )

    strategy_logic = artifact["strategy_logic"]
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

    metadata_value = registration.get("metadata") or {}
    if not isinstance(metadata_value, Mapping):
        raise StrategyArtifactValidationError("metadata must be a JSON object")
    metadata = copy.deepcopy(dict(metadata_value))
    embedded = metadata.get("strategy_artifact")
    if embedded is not None and embedded != artifact:
        raise StrategyArtifactValidationError(
            "metadata.strategy_artifact conflicts with strategy_artifact"
        )
    metadata["strategy_artifact"] = artifact

    evaluation_value = registration.get("evaluation_summary") or {}
    if not isinstance(evaluation_value, Mapping):
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
        rollback_target=registration.get("rollback_target"),
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
        existing = registry_service.store.get(registry_id)
        if existing is None:
            views.append(registry_service.register(payload, registry_id))
            continue

        existing_artifact = (existing.metadata or {}).get("strategy_artifact")
        expected_artifact = (payload.metadata or {}).get("strategy_artifact")
        if (
            existing.artifact_type != ArtifactType.EXECUTION_BUNDLE
            or existing.checksum != payload.checksum
            or existing_artifact != expected_artifact
        ):
            raise StrategyArtifactValidationError(
                f"built-in StrategyArtifact id collision: {registry_id}"
            )
        views.append(registry_service.get(registry_id))
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
    changed = False
    for key, value in parameter_updates.items():
        if key not in child["parameters"]:
            raise StrategyArtifactValidationError(f"unknown parameter {key!r}")
        if key not in controls:
            raise StrategyArtifactValidationError(f"parameter {key!r} is immutable")
        _validate_control_value(controls[key], value)
        if child["parameters"][key] != value:
            changed = True
        child["parameters"][key] = value
        controls[key]["current_value"] = value
    if not changed:
        raise StrategyArtifactValidationError(
            "mutation must change at least one parameter value"
        )

    normalized_source_run_ids: list[str] = []
    for source_run_id in source_run_ids:
        normalized = str(source_run_id).strip()
        if not normalized:
            raise StrategyArtifactValidationError(
                "source_run_ids must contain only non-empty ids"
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


def evaluate_strategy_action(
    strategy_artifact: Mapping[str, Any],
    closes: Sequence[float],
) -> str:
    """Interpret the v1 close-to-close momentum rule deterministically."""
    validate_strategy_artifact(strategy_artifact)
    if isinstance(closes, (str, bytes)):
        raise StrategyArtifactValidationError("closes must be a numeric sequence")

    logic = strategy_artifact["strategy_logic"]
    if logic["kind"] != "close_to_close_momentum":
        raise StrategyArtifactValidationError(
            f"unsupported strategy_logic kind: {logic['kind']!r}"
        )
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
    if not math.isfinite(float(anchor)) or not math.isfinite(float(latest)):
        raise StrategyArtifactValidationError("closes must contain finite values")
    if float(anchor) == 0.0:
        raise StrategyArtifactValidationError("momentum anchor close must not be zero")

    momentum = float(latest) / float(anchor) - 1.0
    if momentum > float(threshold):
        return logic["positive_action"]
    return logic["non_positive_action"]

"""AlphaTemplate (``alpha_template.v1``) validation, evaluation and registry mapping.

An AlphaTemplate is a reusable factor expression stored in the registry as an
entry of kind ``alpha_template`` (development plan §3.3).  Its ``expression`` is
the ``signal`` sub-tree of ``factor_rule_v1`` (``factor_rule.py``).  Unlike a
``StrategyArtifact`` a template may carry literal numeric constants (``{"const":
x}``) and, when ``scope == "cross_sectional"``, cross-sectional operators.

The per-symbol evaluator refuses every cross-sectional template.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator

from .factor_rule import (
    CROSS_SECTIONAL_OPERATORS,
    FactorRuleValidationError,
    _eval_node_series,
    _validate_window_And_param_constraints,
    collect_expression_refs_and_params,
    get_feature_definition,
    parse_factor_expression,
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
ALPHA_TEMPLATE_SCHEMA_PATH = (
    _REGISTRY_DIR.parent.parent / "docs" / "contracts" / "alpha_template.v1.schema.json"
)
ALPHA_TEMPLATE_LIBRARY_DIR = _REGISTRY_DIR / "alpha_templates"
ALPHA_TEMPLATE_LIBRARY_FILES: tuple[str, ...] = (
    "qlib_alpha158.v1.json",
    "alpha101_time_series.v1.json",
    "alpha101_cross_sectional.v1.json",
)
ALPHA_TEMPLATE_SCHEMA_VERSION = "alpha_template.v1"
ALPHA_TEMPLATE_REGISTRY_VERSION = "1.0.0"
ALPHA_TEMPLATE_METADATA_KEY = "alpha_template"

_RECORD_KEYS = (
    "schema_version",
    "template_id",
    "name",
    "family",
    "expression",
    "inputs",
    "parameters",
    "scope",
    "origin",
    "licence",
    "attribution",
    "status",
    "evaluation_ref",
)


class AlphaTemplateValidationError(ValueError):
    """Raised when an AlphaTemplate record is invalid or cannot be evaluated."""


@lru_cache(maxsize=1)
def _schema_validator() -> Draft7Validator:
    schema = json.loads(ALPHA_TEMPLATE_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft7Validator(schema)


def _expression_has_cross_sectional_op(node: Any) -> bool:
    if not isinstance(node, Mapping):
        return False
    if node.get("op") in CROSS_SECTIONAL_OPERATORS:
        return True
    return any(_expression_has_cross_sectional_op(a) for a in node.get("args", ()) or ())


def validate_alpha_template(template: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an ``alpha_template.v1`` record and return a normalised deep copy."""
    if not isinstance(template, Mapping):
        raise AlphaTemplateValidationError("alpha template must be a JSON object")
    errors = sorted(
        _schema_validator().iter_errors(template), key=lambda e: list(e.absolute_path)
    )
    if errors:
        e = errors[0]
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        raise AlphaTemplateValidationError(f"alpha template schema violation at {loc}: {e.message}")

    record = copy.deepcopy(dict(template))
    scope = record["scope"]
    parameters = record["parameters"]
    try:
        parsed = parse_factor_expression(
            record["expression"],
            allow_cross_sectional=(scope == "cross_sectional"),
            allow_literal_constants=True,
        )
        _validate_window_And_param_constraints(parsed, parameters)
        refs, const_params = collect_expression_refs_and_params(parsed)
    except FactorRuleValidationError as exc:
        raise AlphaTemplateValidationError(f"expression invalid: {exc}") from exc

    has_cs = _expression_has_cross_sectional_op(parsed)
    if scope == "cross_sectional" and not has_cs:
        raise AlphaTemplateValidationError(
            "cross_sectional template must use at least one cross-sectional operator"
        )
    if scope == "time_series" and has_cs:  # pragma: no cover - parser already refuses
        raise AlphaTemplateValidationError("time_series template cannot use cross-sectional operators")

    declared: dict[str, dict[str, Any]] = {}
    for inp in record["inputs"]:
        if inp["name"] in declared:
            raise AlphaTemplateValidationError(f"duplicate input name {inp['name']!r}")
        definition = get_feature_definition(inp["dataset"], inp["field"])
        if definition is None:
            raise AlphaTemplateValidationError(
                f"input {inp['name']!r} references ungoverned feature {inp['dataset']}.{inp['field']}"
            )
        if inp["availability_lag_days"] < definition.min_availability_lag_days:
            raise AlphaTemplateValidationError(
                f"input {inp['name']!r} availability_lag_days below governed minimum "
                f"{definition.min_availability_lag_days}"
            )
        declared[inp["name"]] = inp
    if refs != set(declared):
        raise AlphaTemplateValidationError(
            f"expression refs {sorted(refs)} must equal declared inputs {sorted(declared)}"
        )
    missing = sorted(const_params - set(parameters))
    if missing:
        raise AlphaTemplateValidationError(f"expression uses undeclared parameters {missing}")
    unused = sorted(set(parameters) - const_params)
    if unused:
        raise AlphaTemplateValidationError(f"parameters declared but unused: {unused}")
    record["expression"] = parsed
    return record


def evaluate_alpha_template_series(
    template: Mapping[str, Any],
    env: Mapping[str, Sequence[float]],
    parameters: Mapping[str, Any] | None = None,
) -> list[float]:
    """Per-symbol evaluation of a ``time_series`` template; returns the full series.

    ``env`` maps input names to chronological series (oldest first). Entries
    before the warm-up window are ``nan``.  Cross-sectional templates are refused.
    """
    if template.get("scope") != "time_series":
        raise AlphaTemplateValidationError(
            f"template {template.get('template_id')!r} has scope {template.get('scope')!r}; "
            "the per-symbol evaluator refuses cross-sectional templates"
        )
    record = validate_alpha_template(template)
    params = dict(record["parameters"])
    if parameters:
        params.update(parameters)
    tuple_env = {k: tuple(float(v) for v in seq) for k, seq in env.items()}
    try:
        series, _ = _eval_node_series(record["expression"], tuple_env, params)
    except FactorRuleValidationError as exc:
        raise AlphaTemplateValidationError(str(exc)) from exc
    return series


def evaluate_alpha_template(
    template: Mapping[str, Any],
    env: Mapping[str, Sequence[float]],
    parameters: Mapping[str, Any] | None = None,
) -> float:
    """Return the latest finite value of a ``time_series`` template."""
    series = evaluate_alpha_template_series(template, env, parameters)
    if not series or not math.isfinite(series[-1]):
        raise AlphaTemplateValidationError(
            "alpha template requires sufficient history to produce a finite value"
        )
    return float(series[-1])


def alpha_template_checksum(template: Mapping[str, Any]) -> str:
    raw = json.dumps(template, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def alpha_template_registry_id(template_id: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in template_id).strip("-").lower()
    digest = hashlib.sha256(template_id.encode("utf-8")).hexdigest()[:8]
    return f"reg-alpha-template-{safe}-{digest}"


def build_alpha_template_registry_payload(
    template: Mapping[str, Any],
    *,
    version: str = ALPHA_TEMPLATE_REGISTRY_VERSION,
    extra_metadata: Mapping[str, Any] | None = None,
) -> tuple[str, RegistryEntryCreate]:
    """Map a validated template to ``(registry_id, RegistryEntryCreate)``."""
    record = validate_alpha_template(template)
    metadata = dict(extra_metadata or {})
    metadata[ALPHA_TEMPLATE_METADATA_KEY] = record
    payload = RegistryEntryCreate(
        artifact_type=ArtifactType.ALPHA_TEMPLATE,
        strategy_id=record["template_id"],
        version=version,
        artifact_state=ArtifactState.CANDIDATE,
        lineage=Lineage(source_dataset_refs=[record["origin"]["library"] + ":" + record["origin"]["ref"]]),
        storage_ref=StorageRef(
            backend=StorageBackend.INLINE,
            path=f"$.entry.metadata.{ALPHA_TEMPLATE_METADATA_KEY}",
        ),
        checksum=alpha_template_checksum(record),
        evaluation_summary={
            "template_id": record["template_id"],
            "family": record["family"],
            "scope": record["scope"],
            "status": record["status"],
            "origin": record["origin"],
            "licence": record["licence"],
            "evaluation_ref": record["evaluation_ref"],
        },
        metadata=metadata,
    )
    return alpha_template_registry_id(record["template_id"]), payload


def load_library_templates(
    files: Sequence[str] = ALPHA_TEMPLATE_LIBRARY_FILES,
) -> list[dict[str, Any]]:
    """Load the checked-in seed library (not validated; see ``validate_alpha_template``)."""
    out: list[dict[str, Any]] = []
    for name in files:
        doc = json.loads((ALPHA_TEMPLATE_LIBRARY_DIR / name).read_text(encoding="utf-8"))
        out.extend(doc["templates"])
    return out


def ensure_builtin_alpha_templates(registry_service: RegistryService) -> list[RegistryEntryView]:
    """Idempotently register the checked-in library as built-in registry entries."""
    views: list[RegistryEntryView] = []
    for template in load_library_templates():
        registry_id, payload = build_alpha_template_registry_payload(template)
        view, created = registry_service.register_if_absent(payload, registry_id)
        if not created:
            existing = view.entry
            if (
                existing.artifact_type != ArtifactType.ALPHA_TEMPLATE
                or existing.checksum != payload.checksum
                or existing.strategy_id != payload.strategy_id
            ):
                raise AlphaTemplateValidationError(
                    f"built-in AlphaTemplate id collision: {registry_id}"
                )
        views.append(view)
    return views

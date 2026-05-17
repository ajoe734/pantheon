"""Pool/runtime compatibility guard for DEP-004.

The guard runs after a DeploymentPlan exists and before deployment advances
toward RuntimeBinding creation.  It is read-only: callers provide the pool,
plan, runtime requirements, and binding records or stores; this module only
returns an auditable CompatibilityResult dictionary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


ACTIVE_STATUS = "active"
VALID_DEPLOYMENT_STAGES = {"paper", "canary", "live", "frozen"}


@dataclass(frozen=True)
class CompatibilityResult:
    """Serializable DEP-004 compatibility result."""

    capital_pool_id: str
    deployment_plan_id: str
    passed: bool
    rejection_reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capital_pool_id": self.capital_pool_id,
            "deployment_plan_id": self.deployment_plan_id,
            "passed": self.passed,
            "rejection_reasons": list(self.rejection_reasons),
            "details": dict(self.details),
        }


def check_compatibility(
    capital_pool_id: str,
    deployment_plan_id: str,
    *,
    capital_pool: Any | None = None,
    deployment_plan: Any | None = None,
    runtime_requirements: Mapping[str, Any] | None = None,
    persona_capital_binding: Any | None = None,
    capital_pool_store: Any | None = None,
    deployment_plan_store: Any | None = None,
    persona_capital_binding_store: Any | None = None,
) -> dict[str, Any]:
    """Return DEP-004 pool/runtime compatibility as a plain dictionary.

    Required checks:
    - pool admissibility status is active
    - pool risk_budget covers DeploymentPlan target_size
    - pool jurisdiction matches runtime broker jurisdiction
    - runtime mode matches DeploymentPlan target deployment_stage
    - PersonaCapitalBinding exists and is active
    """

    reasons: list[str] = []

    plan = deployment_plan or _store_get(deployment_plan_store, deployment_plan_id)
    pool = capital_pool or _store_get(capital_pool_store, capital_pool_id)
    runtime = _coerce_mapping(runtime_requirements) or _runtime_requirements_from_plan(plan)

    details: dict[str, Any] = {
        "compatibility_contract": "DEP-004",
    }

    if plan is None:
        reasons.append("deployment_plan_not_found")
        return _result(capital_pool_id, deployment_plan_id, reasons, details)

    plan_pool_id = _string_value(_field(plan, "capital_pool_id"))
    plan_id = _string_value(_field(plan, "plan_id"))
    if plan_id and plan_id != deployment_plan_id:
        reasons.append("deployment_plan_id_mismatch")
    if plan_pool_id and plan_pool_id != capital_pool_id:
        reasons.append("capital_pool_id_mismatch")

    stage = _deployment_stage(plan)
    details["deployment_stage"] = stage
    target_size = _target_size(plan)
    details["target_size"] = target_size

    if pool is None:
        reasons.append("capital_pool_not_found")
    else:
        pool_status = _normalized_field(pool, "status")
        details["pool_status"] = pool_status
        if pool_status != ACTIVE_STATUS:
            reasons.append("pool_admissibility_status_not_active")

        risk_budget = _risk_budget(pool)
        details["pool_risk_budget"] = risk_budget
        if risk_budget is None:
            reasons.append("pool_risk_budget_missing")
        elif target_size is None:
            reasons.append("deployment_plan_target_size_missing")
        elif target_size > risk_budget:
            reasons.append("pool_risk_budget_insufficient")

    if not runtime:
        reasons.append("runtime_requirements_missing")
    else:
        runtime_mode = _runtime_mode(runtime)
        broker_jurisdiction = _broker_jurisdiction(runtime)
        details["runtime_mode"] = runtime_mode
        details["runtime_broker_jurisdiction"] = broker_jurisdiction

        if runtime_mode is None:
            reasons.append("runtime_mode_missing")
        elif stage not in VALID_DEPLOYMENT_STAGES:
            reasons.append("deployment_stage_invalid")
        elif runtime_mode != stage:
            reasons.append("runtime_mode_stage_mismatch")

        if pool is not None:
            pool_jurisdictions = _pool_jurisdictions(pool)
            details["pool_jurisdictions"] = sorted(pool_jurisdictions)
            if broker_jurisdiction is None:
                reasons.append("runtime_broker_jurisdiction_missing")
            elif not pool_jurisdictions:
                reasons.append("pool_jurisdiction_missing")
            elif broker_jurisdiction not in pool_jurisdictions:
                reasons.append("pool_runtime_jurisdiction_mismatch")

    binding = persona_capital_binding or _resolve_persona_binding(
        plan=plan,
        runtime_requirements=runtime,
        store=persona_capital_binding_store,
    )
    binding_id = _binding_id(plan, runtime)
    details["persona_capital_binding_id"] = (
        _string_value(_field(binding, "binding_id")) if binding is not None else binding_id
    )
    if binding is None:
        reasons.append("persona_capital_binding_missing")
    else:
        binding_status = _normalized_field(binding, "status")
        details["persona_capital_binding_status"] = binding_status
        if binding_status != ACTIVE_STATUS:
            reasons.append("persona_capital_binding_not_active")
        binding_pool_id = _string_value(_field(binding, "capital_pool_id"))
        if binding_pool_id and binding_pool_id != capital_pool_id:
            reasons.append("persona_capital_binding_pool_mismatch")
        sponsor_persona_id = _string_value(_field(plan, "sponsor_persona_id"))
        binding_persona_id = _string_value(_field(binding, "persona_id"))
        if sponsor_persona_id and binding_persona_id and binding_persona_id != sponsor_persona_id:
            reasons.append("persona_capital_binding_persona_mismatch")

    return _result(capital_pool_id, deployment_plan_id, reasons, details)


def enforce_compatibility(
    capital_pool_id: str,
    deployment_plan_id: str,
    *,
    error_factory: type[Exception] = ValueError,
    **kwargs: Any,
) -> dict[str, Any]:
    """Raise if check_compatibility() fails, otherwise return the result."""

    result = check_compatibility(capital_pool_id, deployment_plan_id, **kwargs)
    if not result["passed"]:
        reasons = ", ".join(result["rejection_reasons"])
        raise error_factory(f"Pool/runtime compatibility check failed: {reasons}")
    return result


def _result(
    capital_pool_id: str,
    deployment_plan_id: str,
    reasons: list[str],
    details: dict[str, Any],
) -> dict[str, Any]:
    return CompatibilityResult(
        capital_pool_id=capital_pool_id,
        deployment_plan_id=deployment_plan_id,
        passed=not reasons,
        rejection_reasons=reasons,
        details=details,
    ).to_dict()


def _store_get(store: Any | None, key: str) -> Any | None:
    if store is None:
        return None
    getter = getattr(store, "get", None)
    if callable(getter):
        return getter(key)
    require = getattr(store, "require", None)
    if callable(require):
        try:
            return require(key)
        except Exception:
            return None
    if isinstance(store, Mapping):
        return store.get(key)
    return None


def _field(obj: Any | None, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _metadata(obj: Any | None) -> Mapping[str, Any]:
    value = _field(obj, "metadata")
    return value if isinstance(value, Mapping) else {}


def _coerce_mapping(value: Any | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _string_value(value: Any) -> Optional[str]:
    value = _enum_value(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized(value: Any) -> Optional[str]:
    text = _string_value(value)
    return text.lower() if text else None


def _normalized_field(obj: Any, key: str) -> Optional[str]:
    return _normalized(_field(obj, key))


def _number(value: Any) -> Optional[float]:
    value = _enum_value(value)
    if value in (None, ""):
        return None
    if isinstance(value, Mapping):
        for key in ("amount", "limit", "max_target_size", "risk_budget", "value"):
            parsed = _number(value.get(key))
            if parsed is not None:
                return parsed
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(obj: Any, keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        parsed = _number(_field(obj, key))
        if parsed is not None:
            return parsed
    metadata = _metadata(obj)
    for key in keys:
        parsed = _number(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _risk_budget(pool: Any) -> Optional[float]:
    return _first_number(
        pool,
        ("risk_budget", "risk_budget_amount", "max_target_size", "budget"),
    )


def _target_size(plan: Any) -> Optional[float]:
    return _first_number(
        plan,
        ("target_size", "target_notional", "target_capital", "requested_notional"),
    )


def _deployment_stage(plan: Any) -> Optional[str]:
    return _normalized(
        _field(plan, "target_stage")
        or _field(plan, "deployment_stage")
        or _metadata(plan).get("deployment_stage")
    )


def _runtime_requirements_from_plan(plan: Any | None) -> Mapping[str, Any]:
    if plan is None:
        return {}
    value = _field(plan, "runtime_requirements")
    if isinstance(value, Mapping):
        return value
    metadata = _metadata(plan)
    value = metadata.get("runtime_requirements")
    return value if isinstance(value, Mapping) else {}


def _runtime_mode(runtime: Mapping[str, Any]) -> Optional[str]:
    for key in ("runtime_mode", "deployment_mode", "mode", "deployment_stage"):
        value = _normalized(runtime.get(key))
        if value:
            return value
    return None


def _broker_jurisdiction(runtime: Mapping[str, Any]) -> Optional[str]:
    for key in ("broker_jurisdiction", "runtime_broker_jurisdiction", "jurisdiction"):
        value = _normalized(runtime.get(key))
        if value:
            return value
    broker = runtime.get("broker")
    if isinstance(broker, Mapping):
        return _normalized(broker.get("jurisdiction") or broker.get("jurisdiction_code"))
    return None


def _pool_jurisdictions(pool: Any) -> set[str]:
    values: list[Any] = [
        _field(pool, "jurisdiction"),
        _field(pool, "broker_jurisdiction"),
        _field(pool, "allowed_jurisdictions"),
        _metadata(pool).get("jurisdiction"),
        _metadata(pool).get("broker_jurisdiction"),
        _metadata(pool).get("allowed_jurisdictions"),
    ]
    jurisdictions: set[str] = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            jurisdictions.update(v for item in value if (v := _normalized(item)))
        else:
            normalized = _normalized(value)
            if normalized:
                jurisdictions.add(normalized)
    return jurisdictions


def _binding_id(plan: Any, runtime_requirements: Mapping[str, Any]) -> Optional[str]:
    for value in (
        runtime_requirements.get("persona_capital_binding_id"),
        _field(plan, "persona_capital_binding_id"),
        _field(plan, "binding_id"),
        _metadata(plan).get("persona_capital_binding_id"),
        _metadata(plan).get("binding_id"),
    ):
        binding_id = _string_value(value)
        if binding_id:
            return binding_id
    return None


def _resolve_persona_binding(
    *,
    plan: Any,
    runtime_requirements: Mapping[str, Any],
    store: Any | None,
) -> Any | None:
    binding_id = _binding_id(plan, runtime_requirements)
    if binding_id:
        return _store_get(store, binding_id)
    if store is None:
        return None

    list_fn = getattr(store, "list", None)
    if not callable(list_fn):
        return None

    capital_pool_id = _string_value(_field(plan, "capital_pool_id"))
    sponsor_persona_id = _string_value(_field(plan, "sponsor_persona_id"))
    kwargs: dict[str, str] = {}
    if capital_pool_id:
        kwargs["capital_pool_id"] = capital_pool_id
    if sponsor_persona_id:
        kwargs["persona_id"] = sponsor_persona_id
    try:
        candidates = list_fn(**kwargs)
    except TypeError:
        return None
    for binding in candidates:
        if _normalized_field(binding, "status") == ACTIVE_STATUS:
            return binding
    return candidates[0] if candidates else None

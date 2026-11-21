"""First-class RiskPolicy evaluator contract.

The Capital Pool Plane owns risk policy definitions.  Other services should
consume this module as the shared evaluator contract instead of interpreting
``risk_policy_ref`` as metadata-only text.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RiskPolicyError(ValueError):
    """Raised when a risk policy rejects a target."""

    def __init__(self, message: str, *, evaluation: "RiskPolicyEvaluation | None" = None) -> None:
        super().__init__(message)
        self.evaluation = evaluation


class RiskPolicyLoadError(RuntimeError):
    """Raised when a risk policy file or bound capital pool policy reference cannot be loaded."""


class RiskPolicyDecision(str, Enum):
    ALLOWED = "allowed"
    ALLOWED_WITH_CONDITIONS = "allowed_with_conditions"
    REJECTED = "rejected"


class RiskPolicyCheckStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class RiskPolicyTargetType(str, Enum):
    ALLOCATION_PROPOSAL = "allocation_proposal"
    DEPLOYMENT_PLAN = "deployment_plan"
    PROMOTION = "promotion"
    RUNTIME_BINDING = "runtime_binding"
    RUNTIME_LAUNCH = "runtime_launch"
    RUNTIME_ACTION = "runtime_action"


_FACT_OF_LIMIT = {
    "allowed_stages": "stage", "max_single_name_weight": "target_weights", "gross_limit": "gross_exposure",
    "net_limit": "net_exposure", "max_leverage": "leverage", "turnover_limit": "turnover",
    "max_sector_exposure": "sector_exposures", "max_factor_exposure": "factor_exposures",
    "max_strategy_family_concentration": "strategy_family_concentration",
    "max_target_overlap": "target_overlap", "max_signal_correlation": "signal_correlation",
    "max_canary_capital_scale_pct": "capital_scale_pct", "max_canary_gross_scale_pct": "gross_scale_pct",
    "allowed_asset_classes": "asset_classes", "forbidden_asset_classes": "asset_classes",
    "allowed_strategy_families": "strategy_family", "forbidden_strategy_families": "strategy_family",
    "liquidity_constraints": "liquidity", "drawdown_actions": "drawdown_pct",
}
_STRING_LIMITS = frozenset({
    "allowed_stages", "allowed_asset_classes", "forbidden_asset_classes",
    "allowed_strategy_families", "forbidden_strategy_families",
})
_ALLOCATION_LIMITS = frozenset(
    _STRING_LIMITS | {"liquidity_constraints", "drawdown_actions", "max_canary_capital_scale_pct", "max_canary_gross_scale_pct"}
)

_ACTIVE_POLICY_STATUSES = {"active"}
_DRAW_DOWN_ACTIONS = ("liquidate", "risk_off", "warn")


@dataclass(frozen=True)
class RiskPolicy:
    """Executable risk policy definition shared by optimizer/deployment/runtime.

    Limits are optional.  Missing limits mean the policy does not check that
    dimension; it never means another service may reinterpret the field.
    """

    risk_policy_id: str
    version: str = "v1"
    name: str | None = None
    status: str = "active"
    gross_limit: float | None = None
    net_limit: float | None = None
    max_single_name_weight: float | None = None
    max_sector_exposure: Mapping[str, float] | float | None = None
    max_factor_exposure: Mapping[str, float] | float | None = None
    max_leverage: float | None = None
    turnover_limit: float | None = None
    liquidity_constraints: Mapping[str, Any] = field(default_factory=dict)
    drawdown_actions: Mapping[str, float] = field(default_factory=dict)
    pause_rules: Mapping[str, Any] = field(default_factory=dict)
    liquidation_rules: Mapping[str, Any] = field(default_factory=dict)
    allowed_order_types: tuple[str, ...] = ()
    allowed_time_in_force: tuple[str, ...] = ()
    allowed_asset_classes: tuple[str, ...] = ()
    forbidden_asset_classes: tuple[str, ...] = ()
    allowed_strategy_families: tuple[str, ...] = ()
    forbidden_strategy_families: tuple[str, ...] = ()
    max_strategy_family_concentration: Mapping[str, float] | float | None = None
    max_target_overlap: float | None = None
    max_signal_correlation: float | None = None
    allowed_stages: tuple[str, ...] = ()
    max_canary_capital_scale_pct: float | None = None
    max_canary_gross_scale_pct: float | None = None
    kill_switch_triggers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskPolicy":
        """Build a policy from a JSON-compatible mapping.

        The parser accepts both canonical names and legacy optimizer aliases
        such as ``max_single_weight``.
        """

        if not isinstance(payload, Mapping):
            raise RiskPolicyError("Malformed risk policy: expected a mapping")
        risk_policy_id = _first_text(
            payload,
            "risk_policy_id",
            "policy_id",
            "risk_policy_ref",
            default="risk-policy-inline",
        )
        metadata = dict(_mapping(payload.get("metadata")))
        for provenance_key in (
            "tenant_id",
            "expires_at",
            "effective_to",
            "effective_from",
            "superseded_by",
            "stale",
            "requested_profile",
            "risk_profile",
            "explicit_policy",
        ):
            if provenance_key in payload and payload.get(provenance_key) is not None and provenance_key not in metadata:
                metadata[provenance_key] = payload.get(provenance_key)
        return cls(
            risk_policy_id=risk_policy_id,
            version=str(payload.get("version") or "v1"),
            name=_optional_text(payload.get("name")),
            status=str(payload.get("status") or "active"),
            gross_limit=_policy_limit(payload.get("gross_limit")),
            net_limit=_policy_limit(payload.get("net_limit")),
            max_single_name_weight=_policy_alias(payload, "max_single_name_weight", "max_single_weight"),
            max_sector_exposure=_policy_limit(payload.get("max_sector_exposure"), "flex"),
            max_factor_exposure=_policy_limit(payload.get("max_factor_exposure"), "flex"),
            max_leverage=_policy_limit(payload.get("max_leverage")),
            turnover_limit=_policy_limit(payload.get("turnover_limit")),
            liquidity_constraints=_policy_limit(payload.get("liquidity_constraints"), "map"),
            drawdown_actions=_policy_limit(payload.get("drawdown_actions"), "map"),
            pause_rules=_policy_limit(payload.get("pause_rules"), "map"),
            liquidation_rules=_policy_limit(payload.get("liquidation_rules"), "map"),
            allowed_order_types=_policy_limit(payload.get("allowed_order_types"), "list"),
            allowed_time_in_force=_policy_limit(payload.get("allowed_time_in_force"), "list"),
            allowed_asset_classes=_policy_limit(payload.get("allowed_asset_classes"), "list"),
            forbidden_asset_classes=_policy_limit(payload.get("forbidden_asset_classes"), "list"),
            allowed_strategy_families=_policy_limit(payload.get("allowed_strategy_families"), "list"),
            forbidden_strategy_families=_policy_limit(payload.get("forbidden_strategy_families"), "list"),
            max_strategy_family_concentration=_policy_limit(
                payload.get("max_strategy_family_concentration"), "flex"
            ),
            max_target_overlap=_policy_limit(payload.get("max_target_overlap")),
            max_signal_correlation=_policy_alias(payload, "max_signal_correlation", "max_pairwise_correlation"),
            allowed_stages=_policy_limit(payload.get("allowed_stages"), "list"),
            max_canary_capital_scale_pct=_policy_limit(payload.get("max_canary_capital_scale_pct")),
            max_canary_gross_scale_pct=_policy_limit(payload.get("max_canary_gross_scale_pct")),
            kill_switch_triggers=_policy_limit(payload.get("kill_switch_triggers"), "list"),
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, (), {})}


@dataclass(frozen=True)
class RiskPolicyEvaluationContext:
    """Target facts supplied to the evaluator by each consuming service."""

    target_type: str
    target_id: str
    capital_pool_id: str
    stage: str | None = None
    risk_policy_ref: str | None = None
    target_weights: Mapping[str, float] = field(default_factory=dict)
    gross_exposure: float | None = None
    net_exposure: float | None = None
    leverage: float | None = None
    turnover: float | None = None
    asset_classes: Sequence[str] = ()
    strategy_family: str | None = None
    strategy_family_concentration: Mapping[str, float] = field(default_factory=dict)
    target_overlap: float | None = None
    signal_correlation: float | None = None
    sector_exposures: Mapping[str, float] = field(default_factory=dict)
    factor_exposures: Mapping[str, float] = field(default_factory=dict)
    liquidity: Mapping[str, Any] = field(default_factory=dict)
    order_type: str | None = None
    time_in_force: str | None = None
    drawdown_pct: float | None = None
    capital_scale_pct: float | None = None
    gross_scale_pct: float | None = None
    runtime_action: str | None = None
    kill_switch_trigger: str | None = None
    decision_id: str | None = None
    instrument_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    correlation_envelope: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_classes", _string_tuple(self.asset_classes))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskPolicyEvaluationContext":
        metadata = _mapping(payload.get("metadata"))
        return cls(
            target_type=_first_text(payload, "target_type", default=RiskPolicyTargetType.RUNTIME_ACTION.value),
            target_id=_first_text(
                payload,
                "target_id",
                "decision_id",
                "order_id",
                "proposal_id",
                "plan_id",
                "binding_id",
                "runtime_id",
            ),
            capital_pool_id=_first_text(payload, "capital_pool_id"),
            stage=_first_text_or_none(payload, "stage", "target_stage", "deployment_mode", "scope_ref"),
            risk_policy_ref=_first_text_or_none(payload, "risk_policy_ref", "risk_policy_id"),
            target_weights=_numeric_mapping(payload.get("target_weights")),
            gross_exposure=_optional_float(payload.get("gross_exposure")),
            net_exposure=_optional_float(payload.get("net_exposure")),
            leverage=_optional_float(payload.get("leverage")),
            turnover=_optional_float(payload.get("turnover")),
            asset_classes=_string_tuple(
                payload.get(
                    "asset_classes",
                    payload.get("asset_class", metadata.get("asset_classes")),
                )
            ),
            strategy_family=_first_text_or_none(payload, "strategy_family"),
            strategy_family_concentration=_numeric_mapping(
                payload.get(
                    "strategy_family_concentration",
                    payload.get("strategy_family_concentrations", metadata.get("strategy_family_concentration")),
                )
            ),
            target_overlap=_optional_float(
                payload.get("target_overlap", metadata.get("target_overlap"))
            ),
            signal_correlation=_optional_float(
                payload.get(
                    "signal_correlation",
                    payload.get(
                        "max_pairwise_correlation",
                        metadata.get("signal_correlation", metadata.get("max_pairwise_correlation")),
                    ),
                )
            ),
            sector_exposures=_numeric_mapping(payload.get("sector_exposures")),
            factor_exposures=_numeric_mapping(payload.get("factor_exposures")),
            liquidity=_mapping(payload.get("liquidity")),
            order_type=_first_text_or_none(payload, "order_type"),
            time_in_force=_first_text_or_none(payload, "time_in_force"),
            drawdown_pct=_optional_float(payload.get("drawdown_pct")),
            capital_scale_pct=_optional_float(payload.get("capital_scale_pct")),
            gross_scale_pct=_optional_float(payload.get("gross_scale_pct")),
            runtime_action=_first_text_or_none(payload, "runtime_action"),
            kill_switch_trigger=_first_text_or_none(payload, "kill_switch_trigger"),
            decision_id=_optional_text(payload.get("decision_id") or metadata.get("decision_id")),
            instrument_id=_optional_text(payload.get("instrument_id") or metadata.get("instrument_id")),
            metadata=metadata,
            trace_id=_first_text_or_none(payload, "trace_id"),
            correlation_envelope=_mapping(payload.get("correlation_envelope")) or None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, (), {})}


@dataclass(frozen=True)
class RiskPolicyCheck:
    check_name: str
    status: str
    reason: str
    code: str
    limit: Any = None
    observed: Any = None
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class RiskPolicyEvaluation:
    risk_policy_id: str
    risk_policy_version: str
    capital_pool_id: str
    target_type: str
    target_id: str
    decision: str
    checks: tuple[RiskPolicyCheck, ...]
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    evaluated_at: str
    trace_id: str
    correlation_envelope: Mapping[str, Any] | None = None

    @property
    def allowed(self) -> bool:
        return self.decision in {
            RiskPolicyDecision.ALLOWED.value,
            RiskPolicyDecision.ALLOWED_WITH_CONDITIONS.value,
        }

    @property
    def rejected(self) -> bool:
        return self.decision == RiskPolicyDecision.REJECTED.value

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "risk_policy_id": self.risk_policy_id,
            "risk_policy_version": self.risk_policy_version,
            "capital_pool_id": self.capital_pool_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "decision": self.decision,
            "checks": [check.to_dict() for check in self.checks],
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
            "evaluated_at": self.evaluated_at,
            "trace_id": self.trace_id,
        }
        if self.correlation_envelope:
            payload["correlation_envelope"] = dict(self.correlation_envelope)
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskPolicyEvaluation":
        checks = tuple(
            RiskPolicyCheck(
                check_name=str(item.get("check_name") or ""),
                status=str(item.get("status") or ""),
                reason=str(item.get("reason") or ""),
                code=str(item.get("code") or item.get("check_name") or ""),
                limit=item.get("limit"),
                observed=item.get("observed"),
                action=_optional_text(item.get("action")),
            )
            for item in payload.get("checks", [])
            if isinstance(item, Mapping)
        )
        return cls(
            risk_policy_id=str(payload.get("risk_policy_id") or ""),
            risk_policy_version=str(payload.get("risk_policy_version") or payload.get("version") or ""),
            capital_pool_id=str(payload.get("capital_pool_id") or ""),
            target_type=str(payload.get("target_type") or ""),
            target_id=str(payload.get("target_id") or ""),
            decision=str(payload.get("decision") or ""),
            checks=checks,
            blocking_reasons=tuple(str(item) for item in payload.get("blocking_reasons", [])),
            warnings=tuple(str(item) for item in payload.get("warnings", [])),
            evaluated_at=str(payload.get("evaluated_at") or ""),
            trace_id=str(payload.get("trace_id") or ""),
            correlation_envelope=_mapping(payload.get("correlation_envelope")) or None,
        )


class RiskPolicyEvaluator:
    """Evaluate a RiskPolicy against an optimizer/deployment/runtime target."""

    def evaluate(
        self,
        policy: RiskPolicy | Mapping[str, Any],
        context: RiskPolicyEvaluationContext | Mapping[str, Any],
    ) -> RiskPolicyEvaluation:
        resolved_policy = policy if isinstance(policy, RiskPolicy) else RiskPolicy.from_mapping(policy)
        # Capital risk increases require all applicable facts before normalization can
        # discard malformed values. Other consumers retain their target contracts.
        facts = context.to_dict() if isinstance(context, RiskPolicyEvaluationContext) else context
        if facts.get("target_type") in {"capital_pool_activation", "capital_binding_activation", "rebalance_apply"}:
            for limit, fact in _FACT_OF_LIMIT.items():
                if limit.startswith("max_canary_") and facts.get("stage") != "canary":
                    continue
                if facts["target_type"] == "capital_pool_activation" and "stage" not in facts and limit in _ALLOCATION_LIMITS:
                    continue
                configured = getattr(resolved_policy, limit)
                if configured not in (None, (), {}) and _is_obs_missing(limit, configured, facts.get(fact)):
                    raise RiskPolicyError(f"Risk policy limit {limit} cannot be evaluated: {fact} unavailable")
        resolved_context = (
            context
            if isinstance(context, RiskPolicyEvaluationContext)
            else RiskPolicyEvaluationContext.from_mapping(context)
        )
        checks: list[RiskPolicyCheck] = []

        self._check_policy_identity(resolved_policy, resolved_context, checks)
        self._check_policy_status(resolved_policy, checks)
        self._check_stage(resolved_policy, resolved_context, checks)
        self._check_asset_classes(resolved_policy, resolved_context, checks)
        self._check_strategy_family(resolved_policy, resolved_context, checks)
        self._check_target_weights(resolved_policy, resolved_context, checks)
        self._check_exposure_map(
            "strategy_family_concentration",
            resolved_policy.max_strategy_family_concentration,
            resolved_context.strategy_family_concentration,
            checks,
        )
        self._check_scalar_limit(
            "target_overlap",
            resolved_policy.max_target_overlap,
            resolved_context.target_overlap,
            checks,
        )
        self._check_scalar_limit(
            "signal_correlation",
            resolved_policy.max_signal_correlation,
            resolved_context.signal_correlation,
            checks,
        )
        self._check_scalar_limit("gross_exposure", resolved_policy.gross_limit, resolved_context.gross_exposure, checks)
        self._check_scalar_limit("net_exposure", resolved_policy.net_limit, resolved_context.net_exposure, checks)
        self._check_scalar_limit("leverage", resolved_policy.max_leverage, resolved_context.leverage, checks)
        self._check_scalar_limit("turnover", resolved_policy.turnover_limit, resolved_context.turnover, checks)
        self._check_exposure_map("sector_exposure", resolved_policy.max_sector_exposure, resolved_context.sector_exposures, checks)
        self._check_exposure_map("factor_exposure", resolved_policy.max_factor_exposure, resolved_context.factor_exposures, checks)
        self._check_liquidity(resolved_policy, resolved_context, checks)
        self._check_order_controls(resolved_policy, resolved_context, checks)
        self._check_canary_scale(resolved_policy, resolved_context, checks)
        self._check_drawdown(resolved_policy, resolved_context, checks)
        self._check_kill_switch(resolved_policy, resolved_context, checks)

        blocking = tuple(check.reason for check in checks if check.status == RiskPolicyCheckStatus.FAILED.value)
        warnings = tuple(check.reason for check in checks if check.status == RiskPolicyCheckStatus.WARNING.value)
        if blocking:
            decision = RiskPolicyDecision.REJECTED.value
        elif warnings:
            decision = RiskPolicyDecision.ALLOWED_WITH_CONDITIONS.value
        else:
            decision = RiskPolicyDecision.ALLOWED.value

        envelope = None
        if resolved_context.correlation_envelope:
            from services.trade_journey.correlation_envelope import propagate_envelope
            envelope = propagate_envelope(
                resolved_context.correlation_envelope,
                producer="risk.evaluation",
                event_id=f"risk-evaluation:{resolved_context.target_id}:{uuid.uuid4().hex[:12]}",
                event_time=utc_now(),
            )
        return RiskPolicyEvaluation(
            risk_policy_id=resolved_policy.risk_policy_id,
            risk_policy_version=resolved_policy.version,
            capital_pool_id=resolved_context.capital_pool_id,
            target_type=resolved_context.target_type,
            target_id=resolved_context.target_id,
            decision=decision,
            checks=tuple(checks),
            blocking_reasons=blocking,
            warnings=warnings,
            evaluated_at=utc_now(),
            trace_id=resolved_context.trace_id or f"risk-eval-{uuid.uuid4().hex[:12]}",
            correlation_envelope=envelope,
        )

    def ensure_allowed(
        self,
        policy: RiskPolicy | Mapping[str, Any],
        context: RiskPolicyEvaluationContext | Mapping[str, Any],
    ) -> RiskPolicyEvaluation:
        evaluation = self.evaluate(policy, context)
        if evaluation.rejected:
            raise RiskPolicyError(
                "RiskPolicy rejected target: " + "; ".join(evaluation.blocking_reasons),
                evaluation=evaluation,
            )
        return evaluation

    def _check_policy_identity(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if not context.risk_policy_ref:
            return
        if context.risk_policy_ref == policy.risk_policy_id:
            checks.append(_passed("risk_policy_identity", "risk_policy_ref matches evaluator policy", policy.risk_policy_id))
            return
        checks.append(
            _failed(
                "risk_policy_identity",
                "risk_policy_identity_mismatch",
                f"risk_policy_ref {context.risk_policy_ref!r} does not match evaluator policy {policy.risk_policy_id!r}",
                limit=policy.risk_policy_id,
                observed=context.risk_policy_ref,
            )
        )

    def _check_policy_status(self, policy: RiskPolicy, checks: list[RiskPolicyCheck]) -> None:
        if policy.status in _ACTIVE_POLICY_STATUSES:
            checks.append(_passed("policy_status", "RiskPolicy is active", policy.status))
            return
        checks.append(
            _failed(
                "policy_status",
                "risk_policy_not_active",
                f"RiskPolicy {policy.risk_policy_id!r} status {policy.status!r} is not active",
                limit="active",
                observed=policy.status,
            )
        )

    def _check_stage(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if not policy.allowed_stages or not context.stage:
            return
        if context.stage in policy.allowed_stages:
            checks.append(_passed("stage", "target stage is allowed by RiskPolicy", context.stage))
            return
        checks.append(
            _failed(
                "stage",
                "stage_not_allowed",
                f"target stage {context.stage!r} is not allowed by RiskPolicy",
                limit=list(policy.allowed_stages),
                observed=context.stage,
            )
        )

    def _check_asset_classes(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        for asset_class in context.asset_classes:
            if asset_class in policy.forbidden_asset_classes:
                checks.append(
                    _failed(
                        "asset_class",
                        "forbidden_asset_class",
                        f"asset class {asset_class!r} is forbidden by RiskPolicy",
                        limit=list(policy.forbidden_asset_classes),
                        observed=asset_class,
                    )
                )
        if not policy.allowed_asset_classes:
            return
        for asset_class in context.asset_classes:
            if asset_class not in policy.allowed_asset_classes:
                checks.append(
                    _failed(
                        "asset_class",
                        "asset_class_not_allowed",
                        f"asset class {asset_class!r} is not in RiskPolicy allowed_asset_classes",
                        limit=list(policy.allowed_asset_classes),
                        observed=asset_class,
                    )
                )

    def _check_strategy_family(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        family = context.strategy_family
        if not family:
            return
        if family in policy.forbidden_strategy_families:
            checks.append(
                _failed(
                    "strategy_family",
                    "forbidden_strategy_family",
                    f"strategy family {family!r} is forbidden by RiskPolicy",
                    limit=list(policy.forbidden_strategy_families),
                    observed=family,
                )
            )
            return
        if policy.allowed_strategy_families and family not in policy.allowed_strategy_families:
            checks.append(
                _failed(
                    "strategy_family",
                    "strategy_family_not_allowed",
                    f"strategy family {family!r} is not in RiskPolicy allowed_strategy_families",
                    limit=list(policy.allowed_strategy_families),
                    observed=family,
                )
            )

    def _check_target_weights(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if policy.max_single_name_weight is None:
            return
        for symbol, raw_weight in context.target_weights.items():
            weight = _optional_float(raw_weight)
            if weight is None:
                continue
            if weight > policy.max_single_name_weight:
                checks.append(
                    _failed(
                        "max_single_name_weight",
                        "pool_risk_policy",
                        f"target weight {symbol}={weight:.6g} exceeds max_single_name_weight {policy.max_single_name_weight:.6g}",
                        limit=policy.max_single_name_weight,
                        observed={symbol: weight},
                    )
                )

    def _check_scalar_limit(
        self,
        name: str,
        limit: float | None,
        observed: float | None,
        checks: list[RiskPolicyCheck],
    ) -> None:
        observed_value = _optional_float(observed)
        if limit is None or observed_value is None:
            return
        if abs(observed_value) <= limit:
            checks.append(_passed(name, f"{name} is within RiskPolicy limit", observed_value, limit=limit))
            return
        checks.append(
            _failed(
                name,
                f"{name}_limit_exceeded",
                f"{name} {observed_value:.6g} exceeds RiskPolicy limit {limit:.6g}",
                limit=limit,
                observed=observed_value,
            )
        )

    def _check_exposure_map(
        self,
        name: str,
        limit: Mapping[str, float] | float | None,
        observed_map: Mapping[str, float],
        checks: list[RiskPolicyCheck],
    ) -> None:
        if limit is None or not observed_map:
            return
        for key, raw_observed in observed_map.items():
            observed = _optional_float(raw_observed)
            if observed is None:
                continue
            allowed = _limit_for_key(limit, str(key))
            if allowed is None:
                continue
            if abs(observed) > allowed:
                checks.append(
                    _failed(
                        name,
                        f"{name}_limit_exceeded",
                        f"{name} {key}={observed:.6g} exceeds RiskPolicy limit {allowed:.6g}",
                        limit={key: allowed},
                        observed={key: observed},
                    )
                )

    def _check_liquidity(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if not policy.liquidity_constraints or not context.liquidity:
            return
        min_adv = _optional_float(policy.liquidity_constraints.get("min_avg_daily_volume"))
        observed_adv = _optional_float(context.liquidity.get("avg_daily_volume"))
        if min_adv is not None and observed_adv is not None and observed_adv < min_adv:
            checks.append(
                _failed(
                    "liquidity",
                    "liquidity_min_adv_breach",
                    f"avg_daily_volume {observed_adv:.6g} is below RiskPolicy minimum {min_adv:.6g}",
                    limit=min_adv,
                    observed=observed_adv,
                )
            )
        max_order_pct_adv = _optional_float(policy.liquidity_constraints.get("max_order_pct_adv"))
        observed_order_pct_adv = _optional_float(context.liquidity.get("order_pct_adv"))
        if (
            max_order_pct_adv is not None
            and observed_order_pct_adv is not None
            and observed_order_pct_adv > max_order_pct_adv
        ):
            checks.append(
                _failed(
                    "liquidity",
                    "liquidity_order_pct_adv_breach",
                    f"order_pct_adv {observed_order_pct_adv:.6g} exceeds RiskPolicy limit {max_order_pct_adv:.6g}",
                    limit=max_order_pct_adv,
                    observed=observed_order_pct_adv,
                )
            )

    def _check_order_controls(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if policy.allowed_order_types and context.order_type and context.order_type not in policy.allowed_order_types:
            checks.append(
                _failed(
                    "order_type",
                    "order_type_not_allowed",
                    f"order_type {context.order_type!r} is not allowed by RiskPolicy",
                    limit=list(policy.allowed_order_types),
                    observed=context.order_type,
                )
            )
        if (
            policy.allowed_time_in_force
            and context.time_in_force
            and context.time_in_force not in policy.allowed_time_in_force
        ):
            checks.append(
                _failed(
                    "time_in_force",
                    "time_in_force_not_allowed",
                    f"time_in_force {context.time_in_force!r} is not allowed by RiskPolicy",
                    limit=list(policy.allowed_time_in_force),
                    observed=context.time_in_force,
                )
            )

    def _check_canary_scale(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if context.stage != "canary":
            return
        self._check_scalar_limit(
            "canary_capital_scale_pct",
            policy.max_canary_capital_scale_pct,
            context.capital_scale_pct,
            checks,
        )
        self._check_scalar_limit(
            "canary_gross_scale_pct",
            policy.max_canary_gross_scale_pct,
            context.gross_scale_pct,
            checks,
        )

    def _check_drawdown(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if context.drawdown_pct is None or not policy.drawdown_actions:
            return
        for action in _DRAW_DOWN_ACTIONS:
            threshold = _optional_float(policy.drawdown_actions.get(action))
            if threshold is None or context.drawdown_pct < threshold:
                continue
            if action == "warn":
                checks.append(
                    _warning(
                        "drawdown",
                        "drawdown_warn",
                        f"drawdown_pct {context.drawdown_pct:.6g} reached RiskPolicy warn threshold {threshold:.6g}",
                        limit=threshold,
                        observed=context.drawdown_pct,
                        action=action,
                    )
                )
                return
            checks.append(
                _failed(
                    "drawdown",
                    f"drawdown_{action}",
                    f"drawdown_pct {context.drawdown_pct:.6g} reached RiskPolicy {action} threshold {threshold:.6g}",
                    limit=threshold,
                    observed=context.drawdown_pct,
                    action=action,
                )
            )
            return

    def _check_kill_switch(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if not context.kill_switch_trigger or context.kill_switch_trigger not in policy.kill_switch_triggers:
            return
        checks.append(
            _failed(
                "kill_switch_trigger",
                "kill_switch_triggered",
                f"kill_switch_trigger {context.kill_switch_trigger!r} is configured to force risk-off",
                limit=list(policy.kill_switch_triggers),
                observed=context.kill_switch_trigger,
                action="risk_off",
            )
        )


def risk_policy_rejection_message(prefix: str, evaluation: RiskPolicyEvaluation) -> str:
    reasons = "; ".join(evaluation.blocking_reasons) or "no blocking reason recorded"
    return f"{prefix}: RiskPolicy {evaluation.risk_policy_id!r} rejected {evaluation.target_type} {evaluation.target_id!r}: {reasons}"


def _passed(name: str, reason: str, observed: Any = None, *, limit: Any = None) -> RiskPolicyCheck:
    return RiskPolicyCheck(
        check_name=name,
        status=RiskPolicyCheckStatus.PASSED.value,
        reason=reason,
        code=name,
        limit=limit,
        observed=observed,
    )


def _warning(
    name: str,
    code: str,
    reason: str,
    *,
    limit: Any = None,
    observed: Any = None,
    action: str | None = None,
) -> RiskPolicyCheck:
    return RiskPolicyCheck(
        check_name=name,
        status=RiskPolicyCheckStatus.WARNING.value,
        reason=reason,
        code=code,
        limit=limit,
        observed=observed,
        action=action,
    )


def _failed(
    name: str,
    code: str,
    reason: str,
    *,
    limit: Any = None,
    observed: Any = None,
    action: str | None = None,
) -> RiskPolicyCheck:
    return RiskPolicyCheck(
        check_name=name,
        status=RiskPolicyCheckStatus.FAILED.value,
        reason=reason,
        code=code,
        limit=limit,
        observed=observed,
        action=action,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(payload: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        text = _optional_text(payload.get(key))
        if text:
            return text
    return default


def _first_text_or_none(payload: Mapping[str, Any], *keys: str) -> str | None:
    text = _first_text(payload, *keys)
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _numeric_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        numeric = _optional_float(raw)
        if numeric is not None:
            result[str(key)] = numeric
    return result


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, set):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if not isinstance(value, Sequence):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _policy_alias(payload: Mapping[str, Any], key: str, alias: str) -> float | None:
    canonical, legacy = _policy_limit(payload.get(key)), _policy_limit(payload.get(alias))
    return canonical if key in payload else legacy


def _policy_limit(value: Any, kind: str = "scalar") -> Any:
    if value is None and kind != "number":
        return {"map": {}, "list": ()}.get(kind)
    if kind == "list":
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return tuple(item.strip() for item in value if item.strip())
    elif kind in ("map", "flex") and isinstance(value, Mapping):
        return {str(key): _policy_limit(raw, "number") for key, raw in value.items()}
    elif kind in ("scalar", "flex", "number"):
        numeric = _optional_float(value)
        if numeric is not None:
            return numeric
    raise RiskPolicyError(f"Malformed risk policy {kind} limit: {value!r}")


def _is_obs_missing(limit: str, limit_value: Any, obs: Any) -> bool:
    if obs in (None, "", (), [], {}):
        return True
    if limit in _STRING_LIMITS:
        return False
    if limit == "liquidity_constraints":
        if not isinstance(obs, Mapping):
            return True
        required = (("min_avg_daily_volume", "avg_daily_volume"), ("max_order_pct_adv", "order_pct_adv"))
        if any(_optional_float(obs.get(o)) is None for k, o in required if k in limit_value):
            return True
    values = obs.values() if isinstance(obs, Mapping) else [obs]
    return any(_optional_float(v) is None for v in values)


def _limit_for_key(limit: Mapping[str, float] | float, key: str) -> float | None:
    if isinstance(limit, Mapping):
        return _optional_float(limit.get(key, limit.get("*")))
    return _optional_float(limit)


def load_risk_policy(ref: str, *, policy_dir: str | os.PathLike[str] | None = None) -> Mapping[str, Any]:
    """Load a raw RiskPolicy JSON mapping from ``CAPITAL_RISK_POLICY_DIR`` (stdlib-only)."""
    clean_ref = str(ref or "").strip()
    if policy_dir is not None:
        root = Path(str(policy_dir).strip())
    elif os.getenv("CAPITAL_RISK_POLICY_DIR", "").strip():
        root = Path(os.getenv("CAPITAL_RISK_POLICY_DIR", "").strip())
    else:
        repo_config_dir = Path(__file__).resolve().parents[2] / "config" / "risk_policies"
        local_dir = Path(__file__).parent / "risk_policies"
        root = repo_config_dir if repo_config_dir.exists() else local_dir
    if not clean_ref or "/" in clean_ref or "\\" in clean_ref or clean_ref.startswith("."):
        raise RiskPolicyLoadError("risk policy source is not configured for this reference")
    path = root / f"{clean_ref}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RiskPolicyLoadError(f"Risk policy {clean_ref!r} unavailable at {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise RiskPolicyLoadError(f"Malformed risk policy {clean_ref!r}: expected a JSON object")
    normalized = dict(raw)
    exposure_limits = normalized.get("exposure_limits")
    if isinstance(exposure_limits, Mapping):
        for k, v in exposure_limits.items():
            normalized.setdefault(k, v)
    return normalized


def normalize_risk_policy(
    policy: "RiskPolicy | Mapping[str, Any]",
    *,
    default_id: str | None = None,
) -> RiskPolicy:
    """Normalize a ``RiskPolicy`` or JSON mapping into a validated ``RiskPolicy`` model."""
    if isinstance(policy, RiskPolicy):
        return policy
    if isinstance(policy, Mapping):
        payload = dict(policy)
        exposure_limits = payload.get("exposure_limits")
        if isinstance(exposure_limits, Mapping):
            for k, v in exposure_limits.items():
                payload.setdefault(k, v)
        if default_id and not any(payload.get(k) for k in ("risk_policy_id", "policy_id", "risk_policy_ref")):
            payload["risk_policy_id"] = default_id
        return RiskPolicy.from_mapping(payload)
    raise RiskPolicyError(f"Malformed risk policy: expected RiskPolicy or mapping, got {type(policy).__name__}")


def load_risk_policy_model(
    ref: str,
    *,
    policy_dir: str | os.PathLike[str] | None = None,
    loader: Callable[[str], Any] | None = None,
) -> RiskPolicy:
    """Load and parse a ``RiskPolicy`` model by reference."""
    clean_ref = str(ref or "").strip()
    if not clean_ref:
        raise RiskPolicyLoadError("risk_policy_ref is empty")
    raw = loader(clean_ref) if loader is not None else load_risk_policy(clean_ref, policy_dir=policy_dir)
    return normalize_risk_policy(raw, default_id=clean_ref)


def _configured_pool_store_paths(explicit_path: str | os.PathLike[str] | None = None) -> list[tuple[Path, bool]]:
    candidates: list[tuple[Path, bool]] = []
    if explicit_path is not None and str(explicit_path).strip():
        candidates.append((Path(str(explicit_path).strip()), True))
    env_store = os.getenv("PANTHEON_CAPITAL_POOL_STORE_PATH", "").strip()
    if env_store:
        candidates.append((Path(env_store), True))
    for env_key in ("CAPITAL_DATA_DIR", "PANTHEON_GOVERNANCE_DATA_DIR", "DEPLOYMENT_DATA_DIR"):
        env_dir = os.getenv(env_key, "").strip()
        if env_dir:
            candidates.append((Path(env_dir) / "capital_pools.json", False))
    return candidates


def read_capital_pool_record(
    pool_id: str | None,
    *,
    pool_store_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Read a bound CapitalPool record from configured JSON stores without third-party imports."""
    clean_pool_id = str(pool_id or "").strip()
    for path, explicit in _configured_pool_store_paths(pool_store_path):
        if not path.exists():
            if explicit:
                raise RiskPolicyLoadError(f"Capital pool store not found at {path}")
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RiskPolicyLoadError(f"Capital pool store unreadable at {path}: {exc}") from exc
        records: list[Mapping[str, Any]] = []
        if isinstance(raw, list):
            records = [item for item in raw if isinstance(item, Mapping)]
        elif isinstance(raw, Mapping):
            if isinstance(raw.get("pools"), list):
                records = [item for item in raw["pools"] if isinstance(item, Mapping)]
            elif isinstance(raw.get("items"), list):
                records = [item for item in raw["items"] if isinstance(item, Mapping)]
            elif clean_pool_id and isinstance(raw.get(clean_pool_id), Mapping):
                return dict(raw[clean_pool_id])
            elif "pool_id" in raw or "capital_pool_id" in raw:
                records = [raw]
            else:
                records = [val for val in raw.values() if isinstance(val, Mapping)]
        else:
            raise RiskPolicyLoadError(f"Capital pool store at {path} must be a JSON list or object")

        if clean_pool_id:
            for item in records:
                item_id = str(item.get("pool_id") or item.get("capital_pool_id") or "").strip()
                if item_id == clean_pool_id:
                    return dict(item)
            raise RiskPolicyLoadError(f"Capital pool {clean_pool_id!r} not found in {path}")
        if len(records) == 1:
            return dict(records[0])
    return None


POLICY_DIGEST_FIELDS: tuple[str, ...] = (
    "risk_policy_id",
    "version",
    "status",
    "gross_limit",
    "net_limit",
    "max_leverage",
    "max_single_name_weight",
    "allowed_stages",
    "allowed_asset_classes",
    "forbidden_asset_classes",
    "allowed_order_types",
    "allowed_time_in_force",
    "liquidity_constraints",
    "drawdown_actions",
    "pause_rules",
    "liquidation_rules",
    "kill_switch_triggers",
)

_RECOGNIZED_POLICY_LIMIT_KEYS: frozenset[str] = frozenset(
    {
        *_FACT_OF_LIMIT.keys(),
        "max_single_weight",
        "max_pairwise_correlation",
        "exposure_limits",
        "allowed_order_types",
        "allowed_time_in_force",
        "pause_rules",
        "liquidation_rules",
        "kill_switch_triggers",
    }
)

_POOL_ENVELOPE_KEYS: frozenset[str] = frozenset(
    {
        "pool_id",
        "capital_pool_id",
        "single_runtime_enforced",
        "ring_fenced",
        "allocation_mechanism",
        "budget",
        "currency",
    }
)

_POSITIVE_EXPOSURE_LIMIT_FIELDS: tuple[str, ...] = (
    "gross_limit",
    "net_limit",
    "max_leverage",
    "max_single_name_weight",
)

_NON_NEGATIVE_SCALAR_LIMIT_FIELDS: tuple[str, ...] = (
    *_POSITIVE_EXPOSURE_LIMIT_FIELDS,
    "turnover_limit",
    "max_target_overlap",
    "max_canary_capital_scale_pct",
    "max_canary_gross_scale_pct",
)


def risk_policy_digest(policy: RiskPolicy | Mapping[str, Any] | None) -> str:
    """Compute the canonical SHA-256 digest of a RiskPolicy matching the acceptance probe."""
    if policy is None:
        return ""
    resolved = policy if isinstance(policy, RiskPolicy) else normalize_risk_policy(policy)
    raw = resolved.to_dict()
    projected = {key: raw.get(key) for key in POLICY_DIGEST_FIELDS if raw.get(key) not in (None, (), {}, [])}
    encoded = json.dumps(projected, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_pool_envelope_mapping(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in _POOL_ENVELOPE_KEYS)


def _is_policy_content_mapping(payload: Mapping[str, Any]) -> bool:
    if not isinstance(payload, Mapping) or len(payload) == 0:
        return False
    if any(_optional_text(payload.get(key)) for key in ("risk_policy_id", "policy_id", "risk_policy_ref")):
        return True
    if any(payload.get(key) not in (None, (), {}, []) for key in _RECOGNIZED_POLICY_LIMIT_KEYS):
        return True
    return False


def _parse_iso_timestamp(value: Any) -> datetime | None:
    text = _optional_text(value)
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _policy_effective_limits(policy: RiskPolicy) -> dict[str, Any]:
    raw = policy.to_dict()
    limits: dict[str, Any] = {}
    for key in (
        "gross_limit",
        "net_limit",
        "max_leverage",
        "max_single_name_weight",
        "turnover_limit",
        "max_sector_exposure",
        "max_factor_exposure",
        "max_strategy_family_concentration",
        "max_target_overlap",
        "max_signal_correlation",
        "allowed_stages",
        "allowed_asset_classes",
        "forbidden_asset_classes",
        "allowed_strategy_families",
        "forbidden_strategy_families",
        "allowed_order_types",
        "allowed_time_in_force",
        "liquidity_constraints",
        "drawdown_actions",
        "pause_rules",
        "liquidation_rules",
        "max_canary_capital_scale_pct",
        "max_canary_gross_scale_pct",
        "kill_switch_triggers",
    ):
        value = raw.get(key)
        if value not in (None, (), {}, []):
            limits[key] = list(value) if isinstance(value, tuple) else value
    return limits


@dataclass(frozen=True)
class BoundPoolRiskPolicyResolution:
    """Detailed resolution and validation result for a capital pool's bound RiskPolicy."""

    capital_pool_id: str
    resolved: bool
    status: str
    source: str
    policy: RiskPolicy | None = None
    risk_policy_id: str | None = None
    risk_policy_version: str | None = None
    risk_policy_digest: str | None = None
    tenant_id: str | None = None
    policy_tenant_id: str | None = None
    stage: str | None = None
    requested_profile: str | None = None
    profile_mapping_status: str = "not_applicable"
    effective_limits: Mapping[str, Any] = field(default_factory=dict)
    reason_code: str | None = None
    reason: str | None = None
    allow_new_or_increased_risk: bool = False
    allow_risk_reducing_actions: bool = True
    owner_decision_request: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "capital_pool_id": self.capital_pool_id,
            "resolved": self.resolved,
            "status": self.status,
            "source": self.source,
            "risk_policy_id": self.risk_policy_id,
            "risk_policy_version": self.risk_policy_version,
            "risk_policy_digest": self.risk_policy_digest,
            "tenant_id": self.tenant_id,
            "policy_tenant_id": self.policy_tenant_id,
            "stage": self.stage,
            "requested_profile": self.requested_profile,
            "profile_mapping_status": self.profile_mapping_status,
            "effective_limits": dict(self.effective_limits),
            "reason_code": self.reason_code,
            "reason": self.reason,
            "allow_new_or_increased_risk": self.allow_new_or_increased_risk,
            "allow_risk_reducing_actions": self.allow_risk_reducing_actions,
        }
        if self.policy is not None:
            payload["policy"] = self.policy.to_dict()
        if self.owner_decision_request is not None:
            payload["owner_decision_request"] = dict(self.owner_decision_request)
        return payload


def _extract_pool_policy_candidates(
    pool_obj: Any,
) -> tuple[str | None, Any, bool, str, dict[str, Any], str | None]:
    """Extract (ref, inline_policy, explicit_inline_key, source, pool_metadata, pool_tenant_id)."""
    if pool_obj is None:
        return None, None, False, "unresolved", {}, None
    if isinstance(pool_obj, RiskPolicy):
        meta = dict(pool_obj.metadata) if isinstance(pool_obj.metadata, Mapping) else {}
        return None, pool_obj, True, "pool_inline", meta, _optional_text(meta.get("tenant_id"))
    if isinstance(pool_obj, str):
        clean_ref = pool_obj.strip()
        return (clean_ref or None), None, False, ("risk_policy_ref" if clean_ref else "unresolved"), {}, None
    if isinstance(pool_obj, Mapping):
        pool_meta = dict(pool_obj.get("metadata")) if isinstance(pool_obj.get("metadata"), Mapping) else {}
        pool_tenant_id = _first_text_or_none(pool_obj, "tenant_id") or _first_text_or_none(pool_meta, "tenant_id")
        ref_from_pool = _first_text_or_none(pool_obj, "risk_policy_ref")
        has_top_inline = "risk_policy" in pool_obj and pool_obj.get("risk_policy") is not None
        has_meta_inline = "risk_policy" in pool_meta and pool_meta.get("risk_policy") is not None
        inline_policy = (
            pool_obj.get("risk_policy")
            if has_top_inline
            else (pool_meta.get("risk_policy") if has_meta_inline else None)
        )
        explicit_inline_key = has_top_inline or has_meta_inline
        if ref_from_pool:
            return ref_from_pool, inline_policy, explicit_inline_key, "risk_policy_ref", pool_meta, pool_tenant_id
        if explicit_inline_key:
            source = "pool_inline" if has_top_inline else "pool_metadata_inline"
            return None, inline_policy, True, source, pool_meta, pool_tenant_id
        ref_id = _first_text_or_none(pool_obj, "risk_policy_id")
        if ref_id and _is_pool_envelope_mapping(pool_obj):
            return ref_id, None, False, "risk_policy_ref", pool_meta, pool_tenant_id
        if not _is_pool_envelope_mapping(pool_obj) and _is_policy_content_mapping(pool_obj):
            return None, pool_obj, True, "pool_inline", pool_meta, pool_tenant_id
        return None, None, False, "unresolved", pool_meta, pool_tenant_id

    pool_meta = (
        dict(getattr(pool_obj, "metadata", None))
        if isinstance(getattr(pool_obj, "metadata", None), Mapping)
        else {}
    )
    pool_tenant_id = _optional_text(getattr(pool_obj, "tenant_id", None)) or _first_text_or_none(
        pool_meta, "tenant_id"
    )
    ref_attr = _optional_text(getattr(pool_obj, "risk_policy_ref", None))
    has_meta_inline = "risk_policy" in pool_meta and pool_meta.get("risk_policy") is not None
    inline_policy = pool_meta.get("risk_policy") if has_meta_inline else None
    if ref_attr:
        return ref_attr, inline_policy, has_meta_inline, "risk_policy_ref", pool_meta, pool_tenant_id
    if has_meta_inline:
        return None, inline_policy, True, "pool_metadata_inline", pool_meta, pool_tenant_id
    return None, None, False, "unresolved", pool_meta, pool_tenant_id


def resolve_pool_risk_policy_binding(
    *,
    capital_pool_id: str | None = None,
    pool: Any | None = None,
    binding: Mapping[str, Any] | None = None,
    capital_pool_reader: Callable[[str], Any] | None = None,
    risk_policy_ref: str | None = None,
    risk_policy_loader: Callable[[str], Any] | None = None,
    pool_store_path: str | os.PathLike[str] | None = None,
    expected_tenant_id: str | None = None,
    expected_stage: str | None = "paper",
    expected_version: str | None = None,
    expected_digest: str | None = None,
    require_active: bool = True,
    require_limits: Sequence[str] = ("gross_limit",),
    requested_profile: str | None = None,
    require_approved_profile_mapping: bool = False,
    as_of: str | None = None,
    raise_on_error: bool = False,
) -> BoundPoolRiskPolicyResolution:
    """Resolve and validate a CapitalPool's bound RiskPolicy with full provenance and guard diagnostics."""
    b_map = dict(binding) if isinstance(binding, Mapping) else {}
    b_meta = dict(b_map.get("metadata")) if isinstance(b_map.get("metadata"), Mapping) else {}
    pool_sub = dict(b_map.get("capital_pool")) if isinstance(b_map.get("capital_pool"), Mapping) else {}

    resolved_pool_id = str(
        b_map.get("capital_pool_id")
        or (
            (pool.get("pool_id") or pool.get("capital_pool_id"))
            if isinstance(pool, Mapping)
            else getattr(pool, "pool_id", None)
        )
        or capital_pool_id
        or os.getenv("PANTHEON_CAPITAL_POOL_ID", "")
    ).strip()

    pool_obj = pool
    if pool_obj is None and capital_pool_reader is not None:
        pool_obj = capital_pool_reader(resolved_pool_id)

    resolved_ref, inline_policy, explicit_inline_key, source, pool_meta, pool_tenant_id = (
        _extract_pool_policy_candidates(pool_obj)
    )

    if not resolved_ref and not explicit_inline_key and pool is None:
        pool_record = read_capital_pool_record(resolved_pool_id, pool_store_path=pool_store_path)
        if pool_record is not None:
            pool_obj = pool_record
            resolved_ref, inline_policy, explicit_inline_key, source, pool_meta, pool_tenant_id = (
                _extract_pool_policy_candidates(pool_record)
            )

    if not resolved_ref and not explicit_inline_key and b_map:
        has_b_inline = "risk_policy" in b_map and b_map.get("risk_policy") is not None
        has_sub_inline = "risk_policy" in pool_sub and pool_sub.get("risk_policy") is not None
        if has_b_inline or has_sub_inline:
            inline_policy = b_map.get("risk_policy") if has_b_inline else pool_sub.get("risk_policy")
            explicit_inline_key = True
            source = "binding_inline"
        else:
            resolved_ref = (
                _first_text_or_none(b_map, "risk_policy_ref", "risk_policy_id")
                or _first_text_or_none(pool_sub, "risk_policy_ref", "risk_policy_id")
                or _first_text_or_none(b_meta, "risk_policy_ref", "risk_policy_id")
            )
            if resolved_ref:
                source = "risk_policy_ref"

    if not resolved_ref and not explicit_inline_key:
        resolved_ref = (
            _optional_text(risk_policy_ref)
            or _optional_text(os.getenv("PANTHEON_RISK_POLICY_REF"))
            or _optional_text(os.getenv("CAPITAL_RISK_POLICY_REF"))
        )
        if resolved_ref:
            source = "risk_policy_ref"

    clean_expected_tenant = _optional_text(expected_tenant_id)
    binding_tenant = (
        _first_text_or_none(b_map, "tenant_id")
        or _first_text_or_none(pool_sub, "tenant_id")
        or _first_text_or_none(b_meta, "tenant_id")
    )
    effective_tenant_id = clean_expected_tenant or pool_tenant_id or binding_tenant
    clean_stage = _optional_text(expected_stage)
    clean_profile = (
        _optional_text(requested_profile)
        or _first_text_or_none(pool_meta, "requested_risk_profile", "risk_profile", "requested_risk")
        or _first_text_or_none(b_meta, "requested_risk_profile", "risk_profile", "requested_risk")
    )
    needs_approved_mapping = bool(
        require_approved_profile_mapping
        or pool_meta.get("require_approved_profile_mapping") is True
        or b_meta.get("require_approved_profile_mapping") is True
    )

    def _fail(
        status: str,
        reason_code: str,
        reason: str,
        *,
        policy_obj: RiskPolicy | None = None,
        policy_tenant: str | None = None,
        profile_status: str = "not_applicable",
        owner_decision: Mapping[str, Any] | None = None,
        exc_type: type[Exception] = RiskPolicyError,
    ) -> BoundPoolRiskPolicyResolution:
        if raise_on_error:
            raise exc_type(f"{reason} ({reason_code})")
        return BoundPoolRiskPolicyResolution(
            capital_pool_id=resolved_pool_id,
            resolved=False,
            status=status,
            source=source,
            policy=policy_obj,
            risk_policy_id=policy_obj.risk_policy_id if policy_obj is not None else resolved_ref,
            risk_policy_version=policy_obj.version if policy_obj is not None else None,
            risk_policy_digest=risk_policy_digest(policy_obj) if policy_obj is not None else None,
            tenant_id=effective_tenant_id,
            policy_tenant_id=policy_tenant,
            stage=clean_stage,
            requested_profile=clean_profile,
            profile_mapping_status=profile_status,
            effective_limits=_policy_effective_limits(policy_obj) if policy_obj is not None else {},
            reason_code=reason_code,
            reason=reason,
            allow_new_or_increased_risk=False,
            allow_risk_reducing_actions=True,
            owner_decision_request=owner_decision,
        )

    if clean_expected_tenant and pool_tenant_id and clean_expected_tenant != pool_tenant_id:
        return _fail(
            "wrong_tenant",
            "risk_policy_wrong_tenant",
            f"Capital pool {resolved_pool_id!r} belongs to tenant {pool_tenant_id!r}, expected {clean_expected_tenant!r}",
            policy_tenant=pool_tenant_id,
        )

    if needs_approved_mapping and clean_profile and not resolved_ref and not explicit_inline_key:
        decision_req = {
            "policy_owner": "services/capital/risk_policy.py",
            "governance_surface": "services/control-plane/governance/risk_policy.py",
            "requested_profile": clean_profile,
            "capital_pool_id": resolved_pool_id,
            "tenant_id": effective_tenant_id,
            "stage": clean_stage or "paper",
            "decision_required": (
                f"No approved capital_pool_activation RiskPolicy limit mapping exists for risk profile "
                f"{clean_profile!r}; policy owner must approve explicit gross_limit/net_limit/max_leverage/"
                f"max_single_name_weight or bind an approved risk_policy_ref."
            ),
            "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
        }
        return _fail(
            "unresolved_profile_mapping",
            "risk_policy_profile_mapping_unapproved",
            decision_req["decision_required"],
            profile_status="unresolved_owner_decision_required",
            owner_decision=decision_req,
        )

    if not resolved_ref and not explicit_inline_key:
        return _fail(
            "missing",
            "risk_policy_missing",
            f"Capital pool {resolved_pool_id!r} is missing an approved risk_policy_ref or metadata.risk_policy",
        )

    raw_policy_mapping: Mapping[str, Any] | None = None
    policy_model: RiskPolicy | None = None
    authoritative_ref_policy: RiskPolicy | None = None

    if resolved_ref:
        try:
            if risk_policy_loader is not None:
                loaded_raw = risk_policy_loader(resolved_ref)
            else:
                loaded_raw = load_risk_policy(resolved_ref)
            if isinstance(loaded_raw, Mapping):
                raw_policy_mapping = dict(loaded_raw)
            authoritative_ref_policy = normalize_risk_policy(loaded_raw, default_id=resolved_ref)
            policy_model = authoritative_ref_policy
        except RiskPolicyLoadError as exc:
            if explicit_inline_key and risk_policy_loader is None:
                # Allow an inline policy contract whose risk_policy_id matches risk_policy_ref when no external file exists.
                inline_id = (
                    inline_policy.risk_policy_id
                    if isinstance(inline_policy, RiskPolicy)
                    else (
                        _first_text_or_none(inline_policy, "risk_policy_id", "policy_id", "risk_policy_ref")
                        if isinstance(inline_policy, Mapping)
                        else None
                    )
                )
                if inline_id == resolved_ref:
                    resolved_ref = None
                    source = "pool_metadata_inline"
                else:
                    return _fail(
                        "missing",
                        "risk_policy_missing",
                        str(exc),
                        exc_type=RiskPolicyLoadError,
                    )
            else:
                return _fail(
                    "missing",
                    "risk_policy_missing",
                    str(exc),
                    exc_type=RiskPolicyLoadError,
                )
        except RiskPolicyError as exc:
            return _fail("invalid", "risk_policy_invalid", str(exc))

    if policy_model is None and explicit_inline_key:
        if isinstance(inline_policy, RiskPolicy):
            policy_model = inline_policy
            raw_policy_mapping = inline_policy.to_dict()
        elif isinstance(inline_policy, Mapping):
            if not _is_policy_content_mapping(inline_policy):
                return _fail(
                    "invalid",
                    "risk_policy_invalid",
                    "Malformed risk policy: inline risk_policy is empty or missing policy identity and limits",
                )
            raw_policy_mapping = dict(inline_policy)
            try:
                policy_model = normalize_risk_policy(inline_policy)
            except RiskPolicyError as exc:
                return _fail("invalid", "risk_policy_invalid", str(exc))
        else:
            return _fail(
                "invalid",
                "risk_policy_invalid",
                f"Malformed risk policy: expected RiskPolicy or mapping, got {type(inline_policy).__name__}",
            )

    assert policy_model is not None
    policy_meta = dict(policy_model.metadata) if isinstance(policy_model.metadata, Mapping) else {}
    if isinstance(raw_policy_mapping, Mapping):
        for key in (
            "tenant_id",
            "expires_at",
            "effective_to",
            "effective_from",
            "superseded_by",
            "stale",
            "requested_profile",
            "risk_profile",
            "explicit_policy",
        ):
            if key in raw_policy_mapping and raw_policy_mapping.get(key) is not None and key not in policy_meta:
                policy_meta[key] = raw_policy_mapping.get(key)

    # If both an authoritative ref policy and an inline snapshot exist on the pool, ensure the snapshot is not stale.
    if authoritative_ref_policy is not None and explicit_inline_key and isinstance(inline_policy, (RiskPolicy, Mapping)):
        try:
            inline_norm = (
                inline_policy
                if isinstance(inline_policy, RiskPolicy)
                else normalize_risk_policy(inline_policy, default_id=authoritative_ref_policy.risk_policy_id)
            )
        except RiskPolicyError as exc:
            return _fail("invalid", "risk_policy_invalid", str(exc), policy_obj=authoritative_ref_policy)
        if inline_norm.version != authoritative_ref_policy.version or (
            risk_policy_digest(inline_norm) != risk_policy_digest(authoritative_ref_policy)
        ):
            return _fail(
                "stale",
                "risk_policy_stale",
                (
                    f"Bound inline risk policy snapshot for {authoritative_ref_policy.risk_policy_id!r} "
                    f"(version={inline_norm.version!r}) is stale against authoritative version "
                    f"{authoritative_ref_policy.version!r}"
                ),
                policy_obj=inline_norm,
            )

    policy_tenant_id = _first_text_or_none(policy_meta, "tenant_id")
    if effective_tenant_id and policy_tenant_id and effective_tenant_id != policy_tenant_id:
        return _fail(
            "wrong_tenant",
            "risk_policy_wrong_tenant",
            (
                f"Risk policy {policy_model.risk_policy_id!r} belongs to tenant {policy_tenant_id!r}, "
                f"cannot bind to tenant {effective_tenant_id!r}"
            ),
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )

    # Staleness checks: explicit stale flag, superseded_by, expired status, expires_at/effective_to, expected version/digest.
    raw_status = str(policy_model.status or "").strip().lower()
    if raw_status in {"stale", "superseded", "expired"}:
        return _fail(
            "stale",
            "risk_policy_stale",
            f"Risk policy {policy_model.risk_policy_id!r} status is {policy_model.status!r}",
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )
    if policy_meta.get("stale") is True or pool_meta.get("risk_policy_stale") is True:
        return _fail(
            "stale",
            "risk_policy_stale",
            f"Risk policy {policy_model.risk_policy_id!r} is marked stale",
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )
    superseded_by = _first_text_or_none(policy_meta, "superseded_by") or _first_text_or_none(
        pool_meta, "risk_policy_superseded_by"
    )
    if superseded_by:
        return _fail(
            "stale",
            "risk_policy_stale",
            f"Risk policy {policy_model.risk_policy_id!r} version {policy_model.version!r} is superseded by {superseded_by!r}",
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )

    expiry_raw = _first_text_or_none(policy_meta, "expires_at", "effective_to") or _first_text_or_none(
        pool_meta, "risk_policy_expires_at"
    )
    if expiry_raw:
        expiry_dt = _parse_iso_timestamp(expiry_raw)
        now_dt = _parse_iso_timestamp(as_of) or datetime.now(timezone.utc)
        if expiry_dt is None:
            return _fail(
                "invalid",
                "risk_policy_invalid",
                f"Risk policy {policy_model.risk_policy_id!r} has malformed expiration timestamp {expiry_raw!r}",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )
        if expiry_dt <= now_dt:
            return _fail(
                "stale",
                "risk_policy_stale",
                f"Risk policy {policy_model.risk_policy_id!r} expired at {expiry_raw}",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )

    target_expected_version = (
        _optional_text(expected_version)
        or _first_text_or_none(pool_meta, "expected_risk_policy_version")
        or _first_text_or_none(b_map, "expected_risk_policy_version", "risk_policy_version")
        or _first_text_or_none(b_meta, "expected_risk_policy_version", "risk_policy_version")
    )
    if target_expected_version and str(policy_model.version) != target_expected_version:
        return _fail(
            "stale",
            "risk_policy_stale",
            (
                f"Risk policy {policy_model.risk_policy_id!r} version {policy_model.version!r} "
                f"does not match expected version {target_expected_version!r}"
            ),
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )

    computed_digest = risk_policy_digest(policy_model)
    target_expected_digest = (
        _optional_text(expected_digest)
        or _first_text_or_none(pool_meta, "expected_risk_policy_digest")
        or _first_text_or_none(b_map, "expected_risk_policy_digest", "risk_policy_digest")
        or _first_text_or_none(b_meta, "expected_risk_policy_digest", "risk_policy_digest")
    )
    if target_expected_digest and computed_digest != target_expected_digest:
        return _fail(
            "stale",
            "risk_policy_stale",
            (
                f"Risk policy {policy_model.risk_policy_id!r} digest {computed_digest!r} "
                f"does not match expected digest {target_expected_digest!r}"
            ),
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )

    # Scalar limit sanity checks (no negative scalar limits; positive exposure limits when configured).
    for field_name in _NON_NEGATIVE_SCALAR_LIMIT_FIELDS:
        val = getattr(policy_model, field_name, None)
        if val is None:
            continue
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(float(val)):
            return _fail(
                "invalid",
                "risk_policy_invalid_limit",
                f"Risk policy {policy_model.risk_policy_id!r} limit {field_name} is not a finite number",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )
        if field_name in _POSITIVE_EXPOSURE_LIMIT_FIELDS and float(val) <= 0.0:
            return _fail(
                "invalid",
                "risk_policy_invalid_limit",
                f"Risk policy {policy_model.risk_policy_id!r} limit {field_name}={val!r} must be > 0",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )
        if float(val) < 0.0:
            return _fail(
                "invalid",
                "risk_policy_invalid_limit",
                f"Risk policy {policy_model.risk_policy_id!r} limit {field_name}={val!r} must be >= 0",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )

    if require_active and raw_status not in _ACTIVE_POLICY_STATUSES:
        return _fail(
            "invalid",
            "risk_policy_inactive",
            f"Risk policy {policy_model.risk_policy_id!r} is not active (status={policy_model.status!r})",
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )

    if clean_stage:
        if policy_model.allowed_stages and clean_stage not in policy_model.allowed_stages:
            return _fail(
                "invalid",
                "risk_policy_stage_not_allowed",
                (
                    f"Risk policy {policy_model.risk_policy_id!r} does not allow stage {clean_stage!r} "
                    f"(allowed_stages={list(policy_model.allowed_stages)!r})"
                ),
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )
        if not policy_model.allowed_stages and require_limits:
            return _fail(
                "invalid",
                "risk_policy_stage_not_allowed",
                f"Risk policy {policy_model.risk_policy_id!r} does not declare allowed_stages for stage {clean_stage!r}",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )

    for required_limit in require_limits:
        limit_val = getattr(policy_model, required_limit, None)
        if limit_val in (None, (), {}, []):
            return _fail(
                "invalid",
                "risk_policy_missing_required_limit",
                f"Risk policy {policy_model.risk_policy_id!r} is missing required limit {required_limit!r}",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )
        if required_limit in _POSITIVE_EXPOSURE_LIMIT_FIELDS and float(limit_val) <= 0.0:
            return _fail(
                "invalid",
                "risk_policy_missing_required_limit",
                f"Risk policy {policy_model.risk_policy_id!r} required limit {required_limit!r} must be > 0",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )

    is_explicit_contract = bool(
        source == "risk_policy_ref"
        or policy_meta.get("explicit_policy") is True
        or pool_meta.get("risk_policy_explicit") is True
        or not str(policy_model.risk_policy_id).startswith("paper-inline-policy:")
    )
    profile_status = "explicit_policy" if is_explicit_contract else "default_paper_baseline"

    return BoundPoolRiskPolicyResolution(
        capital_pool_id=resolved_pool_id,
        resolved=True,
        status="valid",
        source=source,
        policy=policy_model,
        risk_policy_id=policy_model.risk_policy_id,
        risk_policy_version=policy_model.version,
        risk_policy_digest=computed_digest,
        tenant_id=effective_tenant_id,
        policy_tenant_id=policy_tenant_id or effective_tenant_id,
        stage=clean_stage,
        requested_profile=clean_profile,
        profile_mapping_status=profile_status,
        effective_limits=_policy_effective_limits(policy_model),
        reason_code=None,
        reason=None,
        allow_new_or_increased_risk=True,
        allow_risk_reducing_actions=True,
        owner_decision_request=None,
    )


def resolve_bound_pool_risk_policy(
    *,
    capital_pool_id: str | None = None,
    binding: Mapping[str, Any] | None = None,
    capital_pool_reader: Callable[[str], Any] | None = None,
    risk_policy_ref: str | None = None,
    risk_policy_loader: Callable[[str], Any] | None = None,
    pool_store_path: str | os.PathLike[str] | None = None,
    expected_tenant_id: str | None = None,
    expected_stage: str | None = None,
    expected_version: str | None = None,
    expected_digest: str | None = None,
    require_active: bool = False,
    require_limits: Sequence[str] = (),
) -> RiskPolicy | None:
    """Resolve and load the bound CapitalPool's active ``RiskPolicy`` (never from signal metadata)."""
    resolution = resolve_pool_risk_policy_binding(
        capital_pool_id=capital_pool_id,
        binding=binding,
        capital_pool_reader=capital_pool_reader,
        risk_policy_ref=risk_policy_ref,
        risk_policy_loader=risk_policy_loader,
        pool_store_path=pool_store_path,
        expected_tenant_id=expected_tenant_id,
        expected_stage=expected_stage,
        expected_version=expected_version,
        expected_digest=expected_digest,
        require_active=require_active,
        require_limits=require_limits,
        raise_on_error=False,
    )
    if resolution.resolved:
        return resolution.policy
    if resolution.status == "missing" and resolution.source == "unresolved":
        return None
    if resolution.status == "missing" and resolution.source == "risk_policy_ref":
        raise RiskPolicyLoadError(f"{resolution.reason} ({resolution.reason_code})")
    raise RiskPolicyError(f"{resolution.reason} ({resolution.reason_code})")



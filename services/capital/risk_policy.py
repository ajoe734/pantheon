"""First-class RiskPolicy evaluator contract.

The Capital Pool Plane owns risk policy definitions.  Other services should
consume this module as the shared evaluator contract instead of interpreting
``risk_policy_ref`` as metadata-only text.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
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
    min_cash_reserve_pct: float | None = None
    min_cash_reserve: float | None = None
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
            min_cash_reserve_pct=_policy_alias(payload, "min_cash_reserve_pct", "cash_reserve_pct"),
            min_cash_reserve=_policy_alias(payload, "min_cash_reserve", "cash_reserve"),
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
    cash_reserve_pct: float | None = None
    cash_reserve: float | None = None
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
            cash_reserve_pct=_optional_float(
                payload.get("cash_reserve_pct", metadata.get("cash_reserve_pct"))
            ),
            cash_reserve=_optional_float(
                payload.get("cash_reserve", metadata.get("cash_reserve"))
            ),
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
                if (
                    facts["target_type"] in {"capital_pool_activation", "capital_binding_activation"}
                    and facts.get("target_weights") == {}
                    and facts.get("gross_exposure") == 0.0
                    and limit in (
                        "max_single_name_weight",
                        "allowed_asset_classes",
                        "forbidden_asset_classes",
                        "allowed_strategy_families",
                        "forbidden_strategy_families",
                    )
                ):
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
        self._check_cash_reserve(resolved_policy, resolved_context, checks)
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

    def _check_cash_reserve(
        self,
        policy: RiskPolicy,
        context: RiskPolicyEvaluationContext,
        checks: list[RiskPolicyCheck],
    ) -> None:
        if policy.min_cash_reserve_pct is not None and context.cash_reserve_pct is not None:
            if context.cash_reserve_pct + 1e-12 >= policy.min_cash_reserve_pct:
                checks.append(
                    _passed(
                        "min_cash_reserve_pct",
                        "cash_reserve_pct meets RiskPolicy minimum",
                        context.cash_reserve_pct,
                        limit=policy.min_cash_reserve_pct,
                    )
                )
            else:
                checks.append(
                    _failed(
                        "min_cash_reserve_pct",
                        "cash_reserve_breach",
                        f"cash_reserve_pct {context.cash_reserve_pct:.6g} is below RiskPolicy min_cash_reserve_pct {policy.min_cash_reserve_pct:.6g}",
                        limit=policy.min_cash_reserve_pct,
                        observed=context.cash_reserve_pct,
                    )
                )
        if policy.min_cash_reserve is not None and context.cash_reserve is not None:
            if context.cash_reserve + 1e-9 >= policy.min_cash_reserve:
                checks.append(
                    _passed(
                        "min_cash_reserve",
                        "cash_reserve meets RiskPolicy minimum",
                        context.cash_reserve,
                        limit=policy.min_cash_reserve,
                    )
                )
            else:
                checks.append(
                    _failed(
                        "min_cash_reserve",
                        "cash_reserve_breach",
                        f"cash_reserve {context.cash_reserve:.6g} is below RiskPolicy min_cash_reserve {policy.min_cash_reserve:.6g}",
                        limit=policy.min_cash_reserve,
                        observed=context.cash_reserve,
                    )
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
    "name",
    "status",
    "gross_limit",
    "net_limit",
    "max_leverage",
    "max_single_name_weight",
    "max_sector_exposure",
    "max_factor_exposure",
    "turnover_limit",
    "allowed_stages",
    "allowed_asset_classes",
    "forbidden_asset_classes",
    "allowed_strategy_families",
    "forbidden_strategy_families",
    "max_strategy_family_concentration",
    "max_target_overlap",
    "max_signal_correlation",
    "allowed_order_types",
    "allowed_time_in_force",
    "liquidity_constraints",
    "drawdown_actions",
    "pause_rules",
    "liquidation_rules",
    "max_canary_capital_scale_pct",
    "max_canary_gross_scale_pct",
    "min_cash_reserve_pct",
    "min_cash_reserve",
    "kill_switch_triggers",
)

POLICY_PROVENANCE_DIGEST_KEYS: tuple[str, ...] = (
    "tenant_id",
    "effective_from",
    "effective_to",
    "expires_at",
    "superseded_by",
    "stale",
    "requested_profile",
    "risk_profile",
    "explicit_policy",
)

_RECOGNIZED_POLICY_LIMIT_KEYS: frozenset[str] = frozenset(
    {
        *_FACT_OF_LIMIT.keys(),
        "max_single_weight",
        "max_pairwise_correlation",
        "min_cash_reserve_pct",
        "cash_reserve_pct",
        "min_cash_reserve",
        "cash_reserve",
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
    "max_signal_correlation",
    "max_canary_capital_scale_pct",
    "max_canary_gross_scale_pct",
    "min_cash_reserve_pct",
    "min_cash_reserve",
)

_FLEX_EXPOSURE_LIMIT_FIELDS: tuple[str, ...] = (
    "max_sector_exposure",
    "max_factor_exposure",
    "max_strategy_family_concentration",
)


_MAX_LIMITS = (
    "gross_limit", "net_limit", "max_single_name_weight", "max_leverage", "turnover_limit",
    "max_target_overlap", "max_signal_correlation", "max_canary_capital_scale_pct",
    "max_canary_gross_scale_pct", "max_sector_exposure", "max_factor_exposure",
    "max_strategy_family_concentration",
)
_MIN_LIMITS = ("min_cash_reserve_pct", "min_cash_reserve")


def _limit_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _compare_limit(kind: str, cand: Any, cur: Any) -> str:
    """One limit: 'stricter' | 'equal' | 'looser'. Missing is unlimited (max: +inf, min: 0)."""
    if isinstance(cand, Mapping) or isinstance(cur, Mapping):
        cm = cand if isinstance(cand, Mapping) else {}
        um = cur if isinstance(cur, Mapping) else {}
        scalar_c = None if isinstance(cand, Mapping) else _limit_number(cand)
        scalar_u = None if isinstance(cur, Mapping) else _limit_number(cur)
        results = []
        for key in sorted(set(cm) | set(um)):
            results.append(_compare_limit(
                kind, _limit_number(cm.get(key)) if key in cm else scalar_c,
                _limit_number(um.get(key)) if key in um else scalar_u))
        if not results:
            results.append(_compare_limit(kind, scalar_c, scalar_u))
        for verdict in ("looser", "stricter"):
            if verdict in results:
                return verdict
        return "equal"
    c, u = _limit_number(cand), _limit_number(cur)
    if kind == "max":
        c = math.inf if c is None else c
        u = math.inf if u is None else u
        return "equal" if c == u else ("stricter" if c < u else "looser")
    c = 0.0 if c is None else c
    u = 0.0 if u is None else u
    return "equal" if c == u else ("stricter" if c > u else "looser")


def compare_risk_policies(
    candidate: Mapping[str, Any] | None, current: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Risk direction of a candidate pool policy against the pool's effective policy.

    Every limit equal or stricter -> 'reduce' (some stricter) or 'neutral' (all equal);
    any looser limit, or no effective policy -> 'increase'. Returns the direction and the
    per-limit comparison for the proposal subject.
    """
    cand = candidate if isinstance(candidate, Mapping) else {}
    if not isinstance(current, Mapping) or not current:
        return {"risk_direction": "increase", "risk_comparison": {"_effective_policy": "absent"}}
    cur = current
    comparison: dict[str, str] = {}
    for name in (*_MAX_LIMITS, *_MIN_LIMITS):
        if name not in cand and name not in cur:
            continue
        comparison[name] = _compare_limit("min" if name in _MIN_LIMITS else "max", cand.get(name), cur.get(name))
    values = set(comparison.values())
    direction = "increase" if "looser" in values else ("reduce" if "stricter" in values else "neutral")
    return {"risk_direction": direction, "risk_comparison": comparison}


def require_valid_any(approval: Any, *, expected: Mapping[str, Any], **kwargs: Any) -> Any:
    """``approval.require_valid`` where an expected value may be a set of accepted values.

    The computed risk direction is any of increase, reduce or neutral; evidence satisfying any one member
    passes, every other expected field is matched exactly. The first failure is raised when none passes.
    """
    candidates: list[dict[str, Any]] = [dict(expected)]
    for key, value in expected.items():
        if isinstance(value, (set, frozenset)):
            candidates = [{**c, key: member} for c in candidates for member in sorted(value)]
    failure: Exception | None = None
    for candidate in candidates:
        try:
            return approval.require_valid(expected=candidate, **kwargs)
        except Exception as exc:
            failure = failure or exc
    raise failure  # type: ignore[misc]


def risk_policy_digest(policy: RiskPolicy | Mapping[str, Any] | None) -> str:
    """Compute the canonical SHA-256 digest of a RiskPolicy binding all executable limits and provenance."""
    if policy is None:
        return ""
    resolved = policy if isinstance(policy, RiskPolicy) else normalize_risk_policy(policy)
    raw = resolved.to_dict()
    projected: dict[str, Any] = {
        key: raw.get(key)
        for key in POLICY_DIGEST_FIELDS
        if raw.get(key) not in (None, (), {}, [])
    }
    meta = dict(resolved.metadata) if isinstance(resolved.metadata, Mapping) else {}
    provenance = {
        key: meta.get(key)
        for key in POLICY_PROVENANCE_DIGEST_KEYS
        if meta.get(key) not in (None, (), {}, [], "")
    }
    if provenance:
        projected["metadata"] = provenance
    encoded = json.dumps(projected, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capital_pool_approval_digest(pool: Any) -> str:
    """Compute the canonical CapitalPool approval_digest matching capital.main.pool_digest."""
    if pool is None:
        return ""
    is_map = isinstance(pool, Mapping)
    payload: dict[str, Any] = {
        field_name: (pool.get(field_name) if is_map else getattr(pool, field_name, None))
        for field_name in (
            "pool_id",
            "owner_id",
            "owner_type",
            "currency",
            "budget",
            "risk_policy_ref",
            "single_runtime_enforced",
        )
    }
    if not payload.get("pool_id") and is_map and pool.get("capital_pool_id"):
        payload["pool_id"] = pool.get("capital_pool_id")
    if isinstance(payload.get("budget"), (int, float)) and not isinstance(payload.get("budget"), bool):
        payload["budget"] = float(payload["budget"])
    pool_meta = pool.get("metadata") if is_map else getattr(pool, "metadata", None)
    if isinstance(pool_meta, Mapping) and isinstance(pool_meta.get("risk_policy"), (Mapping, RiskPolicy)):
        try:
            digest = risk_policy_digest(pool_meta["risk_policy"])
            if digest:
                payload["risk_policy_digest"] = digest
        except Exception:
            pass
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
        "min_cash_reserve_pct",
        "min_cash_reserve",
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
    profile_mapping_approval_id: str | None = None
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
        if self.profile_mapping_approval_id is not None:
            payload["profile_mapping_approval_id"] = self.profile_mapping_approval_id
        if self.policy is not None:
            payload["policy"] = self.policy.to_dict()
        if self.owner_decision_request is not None:
            payload["owner_decision_request"] = dict(self.owner_decision_request)
        return payload


def _extract_decision_id_reference(item: Any) -> str | None:
    if isinstance(item, str):
        return _optional_text(item)
    if isinstance(item, Mapping):
        return _first_text_or_none(
            item,
            "decision_id",
            "approval_decision_id",
            "profile_mapping_approval_id",
        )
    if hasattr(item, "decision_id"):
        return _optional_text(getattr(item, "decision_id", None))
    return None


_COMPOSE_VAR_RE = re.compile(r"^\$\{([A-Z0-9_]+):-(.*)\}$")


def _unwrap_compose_value(raw: Any, source: Mapping[str, str]) -> str:
    text = str(raw if raw is not None else "").strip()
    for _ in range(4):
        match = _COMPOSE_VAR_RE.match(text)
        if match is None:
            break
        var_name, default_val = match.groups()
        candidate = str(source.get(var_name) or "").strip()
        if candidate and candidate != text and not _COMPOSE_VAR_RE.match(candidate):
            text = candidate
            break
        text = default_val.strip()
    return text


def _capital_owner_http_get_json(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> Any:
    from services.governance.approval_authority import (
        ApprovalInvalid,
        ApprovalUnavailable,
        NoOwnerRedirect,
    )

    opener = urllib.request.build_opener(NoOwnerRedirect())
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ApprovalUnavailable("Capital owner did not return 200 for decision read")
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise ApprovalInvalid("Capital owner denied exact decision read") from exc
        raise ApprovalUnavailable("Capital owner approval read unavailable") from exc
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise ApprovalUnavailable("Capital owner approval read unavailable or malformed") from exc


class CapitalOwnerApprovalReader:
    """Scoped read-only ApprovalEvidence reader via the Capital owner boundary.

    Paper signal producer and paper runtime workers mount only the read-only
    ``paper-capital-reader`` principal (``DEPLOYMENT_CAPITAL_SERVICE_TOKEN`` /
    ``RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN``) and ``PANTHEON_CAPITAL_API_URL``,
    never direct Governance credentials. This reader fetches the pool's bound
    ``capital_pool_activation`` ApprovalEvidence JSON from the Capital owner.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str = "",
        token_provider: Callable[[], str] | None = None,
        pool_id: str | None = None,
        tenant_id: str | None = None,
        service_name: str = "runtime-manager",
        timeout_seconds: float = 5.0,
        fetch_json: Callable[[str, Mapping[str, str], float], Any] | None = None,
    ) -> None:
        from services.governance.approval_authority import ApprovalUnavailable

        try:
            parsed = urllib.parse.urlsplit(base_url)
        except ValueError as exc:
            raise ApprovalUnavailable("Capital owner reader URL is malformed") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (not service_token and token_provider is None)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ApprovalUnavailable("Capital owner reader URL, scoped principal and timeout required")
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token.strip()
        self.token_provider = token_provider
        self.pool_id = _optional_text(pool_id)
        self.tenant_id = _optional_text(tenant_id)
        self.service_name = _optional_text(service_name) or "runtime-manager"
        self.timeout_seconds = timeout_seconds
        self._fetch_json = fetch_json

    def _headers(self, *, tenant_id: str | None = None) -> dict[str, str]:
        from services.governance.approval_authority import ApprovalUnavailable

        try:
            token = (self.token_provider() if self.token_provider is not None else self.service_token).strip()
            if not token:
                raise RuntimeError("Missing service credential")
        except RuntimeError as exc:
            raise ApprovalUnavailable("Capital owner read principal unavailable") from exc
        headers = {
            "Accept": "application/json",
            "Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}",
            "X-Pantheon-Service": self.service_name,
        }
        effective_tenant = _optional_text(tenant_id) or self.tenant_id
        if effective_tenant:
            headers["X-Tenant-Id"] = effective_tenant
        return headers

    def get(
        self,
        decision_id: str,
        *,
        pool_id: str | None = None,
        tenant_id: str | None = None,
    ) -> Any:
        from services.governance.approval_authority import (
            ApprovalEvidence,
            ApprovalInvalid,
            ApprovalUnavailable,
        )

        clean_id = _optional_text(decision_id)
        if not clean_id:
            raise ApprovalInvalid("Exact decision ID required")
        effective_pool_id = _optional_text(pool_id) or self.pool_id
        if effective_pool_id:
            url = (
                f"{self.base_url}/api/capital-pools/"
                f"{urllib.parse.quote(effective_pool_id, safe='')}/approvals/"
                f"{urllib.parse.quote(clean_id, safe='')}"
            )
        else:
            url = f"{self.base_url}/api/capital/approvals/{urllib.parse.quote(clean_id, safe='')}"
        headers = self._headers(tenant_id=tenant_id)
        fetcher = self._fetch_json or _capital_owner_http_get_json
        try:
            body = fetcher(url, headers, self.timeout_seconds)
        except (ApprovalInvalid, ApprovalUnavailable):
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise ApprovalInvalid("Capital owner denied exact decision read") from exc
            raise ApprovalUnavailable("Capital owner approval read unavailable") from exc
        except Exception as exc:
            raise ApprovalUnavailable("Capital owner approval read unavailable or malformed") from exc
        if not isinstance(body, Mapping):
            raise ApprovalInvalid("Malformed Capital owner approval decision")
        try:
            evidence = ApprovalEvidence.model_validate(dict(body))
        except ValueError as exc:
            raise ApprovalInvalid("Malformed Capital owner approval decision") from exc
        if evidence.decision_id != clean_id:
            raise ApprovalInvalid("Capital owner exact decision ID mismatch")
        effective_tenant = _optional_text(tenant_id) or self.tenant_id
        if effective_tenant and evidence.tenant_id != effective_tenant:
            raise ApprovalInvalid("Capital owner approval tenant mismatch")
        if effective_pool_id and evidence.target_id != effective_pool_id:
            ev_dump = evidence.model_dump()
            ev_meta = ev_dump.get("metadata") if isinstance(ev_dump.get("metadata"), Mapping) else {}
            ev_subj = ev_meta.get("subject") if isinstance(ev_meta.get("subject"), Mapping) else {}
            subj_pol_id = _optional_text(ev_subj.get("risk_policy_id"))
            if not subj_pol_id or evidence.target_id != subj_pol_id:
                raise ApprovalInvalid("Capital owner approval pool target mismatch")
        return evidence

    def list_for_target(
        self,
        *,
        target_type: str = "capital_pool_activation",
        target_id: str | None = None,
        pool_id: str | None = None,
        tenant_id: str | None = None,
        decision_state: str | None = "decided",
        max_items: int = 16,
        history: bool = False,
        now: datetime | None = None,
    ) -> list[Any]:
        from services.governance.approval_authority import (
            ApprovalEvidence,
            ApprovalInvalid,
            ApprovalUnavailable,
            BoundedApprovalList,
            bound_candidate_history,
        )

        effective_pool_id = _optional_text(pool_id) or self.pool_id or _optional_text(target_id)
        if not effective_pool_id:
            return BoundedApprovalList()
        url = (
            f"{self.base_url}/api/capital-pools/"
            f"{urllib.parse.quote(effective_pool_id, safe='')}/approvals"
        )
        headers = self._headers(tenant_id=tenant_id)
        fetcher = self._fetch_json or _capital_owner_http_get_json
        try:
            body = fetcher(url, headers, self.timeout_seconds)
        except (ApprovalInvalid, ApprovalUnavailable):
            return BoundedApprovalList()
        except Exception:
            return BoundedApprovalList()
        raw_items = body.get("items") if isinstance(body, Mapping) else body
        if not isinstance(raw_items, list):
            return BoundedApprovalList()
        effective_tenant = _optional_text(tenant_id) or self.tenant_id
        bound = max(1, min(int(max_items or 16), 64))
        results: list[Any] = []
        overflow = bool(body.get("overflow")) if isinstance(body, Mapping) else False
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            try:
                ev = ApprovalEvidence.model_validate(dict(item))
            except ValueError:
                continue
            if effective_tenant and ev.tenant_id != effective_tenant:
                continue
            if target_type and ev.target_type != target_type:
                continue
            if target_id and ev.target_id not in {target_id, effective_pool_id}:
                continue
            if decision_state and ev.decision_state != decision_state:
                continue
            results.append(ev)
        out = bound_candidate_history(results, bound=bound, now=now, history=history)
        out.overflow = out.overflow or overflow
        return out

    def verify(
        self,
        decision_id: str,
        *,
        expected: Mapping[str, Any],
        now: datetime | None = None,
        usage_context: Any = None,
    ) -> Any:
        pool_id = _optional_text(expected.get("target_id") or expected.get("subject.pool_id"))
        tenant_id = _optional_text(expected.get("tenant_id"))
        return require_valid_any(
            self.get(decision_id, pool_id=pool_id, tenant_id=tenant_id),
            expected=expected,
            now=now,
            usage_context=usage_context,
        )


def configured_capital_owner_approval_reader(
    env: Mapping[str, str] | None = None,
    *,
    base_url: str | None = None,
    pool_id: str | None = None,
    tenant_id: str | None = None,
    fetch_json: Callable[[str, Mapping[str, str], float], Any] | None = None,
) -> CapitalOwnerApprovalReader:
    from services.governance.approval_authority import ApprovalUnavailable
    from services.service_token_file import configured_service_token

    raw_source = os.environ if env is None else env
    source = {str(k): _unwrap_compose_value(v, raw_source) for k, v in raw_source.items()}
    resolved_base_url = (
        _optional_text(base_url)
        or _optional_text(source.get("PANTHEON_CAPITAL_API_URL"))
        or _optional_text(source.get("PANTHEON_CAPITAL_SERVICE_URL"))
        or ""
    )
    try:
        timeout = float(source.get("PANTHEON_CAPITAL_TIMEOUT_SECONDS") or "5")
    except ValueError as exc:
        raise ApprovalUnavailable("Capital owner reader timeout is malformed") from exc

    def _resolve_token() -> str:
        cur_raw = os.environ if env is None else env
        cur_source = {str(k): _unwrap_compose_value(v, cur_raw) for k, v in cur_raw.items()}
        return (
            configured_service_token("RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN", cur_source)
            or configured_service_token("DEPLOYMENT_CAPITAL_SERVICE_TOKEN", cur_source)
        )

    try:
        initial_token = _resolve_token()
    except RuntimeError as exc:
        raise ApprovalUnavailable("Capital owner read principal unavailable") from exc
    if not resolved_base_url or not initial_token:
        raise ApprovalUnavailable("Capital owner reader URL, scoped principal and timeout required")
    resolved_tenant = (
        _optional_text(tenant_id)
        or _optional_text(source.get("PANTHEON_TENANT_ID"))
    )
    return CapitalOwnerApprovalReader(
        base_url=resolved_base_url,
        service_token=initial_token,
        token_provider=_resolve_token,
        pool_id=pool_id,
        tenant_id=resolved_tenant,
        timeout_seconds=timeout,
        fetch_json=fetch_json,
    )


def _capital_owner_approval_reader_from_pool_reader(
    pool_reader: Any,
    *,
    pool_id: str | None = None,
    tenant_id: str | None = None,
) -> CapitalOwnerApprovalReader | None:
    if pool_reader is None:
        return None
    base_url = _optional_text(getattr(pool_reader, "_base_url", None) or getattr(pool_reader, "base_url", None))
    token = _optional_text(getattr(pool_reader, "_token", None) or getattr(pool_reader, "service_token", None))
    if not base_url or not token:
        return None
    reader_tenant = _optional_text(tenant_id) or _optional_text(
        getattr(pool_reader, "_tenant_id", None) or getattr(pool_reader, "tenant_id", None)
    )
    service_name = _optional_text(
        getattr(pool_reader, "_service_name", None) or getattr(pool_reader, "service_name", None)
    ) or "runtime-manager"
    raw_timeout = getattr(pool_reader, "_timeout", None) or getattr(pool_reader, "timeout_seconds", None) or 5.0
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 5.0
    try:
        return CapitalOwnerApprovalReader(
            base_url=base_url,
            service_token=token,
            pool_id=pool_id,
            tenant_id=reader_tenant,
            service_name=service_name,
            timeout_seconds=timeout,
        )
    except Exception:
        return None


MAX_POLICY_CATALOG_FILES = 64


def split_candidate_decisions(items: Any, now: datetime | None = None) -> tuple[list[Any], list[Any]]:
    from services.governance.approval_authority import split_candidate_decisions as _split

    return _split(items, now)

MAX_BOUNDED_APPROVAL_CANDIDATES = 16

_ABSENCE_SOURCE_PRIORITY: dict[str, int] = {
    "policy_candidates_ambiguous": 100,
    "policy_approval_revoked": 90,
    "policy_approval_expired": 85,
    "policy_approval_superseded": 80,
    "policy_approval_rejected": 75,
    "policy_approval_pending": 70,
    "policy_approval_missing": 60,
    "policy_expired_or_stale": 50,
    "cross_tenant_policy_or_approval_rejected": 45,
    "policy_version_mismatch": 40,
    "policy_invalid_or_stage_not_allowed": 35,
    "policy_definition_and_approval_absent": 10,
    "approval_reader_unavailable": 65,
}


def _higher_priority_absence_source(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current:
        return candidate
    if _ABSENCE_SOURCE_PRIORITY.get(candidate, 0) > _ABSENCE_SOURCE_PRIORITY.get(current, 0):
        return candidate
    return current


_VETOED_POLICY_APPROVAL_STATUSES: frozenset[str] = frozenset(
    {"unapproved", "pending", "proposed", "rejected", "revoked"}
)


def _policy_approval_veto_source(
    policy_model: RiskPolicy | None,
    raw_mapping: Mapping[str, Any] | None = None,
    *extra_containers: Mapping[str, Any] | None,
) -> str | None:
    """Return the fail-closed absence_source if a policy carries an explicit revocation or unapproved veto."""
    containers: list[Mapping[str, Any]] = []
    if policy_model is not None:
        raw_status = str(policy_model.status or "").strip().lower()
        if raw_status == "revoked":
            return "policy_approval_revoked"
        if raw_status == "rejected":
            return "policy_approval_rejected"
        if raw_status in {"unapproved", "pending", "proposed"}:
            return "policy_approval_missing"
        if isinstance(policy_model.metadata, Mapping):
            containers.append(policy_model.metadata)
    if isinstance(raw_mapping, Mapping):
        containers.append(raw_mapping)
        if isinstance(raw_mapping.get("metadata"), Mapping):
            containers.append(raw_mapping["metadata"])
    for extra in extra_containers:
        if isinstance(extra, Mapping):
            containers.append(extra)

    has_unapproved_flag = False
    for c in containers:
        if c.get("revoked") is True or c.get("risk_policy_revoked") is True:
            return "policy_approval_revoked"
        if c.get("revoked_at") not in (None, "") or c.get("risk_policy_revoked_at") not in (None, ""):
            return "policy_approval_revoked"
        for status_key in ("approval_status", "risk_policy_approval_status"):
            st = str(c.get(status_key) or "").strip().lower()
            if st in _VETOED_POLICY_APPROVAL_STATUSES:
                if st == "revoked":
                    return "policy_approval_revoked"
                if st == "rejected":
                    return "policy_approval_rejected"
                has_unapproved_flag = True
        if c.get("approved") is False or c.get("risk_policy_approved") is False:
            has_unapproved_flag = True

    if has_unapproved_flag:
        return "policy_approval_missing"
    return None




def _build_approval_readers(**kwargs: Any) -> list[Any]:
    return _build_approval_readers_with_diag(**kwargs)[0]


def _build_approval_readers_with_diag(
    *,
    approval_reader: Any | None,
    capital_pool_reader: Any | None,
    resolved_pool_id: str,
    effective_tenant_id: str | None,
) -> tuple[list[Any], str | None, tuple[str, ...]]:
    readers: list[Any] = []
    failure_types: list[str] = []
    seen_reader_ids: set[int] = set()

    def _add_reader(cand_reader: Any) -> None:
        if cand_reader is not None and id(cand_reader) not in seen_reader_ids:
            seen_reader_ids.add(id(cand_reader))
            readers.append(cand_reader)

    _add_reader(approval_reader)
    if not readers:
        capital_owner_factory = globals().get("configured_capital_owner_approval_reader")
        if callable(capital_owner_factory):
            try:
                _add_reader(
                    capital_owner_factory(
                        pool_id=resolved_pool_id,
                        tenant_id=effective_tenant_id,
                    )
                )
            except Exception as exc:
                failure_types.append(type(exc).__name__)
        _add_reader(
            _capital_owner_approval_reader_from_pool_reader(
                capital_pool_reader,
                pool_id=resolved_pool_id,
                tenant_id=effective_tenant_id,
            )
        )
        try:
            from services.governance import approval_authority as _gov_auth

            domains = (
                "capital",
                "runtime_manager",
                "deployment",
                "persona_training_target",
                "registry",
                "evolution",
            )
            auth_factory = getattr(_gov_auth, "configured_approval_reader", None)
            if callable(auth_factory):
                for domain in domains:
                    try:
                        _add_reader(auth_factory(domain))
                    except Exception as exc:
                        failure_types.append(type(exc).__name__)
                        continue
            local_factory = globals().get("configured_approval_reader")
            if not readers and callable(local_factory) and local_factory is not auth_factory:
                for domain in domains:
                    try:
                        _add_reader(local_factory(domain))
                    except Exception as exc:
                        failure_types.append(type(exc).__name__)
                        continue
        except Exception as exc:
            failure_types.append(type(exc).__name__)
    diag = "approval_reader_unavailable" if (not readers and failure_types) else None
    # Only exception type names are retained (never message text, URLs or credentials).
    return readers, diag, (tuple(dict.fromkeys(failure_types)) if diag else ())


def _classify_decision_payload_rejection(
    raw_dec: Mapping[str, Any],
    *,
    effective_tenant_id: str | None,
    clean_profile: str | None,
    now_dt: datetime,
) -> str | None:
    meta = raw_dec.get("metadata") if isinstance(raw_dec.get("metadata"), Mapping) else {}
    subject = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
    subj_risk = _first_text_or_none(subject, "requested_risk", "requested_profile", "risk_profile")
    if clean_profile and subj_risk and subj_risk != clean_profile:
        return None
    dec_tenant = _optional_text(raw_dec.get("tenant_id"))
    if effective_tenant_id and dec_tenant and dec_tenant != effective_tenant_id:
        return "cross_tenant_policy_or_approval_rejected"
    dec_state = _optional_text(raw_dec.get("decision_state")) or ""
    if raw_dec.get("revoked_at") not in (None, "") or dec_state == "revoked":
        return "policy_approval_revoked"
    if raw_dec.get("superseded_by") not in (None, "") or dec_state == "superseded":
        return "policy_approval_superseded"
    if dec_state in {"proposed", "under_review"}:
        return "policy_approval_pending"
    if dec_state == "decided" and _optional_text(raw_dec.get("decision")) == "rejected":
        return "policy_approval_rejected"
    raw_exp = _optional_text(raw_dec.get("expires_at"))
    if raw_exp:
        exp_dt = _parse_iso_timestamp(raw_exp)
        if exp_dt is None or exp_dt <= now_dt:
            return "policy_approval_expired"
    return None


def _verify_policy_owner_approval_decisions(
    *,
    resolved_pool_id: str,
    effective_tenant_id: str | None,
    clean_stage: str | None,
    clean_profile: str | None,
    policy_model: RiskPolicy,
    computed_digest: str,
    raw_policy_mapping: Mapping[str, Any] | None,
    pool_obj: Any,
    pool_meta: Mapping[str, Any],
    b_map: Mapping[str, Any],
    b_meta: Mapping[str, Any],
    policy_meta: Mapping[str, Any],
    approval_decision_id: str | None,
    approval_decision: Any | None,
    approval_decisions: Sequence[Any] | None,
    approval_reader: Any | None,
    capital_pool_reader: Any | None = None,
    now_dt: datetime,
    allow_target_discovery: bool = True,
) -> tuple[str | None, list[str], str | None]:
    """Verify tenant-scoped policy-owner ApprovalDecisions and detect ambiguity or specific rejection sources."""
    caller_explicit_ids: list[str] = []
    container_candidate_ids: list[str] = []

    def _add_caller_ref(ref_candidate: Any) -> None:
        dec_ref = _extract_decision_id_reference(ref_candidate)
        if dec_ref and dec_ref not in caller_explicit_ids:
            caller_explicit_ids.append(dec_ref)

    def _add_container_ref(ref_candidate: Any) -> None:
        dec_ref = _extract_decision_id_reference(ref_candidate)
        if dec_ref and dec_ref not in caller_explicit_ids and dec_ref not in container_candidate_ids:
            container_candidate_ids.append(dec_ref)

    _add_caller_ref(approval_decision_id)
    _add_caller_ref(approval_decision)
    if approval_decisions is not None and not isinstance(approval_decisions, (str, bytes)):
        for item in approval_decisions:
            _add_caller_ref(item)

    if isinstance(pool_obj, Mapping):
        _add_container_ref(_first_text_or_none(pool_obj, "approval_decision_id", "profile_mapping_approval_id", "decision_id"))
    elif pool_obj is not None:
        _add_container_ref(getattr(pool_obj, "approval_decision_id", None))
        _add_container_ref(getattr(pool_obj, "profile_mapping_approval_id", None))

    pool_sub = dict(b_map.get("capital_pool")) if isinstance(b_map.get("capital_pool"), Mapping) else {}
    pool_sub_meta = dict(pool_sub.get("metadata")) if isinstance(pool_sub.get("metadata"), Mapping) else {}

    for container in (pool_meta, b_map, b_meta, policy_meta, pool_sub, pool_sub_meta):
        if not isinstance(container, Mapping):
            continue
        for exp_key in ("profile_mapping_expires_at", "approval_expires_at"):
            raw_exp = container.get(exp_key)
            if raw_exp not in (None, ""):
                exp_str = _optional_text(raw_exp)
                exp_dt = _parse_iso_timestamp(exp_str) if exp_str else None
                if exp_dt is None or exp_dt <= now_dt:
                    return None, [], "policy_approval_expired"
        _add_container_ref(
            _first_text_or_none(
                container,
                "profile_mapping_approval_id",
                "approval_decision_id",
                "decision_id",
            )
        )
        for attached_key in ("profile_mapping_approval", "approval_decision"):
            attached = container.get(attached_key)
            if attached is not None:
                _add_container_ref(attached)
        attached_list = container.get("approval_decisions")
        if isinstance(attached_list, Sequence) and not isinstance(attached_list, (str, bytes)):
            for item in attached_list:
                _add_container_ref(item)

    if not effective_tenant_id or not resolved_pool_id:
        return None, [], None

    policy_veto_diag = _policy_approval_veto_source(
        policy_model,
        raw_policy_mapping,
        policy_meta,
        pool_meta,
        b_meta,
    )
    if policy_veto_diag is not None:
        return None, [], policy_veto_diag

    readers = _build_approval_readers(
        approval_reader=approval_reader,
        capital_pool_reader=capital_pool_reader,
        resolved_pool_id=resolved_pool_id,
        effective_tenant_id=effective_tenant_id,
    )
    if not readers:
        return None, [], None

    from services.governance import approval_targets
    from services.governance.approval_authority import ApprovalEvidence
    from services.governance.paper_approval_scope import ApprovalUsageContext, current_environment

    pool_approval_digest = (
        (
            _first_text_or_none(pool_obj, "approval_digest", "pool_approval_digest")
            if isinstance(pool_obj, Mapping)
            else _optional_text(getattr(pool_obj, "approval_digest", None))
        )
        or _first_text_or_none(pool_meta, "approval_digest", "pool_approval_digest")
        or _first_text_or_none(b_map, "approval_digest", "pool_approval_digest")
        or _first_text_or_none(b_meta, "approval_digest", "pool_approval_digest")
        or _first_text_or_none(pool_sub, "approval_digest", "pool_approval_digest")
        or _first_text_or_none(pool_sub_meta, "approval_digest", "pool_approval_digest")
    )
    if (
        not pool_approval_digest
        and pool_obj is not None
        and not isinstance(pool_obj, (Mapping, RiskPolicy, str))
        and getattr(pool_obj, "pool_id", None)
        and getattr(pool_obj, "owner_id", None)
    ):
        pool_approval_digest = capital_pool_approval_digest(pool_obj)

    valid_digests = {computed_digest}
    if isinstance(raw_policy_mapping, Mapping):
        try:
            raw_digest = hashlib.sha256(
                json.dumps(raw_policy_mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            valid_digests.add(raw_digest)
        except Exception:
            pass

    is_default_inline_id = (
        str(policy_model.risk_policy_id).startswith("rp-pool-")
        or (bool(resolved_pool_id) and str(policy_model.risk_policy_id) == f"rp-{resolved_pool_id}")
        or policy_meta.get("default_paper_baseline") is True
        or pool_meta.get("default_paper_baseline") is True
    )

    target_stage_val = clean_stage or "paper"
    raw_scale = pool_meta.get("capital_scale_pct")
    if raw_scale is not None and isinstance(raw_scale, (int, float)) and not isinstance(raw_scale, bool):
        scale_pct = float(raw_scale)
    else:
        scale_pct = 0.0 if target_stage_val == "paper" else 100.0
    env_name = (
        str(pool_meta.get("environment") or b_meta.get("environment") or "").strip().lower()
        or current_environment()
        or "dev"
    )
    usage_ctx = ApprovalUsageContext(
        environment=env_name,
        target_stage=target_stage_val,
        capital_scale_pct=scale_pct,
    )

    explicit_candidate_ids = frozenset([*caller_explicit_ids, *container_candidate_ids])
    diagnostic_source: str | None = None
    verified_info: dict[str, tuple[str, bool, str]] = {}

    def _collapse_renewal_overlap(ids: list[str]) -> list[str]:
        """Renewals of one policy digest overlap while the previous decision is still valid: keep the
        pool-bound one, else the most recently created. Different digests stay distinct (ambiguous)."""
        by_digest: dict[str, list[str]] = {}
        for vid in ids:
            by_digest.setdefault(verified_info.get(vid, ("", False, ""))[0] or vid, []).append(vid)
        picked: list[str] = []
        for group in by_digest.values():
            if len(group) == 1:
                picked.append(group[0])
                continue
            pool_bound = [g for g in group if verified_info.get(g, ("", False, ""))[1]]
            pool = pool_bound or group
            picked.append(max(pool, key=lambda g: (verified_info.get(g, ("", False, ""))[2], g)))
        return picked

    def _validate_loaded_evidence(loaded_ev: Any, expected_decision_id: str | None = None) -> str | None:
        nonlocal diagnostic_source
        if loaded_ev is None:
            return None
        raw_dict: dict[str, Any] | None = None
        if isinstance(loaded_ev, Mapping):
            raw_dict = dict(loaded_ev)
        elif hasattr(loaded_ev, "model_dump"):
            try:
                raw_dict = dict(loaded_ev.model_dump())
            except Exception:
                raw_dict = None
        if raw_dict is not None:
            diag = _classify_decision_payload_rejection(
                raw_dict,
                effective_tenant_id=effective_tenant_id,
                clean_profile=clean_profile,
                now_dt=now_dt,
            )
            diagnostic_source = _higher_priority_absence_source(diagnostic_source, diag)

        ev_target_type = _optional_text(raw_dict.get("target_type")) if raw_dict else "capital_pool_activation"
        ev_target_id = _optional_text(raw_dict.get("target_id")) if raw_dict else resolved_pool_id
        ev_target_ver = _optional_text(raw_dict.get("target_version")) if raw_dict else pool_approval_digest

        is_policy_scoped_target = (
            not is_default_inline_id
            and ev_target_id == policy_model.risk_policy_id
            and ev_target_type == "capital_pool_activation"
        )

        if is_policy_scoped_target:
            if ev_target_ver not in (valid_digests | {str(policy_model.version)}):
                return None
            raw_meta = raw_dict.get("metadata") if raw_dict and isinstance(raw_dict.get("metadata"), Mapping) else {}
            raw_subj = raw_meta.get("subject") if isinstance(raw_meta.get("subject"), Mapping) else {}
            subj_pool = _optional_text(raw_subj.get("pool_id"))
            if subj_pool not in {resolved_pool_id, policy_model.risk_policy_id, "*"}:
                return None
            expected_binding: dict[str, Any] = {
                "tenant_id": effective_tenant_id,
                "target_type": "capital_pool_activation",
                "target_id": policy_model.risk_policy_id,
                "target_version": ev_target_ver,
                "subject.pool_id": subj_pool,
                "subject.risk_direction": frozenset({"increase", "reduce", "neutral"}),
                "subject.risk_policy_id": policy_model.risk_policy_id,
                "subject.risk_policy_version": str(policy_model.version),
            }
        else:
            expected_binding = {
                "tenant_id": effective_tenant_id,
                "target_type": "capital_pool_activation",
                "target_id": resolved_pool_id,
                "subject.pool_id": resolved_pool_id,
                "subject.risk_direction": frozenset({"increase", "reduce", "neutral"}),
                "subject.risk_policy_id": policy_model.risk_policy_id,
                "subject.risk_policy_version": str(policy_model.version),
            }
            if pool_approval_digest:
                expected_binding["target_version"] = pool_approval_digest

        try:
            if isinstance(loaded_ev, ApprovalEvidence):
                evidence = loaded_ev
            elif isinstance(loaded_ev, Mapping):
                evidence = ApprovalEvidence.model_validate(dict(loaded_ev))
            elif hasattr(loaded_ev, "model_dump"):
                evidence = ApprovalEvidence.model_validate(loaded_ev.model_dump())
            elif (
                expected_decision_id in explicit_candidate_ids
                and callable(getattr(loaded_ev, "require_valid", None))
            ):
                res_ev = require_valid_any(
                    loaded_ev,
                    expected={
                        **expected_binding,
                        "subject.risk_policy_digest": computed_digest,
                        "subject.requested_risk": clean_profile or "low",
                    },
                    now=now_dt,
                    usage_context=usage_ctx,
                )
                if isinstance(res_ev, ApprovalEvidence):
                    evidence = res_ev
                elif isinstance(res_ev, Mapping):
                    evidence = ApprovalEvidence.model_validate(dict(res_ev))
                elif hasattr(res_ev, "model_dump"):
                    evidence = ApprovalEvidence.model_validate(res_ev.model_dump())
                else:
                    return None
            else:
                return None
        except Exception:
            return None

        if expected_decision_id and evidence.decision_id != expected_decision_id:
            return None

        try:
            require_valid_any(
                evidence,
                expected=expected_binding,
                now=now_dt,
                usage_context=usage_ctx,
            )
        except Exception:
            return None

        d = evidence.model_dump()
        meta = d.get("metadata") if isinstance(d.get("metadata"), Mapping) else {}
        subject = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
        for emb_cand in (subject.get("risk_policy"), meta.get("risk_policy")):
            if isinstance(emb_cand, Mapping):
                emb_veto = _policy_approval_veto_source(None, emb_cand)
                if emb_veto is not None:
                    diagnostic_source = _higher_priority_absence_source(diagnostic_source, emb_veto)
                    return None
        if _optional_text(subject.get("risk_policy_digest")) not in valid_digests:
            return None
        subj_risk = _first_text_or_none(subject, "requested_risk", "requested_profile", "risk_profile")
        if clean_profile:
            if subj_risk != clean_profile:
                return None
        elif not subj_risk:
            return None

        verified_info[evidence.decision_id] = (
            _optional_text(subject.get("risk_policy_digest")) or "",
            ev_target_id == resolved_pool_id,
            _first_text_or_none(d, "created_at", "recorded_at", "decided_at") or "",
        )
        return evidence.decision_id

    def _fetch_by_id(cand_id: str) -> Any:
        for reader in readers:
            loaded_ev = None
            try:
                if hasattr(reader, "get"):
                    loaded_ev = reader.get(cand_id)
                elif callable(reader):
                    loaded_ev = reader(cand_id)
            except Exception:
                loaded_ev = None
            if loaded_ev is not None:
                return loaded_ev
        return None

    # 1. If the caller explicitly provided approval_decision_id(s), verify only those exact IDs.
    if caller_explicit_ids:
        verified_caller: list[str] = []
        for cand_id in caller_explicit_ids:
            matched_id = _validate_loaded_evidence(_fetch_by_id(cand_id), cand_id)
            if matched_id and matched_id not in verified_caller:
                verified_caller.append(matched_id)
        if len(verified_caller) == 1:
            return verified_caller[0], verified_caller, None
        if len(verified_caller) > 1:
            return None, verified_caller, "policy_candidates_ambiguous"
        return None, [], diagnostic_source

    # 2. Check decision IDs already persisted on the pool/container metadata.
    if container_candidate_ids:
        verified_container: list[str] = []
        for cand_id in container_candidate_ids:
            matched_id = _validate_loaded_evidence(_fetch_by_id(cand_id), cand_id)
            if matched_id and matched_id not in verified_container:
                verified_container.append(matched_id)
        if len(verified_container) == 1:
            return verified_container[0], verified_container, None
        if len(verified_container) > 1:
            return None, verified_container, "policy_candidates_ambiguous"

    # 3. Bounded target discovery in Governance / Capital owner reader.
    discovered_verified: list[str] = []
    checked_ids: set[str] = set(container_candidate_ids)
    target_lookup_overflow = False

    if allow_target_discovery:
        targets: list[tuple[str, str]] = [("capital_pool_activation", resolved_pool_id)]
        if not is_default_inline_id and policy_model.risk_policy_id:
            targets.append(("capital_pool_activation", policy_model.risk_policy_id))
        for t_type, t_id in targets:
            for reader in readers:
                if not hasattr(reader, "list_for_target"):
                    continue
                items: list[Any] = []
                try:
                    raw_res = (
                        reader.list_for_target(
                            target_type=t_type,
                            target_id=t_id,
                            decision_state=None,
                            max_items=MAX_BOUNDED_APPROVAL_CANDIDATES,
                        )
                        or []
                    )
                    if bool(getattr(raw_res, "overflow", False)):
                        target_lookup_overflow = True
                    items = list(raw_res)
                except TypeError:
                    try:
                        raw_res = (
                            reader.list_for_target(
                                target_type=t_type,
                                target_id=t_id,
                            )
                            or []
                        )
                        if bool(getattr(raw_res, "overflow", False)):
                            target_lookup_overflow = True
                        items = list(raw_res)
                    except Exception:
                        items = []
                except Exception:
                    items = []
                current_items, past_items = split_candidate_decisions(items, now_dt)
                if len(current_items) > MAX_BOUNDED_APPROVAL_CANDIDATES:
                    target_lookup_overflow = True
                for item in [*current_items[:MAX_BOUNDED_APPROVAL_CANDIDATES], *past_items]:
                    item_id = _extract_decision_id_reference(item)
                    if item_id:
                        checked_ids.add(item_id)
                    matched_id = _validate_loaded_evidence(item, item_id)
                    if matched_id and matched_id not in discovered_verified:
                        discovered_verified.append(matched_id)

    # 4. Fallback exact-ID lookup for deterministic default ID if not already checked.
    default_apv_id = f"apv-capital-pool-{resolved_pool_id}"
    if not discovered_verified and not target_lookup_overflow and default_apv_id not in checked_ids:
        matched_default = _validate_loaded_evidence(_fetch_by_id(default_apv_id), default_apv_id)
        if matched_default and matched_default not in discovered_verified:
            discovered_verified.append(matched_default)

    if len(discovered_verified) > 1 and not target_lookup_overflow:
        discovered_verified = _collapse_renewal_overlap(discovered_verified)
    if len(discovered_verified) > 1 or target_lookup_overflow:
        return None, discovered_verified, "policy_candidates_ambiguous"
    if len(discovered_verified) == 1:
        return discovered_verified[0], discovered_verified, None
    return None, [], diagnostic_source


def _verify_policy_owner_approval_decision(
    *,
    resolved_pool_id: str,
    effective_tenant_id: str | None,
    clean_stage: str | None,
    clean_profile: str | None,
    policy_model: RiskPolicy,
    computed_digest: str,
    raw_policy_mapping: Mapping[str, Any] | None,
    pool_obj: Any,
    pool_meta: Mapping[str, Any],
    b_map: Mapping[str, Any],
    b_meta: Mapping[str, Any],
    policy_meta: Mapping[str, Any],
    approval_decision_id: str | None,
    approval_decision: Any | None,
    approval_decisions: Sequence[Any] | None,
    approval_reader: Any | None,
    capital_pool_reader: Any | None = None,
    now_dt: datetime,
) -> str | None:
    """Verify a tenant-scoped policy-owner ApprovalDecision via canonical owner readback and ApprovalEvidence.require_valid."""
    verified_id, _, _ = _verify_policy_owner_approval_decisions(
        resolved_pool_id=resolved_pool_id,
        effective_tenant_id=effective_tenant_id,
        clean_stage=clean_stage,
        clean_profile=clean_profile,
        policy_model=policy_model,
        computed_digest=computed_digest,
        raw_policy_mapping=raw_policy_mapping,
        pool_obj=pool_obj,
        pool_meta=pool_meta,
        b_map=b_map,
        b_meta=b_meta,
        policy_meta=policy_meta,
        approval_decision_id=approval_decision_id,
        approval_decision=approval_decision,
        approval_decisions=approval_decisions,
        approval_reader=approval_reader,
        capital_pool_reader=capital_pool_reader,
        now_dt=now_dt,
    )
    return verified_id


@dataclass(frozen=True)
class ProfilePolicyAutoResolution:
    """Result of auto-resolving a RiskPolicy for (tenant_id, requested_profile, stage, capital_pool_id)."""

    resolved: bool
    status: str
    reason_code: str | None = None
    reason: str | None = None
    absence_source: str | None = None
    risk_policy_ref: str | None = None
    policy: RiskPolicy | None = None
    raw_policy: Mapping[str, Any] | None = None
    approval_decision_id: str | None = None
    candidate_policy: RiskPolicy | None = None
    ambiguous_policy_ids: tuple[str, ...] = ()
    ambiguous_decision_ids: tuple[str, ...] = ()


def _configured_policy_catalog_root(policy_dir: str | os.PathLike[str] | None = None) -> Path | None:
    if policy_dir is not None and str(policy_dir).strip():
        root = Path(str(policy_dir).strip())
        return root if root.exists() else None
    env_dir = os.getenv("CAPITAL_RISK_POLICY_DIR", "").strip()
    if env_dir:
        root = Path(env_dir)
        return root if root.exists() else None
    repo_config_dir = Path(__file__).resolve().parents[2] / "config" / "risk_policies"
    if repo_config_dir.exists():
        return repo_config_dir
    local_dir = Path(__file__).parent / "risk_policies"
    if local_dir.exists():
        return local_dir
    return None


def _is_policy_Candidate_valid_for_profile_window(
    policy_model: RiskPolicy,
    raw_mapping: Mapping[str, Any] | None,
    *,
    clean_stage: str | None,
    require_limits: Sequence[str],
    now_dt: datetime,
) -> tuple[bool, str | None]:
    policy_meta = dict(policy_model.metadata) if isinstance(policy_model.metadata, Mapping) else {}
    raw_status = str(policy_model.status or "").strip().lower()
    if raw_status in {"stale", "superseded", "expired"} or policy_meta.get("stale") is True:
        return False, "policy_expired_or_stale"
    if _first_text_or_none(policy_meta, "superseded_by"):
        return False, "policy_expired_or_stale"
    if raw_status not in _ACTIVE_POLICY_STATUSES:
        return False, "policy_invalid_or_stage_not_allowed"

    for key in ("effective_from",):
        for src in (raw_mapping or {}, policy_meta):
            if not isinstance(src, Mapping):
                continue
            val = src.get(key)
            if val not in (None, ""):
                dt = _parse_iso_timestamp(val)
                if dt is None:
                    return False, "policy_invalid_or_stage_not_allowed"
                if dt > now_dt:
                    return False, "policy_expired_or_stale"

    for key in ("expires_at", "effective_to"):
        for src in (raw_mapping or {}, policy_meta):
            if not isinstance(src, Mapping):
                continue
            val = src.get(key)
            if val not in (None, ""):
                dt = _parse_iso_timestamp(val)
                if dt is None:
                    return False, "policy_invalid_or_stage_not_allowed"
                if dt <= now_dt:
                    return False, "policy_expired_or_stale"

    if clean_stage:
        if not policy_model.allowed_stages or clean_stage not in policy_model.allowed_stages:
            return False, "policy_invalid_or_stage_not_allowed"

    for field_name in _NON_NEGATIVE_SCALAR_LIMIT_FIELDS:
        val = getattr(policy_model, field_name, None)
        if val is None:
            continue
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(float(val)):
            return False, "policy_invalid_or_stage_not_allowed"
        if field_name in _POSITIVE_EXPOSURE_LIMIT_FIELDS and float(val) <= 0.0:
            return False, "policy_invalid_or_stage_not_allowed"
        if float(val) < 0.0:
            return False, "policy_invalid_or_stage_not_allowed"

    for req_lim in require_limits:
        val = getattr(policy_model, req_lim, None)
        if val in (None, (), {}, []):
            return False, "policy_invalid_or_stage_not_allowed"
        if req_lim in _POSITIVE_EXPOSURE_LIMIT_FIELDS and float(val) <= 0.0:
            return False, "policy_invalid_or_stage_not_allowed"

    return True, None


def discover_approved_profile_risk_policy(
    *,
    resolved_pool_id: str,
    effective_tenant_id: str | None,
    clean_stage: str | None = "paper",
    clean_profile: str | None = None,
    expected_version: str | None = None,
    base_pool: Mapping[str, Any] | None = None,
    currency: str | None = None,
    budget: Any = None,
    owner_id: str | None = None,
    owner_type: str | None = None,
    single_runtime_enforced: bool | None = None,
    approval_decision_id: str | None = None,
    approval_reader: Any | None = None,
    capital_pool_reader: Any | None = None,
    risk_policy_loader: Callable[[str], Any] | None = None,
    policy_dir: str | os.PathLike[str] | None = None,
    require_limits: Sequence[str] = ("gross_limit",),
    as_of: str | None = None,
    now_dt: datetime | None = None,
) -> ProfilePolicyAutoResolution:
    """Bounded discovery of an applicable approved RiskPolicy for (tenant_id, requested_profile, stage)."""
    effective_now = now_dt or _parse_iso_timestamp(as_of) or datetime.now(timezone.utc)
    eff_profile = _optional_text(clean_profile)
    if eff_profile is None or not isinstance(clean_profile, str):
        return ProfilePolicyAutoResolution(
            resolved=False,
            status="unresolved_profile_mapping",
            reason_code="risk_policy_profile_mapping_unapproved",
            reason="Requested profile provenance must be established before policy discovery",
            absence_source="requested_profile_missing" if eff_profile is None else "requested_profile_invalid",
        )
    eff_stage = _optional_text(clean_stage) or "paper"
    req_version = _optional_text(expected_version)

    diagnostic_source: str | None = None
    catalog_candidates: list[tuple[RiskPolicy, dict[str, Any]]] = []
    unapproved_catalog_policies: list[RiskPolicy] = []
    catalog_by_id: dict[str, tuple[RiskPolicy, dict[str, Any]]] = {}
    rejected_catalog_policy_ids: set[str] = set()
    vetoed_catalog_by_id: dict[str, str] = {}
    catalog_overflow = False

    # 1. Bounded scan of CAPITAL_RISK_POLICY_DIR for policies explicitly declaring requested_profile.
    catalog_root = _configured_policy_catalog_root(policy_dir)
    if catalog_root is not None and catalog_root.is_dir():
        eligible_paths = [
            p
            for p in sorted(catalog_root.glob("*.json"))
            if p.stem.strip() and not p.stem.strip().startswith(".") and not p.stem.strip().startswith("rp-pool-")
        ]
        if len(eligible_paths) > MAX_POLICY_CATALOG_FILES:
            catalog_overflow = True
        for path in eligible_paths[:MAX_POLICY_CATALOG_FILES]:
            stem = path.stem.strip()
            try:
                raw_loaded = (
                    risk_policy_loader(stem)
                    if risk_policy_loader is not None
                    else load_risk_policy(stem, policy_dir=catalog_root)
                )
                if not isinstance(raw_loaded, Mapping):
                    continue
                raw_dict = dict(raw_loaded)
                cand_policy = normalize_risk_policy(raw_dict, default_id=stem)
            except Exception:
                continue

            cand_meta = dict(cand_policy.metadata) if isinstance(cand_policy.metadata, Mapping) else {}
            if cand_meta.get("default_paper_baseline") is True or str(cand_policy.risk_policy_id).startswith("rp-pool-"):
                continue
            # Never infer a profile mapping by searching labels/names; require explicit profile metadata.
            cand_profile = _first_text_or_none(cand_meta, "requested_profile", "risk_profile", "requested_risk")
            if cand_profile != eff_profile:
                continue

            cand_tenant = _first_text_or_none(cand_meta, "tenant_id")
            if not effective_tenant_id or cand_tenant != effective_tenant_id:
                rejected_catalog_policy_ids.add(cand_policy.risk_policy_id)
                diagnostic_source = _higher_priority_absence_source(
                    diagnostic_source,
                    "cross_tenant_policy_or_approval_rejected",
                )
                continue

            if req_version and str(cand_policy.version) != req_version:
                rejected_catalog_policy_ids.add(cand_policy.risk_policy_id)
                diagnostic_source = _higher_priority_absence_source(
                    diagnostic_source,
                    "policy_version_mismatch",
                )
                continue

            window_ok, window_diag = _is_policy_Candidate_valid_for_profile_window(
                cand_policy,
                raw_dict,
                clean_stage=eff_stage,
                require_limits=require_limits,
                now_dt=effective_now,
            )
            if not window_ok:
                rejected_catalog_policy_ids.add(cand_policy.risk_policy_id)
                diagnostic_source = _higher_priority_absence_source(diagnostic_source, window_diag)
                continue

            veto_diag = _policy_approval_veto_source(cand_policy, raw_dict, cand_meta)
            if veto_diag is not None:
                unapproved_catalog_policies.append(cand_policy)
                rejected_catalog_policy_ids.add(cand_policy.risk_policy_id)
                vetoed_catalog_by_id[cand_policy.risk_policy_id] = veto_diag
                diagnostic_source = _higher_priority_absence_source(
                    diagnostic_source,
                    veto_diag,
                )
                continue

            catalog_candidates.append((cand_policy, raw_dict))
            catalog_by_id[cand_policy.risk_policy_id] = (cand_policy, raw_dict)

    readers, reader_diag, reader_err_types = _build_approval_readers_with_diag(
        approval_reader=approval_reader,
        capital_pool_reader=capital_pool_reader,
        resolved_pool_id=resolved_pool_id,
        effective_tenant_id=effective_tenant_id,
    )
    if reader_diag:
        diagnostic_source = _higher_priority_absence_source(diagnostic_source, reader_diag)

    base = dict(base_pool) if isinstance(base_pool, Mapping) else {}
    base_meta = dict(base.get("metadata")) if isinstance(base.get("metadata"), Mapping) else {}

    def _build_candidate_pool(cand_ref: str | None, cand_pol: RiskPolicy) -> dict[str, Any]:
        stripped_keys = {
            "approval_decision_id",
            "profile_mapping_approval_id",
            "decision_id",
            "profile_mapping_approval",
            "approval_decision",
            "approval_decisions",
        }
        clean_base = {k: v for k, v in base.items() if k not in stripped_keys}
        clean_base_meta = {k: v for k, v in base_meta.items() if k not in stripped_keys}
        cand_pool: dict[str, Any] = {
            **clean_base,
            "pool_id": resolved_pool_id,
            "tenant_id": effective_tenant_id,
            "owner_id": owner_id if owner_id is not None else clean_base.get("owner_id", effective_tenant_id),
            "owner_type": owner_type if owner_type is not None else clean_base.get("owner_type", "org"),
            "currency": currency if currency is not None else clean_base.get("currency", "USD"),
            "budget": budget if (budget is not None or "budget" not in clean_base) else clean_base.get("budget"),
            "risk_policy_ref": cand_ref,
            "single_runtime_enforced": (
                single_runtime_enforced
                if single_runtime_enforced is not None
                else clean_base.get("single_runtime_enforced", True)
            ),
            "metadata": {
                **clean_base_meta,
                "tenant_id": effective_tenant_id,
                "requested_risk_profile": eff_profile,
                "require_approved_profile_mapping": True,
                "risk_policy": cand_pol.to_dict(),
            },
        }
        cand_pool["approval_digest"] = capital_pool_approval_digest(cand_pool)
        return cand_pool

    # 2. Bounded lookup of pool-targeted ApprovalDecisions to discover any policy referenced by an approved decision.
    pool_decisions: list[dict[str, Any]] = []
    seen_dec_ids: set[str] = set()

    def _collect_decision_obj(item: Any) -> None:
        nonlocal diagnostic_source
        if item is None:
            return
        d: dict[str, Any] | None = None
        if isinstance(item, Mapping):
            d = dict(item)
        elif hasattr(item, "model_dump"):
            try:
                d = dict(item.model_dump())
            except Exception:
                d = None
        if not isinstance(d, Mapping):
            return
        dec_id = _optional_text(d.get("decision_id"))
        if not dec_id or dec_id in seen_dec_ids:
            return
        seen_dec_ids.add(dec_id)
        diag = _classify_decision_payload_rejection(
            d,
            effective_tenant_id=effective_tenant_id,
            clean_profile=eff_profile,
            now_dt=effective_now,
        )
        diagnostic_source = _higher_priority_absence_source(diagnostic_source, diag)
        pool_decisions.append(d)

    pool_decision_lookup_overflow = False
    if approval_decision_id:
        for reader in readers:
            try:
                if hasattr(reader, "get"):
                    _collect_decision_obj(reader.get(approval_decision_id))
            except Exception:
                continue
    else:
        for container in (base, base_meta):
            cid = _first_text_or_none(container, "profile_mapping_approval_id", "approval_decision_id", "decision_id")
            if cid and cid not in seen_dec_ids:
                for reader in readers:
                    try:
                        if hasattr(reader, "get"):
                            _collect_decision_obj(reader.get(cid))
                    except Exception:
                        continue
        for reader in readers:
            if not hasattr(reader, "list_for_target"):
                continue
            items: list[Any] = []
            try:
                raw_res = (
                    reader.list_for_target(
                        target_type="capital_pool_activation",
                        target_id=resolved_pool_id,
                        decision_state=None,
                        max_items=MAX_BOUNDED_APPROVAL_CANDIDATES,
                    )
                    or []
                )
                if bool(getattr(raw_res, "overflow", False)):
                    pool_decision_lookup_overflow = True
                items = list(raw_res)
            except TypeError:
                try:
                    raw_res = (
                        reader.list_for_target(
                            target_type="capital_pool_activation",
                            target_id=resolved_pool_id,
                        )
                        or []
                    )
                    if bool(getattr(raw_res, "overflow", False)):
                        pool_decision_lookup_overflow = True
                    items = list(raw_res)
                except Exception:
                    items = []
            except Exception:
                items = []
            current_items, past_items = split_candidate_decisions(items, effective_now)
            if len(current_items) > MAX_BOUNDED_APPROVAL_CANDIDATES:
                pool_decision_lookup_overflow = True
            for item in [*current_items[:MAX_BOUNDED_APPROVAL_CANDIDATES], *past_items]:
                _collect_decision_obj(item)
        default_apv_id = f"apv-capital-pool-{resolved_pool_id}"
        if not pool_decisions and not pool_decision_lookup_overflow and default_apv_id not in seen_dec_ids:
            for reader in readers:
                try:
                    if hasattr(reader, "get"):
                        _collect_decision_obj(reader.get(default_apv_id))
                except Exception:
                    continue

    existing_inline_ver = (
        _optional_text(base_meta["risk_policy"].get("version"))
        if isinstance(base_meta.get("risk_policy"), Mapping)
        else None
    )
    has_persisted_pool_approval = bool(
        _first_text_or_none(base_meta, "profile_mapping_approval_id", "approval_decision_id", "decision_id")
    )

    # Extract any candidate policies referenced by pool_decisions that are not already in catalog_candidates.
    decision_policy_candidates: list[tuple[str | None, RiskPolicy, dict[str, Any]]] = []
    for d in pool_decisions:
        meta = d.get("metadata") if isinstance(d.get("metadata"), Mapping) else {}
        subject = meta.get("subject") if isinstance(meta.get("subject"), Mapping) else {}
        subj_risk = _first_text_or_none(subject, "requested_risk", "requested_profile", "risk_profile")
        if subj_risk and subj_risk != eff_profile:
            continue
        subj_pol_id = _optional_text(subject.get("risk_policy_id"))
        subj_pol_ver = _optional_text(subject.get("risk_policy_version")) or (req_version or "1.0")
        if req_version and subj_pol_ver != req_version:
            continue
        if not subj_pol_id:
            continue
        if subj_pol_id in catalog_by_id:
            continue
        if subj_pol_id in rejected_catalog_policy_ids:
            if subj_pol_id in vetoed_catalog_by_id:
                diagnostic_source = _higher_priority_absence_source(
                    diagnostic_source,
                    vetoed_catalog_by_id[subj_pol_id],
                )
            continue
        # Try loading from policy catalog by exact ID if it is not a default pool inline ID.
        is_def_id = subj_pol_id.startswith("rp-pool-") or subj_pol_id == f"rp-{resolved_pool_id}"
        if (
            is_def_id
            and not req_version
            and has_persisted_pool_approval
            and existing_inline_ver
            and subj_pol_ver != existing_inline_ver
        ):
            continue
        if not is_def_id:
            loaded_authoritative = False
            try:
                loaded_raw = (
                    risk_policy_loader(subj_pol_id)
                    if risk_policy_loader is not None
                    else load_risk_policy(subj_pol_id, policy_dir=catalog_root)
                )
                if isinstance(loaded_raw, Mapping):
                    loaded_authoritative = True
                    raw_d = dict(loaded_raw)
                    loaded_pol = normalize_risk_policy(raw_d, default_id=subj_pol_id)
                    l_meta = dict(loaded_pol.metadata) if isinstance(loaded_pol.metadata, Mapping) else {}
                    l_tenant = _first_text_or_none(l_meta, "tenant_id")
                    if effective_tenant_id and l_tenant and l_tenant != effective_tenant_id:
                        rejected_catalog_policy_ids.add(subj_pol_id)
                        diagnostic_source = _higher_priority_absence_source(
                            diagnostic_source,
                            "cross_tenant_policy_or_approval_rejected",
                        )
                        continue
                    if req_version and str(loaded_pol.version) != req_version:
                        rejected_catalog_policy_ids.add(subj_pol_id)
                        diagnostic_source = _higher_priority_absence_source(
                            diagnostic_source,
                            "policy_version_mismatch",
                        )
                        continue
                    w_ok, w_diag = _is_policy_Candidate_valid_for_profile_window(
                        loaded_pol,
                        raw_d,
                        clean_stage=eff_stage,
                        require_limits=require_limits,
                        now_dt=effective_now,
                    )
                    if not w_ok:
                        rejected_catalog_policy_ids.add(subj_pol_id)
                        diagnostic_source = _higher_priority_absence_source(diagnostic_source, w_diag)
                        continue
                    veto_diag = _policy_approval_veto_source(loaded_pol, raw_d, l_meta)
                    if veto_diag is not None:
                        unapproved_catalog_policies.append(loaded_pol)
                        rejected_catalog_policy_ids.add(subj_pol_id)
                        vetoed_catalog_by_id[subj_pol_id] = veto_diag
                        diagnostic_source = _higher_priority_absence_source(diagnostic_source, veto_diag)
                        continue
                    decision_policy_candidates.append((loaded_pol.risk_policy_id, loaded_pol, raw_d))
                    continue
            except Exception:
                if loaded_authoritative:
                    rejected_catalog_policy_ids.add(subj_pol_id)
                    continue

        # Check if an inline policy object is embedded in the decision metadata/subject or base_pool.
        embedded_pol = subject.get("risk_policy") if isinstance(subject.get("risk_policy"), Mapping) else (
            meta.get("risk_policy") if isinstance(meta.get("risk_policy"), Mapping) else None
        )
        if isinstance(embedded_pol, Mapping) and _is_policy_content_mapping(embedded_pol):
            try:
                raw_d = dict(embedded_pol)
                norm_p = normalize_risk_policy(raw_d, default_id=subj_pol_id)
                e_meta = dict(norm_p.metadata) if isinstance(norm_p.metadata, Mapping) else {}
                e_tenant = _first_text_or_none(e_meta, "tenant_id")
                if effective_tenant_id and e_tenant and e_tenant != effective_tenant_id:
                    diagnostic_source = _higher_priority_absence_source(
                        diagnostic_source,
                        "cross_tenant_policy_or_approval_rejected",
                    )
                    continue
                veto_diag = _policy_approval_veto_source(norm_p, raw_d, e_meta)
                if veto_diag is not None:
                    diagnostic_source = _higher_priority_absence_source(diagnostic_source, veto_diag)
                    continue
                w_ok, w_diag = _is_policy_Candidate_valid_for_profile_window(
                    norm_p,
                    raw_d,
                    clean_stage=eff_stage,
                    require_limits=require_limits,
                    now_dt=effective_now,
                )
                if w_ok:
                    decision_policy_candidates.append((None, norm_p, raw_d))
                    continue
                diagnostic_source = _higher_priority_absence_source(diagnostic_source, w_diag)
                continue
            except Exception:
                pass

        if is_def_id:
            # Never synthesize limits for a default-id approval. Content comes only from a risk-officer draft
            # embedded in the decision subject, bound to the approved digest.
            draft = subject.get("risk_officer_draft") if isinstance(subject.get("risk_officer_draft"), Mapping) else {}
            draft_policy = draft.get("policy") if isinstance(draft.get("policy"), Mapping) else None
            if (
                draft_policy is not None
                and _optional_text(draft_policy.get("risk_policy_id")) == subj_pol_id
                and (_optional_text(draft_policy.get("version")) or "") == subj_pol_ver
                and risk_policy_digest(draft_policy) == _optional_text(subject.get("risk_policy_digest"))
            ):
                raw_draft = dict(draft_policy)
                decision_policy_candidates.append(
                    (None, normalize_risk_policy(raw_draft, default_id=subj_pol_id), raw_draft)
                )
                continue

        if isinstance(base_meta.get("risk_policy"), Mapping):
            base_pol_dict = dict(base_meta["risk_policy"])
            if str(base_pol_dict.get("risk_policy_id") or "") == subj_pol_id:
                if is_def_id:
                    base_pol_dict["version"] = subj_pol_ver
                try:
                    norm_p = normalize_risk_policy(base_pol_dict, default_id=subj_pol_id)
                    b_pol_meta = dict(norm_p.metadata) if isinstance(norm_p.metadata, Mapping) else {}
                    b_pol_tenant = _first_text_or_none(b_pol_meta, "tenant_id")
                    if effective_tenant_id and b_pol_tenant and b_pol_tenant != effective_tenant_id:
                        diagnostic_source = _higher_priority_absence_source(
                            diagnostic_source,
                            "cross_tenant_policy_or_approval_rejected",
                        )
                        continue
                    veto_diag = _policy_approval_veto_source(norm_p, base_pol_dict, b_pol_meta, base_meta)
                    if veto_diag is not None:
                        diagnostic_source = _higher_priority_absence_source(diagnostic_source, veto_diag)
                        continue
                    w_ok, w_diag = _is_policy_Candidate_valid_for_profile_window(
                        norm_p,
                        base_pol_dict,
                        clean_stage=eff_stage,
                        require_limits=require_limits,
                        now_dt=effective_now,
                    )
                    if w_ok:
                        decision_policy_candidates.append((None, norm_p, base_pol_dict))
                        continue
                    diagnostic_source = _higher_priority_absence_source(diagnostic_source, w_diag)
                    continue
                except Exception:
                    pass

    # Also include the base_pool inline policy (or default paper pool baseline) so that existing/default
    # pool approvals (including apv-capital-pool-{pool_id} or custom decision_ids) are evaluated.
    fallback_inline_raw: dict[str, Any]
    fallback_inline_policy: RiskPolicy | None
    fallback_is_vetoed = False
    if isinstance(base_meta.get("risk_policy"), Mapping) and _is_policy_content_mapping(base_meta["risk_policy"]):
        fallback_inline_raw = dict(base_meta["risk_policy"])
        if req_version and str(fallback_inline_raw.get("risk_policy_id") or "").startswith(
            ("rp-pool-", "paper-inline-policy:")
        ):
            fallback_inline_raw["version"] = req_version
        fallback_inline_policy = normalize_risk_policy(fallback_inline_raw, default_id=f"rp-{resolved_pool_id}")
        fb_id = fallback_inline_policy.risk_policy_id
        if fb_id in rejected_catalog_policy_ids:
            fallback_is_vetoed = True
            if fb_id in vetoed_catalog_by_id:
                diagnostic_source = _higher_priority_absence_source(diagnostic_source, vetoed_catalog_by_id[fb_id])
        else:
            fb_veto = _policy_approval_veto_source(fallback_inline_policy, fallback_inline_raw, base_meta)
            if fb_veto is not None:
                fallback_is_vetoed = True
                diagnostic_source = _higher_priority_absence_source(diagnostic_source, fb_veto)
    else:
        # No inline policy: nothing is invented. A candidate can only come from the catalog or an approved
        # decision that embeds a risk-officer draft.
        fallback_inline_raw = {}
        fallback_inline_policy = None
    if not fallback_is_vetoed and fallback_inline_policy is not None:
        decision_policy_candidates.append((None, fallback_inline_policy, fallback_inline_raw))

    # 3. Verify each candidate policy against Governance approvals.
    verified_matches: list[tuple[str | None, RiskPolicy, dict[str, Any], str]] = []
    seen_match_keys: set[tuple[str, str, str, str]] = set()
    ambiguous_decision_ids: list[str] = []
    target_approval_lookup_overflow = False

    all_candidates_to_check: list[tuple[str | None, RiskPolicy, dict[str, Any], bool]] = [
        (cand_pol.risk_policy_id, cand_pol, raw_d, True)
        for cand_pol, raw_d in catalog_candidates
    ] + [
        (pref_ref, cand_pol, raw_d, pref_ref is not None)
        for pref_ref, cand_pol, raw_d in decision_policy_candidates
    ]

    for pref_ref, cand_pol, raw_d, is_catalog in all_candidates_to_check:
        cand_digest = risk_policy_digest(cand_pol)
        cand_meta = dict(cand_pol.metadata) if isinstance(cand_pol.metadata, Mapping) else {}
        cand_veto = _policy_approval_veto_source(cand_pol, raw_d, cand_meta)
        if cand_veto is not None:
            diagnostic_source = _higher_priority_absence_source(diagnostic_source, cand_veto)
            continue
        ref_options: list[str | None] = [pref_ref, None] if pref_ref is not None else [None]
        matched_for_this_policy = False
        policy_ver_diag: str | None = None
        for ref_opt in ref_options:
            cand_pool = _build_candidate_pool(ref_opt, cand_pol)
            ver_id, all_ver_ids, ver_diag = _verify_policy_owner_approval_decisions(
                resolved_pool_id=resolved_pool_id,
                effective_tenant_id=effective_tenant_id,
                clean_stage=eff_stage,
                clean_profile=eff_profile,
                policy_model=cand_pol,
                computed_digest=cand_digest,
                raw_policy_mapping=raw_d,
                pool_obj=cand_pool,
                pool_meta=cand_pool["metadata"],
                b_map={},
                b_meta={},
                policy_meta=cand_meta,
                approval_decision_id=approval_decision_id,
                approval_decision=None,
                approval_decisions=None,
                approval_reader=approval_reader,
                capital_pool_reader=capital_pool_reader,
                now_dt=effective_now,
            )
            policy_ver_diag = _higher_priority_absence_source(policy_ver_diag, ver_diag)
            diagnostic_source = _higher_priority_absence_source(diagnostic_source, ver_diag)
            if ver_diag == "policy_candidates_ambiguous":
                target_approval_lookup_overflow = True
                for aid in all_ver_ids:
                    if aid not in ambiguous_decision_ids:
                        ambiguous_decision_ids.append(aid)
            if len(all_ver_ids) > 1:
                for aid in all_ver_ids:
                    if aid not in ambiguous_decision_ids:
                        ambiguous_decision_ids.append(aid)
            if ver_id is not None:
                match_key = (cand_pol.risk_policy_id, str(cand_pol.version), cand_digest, ver_id)
                if match_key not in seen_match_keys:
                    seen_match_keys.add(match_key)
                    verified_matches.append((ref_opt, cand_pol, raw_d, ver_id))
                matched_for_this_policy = True
                break
        if not matched_for_this_policy and is_catalog and policy_ver_diag is None:
            diagnostic_source = _higher_priority_absence_source(
                diagnostic_source,
                "policy_approval_missing",
            )

    if (
        len(verified_matches) > 1
        or ambiguous_decision_ids
        or catalog_overflow
        or pool_decision_lookup_overflow
        or target_approval_lookup_overflow
    ):
        pol_ids = tuple(dict.fromkeys(m[1].risk_policy_id for m in verified_matches)) or tuple(
            dict.fromkeys(c[0].risk_policy_id for c in catalog_candidates)
        )
        dec_ids = tuple(
            dict.fromkeys([*(m[3] for m in verified_matches), *ambiguous_decision_ids])
        )
        return ProfilePolicyAutoResolution(
            resolved=False,
            status="ambiguous_profile_mapping",
            reason_code="risk_policy_profile_mapping_ambiguous",
            reason=(
                f"Ambiguous risk policy candidates for profile {eff_profile!r} on pool {resolved_pool_id!r}: "
                f"policies={list(pol_ids)!r}, decisions={list(dec_ids)!r}; explicit risk_policy_ref or "
                f"single authoritative approval is required."
            ),
            absence_source="policy_candidates_ambiguous",
            candidate_policy=None,
            ambiguous_policy_ids=pol_ids,
            ambiguous_decision_ids=dec_ids,
        )

    if len(verified_matches) == 1:
        win_ref, win_pol, win_raw, win_dec_id = verified_matches[0]
        return ProfilePolicyAutoResolution(
            resolved=True,
            status="valid",
            risk_policy_ref=win_ref,
            policy=win_pol,
            raw_policy=win_raw,
            approval_decision_id=win_dec_id,
        )

    if len(catalog_candidates) > 1:
        pol_ids = tuple(dict.fromkeys(c[0].risk_policy_id for c in catalog_candidates))
        return ProfilePolicyAutoResolution(
            resolved=False,
            status="ambiguous_profile_mapping",
            reason_code="risk_policy_profile_mapping_ambiguous",
            reason=(
                f"Multiple unapproved risk policy definitions match profile {eff_profile!r} in tenant "
                f"{effective_tenant_id!r} ({list(pol_ids)!r}); cannot auto-resolve uniquely."
            ),
            absence_source="policy_candidates_ambiguous",
            candidate_policy=None,
            ambiguous_policy_ids=pol_ids,
        )

    best_candidate_policy = (
        catalog_candidates[0][0]
        if catalog_candidates
        else (unapproved_catalog_policies[0] if unapproved_catalog_policies else fallback_inline_policy)
    )
    final_absence = diagnostic_source or (
        "policy_approval_missing"
        if (catalog_candidates or unapproved_catalog_policies)
        else "policy_definition_and_approval_absent"
    )
    on_policy = (
        f" on policy {best_candidate_policy.risk_policy_id!r}@{best_candidate_policy.version!r}"
        if best_candidate_policy is not None else ""
    )
    if final_absence == "approval_reader_unavailable" and reader_err_types:
        on_policy += f" (approval_reader_error_types={list(reader_err_types)!r})"
    return ProfilePolicyAutoResolution(
        resolved=False,
        status="unresolved_profile_mapping",
        reason_code="risk_policy_profile_mapping_unapproved",
        reason=(
            f"No approved capital_pool_activation RiskPolicy limit mapping exists for risk profile "
            f"{eff_profile!r}{on_policy} "
            f"(absence_source={final_absence}); policy owner must approve explicit gross_limit/net_limit/"
            f"max_leverage/max_single_name_weight or bind an approved risk_policy_ref."
        ),
        absence_source=final_absence,
        candidate_policy=best_candidate_policy,
    )


def _caller_is_risk_reducing_order_gate() -> bool:
    """Detect whether ``EvaluateOrderRiskGate`` is evaluating a pure position-reducing or closing order."""
    frame = inspect.currentframe()
    try:
        cur = frame.f_back if frame is not None else None
        depth = 0
        while cur is not None and depth < 6:
            if cur.f_code.co_name == "EvaluateOrderRiskGate":
                f_locals = cur.f_locals
                is_opening_or_increasing = f_locals.get("is_opening_or_increasing")
                is_sign_reversal = f_locals.get("is_sign_reversal")
                q_before = _optional_float(f_locals.get("q_before"))
                q_after = _optional_float(f_locals.get("q_after"))
                signed_order_qty = _optional_float(f_locals.get("signed_order_qty"))
                if (
                    is_opening_or_increasing is False
                    and is_sign_reversal is False
                    and q_before is not None
                    and q_after is not None
                    and signed_order_qty is not None
                    and abs(q_before) > 1e-12
                    and abs(signed_order_qty) > 1e-12
                    and abs(q_after) < abs(q_before) - 1e-12
                    and (
                        (q_before > 0.0 and q_after >= -1e-12 and signed_order_qty < 0.0)
                        or (q_before < 0.0 and q_after <= 1e-12 and signed_order_qty > 0.0)
                    )
                ):
                    return True
                return False
            cur = cur.f_back
            depth += 1
        return False
    finally:
        del frame


def _policy_eligible_for_governed_risk_reduction(
    resolution: BoundPoolRiskPolicyResolution,
    *,
    expected_stage: str | None = None,
    require_limits: Sequence[str] = (),
) -> bool:
    """Check whether an unresolved/expired owner-mapping resolution retains a valid active policy for risk reduction."""
    if not resolution.allow_risk_reducing_actions or resolution.policy is None:
        return False
    if resolution.status == "unresolved_profile_mapping":
        if resolution.reason_code != "risk_policy_profile_mapping_unapproved":
            return False
    elif resolution.status == "stale":
        if resolution.reason_code != "risk_policy_stale" or "expired at " not in str(resolution.reason or ""):
            return False
    else:
        return False

    policy_model = resolution.policy
    if str(policy_model.status or "").strip().lower() not in _ACTIVE_POLICY_STATUSES:
        return False
    if resolution.tenant_id and resolution.policy_tenant_id and resolution.tenant_id != resolution.policy_tenant_id:
        return False

    clean_stage = _optional_text(expected_stage) or _optional_text(resolution.stage) or "paper"
    if clean_stage and policy_model.allowed_stages and clean_stage not in policy_model.allowed_stages:
        return False

    for field_name in _NON_NEGATIVE_SCALAR_LIMIT_FIELDS:
        val = getattr(policy_model, field_name, None)
        if val is None:
            continue
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(float(val)):
            return False
        if field_name in _POSITIVE_EXPOSURE_LIMIT_FIELDS and float(val) <= 0.0:
            return False
        if field_name == "min_cash_reserve_pct" and float(val) >= 1.0:
            return False
        if float(val) < 0.0:
            return False

    for flex_field in _FLEX_EXPOSURE_LIMIT_FIELDS:
        flex_val = getattr(policy_model, flex_field, None)
        if flex_val in (None, (), {}, []):
            continue
        items = flex_val.items() if isinstance(flex_val, Mapping) else [(flex_field, flex_val)]
        for _item_key, item_val in items:
            if (
                not isinstance(item_val, (int, float))
                or isinstance(item_val, bool)
                or not math.isfinite(float(item_val))
                or float(item_val) < 0.0
            ):
                return False

    for required_limit in require_limits:
        limit_val = getattr(policy_model, required_limit, None)
        if limit_val in (None, (), {}, []):
            return False
        if required_limit in _POSITIVE_EXPOSURE_LIMIT_FIELDS and float(limit_val) <= 0.0:
            return False

    has_positive_limit = any(
        isinstance(getattr(policy_model, f_name, None), (int, float))
        and not isinstance(getattr(policy_model, f_name, None), bool)
        and float(getattr(policy_model, f_name)) > 0.0
        for f_name in _POSITIVE_EXPOSURE_LIMIT_FIELDS
    )
    return has_positive_limit


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
        ref_from_pool = _first_text_or_none(pool_obj, "risk_policy_ref") or _first_text_or_none(
            pool_meta, "risk_policy_ref"
        )
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
    ref_attr = _optional_text(getattr(pool_obj, "risk_policy_ref", None)) or _first_text_or_none(
        pool_meta, "risk_policy_ref"
    )
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
    require_approved_profile_mapping: bool | None = None,
    approval_decision_id: str | None = None,
    approval_decision: Any | None = None,
    approval_decisions: Sequence[Any] | None = None,
    approval_reader: Any | None = None,
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
    # Request provenance belongs to the caller/pool/binding, never the candidate
    # policy. Check every alias so precedence cannot hide a contradictory source.
    profile_keys = ("requested_risk_profile", "risk_profile", "requested_risk", "risk")
    profile_sources = {"requested_profile": requested_profile}
    for label, container in (
        ("pool", pool_obj), ("pool.metadata", pool_meta),
        ("binding", b_map), ("binding.metadata", b_meta),
        ("binding.capital_pool", pool_sub),
        ("binding.capital_pool.metadata", pool_sub.get("metadata")),
    ):
        for key in profile_keys:
            value = container.get(key) if isinstance(container, Mapping) else getattr(container, key, None)
            if value is not None:
                profile_sources[f"{label}.{key}"] = value
    present_profiles = {key: value for key, value in profile_sources.items() if value is not None and value != ""}
    invalid_profile = any(not isinstance(value, str) or not value.strip() for value in present_profiles.values())
    distinct_profiles = {value.strip() for value in present_profiles.values() if isinstance(value, str) and value.strip()}
    profile_absence = (
        "requested_profile_invalid" if invalid_profile else
        "requested_profile_conflict" if len(distinct_profiles) > 1 else
        "requested_profile_missing" if not distinct_profiles else None
    )
    clean_profile = next(iter(distinct_profiles)) if profile_absence is None else None
    needs_approved_mapping = bool(
        require_approved_profile_mapping is True
        or pool_meta.get("require_approved_profile_mapping") is True
        or b_meta.get("require_approved_profile_mapping") is True
        or getattr(pool_obj, "require_approved_profile_mapping", False) is True
        or pool_sub.get("require_approved_profile_mapping") is True
        or _mapping(pool_sub.get("metadata")).get("require_approved_profile_mapping") is True
        or (require_approved_profile_mapping is not False and bool(present_profiles))
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

    def _profile_provenance_failure(
        policy_obj: RiskPolicy | None = None, policy_tenant: str | None = None,
    ) -> BoundPoolRiskPolicyResolution:
        return _fail(
            "unresolved_profile_mapping", "risk_policy_profile_mapping_unapproved",
            f"Requested profile provenance rejected: {profile_absence}; repair the original request before policy mapping",
            policy_obj=policy_obj, policy_tenant=policy_tenant,
            profile_status="unresolved_owner_decision_required",
            owner_decision={
                "policy_owner": "services/capital/risk_policy.py",
                "accountable_domain": "capital_risk_policy_owner",
                "absence_source": profile_absence,
                "requested_profile": None,
                "profile_sources": present_profiles,
                "capital_pool_id": resolved_pool_id,
                "tenant_id": effective_tenant_id,
                "stage": clean_stage,
            },
        )

    if clean_expected_tenant and pool_tenant_id and clean_expected_tenant != pool_tenant_id:
        return _fail(
            "wrong_tenant",
            "risk_policy_wrong_tenant",
            f"Capital pool {resolved_pool_id!r} belongs to tenant {pool_tenant_id!r}, expected {clean_expected_tenant!r}",
            policy_tenant=pool_tenant_id,
        )

    if needs_approved_mapping and not resolved_ref and not explicit_inline_key:
        if profile_absence is not None:
            return _profile_provenance_failure()
        effective_req_profile = clean_profile
        auto_now = _parse_iso_timestamp(as_of) or datetime.now(timezone.utc)
        auto_res = discover_approved_profile_risk_policy(
            resolved_pool_id=resolved_pool_id,
            effective_tenant_id=effective_tenant_id,
            clean_stage=clean_stage or "paper",
            clean_profile=effective_req_profile,
            expected_version=_optional_text(expected_version),
            base_pool=pool_obj if isinstance(pool_obj, Mapping) else None,
            approval_decision_id=approval_decision_id,
            approval_reader=approval_reader,
            capital_pool_reader=capital_pool_reader,
            risk_policy_loader=risk_policy_loader,
            require_limits=require_limits,
            now_dt=auto_now,
        )
        if auto_res.resolved and auto_res.policy is not None and auto_res.approval_decision_id is not None:
            resolved_ref = auto_res.risk_policy_ref
            inline_policy = auto_res.raw_policy or auto_res.policy.to_dict()
            explicit_inline_key = True
            source = "risk_policy_ref" if resolved_ref else "pool_metadata_inline"
            if not approval_decision_id:
                approval_decision_id = auto_res.approval_decision_id
        elif auto_res.status == "ambiguous_profile_mapping":
            decision_req = {
                "policy_owner": "services/capital/risk_policy.py",
                "governance_surface": "services/control-plane/governance/risk_policy.py",
                "accountable_domain": "capital_risk_policy_owner",
                "absence_source": "policy_candidates_ambiguous",
                "requested_profile": effective_req_profile,
                "capital_pool_id": resolved_pool_id,
                "tenant_id": effective_tenant_id,
                "stage": clean_stage or "paper",
                "ambiguous_policy_ids": list(auto_res.ambiguous_policy_ids),
                "ambiguous_approval_decision_ids": list(auto_res.ambiguous_decision_ids),
                "decision_required": auto_res.reason
                or (
                    f"Ambiguous risk policy candidates for profile {effective_req_profile!r}; "
                    f"policy owner must select a single authoritative policy or approval."
                ),
                "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
                "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
                "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
                "autoresolve_task_id": "PAPER-RISK-PROFILE-AUTORESOLVE-20261122",
            }
            return _fail(
                "ambiguous_profile_mapping",
                "risk_policy_profile_mapping_ambiguous",
                decision_req["decision_required"],
                policy_obj=auto_res.candidate_policy,
                profile_status="ambiguous_owner_decision_required",
                owner_decision=decision_req,
            )
        else:
            absence_src = auto_res.absence_source or "policy_definition_and_approval_absent"
            decision_req = {
                "policy_owner": "services/capital/risk_policy.py",
                "governance_surface": "services/control-plane/governance/risk_policy.py",
                "accountable_domain": "capital_risk_policy_owner",
                "absence_source": absence_src,
                "requested_profile": effective_req_profile,
                "capital_pool_id": resolved_pool_id,
                "tenant_id": effective_tenant_id,
                "stage": clean_stage or "paper",
                "decision_required": (
                    f"No approved capital_pool_activation RiskPolicy limit mapping exists for risk profile "
                    f"{effective_req_profile!r} (absence_source={absence_src}); policy owner must approve explicit "
                    f"gross_limit/net_limit/max_leverage/max_single_name_weight or bind an approved risk_policy_ref."
                ),
                "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
                "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
                "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
                "autoresolve_task_id": "PAPER-RISK-PROFILE-AUTORESOLVE-20261122",
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

    # Staleness checks: explicit stale flag, superseded_by, expired status, effective_from/expires_at/effective_to, expected version/digest.
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

    now_dt = _parse_iso_timestamp(as_of) or datetime.now(timezone.utc)
    raw_policy_meta = (
        dict(policy_model.metadata)
        if isinstance(policy_model.metadata, Mapping)
        else {}
    )
    lower_bound_sources: list[tuple[str, Any]] = []
    if isinstance(raw_policy_mapping, Mapping):
        lower_bound_sources.append(("policy.effective_from", raw_policy_mapping.get("effective_from")))
    lower_bound_sources.extend(
        [
            ("policy.metadata.effective_from", raw_policy_meta.get("effective_from")),
            ("policy_meta.effective_from", policy_meta.get("effective_from")),
            ("pool.metadata.risk_policy_effective_from", pool_meta.get("risk_policy_effective_from")),
            ("pool.metadata.effective_from", pool_meta.get("effective_from")),
            ("binding.effective_from", b_map.get("effective_from")),
            ("binding.metadata.risk_policy_effective_from", b_meta.get("risk_policy_effective_from")),
            ("binding.metadata.effective_from", b_meta.get("effective_from")),
        ]
    )
    parsed_lower_bounds: list[tuple[str, str, datetime]] = []
    for label_name, raw_val in lower_bound_sources:
        if raw_val in (None, ""):
            continue
        raw_str = _optional_text(raw_val)
        parsed_dt = _parse_iso_timestamp(raw_str) if raw_str else None
        if parsed_dt is None:
            return _fail(
                "invalid",
                "risk_policy_invalid",
                f"Risk policy {policy_model.risk_policy_id!r} has malformed effective_from timestamp {raw_val!r} ({label_name})",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )
        assert raw_str is not None
        parsed_lower_bounds.append((label_name, raw_str, parsed_dt))

    upper_bound_sources: list[tuple[str, Any]] = []
    if isinstance(raw_policy_mapping, Mapping):
        upper_bound_sources.extend(
            [
                ("policy.expires_at", raw_policy_mapping.get("expires_at")),
                ("policy.effective_to", raw_policy_mapping.get("effective_to")),
            ]
        )
    upper_bound_sources.extend(
        [
            ("policy.metadata.expires_at", raw_policy_meta.get("expires_at")),
            ("policy.metadata.effective_to", raw_policy_meta.get("effective_to")),
            ("policy_meta.expires_at", policy_meta.get("expires_at")),
            ("policy_meta.effective_to", policy_meta.get("effective_to")),
            ("pool.metadata.risk_policy_expires_at", pool_meta.get("risk_policy_expires_at")),
            ("pool.metadata.risk_policy_effective_to", pool_meta.get("risk_policy_effective_to")),
            ("pool.metadata.expires_at", pool_meta.get("expires_at")),
            ("pool.metadata.effective_to", pool_meta.get("effective_to")),
            ("binding.expires_at", b_map.get("expires_at")),
            ("binding.effective_to", b_map.get("effective_to")),
            ("binding.metadata.risk_policy_expires_at", b_meta.get("risk_policy_expires_at")),
            ("binding.metadata.risk_policy_effective_to", b_meta.get("risk_policy_effective_to")),
            ("binding.metadata.expires_at", b_meta.get("expires_at")),
            ("binding.metadata.effective_to", b_meta.get("effective_to")),
        ]
    )
    parsed_upper_bounds: list[tuple[str, str, datetime]] = []
    for label_name, raw_val in upper_bound_sources:
        if raw_val in (None, ""):
            continue
        raw_str = _optional_text(raw_val)
        parsed_dt = _parse_iso_timestamp(raw_str) if raw_str else None
        if parsed_dt is None:
            return _fail(
                "invalid",
                "risk_policy_invalid",
                f"Risk policy {policy_model.risk_policy_id!r} has malformed expiration timestamp {raw_val!r} ({label_name})",
                policy_obj=policy_model,
                policy_tenant=policy_tenant_id,
            )
        assert raw_str is not None
        parsed_upper_bounds.append((label_name, raw_str, parsed_dt))

    effective_from_dt: datetime | None = None
    effective_from_raw: str | None = None
    if parsed_lower_bounds:
        _, effective_from_raw, effective_from_dt = max(parsed_lower_bounds, key=lambda item: item[2])

    expiry_dt: datetime | None = None
    expiry_raw: str | None = None
    if parsed_upper_bounds:
        _, expiry_raw, expiry_dt = min(parsed_upper_bounds, key=lambda item: item[2])

    if effective_from_dt is not None and expiry_dt is not None and expiry_dt <= effective_from_dt:
        return _fail(
            "invalid",
            "risk_policy_invalid",
            f"Risk policy {policy_model.risk_policy_id!r} has invalid effective window ({effective_from_raw}..{expiry_raw})",
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )
    if effective_from_dt is not None and effective_from_dt > now_dt:
        return _fail(
            "stale",
            "risk_policy_stale",
            f"Risk policy {policy_model.risk_policy_id!r} is outside its effective window (effective_from={effective_from_raw})",
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
        )
    if expiry_dt is not None and expiry_dt <= now_dt:
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
        if field_name == "min_cash_reserve_pct" and float(val) >= 1.0:
            return _fail(
                "invalid",
                "risk_policy_invalid_limit",
                f"Risk policy {policy_model.risk_policy_id!r} limit min_cash_reserve_pct={val!r} must be < 1.0",
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

    for flex_field in _FLEX_EXPOSURE_LIMIT_FIELDS:
        flex_val = getattr(policy_model, flex_field, None)
        if flex_val in (None, (), {}, []):
            continue
        items = flex_val.items() if isinstance(flex_val, Mapping) else [(flex_field, flex_val)]
        for item_key, item_val in items:
            if not isinstance(item_val, (int, float)) or isinstance(item_val, bool) or not math.isfinite(float(item_val)) or float(item_val) < 0.0:
                return _fail(
                    "invalid",
                    "risk_policy_invalid_limit",
                    f"Risk policy {policy_model.risk_policy_id!r} limit {flex_field}[{item_key!r}]={item_val!r} must be a non-negative finite number",
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

    policy_profile = _first_text_or_none(policy_meta, "requested_profile", "risk_profile", "requested_risk")
    is_default_inline_id = (
        str(policy_model.risk_policy_id).startswith("rp-pool-")
        or (bool(resolved_pool_id) and str(policy_model.risk_policy_id) == f"rp-{resolved_pool_id}")
        or policy_meta.get("default_paper_baseline") is True
        or pool_meta.get("default_paper_baseline") is True
    )
    explicit_profile_mapping = bool(
        needs_approved_mapping
        or policy_meta.get("require_approved_profile_mapping") is True
        or (require_approved_profile_mapping is not False and policy_profile is not None)
    )
    needs_approved_mapping = bool(
        explicit_profile_mapping
        or (require_approved_profile_mapping is not False and is_default_inline_id)
    )
    # Older pools may already have an exact pool/limits approval without a
    # separate profile-mapping request. Preserve that contract, but never use
    # catalog discovery or policy metadata to invent the missing request.
    legacy_pool_contract = (
        profile_absence == "requested_profile_missing"
        and is_default_inline_id and not explicit_profile_mapping
    )
    if needs_approved_mapping and profile_absence is not None and not legacy_pool_contract:
        # Retain the validated bound policy for governed reductions and exits.
        return _profile_provenance_failure(policy_model, policy_tenant_id)
    verification_pool = pool_obj
    if legacy_pool_contract and isinstance(pool_obj, Mapping):
        verification_pool = {**pool_obj, "approval_digest": capital_pool_approval_digest(pool_obj)}
    verified_decision_id, all_verified_decision_ids, verification_diag = _verify_policy_owner_approval_decisions(
        resolved_pool_id=resolved_pool_id,
        effective_tenant_id=effective_tenant_id,
        clean_stage=clean_stage,
        clean_profile=clean_profile,
        policy_model=policy_model,
        computed_digest=computed_digest,
        raw_policy_mapping=raw_policy_mapping,
        pool_obj=verification_pool,
        pool_meta=pool_meta,
        b_map=b_map,
        b_meta=b_meta,
        policy_meta=policy_meta,
        approval_decision_id=approval_decision_id,
        approval_decision=approval_decision,
        approval_decisions=approval_decisions,
        approval_reader=approval_reader,
        capital_pool_reader=capital_pool_reader,
        now_dt=now_dt,
        allow_target_discovery=not legacy_pool_contract,
    )
    if needs_approved_mapping and profile_absence is not None and verified_decision_id is None:
        return _profile_provenance_failure(policy_model, policy_tenant_id)

    if needs_approved_mapping and (
        len(all_verified_decision_ids) > 1
        or verification_diag == "policy_candidates_ambiguous"
    ):
        effective_req_profile = clean_profile
        ambig_req = {
            "policy_owner": "services/capital/risk_policy.py",
            "governance_surface": "services/control-plane/governance/risk_policy.py",
            "accountable_domain": "capital_risk_policy_owner",
            "absence_source": "policy_candidates_ambiguous",
            "requested_profile": effective_req_profile,
            "capital_pool_id": resolved_pool_id,
            "tenant_id": effective_tenant_id,
            "stage": clean_stage or "paper",
            "candidate_risk_policy_id": policy_model.risk_policy_id,
            "candidate_risk_policy_version": policy_model.version,
            "candidate_risk_policy_digest": computed_digest,
            "ambiguous_approval_decision_ids": list(all_verified_decision_ids),
            "decision_required": (
                f"Ambiguous risk policy approval mapping for profile {effective_req_profile!r} on pool "
                f"{resolved_pool_id!r}: multiple valid ApprovalDecisions match ({list(all_verified_decision_ids)!r})."
            ),
            "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
            "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
            "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
            "autoresolve_task_id": "PAPER-RISK-PROFILE-AUTORESOLVE-20261122",
        }
        return _fail(
            "ambiguous_profile_mapping",
            "risk_policy_profile_mapping_ambiguous",
            ambig_req["decision_required"],
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
            profile_status="ambiguous_owner_decision_required",
            owner_decision=ambig_req,
        )

    policy_veto_diag = _policy_approval_veto_source(
        policy_model,
        raw_policy_mapping,
        policy_meta,
        pool_meta,
        b_meta,
    )
    owner_verified_explicit_contract = False
    if not is_default_inline_id:
        owner_ref_policy = authoritative_ref_policy
        loaded_owner_raw: Mapping[str, Any] | None = (
            raw_policy_mapping if authoritative_ref_policy is not None else None
        )
        if owner_ref_policy is None:
            try:
                raw_owner_candidate = (
                    risk_policy_loader(policy_model.risk_policy_id)
                    if risk_policy_loader is not None
                    else load_risk_policy(policy_model.risk_policy_id)
                )
                if isinstance(raw_owner_candidate, Mapping):
                    loaded_owner_raw = dict(raw_owner_candidate)
                owner_ref_policy = normalize_risk_policy(raw_owner_candidate, default_id=policy_model.risk_policy_id)
            except Exception:
                owner_ref_policy = None
        if owner_ref_policy is not None:
            owner_meta = dict(owner_ref_policy.metadata) if isinstance(owner_ref_policy.metadata, Mapping) else {}
            owner_tenant = _first_text_or_none(owner_meta, "tenant_id")
            owner_profile = _first_text_or_none(owner_meta, "requested_profile", "risk_profile", "requested_risk")
            policy_profile = _first_text_or_none(policy_meta, "requested_profile", "risk_profile", "requested_risk")
            owner_veto = _policy_approval_veto_source(owner_ref_policy, loaded_owner_raw, owner_meta)
            if owner_veto is not None:
                policy_veto_diag = _higher_priority_absence_source(policy_veto_diag, owner_veto)
            has_bound_approval_provenance = bool(
                _optional_text(approval_decision_id)
                or approval_decision is not None
                or bool(approval_decisions)
                or (
                    pool_obj is not None
                    and not isinstance(pool_obj, (Mapping, RiskPolicy, str))
                    and (
                        _optional_text(getattr(pool_obj, "profile_mapping_approval_id", None)) is not None
                        or _optional_text(getattr(pool_obj, "approval_decision_id", None)) is not None
                    )
                )
                or any(
                    isinstance(container, Mapping)
                    and (
                        _first_text_or_none(
                            container,
                            "profile_mapping_approval_id",
                            "approval_decision_id",
                            "decision_id",
                        )
                        is not None
                        or container.get("profile_mapping_approval") is not None
                        or container.get("approval_decision") is not None
                        or bool(container.get("approval_decisions"))
                        or container.get("profile_auto_resolved") is True
                    )
                    for container in (
                        pool_obj if isinstance(pool_obj, Mapping) else None,
                        pool_meta,
                        b_map,
                        b_meta,
                        pool_sub,
                        dict(pool_sub.get("metadata")) if isinstance(pool_sub.get("metadata"), Mapping) else None,
                        raw_policy_mapping,
                        policy_meta,
                        loaded_owner_raw,
                        owner_meta,
                    )
                )
            )
            explicitly_unapproved = (
                policy_veto_diag is not None
                or verification_diag is not None
                or has_bound_approval_provenance
                or owner_profile is not None
                or policy_profile is not None
                or owner_meta.get("require_approved_profile_mapping") is True
                or policy_meta.get("require_approved_profile_mapping") is True
                or pool_meta.get("require_approved_profile_mapping") is True
                or b_meta.get("require_approved_profile_mapping") is True
                or pool_meta.get("profile_auto_resolved") is True
            )
            if explicitly_unapproved:
                if policy_veto_diag is not None:
                    verification_diag = _higher_priority_absence_source(verification_diag, policy_veto_diag)
                elif verification_diag is None:
                    verification_diag = "policy_approval_missing"
            elif (
                owner_ref_policy.risk_policy_id == policy_model.risk_policy_id
                and str(owner_ref_policy.version) == str(policy_model.version)
                and risk_policy_digest(owner_ref_policy) == computed_digest
                and (not effective_tenant_id or not owner_tenant or owner_tenant == effective_tenant_id)
                and (not clean_profile or not owner_profile or owner_profile == clean_profile)
            ):
                owner_verified_explicit_contract = True

    if policy_veto_diag is not None:
        verified_decision_id = None
        owner_verified_explicit_contract = False
        verification_diag = _higher_priority_absence_source(verification_diag, policy_veto_diag)

    if (needs_approved_mapping or policy_veto_diag is not None) and verified_decision_id is None and not owner_verified_explicit_contract:
        effective_req_profile = clean_profile
        if is_default_inline_id and not resolved_ref and policy_veto_diag is None:
            auto_res = discover_approved_profile_risk_policy(
                resolved_pool_id=resolved_pool_id,
                effective_tenant_id=effective_tenant_id,
                clean_stage=clean_stage or "paper",
                clean_profile=effective_req_profile,
                expected_version=target_expected_version or (str(policy_model.version) if explicit_inline_key else None),
                base_pool=pool_obj if isinstance(pool_obj, Mapping) else None,
                approval_decision_id=approval_decision_id,
                approval_reader=approval_reader,
                capital_pool_reader=capital_pool_reader,
                risk_policy_loader=risk_policy_loader,
                require_limits=require_limits,
                now_dt=now_dt,
            )
            if auto_res.resolved and auto_res.policy is not None and auto_res.approval_decision_id is not None:
                policy_model = auto_res.policy
                computed_digest = risk_policy_digest(policy_model)
                verified_decision_id = auto_res.approval_decision_id
                if auto_res.risk_policy_ref:
                    resolved_ref = auto_res.risk_policy_ref
                    source = "risk_policy_ref"
                policy_meta = dict(policy_model.metadata) if isinstance(policy_model.metadata, Mapping) else {}
                policy_tenant_id = _first_text_or_none(policy_meta, "tenant_id") or policy_tenant_id
            elif auto_res.status == "ambiguous_profile_mapping":
                ambig_req = {
                    "policy_owner": "services/capital/risk_policy.py",
                    "governance_surface": "services/control-plane/governance/risk_policy.py",
                    "accountable_domain": "capital_risk_policy_owner",
                    "absence_source": "policy_candidates_ambiguous",
                    "requested_profile": effective_req_profile,
                    "capital_pool_id": resolved_pool_id,
                    "tenant_id": effective_tenant_id,
                    "stage": clean_stage or "paper",
                    "ambiguous_policy_ids": list(auto_res.ambiguous_policy_ids),
                    "ambiguous_approval_decision_ids": list(auto_res.ambiguous_decision_ids),
                    "decision_required": auto_res.reason
                    or f"Ambiguous risk policy candidates for profile {effective_req_profile!r} on pool {resolved_pool_id!r}.",
                    "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
                    "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
                    "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
                    "autoresolve_task_id": "PAPER-RISK-PROFILE-AUTORESOLVE-20261122",
                }
                return _fail(
                    "ambiguous_profile_mapping",
                    "risk_policy_profile_mapping_ambiguous",
                    ambig_req["decision_required"],
                    policy_obj=auto_res.candidate_policy or policy_model,
                    policy_tenant=policy_tenant_id,
                    profile_status="ambiguous_owner_decision_required",
                    owner_decision=ambig_req,
                )
            else:
                verification_diag = _higher_priority_absence_source(verification_diag, auto_res.absence_source)
                if auto_res.candidate_policy is not None and not str(auto_res.candidate_policy.risk_policy_id).startswith("rp-pool-"):
                    policy_model = auto_res.candidate_policy
                    computed_digest = risk_policy_digest(policy_model)

    if (needs_approved_mapping or policy_veto_diag is not None) and verified_decision_id is None and not owner_verified_explicit_contract:
        effective_req_profile = clean_profile
        cand_pool_digest = (
            (
                _first_text_or_none(pool_obj, "approval_digest", "pool_approval_digest")
                if isinstance(pool_obj, Mapping)
                else _optional_text(getattr(pool_obj, "approval_digest", None))
            )
            or _first_text_or_none(pool_meta, "approval_digest", "pool_approval_digest")
            or (
                capital_pool_approval_digest(pool_obj)
                if pool_obj is not None
                and (
                    not isinstance(pool_obj, (Mapping, RiskPolicy, str))
                    or (
                        isinstance(pool_obj, Mapping)
                        and any(k in pool_obj for k in ("owner_id", "currency", "budget", "single_runtime_enforced"))
                    )
                )
                else None
            )
        )
        absence_src = verification_diag or (
            "policy_approval_missing" if not is_default_inline_id else "policy_definition_and_approval_absent"
        )
        decision_req = {
            "policy_owner": "services/capital/risk_policy.py",
            "governance_surface": "services/control-plane/governance/risk_policy.py",
            "accountable_domain": "capital_risk_policy_owner",
            "absence_source": absence_src,
            "requested_profile": effective_req_profile,
            "capital_pool_id": resolved_pool_id,
            "tenant_id": effective_tenant_id,
            "stage": clean_stage or "paper",
            "candidate_risk_policy_id": policy_model.risk_policy_id,
            "candidate_risk_policy_version": policy_model.version,
            "candidate_risk_policy_digest": computed_digest,
            **(
                {"candidate_pool_approval_digest": cand_pool_digest}
                if cand_pool_digest
                else {}
            ),
            "decision_required": (
                f"No approved capital_pool_activation RiskPolicy limit mapping exists for risk profile "
                f"{effective_req_profile!r} on policy {policy_model.risk_policy_id!r}@{policy_model.version!r} "
                f"(absence_source={absence_src}); policy owner must approve explicit gross_limit/net_limit/"
                f"max_leverage/max_single_name_weight or bind an approved risk_policy_ref."
            ),
            "disposition_task_id": "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122",
            "hosted_proof_task_id": "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121",
            "provenance_task_id": "PAPER-RISK-POLICY-PROVENANCE-20261121",
            "autoresolve_task_id": "PAPER-RISK-PROFILE-AUTORESOLVE-20261122",
        }
        return _fail(
            "unresolved_profile_mapping",
            "risk_policy_profile_mapping_unapproved",
            decision_req["decision_required"],
            policy_obj=policy_model,
            policy_tenant=policy_tenant_id,
            profile_status="unresolved_owner_decision_required",
            owner_decision=decision_req,
        )

    is_explicit_contract = bool(
        owner_verified_explicit_contract
        or source == "risk_policy_ref"
        or policy_meta.get("explicit_policy") is True
        or pool_meta.get("risk_policy_explicit") is True
        or not is_default_inline_id
    )
    if verified_decision_id is not None:
        profile_status = "approved_profile_mapping" if is_default_inline_id or clean_profile else "explicit_policy"
    else:
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
        profile_mapping_approval_id=verified_decision_id,
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
    pool: Any | None = None,
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
    requested_profile: str | None = None,
    require_approved_profile_mapping: bool | None = None,
    approval_decision_id: str | None = None,
    approval_decision: Any | None = None,
    approval_decisions: Sequence[Any] | None = None,
    approval_reader: Any | None = None,
    as_of: str | None = None,
    allow_risk_reducing_actions: bool | None = None,
) -> RiskPolicy | None:
    """Resolve and load the bound CapitalPool's active ``RiskPolicy`` (never from signal metadata)."""
    resolved_pool_obj = pool
    effective_pool_reader = capital_pool_reader
    if resolved_pool_obj is None and capital_pool_reader is not None:
        b_map = dict(binding) if isinstance(binding, Mapping) else {}
        lookup_pool_id = str(
            b_map.get("capital_pool_id")
            or capital_pool_id
            or os.getenv("PANTHEON_CAPITAL_POOL_ID", "")
        ).strip()
        resolved_pool_obj = capital_pool_reader(lookup_pool_id)

    if (
        approval_reader is None
        and _capital_owner_approval_reader_from_pool_reader(effective_pool_reader) is None
    ):
        frame = inspect.currentframe()
        try:
            caller = frame.f_back if frame is not None else None
            caller_self = caller.f_locals.get("self") if caller is not None else None
            caller_reader = getattr(caller_self, "_capital_pool_reader", None) if caller_self is not None else None
            if _capital_owner_approval_reader_from_pool_reader(caller_reader) is not None:
                effective_pool_reader = caller_reader
        finally:
            del frame

    resolution = resolve_pool_risk_policy_binding(
        capital_pool_id=capital_pool_id,
        pool=resolved_pool_obj,
        binding=binding,
        capital_pool_reader=effective_pool_reader,
        risk_policy_ref=risk_policy_ref,
        risk_policy_loader=risk_policy_loader,
        pool_store_path=pool_store_path,
        expected_tenant_id=expected_tenant_id,
        expected_stage=expected_stage,
        expected_version=expected_version,
        expected_digest=expected_digest,
        require_active=require_active,
        require_limits=require_limits,
        requested_profile=requested_profile,
        require_approved_profile_mapping=require_approved_profile_mapping,
        approval_decision_id=approval_decision_id,
        approval_decision=approval_decision,
        approval_decisions=approval_decisions,
        approval_reader=approval_reader,
        as_of=as_of,
        raise_on_error=False,
    )
    if resolution.resolved:
        return resolution.policy
    effective_allow_risk_reducing = (
        allow_risk_reducing_actions
        if allow_risk_reducing_actions is not None
        else _caller_is_risk_reducing_order_gate()
    )
    if effective_allow_risk_reducing and _policy_eligible_for_governed_risk_reduction(
        resolution,
        expected_stage=expected_stage,
        require_limits=require_limits,
    ):
        return resolution.policy
    if resolution.status == "missing" and resolution.source == "unresolved":
        return None
    if resolution.status == "missing" and resolution.source == "risk_policy_ref":
        raise RiskPolicyLoadError(f"{resolution.reason} ({resolution.reason_code})")
    raise RiskPolicyError(f"{resolution.reason} ({resolution.reason_code})")



# ---------------------------------------------------------------------------
# Risk-officer drafted paper-pool policy: Capital-owned validation + outer bounds
# ---------------------------------------------------------------------------
PROFILE_OUTER_BOUNDS_ENV = "PANTHEON_PAPER_RISK_PROFILE_BOUNDS"
# Override with PANTHEON_PAPER_RISK_PROFILE_BOUNDS='{"low": {"gross_limit": 2.0, "max_leverage": 2.0,
# "max_single_name_weight": 0.2, "min_cash_reserve_pct": 0.1}}'.  Mirrored in services/governance/paper_approval_scope.py.
DEFAULT_PROFILE_OUTER_BOUNDS: Mapping[str, Mapping[str, float]] = {
    "low": {
        # Operator decision 2026-11-07 (DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER Q3): gross and leverage <= 2.0x pool equity.
        "gross_limit": 2.0,
        "max_leverage": 2.0,
        # Operator decision 2026-11-23: single name <= 20% of NAV, cash reserve >= 10% of NAV.
        "max_single_name_weight": 0.20,
        "min_cash_reserve_pct": 0.10,
    },
}
PROFILE_OUTER_BOUND_KEYS = ("gross_limit", "max_leverage", "max_single_name_weight", "min_cash_reserve_pct")
_PROFILE_UPPER_BOUND_KEYS = ("gross_limit", "max_leverage", "max_single_name_weight")
DRAFT_REQUIRED_LIMITS = ("gross_limit", "net_limit", "max_leverage", "max_single_name_weight", "min_cash_reserve_pct")


def profile_outer_bounds(env: Mapping[str, str] | None = None) -> dict[str, dict[str, float]]:
    """Operator outer bounds per profile. A malformed override fails closed (raises)."""
    source = os.environ if env is None else env
    raw = str(source.get(PROFILE_OUTER_BOUNDS_ENV) or "").strip()
    loaded: Any = json.loads(raw) if raw else DEFAULT_PROFILE_OUTER_BOUNDS
    if not isinstance(loaded, Mapping):
        raise RiskPolicyError(f"{PROFILE_OUTER_BOUNDS_ENV} must be a JSON object")
    bounds: dict[str, dict[str, float]] = {}
    for profile, limits in loaded.items():
        if not isinstance(limits, Mapping) or set(limits) - set(PROFILE_OUTER_BOUND_KEYS):
            raise RiskPolicyError(f"{PROFILE_OUTER_BOUNDS_ENV}[{profile!r}] has unsupported bounds")
        bounds[str(profile).strip().lower()] = {k: float(v) for k, v in limits.items()}
    return bounds


def validate_drafted_pool_risk_policy(
    policy: Mapping[str, Any],
    *,
    profile: str,
    bounds: Mapping[str, Mapping[str, float]] | None = None,
) -> tuple[RiskPolicy | None, str | None]:
    """Validate a risk-officer drafted policy. Returns ``(model, None)`` or ``(None, reason)``.

    Requires gross/net/leverage, a single-name cap strictly inside (0, 1) and a positive cash reserve < 1, and
    enforces the configured operator outer bounds for ``profile`` as hard validation.
    """
    try:
        model = normalize_risk_policy(policy)
    except (RiskPolicyError, TypeError, ValueError) as exc:
        return None, f"risk_policy_draft_malformed: {exc}"
    for name in DRAFT_REQUIRED_LIMITS:
        val = getattr(model, name)
        if val is None or isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
            return None, f"risk_policy_draft_missing_or_non_numeric:{name}"
        if float(val) <= 0.0:
            return None, f"risk_policy_draft_limit_not_positive:{name}={val!r}"
    if not float(model.max_single_name_weight) < 1.0:
        return None, f"risk_policy_draft_single_name_not_below_1:max_single_name_weight={model.max_single_name_weight!r}"
    if not float(model.min_cash_reserve_pct) < 1.0:
        return None, f"risk_policy_draft_cash_reserve_not_below_1:min_cash_reserve_pct={model.min_cash_reserve_pct!r}"
    limits = (profile_outer_bounds() if bounds is None else bounds).get(str(profile or "").strip().lower())
    if limits:
        for name in _PROFILE_UPPER_BOUND_KEYS:
            cap = limits.get(name)
            if cap is not None and float(getattr(model, name)) > cap + 1e-12:
                return None, (f"risk_policy_draft_outside_bounds:{name}="
                              f"{getattr(model, name)!r} exceeds {profile} bound {cap!r}")
        reserve = limits.get("min_cash_reserve_pct")
        if reserve is not None and float(model.min_cash_reserve_pct) + 1e-12 < reserve:
            return None, (f"risk_policy_draft_outside_bounds:min_cash_reserve_pct="
                          f"{model.min_cash_reserve_pct!r} below {profile} bound {reserve!r}")
    return model, None

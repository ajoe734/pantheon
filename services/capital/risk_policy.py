"""First-class RiskPolicy evaluator contract.

The Capital Pool Plane owns risk policy definitions.  Other services should
consume this module as the shared evaluator contract instead of interpreting
``risk_policy_ref`` as metadata-only text.
"""
from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RiskPolicyError(ValueError):
    """Raised when a risk policy rejects a target."""

    def __init__(self, message: str, *, evaluation: "RiskPolicyEvaluation | None" = None) -> None:
        super().__init__(message)
        self.evaluation = evaluation


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


class RiskGuardrailAction(str, Enum):
    PAUSE_NEW_ORDERS = "pause_new_orders"
    REDUCE_EXPOSURE = "reduce_exposure"
    RISK_OFF = "risk_off"
    FROZEN = "frozen"


_ACTIVE_POLICY_STATUSES = {"active"}
_DRAW_DOWN_ACTIONS = ("liquidate", "risk_off", "warn")
_RESUME_HUMAN_REVIEW_ACTIONS = {
    RiskGuardrailAction.RISK_OFF.value,
    RiskGuardrailAction.FROZEN.value,
}


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

        risk_policy_id = _first_text(
            payload,
            "risk_policy_id",
            "policy_id",
            "risk_policy_ref",
            default="risk-policy-inline",
        )
        return cls(
            risk_policy_id=risk_policy_id,
            version=str(payload.get("version") or "v1"),
            name=_optional_text(payload.get("name")),
            status=str(payload.get("status") or "active"),
            gross_limit=_optional_float(payload.get("gross_limit")),
            net_limit=_optional_float(payload.get("net_limit")),
            max_single_name_weight=_optional_float(
                payload.get("max_single_name_weight", payload.get("max_single_weight"))
            ),
            max_sector_exposure=_normalize_limit_map(payload.get("max_sector_exposure")),
            max_factor_exposure=_normalize_limit_map(payload.get("max_factor_exposure")),
            max_leverage=_optional_float(payload.get("max_leverage")),
            turnover_limit=_optional_float(payload.get("turnover_limit")),
            liquidity_constraints=_mapping(payload.get("liquidity_constraints")),
            drawdown_actions=_numeric_mapping(payload.get("drawdown_actions")),
            pause_rules=_mapping(payload.get("pause_rules")),
            liquidation_rules=_mapping(payload.get("liquidation_rules")),
            allowed_order_types=_string_tuple(payload.get("allowed_order_types")),
            allowed_time_in_force=_string_tuple(payload.get("allowed_time_in_force")),
            allowed_asset_classes=_string_tuple(payload.get("allowed_asset_classes")),
            forbidden_asset_classes=_string_tuple(payload.get("forbidden_asset_classes")),
            allowed_strategy_families=_string_tuple(payload.get("allowed_strategy_families")),
            forbidden_strategy_families=_string_tuple(payload.get("forbidden_strategy_families")),
            max_strategy_family_concentration=_normalize_limit_map(
                payload.get("max_strategy_family_concentration")
            ),
            max_target_overlap=_optional_float(payload.get("max_target_overlap")),
            max_signal_correlation=_optional_float(
                payload.get("max_signal_correlation", payload.get("max_pairwise_correlation"))
            ),
            allowed_stages=_string_tuple(payload.get("allowed_stages")),
            max_canary_capital_scale_pct=_optional_float(payload.get("max_canary_capital_scale_pct")),
            max_canary_gross_scale_pct=_optional_float(payload.get("max_canary_gross_scale_pct")),
            kill_switch_triggers=_string_tuple(payload.get("kill_switch_triggers")),
            metadata=_mapping(payload.get("metadata")),
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
    asset_classes: tuple[str, ...] = ()
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
    metadata: Mapping[str, Any] = field(default_factory=dict)
    trace_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskPolicyEvaluationContext":
        metadata = _mapping(payload.get("metadata"))
        return cls(
            target_type=_first_text(payload, "target_type", default=RiskPolicyTargetType.RUNTIME_ACTION.value),
            target_id=_first_text(payload, "target_id", "proposal_id", "plan_id", "binding_id", "runtime_id"),
            capital_pool_id=_first_text(payload, "capital_pool_id"),
            stage=_first_text_or_none(payload, "stage", "target_stage", "deployment_mode", "scope_ref"),
            risk_policy_ref=_first_text_or_none(payload, "risk_policy_ref", "risk_policy_id"),
            target_weights=_numeric_mapping(payload.get("target_weights")),
            gross_exposure=_optional_float(payload.get("gross_exposure")),
            net_exposure=_optional_float(payload.get("net_exposure")),
            leverage=_optional_float(payload.get("leverage")),
            turnover=_optional_float(payload.get("turnover")),
            asset_classes=_string_tuple(payload.get("asset_classes", metadata.get("asset_classes"))),
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
            metadata=metadata,
            trace_id=_first_text_or_none(payload, "trace_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, (), {})}


@dataclass(frozen=True)
class RiskGuardrailContext:
    """Runtime, pool, persona, and telemetry facts for automatic guardrails."""

    persona_id: str
    runtime_binding_id: str
    runtime_id: str
    capital_pool_id: str
    deployment_stage: str
    deployment_plan_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    trace_id: str | None = None
    daily_loss_pct: float | None = None
    daily_loss_limit_pct: float | None = None
    exposure_pct: float | None = None
    exposure_limit_pct: float | None = None
    slippage_bps: float | None = None
    slippage_limit_bps: float | None = None
    order_reject_rate: float | None = None
    order_reject_rate_limit: float | None = None
    broker_error_count: float | None = None
    broker_error_limit: float | None = None
    data_freshness_pct: float | None = None
    min_data_freshness_pct: float | None = None
    runtime_heartbeat_age_seconds: float | None = None
    max_runtime_heartbeat_age_seconds: float | None = None
    policy_violation_severity: str | None = None
    correlation: float | None = None
    correlation_limit: float | None = None
    telemetry_event_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskGuardrailContext":
        binding = _mapping(payload.get("runtime_binding"))
        metadata = {
            **_mapping(binding.get("metadata")),
            **_mapping(payload.get("metadata")),
        }
        return cls(
            persona_id=_first_text(payload, "persona_id", default=str(metadata.get("persona_id") or "")),
            runtime_binding_id=_first_text(
                payload,
                "runtime_binding_id",
                "binding_id",
                default=str(binding.get("binding_id") or binding.get("runtime_binding_id") or ""),
            ),
            runtime_id=_first_text(payload, "runtime_id", default=str(binding.get("runtime_id") or "")),
            capital_pool_id=_first_text(payload, "capital_pool_id", default=str(binding.get("capital_pool_id") or "")),
            deployment_stage=_first_text(
                payload,
                "deployment_stage",
                "deployment_mode",
                "stage",
                default=str(binding.get("deployment_mode") or "paper"),
            ),
            deployment_plan_id=_first_text(
                payload,
                "deployment_plan_id",
                "plan_id",
                default=str(binding.get("plan_id") or ""),
            ),
            persona_capital_binding_id=_first_text(
                payload,
                "persona_capital_binding_id",
                default=str(binding.get("persona_capital_binding_id") or ""),
            ),
            artifact_id=_first_text(payload, "artifact_id", default=str(binding.get("artifact_id") or "")),
            artifact_version=_first_text(
                payload,
                "artifact_version",
                default=str(binding.get("artifact_version") or ""),
            ),
            trace_id=_first_text_or_none(payload, "trace_id") or _optional_text(metadata.get("trace_id")),
            daily_loss_pct=_optional_float(payload.get("daily_loss_pct", metadata.get("daily_loss_pct"))),
            daily_loss_limit_pct=_optional_float(
                payload.get("daily_loss_limit_pct", metadata.get("daily_loss_limit_pct"))
            ),
            exposure_pct=_optional_float(payload.get("exposure_pct", metadata.get("exposure_pct"))),
            exposure_limit_pct=_optional_float(payload.get("exposure_limit_pct", metadata.get("exposure_limit_pct"))),
            slippage_bps=_optional_float(payload.get("slippage_bps", metadata.get("slippage_bps"))),
            slippage_limit_bps=_optional_float(payload.get("slippage_limit_bps", metadata.get("slippage_limit_bps"))),
            order_reject_rate=_optional_float(payload.get("order_reject_rate", metadata.get("order_reject_rate"))),
            order_reject_rate_limit=_optional_float(
                payload.get("order_reject_rate_limit", metadata.get("order_reject_rate_limit"))
            ),
            broker_error_count=_optional_float(payload.get("broker_error_count", metadata.get("broker_error_count"))),
            broker_error_limit=_optional_float(payload.get("broker_error_limit", metadata.get("broker_error_limit"))),
            data_freshness_pct=_optional_float(payload.get("data_freshness_pct", metadata.get("data_freshness_pct"))),
            min_data_freshness_pct=_optional_float(
                payload.get("min_data_freshness_pct", metadata.get("min_data_freshness_pct"))
            ),
            runtime_heartbeat_age_seconds=_optional_float(
                payload.get("runtime_heartbeat_age_seconds", metadata.get("runtime_heartbeat_age_seconds"))
            ),
            max_runtime_heartbeat_age_seconds=_optional_float(
                payload.get("max_runtime_heartbeat_age_seconds", metadata.get("max_runtime_heartbeat_age_seconds"))
            ),
            policy_violation_severity=_first_text_or_none(payload, "policy_violation_severity")
            or _optional_text(metadata.get("policy_violation_severity")),
            correlation=_optional_float(payload.get("correlation", metadata.get("correlation"))),
            correlation_limit=_optional_float(payload.get("correlation_limit", metadata.get("correlation_limit"))),
            telemetry_event_ids=_string_tuple(payload.get("telemetry_event_ids", metadata.get("telemetry_event_ids"))),
            metadata=metadata,
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
        return {
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
        )


@dataclass(frozen=True)
class RiskGuardrailEvent:
    event_id: str
    persona_id: str
    runtime_binding_id: str
    capital_pool_id: str
    trigger_name: str
    observed_value: float | str
    threshold: float | str
    automatic_action: str
    effective_at: str
    incident_id: str
    review_required: bool
    may_promote: bool
    may_increase_allocation: bool
    trace_id: str
    resume_requires_human_review: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskGuardrailEvaluation:
    events: tuple[RiskGuardrailEvent, ...]
    incident_records: tuple[dict[str, Any], ...]
    evaluated_at: str
    trace_id: str

    @property
    def triggered(self) -> bool:
        return bool(self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "incident_records": list(self.incident_records),
            "evaluated_at": self.evaluated_at,
            "trace_id": self.trace_id,
        }


class RiskPolicyEvaluator:
    """Evaluate a RiskPolicy against an optimizer/deployment/runtime target."""

    def evaluate(
        self,
        policy: RiskPolicy | Mapping[str, Any],
        context: RiskPolicyEvaluationContext | Mapping[str, Any],
    ) -> RiskPolicyEvaluation:
        resolved_policy = policy if isinstance(policy, RiskPolicy) else RiskPolicy.from_mapping(policy)
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


class RiskGuardrailEvaluator:
    """Convert runtime/pool/persona telemetry breaches into protection events."""

    def evaluate(
        self,
        policy: RiskPolicy | Mapping[str, Any],
        context: RiskGuardrailContext | Mapping[str, Any],
    ) -> RiskGuardrailEvaluation:
        resolved_policy = policy if isinstance(policy, RiskPolicy) else RiskPolicy.from_mapping(policy)
        resolved_context = (
            context
            if isinstance(context, RiskGuardrailContext)
            else RiskGuardrailContext.from_mapping(context)
        )
        trace_id = resolved_context.trace_id or f"risk-guardrail-{uuid.uuid4().hex[:12]}"
        evaluated_at = utc_now()

        events: list[RiskGuardrailEvent] = []
        self._append_direct_telemetry_events(resolved_policy, resolved_context, events, trace_id, evaluated_at)
        self._append_policy_evaluation_events(resolved_policy, resolved_context, events, trace_id, evaluated_at)

        incident_records = tuple(_incident_record_from_guardrail(event, resolved_context) for event in events)
        return RiskGuardrailEvaluation(
            events=tuple(events),
            incident_records=incident_records,
            evaluated_at=evaluated_at,
            trace_id=trace_id,
        )

    def _append_direct_telemetry_events(
        self,
        policy: RiskPolicy,
        context: RiskGuardrailContext,
        events: list[RiskGuardrailEvent],
        trace_id: str,
        evaluated_at: str,
    ) -> None:
        daily_loss_limit = _threshold(
            context.daily_loss_limit_pct,
            policy.pause_rules.get("daily_loss_pct"),
            policy.pause_rules.get("daily_loss_limit_pct"),
        )
        if _breached_abs(context.daily_loss_pct, daily_loss_limit):
            events.append(
                _guardrail_event(
                    context,
                    trigger_name="daily_loss_pct",
                    observed_value=abs(float(context.daily_loss_pct or 0)),
                    threshold=daily_loss_limit,
                    automatic_action=RiskGuardrailAction.PAUSE_NEW_ORDERS.value,
                    trace_id=trace_id,
                    effective_at=evaluated_at,
                )
            )

        drawdown_limit = _threshold(
            None,
            policy.drawdown_actions.get("risk_off"),
            policy.drawdown_actions.get("liquidate"),
        )
        drawdown_pct = _optional_float(context.metadata.get("drawdown_pct"))
        if _breached_abs(drawdown_pct, drawdown_limit):
            events.append(
                _guardrail_event(
                    context,
                    trigger_name="max_drawdown_pct",
                    observed_value=abs(float(drawdown_pct or 0)),
                    threshold=drawdown_limit,
                    automatic_action=RiskGuardrailAction.RISK_OFF.value,
                    trace_id=trace_id,
                    effective_at=evaluated_at,
                )
            )

        if str(context.policy_violation_severity or "").lower() == "critical":
            events.append(
                _guardrail_event(
                    context,
                    trigger_name="critical_policy_violation",
                    observed_value="critical",
                    threshold="critical",
                    automatic_action=RiskGuardrailAction.FROZEN.value,
                    trace_id=trace_id,
                    effective_at=evaluated_at,
                )
            )

        direct_rules = [
            (
                "data_freshness_pct",
                context.data_freshness_pct,
                _threshold(context.min_data_freshness_pct, policy.pause_rules.get("min_data_freshness_pct")),
                RiskGuardrailAction.PAUSE_NEW_ORDERS.value,
                _breached_below,
            ),
            (
                "runtime_heartbeat_age_seconds",
                context.runtime_heartbeat_age_seconds,
                _threshold(
                    context.max_runtime_heartbeat_age_seconds,
                    policy.pause_rules.get("max_runtime_heartbeat_age_seconds"),
                ),
                RiskGuardrailAction.PAUSE_NEW_ORDERS.value,
                _breached_above,
            ),
            (
                "broker_error_count",
                context.broker_error_count,
                _threshold(context.broker_error_limit, policy.pause_rules.get("broker_error_count")),
                RiskGuardrailAction.PAUSE_NEW_ORDERS.value,
                _breached_above,
            ),
            (
                "order_reject_rate",
                context.order_reject_rate,
                _threshold(context.order_reject_rate_limit, policy.pause_rules.get("order_reject_rate")),
                RiskGuardrailAction.PAUSE_NEW_ORDERS.value,
                _breached_above,
            ),
            (
                "slippage_bps",
                context.slippage_bps,
                _threshold(
                    context.slippage_limit_bps,
                    policy.pause_rules.get("slippage_bps"),
                    policy.pause_rules.get("max_slippage_bps"),
                ),
                RiskGuardrailAction.PAUSE_NEW_ORDERS.value,
                _breached_above,
            ),
            (
                "exposure_pct",
                context.exposure_pct,
                _threshold(context.exposure_limit_pct, policy.pause_rules.get("exposure_pct")),
                RiskGuardrailAction.REDUCE_EXPOSURE.value,
                _breached_above,
            ),
            (
                "correlation",
                context.correlation,
                _threshold(context.correlation_limit, policy.max_signal_correlation),
                RiskGuardrailAction.REDUCE_EXPOSURE.value,
                _breached_above,
            ),
        ]
        for trigger_name, observed, threshold, action, predicate in direct_rules:
            if not predicate(observed, threshold):
                continue
            events.append(
                _guardrail_event(
                    context,
                    trigger_name=trigger_name,
                    observed_value=float(observed or 0),
                    threshold=float(threshold or 0),
                    automatic_action=action,
                    trace_id=trace_id,
                    effective_at=evaluated_at,
                )
            )

    def _append_policy_evaluation_events(
        self,
        policy: RiskPolicy,
        context: RiskGuardrailContext,
        events: list[RiskGuardrailEvent],
        trace_id: str,
        evaluated_at: str,
    ) -> None:
        policy_context = RiskPolicyEvaluationContext.from_mapping(
            {
                **dict(context.metadata),
                "target_type": RiskPolicyTargetType.RUNTIME_ACTION.value,
                "target_id": context.runtime_binding_id,
                "capital_pool_id": context.capital_pool_id,
                "stage": context.deployment_stage,
                "trace_id": trace_id,
            }
        )
        evaluation = RiskPolicyEvaluator().evaluate(policy, policy_context)
        existing = {(event.trigger_name, event.automatic_action) for event in events}
        for check in evaluation.checks:
            if check.status != RiskPolicyCheckStatus.FAILED.value:
                continue
            action = _action_for_failed_policy_check(check)
            if not action or (check.code, action) in existing:
                continue
            events.append(
                _guardrail_event(
                    context,
                    trigger_name=check.code,
                    observed_value=_event_value(check.observed),
                    threshold=_event_value(check.limit),
                    automatic_action=action,
                    trace_id=trace_id,
                    effective_at=evaluated_at,
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


def _normalize_limit_map(value: Any) -> Mapping[str, float] | float | None:
    scalar = _optional_float(value)
    if scalar is not None:
        return scalar
    mapped = _numeric_mapping(value)
    return mapped or None


def _limit_for_key(limit: Mapping[str, float] | float, key: str) -> float | None:
    if isinstance(limit, Mapping):
        return _optional_float(limit.get(key, limit.get("*")))
    return _optional_float(limit)


def _threshold(*values: Any) -> float | None:
    for value in values:
        numeric = _optional_float(value)
        if numeric is not None:
            return numeric
    return None


def _breached_abs(observed: Any, threshold: Any) -> bool:
    observed_value = _optional_float(observed)
    threshold_value = _optional_float(threshold)
    if observed_value is None or threshold_value is None:
        return False
    return abs(observed_value) >= threshold_value


def _breached_above(observed: Any, threshold: Any) -> bool:
    observed_value = _optional_float(observed)
    threshold_value = _optional_float(threshold)
    if observed_value is None or threshold_value is None:
        return False
    return observed_value >= threshold_value


def _breached_below(observed: Any, threshold: Any) -> bool:
    observed_value = _optional_float(observed)
    threshold_value = _optional_float(threshold)
    if observed_value is None or threshold_value is None:
        return False
    return observed_value <= threshold_value


def _guardrail_event(
    context: RiskGuardrailContext,
    *,
    trigger_name: str,
    observed_value: float | str,
    threshold: float | str | None,
    automatic_action: str,
    trace_id: str,
    effective_at: str,
) -> RiskGuardrailEvent:
    RiskGuardrailAction(automatic_action)
    event_id = f"rge-{uuid.uuid4().hex[:12]}"
    return RiskGuardrailEvent(
        event_id=event_id,
        persona_id=context.persona_id,
        runtime_binding_id=context.runtime_binding_id,
        capital_pool_id=context.capital_pool_id,
        trigger_name=trigger_name,
        observed_value=observed_value,
        threshold="" if threshold is None else threshold,
        automatic_action=automatic_action,
        effective_at=effective_at,
        incident_id=f"inc-{event_id}",
        review_required=True,
        may_promote=False,
        may_increase_allocation=False,
        trace_id=trace_id,
        resume_requires_human_review=automatic_action in _RESUME_HUMAN_REVIEW_ACTIONS,
    )


def _incident_record_from_guardrail(
    event: RiskGuardrailEvent,
    context: RiskGuardrailContext,
) -> dict[str, Any]:
    severity = "critical" if event.automatic_action == RiskGuardrailAction.FROZEN.value else "high"
    if event.automatic_action == RiskGuardrailAction.PAUSE_NEW_ORDERS.value:
        severity = "medium"
    review_note = " Resume requires human review." if event.resume_requires_human_review else ""
    return {
        "incident_id": event.incident_id,
        "title": f"Risk guardrail {event.automatic_action} for {context.persona_id or context.runtime_binding_id}",
        "status": "open",
        "severity": severity,
        "created_at": event.effective_at,
        "binding_id": context.runtime_binding_id,
        "deployment_stage": context.deployment_stage,
        "deployment_plan_id": context.deployment_plan_id,
        "capital_pool_id": context.capital_pool_id,
        "persona_capital_binding_id": context.persona_capital_binding_id,
        "artifact_id": context.artifact_id,
        "artifact_version": context.artifact_version,
        "runtime_id": context.runtime_id,
        "trace_id": event.trace_id,
        "telemetry_event_ids": list(context.telemetry_event_ids),
        "evidence_summary": (
            f"{event.trigger_name} observed {event.observed_value!r} against threshold "
            f"{event.threshold!r}; automatic_action={event.automatic_action}; "
            "review_required=true."
            f"{review_note}"
        ),
        "lineage_ref": f"{context.artifact_id}@{context.artifact_version}",
    }


def _action_for_failed_policy_check(check: RiskPolicyCheck) -> str | None:
    allowed_actions = {item.value for item in RiskGuardrailAction}
    if check.action in allowed_actions:
        return check.action
    if check.code in {"drawdown_risk_off", "drawdown_liquidate", "kill_switch_triggered"}:
        return RiskGuardrailAction.RISK_OFF.value
    if check.code in {
        "gross_exposure_limit_exceeded",
        "net_exposure_limit_exceeded",
        "leverage_limit_exceeded",
        "sector_exposure_limit_exceeded",
        "factor_exposure_limit_exceeded",
        "strategy_family_concentration_limit_exceeded",
        "target_overlap_limit_exceeded",
        "signal_correlation_limit_exceeded",
        "canary_capital_scale_pct_limit_exceeded",
        "canary_gross_scale_pct_limit_exceeded",
        "pool_risk_policy",
    }:
        return RiskGuardrailAction.REDUCE_EXPOSURE.value
    if check.code in {
        "risk_policy_not_active",
        "stage_not_allowed",
        "forbidden_asset_class",
        "asset_class_not_allowed",
        "forbidden_strategy_family",
        "strategy_family_not_allowed",
        "order_type_not_allowed",
        "time_in_force_not_allowed",
    }:
        return RiskGuardrailAction.FROZEN.value
    if check.code in {"liquidity_min_adv_breach", "liquidity_order_pct_adv_breach"}:
        return RiskGuardrailAction.PAUSE_NEW_ORDERS.value
    return None


def _event_value(value: Any) -> float | str:
    numeric = _optional_float(value)
    if numeric is not None:
        return numeric
    return str(value)

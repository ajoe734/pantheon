from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "bff_ha_cost_ceiling_monitor.v1"
SOURCE = "2026-05-19 supplement Group E; services/bff/ha/sla_targets.json#targets"
DEFAULT_SLA_TARGETS_PATH = Path(__file__).with_name("sla_targets.json")
DEFAULT_ENVIRONMENT = "production"
DEFAULT_WARNING_RATIO = Decimal("0.80")


class CostCeilingState(str, Enum):
    OK = "ok"
    WATCH = "watch"
    PROJECTED_BREACH = "projected_breach"
    BREACHED = "breached"
    COST_DATA_UNAVAILABLE = "cost_data_unavailable"


@dataclass(frozen=True)
class AlarmPath:
    event_type: str = "bff_ha.cost_ceiling"
    severity_channel: str = "telemetry.alerts"
    operator_surface: str = "/bff/alerts"
    escalation_target: str = "infra-owner+risk-owner"
    runbook: str = "docs/operations/bff_ha_failover_runbook.md"


@dataclass(frozen=True)
class AutoCapPolicy:
    mode: str
    automatic_cap_allowed: bool
    cap_applied_by_monitor: bool
    manual_gate_required: bool
    allowed_automatic_actions: tuple[str, ...]
    forbidden_automatic_actions: tuple[str, ...]
    operator_guidance: str


@dataclass(frozen=True)
class CostCeilingPolicy:
    environment: str
    monthly_ceiling_usd: Decimal
    warning_ratio: Decimal = DEFAULT_WARNING_RATIO
    schema_version: str = SCHEMA_VERSION
    source: str = SOURCE
    alarm_path: AlarmPath = AlarmPath()
    auto_cap_policy: AutoCapPolicy = AutoCapPolicy(
        mode="alarm_only_manual_gate",
        automatic_cap_allowed=False,
        cap_applied_by_monitor=False,
        manual_gate_required=True,
        allowed_automatic_actions=(
            "freeze_non_critical_ha_scale_out_requests",
            "open_cost_ceiling_incident",
            "page_infra_owner_and_risk_owner",
        ),
        forbidden_automatic_actions=(
            "reduce_production_bff_below_sla_replica_floor",
            "disable_idempotency_audit_or_telemetry_paths",
            "change_broker_capital_or_runtime_stage",
            "hide_degraded_state_or_fallback_to_fixtures",
        ),
        operator_guidance=(
            "Production cost ceiling breach is alarm-first. The monitor may "
            "freeze non-critical HA scale-out requests, but it must not "
            "automatically degrade production BFF availability or bypass "
            "fail-closed dependencies."
        ),
    )


@dataclass(frozen=True)
class CostCeilingObservation:
    environment: str
    billing_month: str
    month_to_date_usd: Decimal | None
    projected_month_end_usd: Decimal | None = None
    currency: str = "USD"
    cost_data_available: bool = True


@dataclass(frozen=True)
class CostCeilingDecision:
    schema_version: str
    environment: str
    billing_month: str
    state: CostCeilingState
    severity: str
    alarm_required: bool
    ceiling_usd: Decimal
    month_to_date_usd: Decimal | None
    projected_month_end_usd: Decimal | None
    observed_ratio: Decimal | None
    projected_ratio: Decimal | None
    remaining_budget_usd: Decimal | None
    alarm_path: AlarmPath
    auto_cap_policy: AutoCapPolicy
    reasons: tuple[str, ...]
    recommended_actions: tuple[str, ...]


def load_cost_ceiling_policy(
    environment: str = DEFAULT_ENVIRONMENT,
    *,
    sla_targets_path: Path = DEFAULT_SLA_TARGETS_PATH,
    warning_ratio: Decimal = DEFAULT_WARNING_RATIO,
) -> CostCeilingPolicy:
    payload = json.loads(sla_targets_path.read_text(encoding="utf-8"))
    targets = payload.get("targets")
    if not isinstance(targets, Mapping):
        raise ValueError("sla_targets.json must contain targets")

    target = targets.get(environment)
    if not isinstance(target, Mapping):
        raise ValueError(f"Unknown SLA target environment: {environment}")

    ceiling = _to_decimal(target.get("monthly_cost_ceiling_usd"), "monthly_cost_ceiling_usd")
    if ceiling <= 0:
        raise ValueError("monthly_cost_ceiling_usd must be positive")
    if warning_ratio <= 0 or warning_ratio >= 1:
        raise ValueError("warning_ratio must be between 0 and 1")

    return CostCeilingPolicy(
        environment=environment,
        monthly_ceiling_usd=ceiling,
        warning_ratio=warning_ratio,
        source=f"{payload.get('source', SOURCE)}#targets.{environment}.monthly_cost_ceiling_usd",
    )


def evaluate_cost_ceiling(
    observation: CostCeilingObservation | Mapping[str, Any],
    policy: CostCeilingPolicy | None = None,
) -> CostCeilingDecision:
    normalized = normalize_observation(observation)
    active_policy = policy or load_cost_ceiling_policy(normalized.environment)
    if normalized.environment != active_policy.environment:
        raise ValueError(
            "observation environment must match policy environment: "
            f"{normalized.environment!r} != {active_policy.environment!r}"
        )

    _validate_billing_month(normalized.billing_month)
    if normalized.currency != "USD":
        raise ValueError("cost ceiling monitor only accepts USD observations")

    if not normalized.cost_data_available:
        return _decision(
            normalized,
            active_policy,
            state=CostCeilingState.COST_DATA_UNAVAILABLE,
            severity="critical",
            alarm_required=True,
            observed_ratio=None,
            projected_ratio=None,
            remaining_budget_usd=None,
            reasons=("Billing cost telemetry is unavailable; ceiling compliance cannot be proven.",),
            recommended_actions=(
                "emit_cost_ceiling_alarm",
                "open_cost_ceiling_incident",
                "freeze_non_critical_ha_scale_out_requests",
            ),
        )

    if normalized.month_to_date_usd is None:
        raise ValueError("month_to_date_usd is required when cost_data_available is true")

    month_to_date = _non_negative_money(normalized.month_to_date_usd, "month_to_date_usd")
    projected = (
        _non_negative_money(normalized.projected_month_end_usd, "projected_month_end_usd")
        if normalized.projected_month_end_usd is not None
        else None
    )
    ceiling = active_policy.monthly_ceiling_usd
    observed_ratio = _ratio(month_to_date, ceiling)
    projected_ratio = _ratio(projected, ceiling) if projected is not None else None
    remaining = _money(max(ceiling - month_to_date, Decimal("0")))

    effective_ratio = max(observed_ratio, projected_ratio or observed_ratio)
    if observed_ratio >= 1:
        return _decision(
            normalized,
            active_policy,
            state=CostCeilingState.BREACHED,
            severity="critical",
            alarm_required=True,
            observed_ratio=observed_ratio,
            projected_ratio=projected_ratio,
            remaining_budget_usd=remaining,
            reasons=(f"Month-to-date cost meets or exceeds the {ceiling} USD production ceiling.",),
            recommended_actions=(
                "emit_cost_ceiling_alarm",
                "open_cost_ceiling_incident",
                "freeze_non_critical_ha_scale_out_requests",
                "request_manual_cost_cap_decision",
            ),
        )

    if projected_ratio is not None and projected_ratio >= 1:
        return _decision(
            normalized,
            active_policy,
            state=CostCeilingState.PROJECTED_BREACH,
            severity="warning",
            alarm_required=True,
            observed_ratio=observed_ratio,
            projected_ratio=projected_ratio,
            remaining_budget_usd=remaining,
            reasons=(f"Projected month-end cost meets or exceeds the {ceiling} USD production ceiling.",),
            recommended_actions=(
                "emit_cost_ceiling_alarm",
                "freeze_non_critical_ha_scale_out_requests",
                "request_manual_cost_cap_decision",
            ),
        )

    if effective_ratio >= active_policy.warning_ratio:
        return _decision(
            normalized,
            active_policy,
            state=CostCeilingState.WATCH,
            severity="warning",
            alarm_required=True,
            observed_ratio=observed_ratio,
            projected_ratio=projected_ratio,
            remaining_budget_usd=remaining,
            reasons=(f"Cost is at or above {active_policy.warning_ratio:.0%} of the monthly ceiling.",),
            recommended_actions=(
                "emit_cost_ceiling_warning",
                "review_non_critical_ha_scale_out_requests",
            ),
        )

    return _decision(
        normalized,
        active_policy,
        state=CostCeilingState.OK,
        severity="info",
        alarm_required=False,
        observed_ratio=observed_ratio,
        projected_ratio=projected_ratio,
        remaining_budget_usd=remaining,
        reasons=("Cost remains below the warning threshold.",),
        recommended_actions=("continue_monitoring",),
    )


def normalize_observation(observation: CostCeilingObservation | Mapping[str, Any]) -> CostCeilingObservation:
    if isinstance(observation, CostCeilingObservation):
        return observation

    environment = str(observation.get("environment", DEFAULT_ENVIRONMENT))
    billing_month = str(observation.get("billing_month", ""))
    currency = str(observation.get("currency", "USD")).upper()
    cost_data_available = bool(observation.get("cost_data_available", True))
    month_to_date = observation.get("month_to_date_usd")
    projected = observation.get("projected_month_end_usd")

    return CostCeilingObservation(
        environment=environment,
        billing_month=billing_month,
        month_to_date_usd=_to_decimal(month_to_date, "month_to_date_usd") if month_to_date is not None else None,
        projected_month_end_usd=_to_decimal(projected, "projected_month_end_usd") if projected is not None else None,
        currency=currency,
        cost_data_available=cost_data_available,
    )


def decision_to_dict(decision: CostCeilingDecision) -> dict[str, Any]:
    return {
        "schema_version": decision.schema_version,
        "environment": decision.environment,
        "billing_month": decision.billing_month,
        "state": decision.state.value,
        "severity": decision.severity,
        "alarm_required": decision.alarm_required,
        "ceiling_usd": _decimal_to_float(decision.ceiling_usd),
        "month_to_date_usd": _decimal_to_float(decision.month_to_date_usd),
        "projected_month_end_usd": _decimal_to_float(decision.projected_month_end_usd),
        "observed_ratio": _decimal_to_float(decision.observed_ratio),
        "projected_ratio": _decimal_to_float(decision.projected_ratio),
        "remaining_budget_usd": _decimal_to_float(decision.remaining_budget_usd),
        "alarm_path": {
            "event_type": decision.alarm_path.event_type,
            "severity_channel": decision.alarm_path.severity_channel,
            "operator_surface": decision.alarm_path.operator_surface,
            "escalation_target": decision.alarm_path.escalation_target,
            "runbook": decision.alarm_path.runbook,
        },
        "auto_cap": {
            "mode": decision.auto_cap_policy.mode,
            "automatic_cap_allowed": decision.auto_cap_policy.automatic_cap_allowed,
            "cap_applied_by_monitor": decision.auto_cap_policy.cap_applied_by_monitor,
            "manual_gate_required": decision.auto_cap_policy.manual_gate_required,
            "allowed_automatic_actions": list(decision.auto_cap_policy.allowed_automatic_actions),
            "forbidden_automatic_actions": list(decision.auto_cap_policy.forbidden_automatic_actions),
            "operator_guidance": decision.auto_cap_policy.operator_guidance,
        },
        "reasons": list(decision.reasons),
        "recommended_actions": list(decision.recommended_actions),
    }


def monitor_contract_as_dict(policy: CostCeilingPolicy | None = None) -> dict[str, Any]:
    active_policy = policy or load_cost_ceiling_policy(DEFAULT_ENVIRONMENT)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": active_policy.source,
        "environment": active_policy.environment,
        "monthly_cost_ceiling_usd": _decimal_to_float(active_policy.monthly_ceiling_usd),
        "warning_ratio": _decimal_to_float(active_policy.warning_ratio),
        "alarm_path": {
            "event_type": active_policy.alarm_path.event_type,
            "severity_channel": active_policy.alarm_path.severity_channel,
            "operator_surface": active_policy.alarm_path.operator_surface,
            "escalation_target": active_policy.alarm_path.escalation_target,
            "runbook": active_policy.alarm_path.runbook,
        },
        "auto_cap": {
            "mode": active_policy.auto_cap_policy.mode,
            "automatic_cap_allowed": active_policy.auto_cap_policy.automatic_cap_allowed,
            "cap_applied_by_monitor": active_policy.auto_cap_policy.cap_applied_by_monitor,
            "manual_gate_required": active_policy.auto_cap_policy.manual_gate_required,
            "allowed_automatic_actions": list(active_policy.auto_cap_policy.allowed_automatic_actions),
            "forbidden_automatic_actions": list(active_policy.auto_cap_policy.forbidden_automatic_actions),
            "operator_guidance": active_policy.auto_cap_policy.operator_guidance,
        },
    }


def _decision(
    observation: CostCeilingObservation,
    policy: CostCeilingPolicy,
    *,
    state: CostCeilingState,
    severity: str,
    alarm_required: bool,
    observed_ratio: Decimal | None,
    projected_ratio: Decimal | None,
    remaining_budget_usd: Decimal | None,
    reasons: tuple[str, ...],
    recommended_actions: tuple[str, ...],
) -> CostCeilingDecision:
    return CostCeilingDecision(
        schema_version=SCHEMA_VERSION,
        environment=policy.environment,
        billing_month=observation.billing_month,
        state=state,
        severity=severity,
        alarm_required=alarm_required,
        ceiling_usd=policy.monthly_ceiling_usd,
        month_to_date_usd=observation.month_to_date_usd,
        projected_month_end_usd=observation.projected_month_end_usd,
        observed_ratio=observed_ratio,
        projected_ratio=projected_ratio,
        remaining_budget_usd=remaining_budget_usd,
        alarm_path=policy.alarm_path,
        auto_cap_policy=policy.auto_cap_policy,
        reasons=reasons,
        recommended_actions=recommended_actions,
    )


def _validate_billing_month(value: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("billing_month must use YYYY-MM format")
    month = int(value[5:])
    if month < 1 or month > 12:
        raise ValueError("billing_month month must be between 01 and 12")


def _non_negative_money(value: Decimal, field_name: str) -> Decimal:
    money = _money(value)
    if money < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return money


def _to_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_name} must be numeric") from None
    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return _money(decimal_value)


def _ratio(value: Decimal | None, ceiling: Decimal) -> Decimal:
    if value is None:
        return Decimal("0.0000")
    return (value / ceiling).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)

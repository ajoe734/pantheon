"""First-week observation report builder for broker production-live activation.

Implements the 2026-05-19 blueprint supplement Part B5 first-week
observation shape. The builder packages operator evidence into a deterministic
machine-readable report only; it never records approvals, mutates runtime
state, or enables broker live flags.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .validator import (
    EXPECTED_COOLDOWN_HOURS,
    EXPECTED_HARD_FAIL_CONDITIONS,
    load_default_criteria,
    validate_criteria_shape,
)


REPORT_VERSION = "1.0"
REPORT_SOURCE = "2026-05-19 blueprint supplement Part B5"
READY = "ready"
BLOCKED = "blocked"
DECISION_CONTINUE = "continue_live"
DECISION_ESCALATE = "escalate_to_risk_owner"
DECISION_FREEZE = "freeze_or_safe_mode"
DECISION_HOLD = "hold_live_change"
REQUIRED_OBSERVATION_DAYS = 7

EXPECTED_FIRST_WEEK_OBSERVATION_CHECKS = (
    "Live activation identity is traceable.",
    "First-week observation window covers seven days.",
    "Daily operator check-ins are complete for days 1-7.",
    "Runtime binding and broker session stayed healthy.",
    "Telemetry stream stayed available with required snapshots.",
    "Audit retention and replay path stayed available.",
    "PnL and drawdown stayed within risk thresholds.",
    "Exposure and liquidity stayed within policy thresholds.",
    "Slippage, fills, and order rejections stayed within thresholds.",
    "Short-term drift is clear or cooldown governance is satisfied.",
    "Kill-switch and safe-mode paths remain reachable.",
    "Rollback/frozen transition path remains ready.",
    "No-go, incident, and hard-fail conditions are clear.",
)


class FirstWeekObservationReportError(ValueError):
    """Raised when a first-week observation packet fails report checks."""


@dataclass(frozen=True)
class FirstWeekObservationCheck:
    id: str
    order: int
    text: str
    status: str
    evidence_refs: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    required: bool = True
    source: str = REPORT_SOURCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order": self.order,
            "text": self.text,
            "status": self.status,
            "required": self.required,
            "evidence_refs": list(self.evidence_refs),
            "blocking_reasons": list(self.blocking_reasons),
            "source": self.source,
        }


@dataclass(frozen=True)
class FirstWeekObservationReport:
    version: str
    source: str
    report_id: str
    decision: str
    can_continue_live: bool
    escalation_required: bool
    observation_window_days: float | None
    live_started_at: str | None
    observed_through_at: str | None
    checks: tuple[FirstWeekObservationCheck, ...]
    blocking_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "report_id": self.report_id,
            "decision": self.decision,
            "can_continue_live": self.can_continue_live,
            "passed": self.can_continue_live,
            "escalation_required": self.escalation_required,
            "observation_window_days": self.observation_window_days,
            "live_started_at": self.live_started_at,
            "observed_through_at": self.observed_through_at,
            "blocking_reasons": list(self.blocking_reasons),
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class _ObservationContext:
    packet: Mapping[str, Any]
    evidence: Mapping[str, Any]
    window: Mapping[str, Any]
    telemetry: Mapping[str, Any]
    risk: Mapping[str, Any]
    broker: Mapping[str, Any]
    runtime: Mapping[str, Any]
    governance: Mapping[str, Any]
    operations: Mapping[str, Any]

    @property
    def sources(self) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        return (
            ("window", self.window),
            ("telemetry", self.telemetry),
            ("risk", self.risk),
            ("broker", self.broker),
            ("runtime", self.runtime),
            ("governance", self.governance),
            ("operations", self.operations),
            ("evidence", self.evidence),
            ("$", self.packet),
        )


@dataclass(frozen=True)
class _CheckResult:
    evidence_refs: tuple[str, ...]
    blocking_reasons: tuple[str, ...]


def build_first_week_observation_report(
    packet: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> FirstWeekObservationReport:
    """Build a fail-closed Part B5 first-week observation report."""

    criteria_result = validate_criteria_shape(criteria)
    if not criteria_result.passed:
        reasons = _unique(criteria_result.blocking_reasons)
        return _blocked_report(packet, reasons)

    if not isinstance(packet, Mapping):
        return _blocked_report({}, ("observation packet must be an object",))

    criteria_payload = load_default_criteria() if criteria is None else dict(criteria)
    context = _context_for(packet)
    checks = tuple(
        _check(index=index, result=result)
        for index, result in enumerate(
            (
                _check_live_activation_identity(context),
                _check_observation_window(context),
                _check_daily_checkins(context),
                _check_runtime_broker_health(context),
                _check_telemetry_stream(context),
                _check_audit_retention(context),
                _check_pnl_drawdown_thresholds(context),
                _check_exposure_liquidity_thresholds(context),
                _check_execution_quality_thresholds(context),
                _check_drift_and_cooldown(context, criteria_payload),
                _check_kill_switch_safe_mode(context),
                _check_rollback_transition(context),
                _check_no_go_incident_hard_fail(context, criteria_payload),
            ),
            start=1,
        )
    )
    blocking_reasons = _unique(
        tuple(reason for check in checks for reason in check.blocking_reasons)
    )
    decision = _decision_for(context, criteria_payload, blocking_reasons)
    can_continue_live = not blocking_reasons and decision == DECISION_CONTINUE
    live_started_at = _first_string(context, ("live_started_at", "window_started_at", "started_at"))
    observed_through_at = _first_string(context, ("observed_through_at", "window_ended_at", "ended_at"))
    observation_window_days = _observation_window_days(context)
    return FirstWeekObservationReport(
        version=REPORT_VERSION,
        source=REPORT_SOURCE,
        report_id=_report_id(packet, checks, decision),
        decision=decision,
        can_continue_live=can_continue_live,
        escalation_required=not can_continue_live,
        observation_window_days=observation_window_days,
        live_started_at=live_started_at,
        observed_through_at=observed_through_at,
        checks=checks,
        blocking_reasons=blocking_reasons,
    )


def build_first_week_observation_report_or_raise(
    packet: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> FirstWeekObservationReport:
    """Build a first-week report and raise a compact fail-closed error."""

    report = build_first_week_observation_report(packet, criteria)
    if not report.can_continue_live:
        raise FirstWeekObservationReportError("; ".join(report.blocking_reasons))
    return report


def _blocked_report(
    packet: Mapping[str, Any],
    blocking_reasons: Sequence[str],
) -> FirstWeekObservationReport:
    reasons = _unique(blocking_reasons)
    checks = tuple(
        FirstWeekObservationCheck(
            id=_check_id(index),
            order=index,
            text=text,
            status=BLOCKED,
            blocking_reasons=reasons,
        )
        for index, text in enumerate(EXPECTED_FIRST_WEEK_OBSERVATION_CHECKS, start=1)
    )
    return FirstWeekObservationReport(
        version=REPORT_VERSION,
        source=REPORT_SOURCE,
        report_id=_report_id(packet, checks, DECISION_HOLD),
        decision=DECISION_HOLD,
        can_continue_live=False,
        escalation_required=True,
        observation_window_days=None,
        live_started_at=None,
        observed_through_at=None,
        checks=checks,
        blocking_reasons=reasons,
    )


def _context_for(packet: Mapping[str, Any]) -> _ObservationContext:
    evidence = _mapping(packet.get("evidence")) or packet
    return _ObservationContext(
        packet=packet,
        evidence=evidence,
        window=_mapping(packet.get("observation_window") or packet.get("window")),
        telemetry=_mapping(packet.get("telemetry")),
        risk=_mapping(packet.get("risk") or packet.get("risk_metrics")),
        broker=_mapping(packet.get("broker") or packet.get("broker_session")),
        runtime=_mapping(packet.get("runtime") or packet.get("runtime_binding")),
        governance=_mapping(packet.get("governance") or packet.get("decision_governance")),
        operations=_mapping(packet.get("operations") or packet.get("ops")),
    )


def _check(index: int, result: _CheckResult) -> FirstWeekObservationCheck:
    return FirstWeekObservationCheck(
        id=_check_id(index),
        order=index,
        text=EXPECTED_FIRST_WEEK_OBSERVATION_CHECKS[index - 1],
        status=READY if not result.blocking_reasons else BLOCKED,
        evidence_refs=result.evidence_refs,
        blocking_reasons=result.blocking_reasons,
    )


def _check_id(index: int) -> str:
    return f"first_week_b5_{index:02d}"


def _check_live_activation_identity(context: _ObservationContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    for keys, message in (
        (
            ("live_activation_ref", "activation_ref", "activation_id"),
            "live activation evidence is required",
        ),
        (
            ("deployment_plan_ref", "deployment_plan_id", "live_deployment_plan_ref"),
            "DeploymentPlan evidence link is required",
        ),
        (
            ("runtime_binding_id", "runtime_binding_ref", "live_runtime_binding_ref"),
            "RuntimeBinding evidence link is required",
        ),
        (
            ("capital_pool_id", "capital_pool_ref", "capital_binding_ref"),
            "capital pool / binding evidence is required",
        ),
    ):
        ref = _first_present_ref(context, keys)
        if ref:
            evidence_refs.append(ref)
        else:
            blocking_reasons.append(message)
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_observation_window(context: _ObservationContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    start = _first_string(context, ("live_started_at", "window_started_at", "started_at"))
    end = _first_string(context, ("observed_through_at", "window_ended_at", "ended_at"))
    start_path = _first_present_ref(context, ("live_started_at", "window_started_at", "started_at"))
    end_path = _first_present_ref(context, ("observed_through_at", "window_ended_at", "ended_at"))
    if start_path:
        evidence_refs.append(start_path)
    else:
        blocking_reasons.append("live_started_at is required")
    if end_path:
        evidence_refs.append(end_path)
    else:
        blocking_reasons.append("observed_through_at is required")

    days = _observation_window_days(context)
    if days is None:
        if start and end:
            blocking_reasons.append("observation timestamps must be valid ISO-8601 values")
        else:
            blocking_reasons.append("observation window days are required")
    elif days < REQUIRED_OBSERVATION_DAYS:
        blocking_reasons.append(
            f"observation window must cover at least {REQUIRED_OBSERVATION_DAYS} days"
        )

    report_ref = _first_present_ref(
        context,
        (
            "first_week_observation_window_ref",
            "first_week_observation_ref",
            "observation_window_ref",
        ),
    )
    if report_ref:
        evidence_refs.append(report_ref)
    else:
        blocking_reasons.append("first-week observation evidence reference is required")
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_daily_checkins(context: _ObservationContext) -> _CheckResult:
    checkins = _daily_checkins(context)
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    by_day: dict[int, Mapping[str, Any]] = {}
    for index, checkin in enumerate(checkins):
        day = _int_or_none(checkin.get("day") or checkin.get("day_index") or checkin.get("window_day"))
        if day is not None:
            by_day[day] = checkin
        if _present(checkin.get("evidence_ref") or checkin.get("report_ref")):
            evidence_refs.append(f"daily_checkins[{index}]")

    for day in range(1, REQUIRED_OBSERVATION_DAYS + 1):
        checkin = by_day.get(day)
        if checkin is None:
            blocking_reasons.append(f"daily check-in day {day} is required")
            continue
        if not _present(checkin.get("evidence_ref") or checkin.get("report_ref")):
            blocking_reasons.append(f"daily check-in day {day} evidence reference is required")
        status = _normalized(checkin.get("status") or checkin.get("state"))
        recorded = checkin.get("recorded") is True or checkin.get("complete") is True
        if status not in {"approved", "complete", "completed", "ok", "passed", "ready", "recorded"} and not recorded:
            blocking_reasons.append(f"daily check-in day {day} must be complete")

    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_runtime_broker_health(context: _ObservationContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    runtime_ref = _first_present_ref(
        context,
        ("runtime_health_ref", "runtime_binding_ref", "runtime_binding_id"),
    )
    broker_ref = _first_present_ref(
        context,
        ("broker_session_ref", "broker_live_session_ref", "broker_health_ref"),
    )
    if runtime_ref:
        evidence_refs.append(runtime_ref)
    else:
        blocking_reasons.append("runtime health evidence is required")
    if broker_ref:
        evidence_refs.append(broker_ref)
    else:
        blocking_reasons.append("broker session health evidence is required")

    for keys, message in (
        (
            ("runtime_healthy", "runtime_binding_healthy", "runtime_health_ok"),
            "runtime binding must be healthy",
        ),
        (
            ("broker_session_healthy", "broker_session_ready", "broker_health_ok"),
            "broker session must be healthy",
        ),
    ):
        result = _boolean_or_status_ready(context, keys, message)
        evidence_refs.extend(result.evidence_refs)
        blocking_reasons.extend(result.blocking_reasons)
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_telemetry_stream(context: _ObservationContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    telemetry_ready = _flag_or_ref(
        context,
        flag_keys=("telemetry_path_available", "telemetry_readiness_available"),
        ref_keys=("telemetry_readiness_ref", "telemetry_path_ref", "telemetry_report_ref"),
        missing_message="telemetry path evidence is required",
    )
    evidence_refs.extend(telemetry_ready.evidence_refs)
    blocking_reasons.extend(telemetry_ready.blocking_reasons)
    evidence_refs.extend(
        _minimum_count_refs(
            context,
            keys=("runtime_heartbeat_count", "heartbeat_count"),
            minimum=REQUIRED_OBSERVATION_DAYS,
            missing_message="runtime heartbeat snapshots are required",
            low_message=f"runtime heartbeat count must be >= {REQUIRED_OBSERVATION_DAYS}",
        )
    )
    blocking_reasons.extend(
        _minimum_count_blockers(
            context,
            keys=("runtime_heartbeat_count", "heartbeat_count"),
            minimum=REQUIRED_OBSERVATION_DAYS,
            missing_message="runtime heartbeat snapshots are required",
            low_message=f"runtime heartbeat count must be >= {REQUIRED_OBSERVATION_DAYS}",
        )
    )
    evidence_refs.extend(
        _minimum_count_refs(
            context,
            keys=("pnl_snapshot_count", "pnl_snapshots_recorded"),
            minimum=REQUIRED_OBSERVATION_DAYS,
            missing_message="PnL snapshots are required",
            low_message=f"PnL snapshot count must be >= {REQUIRED_OBSERVATION_DAYS}",
        )
    )
    blocking_reasons.extend(
        _minimum_count_blockers(
            context,
            keys=("pnl_snapshot_count", "pnl_snapshots_recorded"),
            minimum=REQUIRED_OBSERVATION_DAYS,
            missing_message="PnL snapshots are required",
            low_message=f"PnL snapshot count must be >= {REQUIRED_OBSERVATION_DAYS}",
        )
    )
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_audit_retention(context: _ObservationContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    for result in (
        _flag_or_ref(
            context,
            flag_keys=("audit_path_available", "audit_retention_ready"),
            ref_keys=("audit_retention_ref", "audit_retention_ready_ref", "audit_path_ref"),
            missing_message="audit retention evidence is required",
        ),
        _flag_or_ref(
            context,
            flag_keys=("audit_replay_available", "audit_replay_ready"),
            ref_keys=("audit_replay_ref", "audit_replay_evidence_ref"),
            missing_message="audit replay evidence is required",
        ),
    ):
        evidence_refs.extend(result.evidence_refs)
        blocking_reasons.extend(result.blocking_reasons)
    evidence_refs.extend(
        _minimum_count_refs(
            context,
            keys=("audit_event_count", "audit_events_recorded"),
            minimum=1,
            missing_message="audit events must be recorded",
            low_message="audit event count must be >= 1",
        )
    )
    blocking_reasons.extend(
        _minimum_count_blockers(
            context,
            keys=("audit_event_count", "audit_events_recorded"),
            minimum=1,
            missing_message="audit events must be recorded",
            low_message="audit event count must be >= 1",
        )
    )
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_pnl_drawdown_thresholds(context: _ObservationContext) -> _CheckResult:
    return _all_flags_ready(
        context,
        (
            (
                ("pnl_within_threshold", "pnl_within_policy"),
                "PnL must stay within threshold",
            ),
            (
                ("drawdown_within_threshold", "drawdown_within_policy"),
                "drawdown must stay within threshold",
            ),
        ),
        optional_refs=("pnl_drawdown_ref", "risk_thresholds_ref", "risk_metrics_ref"),
    )


def _check_exposure_liquidity_thresholds(context: _ObservationContext) -> _CheckResult:
    return _all_flags_ready(
        context,
        (
            (
                ("exposure_within_threshold", "exposure_within_policy"),
                "exposure must stay within threshold",
            ),
            (
                ("liquidity_within_threshold", "liquidity_within_policy"),
                "liquidity must stay within threshold",
            ),
        ),
        optional_refs=("exposure_liquidity_ref", "risk_thresholds_ref", "risk_metrics_ref"),
    )


def _check_execution_quality_thresholds(context: _ObservationContext) -> _CheckResult:
    return _all_flags_ready(
        context,
        (
            (
                ("slippage_within_threshold", "slippage_within_policy"),
                "slippage must stay within threshold",
            ),
            (
                ("fill_rate_within_threshold", "fills_within_threshold"),
                "fills must stay within threshold",
            ),
            (
                ("order_rejections_within_threshold", "order_rejection_within_threshold"),
                "order rejections must stay within threshold",
            ),
        ),
        optional_refs=("execution_quality_ref", "slippage_fill_ref", "order_rejection_ref"),
    )


def _check_drift_and_cooldown(
    context: _ObservationContext,
    criteria: Mapping[str, Any],
) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    drift_ref = _first_present_ref(
        context,
        ("drift_report_ref", "alpha_drift_ref", "short_term_drift_ref"),
    )
    if drift_ref:
        evidence_refs.append(drift_ref)

    alpha_ready = _first_bool(context, ("alpha_drift_within_threshold", "short_term_drift_clear"))
    drift_detected = (
        _first_bool(context, ("short_term_drift_detected", "alpha_drift_detected")) is True
    )
    if alpha_ready is True and not drift_detected:
        alpha_path = _first_key_path(context, ("alpha_drift_within_threshold", "short_term_drift_clear"))
        if alpha_path:
            evidence_refs.append(alpha_path)
        return _CheckResult(tuple(evidence_refs), ())

    minimum_hours = _cooldown_hours(criteria)
    hours_since_drift = _first_number(
        context,
        (
            "hours_since_short_term_drift",
            "hours_since_last_short_term_drift",
            "cooldown_hours_since_drift",
        ),
    )
    if drift_detected:
        drift_path = _first_key_path(context, ("short_term_drift_detected", "alpha_drift_detected"))
        if drift_path:
            evidence_refs.append(drift_path)
        governance_ref = _first_present_ref(
            context,
            ("drift_governance_ref", "cooldown_governance_ref", "evolution_decision_ref"),
        )
        if governance_ref:
            evidence_refs.append(governance_ref)
        else:
            blocking_reasons.append("drift governance evidence is required")
        if hours_since_drift is None or hours_since_drift < minimum_hours:
            blocking_reasons.append(
                f"live change requires at least {int(minimum_hours)} hours after short-term drift"
            )
    elif alpha_ready is None:
        blocking_reasons.append("short-term drift status is required")
    else:
        blocking_reasons.append("short-term drift must be clear")
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_kill_switch_safe_mode(context: _ObservationContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    for result in (
        _flag_or_ref(
            context,
            flag_keys=("kill_switch_available", "kill_switch_path_ready"),
            ref_keys=("kill_switch_demo_ref", "kill_switch_path_ref"),
            missing_message="kill-switch path evidence is required",
        ),
        _flag_or_ref(
            context,
            flag_keys=("safe_mode_ready", "safe_mode_path_ready"),
            ref_keys=("safe_mode_ref", "safe_mode_runbook_ref", "safe_mode_drill_ref"),
            missing_message="safe-mode readiness evidence is required",
        ),
    ):
        evidence_refs.extend(result.evidence_refs)
        blocking_reasons.extend(result.blocking_reasons)
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_rollback_transition(context: _ObservationContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    for result in (
        _flag_or_ref(
            context,
            flag_keys=("rollback_target_available", "rollback_target_exists"),
            ref_keys=("rollback_target_ref", "rollback_target_id"),
            missing_message="rollback target evidence is required",
        ),
        _flag_or_ref(
            context,
            flag_keys=("frozen_transition_ready", "retired_transition_ready"),
            ref_keys=("rollback_drill_ref", "frozen_transition_ref", "retired_transition_ref"),
            missing_message="rollback/frozen transition evidence is required",
        ),
    ):
        evidence_refs.extend(result.evidence_refs)
        blocking_reasons.extend(result.blocking_reasons)
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_no_go_incident_hard_fail(
    context: _ObservationContext,
    criteria: Mapping[str, Any],
) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    no_go = _flag_or_ref(
        context,
        flag_keys=("no_go_conditions_acknowledged", "operator_no_go_conditions_explicit"),
        ref_keys=("no_go_conditions_ref", "operator_no_go_ref"),
        missing_message="operator no-go conditions evidence is required",
    )
    evidence_refs.extend(no_go.evidence_refs)
    blocking_reasons.extend(no_go.blocking_reasons)

    for keys, message in (
        (
            ("open_incidents", "active_incidents", "active_incident_count"),
            "first-week observation must have no active incidents",
        ),
        (
            ("open_operator_blockers", "operator_open_blockers", "open_no_go_conditions"),
            "operator no-go conditions must have no open blockers",
        ),
    ):
        value, path = _first_key(context, keys)
        open_count = _open_count(value)
        if open_count == 0:
            if path:
                evidence_refs.append(path)
        elif open_count is None:
            blocking_reasons.append(message)
        else:
            blocking_reasons.append(message)

    active_hard_fails = _active_hard_fail_conditions(context, criteria)
    for condition in active_hard_fails:
        blocking_reasons.append(f"hard fail condition active: {condition}")
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _decision_for(
    context: _ObservationContext,
    criteria: Mapping[str, Any],
    blocking_reasons: Sequence[str],
) -> str:
    if not blocking_reasons:
        return DECISION_CONTINUE
    active_hard_fails = _active_hard_fail_conditions(context, criteria)
    if active_hard_fails or any(
        token in reason
        for reason in blocking_reasons
        for token in (
            "telemetry path",
            "kill-switch",
            "safe-mode",
            "runtime binding must be healthy",
            "broker session must be healthy",
        )
    ):
        return DECISION_FREEZE
    if any(
        token in reason
        for reason in blocking_reasons
        for token in (
            "drift",
            "threshold",
            "incident",
            "no-go",
            "order rejections",
            "slippage",
            "fills",
        )
    ):
        return DECISION_ESCALATE
    return DECISION_HOLD


def _flag_or_ref(
    context: _ObservationContext,
    *,
    flag_keys: Sequence[str],
    ref_keys: Sequence[str],
    missing_message: str,
) -> _CheckResult:
    flag_value, flag_path = _first_key(context, flag_keys)
    if flag_value is True:
        return _ready(flag_path)
    if flag_value is False:
        return _blocked(missing_message)
    ref_path = _first_present_ref(context, ref_keys)
    if ref_path:
        return _ready(ref_path)
    return _blocked(missing_message)


def _boolean_or_status_ready(
    context: _ObservationContext,
    keys: Sequence[str],
    missing_message: str,
) -> _CheckResult:
    value, path = _first_key(context, keys)
    if value is True:
        return _ready(path)
    if value is False:
        return _blocked(missing_message)
    normalized = _normalized(value)
    if normalized in {"healthy", "ok", "ready", "running", "live"}:
        return _ready(path)
    if normalized:
        return _blocked(missing_message)
    return _blocked(missing_message)


def _all_flags_ready(
    context: _ObservationContext,
    flag_groups: Sequence[tuple[Sequence[str], str]],
    *,
    optional_refs: Sequence[str] = (),
) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    for keys, message in flag_groups:
        value, path = _first_key(context, keys)
        if value is True:
            if path:
                evidence_refs.append(path)
        else:
            blocking_reasons.append(message)
    for ref_key in optional_refs:
        ref = _first_present_ref(context, (ref_key,))
        if ref:
            evidence_refs.append(ref)
            break
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _minimum_count_refs(
    context: _ObservationContext,
    *,
    keys: Sequence[str],
    minimum: int,
    missing_message: str,
    low_message: str,
) -> tuple[str, ...]:
    del missing_message, low_message
    value, path = _first_key(context, keys)
    number = _number_or_none(value)
    if number is not None and number >= minimum and path:
        return (path,)
    return ()


def _minimum_count_blockers(
    context: _ObservationContext,
    *,
    keys: Sequence[str],
    minimum: int,
    missing_message: str,
    low_message: str,
) -> tuple[str, ...]:
    value, _path = _first_key(context, keys)
    number = _number_or_none(value)
    if number is None:
        return (missing_message,)
    if number < minimum:
        return (low_message,)
    return ()


def _ready(*evidence_refs: str | None) -> _CheckResult:
    return _CheckResult(tuple(ref for ref in evidence_refs if ref), ())


def _blocked(*blocking_reasons: str) -> _CheckResult:
    return _CheckResult((), tuple(blocking_reasons))


def _first_key(
    context: _ObservationContext,
    keys: Sequence[str],
) -> tuple[Any, str | None]:
    for source_name, source in context.sources:
        for key in keys:
            if key in source:
                return source.get(key), _path(source_name, key)
    return None, None


def _first_key_path(context: _ObservationContext, keys: Sequence[str]) -> str | None:
    _value, path = _first_key(context, keys)
    return path


def _first_present_ref(context: _ObservationContext, keys: Sequence[str]) -> str | None:
    for source_name, source in context.sources:
        for key in keys:
            if _present(source.get(key)):
                return _path(source_name, key)
    return None


def _first_string(context: _ObservationContext, keys: Sequence[str]) -> str | None:
    for _source_name, source in context.sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
            elif value is not None and not isinstance(value, (Mapping, Sequence)):
                return str(value)
    return None


def _first_bool(context: _ObservationContext, keys: Sequence[str]) -> bool | None:
    value, _path = _first_key(context, keys)
    return value if isinstance(value, bool) else None


def _first_number(context: _ObservationContext, keys: Sequence[str]) -> float | None:
    value, _path = _first_key(context, keys)
    return _number_or_none(value)


def _daily_checkins(context: _ObservationContext) -> tuple[Mapping[str, Any], ...]:
    for source_name, source in context.sources:
        del source_name
        for key in ("daily_checkins", "daily_check_ins", "daily_reports", "checkins"):
            value = source.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _observation_window_days(context: _ObservationContext) -> float | None:
    explicit = _first_number(
        context,
        (
            "observation_window_days",
            "window_days",
            "observed_days",
            "first_week_observed_days",
        ),
    )
    start = _parse_timestamp(
        _first_string(context, ("live_started_at", "window_started_at", "started_at"))
    )
    end = _parse_timestamp(
        _first_string(context, ("observed_through_at", "window_ended_at", "ended_at"))
    )
    derived: float | None = None
    if start is not None and end is not None and end >= start:
        derived = (end - start).total_seconds() / 86400
    if explicit is not None and derived is not None:
        return min(explicit, derived)
    return explicit if explicit is not None else derived


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _active_hard_fail_conditions(
    context: _ObservationContext,
    criteria: Mapping[str, Any],
) -> tuple[str, ...]:
    configured = tuple(_string_items(criteria.get("hard_fail_conditions"))) or EXPECTED_HARD_FAIL_CONDITIONS
    active_list = set(_string_items(context.packet.get("hard_fail_conditions")))
    conditions = _mapping(context.packet.get("conditions"))
    active: list[str] = []
    for condition in configured:
        if (
            condition in active_list
            or conditions.get(condition) is True
            or context.packet.get(condition) is True
        ):
            active.append(condition)
    return tuple(active)


def _cooldown_hours(criteria: Mapping[str, Any]) -> float:
    cooldown_policy = _mapping(criteria.get("cooldown_policy"))
    value = _number_or_none(
        cooldown_policy.get("min_hours_after_short_term_drift_before_live_change")
    )
    return value if value is not None else float(EXPECTED_COOLDOWN_HOURS)


def _report_id(
    packet: Mapping[str, Any],
    checks: Sequence[FirstWeekObservationCheck],
    decision: str,
) -> str:
    stable_payload = {
        "activation_ref": _first_value(
            packet,
            ("live_activation_ref", "activation_ref", "activation_id"),
        ),
        "runtime_binding_id": _first_value(
            packet,
            ("runtime_binding_id", "runtime_binding_ref", "live_runtime_binding_ref"),
        ),
        "observed_through_at": _first_value(
            packet,
            ("observed_through_at", "window_ended_at", "ended_at"),
        ),
        "decision": decision,
        "checks": [check.to_dict() for check in checks],
    }
    digest = hashlib.sha256(json.dumps(stable_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"first-week-observation-{digest[:16]}"


def _first_value(source: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = source.get(key)
        if _present(value):
            return value
    return None


def _path(source_name: str, key: str) -> str:
    return key if source_name == "$" else f"{source_name}.{key}"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Sequence, Mapping)):
        return bool(value)
    return True


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return int(number)


def _open_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "none", "no"}:
            return 0
        return 1
    if isinstance(value, (Sequence, Mapping)):
        return len(value)
    return None


def _string_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _normalized(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)

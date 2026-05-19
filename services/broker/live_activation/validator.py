"""Fail-closed validator for broker production-live activation criteria.

Implements the 2026-05-19 blueprint supplement Part B2 criteria shape. The
validator checks pre-gate evidence and approvals only; it never enables broker
live flags or performs broker/runtime side effects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_CRITERIA_PATH = Path(__file__).with_name("criteria.json")
EXPECTED_VERSION = "1.0"
PASSING_APPROVAL_STATUSES = {"approved", "approved_with_conditions", "recorded", "signed"}
BLOCKING_APPROVAL_STATUSES = {"pending", "rejected", "revoked", "expired", "missing"}

EXPECTED_REQUIRED_EVIDENCE = {
    "paper_run_days_min": 14,
    "canary_run_days_min": 7,
    "ep4_packet_required": True,
    "ep5_packet_required": True,
    "broker_sandbox_smoke_required": True,
    "broker_credential_scope_verified": True,
    "kill_switch_demo_required": True,
    "rollback_drill_required": True,
    "bff_ha_readiness_required": True,
    "telemetry_readiness_required": True,
    "audit_retention_ready": True,
    "first_week_observation_window_ready": True,
}
EXPECTED_REQUIRED_APPROVALS = ("risk_owner", "operator")
EXPECTED_HARD_FAIL_CONDITIONS = (
    "telemetry_unavailable",
    "audit_unavailable",
    "kill_switch_unavailable",
    "rollback_target_missing",
    "broker_credential_unverified",
    "bff_control_plane_unhealthy",
    "capital_binding_unapproved",
    "runtime_binding_unverified",
    "openclaw_as_execution_kernel_attempted",
)
COOLDOWN_FIELD = "min_hours_after_short_term_drift_before_live_change"
EXPECTED_COOLDOWN_HOURS = 24

MINIMUM_EVIDENCE_FIELDS = {
    "paper_run_days_min": "paper_run_days",
    "canary_run_days_min": "canary_run_days",
}
REQUIRED_REF_FIELDS = {
    "ep4_packet_required": ("ep4_packet_ref", "ep4_packet_id"),
    "ep5_packet_required": ("ep5_packet_ref", "ep5_packet_id"),
    "broker_sandbox_smoke_required": ("broker_sandbox_smoke_ref",),
    "kill_switch_demo_required": ("kill_switch_demo_ref",),
    "rollback_drill_required": ("rollback_drill_ref",),
    "bff_ha_readiness_required": ("bff_ha_readiness_ref",),
    "telemetry_readiness_required": ("telemetry_readiness_ref",),
    "audit_retention_ready": ("audit_retention_ref", "audit_retention_ready_ref"),
    "first_week_observation_window_ready": (
        "first_week_observation_window_ref",
        "first_week_observation_ref",
    ),
}
REQUIRED_BOOL_FIELDS = {
    "broker_credential_scope_verified": ("broker_credential_scope_verified",),
}


class BrokerLiveActivationValidationError(ValueError):
    """Raised when a broker live activation request fails validation."""


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    blocking_reasons: tuple[str, ...] = ()
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "can_activate": self.passed,
            "blocking_reasons": list(self.blocking_reasons),
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def load_criteria(path: str | Path) -> dict[str, Any]:
    """Load a broker live activation criteria JSON document."""

    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise BrokerLiveActivationValidationError("criteria document must be a JSON object")
    return data


def load_default_criteria() -> dict[str, Any]:
    """Load the repo-local Part B2 criteria document."""

    return load_criteria(DEFAULT_CRITERIA_PATH)


def validate_criteria_shape(criteria: Mapping[str, Any] | None = None) -> ValidationResult:
    """Validate that the criteria document still matches supplement Part B2."""

    if criteria is None:
        payload = load_default_criteria()
    elif isinstance(criteria, Mapping):
        payload = dict(criteria)
    else:
        return _result(
            [
                ValidationIssue(
                    "invalid_criteria_document",
                    "$",
                    "criteria document must be an object",
                )
            ]
        )
    errors: list[ValidationIssue] = []

    if payload.get("version") != EXPECTED_VERSION:
        errors.append(
            ValidationIssue(
                "invalid_criteria_version",
                "version",
                f"version must be {EXPECTED_VERSION!r}",
            )
        )

    required_evidence = _mapping(payload.get("required_evidence"))
    for key, expected_value in EXPECTED_REQUIRED_EVIDENCE.items():
        actual_value = required_evidence.get(key)
        if actual_value != expected_value:
            errors.append(
                ValidationIssue(
                    "invalid_criteria_required_evidence",
                    f"required_evidence.{key}",
                    f"expected {expected_value!r}, got {actual_value!r}",
                )
            )

    approvals = tuple(_string_items(payload.get("required_approvals")))
    if approvals != EXPECTED_REQUIRED_APPROVALS:
        errors.append(
            ValidationIssue(
                "invalid_criteria_required_approvals",
                "required_approvals",
                "required approvals must be risk_owner then operator",
            )
        )

    hard_fail_conditions = tuple(_string_items(payload.get("hard_fail_conditions")))
    if hard_fail_conditions != EXPECTED_HARD_FAIL_CONDITIONS:
        errors.append(
            ValidationIssue(
                "invalid_criteria_hard_fail_conditions",
                "hard_fail_conditions",
                "hard fail conditions must match supplement Part B2",
            )
        )

    cooldown_policy = _mapping(payload.get("cooldown_policy"))
    if cooldown_policy.get(COOLDOWN_FIELD) != EXPECTED_COOLDOWN_HOURS:
        errors.append(
            ValidationIssue(
                "invalid_criteria_cooldown_policy",
                f"cooldown_policy.{COOLDOWN_FIELD}",
                f"cooldown must be {EXPECTED_COOLDOWN_HOURS} hours",
            )
        )

    return _result(errors)


def validate_activation_request(
    request: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> ValidationResult:
    """Validate a candidate broker production-live activation request.

    The request may keep evidence under an ``evidence`` object or at top level.
    Hard-fail conditions may be supplied as an active-condition list under
    ``hard_fail_conditions`` or as booleans under ``conditions``.
    """

    criteria_result = validate_criteria_shape(criteria)
    errors: list[ValidationIssue] = list(criteria_result.errors)
    if not isinstance(request, Mapping):
        errors.append(
            ValidationIssue(
                "invalid_activation_request",
                "$",
                "activation request must be an object",
            )
        )
        return _result(errors)
    if not criteria_result.passed:
        return _result(errors)

    criteria_payload = load_default_criteria() if criteria is None else dict(criteria)
    evidence = _mapping(request.get("evidence")) or request
    approvals = _mapping(request.get("approvals") or request.get("approval"))
    conditions = _mapping(request.get("conditions"))

    required_evidence = _mapping(criteria_payload.get("required_evidence"))
    errors.extend(_validate_required_evidence(required_evidence, evidence))
    errors.extend(_validate_required_approvals(criteria_payload, approvals))
    errors.extend(_validate_hard_fail_conditions(criteria_payload, request, conditions))
    errors.extend(_validate_cooldown(criteria_payload, request))

    return _result(errors)


def validate_activation_request_or_raise(
    request: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> ValidationResult:
    """Validate and raise a compact fail-closed error on blocking issues."""

    result = validate_activation_request(request, criteria)
    if not result.passed:
        raise BrokerLiveActivationValidationError("; ".join(result.blocking_reasons))
    return result


def _validate_required_evidence(
    required_evidence: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []

    for criteria_key, evidence_key in MINIMUM_EVIDENCE_FIELDS.items():
        expected_minimum = required_evidence.get(criteria_key)
        actual_value = _number_or_none(evidence.get(evidence_key))
        if actual_value is None or actual_value < float(expected_minimum):
            errors.append(
                ValidationIssue(
                    "minimum_evidence_not_met",
                    f"evidence.{evidence_key}",
                    f"{evidence_key} must be >= {expected_minimum}",
                )
            )

    for criteria_key, candidate_keys in REQUIRED_REF_FIELDS.items():
        if required_evidence.get(criteria_key) is not True:
            continue
        if not any(_present(evidence.get(candidate_key)) for candidate_key in candidate_keys):
            errors.append(
                ValidationIssue(
                    "missing_required_evidence",
                    "evidence." + "|".join(candidate_keys),
                    f"{criteria_key} requires one of: {', '.join(candidate_keys)}",
                )
            )

    for criteria_key, candidate_keys in REQUIRED_BOOL_FIELDS.items():
        if required_evidence.get(criteria_key) is not True:
            continue
        if not any(evidence.get(candidate_key) is True for candidate_key in candidate_keys):
            errors.append(
                ValidationIssue(
                    "missing_required_evidence",
                    "evidence." + "|".join(candidate_keys),
                    f"{criteria_key} must be explicitly true",
                )
            )

    return errors


def _validate_required_approvals(
    criteria: Mapping[str, Any],
    approvals: Mapping[str, Any],
) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []
    for role in _string_items(criteria.get("required_approvals")):
        status = _approval_status(approvals.get(role))
        if status not in PASSING_APPROVAL_STATUSES:
            errors.append(
                ValidationIssue(
                    "required_approval_missing",
                    f"approvals.{role}",
                    f"{role} approval must be one of: {', '.join(sorted(PASSING_APPROVAL_STATUSES))}",
                )
            )
    return errors


def _validate_hard_fail_conditions(
    criteria: Mapping[str, Any],
    request: Mapping[str, Any],
    conditions: Mapping[str, Any],
) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []
    active_list = set(_string_items(request.get("hard_fail_conditions")))
    configured = tuple(_string_items(criteria.get("hard_fail_conditions")))

    for condition in configured:
        active = (
            condition in active_list
            or conditions.get(condition) is True
            or request.get(condition) is True
        )
        if active:
            errors.append(
                ValidationIssue(
                    "hard_fail_condition_active",
                    f"hard_fail_conditions.{condition}",
                    f"{condition} is active; broker live activation must fail closed",
                )
            )

    return errors


def _validate_cooldown(
    criteria: Mapping[str, Any],
    request: Mapping[str, Any],
) -> list[ValidationIssue]:
    cooldown_policy = _mapping(criteria.get("cooldown_policy"))
    minimum_hours = _number_or_none(cooldown_policy.get(COOLDOWN_FIELD))
    if minimum_hours is None:
        return []

    cooldown = _mapping(request.get("cooldown"))
    short_term_drift_detected = (
        request.get("short_term_drift_detected") is True
        or cooldown.get("short_term_drift_detected") is True
    )
    hours_since_drift = _first_number(
        cooldown.get("hours_since_short_term_drift"),
        cooldown.get("hours_since_last_short_term_drift"),
        request.get("hours_since_short_term_drift"),
        request.get("hours_since_last_short_term_drift"),
    )
    if not short_term_drift_detected and hours_since_drift is None:
        return []

    if hours_since_drift is None or hours_since_drift < minimum_hours:
        return [
            ValidationIssue(
                "cooldown_not_satisfied",
                "cooldown.hours_since_short_term_drift",
                f"live change requires at least {int(minimum_hours)} hours after short-term drift",
            )
        ]
    return []


def _result(errors: Sequence[ValidationIssue]) -> ValidationResult:
    blocking_reasons = tuple(issue.message for issue in errors)
    return ValidationResult(
        passed=not errors,
        blocking_reasons=blocking_reasons,
        errors=tuple(errors),
    )


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _string_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _approval_status(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("status") or value.get("state")
        if value.get("recorded") is True and not raw:
            return "recorded"
        if value.get("approved") is True and not raw:
            return "approved"
        return str(raw or "missing").strip().lower()
    if isinstance(value, bool):
        return "approved" if value else "missing"
    status = str(value or "missing").strip().lower()
    if status in BLOCKING_APPROVAL_STATUSES:
        return status
    return status


def _present(value: Any) -> bool:
    if value is None:
        return False
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


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number_or_none(value)
        if number is not None:
            return number
    return None

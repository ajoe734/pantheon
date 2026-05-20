"""Operator checklist generator for broker production-live activation.

Implements the 2026-05-19 blueprint supplement Part B4 checklist shape. The
generator produces machine-readable operator gate status only; it never records
approvals, mutates runtime state, or enables broker live flags.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .validator import (
    EXPECTED_COOLDOWN_HOURS,
    PASSING_APPROVAL_STATUSES,
    load_default_criteria,
    validate_criteria_shape,
)


CHECKLIST_VERSION = "1.0"
CHECKLIST_SOURCE = "2026-05-19 blueprint supplement Part B4"
READY = "ready"
BLOCKED = "blocked"

EXPECTED_OPERATOR_CHECKLIST_ITEMS = (
    "Risk-owner approval is recorded and still valid.",
    "Operator identity and authority are explicit.",
    "Broker credential scope is verified.",
    "Runtime binding targets the approved live runtime.",
    "Deployment plan and capital binding are operator-reviewed.",
    "Broker sandbox smoke and live-session readiness are recorded.",
    "Kill-switch path is reachable and recently demonstrated.",
    "Rollback / safe-mode drill is ready.",
    "BFF, telemetry, audit, and postmortem paths are available.",
    "First-week observation window and no-go conditions are explicit.",
)


@dataclass(frozen=True)
class OperatorChecklistItem:
    id: str
    order: int
    text: str
    status: str
    evidence_refs: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    required: bool = True
    source: str = CHECKLIST_SOURCE

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
class OperatorChecklist:
    version: str
    source: str
    can_sign_off: bool
    items: tuple[OperatorChecklistItem, ...]
    blocking_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "can_sign_off": self.can_sign_off,
            "passed": self.can_sign_off,
            "blocking_reasons": list(self.blocking_reasons),
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class _ChecklistContext:
    request: Mapping[str, Any]
    evidence: Mapping[str, Any]
    operator_review: Mapping[str, Any]
    operations: Mapping[str, Any]
    approvals: Mapping[str, Any]
    cooldown: Mapping[str, Any]

    @property
    def sources(self) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        return (
            ("operator_review", self.operator_review),
            ("operations", self.operations),
            ("evidence", self.evidence),
            ("$", self.request),
        )


@dataclass(frozen=True)
class _CheckResult:
    evidence_refs: tuple[str, ...]
    blocking_reasons: tuple[str, ...]


def generate_operator_checklist(
    request: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> OperatorChecklist:
    """Generate the Part B4 operator checklist for a live activation request."""

    criteria_result = validate_criteria_shape(criteria)
    if not criteria_result.passed:
        reasons = _unique(criteria_result.blocking_reasons)
        return _blocked_checklist(reasons)

    if not isinstance(request, Mapping):
        return _blocked_checklist(("activation request must be an object",))

    criteria_payload = load_default_criteria() if criteria is None else dict(criteria)
    context = _context_for(request)
    checks = (
        _check_risk_owner_approval(context),
        _check_operator_identity(context),
        _check_broker_credential_scope(context),
        _check_runtime_binding(context),
        _check_deployment_and_capital_binding(context),
        _check_broker_session_readiness(context),
        _check_kill_switch_demo(context),
        _check_rollback_safe_mode(context),
        _check_control_plane_paths(context),
        _check_observation_and_no_go(context, criteria_payload),
    )
    items = tuple(
        _item(index=index, result=result)
        for index, result in enumerate(checks, start=1)
    )
    blocking_reasons = _unique(
        tuple(reason for item in items for reason in item.blocking_reasons)
    )
    return OperatorChecklist(
        version=CHECKLIST_VERSION,
        source=CHECKLIST_SOURCE,
        can_sign_off=not blocking_reasons,
        items=items,
        blocking_reasons=blocking_reasons,
    )


def _blocked_checklist(blocking_reasons: Sequence[str]) -> OperatorChecklist:
    reasons = _unique(blocking_reasons)
    return OperatorChecklist(
        version=CHECKLIST_VERSION,
        source=CHECKLIST_SOURCE,
        can_sign_off=False,
        blocking_reasons=reasons,
        items=tuple(
            OperatorChecklistItem(
                id=_item_id(index),
                order=index,
                text=text,
                status=BLOCKED,
                blocking_reasons=reasons,
            )
            for index, text in enumerate(EXPECTED_OPERATOR_CHECKLIST_ITEMS, start=1)
        ),
    )


def _context_for(request: Mapping[str, Any]) -> _ChecklistContext:
    evidence = _mapping(request.get("evidence")) or request
    operator_review = _mapping(
        request.get("operator_review")
        or request.get("operator_checklist")
        or request.get("operator")
    )
    operations = _mapping(request.get("operations") or request.get("ops"))
    approvals = _mapping(request.get("approvals") or request.get("approval"))
    cooldown = _mapping(request.get("cooldown"))
    return _ChecklistContext(
        request=request,
        evidence=evidence,
        operator_review=operator_review,
        operations=operations,
        approvals=approvals,
        cooldown=cooldown,
    )


def _item(index: int, result: _CheckResult) -> OperatorChecklistItem:
    return OperatorChecklistItem(
        id=_item_id(index),
        order=index,
        text=EXPECTED_OPERATOR_CHECKLIST_ITEMS[index - 1],
        status=READY if not result.blocking_reasons else BLOCKED,
        evidence_refs=result.evidence_refs,
        blocking_reasons=result.blocking_reasons,
    )


def _item_id(index: int) -> str:
    return f"operator_b4_{index:02d}"


def _check_risk_owner_approval(context: _ChecklistContext) -> _CheckResult:
    approval = context.approvals.get("risk_owner")
    approval_status, approval_status_path = _approval_status(
        approval,
        "approvals.risk_owner",
    )
    approval_ref = _approval_ref(approval, "approvals.risk_owner")
    if approval_status in PASSING_APPROVAL_STATUSES:
        return _ready(approval_status_path, approval_ref)
    if approval_status:
        return _blocked("risk-owner approval must be recorded before operator sign-off")

    review_ref = _first_present_ref(
        context,
        (
            "risk_owner_approval_ref",
            "risk_owner_decision_ref",
            "risk_owner_signoff_ref",
        ),
    )
    if review_ref:
        return _ready(review_ref)
    return _blocked("risk-owner approval must be recorded before operator sign-off")


def _check_operator_identity(context: _ChecklistContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []

    operator_ref = _first_present_ref(
        context,
        ("operator_id", "operator_ref", "operator_principal", "operator_identity_ref"),
    )
    if operator_ref:
        evidence_refs.append(operator_ref)
    else:
        blocking_reasons.append("operator identity evidence is required")

    authority_value, authority_path = _first_key(
        context,
        ("operator_authority_confirmed", "operator_has_live_authority"),
    )
    authority_ref = _first_present_ref(
        context,
        ("operator_authority_ref", "operator_role_ref", "operator_rbac_ref"),
    )
    if authority_value is True:
        evidence_refs.append(authority_path)
    elif authority_ref:
        evidence_refs.append(authority_ref)
    else:
        blocking_reasons.append("operator authority evidence is required")

    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_broker_credential_scope(context: _ChecklistContext) -> _CheckResult:
    raw_secret_present, raw_secret_path = _first_key(
        context,
        ("raw_broker_secret_present", "raw_secret_present"),
    )
    if raw_secret_present is True:
        return _blocked(f"raw broker secret must not be present at {raw_secret_path}")
    return _flag_or_ref(
        context,
        flag_keys=("broker_credential_scope_verified",),
        ref_keys=(
            "broker_credential_scope_ref",
            "broker_credential_verification_ref",
            "shioaji_credential_scope_ref",
        ),
        missing_message="broker credential scope verification is required",
    )


def _check_runtime_binding(context: _ChecklistContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []

    binding_ref = _first_present_ref(
        context,
        ("runtime_binding_ref", "runtime_binding_id", "live_runtime_binding_ref"),
    )
    if binding_ref:
        evidence_refs.append(binding_ref)
    else:
        blocking_reasons.append("runtime binding evidence is required")

    stage_value, stage_path = _first_key(
        context,
        (
            "runtime_stage",
            "deployment_stage",
            "target_stage",
            "runtime_mode",
            "execution_stage",
        ),
    )
    if stage_value is not None:
        if _normalized(stage_value) != "live":
            blocking_reasons.append("runtime binding target stage must be live")
        else:
            evidence_refs.append(stage_path)
    else:
        blocking_reasons.append("runtime binding live-stage evidence is required")

    verified_value, verified_path = _first_key(
        context,
        ("runtime_binding_verified", "live_runtime_binding_verified"),
    )
    if verified_value is True:
        evidence_refs.append(verified_path)
    elif verified_value is False:
        blocking_reasons.append("runtime binding must be verified")

    return _CheckResult(
        tuple(ref for ref in evidence_refs if ref),
        tuple(blocking_reasons),
    )


def _check_deployment_and_capital_binding(context: _ChecklistContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []

    plan_ref = _first_present_ref(
        context,
        ("deployment_plan_ref", "deployment_plan_id", "live_deployment_plan_ref"),
    )
    if plan_ref:
        evidence_refs.append(plan_ref)
    else:
        blocking_reasons.append("deployment plan evidence is required")

    plan_review = _flag_or_ref(
        context,
        flag_keys=("deployment_plan_operator_reviewed", "deployment_plan_reviewed"),
        ref_keys=("deployment_plan_review_ref", "operator_deployment_review_ref"),
        missing_message="deployment plan operator review is required",
    )
    evidence_refs.extend(plan_review.evidence_refs)
    blocking_reasons.extend(plan_review.blocking_reasons)

    capital_ref = _first_present_ref(
        context,
        (
            "capital_binding_ref",
            "capital_binding_id",
            "capital_pool_ref",
            "capital_pool_id",
        ),
    )
    if capital_ref:
        evidence_refs.append(capital_ref)
    else:
        blocking_reasons.append("capital binding evidence is required")

    capital_approval = _flag_or_ref(
        context,
        flag_keys=("capital_binding_approved", "capital_pool_binding_approved"),
        ref_keys=("capital_binding_approval_ref", "capital_pool_approval_ref"),
        missing_message="capital binding approval is required",
    )
    evidence_refs.extend(capital_approval.evidence_refs)
    blocking_reasons.extend(capital_approval.blocking_reasons)

    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_broker_session_readiness(context: _ChecklistContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []

    smoke = _flag_or_ref(
        context,
        flag_keys=("broker_sandbox_smoke_passed", "broker_sandbox_smoke_complete"),
        ref_keys=("broker_sandbox_smoke_ref",),
        missing_message="broker sandbox smoke evidence is required",
    )
    evidence_refs.extend(smoke.evidence_refs)
    blocking_reasons.extend(smoke.blocking_reasons)

    session = _flag_or_ref(
        context,
        flag_keys=(
            "broker_live_session_ready",
            "broker_session_ready",
            "shioaji_session_ready",
        ),
        ref_keys=(
            "broker_live_session_ref",
            "broker_session_readiness_ref",
            "shioaji_session_ref",
        ),
        missing_message="broker live-session readiness evidence is required",
    )
    evidence_refs.extend(session.evidence_refs)
    blocking_reasons.extend(session.blocking_reasons)

    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_kill_switch_demo(context: _ChecklistContext) -> _CheckResult:
    return _flag_or_ref(
        context,
        flag_keys=("kill_switch_demo_complete", "kill_switch_demo_completed"),
        ref_keys=("kill_switch_demo_ref", "kill_switch_path_ref"),
        missing_message="kill-switch demo evidence is required",
    )


def _check_rollback_safe_mode(context: _ChecklistContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []

    rollback = _flag_or_ref(
        context,
        flag_keys=("rollback_drill_complete", "rollback_drill_completed"),
        ref_keys=("rollback_drill_ref", "rollback_runbook_ref"),
        missing_message="rollback drill evidence is required",
    )
    evidence_refs.extend(rollback.evidence_refs)
    blocking_reasons.extend(rollback.blocking_reasons)

    safe_mode = _flag_or_ref(
        context,
        flag_keys=("safe_mode_ready", "safe_mode_path_ready"),
        ref_keys=("safe_mode_ref", "safe_mode_runbook_ref", "safe_mode_drill_ref"),
        missing_message="safe-mode readiness evidence is required",
    )
    evidence_refs.extend(safe_mode.evidence_refs)
    blocking_reasons.extend(safe_mode.blocking_reasons)

    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_control_plane_paths(context: _ChecklistContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    required_paths = (
        (
            ("bff_ha_readiness_ref", "bff_control_plane_readiness_ref"),
            ("bff_ha_ready", "bff_control_plane_ready"),
            "BFF HA readiness evidence is required",
        ),
        (
            ("telemetry_readiness_ref", "telemetry_path_ref"),
            ("telemetry_path_available", "telemetry_readiness_available"),
            "telemetry path evidence is required",
        ),
        (
            ("audit_retention_ref", "audit_retention_ready_ref", "audit_path_ref"),
            ("audit_path_available", "audit_retention_ready"),
            "audit path evidence is required",
        ),
        (
            ("postmortem_path_ref", "incident_postmortem_path_ref"),
            ("postmortem_path_available", "incident_postmortem_path_available"),
            "postmortem path evidence is required",
        ),
    )
    for ref_keys, flag_keys, message in required_paths:
        result = _flag_or_ref(
            context,
            flag_keys=flag_keys,
            ref_keys=ref_keys,
            missing_message=message,
        )
        evidence_refs.extend(result.evidence_refs)
        blocking_reasons.extend(result.blocking_reasons)
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


def _check_observation_and_no_go(
    context: _ChecklistContext,
    criteria: Mapping[str, Any],
) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []

    observation = _flag_or_ref(
        context,
        flag_keys=(
            "first_week_observation_window_ready",
            "first_week_observation_ready",
        ),
        ref_keys=(
            "first_week_observation_window_ref",
            "first_week_observation_ref",
        ),
        missing_message="first-week observation window evidence is required",
    )
    evidence_refs.extend(observation.evidence_refs)
    blocking_reasons.extend(observation.blocking_reasons)

    no_go = _flag_or_ref(
        context,
        flag_keys=(
            "no_go_conditions_acknowledged",
            "operator_no_go_conditions_explicit",
        ),
        ref_keys=("no_go_conditions_ref", "operator_no_go_ref"),
        missing_message="operator no-go conditions evidence is required",
    )
    evidence_refs.extend(no_go.evidence_refs)
    blocking_reasons.extend(no_go.blocking_reasons)

    blockers_value, blockers_path = _first_key(
        context,
        ("open_operator_blockers", "operator_open_blockers", "open_no_go_conditions"),
    )
    blockers_count = _open_count(blockers_value)
    if blockers_count == 0:
        evidence_refs.append(blockers_path)
    elif blockers_count is None:
        blocking_reasons.append(
            "operator no-go conditions must explicitly show no open blockers"
        )
    else:
        blocking_reasons.append("operator no-go conditions have open blockers")

    blocking_reasons.extend(_cooldown_blocking_reasons(context, criteria))
    blocking_reasons.extend(_hard_fail_blocking_reasons(context, criteria))
    return _CheckResult(
        tuple(ref for ref in evidence_refs if ref),
        tuple(blocking_reasons),
    )


def _hard_fail_blocking_reasons(
    context: _ChecklistContext,
    criteria: Mapping[str, Any],
) -> tuple[str, ...]:
    hard_fail_conditions = tuple(_string_items(criteria.get("hard_fail_conditions")))
    active_list = set(_string_items(context.request.get("hard_fail_conditions")))
    conditions = _mapping(context.request.get("conditions"))
    blocking_reasons: list[str] = []
    for condition in hard_fail_conditions:
        active = (
            condition in active_list
            or conditions.get(condition) is True
            or context.request.get(condition) is True
        )
        if active:
            blocking_reasons.append(f"hard fail condition active: {condition}")
    return tuple(blocking_reasons)


def _cooldown_blocking_reasons(
    context: _ChecklistContext,
    criteria: Mapping[str, Any],
) -> tuple[str, ...]:
    cooldown_policy = _mapping(criteria.get("cooldown_policy"))
    required_hours = _number_or_none(
        cooldown_policy.get("min_hours_after_short_term_drift_before_live_change")
    )
    if required_hours is None:
        required_hours = float(EXPECTED_COOLDOWN_HOURS)

    drift_detected = (
        context.cooldown.get("short_term_drift_detected") is True
        or context.request.get("short_term_drift_detected") is True
    )
    if not drift_detected:
        return ()

    hours_value = context.cooldown.get("hours_since_short_term_drift")
    if hours_value is None:
        hours_value = context.request.get("hours_since_short_term_drift")
    hours = _number_or_none(hours_value)
    if hours is None or hours < required_hours:
        return (
            "short-term drift cooldown must be satisfied before operator sign-off",
        )
    return ()


def _flag_or_ref(
    context: _ChecklistContext,
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


def _ready(*evidence_refs: str | None) -> _CheckResult:
    return _CheckResult(tuple(ref for ref in evidence_refs if ref), ())


def _blocked(*blocking_reasons: str) -> _CheckResult:
    return _CheckResult((), tuple(blocking_reasons))


def _first_key(
    context: _ChecklistContext,
    keys: Sequence[str],
) -> tuple[Any, str | None]:
    for source_name, source in context.sources:
        for key in keys:
            if key in source:
                return source.get(key), _path(source_name, key)
    return None, None


def _first_present_ref(context: _ChecklistContext, keys: Sequence[str]) -> str | None:
    for source_name, source in context.sources:
        for key in keys:
            if _present(source.get(key)):
                return _path(source_name, key)
    return None


def _approval_status(value: Any, path: str) -> tuple[str | None, str | None]:
    if isinstance(value, Mapping):
        status = value.get("status")
        return _normalized(status), f"{path}.status" if status is not None else None
    return _normalized(value), path if value is not None else None


def _approval_ref(value: Any, path: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("approval_ref", "decision_ref", "approval_decision_id", "ref"):
        if _present(value.get(key)):
            return f"{path}.{key}"
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


def _string_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


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

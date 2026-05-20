"""Risk-owner checklist generator for broker production-live activation.

Implements the 2026-05-19 blueprint supplement Part B3 checklist shape. The
generator produces machine-readable review status only; it never records
approvals, mutates runtime state, or enables broker live flags.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .validator import load_default_criteria, validate_criteria_shape


CHECKLIST_VERSION = "1.0"
CHECKLIST_SOURCE = "2026-05-19 blueprint supplement Part B3"
READY = "ready"
BLOCKED = "blocked"

EXPECTED_RISK_OWNER_CHECKLIST_ITEMS = (
    "Strategy / artifact lineage complete.",
    "14 days paper evidence complete.",
    "7 days canary evidence complete.",
    "Risk policy matches capital pool charter.",
    "Drawdown / liquidity / exposure within threshold.",
    "Rollback target exists.",
    "Kill-switch demo complete.",
    "Telemetry / audit / postmortem path available.",
    "Sponsor persona responsibility explicit.",
    "Conflict resolution log has no open conflict.",
)


@dataclass(frozen=True)
class RiskOwnerChecklistItem:
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
class RiskOwnerChecklist:
    version: str
    source: str
    can_sign_off: bool
    items: tuple[RiskOwnerChecklistItem, ...]
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
    risk_owner_review: Mapping[str, Any]

    @property
    def sources(self) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        return (
            ("risk_owner_review", self.risk_owner_review),
            ("evidence", self.evidence),
            ("$", self.request),
        )


@dataclass(frozen=True)
class _CheckResult:
    evidence_refs: tuple[str, ...]
    blocking_reasons: tuple[str, ...]


def generate_risk_owner_checklist(
    request: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> RiskOwnerChecklist:
    """Generate the Part B3 risk-owner checklist for a live activation request."""

    criteria_result = validate_criteria_shape(criteria)
    if not criteria_result.passed:
        reasons = _unique(criteria_result.blocking_reasons)
        return _blocked_checklist(reasons)

    if not isinstance(request, Mapping):
        return _blocked_checklist(("activation request must be an object",))

    criteria_payload = load_default_criteria() if criteria is None else dict(criteria)
    context = _context_for(request)
    checks = (
        _check_strategy_artifact_lineage(context),
        _check_minimum_days(context, criteria_payload, "paper_run_days_min", "paper_run_days"),
        _check_minimum_days(context, criteria_payload, "canary_run_days_min", "canary_run_days"),
        _check_risk_policy_charter(context),
        _check_risk_thresholds(context),
        _check_rollback_target(context),
        _check_kill_switch_demo(context),
        _check_telemetry_audit_postmortem(context),
        _check_sponsor_persona_responsibility(context),
        _check_conflict_resolution(context),
    )
    items = tuple(
        _item(index=index, result=result)
        for index, result in enumerate(checks, start=1)
    )
    blocking_reasons = _unique(
        tuple(reason for item in items for reason in item.blocking_reasons)
        + _hard_fail_blocking_reasons(context, criteria_payload)
    )
    return RiskOwnerChecklist(
        version=CHECKLIST_VERSION,
        source=CHECKLIST_SOURCE,
        can_sign_off=not blocking_reasons,
        items=items,
        blocking_reasons=blocking_reasons,
    )


def _blocked_checklist(blocking_reasons: Sequence[str]) -> RiskOwnerChecklist:
    reasons = _unique(blocking_reasons)
    return RiskOwnerChecklist(
        version=CHECKLIST_VERSION,
        source=CHECKLIST_SOURCE,
        can_sign_off=False,
        blocking_reasons=reasons,
        items=tuple(
            RiskOwnerChecklistItem(
                id=_item_id(index),
                order=index,
                text=text,
                status=BLOCKED,
                blocking_reasons=reasons,
            )
            for index, text in enumerate(EXPECTED_RISK_OWNER_CHECKLIST_ITEMS, start=1)
        ),
    )


def _context_for(request: Mapping[str, Any]) -> _ChecklistContext:
    evidence = _mapping(request.get("evidence")) or request
    risk_owner_review = _mapping(request.get("risk_owner_review") or request.get("risk_review"))
    return _ChecklistContext(
        request=request,
        evidence=evidence,
        risk_owner_review=risk_owner_review,
    )


def _item(index: int, result: _CheckResult) -> RiskOwnerChecklistItem:
    return RiskOwnerChecklistItem(
        id=_item_id(index),
        order=index,
        text=EXPECTED_RISK_OWNER_CHECKLIST_ITEMS[index - 1],
        status=READY if not result.blocking_reasons else BLOCKED,
        evidence_refs=result.evidence_refs,
        blocking_reasons=result.blocking_reasons,
    )


def _item_id(index: int) -> str:
    return f"risk_owner_b3_{index:02d}"


def _check_strategy_artifact_lineage(context: _ChecklistContext) -> _CheckResult:
    return _flag_or_ref(
        context,
        flag_keys=(
            "strategy_artifact_lineage_complete",
            "artifact_lineage_complete",
            "lineage_complete",
        ),
        ref_keys=(
            "strategy_artifact_lineage_ref",
            "artifact_lineage_ref",
            "strategy_lineage_ref",
            "candidate_artifact_ref",
            "candidate_artifact_id",
        ),
        missing_message="strategy / artifact lineage evidence is required",
    )


def _check_minimum_days(
    context: _ChecklistContext,
    criteria: Mapping[str, Any],
    criteria_key: str,
    evidence_key: str,
) -> _CheckResult:
    required_evidence = _mapping(criteria.get("required_evidence"))
    minimum = _number_or_none(required_evidence.get(criteria_key))
    actual_value, actual_path = _first_key(context, (evidence_key,))
    actual_days = _number_or_none(actual_value)
    if minimum is None:
        return _blocked(f"criteria missing numeric required_evidence.{criteria_key}")
    if actual_days is None or actual_days < minimum:
        return _blocked(f"{evidence_key} must be >= {int(minimum)}")
    return _ready(actual_path)


def _check_risk_policy_charter(context: _ChecklistContext) -> _CheckResult:
    return _flag_or_ref(
        context,
        flag_keys=(
            "risk_policy_matches_capital_pool_charter",
            "risk_policy_charter_match",
        ),
        ref_keys=(
            "risk_policy_capital_pool_charter_ref",
            "capital_pool_charter_ref",
            "risk_policy_ref",
        ),
        missing_message="risk policy must match the capital pool charter",
    )


def _check_risk_thresholds(context: _ChecklistContext) -> _CheckResult:
    aggregate_value, aggregate_path = _first_key(
        context,
        ("risk_thresholds_within_policy", "risk_metrics_within_threshold"),
    )
    if aggregate_value is True:
        refs = [aggregate_path]
        metrics_ref = _first_present_ref(
            context,
            ("risk_thresholds_ref", "risk_metrics_ref", "drawdown_liquidity_exposure_ref"),
        )
        if metrics_ref:
            refs.append(metrics_ref)
        return _ready(*refs)

    threshold_payload = _first_mapping(context, "risk_thresholds", "risk_metrics")
    threshold_checks = (
        ("drawdown_within_threshold", "drawdown must be within threshold"),
        ("liquidity_within_threshold", "liquidity must be within threshold"),
        ("exposure_within_threshold", "exposure must be within threshold"),
    )
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    for key, message in threshold_checks:
        value, path = _first_key(context, (key,))
        if value is None and threshold_payload:
            value = threshold_payload.get(key)
            path = f"{threshold_payload['_path']}.{key}"
        if value is True:
            evidence_refs.append(path)
        else:
            blocking_reasons.append(message)
    if blocking_reasons:
        return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))
    return _CheckResult(tuple(evidence_refs), ())


def _check_rollback_target(context: _ChecklistContext) -> _CheckResult:
    return _flag_or_ref(
        context,
        flag_keys=("rollback_target_exists", "rollback_target_approved"),
        ref_keys=(
            "rollback_target_ref",
            "rollback_target_id",
            "deployment_plan_rollback_target_ref",
        ),
        missing_message="rollback target evidence is required",
    )


def _check_kill_switch_demo(context: _ChecklistContext) -> _CheckResult:
    return _flag_or_ref(
        context,
        flag_keys=("kill_switch_demo_complete", "kill_switch_demo_completed"),
        ref_keys=("kill_switch_demo_ref",),
        missing_message="kill-switch demo evidence is required",
    )


def _check_telemetry_audit_postmortem(context: _ChecklistContext) -> _CheckResult:
    evidence_refs: list[str] = []
    blocking_reasons: list[str] = []
    required_paths = (
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


def _check_sponsor_persona_responsibility(context: _ChecklistContext) -> _CheckResult:
    return _flag_or_ref(
        context,
        flag_keys=(
            "sponsor_persona_responsibility_explicit",
            "sponsor_responsibility_explicit",
        ),
        ref_keys=(
            "sponsor_persona_responsibility_ref",
            "sponsor_persona_id",
            "sponsor_persona_ref",
            "sponsor_responsibility_ref",
        ),
        missing_message="sponsor persona responsibility evidence is required",
    )


def _check_conflict_resolution(context: _ChecklistContext) -> _CheckResult:
    log_ref = _first_present_ref(
        context,
        ("conflict_resolution_log_ref", "conflict_log_ref"),
    )
    evidence_refs = [log_ref] if log_ref else []
    blocking_reasons: list[str] = []
    if not log_ref:
        blocking_reasons.append("conflict resolution log evidence is required")

    no_conflict_value, no_conflict_path = _first_key(
        context,
        ("no_open_conflicts", "conflict_resolution_no_open_conflicts"),
    )
    if no_conflict_value is True:
        evidence_refs.append(no_conflict_path)
        return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))

    has_open_value, has_open_path = _first_key(
        context,
        (
            "conflict_resolution_log_has_open_conflict",
            "has_open_conflicts",
        ),
    )
    if has_open_value is False:
        evidence_refs.append(has_open_path)
        return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))
    if has_open_value is True:
        blocking_reasons.append("conflict resolution log has open conflicts")
        return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))

    open_conflicts, open_conflicts_path = _first_key(
        context,
        ("open_conflicts", "conflict_resolution_open_conflicts"),
    )
    open_conflict_count = _open_conflict_count(open_conflicts)
    if open_conflict_count == 0:
        evidence_refs.append(open_conflicts_path)
    elif open_conflict_count is None:
        blocking_reasons.append("conflict resolution log must explicitly show no open conflicts")
    else:
        blocking_reasons.append("conflict resolution log has open conflicts")
    return _CheckResult(tuple(evidence_refs), tuple(blocking_reasons))


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


def _first_mapping(context: _ChecklistContext, *keys: str) -> dict[str, Any]:
    for source_name, source in context.sources:
        for key in keys:
            payload = _mapping(source.get(key))
            if payload:
                payload["_path"] = _path(source_name, key)
                return payload
    return {}


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


def _open_conflict_count(value: Any) -> int | None:
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


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)

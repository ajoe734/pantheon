"""Rollback drill dry-run evidence runner for broker live activation.

Implements the 2026-05-19 blueprint supplement Part B6 evidence shape. The
runner only prepares deterministic rollback request and telemetry previews; it
never dispatches Runtime Manager commands, mutates RuntimeBinding state, calls
broker APIs, writes files, or ingests telemetry.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


EVIDENCE_VERSION = "1.0"
EVIDENCE_SOURCE = "2026-05-19 blueprint supplement Part B6"
VALID_ROLLBACK_ACTION_TYPES = (
    "replace",
    "pause_then_replace",
    "liquidate_then_replace",
)
VALID_DRILL_STAGES = {"paper", "canary", "staging_live", "live"}
STAGES_REQUIRING_BROKER_SUBACCOUNT = {"canary", "staging_live", "live"}
SAFETY_GUARDS = (
    "dry_run_only",
    "no_runtime_manager_dispatch",
    "no_runtime_binding_mutation",
    "no_broker_api_call",
    "no_position_mutation",
    "no_telemetry_ingest",
)


class RollbackDrillDryRunError(ValueError):
    """Raised when rollback drill dry-run evidence fails activation checks."""


@dataclass(frozen=True)
class RollbackDrillDryRunIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class RollbackDrillDryRunEvidence:
    version: str
    source: str
    evidence_id: str
    passed: bool
    promotion_eligible: bool
    dry_run: bool
    drill_stage: str
    action_type: str
    current_binding_id: str | None
    replacement_binding_preview_id: str | None
    capital_pool_id: str | None
    deployment_plan_ref: str | None
    replacement_plan_ref: str | None
    replacement_runtime_id: str | None
    replacement_artifact_id: str | None
    replacement_artifact_version: str | None
    operator_id: str | None
    broker_subaccount_ref: str | None
    planned_runtime_manager_request: Mapping[str, Any]
    telemetry_event_preview: Mapping[str, Any]
    expected_outcome: Mapping[str, Any]
    safety_guards: tuple[str, ...]
    blocking_reasons: tuple[str, ...] = ()
    errors: tuple[RollbackDrillDryRunIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "evidence_id": self.evidence_id,
            "passed": self.passed,
            "promotion_eligible": self.promotion_eligible,
            "dry_run": self.dry_run,
            "drill_stage": self.drill_stage,
            "action_type": self.action_type,
            "current_binding_id": self.current_binding_id,
            "replacement_binding_preview_id": self.replacement_binding_preview_id,
            "capital_pool_id": self.capital_pool_id,
            "deployment_plan_ref": self.deployment_plan_ref,
            "replacement_plan_ref": self.replacement_plan_ref,
            "replacement_runtime_id": self.replacement_runtime_id,
            "replacement_artifact_id": self.replacement_artifact_id,
            "replacement_artifact_version": self.replacement_artifact_version,
            "operator_id": self.operator_id,
            "broker_subaccount_ref": self.broker_subaccount_ref,
            "planned_runtime_manager_request": dict(self.planned_runtime_manager_request),
            "telemetry_event_preview": dict(self.telemetry_event_preview),
            "expected_outcome": dict(self.expected_outcome),
            "safety_guards": list(self.safety_guards),
            "blocking_reasons": list(self.blocking_reasons),
            "errors": [issue.to_dict() for issue in self.errors],
        }


def run_rollback_drill_dry_run(packet: Mapping[str, Any]) -> RollbackDrillDryRunEvidence:
    """Prepare fail-closed rollback drill evidence without live side effects."""

    if not isinstance(packet, Mapping):
        issue = _issue(
            "invalid_dry_run_packet",
            "$",
            "rollback drill dry-run packet must be an object",
        )
        return _evidence(packet={}, context=_Context(), errors=(issue,))

    payload = dict(packet)
    errors: list[RollbackDrillDryRunIssue] = []
    if _live_side_effect_requested(payload):
        errors.append(
            _issue(
                "live_side_effect_requested",
                "$",
                "rollback drill dry-run refuses live dispatch or side-effect flags",
            )
        )

    drill_stage = _normalize_stage(
        _first_string(
            payload,
            ("drill_stage", "demo_stage", "target_stage", "stage", "deployment_stage"),
        )
    )
    if not drill_stage:
        errors.append(
            _issue(
                "missing_drill_stage",
                "drill_stage",
                "rollback drill stage is required",
            )
        )
    elif drill_stage not in VALID_DRILL_STAGES:
        errors.append(
            _issue(
                "invalid_drill_stage",
                "drill_stage",
                "drill stage must be one of: canary, live, paper, staging_live",
            )
        )

    context = _collect_context(payload, drill_stage)
    errors.extend(context.errors)

    planned_request = _planned_runtime_manager_request(context)
    replacement_binding_preview_id = _replacement_binding_preview_id(context, planned_request)
    telemetry_preview = _telemetry_event_preview(
        context,
        planned_request=planned_request,
        replacement_binding_preview_id=replacement_binding_preview_id,
    )
    expected_outcome = _expected_outcome(
        context,
        replacement_binding_preview_id=replacement_binding_preview_id,
    )
    return _evidence(
        packet=payload,
        context=context,
        planned_request=planned_request,
        telemetry_preview=telemetry_preview,
        expected_outcome=expected_outcome,
        replacement_binding_preview_id=replacement_binding_preview_id,
        errors=tuple(errors),
    )


def run_rollback_drill_dry_run_or_raise(
    packet: Mapping[str, Any],
) -> RollbackDrillDryRunEvidence:
    """Prepare evidence and raise a compact fail-closed error on blockers."""

    evidence = run_rollback_drill_dry_run(packet)
    if not evidence.passed:
        raise RollbackDrillDryRunError("; ".join(evidence.blocking_reasons))
    return evidence


@dataclass(frozen=True)
class _Context:
    drill_stage: str = ""
    action_type: str = ""
    current_binding_id: str | None = None
    current_binding_status: str | None = None
    capital_pool_id: str | None = None
    deployment_plan_ref: str | None = None
    replacement_plan_ref: str | None = None
    replacement_deployment_mode: str | None = None
    replacement_allowed_deployment_scope: str | None = None
    replacement_runtime_id: str | None = None
    replacement_artifact_id: str | None = None
    replacement_artifact_version: str | None = None
    replacement_persona_capital_binding_id: str | None = None
    opened_by_artifact_id: str | None = None
    operator_id: str | None = None
    broker_subaccount_ref: str | None = None
    loader_checks_passed: bool | None = None
    errors: tuple[RollbackDrillDryRunIssue, ...] = ()


def _collect_context(packet: Mapping[str, Any], drill_stage: str) -> _Context:
    action_type = _normalize_action_type(
        _first_string(packet, ("action_type", "rollback_action_type"))
    )
    current_binding_id = _first_string(
        packet,
        (
            "current_binding_id",
            "binding_id",
            "runtime_binding_id",
            "live_runtime_binding_ref",
        ),
    )
    current_binding_status = _normalize_token(
        _first_string(packet, ("current_binding_status", "binding_status"))
    )
    capital_pool_id = _first_string(packet, ("capital_pool_id", "capital_pool_ref"))
    deployment_plan_ref = _first_string(
        packet,
        ("deployment_plan_ref", "deployment_plan_id", "live_deployment_plan_ref"),
    )
    replacement_plan_ref = _first_string(
        packet,
        (
            "replacement_plan_ref",
            "replacement_plan_id",
            "fallback_plan_ref",
            "fallback_plan_id",
            "rollback_plan_ref",
        ),
    )
    replacement_deployment_mode = _normalize_stage(
        _first_string(
            packet,
            (
                "replacement_deployment_mode",
                "replacement_target_stage",
                "fallback_target_stage",
                "fallback_deployment_mode",
            ),
        )
    )
    replacement_allowed_deployment_scope = _normalize_stage(
        _first_string(
            packet,
            ("replacement_allowed_deployment_scope", "fallback_allowed_scope"),
        )
    )
    replacement_runtime_id = _first_string(
        packet,
        ("replacement_runtime_id", "fallback_runtime_id", "target_runtime_id"),
    )
    replacement_artifact_id = _first_string(
        packet,
        ("replacement_artifact_id", "fallback_artifact_id", "target_artifact_id"),
    )
    replacement_artifact_version = _first_string(
        packet,
        (
            "replacement_artifact_version",
            "fallback_artifact_version",
            "target_artifact_version",
        ),
    )
    replacement_persona_capital_binding_id = _first_string(
        packet,
        (
            "replacement_persona_capital_binding_id",
            "persona_capital_binding_id",
            "fallback_persona_capital_binding_id",
        ),
    )
    opened_by_artifact_id = _first_string(
        packet,
        ("opened_by_artifact_id", "current_artifact_id", "registry_id"),
    )
    operator_id = _first_string(
        packet,
        ("operator_id", "operator_ref", "operator_principal", "operator_identity_ref"),
    )
    broker_subaccount_ref = _first_string(
        packet,
        (
            "broker_subaccount_ref",
            "target_broker_subaccount_ref",
            "broker_account_ref",
            "broker_account_id",
        ),
    )
    loader_checks_passed = _first_bool(
        packet,
        ("loader_checks_passed", "fallback_loader_checks_passed"),
    )

    errors: list[RollbackDrillDryRunIssue] = []
    required_fields = (
        (
            "missing_current_binding_id",
            "current_binding_id",
            current_binding_id,
            "current RuntimeBinding id is required",
        ),
        (
            "missing_capital_pool_id",
            "capital_pool_id",
            capital_pool_id,
            "capital_pool_id is required",
        ),
        (
            "missing_deployment_plan_ref",
            "deployment_plan_ref",
            deployment_plan_ref,
            "DeploymentPlan evidence link is required",
        ),
        (
            "missing_replacement_plan_ref",
            "replacement_plan_ref",
            replacement_plan_ref,
            "replacement rollback plan evidence link is required",
        ),
        (
            "missing_replacement_deployment_mode",
            "replacement_deployment_mode",
            replacement_deployment_mode,
            "replacement deployment mode is required",
        ),
        (
            "missing_replacement_runtime_id",
            "replacement_runtime_id",
            replacement_runtime_id,
            "replacement runtime id is required",
        ),
        (
            "missing_replacement_artifact_id",
            "replacement_artifact_id",
            replacement_artifact_id,
            "replacement artifact id is required",
        ),
        (
            "missing_replacement_artifact_version",
            "replacement_artifact_version",
            replacement_artifact_version,
            "replacement artifact version is required",
        ),
        (
            "missing_replacement_persona_capital_binding_id",
            "replacement_persona_capital_binding_id",
            replacement_persona_capital_binding_id,
            "replacement persona-capital binding id is required",
        ),
        (
            "missing_opened_by_artifact_id",
            "opened_by_artifact_id",
            opened_by_artifact_id,
            "opened_by_artifact_id lineage evidence is required",
        ),
        (
            "missing_operator_id",
            "operator_id",
            operator_id,
            "operator identity is required",
        ),
    )
    errors.extend(
        _issue(code, path, message)
        for code, path, value, message in required_fields
        if not value
    )
    if not action_type:
        errors.append(
            _issue(
                "missing_action_type",
                "action_type",
                "rollback action_type is required",
            )
        )
    elif action_type not in VALID_ROLLBACK_ACTION_TYPES:
        errors.append(
            _issue(
                "invalid_action_type",
                "action_type",
                "rollback action_type must be one of: liquidate_then_replace, pause_then_replace, replace",
            )
        )
    if not current_binding_status:
        errors.append(
            _issue(
                "missing_current_binding_status",
                "current_binding_status",
                "current RuntimeBinding status evidence is required",
            )
        )
    elif current_binding_status != "active":
        errors.append(
            _issue(
                "current_binding_not_active",
                "current_binding_status",
                "rollback dry-run requires the current RuntimeBinding to be active",
            )
        )
    if drill_stage in STAGES_REQUIRING_BROKER_SUBACCOUNT and not broker_subaccount_ref:
        errors.append(
            _issue(
                "missing_broker_subaccount_ref",
                "broker_subaccount_ref",
                "canary/live rollback drill evidence must name the target broker subaccount",
            )
        )
    if loader_checks_passed is not True:
        errors.append(
            _issue(
                "loader_checks_not_passed",
                "loader_checks_passed",
                "rollback dry-run requires explicit loader_checks_passed=true",
            )
        )
    if _raw_secret_present(packet):
        errors.append(
            _issue(
                "raw_broker_secret_present",
                "$",
                "rollback drill evidence must not contain raw broker secret material",
            )
        )

    return _Context(
        drill_stage=drill_stage,
        action_type=action_type,
        current_binding_id=current_binding_id,
        current_binding_status=current_binding_status,
        capital_pool_id=capital_pool_id,
        deployment_plan_ref=deployment_plan_ref,
        replacement_plan_ref=replacement_plan_ref,
        replacement_deployment_mode=replacement_deployment_mode,
        replacement_allowed_deployment_scope=replacement_allowed_deployment_scope,
        replacement_runtime_id=replacement_runtime_id,
        replacement_artifact_id=replacement_artifact_id,
        replacement_artifact_version=replacement_artifact_version,
        replacement_persona_capital_binding_id=replacement_persona_capital_binding_id,
        opened_by_artifact_id=opened_by_artifact_id,
        operator_id=operator_id,
        broker_subaccount_ref=broker_subaccount_ref,
        loader_checks_passed=loader_checks_passed,
        errors=tuple(errors),
    )


def _planned_runtime_manager_request(context: _Context) -> dict[str, Any]:
    if not context.current_binding_id or not context.action_type:
        return {}
    return {
        "current_binding_id": context.current_binding_id,
        "action_type": context.action_type,
        "replacement_plan_id": context.replacement_plan_ref,
        "replacement_plan_status": "approved",
        "replacement_deployment_mode": context.replacement_deployment_mode,
        "replacement_artifact_id": context.replacement_artifact_id,
        "replacement_artifact_version": context.replacement_artifact_version,
        "replacement_persona_capital_binding_id": (
            context.replacement_persona_capital_binding_id
        ),
        "replacement_persona_capital_binding_status": "active",
        "replacement_allowed_deployment_scope": (
            context.replacement_allowed_deployment_scope
            or context.replacement_deployment_mode
        ),
        "replacement_runtime_id": context.replacement_runtime_id,
        "loader_checks_passed": context.loader_checks_passed is True,
        "opened_by_artifact_id": context.opened_by_artifact_id,
        "dry_run": True,
        "side_effects_allowed": False,
    }


def _telemetry_event_preview(
    context: _Context,
    *,
    planned_request: Mapping[str, Any],
    replacement_binding_preview_id: str | None,
) -> dict[str, Any]:
    if not planned_request or not replacement_binding_preview_id:
        return {}
    return {
        "event_type": "rollback_completed",
        "execution_mode": "dry_run",
        "environment": context.replacement_deployment_mode,
        "deployment_stage": context.replacement_deployment_mode,
        "binding_id": replacement_binding_preview_id,
        "runtime_id": context.replacement_runtime_id,
        "capital_pool_id": context.capital_pool_id,
        "artifact_id": context.replacement_artifact_id,
        "artifact_version": context.replacement_artifact_version,
        "plan_id": context.replacement_plan_ref,
        "persona_capital_binding_id": context.replacement_persona_capital_binding_id,
        "target": {
            "registry_id": context.replacement_artifact_id,
            "artifact_version": context.replacement_artifact_version,
            "promotion_state": context.replacement_deployment_mode,
        },
        "rollback_parent": context.current_binding_id,
        "rollback_action_type": context.action_type,
        "metrics": {"action": "rollback_completed"},
        "metadata": {
            "source_task_id": "BLA-004-V2",
            "drill_mode": "dry_run",
            "side_effects_allowed": False,
        },
    }


def _expected_outcome(
    context: _Context,
    *,
    replacement_binding_preview_id: str | None,
) -> dict[str, Any]:
    if not context.action_type:
        return {}
    if context.action_type == "replace":
        position_handling = "preserve_positions_until_replacement_binding_cutover"
        pre_cutover_status = "active"
    elif context.action_type == "pause_then_replace":
        position_handling = "pause_new_entries_then_preserve_positions_after_stable_state"
        pre_cutover_status = "paused"
    else:
        position_handling = "cancel_orders_and_flatten_positions_before_replacement"
        pre_cutover_status = "liquidated"
    return {
        "side_effects_executed": [],
        "old_binding_status_after": "retired",
        "replacement_binding_preview_id": replacement_binding_preview_id,
        "replacement_binding_status_after": "active",
        "pre_cutover_current_binding_status": pre_cutover_status,
        "position_handling": position_handling,
        "telemetry_cutover": "preview_only_no_ingest",
    }


def _evidence(
    *,
    packet: Mapping[str, Any],
    context: _Context,
    errors: tuple[RollbackDrillDryRunIssue, ...],
    planned_request: Mapping[str, Any] | None = None,
    telemetry_preview: Mapping[str, Any] | None = None,
    expected_outcome: Mapping[str, Any] | None = None,
    replacement_binding_preview_id: str | None = None,
) -> RollbackDrillDryRunEvidence:
    planned_request = dict(planned_request or {})
    telemetry_preview = dict(telemetry_preview or {})
    expected_outcome = dict(expected_outcome or {})
    blocking_reasons = tuple(issue.message for issue in errors)
    passed = not errors
    return RollbackDrillDryRunEvidence(
        version=EVIDENCE_VERSION,
        source=EVIDENCE_SOURCE,
        evidence_id=_evidence_id(
            packet=packet,
            context=context,
            planned_request=planned_request,
            telemetry_preview=telemetry_preview,
            expected_outcome=expected_outcome,
        ),
        passed=passed,
        promotion_eligible=passed,
        dry_run=True,
        drill_stage=context.drill_stage,
        action_type=context.action_type,
        current_binding_id=context.current_binding_id,
        replacement_binding_preview_id=replacement_binding_preview_id,
        capital_pool_id=context.capital_pool_id,
        deployment_plan_ref=context.deployment_plan_ref,
        replacement_plan_ref=context.replacement_plan_ref,
        replacement_runtime_id=context.replacement_runtime_id,
        replacement_artifact_id=context.replacement_artifact_id,
        replacement_artifact_version=context.replacement_artifact_version,
        operator_id=context.operator_id,
        broker_subaccount_ref=context.broker_subaccount_ref,
        planned_runtime_manager_request=planned_request,
        telemetry_event_preview=telemetry_preview,
        expected_outcome=expected_outcome,
        safety_guards=SAFETY_GUARDS,
        blocking_reasons=blocking_reasons,
        errors=errors,
    )


def _evidence_id(
    *,
    packet: Mapping[str, Any],
    context: _Context,
    planned_request: Mapping[str, Any],
    telemetry_preview: Mapping[str, Any],
    expected_outcome: Mapping[str, Any],
) -> str:
    stable_payload = {
        "packet_id": _first_string(packet, ("drill_id", "evidence_id", "packet_id")),
        "drill_stage": context.drill_stage,
        "current_binding_id": context.current_binding_id,
        "capital_pool_id": context.capital_pool_id,
        "deployment_plan_ref": context.deployment_plan_ref,
        "operator_id": context.operator_id,
        "planned_runtime_manager_request": planned_request,
        "telemetry_event_preview": telemetry_preview,
        "expected_outcome": expected_outcome,
    }
    digest = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"rollback-drill-dry-run-{digest[:16]}"


def _replacement_binding_preview_id(
    context: _Context,
    planned_request: Mapping[str, Any],
) -> str | None:
    if not planned_request:
        return None
    stable_payload = {
        "current_binding_id": context.current_binding_id,
        "action_type": context.action_type,
        "replacement_plan_ref": context.replacement_plan_ref,
        "replacement_runtime_id": context.replacement_runtime_id,
        "replacement_artifact_id": context.replacement_artifact_id,
        "replacement_artifact_version": context.replacement_artifact_version,
    }
    digest = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"dry-run-replacement-binding-{digest[:12]}"


def _live_side_effect_requested(packet: Mapping[str, Any]) -> bool:
    for key in (
        "execute",
        "execute_live",
        "dispatch",
        "dispatch_live",
        "side_effects_allowed",
        "mutate_runtime",
        "call_broker_api",
        "ingest_telemetry",
    ):
        if packet.get(key) is True:
            return True
    if packet.get("dry_run") is False or packet.get("validate_only") is False:
        return True
    mode = _normalize_token(_first_string(packet, ("mode", "drill_mode")))
    return mode in {"execute", "live_execute", "dispatch", "live_dispatch"}


def _issue(code: str, path: str, message: str) -> RollbackDrillDryRunIssue:
    return RollbackDrillDryRunIssue(code, path, message)


def _first_string(source: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        elif value is not None and not isinstance(value, (Mapping, Sequence)):
            return str(value)
    return None


def _first_bool(source: Mapping[str, Any], keys: Sequence[str]) -> bool | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            return value
    return None


def _normalize_action_type(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _normalize_stage(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _normalize_token(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _raw_secret_present(value: Any) -> bool:
    secret_keys = {
        "api_key",
        "api_secret",
        "broker_password",
        "password",
        "raw_broker_secret",
        "raw_secret",
        "secret",
        "token",
    }
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in {"raw_broker_secret_present", "raw_secret_present"} and nested is True:
                return True
            if normalized_key in secret_keys and _present(nested):
                return True
            if _raw_secret_present(nested):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_raw_secret_present(item) for item in value)
    return False


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence)):
        return bool(value)
    return True

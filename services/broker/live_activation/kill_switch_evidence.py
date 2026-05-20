"""Kill-switch demo evidence collector for broker live activation.

Implements the 2026-05-19 blueprint supplement Part B5 evidence shape. The
collector only validates and packages Runtime Manager drill responses; it does
not dispatch kill-switch commands, mutate runtime state, or enable broker live
flags.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


EVIDENCE_VERSION = "1.0"
EVIDENCE_SOURCE = "2026-05-19 blueprint supplement Part B5"
DEFAULT_REQUIRED_ACTION_GROUPS = (("pause", "risk_off"),)
STAGES_REQUIRING_BROKER_SUBACCOUNT = {"canary", "staging_live", "live"}
VALID_DEMO_STAGES = {"paper", "canary", "staging_live", "live"}


class KillSwitchDemoEvidenceError(ValueError):
    """Raised when a kill-switch demo packet fails activation evidence checks."""


@dataclass(frozen=True)
class KillSwitchEvidenceIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class KillSwitchDrillEvidence:
    label: str
    action_type: str
    ack_status: str
    command_id: str | None = None
    audit_id: str | None = None
    capital_pool_id: str | None = None
    runtime_binding_id: str | None = None
    runtime_status_after: str | None = None
    safe_mode_after: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "action_type": self.action_type,
            "ack_status": self.ack_status,
            "evidence_refs": list(self.evidence_refs),
        }
        for key, value in (
            ("command_id", self.command_id),
            ("audit_id", self.audit_id),
            ("capital_pool_id", self.capital_pool_id),
            ("runtime_binding_id", self.runtime_binding_id),
            ("runtime_status_after", self.runtime_status_after),
            ("safe_mode_after", self.safe_mode_after),
        ):
            if value:
                payload[key] = value
        return payload


@dataclass(frozen=True)
class KillSwitchDemoEvidence:
    version: str
    source: str
    evidence_id: str
    passed: bool
    promotion_eligible: bool
    demo_stage: str
    deployment_plan_ref: str | None
    runtime_binding_id: str | None
    capital_pool_id: str | None
    operator_id: str | None
    broker_subaccount_ref: str | None
    required_action_groups: tuple[tuple[str, ...], ...]
    covered_actions: tuple[str, ...]
    drills: tuple[KillSwitchDrillEvidence, ...]
    blocking_reasons: tuple[str, ...] = ()
    errors: tuple[KillSwitchEvidenceIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "evidence_id": self.evidence_id,
            "passed": self.passed,
            "promotion_eligible": self.promotion_eligible,
            "demo_stage": self.demo_stage,
            "deployment_plan_ref": self.deployment_plan_ref,
            "runtime_binding_id": self.runtime_binding_id,
            "capital_pool_id": self.capital_pool_id,
            "operator_id": self.operator_id,
            "broker_subaccount_ref": self.broker_subaccount_ref,
            "required_action_groups": [list(group) for group in self.required_action_groups],
            "covered_actions": list(self.covered_actions),
            "blocking_reasons": list(self.blocking_reasons),
            "errors": [issue.to_dict() for issue in self.errors],
            "drills": [drill.to_dict() for drill in self.drills],
        }


def collect_kill_switch_demo_evidence(
    packet: Mapping[str, Any],
    *,
    required_action_groups: Sequence[Sequence[str]] | None = None,
) -> KillSwitchDemoEvidence:
    """Collect deterministic Part B5 evidence from Runtime Manager drill output."""

    if not isinstance(packet, Mapping):
        groups = _normalize_required_action_groups(required_action_groups)
        issue = KillSwitchEvidenceIssue(
            "invalid_demo_packet",
            "$",
            "kill-switch demo packet must be an object",
        )
        return _evidence(
            packet={},
            required_action_groups=groups,
            drills=(),
            errors=(issue,),
        )

    payload = dict(packet)
    groups = _normalize_required_action_groups(required_action_groups)
    errors: list[KillSwitchEvidenceIssue] = []

    demo_stage = _normalize_stage(_first_string(payload, ("demo_stage", "stage", "drill_stage", "target_stage")))
    if not demo_stage:
        errors.append(
            KillSwitchEvidenceIssue(
                "missing_demo_stage",
                "demo_stage",
                "kill-switch demo stage is required",
            )
        )
    elif demo_stage not in VALID_DEMO_STAGES:
        errors.append(
            KillSwitchEvidenceIssue(
                "invalid_demo_stage",
                "demo_stage",
                "demo stage must be one of: canary, live, paper, staging_live",
            )
        )

    context = _collect_context(payload, demo_stage)
    errors.extend(context.errors)

    raw_drills = _drill_payloads(payload)
    if not raw_drills:
        errors.append(
            KillSwitchEvidenceIssue(
                "missing_kill_switch_drill",
                "drills",
                "at least one Runtime Manager kill-switch drill response is required",
            )
        )

    drill_results: list[KillSwitchDrillEvidence] = []
    for index, drill_payload in enumerate(raw_drills, start=1):
        drill, drill_errors = _collect_drill(
            drill_payload,
            index=index,
            expected=context,
        )
        drill_results.append(drill)
        errors.extend(drill_errors)

    covered_actions = tuple(
        sorted(
            {
                drill.action_type
                for drill in drill_results
                if drill.action_type and drill.ack_status == "acknowledged"
            }
        )
    )
    errors.extend(_validate_action_coverage(groups, covered_actions))

    return _evidence(
        packet=payload,
        required_action_groups=groups,
        drills=tuple(drill_results),
        errors=tuple(errors),
        covered_actions=covered_actions,
        context=context,
    )


def collect_kill_switch_demo_evidence_or_raise(
    packet: Mapping[str, Any],
    *,
    required_action_groups: Sequence[Sequence[str]] | None = None,
) -> KillSwitchDemoEvidence:
    """Collect evidence and raise a compact fail-closed error on blockers."""

    evidence = collect_kill_switch_demo_evidence(
        packet,
        required_action_groups=required_action_groups,
    )
    if not evidence.passed:
        raise KillSwitchDemoEvidenceError("; ".join(evidence.blocking_reasons))
    return evidence


@dataclass(frozen=True)
class _Context:
    demo_stage: str
    deployment_plan_ref: str | None
    runtime_binding_id: str | None
    capital_pool_id: str | None
    operator_id: str | None
    broker_subaccount_ref: str | None
    errors: tuple[KillSwitchEvidenceIssue, ...] = ()


def _collect_context(packet: Mapping[str, Any], demo_stage: str) -> _Context:
    deployment_plan_ref = _first_string(
        packet,
        ("deployment_plan_ref", "deployment_plan_id", "live_deployment_plan_ref"),
    )
    runtime_binding_id = _first_string(
        packet,
        ("runtime_binding_id", "runtime_binding_ref", "binding_id", "live_runtime_binding_ref"),
    )
    capital_pool_id = _first_string(packet, ("capital_pool_id", "capital_pool_ref"))
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

    required_fields = (
        ("missing_deployment_plan_ref", "deployment_plan_ref", deployment_plan_ref, "DeploymentPlan evidence link is required"),
        ("missing_runtime_binding_id", "runtime_binding_id", runtime_binding_id, "RuntimeBinding evidence link is required"),
        ("missing_capital_pool_id", "capital_pool_id", capital_pool_id, "capital_pool_id is required"),
        ("missing_operator_id", "operator_id", operator_id, "operator identity is required"),
    )
    errors = [
        KillSwitchEvidenceIssue(code, path, message)
        for code, path, value, message in required_fields
        if not value
    ]
    if demo_stage in STAGES_REQUIRING_BROKER_SUBACCOUNT and not broker_subaccount_ref:
        errors.append(
            KillSwitchEvidenceIssue(
                "missing_broker_subaccount_ref",
                "broker_subaccount_ref",
                "canary/live kill-switch drill evidence must name the target broker subaccount",
            )
        )
    if _raw_secret_present(packet):
        errors.append(
            KillSwitchEvidenceIssue(
                "raw_broker_secret_present",
                "$",
                "kill-switch demo evidence must not contain raw broker secret material",
            )
        )

    return _Context(
        demo_stage=demo_stage,
        deployment_plan_ref=deployment_plan_ref,
        runtime_binding_id=runtime_binding_id,
        capital_pool_id=capital_pool_id,
        operator_id=operator_id,
        broker_subaccount_ref=broker_subaccount_ref,
        errors=tuple(errors),
    )


def _collect_drill(
    drill_payload: Mapping[str, Any],
    *,
    index: int,
    expected: _Context,
) -> tuple[KillSwitchDrillEvidence, tuple[KillSwitchEvidenceIssue, ...]]:
    response = _response_payload(drill_payload)
    command = _mapping(response.get("command"))
    audit_entry = _mapping(response.get("audit_entry"))
    telemetry_ack = _mapping(response.get("telemetry_ack"))
    binding_action = _mapping(response.get("binding_action"))
    binding = _mapping(binding_action.get("binding"))
    replacement_binding = _mapping(binding_action.get("replacement_binding"))

    path = f"drills[{index - 1}]"
    errors: list[KillSwitchEvidenceIssue] = []
    label = _first_string(drill_payload, ("label", "name", "action_label")) or f"drill_{index:02d}"
    action_type = _first_string(command, ("action_type",)) or _first_string(telemetry_ack, ("action_type",))
    ack_status = (_first_string(telemetry_ack, ("ack_status",)) or "missing").lower()
    command_id = _first_string(command, ("command_id",)) or _first_string(telemetry_ack, ("command_id",))
    audit_id = _first_string(audit_entry, ("audit_id",)) or _first_string(telemetry_ack, ("audit_id",))
    capital_pool_id = (
        _first_string(command, ("capital_pool_id",))
        or _first_string(telemetry_ack, ("capital_pool_id",))
    )
    runtime_binding_id = _first_string(
        telemetry_ack,
        ("runtime_binding_id", "binding_id"),
    ) or _first_string(command, ("binding_id",)) or _first_string(
        replacement_binding or binding,
        ("binding_id", "runtime_binding_id"),
    )
    runtime_status_after = _first_string(
        telemetry_ack,
        ("runtime_status_after", "binding_status_after"),
    ) or _first_string(replacement_binding or binding, ("status",))
    safe_mode_after = (
        _first_string(response, ("safe_mode_after",))
        or _first_string(telemetry_ack, ("safe_mode_after", "safe_mode_state"))
    )

    if not command:
        errors.append(_issue("missing_command", f"{path}.command", "Runtime Manager command evidence is required"))
    if not audit_entry:
        errors.append(_issue("missing_audit_entry", f"{path}.audit_entry", "kill-switch audit entry evidence is required"))
    if not telemetry_ack:
        errors.append(_issue("missing_telemetry_ack", f"{path}.telemetry_ack", "telemetry_ack evidence is required"))
    if not command_id:
        errors.append(_issue("missing_command_id", f"{path}.command.command_id", "command_id is required"))
    if not audit_id:
        errors.append(_issue("missing_audit_id", f"{path}.audit_entry.audit_id", "audit_id is required"))
    if not action_type:
        errors.append(_issue("missing_action_type", f"{path}.command.action_type", "kill-switch action_type is required"))
    if not safe_mode_after:
        errors.append(_issue("missing_safe_mode_after", f"{path}.safe_mode_after", "safe_mode_after is required"))

    if ack_status != "acknowledged":
        errors.append(
            _issue(
                "telemetry_ack_not_acknowledged",
                f"{path}.telemetry_ack.ack_status",
                "telemetry_ack.ack_status must be acknowledged for promotion evidence",
            )
        )
    if telemetry_ack.get("fail_closed") is True:
        errors.append(
            _issue(
                "telemetry_ack_fail_closed",
                f"{path}.telemetry_ack.fail_closed",
                "fail_closed telemetry ack is audit-only evidence and blocks activation",
            )
        )
    if telemetry_ack.get("ack_received") is False:
        errors.append(
            _issue(
                "telemetry_ack_not_received",
                f"{path}.telemetry_ack.ack_received",
                "telemetry ack must be received",
            )
        )
    if telemetry_ack.get("runtime_state_recorded") is False:
        errors.append(
            _issue(
                "runtime_state_not_recorded",
                f"{path}.telemetry_ack.runtime_state_recorded",
                "Runtime Manager runtime follow-through must be recorded",
            )
        )
    if telemetry_ack.get("capital_state_recorded") is False:
        errors.append(
            _issue(
                "capital_state_not_recorded",
                f"{path}.telemetry_ack.capital_state_recorded",
                "Runtime Manager capital follow-through must be recorded",
            )
        )

    errors.extend(
        _validate_consistency(
            path=path,
            command=command,
            audit_entry=audit_entry,
            telemetry_ack=telemetry_ack,
            command_id=command_id,
            audit_id=audit_id,
            action_type=action_type,
            capital_pool_id=capital_pool_id,
            runtime_binding_id=runtime_binding_id,
            expected=expected,
        )
    )

    drill = KillSwitchDrillEvidence(
        label=label,
        action_type=action_type or "",
        ack_status=ack_status,
        command_id=command_id,
        audit_id=audit_id,
        capital_pool_id=capital_pool_id,
        runtime_binding_id=runtime_binding_id,
        runtime_status_after=runtime_status_after,
        safe_mode_after=safe_mode_after,
        evidence_refs=tuple(
            ref
            for ref in (
                command_id,
                audit_id,
                runtime_binding_id,
                capital_pool_id,
                safe_mode_after,
            )
            if ref
        ),
    )
    return drill, tuple(errors)


def _validate_consistency(
    *,
    path: str,
    command: Mapping[str, Any],
    audit_entry: Mapping[str, Any],
    telemetry_ack: Mapping[str, Any],
    command_id: str | None,
    audit_id: str | None,
    action_type: str | None,
    capital_pool_id: str | None,
    runtime_binding_id: str | None,
    expected: _Context,
) -> tuple[KillSwitchEvidenceIssue, ...]:
    errors: list[KillSwitchEvidenceIssue] = []
    if command_id and _first_string(telemetry_ack, ("command_id",)) not in {None, command_id}:
        errors.append(_issue("command_id_mismatch", f"{path}.telemetry_ack.command_id", "telemetry ack command_id must match command.command_id"))
    if audit_id and _first_string(telemetry_ack, ("audit_id",)) not in {None, audit_id}:
        errors.append(_issue("audit_id_mismatch", f"{path}.telemetry_ack.audit_id", "telemetry ack audit_id must match audit_entry.audit_id"))
    if audit_id and _first_string(audit_entry, ("audit_id",)) not in {None, audit_id}:
        errors.append(_issue("audit_id_mismatch", f"{path}.audit_entry.audit_id", "audit_entry audit_id must match telemetry_ack.audit_id"))
    if action_type and _first_string(telemetry_ack, ("action_type",)) not in {None, action_type}:
        errors.append(_issue("action_type_mismatch", f"{path}.telemetry_ack.action_type", "telemetry ack action_type must match command.action_type"))

    if expected.capital_pool_id and capital_pool_id != expected.capital_pool_id:
        errors.append(_issue("capital_pool_mismatch", f"{path}.command.capital_pool_id", "drill capital_pool_id must match the demo packet capital_pool_id"))

    ack_binding = _first_string(telemetry_ack, ("runtime_binding_id", "binding_id"))
    command_binding = _first_string(command, ("binding_id",))
    if expected.runtime_binding_id and expected.runtime_binding_id not in {runtime_binding_id, ack_binding, command_binding}:
        errors.append(_issue("runtime_binding_mismatch", f"{path}.telemetry_ack.runtime_binding_id", "drill runtime binding must match the demo packet RuntimeBinding"))
    if expected.runtime_binding_id and ack_binding and ack_binding != expected.runtime_binding_id:
        errors.append(_issue("runtime_binding_mismatch", f"{path}.telemetry_ack.runtime_binding_id", "telemetry ack runtime binding must match the demo packet RuntimeBinding"))
    if expected.runtime_binding_id and command_binding and command_binding != expected.runtime_binding_id:
        errors.append(_issue("runtime_binding_mismatch", f"{path}.command.binding_id", "command runtime binding must match the demo packet RuntimeBinding"))

    return tuple(errors)


def _validate_action_coverage(
    required_action_groups: tuple[tuple[str, ...], ...],
    covered_actions: tuple[str, ...],
) -> tuple[KillSwitchEvidenceIssue, ...]:
    errors: list[KillSwitchEvidenceIssue] = []
    covered = set(covered_actions)
    for index, group in enumerate(required_action_groups, start=1):
        if not covered.intersection(group):
            errors.append(
                KillSwitchEvidenceIssue(
                    "required_action_not_covered",
                    f"required_action_groups[{index - 1}]",
                    f"kill-switch drill must cover one of: {', '.join(group)}",
                )
            )
    return tuple(errors)


def _evidence(
    *,
    packet: Mapping[str, Any],
    required_action_groups: tuple[tuple[str, ...], ...],
    drills: tuple[KillSwitchDrillEvidence, ...],
    errors: tuple[KillSwitchEvidenceIssue, ...],
    covered_actions: tuple[str, ...] = (),
    context: _Context | None = None,
) -> KillSwitchDemoEvidence:
    if context is None:
        context = _Context("", None, None, None, None, None)
    blocking_reasons = tuple(issue.message for issue in errors)
    passed = not errors
    return KillSwitchDemoEvidence(
        version=EVIDENCE_VERSION,
        source=EVIDENCE_SOURCE,
        evidence_id=_evidence_id(packet, drills, required_action_groups),
        passed=passed,
        promotion_eligible=passed,
        demo_stage=context.demo_stage,
        deployment_plan_ref=context.deployment_plan_ref,
        runtime_binding_id=context.runtime_binding_id,
        capital_pool_id=context.capital_pool_id,
        operator_id=context.operator_id,
        broker_subaccount_ref=context.broker_subaccount_ref,
        required_action_groups=required_action_groups,
        covered_actions=covered_actions,
        drills=drills,
        blocking_reasons=blocking_reasons,
        errors=errors,
    )


def _evidence_id(
    packet: Mapping[str, Any],
    drills: Sequence[KillSwitchDrillEvidence],
    required_action_groups: tuple[tuple[str, ...], ...],
) -> str:
    stable_payload = {
        "demo_id": _first_string(packet, ("demo_id", "evidence_id", "packet_id")),
        "demo_stage": _normalize_stage(_first_string(packet, ("demo_stage", "stage", "drill_stage", "target_stage"))),
        "deployment_plan_ref": _first_string(packet, ("deployment_plan_ref", "deployment_plan_id", "live_deployment_plan_ref")),
        "runtime_binding_id": _first_string(packet, ("runtime_binding_id", "runtime_binding_ref", "binding_id", "live_runtime_binding_ref")),
        "capital_pool_id": _first_string(packet, ("capital_pool_id", "capital_pool_ref")),
        "operator_id": _first_string(packet, ("operator_id", "operator_ref", "operator_principal", "operator_identity_ref")),
        "required_action_groups": required_action_groups,
        "drills": [drill.to_dict() for drill in drills],
    }
    digest = hashlib.sha256(json.dumps(stable_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"kill-switch-demo-{digest[:16]}"


def _normalize_required_action_groups(
    groups: Sequence[Sequence[str]] | None,
) -> tuple[tuple[str, ...], ...]:
    raw_groups = groups if groups is not None else DEFAULT_REQUIRED_ACTION_GROUPS
    normalized: list[tuple[str, ...]] = []
    for group in raw_groups:
        actions = tuple(
            sorted(
                {
                    str(action).strip().lower()
                    for action in group
                    if str(action).strip()
                }
            )
        )
        if actions:
            normalized.append(actions)
    return tuple(normalized)


def _drill_payloads(packet: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    for key in ("drills", "kill_switch_drills", "dispatches"):
        value = packet.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return tuple(item for item in value if isinstance(item, Mapping))
    response = _response_payload(packet)
    if response:
        return (packet,)
    return ()


def _response_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("dispatch_response", "kill_switch_response", "runtime_manager_response", "response"):
        payload = _mapping(value.get(key))
        if payload:
            return payload
    if "command" in value or "telemetry_ack" in value or "audit_entry" in value:
        return dict(value)
    return {}


def _issue(code: str, path: str, message: str) -> KillSwitchEvidenceIssue:
    return KillSwitchEvidenceIssue(code, path, message)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


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


def _normalize_stage(value: str | None) -> str:
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
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence)):
        return bool(value)
    return True

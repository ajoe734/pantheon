from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


MATRIX_VERSION = "bff_degraded_mode_matrix.v1"
MATRIX_SOURCE = "2026-05-19 supplement Part D4; docs/bff/bff_ha_topology.md#fail-closed-boundaries"


class CommandGuard(str, Enum):
    REJECT_ALL = "reject_all"
    REJECT_REGISTRY_GOVERNANCE = "reject_registry_governance"
    REJECT_RUNTIME_LIFECYCLE = "reject_runtime_lifecycle"
    BLOCK_HEALTH_DEPENDENT = "block_health_dependent"
    GUARD_REALTIME_DEPENDENT = "guard_realtime_dependent"


@dataclass(frozen=True)
class DegradedModeRow:
    key: str
    dependency: str
    error_code: str
    message: str
    ui_state: str
    command_guard: CommandGuard
    affected_command_groups: tuple[str, ...]
    operator_guidance: str
    secondary_control_path: str | None = None
    surface_state: str = "unavailable"
    retryable: bool = True
    http_status: int = 503
    strict_mode: bool = True
    fallback_allowed: bool = False


@dataclass(frozen=True)
class CommandGuardDecision:
    allowed: bool
    reason: str
    command_type: str
    command_groups: tuple[str, ...]
    blocked_by: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    ui_state: str | None = None
    response: dict[str, Any] | None = None


DEGRADED_MODE_MATRIX: tuple[DegradedModeRow, ...] = (
    DegradedModeRow(
        key="auth_oidc_jwks_unavailable",
        dependency="Auth / OIDC / JWKS",
        error_code="AUTH_UNAVAILABLE",
        message="Authentication material is unavailable; BFF cannot verify operator identity.",
        ui_state="auth_unavailable",
        command_guard=CommandGuard.REJECT_ALL,
        affected_command_groups=("all",),
        operator_guidance="Re-authenticate when identity service recovers; use protected admin paths only with pre-established credentials.",
        secondary_control_path="protected admin API or CLI with independent credentials",
    ),
    DegradedModeRow(
        key="idempotency_store_unavailable",
        dependency="Shared idempotency store",
        error_code="IDEMPOTENCY_UNAVAILABLE",
        message="Shared idempotency store is unavailable; command dedupe cannot be guaranteed.",
        ui_state="command_submission_unavailable",
        command_guard=CommandGuard.REJECT_ALL,
        affected_command_groups=("all",),
        operator_guidance="Do not dispatch BFF commands until idempotency storage recovers.",
    ),
    DegradedModeRow(
        key="audit_handoff_unavailable",
        dependency="Audit handoff store",
        error_code="AUDIT_UNAVAILABLE",
        message="Audit handoff is unavailable; BFF cannot preserve a command audit trail.",
        ui_state="command_submission_unavailable",
        command_guard=CommandGuard.REJECT_ALL,
        affected_command_groups=("all",),
        operator_guidance="Do not dispatch BFF commands until audit handoff recovers.",
    ),
    DegradedModeRow(
        key="registry_governance_unavailable",
        dependency="Registry / Governance",
        error_code="REGISTRY_GOVERNANCE_UNAVAILABLE",
        message="Registry or governance backend is unavailable; governed state is unverifiable.",
        ui_state="governance_unavailable",
        command_guard=CommandGuard.REJECT_REGISTRY_GOVERNANCE,
        affected_command_groups=(
            "approval",
            "capital",
            "deployment",
            "evolution_governance",
            "persona",
            "strategy",
        ),
        operator_guidance="Disable approval, deployment, capital, strategy, and persona commands.",
    ),
    DegradedModeRow(
        key="runtime_manager_unavailable",
        dependency="Runtime Manager",
        error_code="RUNTIME_MANAGER_UNAVAILABLE",
        message="Runtime Manager is unavailable; runtime lifecycle state is unverifiable.",
        ui_state="runtime_control_unavailable",
        command_guard=CommandGuard.REJECT_RUNTIME_LIFECYCLE,
        affected_command_groups=("kill_switch", "rollback", "runtime_lifecycle"),
        operator_guidance="Disable BFF runtime lifecycle commands and route operators to the protected runtime admin path.",
        secondary_control_path="runtime-manager protected admin endpoint",
    ),
    DegradedModeRow(
        key="telemetry_incident_unavailable",
        dependency="Telemetry / Incident",
        error_code="TELEMETRY_UNAVAILABLE",
        message="Telemetry or incident backend is unavailable; runtime health is unverifiable.",
        ui_state="runtime_health_unverifiable",
        command_guard=CommandGuard.BLOCK_HEALTH_DEPENDENT,
        affected_command_groups=("incident_response", "runtime_health_dependent"),
        operator_guidance="Block high-risk commands that depend on fresh runtime health; show stale health metadata.",
        surface_state="stale",
    ),
    DegradedModeRow(
        key="sse_fanout_unavailable",
        dependency="SSE event source / fanout",
        error_code="SSE_FANOUT_UNAVAILABLE",
        message="Realtime fanout is unavailable or the replay cursor cannot be satisfied.",
        ui_state="realtime_degraded",
        command_guard=CommandGuard.GUARD_REALTIME_DEPENDENT,
        affected_command_groups=("realtime_dependent",),
        operator_guidance="Keep screens in read-only or guarded mode until realtime replay recovers.",
        surface_state="stale_realtime",
    ),
)


_ROWS_BY_KEY = {row.key: row for row in DEGRADED_MODE_MATRIX}
_ROWS_BY_CODE = {row.error_code: row for row in DEGRADED_MODE_MATRIX}

_COMMAND_GROUP_COMMANDS: Mapping[str, frozenset[str]] = {
    "approval": frozenset(
        {
            "approve_deployment",
            "reject_deployment",
            "approve_evolution_decision",
            "reject_evolution_decision",
            "approve_mutation",
            "reject_mutation",
        }
    ),
    "capital": frozenset(
        {
            "bind_capital_pool",
            "rebalance_capital_pool",
            "update_capital_pool",
        }
    ),
    "deployment": frozenset(
        {
            "advance_deployment",
            "approve_deployment",
            "cancel_deployment",
            "reject_deployment",
            "submit_deployment",
        }
    ),
    "evolution_governance": frozenset(
        {
            "approve_evolution_decision",
            "approve_mutation",
            "reject_evolution_decision",
            "reject_mutation",
        }
    ),
    "incident_response": frozenset(
        {
            "acknowledge_alert",
            "open_incident",
            "resolve_incident",
        }
    ),
    "kill_switch": frozenset(
        {
            "activate_kill_switch",
            "deactivate_kill_switch",
            "kill_switch_activate",
            "kill_switch_deactivate",
        }
    ),
    "persona": frozenset(
        {
            "bind_persona",
            "create_persona",
            "start_persona_session",
            "stop_persona_session",
            "update_persona",
        }
    ),
    "rollback": frozenset(
        {
            "execute_rollback",
            "rollback_runtime",
        }
    ),
    "runtime_lifecycle": frozenset(
        {
            "deploy_runtime",
            "execute_rollback",
            "pause_runtime",
            "replace_runtime",
            "resume_runtime",
            "rollback_runtime",
        }
    ),
    "strategy": frozenset(
        {
            "activate_strategy",
            "retire_strategy",
            "update_strategy_spec",
        }
    ),
}


def matrix_as_dict() -> dict[str, Any]:
    return {
        "schema_version": MATRIX_VERSION,
        "source": MATRIX_SOURCE,
        "rows": [row_to_dict(row) for row in DEGRADED_MODE_MATRIX],
    }


def row_to_dict(row: DegradedModeRow) -> dict[str, Any]:
    return {
        "key": row.key,
        "dependency": row.dependency,
        "http_status": row.http_status,
        "error_code": row.error_code,
        "message": row.message,
        "ui_state": row.ui_state,
        "surface_state": row.surface_state,
        "command_guard": row.command_guard.value,
        "affected_command_groups": list(row.affected_command_groups),
        "operator_guidance": row.operator_guidance,
        "secondary_control_path": row.secondary_control_path,
        "retryable": row.retryable,
        "strict_mode": row.strict_mode,
        "fallback_allowed": row.fallback_allowed,
    }


def get_degraded_mode_row(failure: str) -> DegradedModeRow:
    normalized = _normalize_failure(failure)
    row = _ROWS_BY_KEY.get(normalized) or _ROWS_BY_CODE.get(normalized.upper())
    if row is None:
        raise ValueError(f"Unknown degraded mode failure: {failure}")
    return row


def build_typed_503_response(
    failure: str,
    *,
    detail: str | None = None,
    request_id: str | None = None,
    surface_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    row = get_degraded_mode_row(failure)
    payload: dict[str, Any] = {
        "error": {
            "code": row.error_code,
            "message": row.message,
            "dependency": row.dependency,
            "retryable": row.retryable,
            "strict_mode": row.strict_mode,
            "fallback_allowed": row.fallback_allowed,
        },
        "meta": {
            "schema_version": MATRIX_VERSION,
            "source": MATRIX_SOURCE,
            "degraded": True,
            "ui_state": row.ui_state,
            "surface_state": row.surface_state,
            "command_guard": row.command_guard.value,
            "affected_command_groups": list(row.affected_command_groups),
            "operator_guidance": row.operator_guidance,
            "secondary_control_path": row.secondary_control_path,
            "surfaces": {
                surface_id or row.key: {
                    "state": row.surface_state,
                    "dependency": row.dependency,
                    "error_code": row.error_code,
                    "strict_mode": row.strict_mode,
                    "fallback_allowed": row.fallback_allowed,
                }
            },
        },
    }
    if detail:
        payload["error"]["detail"] = detail
    if request_id:
        payload["meta"]["request_id"] = request_id
    return row.http_status, payload


def guard_command(
    command_type: str,
    active_failures: Iterable[str],
    *,
    requires_fresh_runtime_health: bool = False,
    requires_realtime: bool = False,
) -> CommandGuardDecision:
    command_groups = command_groups_for(command_type)
    rows = tuple(get_degraded_mode_row(failure) for failure in active_failures)

    if not rows:
        return CommandGuardDecision(
            allowed=True,
            reason="No active degraded mode failures.",
            command_type=command_type,
            command_groups=command_groups,
        )

    if not command_groups:
        row = rows[0]
        return _blocked_decision(
            row,
            command_type,
            command_groups,
            "Command type is not classified for degraded mode; strict mode blocks silent fallback.",
        )

    for row in rows:
        if _row_blocks_command(
            row,
            command_groups,
            requires_fresh_runtime_health=requires_fresh_runtime_health,
            requires_realtime=requires_realtime,
        ):
            return _blocked_decision(
                row,
                command_type,
                command_groups,
                f"{row.dependency} degradation applies guard {row.command_guard.value}.",
            )

    return CommandGuardDecision(
        allowed=True,
        reason="Active degraded mode failures do not affect this command.",
        command_type=command_type,
        command_groups=command_groups,
    )


def command_groups_for(command_type: str) -> tuple[str, ...]:
    normalized = _normalize_command(command_type)
    groups = sorted(
        group
        for group, command_names in _COMMAND_GROUP_COMMANDS.items()
        if normalized in command_names
    )
    return tuple(groups)


def validate_degraded_mode_matrix() -> None:
    if len(DEGRADED_MODE_MATRIX) != 7:
        raise ValueError("degraded mode matrix must contain exactly seven rows")

    error_codes = [row.error_code for row in DEGRADED_MODE_MATRIX]
    if len(error_codes) != len(set(error_codes)):
        raise ValueError("degraded mode error codes must be unique")

    for row in DEGRADED_MODE_MATRIX:
        if row.http_status != 503:
            raise ValueError(f"{row.key} must return HTTP 503")
        if not row.strict_mode or row.fallback_allowed:
            raise ValueError(f"{row.key} must be strict and must not allow fallback")
        if not row.ui_state or not row.command_guard or not row.affected_command_groups:
            raise ValueError(f"{row.key} must define UI state and command guard")


def _blocked_decision(
    row: DegradedModeRow,
    command_type: str,
    command_groups: tuple[str, ...],
    reason: str,
) -> CommandGuardDecision:
    status, response = build_typed_503_response(row.key, detail=reason)
    return CommandGuardDecision(
        allowed=False,
        reason=reason,
        command_type=command_type,
        command_groups=command_groups,
        blocked_by=row.key,
        http_status=status,
        error_code=row.error_code,
        ui_state=row.ui_state,
        response=response,
    )


def _row_blocks_command(
    row: DegradedModeRow,
    command_groups: tuple[str, ...],
    *,
    requires_fresh_runtime_health: bool,
    requires_realtime: bool,
) -> bool:
    if row.command_guard == CommandGuard.REJECT_ALL:
        return True

    command_group_set = set(command_groups)
    affected = set(row.affected_command_groups)

    if row.command_guard in {
        CommandGuard.REJECT_REGISTRY_GOVERNANCE,
        CommandGuard.REJECT_RUNTIME_LIFECYCLE,
    }:
        return bool(command_group_set & affected)

    if row.command_guard == CommandGuard.BLOCK_HEALTH_DEPENDENT:
        return requires_fresh_runtime_health or bool(command_group_set & affected)

    if row.command_guard == CommandGuard.GUARD_REALTIME_DEPENDENT:
        return requires_realtime or bool(command_group_set & affected)

    return True


def _normalize_failure(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _normalize_command(command_type: str) -> str:
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", command_type.strip())
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", snake_case).lower()
    return normalized.strip("_")

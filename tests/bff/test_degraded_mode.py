from __future__ import annotations

import pytest

from services.bff.ha.degraded_mode import (
    DEGRADED_MODE_MATRIX,
    build_typed_503_response,
    guard_command,
    matrix_as_dict,
    validate_degraded_mode_matrix,
)


def test_degraded_mode_matrix_contains_seven_strict_rows() -> None:
    validate_degraded_mode_matrix()

    matrix = matrix_as_dict()

    assert matrix["schema_version"] == "bff_degraded_mode_matrix.v1"
    assert len(matrix["rows"]) == 7
    assert {row["http_status"] for row in matrix["rows"]} == {503}
    assert {row["fallback_allowed"] for row in matrix["rows"]} == {False}
    assert {row["strict_mode"] for row in matrix["rows"]} == {True}
    assert {row["error_code"] for row in matrix["rows"]} == {
        "AUDIT_UNAVAILABLE",
        "AUTH_UNAVAILABLE",
        "IDEMPOTENCY_UNAVAILABLE",
        "REGISTRY_GOVERNANCE_UNAVAILABLE",
        "RUNTIME_MANAGER_UNAVAILABLE",
        "SSE_FANOUT_UNAVAILABLE",
        "TELEMETRY_UNAVAILABLE",
    }


def test_typed_503_response_has_ui_state_and_no_silent_data_fallback() -> None:
    status, payload = build_typed_503_response(
        "runtime_manager_unavailable",
        detail="runtime-manager timed out",
        request_id="req-123",
        surface_id="RT-03",
    )

    assert status == 503
    assert "data" not in payload
    assert payload["error"] == {
        "code": "RUNTIME_MANAGER_UNAVAILABLE",
        "message": "Runtime Manager is unavailable; runtime lifecycle state is unverifiable.",
        "dependency": "Runtime Manager",
        "retryable": True,
        "strict_mode": True,
        "fallback_allowed": False,
        "detail": "runtime-manager timed out",
    }
    assert payload["meta"]["ui_state"] == "runtime_control_unavailable"
    assert payload["meta"]["command_guard"] == "reject_runtime_lifecycle"
    assert payload["meta"]["request_id"] == "req-123"
    assert payload["meta"]["surfaces"]["RT-03"] == {
        "state": "unavailable",
        "dependency": "Runtime Manager",
        "error_code": "RUNTIME_MANAGER_UNAVAILABLE",
        "strict_mode": True,
        "fallback_allowed": False,
    }


def test_command_guard_allows_command_without_active_degradation() -> None:
    decision = guard_command("PauseRuntime", [])

    assert decision.allowed is True
    assert decision.http_status is None
    assert decision.error_code is None
    assert decision.command_groups == ("runtime_lifecycle",)


def test_command_guard_blocks_runtime_lifecycle_when_runtime_manager_is_down() -> None:
    decision = guard_command("PauseRuntime", ["runtime_manager_unavailable"])

    assert decision.allowed is False
    assert decision.http_status == 503
    assert decision.error_code == "RUNTIME_MANAGER_UNAVAILABLE"
    assert decision.blocked_by == "runtime_manager_unavailable"
    assert decision.ui_state == "runtime_control_unavailable"
    assert decision.response is not None
    assert decision.response["error"]["fallback_allowed"] is False


def test_command_guard_allows_unaffected_runtime_command_when_governance_is_down() -> None:
    decision = guard_command("PauseRuntime", ["registry_governance_unavailable"])

    assert decision.allowed is True
    assert decision.reason == "Active degraded mode failures do not affect this command."


def test_command_guard_blocks_health_dependent_command_when_health_is_unverifiable() -> None:
    decision = guard_command(
        "ApproveDeployment",
        ["TELEMETRY_UNAVAILABLE"],
        requires_fresh_runtime_health=True,
    )

    assert decision.allowed is False
    assert decision.http_status == 503
    assert decision.error_code == "TELEMETRY_UNAVAILABLE"
    assert decision.ui_state == "runtime_health_unverifiable"


def test_strict_guard_blocks_unknown_command_during_degradation() -> None:
    decision = guard_command("InventedFixtureFallback", ["sse_fanout_unavailable"])

    assert decision.allowed is False
    assert decision.http_status == 503
    assert decision.error_code == "SSE_FANOUT_UNAVAILABLE"
    assert "strict mode blocks silent fallback" in decision.reason


def test_unknown_failure_is_rejected_instead_of_falling_back() -> None:
    with pytest.raises(ValueError, match="Unknown degraded mode failure"):
        build_typed_503_response("fixture_seed_fallback")


def test_matrix_rows_are_immutable_contract_objects() -> None:
    with pytest.raises(AttributeError):
        DEGRADED_MODE_MATRIX[0].fallback_allowed = True  # type: ignore[misc]

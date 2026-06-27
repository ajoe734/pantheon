"""
LOOP-AUTO-DEP-002: Tests for the idempotent runtime-manager dispatch adapter.

Acceptance criteria verified here:
1. Approved immutable plan dispatch creates or verifies one binding.
2. Duplicate dispatch does not duplicate bindings (idempotency via binding_id).
3. Runtime-manager failures return retryable or terminal saga state.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from runtime_manager_dispatch_adapter import (
    DispatchOutcome,
    DispatchResult,
    dispatch_to_runtime_manager,
    _build_deploy_request,
    _classify_client_error,
    _classify_runtime_manager_error,
)

try:
    from runtime_manager_client import RuntimeManagerClientError
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime-manager"))
    from runtime_manager_client import RuntimeManagerClientError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_saga(*, binding_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "saga_id": "deployment-saga-plan-001",
        "plan_id": "plan-001",
        "approval_decision_id": "adec-001",
        "strategy_id": "strat-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "capital_pool_id": "pool-paper-001",
        "current_stage": "none",
        "target_stage": "paper",
        "runtime_action": "deploy_new_binding",
        "status": "awaiting_binding",
        "current_step": "binding_requested",
        "trace_id": "trace-001",
        "created_at": "2026-06-27T00:00:00Z",
        "updated_at": "2026-06-27T00:00:00Z",
        "binding_id": binding_id,
    }


def _make_deploy_context(**overrides: Any) -> Dict[str, Any]:
    ctx = {
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
        "plan_status": "approved",
    }
    ctx.update(overrides)
    return ctx


def _make_binding(binding_id: str = "rb-abc123") -> Dict[str, Any]:
    return {
        "binding_id": binding_id,
        "runtime_id": "rt-001",
        "capital_pool_id": "pool-paper-001",
        "artifact_id": "artifact-001",
        "artifact_version": "v1.0.0",
        "deployment_mode": "paper",
        "status": "active",
    }


def _make_client(
    *,
    deploy_return: Optional[Dict[str, Any]] = None,
    deploy_raise: Optional[Exception] = None,
    get_return: Optional[Dict[str, Any]] = None,
    get_raise: Optional[Exception] = None,
) -> MagicMock:
    client = MagicMock()
    if deploy_raise is not None:
        client.deploy.side_effect = deploy_raise
    else:
        client.deploy.return_value = deploy_return or _make_binding()
    if get_raise is not None:
        client.get.side_effect = get_raise
    else:
        client.get.return_value = get_return
    return client


# ---------------------------------------------------------------------------
# Acceptance criterion 1: approved plan creates one binding
# ---------------------------------------------------------------------------

class TestNewDispatch:
    """Saga has no binding_id yet — deploy() should be called once."""

    def test_success_returns_binding_id_and_success_outcome(self):
        binding = _make_binding("rb-newbinding")
        client = _make_client(deploy_return=binding)
        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.SUCCESS
        assert result.binding_id == "rb-newbinding"
        assert result.binding == binding
        assert result.idempotent_replay is False
        assert result.error_message is None
        client.deploy.assert_called_once()

    def test_deploy_request_uses_saga_and_context_fields(self):
        client = _make_client(deploy_return=_make_binding())
        saga = _make_saga()
        ctx = _make_deploy_context(runtime_id="rt-custom-001", idempotency_key="idem-key")
        dispatch_to_runtime_manager(saga=saga, deploy_context=ctx, client=client)

        call_args = client.deploy.call_args[0][0]
        assert call_args["plan_id"] == saga["plan_id"]
        assert call_args["artifact_id"] == saga["artifact_id"]
        assert call_args["artifact_version"] == saga["artifact_version"]
        assert call_args["capital_pool_id"] == saga["capital_pool_id"]
        assert call_args["target_stage"] == saga["target_stage"]
        assert call_args["strategy_id"] == saga["strategy_id"]
        assert call_args["persona_capital_binding_id"] == "pcb-001"
        assert call_args["persona_capital_binding_status"] == "active"
        assert call_args["allowed_deployment_scope"] == "paper"
        assert call_args["loader_checks_passed"] is True
        assert call_args["plan_status"] == "approved"
        assert call_args["runtime_id"] == "rt-custom-001"
        assert call_args["idempotency_key"] == "idem-key"

    def test_get_is_not_called_for_new_dispatch(self):
        client = _make_client(deploy_return=_make_binding())
        dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )
        client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Acceptance criterion 2: duplicate dispatch does not duplicate bindings
# ---------------------------------------------------------------------------

class TestIdempotentReplay:
    """Saga already has a binding_id — verify it, never call deploy()."""

    def test_existing_binding_returns_success_without_calling_deploy(self):
        binding = _make_binding("rb-existing")
        client = _make_client(get_return=binding)
        saga = _make_saga(binding_id="rb-existing")

        result = dispatch_to_runtime_manager(
            saga=saga,
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.SUCCESS
        assert result.binding_id == "rb-existing"
        assert result.binding == binding
        assert result.idempotent_replay is True
        client.deploy.assert_not_called()
        client.get.assert_called_once_with("rb-existing")

    def test_binding_not_found_is_terminal_inconsistency(self):
        client = _make_client(get_return=None)
        saga = _make_saga(binding_id="rb-ghost")

        result = dispatch_to_runtime_manager(
            saga=saga,
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.TERMINAL_ERROR
        assert result.binding_id == "rb-ghost"
        assert "inconsistent" in (result.error_message or "").lower()
        assert result.error_code == "BINDING_NOT_FOUND_AFTER_SAGA_RECORDED"
        client.deploy.assert_not_called()

    def test_network_error_during_verify_is_retryable(self):
        exc = RuntimeManagerClientError(
            "runtime-manager unavailable", error_code="RUNTIME_MANAGER_UNAVAILABLE"
        )
        client = _make_client(get_raise=exc)
        saga = _make_saga(binding_id="rb-existing")

        result = dispatch_to_runtime_manager(
            saga=saga,
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.RETRYABLE_ERROR

    def test_http_500_during_verify_is_retryable(self):
        exc = RuntimeManagerClientError("server error", status_code=500)
        client = _make_client(get_raise=exc)
        saga = _make_saga(binding_id="rb-existing")

        result = dispatch_to_runtime_manager(
            saga=saga,
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.RETRYABLE_ERROR

    def test_http_404_during_new_deploy_is_terminal(self):
        # 404 on deploy() itself (not on get()) means the referenced resource was wrong
        exc = RuntimeManagerClientError("not found", status_code=404)
        client = _make_client(deploy_raise=exc)

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.TERMINAL_ERROR


# ---------------------------------------------------------------------------
# Acceptance criterion 3: runtime-manager failures return retryable or terminal
# ---------------------------------------------------------------------------

class TestErrorClassification:
    """Verify correct retryable vs terminal classification for each failure mode."""

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429])
    def test_retryable_http_codes(self, status_code):
        exc = RuntimeManagerClientError("transient", status_code=status_code)
        client = _make_client(deploy_raise=exc)

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.RETRYABLE_ERROR, (
            f"Expected retryable for HTTP {status_code}"
        )

    @pytest.mark.parametrize("status_code", [400, 401, 403, 422, 409])
    def test_terminal_http_4xx_codes(self, status_code):
        exc = RuntimeManagerClientError("client error", status_code=status_code)
        client = _make_client(deploy_raise=exc)

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.TERMINAL_ERROR, (
            f"Expected terminal for HTTP {status_code}"
        )

    def test_unavailable_error_code_is_retryable(self):
        exc = RuntimeManagerClientError(
            "runtime-manager unavailable", error_code="RUNTIME_MANAGER_UNAVAILABLE"
        )
        client = _make_client(deploy_raise=exc)

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.RETRYABLE_ERROR

    @pytest.mark.parametrize("message_fragment", [
        "must be approved",
        "loader_checks_passed must be True",
        "allowed_deployment_scope",
        "not 'active'",
        "activation is blocked",
    ])
    def test_terminal_keyword_in_local_error_message(self, message_fragment):
        exc = RuntimeManagerClientError(message_fragment)
        client = _make_client(deploy_raise=exc)

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.TERMINAL_ERROR, (
            f"Expected terminal for error containing '{message_fragment}'"
        )

    def test_generic_exception_defaults_to_retryable(self):
        client = _make_client(deploy_raise=RuntimeError("unexpected timeout"))

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.RETRYABLE_ERROR

    def test_terminal_local_error_contains_pre_condition_message(self):
        exc = RuntimeManagerClientError("Deploy is blocked until loader checks are passed")
        client = _make_client(deploy_raise=exc)

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.TERMINAL_ERROR
        assert result.error_message is not None

    def test_result_carries_error_code_on_failure(self):
        exc = RuntimeManagerClientError("fail", status_code=503)
        client = _make_client(deploy_raise=exc)

        result = dispatch_to_runtime_manager(
            saga=_make_saga(),
            deploy_context=_make_deploy_context(),
            client=client,
        )

        assert result.outcome == DispatchOutcome.RETRYABLE_ERROR
        assert result.error_code is not None


# ---------------------------------------------------------------------------
# _build_deploy_request unit tests
# ---------------------------------------------------------------------------

class TestBuildDeployRequest:
    def test_required_fields_from_saga(self):
        saga = _make_saga()
        ctx = _make_deploy_context()
        req = _build_deploy_request(saga=saga, deploy_context=ctx)

        assert req["plan_id"] == saga["plan_id"]
        assert req["artifact_id"] == saga["artifact_id"]
        assert req["artifact_version"] == saga["artifact_version"]
        assert req["capital_pool_id"] == saga["capital_pool_id"]
        assert req["target_stage"] == saga["target_stage"]
        assert req["strategy_id"] == saga["strategy_id"]

    def test_required_fields_from_context(self):
        saga = _make_saga()
        ctx = _make_deploy_context()
        req = _build_deploy_request(saga=saga, deploy_context=ctx)

        assert req["persona_capital_binding_id"] == ctx["persona_capital_binding_id"]
        assert req["persona_capital_binding_status"] == ctx["persona_capital_binding_status"]
        assert req["allowed_deployment_scope"] == ctx["allowed_deployment_scope"]
        assert req["loader_checks_passed"] is True
        assert req["plan_status"] == "approved"

    def test_optional_fields_are_forwarded(self):
        saga = _make_saga()
        ctx = _make_deploy_context(
            runtime_id="rt-override",
            idempotency_key="ik-001",
            rollback_parent="rb-old",
            rollback_action_type="replace",
            metadata={"source": "test"},
        )
        req = _build_deploy_request(saga=saga, deploy_context=ctx)

        assert req["runtime_id"] == "rt-override"
        assert req["idempotency_key"] == "ik-001"
        assert req["rollback_parent"] == "rb-old"
        assert req["rollback_action_type"] == "replace"
        assert req["metadata"] == {"source": "test"}

    def test_plan_status_defaults_to_approved_when_missing(self):
        saga = _make_saga()
        ctx = _make_deploy_context()
        ctx.pop("plan_status", None)
        req = _build_deploy_request(saga=saga, deploy_context=ctx)

        assert req["plan_status"] == "approved"


# ---------------------------------------------------------------------------
# DispatchResult helper methods
# ---------------------------------------------------------------------------

class TestDispatchResult:
    def test_succeeded_helper(self):
        assert DispatchResult(outcome=DispatchOutcome.SUCCESS).succeeded()
        assert not DispatchResult(outcome=DispatchOutcome.RETRYABLE_ERROR).succeeded()

    def test_is_retryable_helper(self):
        assert DispatchResult(outcome=DispatchOutcome.RETRYABLE_ERROR).is_retryable()
        assert not DispatchResult(outcome=DispatchOutcome.TERMINAL_ERROR).is_retryable()

    def test_is_terminal_helper(self):
        assert DispatchResult(outcome=DispatchOutcome.TERMINAL_ERROR).is_terminal()
        assert not DispatchResult(outcome=DispatchOutcome.SUCCESS).is_terminal()

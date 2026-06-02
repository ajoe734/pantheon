"""Regression tests: kernel controls are disabled in user mode.

Acceptance gate for ASST-USER-001:
- product default mode is user
- user mode has no shell, repo, raw-log, repair, or broker capability
- user mode receives only BFF-curated context
- kernel mode is rejected when PANTHEON_ASSISTANT_KERNEL_ENABLED is unset/false
"""
from __future__ import annotations

import os
import sys

import pytest

BFF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BFF_DIR not in sys.path:
    sys.path.insert(0, BFF_DIR)

from assistant.mode_policy import (  # noqa: E402
    PRODUCT_DEFAULT_MODE,
    ModePolicyViolation,
    assert_kernel_allowed,
    command_classes_for_mode,
    kernel_sessions_enabled,
    mode_allows_command_broker,
    user_mode_capability_summary,
)
from assistant.models import AssistantContextPackRequest, AssistantMode  # noqa: E402
from assistant.context_composer import (  # noqa: E402
    AssistantContextPolicyError,
    KERNEL_ONLY_SOURCES,
    _enforce_mode_policy,
)


# ---------------------------------------------------------------------------
# Product default mode
# ---------------------------------------------------------------------------

class TestProductDefaultMode:
    def test_product_default_is_user(self):
        assert PRODUCT_DEFAULT_MODE == AssistantMode.USER

    def test_context_pack_request_defaults_to_user(self):
        req = AssistantContextPackRequest()
        assert req.mode == AssistantMode.USER


# ---------------------------------------------------------------------------
# User mode command broker — no capability
# ---------------------------------------------------------------------------

class TestUserModeCommandBroker:
    def test_user_mode_has_no_command_classes(self):
        assert command_classes_for_mode(AssistantMode.USER) == []

    def test_user_mode_does_not_allow_command_broker(self):
        assert mode_allows_command_broker(AssistantMode.USER) is False

    def test_user_mode_capability_summary_disables_all_kernel_controls(self):
        summary = user_mode_capability_summary()
        assert summary["command_broker"] is False
        assert summary["shell"] is False
        assert summary["repo"] is False
        assert summary["raw_logs"] is False
        assert summary["repair"] is False
        assert summary["provider_session_access"] is False
        assert summary["allowed_command_classes"] == []


# ---------------------------------------------------------------------------
# User mode context policy — no kernel-only sources
# ---------------------------------------------------------------------------

class TestUserModeContextPolicy:
    def test_user_mode_rejects_kernel_only_sources(self):
        for source in KERNEL_ONLY_SOURCES:
            with pytest.raises(AssistantContextPolicyError) as exc_info:
                _enforce_mode_policy(AssistantMode.USER, [source])
            assert source in exc_info.value.denied_sources

    def test_user_mode_accepts_bff_curated_sources(self):
        bff_curated = ["ui", "control_room", "jobs", "alerts", "audit", "recent_sse"]
        # should not raise
        _enforce_mode_policy(AssistantMode.USER, bff_curated)

    def test_user_mode_rejects_repo_status(self):
        with pytest.raises(AssistantContextPolicyError):
            _enforce_mode_policy(AssistantMode.USER, ["repo_status"])

    def test_user_mode_rejects_sanitized_logs(self):
        with pytest.raises(AssistantContextPolicyError):
            _enforce_mode_policy(AssistantMode.USER, ["sanitized_logs"])

    def test_user_mode_rejects_health_probes(self):
        with pytest.raises(AssistantContextPolicyError):
            _enforce_mode_policy(AssistantMode.USER, ["health_probes"])

    def test_kernel_debug_allows_kernel_only_sources(self):
        # kernel modes must NOT raise for kernel-only sources
        for source in KERNEL_ONLY_SOURCES:
            _enforce_mode_policy(AssistantMode.KERNEL_DEBUG, [source])


# ---------------------------------------------------------------------------
# Kernel enabled gate
# ---------------------------------------------------------------------------

class TestKernelEnabledGate:
    def test_kernel_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", raising=False)
        assert kernel_sessions_enabled() is False

    def test_kernel_enabled_when_flag_true(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        assert kernel_sessions_enabled() is True

    def test_kernel_enabled_when_flag_1(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "1")
        assert kernel_sessions_enabled() is True

    def test_kernel_disabled_when_flag_false(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "false")
        assert kernel_sessions_enabled() is False

    def test_assert_kernel_allowed_passes_for_user_mode_even_when_disabled(self, monkeypatch):
        monkeypatch.delenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", raising=False)
        # user mode is always allowed
        assert_kernel_allowed(AssistantMode.USER)

    def test_assert_kernel_allowed_rejects_kernel_observe_when_disabled(self, monkeypatch):
        monkeypatch.delenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", raising=False)
        with pytest.raises(ModePolicyViolation) as exc_info:
            assert_kernel_allowed(AssistantMode.KERNEL_OBSERVE)
        assert exc_info.value.field == "mode"

    def test_assert_kernel_allowed_rejects_kernel_debug_when_disabled(self, monkeypatch):
        monkeypatch.delenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", raising=False)
        with pytest.raises(ModePolicyViolation):
            assert_kernel_allowed(AssistantMode.KERNEL_DEBUG)

    def test_assert_kernel_allowed_rejects_kernel_repair_when_disabled(self, monkeypatch):
        monkeypatch.delenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", raising=False)
        with pytest.raises(ModePolicyViolation):
            assert_kernel_allowed(AssistantMode.KERNEL_REPAIR)

    def test_assert_kernel_allowed_passes_kernel_debug_when_enabled(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
        # should not raise (capability/reason/ttl checks happen later)
        assert_kernel_allowed(AssistantMode.KERNEL_DEBUG)


# ---------------------------------------------------------------------------
# Kernel modes still require capability when kernel is enabled
# ---------------------------------------------------------------------------

class TestKernelCapabilityGateWhenEnabled:
    """When kernel is enabled, kernel sessions still require capability, reason, TTL."""

    @pytest.fixture(autouse=True)
    def _enable_kernel(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")

    def test_kernel_observe_requires_capability(self):
        from assistant.mode_policy import validate_session_request
        with pytest.raises(ModePolicyViolation) as exc_info:
            validate_session_request(
                mode=AssistantMode.KERNEL_OBSERVE,
                reason="debug",
                ttl_seconds=1800,
                capabilities=[],  # no kernel capability
            )
        assert exc_info.value.field == "capabilities"

    def test_kernel_debug_requires_reason(self):
        from assistant.mode_policy import validate_session_request
        with pytest.raises(ModePolicyViolation) as exc_info:
            validate_session_request(
                mode=AssistantMode.KERNEL_DEBUG,
                reason="",
                ttl_seconds=1800,
                capabilities=["assistant.kernel.debug"],
            )
        assert exc_info.value.field == "reason"

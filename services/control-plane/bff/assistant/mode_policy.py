"""Assistant mode policy for session creation and capability gating.

Kernel sessions require an explicit reason, a positive TTL, and at least one
matching capability.  User sessions are created without those requirements but
still carry a TTL when the caller supplies one.

The default kernel TTL is 1800 s (30 minutes).  Callers may request a longer
TTL up to MAX_KERNEL_TTL_SECONDS.  User-mode sessions default to no TTL.

Product default mode is user.  Kernel mode requires the env flag
PANTHEON_ASSISTANT_KERNEL_ENABLED=true to be set; if unset or false, any
request for a kernel mode is rejected before capability checks run.
"""
from __future__ import annotations

import os
from typing import Any, List, Optional

from .models import AssistantMode
from .transcript_store import (
    AssistantSession,
    SessionRejectedError,
    SessionStatus,
    build_session,
    _is_expired,
)


PRODUCT_DEFAULT_MODE = AssistantMode.USER

DEFAULT_KERNEL_TTL_SECONDS = 1800
MAX_KERNEL_TTL_SECONDS = 7200
KERNEL_CAPABILITY_PREFIX = "assistant.kernel"

KERNEL_MODES = frozenset({
    AssistantMode.KERNEL_OBSERVE,
    AssistantMode.KERNEL_DEBUG,
    AssistantMode.KERNEL_REPAIR,
})

COMMAND_CLASS_HEALTH_PROBE = "health_probe"
COMMAND_CLASS_REPO_STATUS = "repo_status"
COMMAND_CLASS_CODE_SEARCH = "code_search"
COMMAND_CLASS_FILE_SLICE = "file_slice"
COMMAND_CLASS_TEST_RUN = "test_run"
COMMAND_CLASS_LOG_READ = "log_read"

COMMAND_CLASSES_BY_MODE = {
    AssistantMode.USER: tuple(),
    AssistantMode.KERNEL_OBSERVE: (
        COMMAND_CLASS_HEALTH_PROBE,
        COMMAND_CLASS_REPO_STATUS,
    ),
    AssistantMode.KERNEL_DEBUG: (
        COMMAND_CLASS_HEALTH_PROBE,
        COMMAND_CLASS_REPO_STATUS,
        COMMAND_CLASS_CODE_SEARCH,
        COMMAND_CLASS_FILE_SLICE,
        COMMAND_CLASS_TEST_RUN,
        COMMAND_CLASS_LOG_READ,
    ),
    # Repair write/restart commands are intentionally outside ASST-KERNEL-006;
    # ASST-KERNEL-007 owns task worktree and repo mutation guardrails.
    AssistantMode.KERNEL_REPAIR: (
        COMMAND_CLASS_HEALTH_PROBE,
        COMMAND_CLASS_REPO_STATUS,
        COMMAND_CLASS_CODE_SEARCH,
        COMMAND_CLASS_FILE_SLICE,
        COMMAND_CLASS_TEST_RUN,
        COMMAND_CLASS_LOG_READ,
    ),
}


def kernel_sessions_enabled() -> bool:
    """Return True only when the kernel feature flag is explicitly enabled."""
    return os.environ.get("PANTHEON_ASSISTANT_KERNEL_ENABLED", "").lower() in ("1", "true", "yes")


def assert_kernel_allowed(mode: AssistantMode | str) -> None:
    """Raise ModePolicyViolation if a kernel mode is requested while kernel is disabled.

    This gate runs before capability checks.  When PANTHEON_ASSISTANT_KERNEL_ENABLED is
    not set or is false, the product default is user mode and kernel sessions are
    unconditionally rejected regardless of the caller's capabilities.
    """
    resolved = mode if isinstance(mode, AssistantMode) else AssistantMode(str(mode))
    if resolved in KERNEL_MODES and not kernel_sessions_enabled():
        raise ModePolicyViolation(
            "Kernel sessions are disabled.  Set PANTHEON_ASSISTANT_KERNEL_ENABLED=true "
            "to allow kernel mode.  Product default is user mode.",
            field="mode",
        )


def user_mode_capability_summary() -> dict:
    """Return a stable summary of what user mode can and cannot do.

    Intended for responses and tests that need a machine-readable capability snapshot.
    """
    return {
        "mode": AssistantMode.USER.value,
        "context": "bff_curated_only",
        "command_broker": False,
        "shell": False,
        "repo": False,
        "raw_logs": False,
        "repair": False,
        "provider_session_access": False,
        "allowed_command_classes": [],
    }


class ModePolicyViolation(ValueError):
    """Raised when a session creation request violates mode policy."""

    def __init__(self, message: str, *, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.field = field


def _has_kernel_capability(capabilities: List[str]) -> bool:
    return any(cap.startswith(KERNEL_CAPABILITY_PREFIX) for cap in capabilities)


def command_classes_for_mode(mode: AssistantMode | str) -> List[str]:
    """Return command broker classes visible for the assistant mode.

    This is a BFF/UI policy helper only.  OpenClaw adapter policy remains the
    enforcement boundary for command authorization.
    """
    try:
        resolved = mode if isinstance(mode, AssistantMode) else AssistantMode(str(mode))
    except ValueError:
        return []
    return list(COMMAND_CLASSES_BY_MODE.get(resolved, tuple()))


def mode_allows_command_broker(mode: AssistantMode | str) -> bool:
    return bool(command_classes_for_mode(mode))


def validate_session_request(
    *,
    mode: AssistantMode,
    reason: Optional[str],
    ttl_seconds: Optional[int],
    capabilities: List[str],
) -> None:
    """Raise ModePolicyViolation if the session request does not satisfy mode rules."""
    if mode not in KERNEL_MODES:
        return

    if not reason or not reason.strip():
        raise ModePolicyViolation(
            "Kernel sessions require a non-empty reason.",
            field="reason",
        )

    resolved_ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_KERNEL_TTL_SECONDS
    if resolved_ttl <= 0:
        raise ModePolicyViolation(
            "Kernel sessions require a positive TTL.",
            field="ttl_seconds",
        )
    if resolved_ttl > MAX_KERNEL_TTL_SECONDS:
        raise ModePolicyViolation(
            f"Requested TTL {resolved_ttl}s exceeds the maximum allowed "
            f"{MAX_KERNEL_TTL_SECONDS}s for kernel sessions.",
            field="ttl_seconds",
        )

    if not _has_kernel_capability(capabilities):
        raise ModePolicyViolation(
            f"Kernel sessions require at least one capability matching "
            f"{KERNEL_CAPABILITY_PREFIX!r}. Actor capabilities: {capabilities!r}",
            field="capabilities",
        )


def create_session(
    *,
    mode: AssistantMode,
    actor: Any,
    reason: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> AssistantSession:
    """Validate mode policy then build a new session (not yet persisted).

    The actor object is expected to expose:
    - ``operator_id`` (str)
    - ``roles`` (list[str])
    - ``claims`` (dict) which may contain ``capabilities`` (list[str] or str)
    """
    actor_id = str(getattr(actor, "operator_id", None) or "unknown")
    roles: List[str] = list(getattr(actor, "roles", []) or [])
    claims = getattr(actor, "claims", {}) or {}
    if not isinstance(claims, dict):
        claims = {}
    raw_caps = claims.get("capabilities") or claims.get("capability") or []
    if isinstance(raw_caps, str):
        import re
        raw_caps = [c.strip() for c in re.split(r"[\s,]+", raw_caps) if c.strip()]
    capabilities: List[str] = [str(c) for c in (raw_caps if isinstance(raw_caps, list) else [])]

    resolved_ttl = ttl_seconds
    if mode in KERNEL_MODES and resolved_ttl is None:
        resolved_ttl = DEFAULT_KERNEL_TTL_SECONDS

    validate_session_request(
        mode=mode,
        reason=reason,
        ttl_seconds=resolved_ttl,
        capabilities=capabilities,
    )

    return build_session(
        mode=mode,
        actor_id=actor_id,
        roles=roles,
        capabilities=capabilities,
        reason=reason,
        ttl_seconds=resolved_ttl,
    )


def check_session_active(session: AssistantSession) -> None:
    """Raise SessionRejectedError if the session is not active.

    This is a lightweight policy check — callers that need atomic expiry
    handling should read the session from the store (which lazily marks it
    expired) before calling this.
    """
    if session.status == SessionStatus.REVOKED:
        raise SessionRejectedError(
            f"Session {session.session_id!r} is revoked."
        )
    if session.status == SessionStatus.EXPIRED or _is_expired(session):
        raise SessionRejectedError(
            f"Session {session.session_id!r} is expired."
        )


def session_to_dict(session: AssistantSession) -> dict:
    """Serialize a session to a JSON-safe dict."""
    return {
        "session_id": session.session_id,
        "mode": session.mode.value,
        "actor_id": session.actor_id,
        "roles": list(session.roles),
        "capabilities": list(session.capabilities),
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "status": session.status.value,
        "reason": session.reason,
        "ttl_seconds": session.ttl_seconds,
        "context_pack_id": session.context_pack_id,
        "provider_run_id": session.provider_run_id,
        "audit_refs": list(session.audit_refs),
        "revoked_at": session.revoked_at,
        "revoke_reason": session.revoke_reason,
    }


def turn_to_dict(turn: Any) -> dict:
    """Serialize a TranscriptTurn to a JSON-safe dict."""
    return {
        "turn_id": turn.turn_id,
        "session_id": turn.session_id,
        "role": turn.role.value if hasattr(turn.role, "value") else str(turn.role),
        "content": turn.content,
        "created_at": turn.created_at,
        "context_pack_id": turn.context_pack_id,
        "provider_run_id": turn.provider_run_id,
        "source_refs": list(turn.source_refs or []),
    }

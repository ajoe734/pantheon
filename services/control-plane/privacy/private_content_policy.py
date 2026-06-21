"""Private-content authorization and redaction policy (AG-DES-SW-PRIV-001 §3.5–§3.8).

This module defines read-authorization rules, retention policy enforcement,
and the redaction requirement for workshop events.  It never holds or
touches plaintext content.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Literal, Optional

from .private_content_models import (
    RETENTION_CLASSES,
    RETENTION_DAYS,
    PrivateContentAccessDenied,
    PrivateContentExpired,
    PrivateContentRedactionUnavailable,
    RetentionClass,
)


# ---------------------------------------------------------------------------
# §3.5  Retention policy
# ---------------------------------------------------------------------------

RETENTION_POLICY_VERSION = "1.0"


def validate_retention_class(retention_class: str) -> None:
    """Raise ValueError if *retention_class* is not a recognised value (§3.5)."""
    if retention_class not in RETENTION_CLASSES:
        raise ValueError(
            f"Unknown retention class {retention_class!r}. "
            f"Must be one of: {sorted(RETENTION_CLASSES)}"
        )


def is_legal_hold(retention_class: RetentionClass) -> bool:
    """Return True when this object must not be deleted by owner request (§3.5)."""
    return retention_class == "legal_hold"


def is_expired(expires_at: Optional[datetime], now: datetime) -> bool:
    """Return True when *expires_at* is in the past relative to *now* (§3.5)."""
    if expires_at is None:
        return False
    # Normalise to UTC for comparison.
    utc_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    utc_exp = (
        expires_at
        if expires_at.tzinfo is not None
        else expires_at.replace(tzinfo=timezone.utc)
    )
    return utc_exp <= utc_now


# ---------------------------------------------------------------------------
# §3.6  Read authorisation
# ---------------------------------------------------------------------------

ActorKind = Literal["owner", "bound_servant", "break_glass"]


@dataclasses.dataclass(frozen=True)
class ReadAuthorisation:
    """Authorisation decision returned by authorise_decrypt (§3.6)."""
    allowed: bool
    actor_kind: ActorKind
    deny_reason: Optional[str] = None


def authorise_decrypt(
    *,
    tenant_id: str,
    owner_user_id: str,
    request_tenant_id: str,
    request_user_id: str,
    actor_kind: ActorKind,
    bound_session_owner_user_id: Optional[str] = None,
    private_content_ref: str,
    expires_at: Optional[datetime],
    now: datetime,
) -> ReadAuthorisation:
    """Evaluate whether a decrypt request is permitted (§3.6).

    Rules:
    - Tenant scope must match exactly.
    - Owner actor: request_user_id must equal owner_user_id.
    - Bound servant: the session's bound owner must equal owner_user_id.
    - Break-glass: allowed unconditionally within the same tenant; caller
      must separately write an audit event with actor_ref and purpose.
    - Management / institutional / cross-user: denied.
    - Expired content: always denied with PrivateContentExpired.

    Returns a ReadAuthorisation.  Callers must raise on allowed=False.
    """
    if request_tenant_id != tenant_id:
        return ReadAuthorisation(
            allowed=False,
            actor_kind=actor_kind,
            deny_reason="tenant_mismatch",
        )

    if is_expired(expires_at, now):
        return ReadAuthorisation(
            allowed=False,
            actor_kind=actor_kind,
            deny_reason="expired",
        )

    if actor_kind == "owner":
        if request_user_id == owner_user_id:
            return ReadAuthorisation(allowed=True, actor_kind="owner")
        return ReadAuthorisation(
            allowed=False,
            actor_kind=actor_kind,
            deny_reason="not_owner",
        )

    if actor_kind == "bound_servant":
        if bound_session_owner_user_id == owner_user_id:
            return ReadAuthorisation(allowed=True, actor_kind="bound_servant")
        return ReadAuthorisation(
            allowed=False,
            actor_kind=actor_kind,
            deny_reason="servant_owner_mismatch",
        )

    if actor_kind == "break_glass":
        # Narrow compliance access: caller is responsible for separate audit.
        return ReadAuthorisation(allowed=True, actor_kind="break_glass")

    return ReadAuthorisation(
        allowed=False,
        actor_kind=actor_kind,
        deny_reason="actor_kind_not_permitted",
    )


def enforce_read_authorisation(auth: ReadAuthorisation, expires_at: Optional[datetime], now: datetime) -> None:
    """Raise the appropriate domain exception when a ReadAuthorisation is denied."""
    if auth.allowed:
        return
    if auth.deny_reason == "expired" or is_expired(expires_at, now):
        raise PrivateContentExpired(
            "Private content has expired and is no longer accessible."
        )
    raise PrivateContentAccessDenied(
        "Caller is not authorised to decrypt this private content."
    )


# ---------------------------------------------------------------------------
# §3.8  Redaction requirement
# ---------------------------------------------------------------------------

REDACTION_POLICY_VERSION = "1.0"


@dataclasses.dataclass(frozen=True)
class RedactionResult:
    """Output of the redaction step required before a workshop event is committed (§3.8)."""
    redacted_summary: str
    redaction_policy_version: str
    redaction_status: Literal["completed"]


def validate_redaction_result(result: Optional[RedactionResult]) -> None:
    """Raise PrivateContentRedactionUnavailable when *result* is absent or incomplete (§3.8).

    The BFF must call this before committing any message event.
    If redaction is unavailable, this function raises 503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE.
    Do not persist a message event containing only a raw-content ref without a valid redacted summary.
    """
    if result is None:
        raise PrivateContentRedactionUnavailable(
            "Redaction service unavailable; refusing to persist message event without redacted summary."
        )
    if result.redaction_status != "completed":
        raise PrivateContentRedactionUnavailable(
            "Redaction did not complete; refusing to persist message event."
        )
    if not result.redacted_summary:
        raise PrivateContentRedactionUnavailable(
            "Redacted summary is empty; refusing to persist message event."
        )
    if not result.redaction_policy_version:
        raise PrivateContentRedactionUnavailable(
            "Redaction policy version missing; refusing to persist message event."
        )


# ---------------------------------------------------------------------------
# §3.7  Logging and transport guards
# ---------------------------------------------------------------------------

def assert_no_raw_content_in_payload(payload: dict) -> None:
    """Guard: raise ValueError if a suspicious raw-content field appears in *payload*.

    This is a best-effort defence in depth; callers must never allow raw
    content into log, audit, trace or error objects.  Check for known
    field names that indicate a plaintext leak.
    """
    FORBIDDEN_KEYS = {"plaintext", "initial_message", "raw_content", "content_bytes"}
    leaked = FORBIDDEN_KEYS & payload.keys()
    if leaked:
        raise ValueError(
            f"Raw content field(s) {leaked} must never appear in log/audit payload."
        )


# ---------------------------------------------------------------------------
# §3.8 & §3.9  Delete authorisation (owner-initiated)
# ---------------------------------------------------------------------------

def authorise_delete(
    *,
    tenant_id: str,
    owner_user_id: str,
    request_tenant_id: str,
    request_user_id: str,
    retention_class: RetentionClass,
) -> None:
    """Raise PrivateContentAccessDenied or ValueError when deletion is not allowed.

    Rules (§3.5):
    - Tenant scope must match.
    - request_user_id must be the owner.
    - legal_hold retention class blocks owner deletion.
    """
    if request_tenant_id != tenant_id:
        raise PrivateContentAccessDenied(
            "Tenant mismatch; deletion not authorised."
        )
    if request_user_id != owner_user_id:
        raise PrivateContentAccessDenied(
            "Only the content owner may request deletion."
        )
    if is_legal_hold(retention_class):
        raise PrivateContentAccessDenied(
            "Deletion blocked by legal hold; contact compliance."
        )

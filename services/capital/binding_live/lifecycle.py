"""Binding live lifecycle semantics.

This module is intentionally side-effect free. It evaluates TTL, revocation,
and suspend semantics for PersonaCapitalBinding-like records without touching
runtime bindings, broker sessions, or persistent stores.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "BindingLifecycle.v1"

BINDING_LIFECYCLE_STATUSES = ("pending", "active", "suspended", "revoked", "expired")
ADMISSIBLE_STATUS = "active"
TERMINAL_STATUSES = {"revoked", "expired"}
DEFAULT_REVOKER_ROLES = ("risk_owner", "operator", "capital.admin", "persona.admin")


class BindingLifecycleError(ValueError):
    """Raised when a binding lifecycle packet or transition is invalid."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: Any, field_name: str) -> datetime:
    text = _required_text(value, field_name)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BindingLifecycleError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _at(value: datetime | str | None) -> datetime:
    if value is None:
        return _utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return _parse_utc_timestamp(value, "at")


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BindingLifecycleError(f"{field_name} must be an object")
    return dict(value)


def _sequence(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BindingLifecycleError(f"{field_name} must be a list")
    return list(value)


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise BindingLifecycleError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise BindingLifecycleError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise BindingLifecycleError(f"{field_name} must be a boolean")
    return value


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BindingLifecycleError(f"{field_name} must be an integer")
    if value <= 0:
        raise BindingLifecycleError(f"{field_name} must be greater than 0")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    return tuple(_required_text(item, f"{field_name}[]") for item in _sequence(value, field_name))


def _status(value: Any, field_name: str) -> str:
    status = _required_text(value, field_name).lower()
    if status not in BINDING_LIFECYCLE_STATUSES:
        raise BindingLifecycleError(
            f"{field_name} must be one of: {', '.join(BINDING_LIFECYCLE_STATUSES)}"
        )
    return status


@dataclass(frozen=True)
class BindingTTL:
    issued_at: str
    ttl_hours: int
    expires_at: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BindingTTL":
        payload = _mapping(data, "ttl")
        ttl = cls(
            issued_at=_required_text(payload.get("issued_at"), "ttl.issued_at"),
            ttl_hours=_required_int(payload.get("ttl_hours"), "ttl.ttl_hours"),
            expires_at=_optional_text(payload.get("expires_at")),
        )
        ttl.validate_consistency()
        return ttl

    @property
    def issued_at_time(self) -> datetime:
        return _parse_utc_timestamp(self.issued_at, "ttl.issued_at")

    @property
    def expires_at_time(self) -> datetime:
        return self.issued_at_time + timedelta(hours=self.ttl_hours)

    @property
    def expires_at_iso(self) -> str:
        return _format_utc(self.expires_at_time)

    def validate_consistency(self) -> None:
        if self.expires_at is None:
            return
        if _parse_utc_timestamp(self.expires_at, "ttl.expires_at") != self.expires_at_time:
            raise BindingLifecycleError("ttl.expires_at must equal ttl.issued_at + ttl.ttl_hours")

    def is_expired(self, at: datetime | str | None = None) -> bool:
        return _at(at) >= self.expires_at_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "issued_at": _format_utc(self.issued_at_time),
            "ttl_hours": self.ttl_hours,
            "expires_at": self.expires_at_iso,
        }


@dataclass(frozen=True)
class BindingRevocationPolicy:
    revocation_allowed: bool
    allowed_revoker_roles: tuple[str, ...] = DEFAULT_REVOKER_ROLES
    requires_reason: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BindingRevocationPolicy":
        payload = _mapping(data, "revocation_policy")
        roles_value = payload.get("allowed_revoker_roles")
        roles = DEFAULT_REVOKER_ROLES if roles_value is None else _string_tuple(
            roles_value,
            "revocation_policy.allowed_revoker_roles",
        )
        policy = cls(
            revocation_allowed=_required_bool(
                payload.get("revocation_allowed"),
                "revocation_policy.revocation_allowed",
            ),
            allowed_revoker_roles=roles,
            requires_reason=_required_bool(
                payload.get("requires_reason", True),
                "revocation_policy.requires_reason",
            ),
        )
        policy.validate_consistency()
        return policy

    def validate_consistency(self) -> None:
        if self.revocation_allowed and not self.allowed_revoker_roles:
            raise BindingLifecycleError("revocation_policy.allowed_revoker_roles must not be empty")

    def validate_revoke(self, *, actor_role: str, reason: str | None) -> None:
        if not self.revocation_allowed:
            raise BindingLifecycleError("revocation_policy.revocation_allowed must be true")
        normalized_role = _required_text(actor_role, "actor_role")
        if normalized_role not in self.allowed_revoker_roles:
            raise BindingLifecycleError(f"actor_role {normalized_role!r} cannot revoke this binding")
        if self.requires_reason and not _optional_text(reason):
            raise BindingLifecycleError("revocation reason is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "revocation_allowed": self.revocation_allowed,
            "allowed_revoker_roles": list(self.allowed_revoker_roles),
            "requires_reason": self.requires_reason,
        }


@dataclass(frozen=True)
class BindingLifecycleEvaluation:
    binding_id: str
    status: str
    admissible: bool
    blocking_reasons: tuple[str, ...]
    evaluated_at: str
    expires_at: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "status": self.status,
            "admissible": self.admissible,
            "blocking_reasons": list(self.blocking_reasons),
            "evaluated_at": self.evaluated_at,
            "expires_at": self.expires_at,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class BindingLifecycleState:
    binding_id: str
    status: str
    ttl: BindingTTL
    revocation_policy: BindingRevocationPolicy
    updated_at: str | None = None
    suspended_at: str | None = None
    suspended_by: str | None = None
    suspend_reason: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BindingLifecycleState":
        payload = _mapping(data, "BindingLifecycleState")
        lifecycle = cls(
            binding_id=_required_text(payload.get("binding_id"), "binding_id"),
            status=_status(payload.get("status"), "status"),
            ttl=BindingTTL.from_dict(payload.get("ttl")),
            revocation_policy=BindingRevocationPolicy.from_dict(payload.get("revocation_policy")),
            updated_at=_optional_text(payload.get("updated_at")),
            suspended_at=_optional_text(payload.get("suspended_at")),
            suspended_by=_optional_text(payload.get("suspended_by")),
            suspend_reason=_optional_text(payload.get("suspend_reason")),
            revoked_at=_optional_text(payload.get("revoked_at")),
            revoked_by=_optional_text(payload.get("revoked_by")),
            revocation_reason=_optional_text(payload.get("revocation_reason")),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
        )
        lifecycle.validate_consistency()
        return lifecycle

    def validate_consistency(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise BindingLifecycleError(f"schema_version must be {SCHEMA_VERSION}")
        if self.status == "revoked":
            _required_text(self.revoked_at, "revoked_at")
            _required_text(self.revoked_by, "revoked_by")
            if self.revocation_policy.requires_reason:
                _required_text(self.revocation_reason, "revocation_reason")
        if self.status == "suspended":
            _required_text(self.suspended_at, "suspended_at")
            _required_text(self.suspended_by, "suspended_by")
            _required_text(self.suspend_reason, "suspend_reason")

    def effective_status(self, at: datetime | str | None = None) -> str:
        if self.status == "revoked":
            return "revoked"
        if self.ttl.is_expired(at):
            return "expired"
        return self.status

    def evaluate(self, at: datetime | str | None = None) -> BindingLifecycleEvaluation:
        evaluated_at = _at(at)
        status = self.effective_status(evaluated_at)
        admissible = status == ADMISSIBLE_STATUS
        return BindingLifecycleEvaluation(
            binding_id=self.binding_id,
            status=status,
            admissible=admissible,
            blocking_reasons=() if admissible else (_blocking_reason(status),),
            evaluated_at=_format_utc(evaluated_at),
            expires_at=self.ttl.expires_at_iso,
        )

    def suspend(
        self,
        *,
        actor_id: str,
        reason: str,
        at: datetime | str | None = None,
    ) -> "BindingLifecycleState":
        transition_at = _at(at)
        if self.effective_status(transition_at) != "active":
            raise BindingLifecycleError("only an active, unexpired binding can be suspended")
        return replace(
            self,
            status="suspended",
            updated_at=_format_utc(transition_at),
            suspended_at=_format_utc(transition_at),
            suspended_by=_required_text(actor_id, "actor_id"),
            suspend_reason=_required_text(reason, "suspend_reason"),
        )

    def reactivate(
        self,
        *,
        actor_id: str,
        at: datetime | str | None = None,
    ) -> "BindingLifecycleState":
        transition_at = _at(at)
        if self.status != "suspended":
            raise BindingLifecycleError("only a suspended binding can be reactivated")
        if self.ttl.is_expired(transition_at):
            raise BindingLifecycleError("expired binding cannot be reactivated")
        return replace(
            self,
            status="active",
            updated_at=_format_utc(transition_at),
            suspended_at=None,
            suspended_by=None,
            suspend_reason=None,
        )

    def revoke(
        self,
        *,
        actor_id: str,
        actor_role: str,
        reason: str | None,
        at: datetime | str | None = None,
    ) -> "BindingLifecycleState":
        transition_at = _at(at)
        if self.effective_status(transition_at) in TERMINAL_STATUSES:
            raise BindingLifecycleError("terminal binding cannot be revoked again")
        self.revocation_policy.validate_revoke(actor_role=actor_role, reason=reason)
        return replace(
            self,
            status="revoked",
            updated_at=_format_utc(transition_at),
            revoked_at=_format_utc(transition_at),
            revoked_by=_required_text(actor_id, "actor_id"),
            revocation_reason=_optional_text(reason),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "binding_id": self.binding_id,
            "status": self.status,
            "ttl": self.ttl.to_dict(),
            "revocation_policy": self.revocation_policy.to_dict(),
            "updated_at": self.updated_at,
            "suspended_at": self.suspended_at,
            "suspended_by": self.suspended_by,
            "suspend_reason": self.suspend_reason,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "revocation_reason": self.revocation_reason,
            "schema_version": self.schema_version,
        }
        return {key: value for key, value in payload.items() if value is not None}


def evaluate_binding_lifecycle(
    record: Mapping[str, Any] | BindingLifecycleState,
    *,
    at: datetime | str | None = None,
) -> BindingLifecycleEvaluation:
    """Evaluate binding lifecycle status and admissibility without side effects."""

    lifecycle = record if isinstance(record, BindingLifecycleState) else BindingLifecycleState.from_dict(record)
    return lifecycle.evaluate(at=at)


def _blocking_reason(status: str) -> str:
    if status == "pending":
        return "binding_pending"
    if status == "suspended":
        return "binding_suspended"
    if status == "revoked":
        return "binding_revoked"
    if status == "expired":
        return "binding_ttl_expired"
    return f"binding_{status}"

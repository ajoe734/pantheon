"""Shared references used by foundation envelopes and records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .exceptions import FoundationValidationError
from .serialization import drop_none


class ActorType(str, Enum):
    USER = "user"
    PERSONA = "persona"
    SERVICE = "service"
    RUNTIME = "runtime"
    SYSTEM = "system"


class EnvironmentName(str, Enum):
    DEV = "dev"
    SANDBOX = "sandbox"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"


def _coerce_enum(enum_type: type[Enum], value: Enum | str, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise FoundationValidationError(f"{field_name} must be one of: {allowed}") from exc


@dataclass(frozen=True)
class EnvironmentScope:
    name: EnvironmentName | str
    region: str | None = None
    market: str | None = None
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _coerce_enum(EnvironmentName, self.name, "environment.name"))
        if not str(self.timezone).strip():
            raise FoundationValidationError("environment.timezone is required")

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "name": self.name.value,
                "region": self.region,
                "market": self.market,
                "timezone": self.timezone,
            }
        )


@dataclass(frozen=True)
class ActorRef:
    actor_type: ActorType | str
    actor_id: str
    display_name: str | None = None
    roles: tuple[str, ...] | list[str] = field(default_factory=tuple)
    workspace_id: str | None = None
    persona_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_type", _coerce_enum(ActorType, self.actor_type, "actor_type"))
        if not str(self.actor_id).strip():
            raise FoundationValidationError("actor_id is required")
        normalized_roles = tuple(str(role).strip() for role in self.roles if str(role).strip())
        object.__setattr__(self, "roles", normalized_roles)

    @property
    def actor_ref(self) -> str:
        return f"{self.actor_type.value}:{self.actor_id}"

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "actor_type": self.actor_type.value,
                "actor_id": self.actor_id,
                "display_name": self.display_name,
                "roles": list(self.roles),
                "workspace_id": self.workspace_id,
                "persona_id": self.persona_id,
                "session_id": self.session_id,
            }
        )


@dataclass(frozen=True)
class AuthorityScope:
    action: str
    target_type: str
    target_id: str
    environment: EnvironmentScope
    persona_id: str | None = None
    workspace_id: str | None = None
    capital_pool_id: str | None = None
    runtime_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("action", "target_type", "target_id"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"authority_scope.{field_name} is required")
        if not isinstance(self.environment, EnvironmentScope):
            raise FoundationValidationError("authority_scope.environment must be an EnvironmentScope")
        object.__setattr__(self, "attributes", dict(self.attributes))

    @property
    def target_ref(self) -> str:
        return f"{self.target_type}:{self.target_id}"

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "action": self.action,
                "target_type": self.target_type,
                "target_id": self.target_id,
                "target_ref": self.target_ref,
                "environment": self.environment.to_dict(),
                "persona_id": self.persona_id,
                "workspace_id": self.workspace_id,
                "capital_pool_id": self.capital_pool_id,
                "runtime_id": self.runtime_id,
                "attributes": dict(self.attributes),
            }
        )

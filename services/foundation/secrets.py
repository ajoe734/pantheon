"""Secret reference primitive.

SecretRef only represents metadata and authority boundaries. It never carries
raw secret values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .exceptions import FoundationValidationError
from .serialization import drop_none
from .types import EnvironmentScope

FORBIDDEN_SECRET_METADATA_KEYS = {
    "access_token",
    "api_key",
    "client_secret",
    "password",
    "plaintext",
    "raw_secret",
    "secret",
    "secret_value",
    "token",
    "value",
}


class SecretProvider(str, Enum):
    VAULT = "vault"
    ENV = "env"
    CLOUD_SECRET_MANAGER = "cloud_secret_manager"
    BROKER_RUNTIME = "broker_runtime"


class SecretScopeType(str, Enum):
    SERVICE = "service"
    RUNTIME = "runtime"
    BROKER_ACCOUNT = "broker_account"
    DATA_VENDOR = "data_vendor"


class SecretRotationState(str, Enum):
    CURRENT = "current"
    ROTATING = "rotating"
    REVOKED = "revoked"


def _coerce_enum(enum_type: type[Enum], value: Enum | str, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise FoundationValidationError(f"{field_name} must be one of: {allowed}") from exc


@dataclass(frozen=True)
class SecretRef:
    secret_ref: str
    provider: SecretProvider | str
    scope_type: SecretScopeType | str
    scope_id: str
    environment: EnvironmentScope
    allowed_consumers: tuple[str, ...] | list[str] = field(default_factory=tuple)
    rotation_state: SecretRotationState | str = SecretRotationState.CURRENT
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("secret_ref", "scope_id"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"secret_ref.{field_name} is required")
        if not isinstance(self.environment, EnvironmentScope):
            raise FoundationValidationError("secret_ref.environment must be an EnvironmentScope")
        provider = _coerce_enum(SecretProvider, self.provider, "secret_ref.provider")
        scope_type = _coerce_enum(SecretScopeType, self.scope_type, "secret_ref.scope_type")
        rotation_state = _coerce_enum(SecretRotationState, self.rotation_state, "secret_ref.rotation_state")
        consumers = tuple(str(item).strip() for item in self.allowed_consumers if str(item).strip())
        forbidden = {"frontend", "front-ai-trading-system", "browser"}
        if forbidden.intersection(consumers):
            raise FoundationValidationError("SecretRef allowed_consumers cannot include frontend consumers")
        metadata = dict(self.metadata)
        forbidden_metadata_keys = {
            str(key).strip().lower()
            for key in metadata
            if str(key).strip().lower() in FORBIDDEN_SECRET_METADATA_KEYS
        }
        if forbidden_metadata_keys:
            keys = ", ".join(sorted(forbidden_metadata_keys))
            raise FoundationValidationError(f"SecretRef metadata cannot include raw secret keys: {keys}")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "scope_type", scope_type)
        object.__setattr__(self, "rotation_state", rotation_state)
        object.__setattr__(self, "allowed_consumers", consumers)
        object.__setattr__(self, "metadata", metadata)

    @property
    def secret_ref_id(self) -> str:
        return self.secret_ref

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "secret_ref": self.secret_ref,
                "provider": self.provider.value,
                "scope_type": self.scope_type.value,
                "scope_id": self.scope_id,
                "environment": self.environment.to_dict(),
                "allowed_consumers": list(self.allowed_consumers),
                "rotation_state": self.rotation_state.value,
                "metadata": dict(self.metadata),
            }
        )

"""Inbound consultation identity and tenant boundary."""

from __future__ import annotations

import contextvars
import hmac
import os
import re
from dataclasses import dataclass
from typing import Mapping


_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class ConsultationIdentity:
    actor_type: str
    actor_id: str
    tenant_id: str


class ConsultationAuthError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_IDENTITY: contextvars.ContextVar[ConsultationIdentity | None] = (
    contextvars.ContextVar("consultation_identity", default=None)
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _tenant(headers: Mapping[str, str], *, required: bool) -> str:
    value = str(
        headers.get("x-pantheon-tenant-id")
        or ("" if required else os.getenv("PANTHEON_TENANT_ID", "default"))
    ).strip()
    if not value:
        raise ConsultationAuthError(400, "X-Pantheon-Tenant-Id is required")
    if _TENANT_RE.fullmatch(value) is None:
        raise ConsultationAuthError(400, "X-Pantheon-Tenant-Id is invalid")
    return value


def authenticate(headers: Mapping[str, str]) -> ConsultationIdentity:
    """Authenticate one API request using configured service/operator tokens."""

    required = _truthy(os.getenv("CONSULTATION_AUTH_REQUIRED"))
    tenant_id = _tenant(headers, required=required)
    if not required:
        service_actor = str(headers.get("x-pantheon-service-actor") or "").strip()
        actor_type = "service" if service_actor else "operator"
        actor_id = service_actor or str(
            headers.get("x-pantheon-actor-id") or "legacy-consultation-client"
        ).strip()
        return ConsultationIdentity(
            actor_type=actor_type,
            actor_id=actor_id,
            tenant_id=tenant_id,
        )

    service_token = str(os.getenv("CONSULTATION_SERVICE_TOKEN") or "").strip()
    operator_token = str(os.getenv("CONSULTATION_OPERATOR_TOKEN") or "").strip()
    if not service_token and not operator_token:
        raise ConsultationAuthError(
            503,
            "consultation auth is required but no service/operator token is configured",
        )
    scheme, separator, presented = str(headers.get("authorization") or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not presented.strip():
        raise ConsultationAuthError(401, "Bearer authorization is required")
    token = presented.strip()
    if service_token and hmac.compare_digest(token, service_token):
        actor_type = "service"
        actor_id = str(
            headers.get("x-pantheon-service-actor")
            or "authenticated-consultation-service"
        ).strip()
    elif operator_token and hmac.compare_digest(token, operator_token):
        actor_type = "operator"
        actor_id = str(
            headers.get("x-pantheon-actor-id") or "authenticated-operator"
        ).strip()
    else:
        raise ConsultationAuthError(403, "consultation authorization is invalid")
    if not actor_id:
        raise ConsultationAuthError(400, "authenticated actor identity is empty")
    return ConsultationIdentity(
        actor_type=actor_type,
        actor_id=actor_id,
        tenant_id=tenant_id,
    )


def bind_identity(identity: ConsultationIdentity) -> contextvars.Token:
    return _IDENTITY.set(identity)


def reset_identity(token: contextvars.Token) -> None:
    _IDENTITY.reset(token)


def current_identity() -> ConsultationIdentity:
    identity = _IDENTITY.get()
    if identity is not None:
        return identity
    return ConsultationIdentity(
        actor_type="operator",
        actor_id="direct-call",
        tenant_id=str(os.getenv("PANTHEON_TENANT_ID") or "default"),
    )


def require_actor(*actor_types: str) -> ConsultationIdentity:
    identity = current_identity()
    if identity.actor_type not in set(actor_types):
        raise ConsultationAuthError(
            403,
            f"authenticated {identity.actor_type} actor is not allowed",
        )
    return identity

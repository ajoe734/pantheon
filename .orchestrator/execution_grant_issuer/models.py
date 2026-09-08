"""Domain models and exceptions for the execution grant issuer.

OPS-EXECUTION-MFA-ISSUER-001.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


class IssuerError(Exception):
    """Base error for execution grant issuer operations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthenticationError(IssuerError):
    """Authentication or token verification failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=401)


class ChallengeError(IssuerError):
    """Challenge lifecycle or validation failure."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message, status_code=status_code)


class PolicyValidationError(IssuerError):
    """Task policy validation or restriction failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=403)


@dataclass(frozen=True)
class VerifiedOperator:
    """Represents a verified Identity Platform operator with MFA."""

    uid: str
    email: str
    auth_time: datetime
    second_factor: str
    claims: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Challenge:
    """Represents an active or consumed issuance challenge."""

    challenge_id: str
    actor_uid: str
    actor_email: str
    task_id: str
    generation: int
    policy_snapshot: dict[str, Any]
    policy_digest: str
    environment: str
    resources: list[str]
    created_at: datetime
    expires_at: datetime
    consumed: bool = False
    consumed_at: datetime | None = None
    challenge_digest: str = ""

"""Shared Pantheon foundation primitives.

The package intentionally exposes pure value objects only. Service adoption
layers can import these contracts without creating storage, network, or runtime
side effects.
"""

from .audit import AuditAction
from .envelopes import CommandEnvelope, ErrorEnvelope, ErrorKind, TraceContext
from .exceptions import FoundationValidationError
from .idempotency import IdempotencyRecord, IdempotencyStatus
from .policy import PolicyDecision, PolicyDecisionValue
from .secrets import SecretProvider, SecretRef, SecretRotationState, SecretScopeType
from .serialization import canonical_json, foundation_id, sha256_checksum, utc_now
from .types import ActorRef, ActorType, AuthorityScope, EnvironmentName, EnvironmentScope

__all__ = [
    "ActorRef",
    "ActorType",
    "AuditAction",
    "AuthorityScope",
    "CommandEnvelope",
    "EnvironmentName",
    "EnvironmentScope",
    "ErrorEnvelope",
    "ErrorKind",
    "FoundationValidationError",
    "IdempotencyRecord",
    "IdempotencyStatus",
    "PolicyDecision",
    "PolicyDecisionValue",
    "SecretProvider",
    "SecretRef",
    "SecretRotationState",
    "SecretScopeType",
    "TraceContext",
    "canonical_json",
    "foundation_id",
    "sha256_checksum",
    "utc_now",
]

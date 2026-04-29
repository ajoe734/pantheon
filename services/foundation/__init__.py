"""Shared Pantheon foundation primitives.

The package intentionally exposes pure value objects only. Service adoption
layers can import these contracts without creating storage, network, or runtime
side effects.
"""

from .audit import AuditAction
from .command_recovery import (
    CommandRecoveryAction,
    CommandRecoveryAudit,
    command_recovery_entry,
    idempotency_record_from_entry,
    is_recoverable_inflight_status,
    load_command_recovery_entries,
)
from .dead_letter import DeadLetterEntry, DeadLetterQueue, DeadLetterStatus
from .envelopes import CommandEnvelope, ErrorEnvelope, ErrorKind, TraceContext
from .exceptions import FoundationValidationError
from .health import health_payload, metrics_payload, readiness_status_code
from .idempotency import IdempotencyRecord, IdempotencyStatus
from .outbox import (
    EventEnvelope,
    InboxReceipt,
    InboxReceiptStatus,
    JsonlOutboxStore,
    OutboxRecord,
    OutboxRecordStatus,
)
from .policy import PolicyDecision, PolicyDecisionValue
from .replay import (
    DeadLetterReplayBatchResult,
    DeadLetterReplayProcessor,
    DeadLetterReplayResult,
    DeadLetterReplayStatus,
    IdempotentReplayLedger,
)
from .schema_registry import SchemaRegistry, SchemaRegistryEntry, SchemaValidationResult
from .secrets import SecretProvider, SecretRef, SecretRotationState, SecretScopeType
from .serialization import canonical_json, foundation_id, sha256_checksum, utc_now
from .types import ActorRef, ActorType, AuthorityScope, EnvironmentName, EnvironmentScope

__all__ = [
    "ActorRef",
    "ActorType",
    "AuditAction",
    "AuthorityScope",
    "CommandEnvelope",
    "CommandRecoveryAction",
    "CommandRecoveryAudit",
    "DeadLetterEntry",
    "DeadLetterQueue",
    "DeadLetterReplayBatchResult",
    "DeadLetterReplayProcessor",
    "DeadLetterReplayResult",
    "DeadLetterReplayStatus",
    "DeadLetterStatus",
    "EnvironmentName",
    "EnvironmentScope",
    "ErrorEnvelope",
    "ErrorKind",
    "EventEnvelope",
    "FoundationValidationError",
    "health_payload",
    "IdempotentReplayLedger",
    "IdempotencyRecord",
    "IdempotencyStatus",
    "InboxReceipt",
    "InboxReceiptStatus",
    "JsonlOutboxStore",
    "OutboxRecord",
    "OutboxRecordStatus",
    "PolicyDecision",
    "PolicyDecisionValue",
    "SchemaRegistry",
    "SchemaRegistryEntry",
    "SchemaValidationResult",
    "SecretProvider",
    "SecretRef",
    "SecretRotationState",
    "SecretScopeType",
    "TraceContext",
    "canonical_json",
    "command_recovery_entry",
    "foundation_id",
    "idempotency_record_from_entry",
    "is_recoverable_inflight_status",
    "load_command_recovery_entries",
    "metrics_payload",
    "readiness_status_code",
    "sha256_checksum",
    "utc_now",
]

"""Audited idempotent replay helpers for foundation DLQ entries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .audit import AuditAction
from .dead_letter import DeadLetterEntry, DeadLetterQueue, DeadLetterStatus
from .exceptions import FoundationValidationError
from .idempotency import IdempotencyRecord, IdempotencyStatus
from .outbox import EventEnvelope
from .schema_registry import SchemaRegistry
from .types import ActorRef, EnvironmentScope


class DeadLetterReplayStatus(str, Enum):
    APPLIED = "applied"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    SCHEMA_REJECTED = "schema_rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class DeadLetterReplayResult:
    entry_id: str
    event_id: str
    status: DeadLetterReplayStatus
    audit_action: AuditAction
    idempotency_key: str
    result_ref: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "entry_id": self.entry_id,
            "event_id": self.event_id,
            "status": self.status.value,
            "audit_action": self.audit_action.to_dict(),
            "idempotency_key": self.idempotency_key,
        }
        if self.result_ref:
            payload["result_ref"] = self.result_ref
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class DeadLetterReplayBatchResult:
    results: tuple[DeadLetterReplayResult, ...]

    @property
    def summary(self) -> dict[str, int]:
        counts = {status.value: 0 for status in DeadLetterReplayStatus}
        for result in self.results:
            counts[result.status.value] += 1
        return counts

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "results": [result.to_dict() for result in self.results],
        }


class IdempotentReplayLedger:
    """In-memory idempotency ledger for replay attempts."""

    def __init__(self, records: dict[str, IdempotencyRecord] | None = None):
        self._records: dict[str, IdempotencyRecord] = dict(records or {})

    def reserve(
        self,
        *,
        idempotency_key: str,
        operation_type: str,
        target_ref: str,
        request_payload: object,
        trace_id: str,
    ) -> tuple[IdempotencyRecord, bool]:
        existing = self._records.get(idempotency_key)
        if existing:
            if not existing.matches_request(request_payload):
                raise FoundationValidationError("replay idempotency key was reused with a different payload")
            return existing, existing.status == IdempotencyStatus.SUCCEEDED
        record = IdempotencyRecord.reserve(
            idempotency_key=idempotency_key,
            operation_type=operation_type,
            target_ref=target_ref,
            request_payload=request_payload,
            trace_id=trace_id,
        )
        self._records[idempotency_key] = record
        return record, False

    def mark_succeeded(self, idempotency_key: str, *, result_ref: str) -> IdempotencyRecord:
        record = self._require_record(idempotency_key)
        updated = record.with_status(IdempotencyStatus.SUCCEEDED, result_ref=result_ref)
        self._records[idempotency_key] = updated
        return updated

    def mark_failed(self, idempotency_key: str, *, result_ref: str | None = None) -> IdempotencyRecord:
        record = self._require_record(idempotency_key)
        updated = record.with_status(IdempotencyStatus.FAILED, result_ref=result_ref)
        self._records[idempotency_key] = updated
        return updated

    def get(self, idempotency_key: str) -> IdempotencyRecord | None:
        return self._records.get(idempotency_key)

    def _require_record(self, idempotency_key: str) -> IdempotencyRecord:
        record = self._records.get(idempotency_key)
        if not record:
            raise FoundationValidationError(f"replay idempotency record not found: {idempotency_key}")
        return record


class DeadLetterReplayProcessor:
    """Replay DLQ entries through schema validation, idempotency, and audit."""

    def __init__(self, *, schema_registry: SchemaRegistry, ledger: IdempotentReplayLedger | None = None):
        self._schema_registry = schema_registry
        self._ledger = ledger or IdempotentReplayLedger()

    def replay(
        self,
        entries: list[DeadLetterEntry] | tuple[DeadLetterEntry, ...],
        *,
        actor_ref: ActorRef,
        environment: EnvironmentScope,
        apply_fn: Callable[[EventEnvelope], str | None],
        reason: str,
        queue: DeadLetterQueue | None = None,
    ) -> DeadLetterReplayBatchResult:
        results: list[DeadLetterReplayResult] = []
        for entry in entries:
            result, updated_entry = self._replay_one(
                entry,
                actor_ref=actor_ref,
                environment=environment,
                apply_fn=apply_fn,
                reason=reason,
            )
            results.append(result)
            if queue:
                queue.replace_entry(updated_entry)
        return DeadLetterReplayBatchResult(results=tuple(results))

    def _replay_one(
        self,
        entry: DeadLetterEntry,
        *,
        actor_ref: ActorRef,
        environment: EnvironmentScope,
        apply_fn: Callable[[EventEnvelope], str | None],
        reason: str,
    ) -> tuple[DeadLetterReplayResult, DeadLetterEntry]:
        event = entry.event
        target_ref = f"{event.aggregate_type}:{event.aggregate_id}:{event.event_id}"
        replay_key = f"dlq-replay:{event.idempotency_key}"

        if event.schema_ref:
            validation = self._schema_registry.validate(event.schema_ref, event.payload)
            if not validation.valid:
                error = "; ".join(validation.errors)
                audit = self._audit(
                    actor_ref=actor_ref,
                    environment=environment,
                    action_type="foundation.dlq.replay.schema_rejected",
                    target_ref=target_ref,
                    reason=reason,
                    entry=entry,
                    error=error,
                )
                result = DeadLetterReplayResult(
                    entry_id=entry.entry_id,
                    event_id=event.event_id,
                    status=DeadLetterReplayStatus.SCHEMA_REJECTED,
                    audit_action=audit,
                    idempotency_key=replay_key,
                    error=error,
                )
                return result, entry.with_replay_result(
                    status=DeadLetterStatus.SCHEMA_REJECTED,
                    audit_action=audit,
                    error=error,
                )

        try:
            _, duplicate = self._ledger.reserve(
                idempotency_key=replay_key,
                operation_type="foundation.dlq.replay",
                target_ref=target_ref,
                request_payload=event.to_dict(),
                trace_id=event.trace_id,
            )
        except FoundationValidationError as exc:
            audit = self._audit(
                actor_ref=actor_ref,
                environment=environment,
                action_type="foundation.dlq.replay.failed",
                target_ref=target_ref,
                reason=reason,
                entry=entry,
                error=str(exc),
            )
            result = DeadLetterReplayResult(
                entry_id=entry.entry_id,
                event_id=event.event_id,
                status=DeadLetterReplayStatus.FAILED,
                audit_action=audit,
                idempotency_key=replay_key,
                error=str(exc),
            )
            return result, entry.with_replay_result(status=DeadLetterStatus.REPLAY_FAILED, audit_action=audit, error=str(exc))

        if duplicate:
            audit = self._audit(
                actor_ref=actor_ref,
                environment=environment,
                action_type="foundation.dlq.replay.duplicate_skipped",
                target_ref=target_ref,
                reason=reason,
                entry=entry,
            )
            result = DeadLetterReplayResult(
                entry_id=entry.entry_id,
                event_id=event.event_id,
                status=DeadLetterReplayStatus.DUPLICATE_SKIPPED,
                audit_action=audit,
                idempotency_key=replay_key,
                result_ref=self._ledger.get(replay_key).result_ref if self._ledger.get(replay_key) else None,
            )
            return result, entry.with_replay_result(status=DeadLetterStatus.DUPLICATE_SKIPPED, audit_action=audit)

        try:
            result_ref = apply_fn(event) or f"event:{event.event_id}"
            self._ledger.mark_succeeded(replay_key, result_ref=result_ref)
            audit = self._audit(
                actor_ref=actor_ref,
                environment=environment,
                action_type="foundation.dlq.replay.applied",
                target_ref=target_ref,
                reason=reason,
                entry=entry,
                after_state_ref=result_ref,
            )
            result = DeadLetterReplayResult(
                entry_id=entry.entry_id,
                event_id=event.event_id,
                status=DeadLetterReplayStatus.APPLIED,
                audit_action=audit,
                idempotency_key=replay_key,
                result_ref=result_ref,
            )
            return result, entry.with_replay_result(status=DeadLetterStatus.REPLAYED, audit_action=audit)
        except Exception as exc:  # noqa: BLE001
            self._ledger.mark_failed(replay_key, result_ref=f"error:{type(exc).__name__}")
            audit = self._audit(
                actor_ref=actor_ref,
                environment=environment,
                action_type="foundation.dlq.replay.failed",
                target_ref=target_ref,
                reason=reason,
                entry=entry,
                error=str(exc),
            )
            result = DeadLetterReplayResult(
                entry_id=entry.entry_id,
                event_id=event.event_id,
                status=DeadLetterReplayStatus.FAILED,
                audit_action=audit,
                idempotency_key=replay_key,
                error=str(exc),
            )
            return result, entry.with_replay_result(status=DeadLetterStatus.REPLAY_FAILED, audit_action=audit, error=str(exc))

    def _audit(
        self,
        *,
        actor_ref: ActorRef,
        environment: EnvironmentScope,
        action_type: str,
        target_ref: str,
        reason: str,
        entry: DeadLetterEntry,
        after_state_ref: str | None = None,
        error: str | None = None,
    ) -> AuditAction:
        metadata = {
            "dead_letter_entry_id": entry.entry_id,
            "event_id": entry.event.event_id,
            "event_type": entry.event.event_type,
            "tags": list(entry.tags),
        }
        if error:
            metadata["error"] = error
        return AuditAction.record(
            actor_ref=actor_ref,
            action_type=action_type,
            target_ref=target_ref,
            environment=environment,
            reason=reason,
            trace=entry.event.trace,
            payload=entry.to_dict(),
            before_state_ref=f"dlq:{entry.entry_id}:{entry.status.value}",
            after_state_ref=after_state_ref,
            metadata=metadata,
        )

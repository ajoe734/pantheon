"""
Deployment orchestration saga backbone for governed cross-service deploy flows.

This module implements the DEP-002 consistency layer:
- a first-class deployment saga aggregate
- transactional outbox append alongside saga writes
- consumer inbox receipts for ordering and idempotency
- compensation decisions anchored to DeploymentPlan rollback linkage

The implementation is intentionally storage-light. It models the owner-scoped
write boundary as a single persisted JSON blob so tests can verify "business
write + outbox append" atomicity without introducing a real database.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from services.control_plane.governance.deployment_plan import (
    RollbackActionType,
    RollbackRef,
    RuntimeAction,
    utc_now,
)


class DeploymentSagaError(ValueError):
    """Raised when DEP-002 saga invariants are violated."""


class SagaStatus(str, Enum):
    AWAITING_BINDING = "awaiting_binding"
    AWAITING_RUNTIME_LOAD = "awaiting_runtime_load"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    FAILED = "failed"
    ABORTED = "aborted"


class SagaStep(str, Enum):
    BINDING_REQUESTED = "binding_requested"
    RUNTIME_LOAD_REQUESTED = "runtime_load_requested"
    RUNTIME_ACTIVE = "runtime_active"
    COMPENSATION_REQUESTED = "compensation_requested"
    COMPENSATED = "compensated"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class ReceiptStatus(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"


class CompensationCommand(str, Enum):
    ABORT_PLAN = "abort_plan"
    MARK_BINDING_FAILED_INACTIVE = "mark_binding_failed_inactive"
    REQUEST_ROLLBACK = "request_rollback"
    ENTER_SAFE_MODE_AND_RAISE_INCIDENT = "enter_safe_mode_and_raise_incident"


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _parse_rollback_action(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, RollbackActionType):
        return value.value
    if isinstance(value, RuntimeAction):
        if value == RuntimeAction.REPLACE_BINDING:
            return RollbackActionType.REPLACE.value
        return RollbackActionType(value.value).value
    if value == RuntimeAction.REPLACE_BINDING.value:
        return RollbackActionType.REPLACE.value
    return RollbackActionType(str(value)).value


def _plan_to_dict(plan: Any) -> Dict[str, Any]:
    if isinstance(plan, Mapping):
        return copy.deepcopy(dict(plan))
    to_dict = getattr(plan, "to_dict", None)
    if callable(to_dict):
        return copy.deepcopy(dict(to_dict()))
    raise DeploymentSagaError("Deployment saga bootstrap requires a DeploymentPlan or mapping payload.")


def _utc_after_seconds(seconds: int) -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        + timedelta(seconds=max(0, seconds))
    ).isoformat().replace("+00:00", "Z")


@dataclass
class SagaStepRecord:
    step: SagaStep | str
    status: SagaStatus | str
    event_id: str
    sequence_no: int
    emitted_at: str
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "step": _enum_value(self.step),
            "status": _enum_value(self.status),
            "event_id": self.event_id,
            "sequence_no": self.sequence_no,
            "emitted_at": self.emitted_at,
        }
        if self.note:
            payload["note"] = self.note
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SagaStepRecord":
        return cls(
            step=str(data["step"]),
            status=str(data["status"]),
            event_id=str(data["event_id"]),
            sequence_no=int(data["sequence_no"]),
            emitted_at=str(data["emitted_at"]),
            note=data.get("note"),
        )


@dataclass
class SagaEventEnvelope:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    sequence_no: int
    causal_parent_id: Optional[str]
    event_time: str
    emitted_at: str
    trace_id: str
    idempotency_key: str
    payload: Dict[str, Any]

    def validate(self) -> List[str]:
        errors: List[str] = []
        required = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "event_time": self.event_time,
            "emitted_at": self.emitted_at,
            "trace_id": self.trace_id,
            "idempotency_key": self.idempotency_key,
        }
        for field_name, value in required.items():
            if value in (None, ""):
                errors.append(f"{field_name} is required")
        if self.sequence_no < 1:
            errors.append("sequence_no must be >= 1")
        if not isinstance(self.payload, dict):
            errors.append("payload must be an object")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "sequence_no": self.sequence_no,
            "causal_parent_id": self.causal_parent_id,
            "event_time": self.event_time,
            "emitted_at": self.emitted_at,
            "trace_id": self.trace_id,
            "idempotency_key": self.idempotency_key,
            "payload": copy.deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SagaEventEnvelope":
        return cls(
            event_id=str(data["event_id"]),
            event_type=str(data["event_type"]),
            aggregate_type=str(data["aggregate_type"]),
            aggregate_id=str(data["aggregate_id"]),
            sequence_no=int(data["sequence_no"]),
            causal_parent_id=data.get("causal_parent_id"),
            event_time=str(data["event_time"]),
            emitted_at=str(data["emitted_at"]),
            trace_id=str(data["trace_id"]),
            idempotency_key=str(data["idempotency_key"]),
            payload=copy.deepcopy(dict(data.get("payload", {}))),
        )


@dataclass
class OutboxRecord:
    owner_service: str
    event: SagaEventEnvelope
    status: OutboxStatus | str = OutboxStatus.PENDING
    delivery_attempts: int = 0
    published_at: Optional[str] = None
    last_error: Optional[str] = None
    last_attempt_at: Optional[str] = None
    next_retry_at: Optional[str] = None
    blocked_reason: Optional[str] = None
    dlq_at: Optional[str] = None
    last_replayed_at: Optional[str] = None
    replay_count: int = 0
    retry_policy: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "owner_service": self.owner_service,
            "event": self.event.to_dict(),
            "status": _enum_value(self.status),
            "delivery_attempts": self.delivery_attempts,
            "replay_count": self.replay_count,
        }
        if self.published_at:
            payload["published_at"] = self.published_at
        if self.last_error:
            payload["last_error"] = self.last_error
        if self.last_attempt_at:
            payload["last_attempt_at"] = self.last_attempt_at
        if self.next_retry_at:
            payload["next_retry_at"] = self.next_retry_at
        if self.blocked_reason:
            payload["blocked_reason"] = self.blocked_reason
        if self.dlq_at:
            payload["dlq_at"] = self.dlq_at
        if self.last_replayed_at:
            payload["last_replayed_at"] = self.last_replayed_at
        if self.retry_policy:
            payload["retry_policy"] = copy.deepcopy(self.retry_policy)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutboxRecord":
        return cls(
            owner_service=str(data["owner_service"]),
            event=SagaEventEnvelope.from_dict(data["event"]),
            status=str(data.get("status", OutboxStatus.PENDING.value)),
            delivery_attempts=int(data.get("delivery_attempts", 0)),
            published_at=data.get("published_at"),
            last_error=data.get("last_error"),
            last_attempt_at=data.get("last_attempt_at"),
            next_retry_at=data.get("next_retry_at"),
            blocked_reason=data.get("blocked_reason"),
            dlq_at=data.get("dlq_at"),
            last_replayed_at=data.get("last_replayed_at"),
            replay_count=int(data.get("replay_count", 0)),
            retry_policy=copy.deepcopy(dict(data["retry_policy"])) if isinstance(data.get("retry_policy"), Mapping) else None,
        )


@dataclass
class InboxReceipt:
    consumer_name: str
    event_id: str
    idempotency_key: str
    aggregate_type: str
    aggregate_id: str
    sequence_no: int
    trace_id: str
    status: ReceiptStatus | str
    processed_at: str
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "consumer_name": self.consumer_name,
            "event_id": self.event_id,
            "idempotency_key": self.idempotency_key,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "sequence_no": self.sequence_no,
            "trace_id": self.trace_id,
            "status": _enum_value(self.status),
            "processed_at": self.processed_at,
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InboxReceipt":
        return cls(
            consumer_name=str(data["consumer_name"]),
            event_id=str(data["event_id"]),
            idempotency_key=str(data["idempotency_key"]),
            aggregate_type=str(data["aggregate_type"]),
            aggregate_id=str(data["aggregate_id"]),
            sequence_no=int(data["sequence_no"]),
            trace_id=str(data["trace_id"]),
            status=str(data["status"]),
            processed_at=str(data["processed_at"]),
            notes=data.get("notes"),
        )


@dataclass
class CompensationDecision:
    command_type: CompensationCommand | str
    owner_service: str
    plan_status: str
    binding_status: str
    write_scope: str
    runtime_action: Optional[str] = None
    incident_required: bool = False
    safe_mode: bool = False
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "command_type": _enum_value(self.command_type),
            "owner_service": self.owner_service,
            "plan_status": self.plan_status,
            "binding_status": self.binding_status,
            "write_scope": self.write_scope,
            "incident_required": self.incident_required,
            "safe_mode": self.safe_mode,
        }
        if self.runtime_action:
            payload["runtime_action"] = self.runtime_action
        if self.reason:
            payload["reason"] = self.reason
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompensationDecision":
        return cls(
            command_type=str(data["command_type"]),
            owner_service=str(data["owner_service"]),
            plan_status=str(data["plan_status"]),
            binding_status=str(data["binding_status"]),
            write_scope=str(data["write_scope"]),
            runtime_action=data.get("runtime_action"),
            incident_required=bool(data.get("incident_required", False)),
            safe_mode=bool(data.get("safe_mode", False)),
            reason=data.get("reason"),
        )


@dataclass
class DeploymentSaga:
    saga_id: str
    plan_id: str
    approval_decision_id: str
    strategy_id: str
    artifact_id: str
    artifact_version: str
    capital_pool_id: str
    current_stage: str
    target_stage: str
    runtime_action: str
    rollback_action_type: Optional[str]
    status: SagaStatus | str
    current_step: SagaStep | str
    trace_id: str
    created_at: str
    updated_at: str
    binding_id: Optional[str] = None
    runtime_id: Optional[str] = None
    last_sequence_no: int = 0
    last_event_id: Optional[str] = None
    failure_reason: Optional[str] = None
    compensation: Optional[CompensationDecision] = None
    metadata: Optional[Dict[str, Any]] = None
    history: List[SagaStepRecord] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        required = {
            "saga_id": self.saga_id,
            "plan_id": self.plan_id,
            "approval_decision_id": self.approval_decision_id,
            "strategy_id": self.strategy_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "capital_pool_id": self.capital_pool_id,
            "current_stage": self.current_stage,
            "target_stage": self.target_stage,
            "runtime_action": self.runtime_action,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        for field_name, value in required.items():
            if value in (None, ""):
                errors.append(f"{field_name} is required")
        try:
            SagaStatus(self.status)
        except ValueError:
            errors.append(f"Invalid status: {self.status}")
        try:
            SagaStep(self.current_step)
        except ValueError:
            errors.append(f"Invalid current_step: {self.current_step}")
        try:
            RuntimeAction(self.runtime_action)
        except ValueError:
            errors.append(f"Invalid runtime_action: {self.runtime_action}")
        if self.rollback_action_type:
            try:
                RollbackActionType(self.rollback_action_type)
            except ValueError:
                errors.append(f"Invalid rollback_action_type: {self.rollback_action_type}")
        if self.last_sequence_no < 0:
            errors.append("last_sequence_no must be >= 0")
        if self.last_sequence_no == 0 and self.last_event_id:
            errors.append("last_event_id requires last_sequence_no >= 1")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["status"] = _enum_value(self.status)
        payload["current_step"] = _enum_value(self.current_step)
        payload["history"] = [record.to_dict() for record in self.history]
        if self.compensation:
            payload["compensation"] = self.compensation.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeploymentSaga":
        compensation = data.get("compensation")
        history = data.get("history", [])
        return cls(
            saga_id=str(data["saga_id"]),
            plan_id=str(data["plan_id"]),
            approval_decision_id=str(data["approval_decision_id"]),
            strategy_id=str(data["strategy_id"]),
            artifact_id=str(data["artifact_id"]),
            artifact_version=str(data["artifact_version"]),
            capital_pool_id=str(data["capital_pool_id"]),
            current_stage=str(data["current_stage"]),
            target_stage=str(data["target_stage"]),
            runtime_action=str(data["runtime_action"]),
            rollback_action_type=data.get("rollback_action_type"),
            status=str(data["status"]),
            current_step=str(data["current_step"]),
            trace_id=str(data["trace_id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            binding_id=data.get("binding_id"),
            runtime_id=data.get("runtime_id"),
            last_sequence_no=int(data.get("last_sequence_no", 0)),
            last_event_id=data.get("last_event_id"),
            failure_reason=data.get("failure_reason"),
            compensation=CompensationDecision.from_dict(compensation) if isinstance(compensation, Mapping) else None,
            metadata=copy.deepcopy(dict(data["metadata"])) if isinstance(data.get("metadata"), Mapping) else None,
            history=[SagaStepRecord.from_dict(item) for item in history if isinstance(item, Mapping)],
        )

    @classmethod
    def from_plan(
        cls,
        plan: Any,
        *,
        saga_id: Optional[str] = None,
        trace_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "DeploymentSaga":
        normalized = _plan_to_dict(plan)
        return cls(
            saga_id=saga_id or f"deployment-saga-{normalized['plan_id']}",
            plan_id=str(normalized["plan_id"]),
            approval_decision_id=str(normalized["approval_decision_id"]),
            strategy_id=str(normalized["strategy_id"]),
            artifact_id=str(normalized["artifact_id"]),
            artifact_version=str(normalized["artifact_version"]),
            capital_pool_id=str(normalized["capital_pool_id"]),
            current_stage=str(normalized["current_stage"]),
            target_stage=str(normalized["target_stage"]),
            runtime_action=str(normalized["runtime_action"]),
            rollback_action_type=_parse_rollback_action(
                (normalized.get("rollback") or {}).get("action_type")
            ),
            status=SagaStatus.AWAITING_BINDING,
            current_step=SagaStep.BINDING_REQUESTED,
            trace_id=trace_id,
            created_at=utc_now(),
            updated_at=utc_now(),
            metadata=copy.deepcopy(metadata),
        )


@dataclass
class DeploymentSagaBootstrap:
    saga: DeploymentSaga
    outbox_event: OutboxRecord

    def to_dict(self) -> Dict[str, Any]:
        return {
            "saga": self.saga.to_dict(),
            "outbox_event": self.outbox_event.to_dict(),
        }


def validate_saga_json(data: Mapping[str, Any]) -> List[str]:
    return DeploymentSaga.from_dict(data).validate()


def validate_event_json(data: Mapping[str, Any]) -> List[str]:
    return SagaEventEnvelope.from_dict(data).validate()


class DeploymentSagaStore:
    """Persist deployment sagas, outbox records, and inbox receipts atomically."""

    def __init__(
        self,
        storage_path: Optional[str] = None,
        before_commit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self._storage_path = Path(storage_path) if storage_path else None
        self._before_commit = before_commit
        self._sagas: Dict[str, DeploymentSaga] = {}
        self._outbox: List[OutboxRecord] = []
        self._inbox: List[InboxReceipt] = []
        if self._storage_path and self._storage_path.exists():
            self._load()

    def bootstrap_for_plan(
        self,
        plan: Any,
        *,
        trace_id: str,
        saga_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeploymentSagaBootstrap:
        def mutate(draft: Dict[str, Any]) -> DeploymentSagaBootstrap:
            saga = DeploymentSaga.from_plan(plan, saga_id=saga_id, trace_id=trace_id, metadata=metadata)
            if saga.saga_id in draft["sagas"]:
                raise DeploymentSagaError(f"Duplicate saga_id: {saga.saga_id}")
            event = self._append_outbox_event(
                draft,
                saga=saga,
                owner_service="deployment-orchestrator",
                event_type="runtime.binding.requested",
                step=SagaStep.BINDING_REQUESTED,
                status=SagaStatus.AWAITING_BINDING,
                payload={
                    "plan_id": saga.plan_id,
                    "approval_decision_id": saga.approval_decision_id,
                    "artifact_id": saga.artifact_id,
                    "artifact_version": saga.artifact_version,
                    "capital_pool_id": saga.capital_pool_id,
                    "strategy_id": saga.strategy_id,
                    "target_stage": saga.target_stage,
                    "runtime_action": saga.runtime_action,
                },
            )
            return DeploymentSagaBootstrap(saga=copy.deepcopy(saga), outbox_event=copy.deepcopy(event))

        return self._transaction(mutate)

    def record_binding_created(
        self,
        saga_id: str,
        *,
        binding_id: str,
        runtime_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> OutboxRecord:
        def mutate(draft: Dict[str, Any]) -> OutboxRecord:
            saga = self._require_saga(draft, saga_id)
            if SagaStatus(saga.status) != SagaStatus.AWAITING_BINDING:
                raise DeploymentSagaError(f"{saga_id} is not awaiting binding")
            saga.binding_id = binding_id
            if runtime_id:
                saga.runtime_id = runtime_id
            return copy.deepcopy(
                self._append_outbox_event(
                    draft,
                    saga=saga,
                    owner_service="runtime-manager-svc",
                    event_type="runtime.load.requested",
                    step=SagaStep.RUNTIME_LOAD_REQUESTED,
                    status=SagaStatus.AWAITING_RUNTIME_LOAD,
                    payload={
                        "plan_id": saga.plan_id,
                        "binding_id": binding_id,
                        "runtime_id": runtime_id,
                        "target_stage": saga.target_stage,
                        "runtime_action": saga.runtime_action,
                    },
                    note=note,
                )
            )

        return self._transaction(mutate)

    def record_runtime_active(
        self,
        saga_id: str,
        *,
        binding_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> OutboxRecord:
        def mutate(draft: Dict[str, Any]) -> OutboxRecord:
            saga = self._require_saga(draft, saga_id)
            if SagaStatus(saga.status) not in {SagaStatus.AWAITING_BINDING, SagaStatus.AWAITING_RUNTIME_LOAD}:
                raise DeploymentSagaError(f"{saga_id} cannot move to runtime_active from {saga.status}")
            if binding_id:
                saga.binding_id = binding_id
            if runtime_id:
                saga.runtime_id = runtime_id
            return copy.deepcopy(
                self._append_outbox_event(
                    draft,
                    saga=saga,
                    owner_service="runtime-manager-svc",
                    event_type="deployment.saga.completed",
                    step=SagaStep.RUNTIME_ACTIVE,
                    status=SagaStatus.COMPLETED,
                    payload={
                        "plan_id": saga.plan_id,
                        "binding_id": saga.binding_id,
                        "runtime_id": saga.runtime_id,
                        "target_stage": saga.target_stage,
                    },
                    note=note,
                )
            )

        return self._transaction(mutate)

    def record_failure(
        self,
        saga_id: str,
        *,
        reason: str,
        failed_step: SagaStep | str | None = None,
    ) -> CompensationDecision:
        def mutate(draft: Dict[str, Any]) -> CompensationDecision:
            saga = self._require_saga(draft, saga_id)
            effective_step = SagaStep(failed_step or saga.current_step)
            compensation = determine_compensation(saga, effective_step, reason=reason)
            saga.failure_reason = reason
            saga.compensation = compensation
            self._append_outbox_event(
                draft,
                saga=saga,
                owner_service="deployment-orchestrator",
                event_type="deployment.compensation.requested",
                step=SagaStep.COMPENSATION_REQUESTED,
                status=SagaStatus.COMPENSATING,
                payload={
                    "plan_id": saga.plan_id,
                    "failed_step": effective_step.value,
                    "reason": reason,
                    "compensation": compensation.to_dict(),
                },
                note=reason,
            )
            return copy.deepcopy(compensation)

        return self._transaction(mutate)

    def finalize_compensation(
        self,
        saga_id: str,
        *,
        note: Optional[str] = None,
        terminal_status: Optional[SagaStatus | str] = None,
    ) -> OutboxRecord:
        def mutate(draft: Dict[str, Any]) -> OutboxRecord:
            saga = self._require_saga(draft, saga_id)
            if not saga.compensation:
                raise DeploymentSagaError(f"{saga_id} has no pending compensation decision")
            if SagaStatus(saga.status) != SagaStatus.COMPENSATING:
                raise DeploymentSagaError(f"{saga_id} is not compensating")
            resolved = SagaStatus(
                terminal_status
                or (
                    SagaStatus.ABORTED
                    if CompensationCommand(saga.compensation.command_type) == CompensationCommand.ABORT_PLAN
                    else SagaStatus.FAILED
                )
            )
            event_type = (
                "deployment.saga.aborted"
                if resolved == SagaStatus.ABORTED
                else "deployment.saga.failed"
            )
            return copy.deepcopy(
                self._append_outbox_event(
                    draft,
                    saga=saga,
                    owner_service=saga.compensation.owner_service,
                    event_type=event_type,
                    step=SagaStep.COMPENSATED,
                    status=resolved,
                    payload={
                        "plan_id": saga.plan_id,
                        "compensation": saga.compensation.to_dict(),
                        "failure_reason": saga.failure_reason,
                    },
                    note=note,
                )
            )

        return self._transaction(mutate)

    def consume_event(
        self,
        consumer_name: str,
        event: SagaEventEnvelope | Mapping[str, Any],
        apply_fn: Optional[Callable[[SagaEventEnvelope], None]] = None,
    ) -> InboxReceipt:
        normalized_event = (
            SagaEventEnvelope.from_dict(event) if isinstance(event, Mapping) else event
        )

        def mutate(draft: Dict[str, Any]) -> tuple[InboxReceipt, bool]:
            receipt, applied = self._build_receipt(draft, consumer_name, normalized_event)
            draft["inbox"].append(receipt)
            return copy.deepcopy(receipt), applied

        receipt, applied = self._transaction(mutate)
        if applied and apply_fn:
            apply_fn(normalized_event)
        return receipt

    def mark_outbox_published(self, event_id: str) -> OutboxRecord:
        def mutate(draft: Dict[str, Any]) -> OutboxRecord:
            record = self._require_outbox_record(draft, event_id)
            if OutboxStatus(record.status) == OutboxStatus.PUBLISHED:
                return copy.deepcopy(record)
            if OutboxStatus(record.status) == OutboxStatus.DEAD_LETTERED:
                raise DeploymentSagaError(
                    f"Outbox event '{event_id}' is dead-lettered; replay it before consuming."
                )
            record.status = OutboxStatus.PUBLISHED
            record.published_at = record.published_at or utc_now()
            record.next_retry_at = None
            record.blocked_reason = None
            return copy.deepcopy(record)

        return self._transaction(mutate)

    def record_outbox_failure(
        self,
        event_id: str,
        *,
        reason: str,
        consumer_name: str,
        retryable: bool = True,
        max_attempts: int = 3,
        retry_delay_seconds: int = 0,
    ) -> OutboxRecord:
        if max_attempts < 1:
            raise DeploymentSagaError("max_attempts must be >= 1")

        def mutate(draft: Dict[str, Any]) -> OutboxRecord:
            record = self._require_outbox_record(draft, event_id)
            status = OutboxStatus(record.status)
            if status == OutboxStatus.PUBLISHED:
                return copy.deepcopy(record)

            now = utc_now()
            record.delivery_attempts += 1
            record.last_attempt_at = now
            record.last_error = reason
            record.retry_policy = {
                "consumer_name": consumer_name,
                "retryable": retryable,
                "max_attempts": max_attempts,
                "retry_delay_seconds": max(0, retry_delay_seconds),
            }
            if retryable and record.delivery_attempts < max_attempts:
                record.status = OutboxStatus.PENDING
                record.next_retry_at = (
                    _utc_after_seconds(retry_delay_seconds)
                    if retry_delay_seconds > 0
                    else None
                )
                record.blocked_reason = None
                record.dlq_at = None
            else:
                record.status = OutboxStatus.DEAD_LETTERED
                record.next_retry_at = None
                record.blocked_reason = reason
                record.dlq_at = now
            return copy.deepcopy(record)

        return self._transaction(mutate)

    def replay_outbox_event(
        self,
        event_id: str,
        *,
        reason: Optional[str] = None,
    ) -> tuple[OutboxRecord, bool]:
        def mutate(draft: Dict[str, Any]) -> tuple[OutboxRecord, bool]:
            record = self._require_outbox_record(draft, event_id)
            status = OutboxStatus(record.status)
            if status not in {OutboxStatus.DEAD_LETTERED, OutboxStatus.FAILED}:
                return copy.deepcopy(record), False

            record.status = OutboxStatus.PENDING
            record.next_retry_at = None
            record.blocked_reason = None
            record.dlq_at = None
            record.last_replayed_at = utc_now()
            record.replay_count += 1
            if reason:
                policy = dict(record.retry_policy or {})
                policy["last_replay_reason"] = reason
                record.retry_policy = policy
            return copy.deepcopy(record), True

        return self._transaction(mutate)

    def get(self, saga_id: str) -> Optional[DeploymentSaga]:
        saga = self._sagas.get(saga_id)
        return copy.deepcopy(saga) if saga else None

    def list_all(self) -> List[DeploymentSaga]:
        return [copy.deepcopy(item) for item in self._sagas.values()]

    def pending_outbox(self) -> List[OutboxRecord]:
        return self.outbox_records(status=OutboxStatus.PENDING.value)

    def outbox_records(self, status: Optional[OutboxStatus | str] = None) -> List[OutboxRecord]:
        if status is None:
            records = self._outbox
        else:
            expected = OutboxStatus(status)
            records = [
                record
                for record in self._outbox
                if OutboxStatus(record.status) == expected
            ]
        return [
            copy.deepcopy(record)
            for record in records
        ]

    def inbox_receipts(self, consumer_name: Optional[str] = None) -> List[InboxReceipt]:
        receipts = self._inbox
        if consumer_name:
            receipts = [receipt for receipt in receipts if receipt.consumer_name == consumer_name]
        return [copy.deepcopy(receipt) for receipt in receipts]

    def _transaction(self, mutator: Callable[[Dict[str, Any]], Any]) -> Any:
        draft = {
            "sagas": copy.deepcopy(self._sagas),
            "outbox": copy.deepcopy(self._outbox),
            "inbox": copy.deepcopy(self._inbox),
        }
        result = mutator(draft)
        self._validate_state(draft)
        if self._before_commit:
            self._before_commit(draft)
        self._persist_draft(draft)
        self._sagas = draft["sagas"]
        self._outbox = draft["outbox"]
        self._inbox = draft["inbox"]
        return result

    def _append_outbox_event(
        self,
        draft: Dict[str, Any],
        *,
        saga: DeploymentSaga,
        owner_service: str,
        event_type: str,
        step: SagaStep,
        status: SagaStatus,
        payload: Dict[str, Any],
        note: Optional[str] = None,
    ) -> OutboxRecord:
        sequence_no = saga.last_sequence_no + 1
        emitted_at = utc_now()
        event = SagaEventEnvelope(
            event_id=f"{saga.saga_id}-evt-{sequence_no:04d}",
            event_type=event_type,
            aggregate_type="deployment_saga",
            aggregate_id=saga.saga_id,
            sequence_no=sequence_no,
            causal_parent_id=saga.last_event_id,
            event_time=emitted_at,
            emitted_at=emitted_at,
            trace_id=saga.trace_id,
            idempotency_key=f"{saga.saga_id}:{sequence_no}:{event_type}",
            payload=copy.deepcopy(payload),
        )
        saga.current_step = step
        saga.status = status
        saga.updated_at = emitted_at
        saga.last_sequence_no = sequence_no
        saga.last_event_id = event.event_id
        saga.history.append(
            SagaStepRecord(
                step=step,
                status=status,
                event_id=event.event_id,
                sequence_no=sequence_no,
                emitted_at=emitted_at,
                note=note,
            )
        )
        draft["sagas"][saga.saga_id] = saga
        record = OutboxRecord(owner_service=owner_service, event=event)
        draft["outbox"].append(record)
        return record

    def _build_receipt(
        self,
        draft: Dict[str, Any],
        consumer_name: str,
        event: SagaEventEnvelope,
    ) -> tuple[InboxReceipt, bool]:
        now = utc_now()
        applied_receipts = [
            receipt
            for receipt in draft["inbox"]
            if receipt.consumer_name == consumer_name
            and receipt.aggregate_type == event.aggregate_type
            and receipt.aggregate_id == event.aggregate_id
            and ReceiptStatus(receipt.status) == ReceiptStatus.APPLIED
        ]
        duplicate_event = any(
            receipt.consumer_name == consumer_name
            and receipt.event_id == event.event_id
            and ReceiptStatus(receipt.status) in {ReceiptStatus.APPLIED, ReceiptStatus.DUPLICATE}
            for receipt in draft["inbox"]
        )
        duplicate_idempotency = any(
            receipt.consumer_name == consumer_name
            and receipt.idempotency_key == event.idempotency_key
            and ReceiptStatus(receipt.status) == ReceiptStatus.APPLIED
            for receipt in draft["inbox"]
        )
        if duplicate_event or duplicate_idempotency:
            return (
                InboxReceipt(
                    consumer_name=consumer_name,
                    event_id=event.event_id,
                    idempotency_key=event.idempotency_key,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    sequence_no=event.sequence_no,
                    trace_id=event.trace_id,
                    status=ReceiptStatus.DUPLICATE,
                    processed_at=now,
                    notes="Duplicate delivery skipped by event_id / idempotency_key.",
                ),
                False,
            )

        last_sequence = max((receipt.sequence_no for receipt in applied_receipts), default=0)
        if event.sequence_no <= last_sequence:
            return (
                InboxReceipt(
                    consumer_name=consumer_name,
                    event_id=event.event_id,
                    idempotency_key=event.idempotency_key,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    sequence_no=event.sequence_no,
                    trace_id=event.trace_id,
                    status=ReceiptStatus.DUPLICATE,
                    processed_at=now,
                    notes="Sequence already applied for this aggregate.",
                ),
                False,
            )
        if event.sequence_no != last_sequence + 1:
            return (
                InboxReceipt(
                    consumer_name=consumer_name,
                    event_id=event.event_id,
                    idempotency_key=event.idempotency_key,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    sequence_no=event.sequence_no,
                    trace_id=event.trace_id,
                    status=ReceiptStatus.OUT_OF_ORDER,
                    processed_at=now,
                    notes=f"Expected sequence {last_sequence + 1}, got {event.sequence_no}.",
                ),
                False,
            )
        return (
            InboxReceipt(
                consumer_name=consumer_name,
                event_id=event.event_id,
                idempotency_key=event.idempotency_key,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                sequence_no=event.sequence_no,
                trace_id=event.trace_id,
                status=ReceiptStatus.APPLIED,
                processed_at=now,
            ),
            True,
        )

    def _validate_state(self, draft: Dict[str, Any]) -> None:
        for saga in draft["sagas"].values():
            errors = saga.validate()
            if errors:
                raise DeploymentSagaError("; ".join(errors))
        for record in draft["outbox"]:
            OutboxStatus(record.status)
            errors = record.event.validate()
            if errors:
                raise DeploymentSagaError("; ".join(errors))

    def _persist_draft(self, draft: Dict[str, Any]) -> None:
        if not self._storage_path:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sagas": {saga_id: saga.to_dict() for saga_id, saga in draft["sagas"].items()},
            "outbox": [record.to_dict() for record in draft["outbox"]],
            "inbox": [receipt.to_dict() for receipt in draft["inbox"]],
        }
        tmp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._storage_path)

    def _load(self) -> None:
        if not self._storage_path:
            return
        text = self._storage_path.read_text(encoding="utf-8")
        if not text.strip():
            return
        raw = json.loads(text)
        self._sagas = {
            saga_id: DeploymentSaga.from_dict(payload)
            for saga_id, payload in raw.get("sagas", {}).items()
        }
        self._outbox = [OutboxRecord.from_dict(item) for item in raw.get("outbox", [])]
        self._inbox = [InboxReceipt.from_dict(item) for item in raw.get("inbox", [])]

    @staticmethod
    def _require_saga(draft: Dict[str, Any], saga_id: str) -> DeploymentSaga:
        saga = draft["sagas"].get(saga_id)
        if not saga:
            raise DeploymentSagaError(f"Unknown saga: {saga_id}")
        return saga

    @staticmethod
    def _require_outbox_record(draft: Dict[str, Any], event_id: str) -> OutboxRecord:
        for record in draft["outbox"]:
            if record.event.event_id == event_id:
                return record
        raise DeploymentSagaError(f"Outbox event '{event_id}' not found")


class DeploymentSagaOrchestrator:
    """Thin orchestration facade used by cron deploy and future workers."""

    def __init__(self, store: Optional[DeploymentSagaStore] = None):
        self.store = store or DeploymentSagaStore()

    def bootstrap(
        self,
        plan: Any,
        *,
        trace_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeploymentSagaBootstrap:
        return self.store.bootstrap_for_plan(plan, trace_id=trace_id, metadata=metadata)


def determine_compensation(
    saga: DeploymentSaga,
    failed_step: SagaStep | str,
    *,
    reason: str,
) -> CompensationDecision:
    step = SagaStep(failed_step)
    if step == SagaStep.BINDING_REQUESTED:
        return CompensationDecision(
            command_type=CompensationCommand.ABORT_PLAN,
            owner_service="governance-svc",
            plan_status="aborted",
            binding_status="not_created",
            write_scope="DeploymentPlan.status only; no runtime tables are touched.",
            reason=reason,
        )
    if step == SagaStep.RUNTIME_LOAD_REQUESTED:
        return CompensationDecision(
            command_type=CompensationCommand.MARK_BINDING_FAILED_INACTIVE,
            owner_service="runtime-manager-svc",
            plan_status="failed",
            binding_status="failed_inactive",
            write_scope="RuntimeBinding.status only; DeploymentPlan stays immutable and saga records the failure.",
            reason=reason,
        )
    if step == SagaStep.RUNTIME_ACTIVE:
        rollback_action = saga.rollback_action_type or RollbackActionType.REPLACE.value
        return CompensationDecision(
            command_type=CompensationCommand.REQUEST_ROLLBACK,
            owner_service="rollback-controller",
            plan_status="failed",
            binding_status="superseding",
            write_scope="Rollback controller issues the command; runtime-manager writes the replacement binding.",
            runtime_action=rollback_action,
            reason=reason,
        )
    if step == SagaStep.COMPENSATION_REQUESTED:
        return CompensationDecision(
            command_type=CompensationCommand.ENTER_SAFE_MODE_AND_RAISE_INCIDENT,
            owner_service="runtime-manager-svc",
            plan_status="failed",
            binding_status="paused_safe_mode",
            write_scope="Runtime manager enters safe mode; telemetry-evolution / incident owner opens the incident trail.",
            incident_required=True,
            safe_mode=True,
            reason=reason,
        )
    raise DeploymentSagaError(f"Unsupported failed_step for compensation: {step.value}")

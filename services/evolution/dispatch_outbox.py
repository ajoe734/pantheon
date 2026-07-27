"""Durable dispatch outbox for approved EvolutionDecision actions.

L12-EVO-001.

Before this module, an approved decision was dispatched by an in-process HTTP
call and immediately recorded as ``executed`` with a ``submitted`` execution
result.  Nothing survived a crash between approval and dispatch, nothing
distinguished "we asked" from "it happened", and a retried trigger could
re-dispatch the same approved action.

This module supplies the durable half of the fix:

* **One intent per approved action.**  ``outbox_id``/``event_id``/
  ``idempotency_key`` are derived deterministically from
  ``(tenant_id, decision_id)``, so a duplicate trigger reuses the same durable
  record instead of creating a second dispatch.
* **Prepare before, activate after.**  The intent is persisted while the
  decision is still being approved and only becomes deliverable once the
  approval is durable.  A crash in between leaves a prepared record that
  :func:`reconcile_dispatch_outbox` activates on the next worker start.
* **Retry, DLQ, and replay cooldown.**  Failed attempts back off and eventually
  dead-letter.  A dead-lettered record may only be replayed after a cooldown has
  elapsed since its last failure, and the replay count is part of the durable
  record, so restarts and duplicate replay triggers cannot bypass the window.
* **Compensation.**  A dispatch that dead-letters after the downstream was
  already touched records a durable, idempotent compensation entry, so a
  half-applied dispatch is visible instead of silently lost.

The terminal-receipt half lives in :mod:`services.evolution.dispatch_receipts`.
This module never decides that a decision executed; it only makes the attempt
durable and auditable.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from services.foundation import (
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    OutboxRecord,
    OutboxRecordStatus,
    TraceContext,
)
from services.foundation.reliable_delivery import (
    ReliableOutboxRecord,
    ReliableOutboxStore,
    build_record_store,
    reconcile_prepared,
)

OWNER_SERVICE = "evolution-svc"
DISPATCH_EVENT_TYPE = "evolution.dispatch_requested"
AGGREGATE_TYPE = "evolution_decision"

# Deterministic-id namespace.  Keeping it a module constant means the same
# (tenant, decision) pair always resolves to the same durable record, across
# processes and restarts, without any shared sequence.
_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "pantheon:evolution:dispatch-outbox")

# Delivery-attempt policy.  Deliberately small and explicit: a dispatch that
# has failed this many times needs an operator, not more automatic retries.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY_SECONDS = 30.0
DEFAULT_CLAIM_LEASE_SECONDS = 60.0

# Minimum time between a dead-letter and the replay that revives it.  This is
# the DLQ replay cooldown: it stops a duplicate replay trigger (or a restarted
# worker re-reading the DLQ) from hammering a downstream that is still broken.
DEFAULT_REPLAY_COOLDOWN_SECONDS = 900.0


class EvolutionDispatchError(RuntimeError):
    """Raised when a dispatch intent cannot be made durable or replayed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _delivery_environment(target_stage: str | None) -> EnvironmentName:
    """Map a decision's target stage onto the event environment scope.

    Mirrors the incident service's mapping so a dispatch event and the incident
    that triggered it land in the same environment scope.  ``frozen`` is a
    governance state of a live runtime, not an environment of its own.
    """
    normalized = str(target_stage or "").strip().lower()
    if normalized == "frozen":
        normalized = EnvironmentName.LIVE.value
    try:
        return EnvironmentName(normalized)
    except ValueError:
        configured = os.getenv("PANTHEON_EVENT_ENVIRONMENT", EnvironmentName.DEV.value)
        try:
            return EnvironmentName(configured.strip().lower())
        except ValueError:
            return EnvironmentName.DEV


def dispatch_identity(tenant_id: str, decision_id: str) -> tuple[str, str, str]:
    """Return the deterministic ``(outbox_id, event_id, idempotency_key)``.

    Tenant is part of the key so two tenants that legitimately hold decisions
    with the same id cannot collapse onto one durable dispatch record.
    """
    tenant = str(tenant_id).strip()
    decision = str(decision_id).strip()
    if not tenant or not decision:
        raise EvolutionDispatchError("dispatch identity requires both tenant_id and decision_id")
    seed = f"{tenant}:{decision}"
    outbox_id = f"evo-dispatch-{uuid.uuid5(_ID_NAMESPACE, seed)}"
    event_id = str(uuid.uuid5(_ID_NAMESPACE, f"event:{seed}"))
    idempotency_key = f"evolution-dispatch:{seed}"
    return outbox_id, event_id, idempotency_key


@dataclass(frozen=True)
class DispatchIntent:
    """The approved action a durable outbox record stands for."""

    tenant_id: str
    decision_id: str
    action_type: str
    execution_plane: str
    boundary_key: str
    target_type: str
    target_id: str
    target_version: str
    target_stage: str | None = None
    approval_decision_id: str | None = None
    command_id: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "decision_id": self.decision_id,
            "action_type": self.action_type,
            "execution_plane": self.execution_plane,
            "boundary_key": self.boundary_key,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_version": self.target_version,
            "target_stage": self.target_stage,
            "approval_decision_id": self.approval_decision_id,
            "command_id": self.command_id,
        }

    def transition(self) -> dict[str, Any]:
        """The local state the intent is waiting on before it may be delivered.

        A prepared record is only activated once the decision is durably in one
        of these states.  ``executed`` is included so a reconcile that runs
        after a successful dispatch still activates rather than discarding a
        record whose delivery already landed.
        """
        return {
            "aggregate_type": AGGREGATE_TYPE,
            "aggregate_id": self.decision_id,
            "tenant_id": self.tenant_id,
            "expected_states": ["approved", "executed"],
        }


def build_dispatch_outbox_store(
    *,
    data_dir: str | Path,
    backend: str | None = None,
    dsn: str | None = None,
) -> ReliableOutboxStore:
    resolved_backend = (
        backend or os.getenv("EVOLUTION_STORE_BACKEND", "json")
    ).strip().lower() or "json"
    return ReliableOutboxStore(
        backend=resolved_backend,
        dsn=dsn or os.getenv("EVOLUTION_STORE_DSN") or os.getenv("DATABASE_URL"),
        table_name="evolution.dispatch_outbox",
        json_path=Path(data_dir) / "dispatch_outbox.json",
        owner_service=OWNER_SERVICE,
    )


class CompensationLedger:
    """Durable, idempotent record of dispatches that need unwinding.

    A dead-lettered dispatch that already touched a downstream leaves work the
    Evolution plane does not own and cannot roll back itself.  Writing the
    compensation here keeps that obligation visible across restarts instead of
    living only in a log line, and the write is keyed by
    ``(tenant, decision)`` so a duplicate trigger records one obligation.
    """

    def __init__(
        self,
        *,
        data_dir: str | Path,
        backend: str | None = None,
        dsn: str | None = None,
    ) -> None:
        resolved_backend = (
            backend or os.getenv("EVOLUTION_STORE_BACKEND", "json")
        ).strip().lower() or "json"
        self._impl = build_record_store(
            backend=resolved_backend,
            dsn=dsn or os.getenv("EVOLUTION_STORE_DSN") or os.getenv("DATABASE_URL"),
            table_name="evolution.dispatch_compensations",
            json_path=Path(data_dir) / "dispatch_compensations.json",
            owner_service=OWNER_SERVICE,
        )

    @staticmethod
    def compensation_id(tenant_id: str, decision_id: str) -> str:
        outbox_id, _, _ = dispatch_identity(tenant_id, decision_id)
        return f"comp-{outbox_id}"

    def record(
        self,
        *,
        tenant_id: str,
        decision_id: str,
        outbox_id: str,
        reason: str,
        downstream_kind: str | None = None,
        downstream_ref_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record one compensation obligation, idempotently.

        Returns the canonical stored record.  A repeat call for the same
        decision returns the original entry unchanged: the obligation was
        already recorded, and overwriting it would lose the first failure's
        reason and timestamp.
        """
        record_id = self.compensation_id(tenant_id, decision_id)
        payload = {
            "compensation_id": record_id,
            "tenant_id": tenant_id,
            "decision_id": decision_id,
            "outbox_id": outbox_id,
            "reason": reason,
            "downstream_kind": downstream_kind,
            "downstream_ref_id": downstream_ref_id,
            "recorded_at": _format_time(now or _utc_now()),
            "resolved": False,
            "resolved_at": None,
            "resolved_by": None,
            "resolution_note": None,
        }
        _, canonical = self._impl.insert_if_absent(record_id, payload)
        return dict(canonical)

    def get(self, tenant_id: str, decision_id: str) -> dict[str, Any] | None:
        stored = self._impl.get(self.compensation_id(tenant_id, decision_id))
        return dict(stored) if stored is not None else None

    def list_all(self, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        records = [dict(item) for item in self._impl.list_all()]
        if tenant_id is None:
            return records
        return [item for item in records if str(item.get("tenant_id") or "") == tenant_id]

    def resolve(
        self,
        *,
        tenant_id: str,
        decision_id: str,
        actor_id: str,
        note: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        record_id = self.compensation_id(tenant_id, decision_id)
        existing = self._impl.get(record_id)
        if existing is None:
            raise EvolutionDispatchError(f"no compensation recorded for decision {decision_id}")
        if existing.get("resolved"):
            return dict(existing)
        if not str(actor_id).strip() or not str(note).strip():
            raise EvolutionDispatchError("compensation resolution requires an actor_id and a note")
        updated = dict(existing)
        updated.update(
            {
                "resolved": True,
                "resolved_at": _format_time(now or _utc_now()),
                "resolved_by": str(actor_id).strip(),
                "resolution_note": str(note).strip(),
            }
        )
        replaced, canonical = self._impl.compare_and_set(record_id, dict(existing), updated)
        if replaced:
            return updated
        if canonical is None:
            raise EvolutionDispatchError(f"compensation record disappeared: {record_id}")
        return dict(canonical)


class EvolutionDispatchOutbox:
    """Durable outbox for approved EvolutionDecision dispatch intents."""

    def __init__(
        self,
        store: ReliableOutboxStore,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
        replay_cooldown_seconds: float = DEFAULT_REPLAY_COOLDOWN_SECONDS,
    ) -> None:
        self.store = store
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))
        self.replay_cooldown_seconds = max(0.0, float(replay_cooldown_seconds))

    # -- write path ---------------------------------------------------------

    def prepare(self, intent: DispatchIntent) -> ReliableOutboxRecord:
        """Persist the dispatch intent before the approval is durable.

        Repeating this for the same decision returns the existing record: the
        deterministic id plus ``ReliableOutboxStore.prepare``'s identity check
        make a duplicate trigger a no-op rather than a second dispatch.  A
        genuinely different intent under the same id fails closed there.
        """
        outbox_id, event_id, idempotency_key = dispatch_identity(
            intent.tenant_id, intent.decision_id
        )
        trace = TraceContext.new(
            environment=EnvironmentScope(name=_delivery_environment(intent.target_stage)),
            source_system=OWNER_SERVICE,
            idempotency_key=idempotency_key,
        )
        event = EventEnvelope(
            event_id=event_id,
            event_type=DISPATCH_EVENT_TYPE,
            aggregate_type=AGGREGATE_TYPE,
            aggregate_id=intent.decision_id,
            sequence_no=1,
            trace=trace,
            payload=intent.payload(),
            idempotency_key=idempotency_key,
            producer_service=OWNER_SERVICE,
        )
        record = OutboxRecord(
            outbox_id=outbox_id,
            owner_service=OWNER_SERVICE,
            event=event,
        )
        return self.store.prepare(record=record, transition=intent.transition())

    def activate(self, record: ReliableOutboxRecord) -> ReliableOutboxRecord:
        """Make a prepared intent deliverable once the approval is durable."""
        return self.store.activate(record)

    def get(self, tenant_id: str, decision_id: str) -> ReliableOutboxRecord | None:
        outbox_id, _, _ = dispatch_identity(tenant_id, decision_id)
        return self.store.get(outbox_id)

    def get_by_id(self, outbox_id: str) -> ReliableOutboxRecord | None:
        return self.store.get(outbox_id)

    # -- read path ----------------------------------------------------------

    def list_all(self, *, tenant_id: str | None = None) -> list[ReliableOutboxRecord]:
        records = self.store.list_all()
        if tenant_id is None:
            return records
        return [item for item in records if _record_tenant(item) == tenant_id]

    def list_dead_lettered(self, *, tenant_id: str | None = None) -> list[ReliableOutboxRecord]:
        return [
            record
            for record in self.list_all(tenant_id=tenant_id)
            if record.status == OutboxRecordStatus.DEAD_LETTERED
        ]

    def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: float = DEFAULT_CLAIM_LEASE_SECONDS,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[ReliableOutboxRecord]:
        return self.store.claim_due(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
            limit=limit,
        )

    # -- completion ---------------------------------------------------------

    def complete_published(
        self, claimed: ReliableOutboxRecord
    ) -> tuple[bool, ReliableOutboxRecord]:
        return self.store.complete_published(claimed)

    def complete_failed(
        self,
        claimed: ReliableOutboxRecord,
        error: str,
        *,
        permanent: bool = False,
        now: datetime | None = None,
    ) -> tuple[bool, ReliableOutboxRecord]:
        return self.store.complete_failed(
            claimed,
            error,
            max_attempts=self.max_attempts,
            base_delay_seconds=self.base_delay_seconds,
            now=now,
            permanent=permanent,
        )

    def dead_letter_terminal_failure(
        self,
        record: ReliableOutboxRecord,
        error: str,
        *,
        now: datetime | None = None,
    ) -> tuple[bool, ReliableOutboxRecord]:
        """Durably settle a directly verified terminal downstream failure.

        The dispatch worker normally settles a leased record through
        :meth:`complete_failed`.  The HTTP execute route can also be called
        directly with a downstream receipt, however, and that caller does not
        own a worker lease.  Requiring a lease there left a real failed receipt
        able to mark the decision ``executed`` while its outbox stayed pending.

        Compare-and-set the current canonical snapshot instead.  This safely
        fences a concurrent worker claim: one writer records the terminal DLQ
        state and the other observes it.  Repeating the same failed receipt is
        idempotent and does not consume another delivery attempt.
        """
        if not str(error).strip():
            raise EvolutionDispatchError("terminal dispatch failure requires an error")

        canonical = self.store.get(record.outbox_id)
        if canonical is None:
            raise EvolutionDispatchError(
                f"dispatch outbox record disappeared: {record.outbox_id}"
            )

        for _ in range(8):
            if canonical.status == OutboxRecordStatus.DEAD_LETTERED:
                return False, canonical
            if canonical.status == OutboxRecordStatus.PUBLISHED:
                raise EvolutionDispatchError(
                    f"cannot dead-letter published dispatch {record.outbox_id}"
                )

            completed = canonical.mark_failed(
                error,
                max_attempts=self.max_attempts,
                base_delay_seconds=self.base_delay_seconds,
                now=now,
                permanent=True,
            )
            replaced, observed_payload = self.store.impl.compare_and_set(
                canonical.outbox_id,
                canonical.to_dict(),
                completed.to_dict(),
            )
            if replaced:
                return True, completed
            if observed_payload is None:
                raise EvolutionDispatchError(
                    f"dispatch outbox record disappeared: {record.outbox_id}"
                )
            canonical = ReliableOutboxRecord.from_dict(observed_payload)

        raise EvolutionDispatchError(
            f"dispatch {record.outbox_id} changed repeatedly while recording terminal failure"
        )

    # -- DLQ replay ---------------------------------------------------------

    def replay_available_at(self, record: ReliableOutboxRecord) -> datetime | None:
        """When a dead-lettered record becomes eligible for replay.

        Measured from the record's last update — the moment it dead-lettered —
        so the window is derived from durable state and survives a restart
        rather than from any in-memory timer.
        """
        if record.status != OutboxRecordStatus.DEAD_LETTERED:
            return None
        last_update = _parse_time(record.record.updated_at)
        if last_update is None:
            return None
        return last_update + timedelta(seconds=self.replay_cooldown_seconds)

    def replay(
        self,
        outbox_id: str,
        *,
        actor: str,
        note: str,
        now: datetime | None = None,
    ) -> ReliableOutboxRecord:
        """Redrive a dead-lettered dispatch once its replay cooldown elapsed.

        Fails closed on every path that would let a broken downstream be
        hammered: a record that is not dead-lettered, and a record still inside
        its cooldown.  Because the cooldown is computed from durable state, a
        restart or a duplicate replay trigger sees the same answer.
        """
        record = self.store.get(outbox_id)
        if record is None:
            raise EvolutionDispatchError(f"unknown dispatch outbox record: {outbox_id}")
        if record.status != OutboxRecordStatus.DEAD_LETTERED:
            raise EvolutionDispatchError(
                f"only dead-lettered dispatches may be replayed: {outbox_id} is "
                f"{record.status.value}"
            )
        moment = now or _utc_now()
        available_at = self.replay_available_at(record)
        if available_at is not None and moment < available_at:
            raise EvolutionDispatchError(
                f"dispatch {outbox_id} is inside its DLQ replay cooldown until "
                f"{_format_time(available_at)}"
            )
        return self.store.redrive(outbox_id, actor=actor, note=note)


def _record_tenant(record: ReliableOutboxRecord) -> str:
    payload = record.event.payload or {}
    return str(payload.get("tenant_id") or "")


def reconcile_dispatch_outbox(
    outbox: EvolutionDispatchOutbox,
    *,
    transition_applied: Callable[[Mapping[str, Any]], bool],
) -> int:
    """Activate prepared intents whose decision is durably approved.

    This is the crash-after-approval recovery path: the approval commit and the
    outbox activation are separate writes, so a process that dies between them
    leaves an inert prepared record.  Running this at worker start turns that
    record back into a deliverable dispatch instead of an approved decision
    that is never dispatched.
    """
    return reconcile_prepared(outbox.store, transition_applied=transition_applied)

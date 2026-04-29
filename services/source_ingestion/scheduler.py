"""Governed ingestion scheduling with replayable watermarks and DLQ routing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    DeadLetterEntry,
    DeadLetterQueue,
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    TraceContext,
)

from .connectors.base import IngestRun, IngestRunStatus, SourceEvidenceError, SourceRecord
from .ingest_manager import IngestManager


@dataclass(frozen=True)
class SourceWatermark:
    connector_id: str
    source_type: str
    value: str | None
    updated_at: str
    last_ingest_run_id: str | None = None
    schema_version: str = "source_watermark.v1"

    def __post_init__(self) -> None:
        if not str(self.connector_id).strip():
            raise SourceEvidenceError("watermark connector_id is required")
        if not str(self.source_type).strip():
            raise SourceEvidenceError("watermark source_type is required")
        if not str(self.updated_at).strip():
            raise SourceEvidenceError("watermark updated_at is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "connector_id": self.connector_id,
            "source_type": self.source_type,
            "value": self.value,
            "updated_at": self.updated_at,
            "last_ingest_run_id": self.last_ingest_run_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceWatermark":
        return cls(
            connector_id=str(data["connector_id"]),
            source_type=str(data["source_type"]),
            value=data.get("value"),
            updated_at=str(data["updated_at"]),
            last_ingest_run_id=data.get("last_ingest_run_id"),
            schema_version=str(data.get("schema_version", "source_watermark.v1")),
        )


@dataclass(frozen=True)
class IngestBatch:
    records: tuple[SourceRecord, ...] | list[SourceRecord] = field(default_factory=tuple)
    next_watermark: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        for record in self.records:
            if not isinstance(record, SourceRecord):
                raise SourceEvidenceError("ingest batch records must be SourceRecord instances")


@dataclass(frozen=True)
class ScheduledIngestResult:
    run: IngestRun
    watermark: SourceWatermark | None
    records: tuple[SourceRecord, ...]
    dlq_entries: tuple[DeadLetterEntry, ...] = field(default_factory=tuple)
    audit_actions: tuple[AuditAction, ...] = field(default_factory=tuple)


class JsonlIngestScheduleStore:
    """Append/replay store for ingest-run state and connector watermarks."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._runs: dict[str, IngestRun] = {}
        self._watermarks: dict[str, SourceWatermark] = {}
        self.reload()

    def reload(self) -> None:
        self._runs = {}
        self._watermarks = {}
        if not self.path.exists():
            return
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceEvidenceError(f"Invalid ingest schedule JSONL at {self.path}:{line_no}: {exc.msg}") from exc
            record_type = str(entry.get("record_type") or "")
            payload = entry.get("payload")
            if not isinstance(payload, Mapping):
                raise SourceEvidenceError(f"Invalid ingest schedule payload at {self.path}:{line_no}")
            if record_type == "ingest_run":
                run = IngestRun.from_dict(payload)
                self._runs[run.ingest_run_id] = run
            elif record_type == "source_watermark":
                watermark = SourceWatermark.from_dict(payload)
                self._watermarks[watermark.connector_id] = watermark
            else:
                raise SourceEvidenceError(f"Unsupported ingest schedule record: {record_type or '<missing>'}")

    def upsert_run(self, run: IngestRun) -> IngestRun:
        self._runs[run.ingest_run_id] = run
        self._append("ingest_run", run.ingest_run_id, run.to_dict())
        return run

    def update_watermark(self, watermark: SourceWatermark) -> SourceWatermark:
        self._watermarks[watermark.connector_id] = watermark
        self._append("source_watermark", watermark.connector_id, watermark.to_dict())
        return watermark

    def get_run(self, ingest_run_id: str) -> IngestRun | None:
        return self._runs.get(ingest_run_id)

    def list_runs(self) -> list[IngestRun]:
        return list(self._runs.values())

    def get_watermark(self, connector_id: str) -> SourceWatermark | None:
        return self._watermarks.get(connector_id)

    def _append(self, record_type: str, record_id: str, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": "ingest_schedule_store.v1",
            "record_type": record_type,
            "record_id": record_id,
            "payload": dict(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


class IngestionScheduler:
    """Runs governed batch ingestion from persisted watermarks."""

    def __init__(
        self,
        *,
        manager: IngestManager,
        store: JsonlIngestScheduleStore,
        dead_letter_queue: DeadLetterQueue,
        environment: EnvironmentScope | None = None,
        actor_ref: ActorRef | None = None,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise SourceEvidenceError("max_attempts must be >= 1")
        self.manager = manager
        self.store = store
        self.dead_letter_queue = dead_letter_queue
        self.environment = environment or EnvironmentScope(EnvironmentName.PAPER, timezone="UTC")
        self.actor_ref = actor_ref or ActorRef(ActorType.SERVICE, "source-ingestion-scheduler", roles=("source_ingest",))
        self.max_attempts = max_attempts

    def run_once(
        self,
        *,
        connector_id: str,
        fetch_batch: Callable[[str | None], IngestBatch | Iterable[SourceRecord]],
        trace_id: str,
        trigger_type: str = "scheduled",
    ) -> ScheduledIngestResult:
        connector = self.manager.get_connector(connector_id)
        if connector is None:
            raise SourceEvidenceError(f"Unknown connector: {connector_id}")

        run = self.manager.start_ingest_run(
            connector_id=connector_id,
            trigger_type=trigger_type,
            trace_id=trace_id,
        )
        self.store.upsert_run(run)
        starting_watermark = self.store.get_watermark(connector_id)
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                fetched = fetch_batch(starting_watermark.value if starting_watermark else None)
                batch = fetched if isinstance(fetched, IngestBatch) else IngestBatch(records=tuple(fetched))
                for record in batch.records:
                    if record.connector_id != connector_id:
                        raise SourceEvidenceError("fetched record connector_id must match job connector")
                    if record.source_type != connector.source_type:
                        raise SourceEvidenceError("fetched record source_type must match job connector")
                run.raw_count = len(batch.records)
                run.normalized_count = sum(1 for record in batch.records if not record.is_rejected)
                run.rejected_count = sum(1 for record in batch.records if record.is_rejected)
                rejected_dlq_entries: list[DeadLetterEntry] = []
                rejected_audit_actions: list[AuditAction] = []
                for index, record in enumerate((record for record in batch.records if record.is_rejected), start=1):
                    dlq_entry, audit_action = self._dead_letter_rejected_record(
                        run=run,
                        record=record,
                        connector_id=connector_id,
                        trigger_type=trigger_type,
                        trace_id=trace_id,
                        starting_watermark=starting_watermark.value if starting_watermark else None,
                        sequence_no=index,
                    )
                    rejected_dlq_entries.append(dlq_entry)
                    rejected_audit_actions.append(audit_action)
                if run.status == IngestRunStatus.FETCHING:
                    run.transition(IngestRunStatus.NORMALIZING, message="Scheduled source records fetched")
                if run.raw_count > 0 and run.normalized_count == 0 and run.rejected_count == run.raw_count:
                    run.transition(IngestRunStatus.REJECTED, message="Scheduled source records rejected and routed to DLQ")
                    self.store.upsert_run(run)
                    return ScheduledIngestResult(
                        run=run,
                        watermark=starting_watermark,
                        records=batch.records,
                        dlq_entries=tuple(rejected_dlq_entries),
                        audit_actions=tuple(rejected_audit_actions),
                    )
                if run.status == IngestRunStatus.NORMALIZING:
                    run.transition(IngestRunStatus.INDEXING, message="Scheduled source records normalized")
                run.transition(IngestRunStatus.COMPLETED, message="Scheduled source records ready for indexing")
                self.store.upsert_run(run)
                watermark = SourceWatermark(
                    connector_id=connector_id,
                    source_type=connector.source_type.value,
                    value=batch.next_watermark if batch.next_watermark is not None else (starting_watermark.value if starting_watermark else None),
                    updated_at=run.finished_at.isoformat().replace("+00:00", "Z")
                    if hasattr(run.finished_at, "isoformat")
                    else str(run.finished_at),
                    last_ingest_run_id=run.ingest_run_id,
                )
                self.store.update_watermark(watermark)
                return ScheduledIngestResult(
                    run=run,
                    watermark=watermark,
                    records=batch.records,
                    dlq_entries=tuple(rejected_dlq_entries),
                    audit_actions=tuple(rejected_audit_actions),
                )
            except Exception as exc:  # noqa: BLE001 - scheduler must DLQ final governed failures.
                last_error = exc
                if attempt < self.max_attempts:
                    continue

        message = str(last_error or "scheduled ingest failed")
        self.manager.fail_run(run.ingest_run_id, message=message)
        self.store.upsert_run(run)
        trace = TraceContext(
            trace_id=trace_id,
            correlation_id=trace_id,
            environment=self.environment,
            actor_ref=self.actor_ref,
        )
        event = EventEnvelope.new(
            event_type="source_ingestion.scheduled_run_failed",
            aggregate_type="source_ingest_run",
            aggregate_id=run.ingest_run_id,
            sequence_no=1,
            trace=trace,
            producer_service="source-ingestion",
            payload={
                "ingest_run_id": run.ingest_run_id,
                "connector_id": connector_id,
                "source_type": connector.source_type.value,
                "trigger_type": trigger_type,
                "attempts": self.max_attempts,
                "error": message,
                "watermark": starting_watermark.value if starting_watermark else None,
            },
        )
        audit_action = AuditAction.record(
            actor_ref=self.actor_ref,
            action_type="source_ingestion.scheduled_run.dead_lettered",
            target_ref=f"source_ingest_run:{run.ingest_run_id}",
            environment=self.environment,
            reason="scheduled ingest exhausted retries",
            trace=trace,
            payload=event.payload,
            metadata={"connector_id": connector_id, "attempts": self.max_attempts},
        )
        dlq_entry = self.dead_letter_queue.reject(
            event,
            reason=message,
            tags=("source_ingestion", "scheduled_ingest", "retry_exhausted"),
            source_ref=f"source_ingest_run:{run.ingest_run_id}",
        )
        return ScheduledIngestResult(
            run=run,
            watermark=starting_watermark,
            records=(),
            dlq_entries=(dlq_entry,),
            audit_actions=(audit_action,),
        )

    def _dead_letter_rejected_record(
        self,
        *,
        run: IngestRun,
        record: SourceRecord,
        connector_id: str,
        trigger_type: str,
        trace_id: str,
        starting_watermark: str | None,
        sequence_no: int,
    ) -> tuple[DeadLetterEntry, AuditAction]:
        trace = TraceContext(
            trace_id=record.trace_id or trace_id,
            correlation_id=trace_id,
            environment=self.environment,
            actor_ref=self.actor_ref,
        )
        payload = {
            "ingest_run_id": run.ingest_run_id,
            "connector_id": connector_id,
            "source_id": record.source_id,
            "source_type": record.source_type.value,
            "trigger_type": trigger_type,
            "watermark": starting_watermark,
            "record_status": record.status.value,
            "metadata": dict(record.metadata),
        }
        event = EventEnvelope.new(
            event_type="source_ingestion.source_record_rejected",
            aggregate_type="source_record",
            aggregate_id=record.source_id,
            sequence_no=sequence_no,
            trace=trace,
            producer_service="source-ingestion",
            payload=payload,
        )
        audit_action = AuditAction.record(
            actor_ref=self.actor_ref,
            action_type="source_ingestion.source_record.dead_lettered",
            target_ref=f"source_record:{record.source_id}",
            environment=self.environment,
            reason="source record rejected during scheduled ingest",
            trace=trace,
            payload=payload,
            metadata={"connector_id": connector_id, "ingest_run_id": run.ingest_run_id},
        )
        dlq_entry = self.dead_letter_queue.reject(
            event,
            reason=str(record.metadata.get("reject_reason") or "source record rejected during scheduled ingest"),
            tags=("source_ingestion", "scheduled_ingest", "source_record_rejected"),
            source_ref=f"source_record:{record.source_id}",
        )
        return dlq_entry, audit_action

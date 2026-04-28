from __future__ import annotations

import pytest

from services.source_ingestion import IngestManager
from services.source_ingestion.scheduler import IngestBatch, IngestionScheduler, JsonlIngestScheduleStore
from services.foundation import DeadLetterQueue
from services.source_ingestion.connectors import (
    ConnectorMode,
    IngestRunStatus,
    SourceConnector,
    SourceEvidenceError,
    SourceRecord,
)


def test_connector_requires_source_type_provider_and_license_scope() -> None:
    connector = SourceConnector(
        connector_id="conn-openalex",
        source_type="paper",
        provider="OpenAlex",
        license_scope="open",
        supported_modes=[ConnectorMode.BATCH],
    )

    assert connector.to_dict()["provider"] == "OpenAlex"
    assert connector.to_dict()["source_type"] == "paper"

    with pytest.raises(SourceEvidenceError, match="provider is required"):
        SourceConnector(
            connector_id="bad",
            source_type="paper",
            provider="",
            license_scope="open",
        )


def test_ingest_run_lifecycle_emits_start_and_completion_events() -> None:
    manager = IngestManager()
    manager.register_connector(
        SourceConnector(
            connector_id="conn-notes",
            source_type="internal_note",
            provider="Pantheon Notes",
            license_scope="internal",
        )
    )

    run = manager.start_ingest_run(
        connector_id="conn-notes",
        trigger_type="manual",
        trace_id="trace-sd03-001",
    )
    assert run.status == IngestRunStatus.FETCHING
    assert [event.event_type for event in run.events][-1] == "IngestRunStarted"

    manager.complete_run(run.ingest_run_id, raw_count=3, normalized_count=2, rejected_count=1)

    assert run.status == IngestRunStatus.COMPLETED
    assert run.finished_at is not None
    assert [event.event_type for event in run.events][-3:] == [
        "SourceNormalizingStarted",
        "EvidenceIndexingStarted",
        "IngestRunCompleted",
    ]
    assert run.to_dict()["trace_id"] == "trace-sd03-001"


def test_ingest_run_rejects_invalid_transition() -> None:
    manager = IngestManager()
    manager.register_connector(
        SourceConnector(
            connector_id="conn-repo",
            source_type="repo",
            provider="GitHub",
            license_scope="internal",
        )
    )
    run = manager.start_ingest_run(
        connector_id="conn-repo",
        trigger_type="manual",
        trace_id="trace-sd03-002",
    )

    with pytest.raises(SourceEvidenceError, match="Cannot transition"):
        manager.advance_run(run.ingest_run_id, IngestRunStatus.COMPLETED)


def test_scheduled_ingest_resumes_from_persisted_watermark_after_restart(tmp_path) -> None:
    manager = IngestManager()
    manager.register_connector(
        SourceConnector(
            connector_id="conn-openalex",
            source_type="paper",
            provider="OpenAlex",
            license_scope="open",
        )
    )
    first_store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    first_scheduler = IngestionScheduler(
        manager=manager,
        store=first_store,
        dead_letter_queue=DeadLetterQueue(tmp_path / "dlq.jsonl"),
    )

    first_scheduler.run_once(
        connector_id="conn-openalex",
        trace_id="trace-scheduled-001",
        fetch_batch=lambda watermark: IngestBatch(
            records=[
                SourceRecord(
                    source_id="src-paper-1",
                    connector_id="conn-openalex",
                    source_type="paper",
                    title="Paper 1",
                    content_ref="https://example.test/paper-1",
                )
            ],
            next_watermark="2026-04-28T09:00:00Z",
        ),
    )

    replayed_store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    seen_watermarks: list[str | None] = []
    replayed_scheduler = IngestionScheduler(
        manager=manager,
        store=replayed_store,
        dead_letter_queue=DeadLetterQueue(tmp_path / "dlq.jsonl"),
    )
    result = replayed_scheduler.run_once(
        connector_id="conn-openalex",
        trace_id="trace-scheduled-002",
        fetch_batch=lambda watermark: seen_watermarks.append(watermark)
        or IngestBatch(
            records=[
                SourceRecord(
                    source_id="src-paper-2",
                    connector_id="conn-openalex",
                    source_type="paper",
                    title="Paper 2",
                    content_ref="https://example.test/paper-2",
                )
            ],
            next_watermark="2026-04-28T10:00:00Z",
        ),
    )

    assert seen_watermarks == ["2026-04-28T09:00:00Z"]
    assert result.run.status == IngestRunStatus.COMPLETED
    assert result.watermark is not None
    assert result.watermark.value == "2026-04-28T10:00:00Z"
    assert JsonlIngestScheduleStore(tmp_path / "schedule.jsonl").get_watermark("conn-openalex").value == (
        "2026-04-28T10:00:00Z"
    )


def test_scheduled_ingest_failure_uses_shared_dlq_and_audit_path(tmp_path) -> None:
    manager = IngestManager()
    manager.register_connector(
        SourceConnector(
            connector_id="conn-repo",
            source_type="repo",
            provider="GitHub",
            license_scope="internal",
        )
    )
    queue = DeadLetterQueue(tmp_path / "dlq.jsonl")
    scheduler = IngestionScheduler(
        manager=manager,
        store=JsonlIngestScheduleStore(tmp_path / "schedule.jsonl"),
        dead_letter_queue=queue,
        max_attempts=2,
    )
    attempts = 0

    def fail(_watermark: str | None) -> IngestBatch:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("upstream rate limit")

    result = scheduler.run_once(
        connector_id="conn-repo",
        trace_id="trace-scheduled-fail",
        fetch_batch=fail,
    )

    assert attempts == 2
    assert result.run.status == IngestRunStatus.FAILED
    assert result.dlq_entries[0].event.event_type == "source_ingestion.scheduled_run_failed"
    assert result.dlq_entries[0].tags == ("source_ingestion", "scheduled_ingest", "retry_exhausted")
    assert result.audit_actions[0].action_type == "source_ingestion.scheduled_run.dead_lettered"
    assert queue.load_from_spill() == 1


def test_rejected_source_records_use_shared_dlq_and_do_not_advance_watermark(tmp_path) -> None:
    manager = IngestManager()
    manager.register_connector(
        SourceConnector(
            connector_id="conn-openalex",
            source_type="paper",
            provider="OpenAlex",
            license_scope="open",
        )
    )
    queue = DeadLetterQueue(tmp_path / "dlq.jsonl")
    store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    scheduler = IngestionScheduler(manager=manager, store=store, dead_letter_queue=queue)

    result = scheduler.run_once(
        connector_id="conn-openalex",
        trace_id="trace-rejected-record",
        fetch_batch=lambda _watermark: IngestBatch(
            records=[
                SourceRecord(
                    source_id="src-rejected-paper",
                    connector_id="conn-openalex",
                    source_type="paper",
                    title="Rejected paper",
                    content_ref="https://example.test/rejected",
                    status="rejected",
                    metadata={"reject_reason": "license scope denied"},
                )
            ],
            next_watermark="2026-04-28T11:00:00Z",
        ),
    )

    assert result.run.status == IngestRunStatus.REJECTED
    assert result.watermark is None
    assert store.get_watermark("conn-openalex") is None
    assert result.dlq_entries[0].event.event_type == "source_ingestion.source_record_rejected"
    assert result.dlq_entries[0].reason == "license scope denied"
    assert result.dlq_entries[0].tags == ("source_ingestion", "scheduled_ingest", "source_record_rejected")
    assert result.audit_actions[0].action_type == "source_ingestion.source_record.dead_lettered"
    assert queue.load_from_spill() == 1
